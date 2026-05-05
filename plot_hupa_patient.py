#!/usr/bin/env python3
"""Create an interactive HUPA-UCM patient timeline with Plotly."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


PREPROCESSED_COLUMNS = {
    "time",
    "glucose",
    "calories",
    "heart_rate",
    "steps",
    "basal_rate",
    "bolus_volume_delivered",
    "carb_input",
}
MGDL_TO_MMOLL = 1 / 18.0182
GLUCOSE_TARGET_MGDL = (70.0, 180.0)
GLUCOSE_UNITS = ("mg/dL", "mmol/L")


def parse_glucose_unit(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "")
    if normalized in {"mg/dl", "mgdl"}:
        return "mg/dL"
    if normalized in {"mmol/l", "mmoll", "mmol"}:
        return "mmol/L"
    raise argparse.ArgumentTypeError("Use 'mg/dL' or 'mmol/L'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create an interactive Plotly timeline for one HUPA-UCM patient using "
            "the preprocessed 5-minute data and optional raw source-event overlays."
        )
    )
    parser.add_argument("--patient", default="HUPA0001P", help="Patient id, e.g. HUPA0001P.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Path to 'HUPA-UCM Diabetes Dataset'. Defaults to current/parent folder search.",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Optional start date/time, e.g. 2018-06-13 or 2018-06-13T18:00.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Optional end date/time. If omitted with --days, start + days is used.",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=None,
        help="Number of days to plot from --start, or from the first patient timestamp.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="HTML output path. Defaults to outputs/<patient>_<range>_overview.html.",
    )
    parser.add_argument(
        "--glucose-unit",
        type=parse_glucose_unit,
        default="mg/dL",
        metavar="{mg/dL,mmol/L}",
        help="Initial glucose display unit. The HTML plot also includes a unit selector.",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Disable raw FreeStyle/Medtronic/Roche event overlays.",
    )
    parser.add_argument("--show", action="store_true", help="Open the interactive figure in a browser.")
    return parser.parse_args()


def find_dataset_root(explicit_path: Path | None) -> Path:
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    candidates.extend(
        [
            Path.cwd() / "HUPA-UCM Diabetes Dataset",
            Path.cwd().parent / "HUPA-UCM Diabetes Dataset",
        ]
    )

    for candidate in candidates:
        if candidate.exists() and (candidate / "Preprocessed").is_dir():
            return candidate.resolve()

    searched = "\n".join(f"  - {path}" for path in candidates)
    raise FileNotFoundError(f"Could not find HUPA-UCM dataset root. Searched:\n{searched}")


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def first_existing_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def load_preprocessed(dataset_root: Path, patient: str) -> pd.DataFrame:
    path = dataset_root / "Preprocessed" / f"{patient}.csv"
    if not path.exists():
        available = sorted(p.stem for p in (dataset_root / "Preprocessed").glob("*.csv"))
        raise FileNotFoundError(
            f"No preprocessed file for {patient}: {path}\n"
            f"Available patients: {', '.join(available)}"
        )

    df = pd.read_csv(path, sep=";")
    missing = PREPROCESSED_COLUMNS.difference(df.columns)
    if missing:
        raise ValueError(f"{path} is missing expected columns: {sorted(missing)}")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    for col in PREPROCESSED_COLUMNS - {"time"}:
        df[col] = to_numeric(df[col])

    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{path} has no valid timestamped rows.")
    return df


def filter_time(df: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.DataFrame:
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= df["time"] >= start
    if end is not None:
        mask &= df["time"] < end
    return df.loc[mask].copy()


def resolve_range(
    df: pd.DataFrame, start_arg: str | None, end_arg: str | None, days: float | None
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    start = pd.to_datetime(start_arg) if start_arg else None
    end = pd.to_datetime(end_arg) if end_arg else None

    if days is not None:
        if start is None:
            start = df["time"].min()
        if end is None:
            end = start + pd.Timedelta(days=days)

    return start, end


def read_csv_with_header(path: Path, header_prefix: str, **kwargs: object) -> pd.DataFrame:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_number, line in enumerate(handle):
            if line.startswith(header_prefix):
                return pd.read_csv(path, sep=";", skiprows=line_number, **kwargs)
    raise ValueError(f"Could not find header starting with {header_prefix!r} in {path}")


def event_frame(times: pd.Series, values: pd.Series, source: str) -> pd.DataFrame:
    df = pd.DataFrame({"time": times, "value": to_numeric(values), "source": source})
    return df.dropna(subset=["time", "value"]).query("value > 0").reset_index(drop=True)


def load_freestyle_events(raw_patient_dir: Path) -> dict[str, list[pd.DataFrame]]:
    result: dict[str, list[pd.DataFrame]] = {"glucose": [], "bolus": [], "carbs": []}
    for path in sorted((raw_patient_dir / "free_style_sensor").glob("*.csv")):
        try:
            df = pd.read_csv(path, sep=";", skiprows=1)
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
            continue

        if "Hora" not in df.columns:
            continue

        times = pd.to_datetime(df["Hora"], format="%Y/%m/%d %H:%M", errors="coerce")
        glucose_cols = ["Histórico glucosa (mg/dL)", "Glucosa leída (mg/dL)", "Glucosa de la tira (mg/dL)"]
        insulin_cols = [
            "Insulina de acción rápida (unidades)",
            "Insulina comida (unidades)",
            "Insulina corrección (unidades)",
            "Insulina cambio usuario (unidades)",
            "Insulina de acción lenta (unidades)",
        ]

        glucose_parts = [
            event_frame(times, df[col], f"FreeStyle {col}") for col in glucose_cols if col in df.columns
        ]
        insulin_parts = [
            event_frame(times, df[col], f"FreeStyle {col}") for col in insulin_cols if col in df.columns
        ]

        if glucose_parts:
            result["glucose"].append(pd.concat(glucose_parts, ignore_index=True))
        if insulin_parts:
            insulin = pd.concat(insulin_parts, ignore_index=True)
            result["bolus"].append(insulin.groupby("time", as_index=False)["value"].sum().assign(source="FreeStyle insulin"))
        if "Carbohidratos (raciones)" in df.columns:
            result["carbs"].append(event_frame(times, df["Carbohidratos (raciones)"], "FreeStyle carbs"))

    return result


def load_medtronic_events(raw_patient_dir: Path) -> dict[str, list[pd.DataFrame]]:
    result: dict[str, list[pd.DataFrame]] = {"glucose": [], "bolus": [], "carbs": [], "basal": []}
    for path in sorted((raw_patient_dir / "medtronic_insulin_pump").glob("*.csv")):
        try:
            df = read_csv_with_header(path, "Index;Date;Time;")
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
            continue

        if not {"Date", "Time"}.issubset(df.columns):
            continue

        times = pd.to_datetime(df["Date"].astype(str) + " " + df["Time"].astype(str), dayfirst=True, errors="coerce")
        glucose_parts = []
        for col in ["BG Reading (mg/dL)", "Sensor Glucose (mg/dL)", "Sensor Calibration BG (mg/dL)"]:
            if col in df.columns:
                glucose_parts.append(event_frame(times, df[col], f"Medtronic {col}"))
        if glucose_parts:
            result["glucose"].append(pd.concat(glucose_parts, ignore_index=True))
        if "Bolus Volume Delivered (U)" in df.columns:
            result["bolus"].append(event_frame(times, df["Bolus Volume Delivered (U)"], "Medtronic bolus"))
        if "BWZ Carb Input (exchanges)" in df.columns:
            result["carbs"].append(event_frame(times, df["BWZ Carb Input (exchanges)"], "Medtronic carbs"))
        if "Basal Rate (U/h)" in df.columns:
            result["basal"].append(event_frame(times, df["Basal Rate (U/h)"], "Medtronic basal"))

    return result


def load_roche_events(raw_patient_dir: Path) -> dict[str, list[pd.DataFrame]]:
    result: dict[str, list[pd.DataFrame]] = {"glucose": [], "bolus": [], "carbs": [], "basal": []}
    for path in sorted((raw_patient_dir / "roche_insulin_pump").glob("*.csv")):
        try:
            df = pd.read_csv(path, sep=";")
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
            continue

        if not {"Fecha", "Hora"}.issubset(df.columns):
            continue

        times = pd.to_datetime(df["Fecha"].astype(str) + " " + df["Hora"].astype(str), dayfirst=True, errors="coerce")
        if "Glucemia" in df.columns:
            result["glucose"].append(event_frame(times, df["Glucemia"], "Roche glucose"))
        if "Hidratos de Carbono" in df.columns:
            result["carbs"].append(event_frame(times, df["Hidratos de Carbono"], "Roche carbs"))
        if "Unidades" in df.columns:
            result["bolus"].append(event_frame(times, df["Unidades"], "Roche bolus"))
        if "Dosis Basal (UI/H)" in df.columns:
            result["basal"].append(event_frame(times, df["Dosis Basal (UI/H)"], "Roche basal"))

    return result


def load_dexcom_events(raw_patient_dir: Path) -> dict[str, list[pd.DataFrame]]:
    result: dict[str, list[pd.DataFrame]] = {"glucose": [], "bolus": [], "carbs": []}
    for path in sorted((raw_patient_dir / "dexcom").glob("*.csv")):
        try:
            df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
            continue

        time_col = first_existing_column(df, ["Marca temporal (AAAA-MM-DDThh:mm:ss)", "Timestamp (YYYY-MM-DDThh:mm:ss)"])
        if time_col is None:
            continue

        times = pd.to_datetime(df[time_col], errors="coerce")
        glucose_col = first_existing_column(df, ["Nivel de glucosa (mg/dl)", "Glucose Value (mg/dL)"])
        insulin_col = first_existing_column(df, ["Nivel de insulina (u)", "Insulin Value (u)"])
        carb_col = first_existing_column(df, ["Nivel de carbohidratos (gramos)", "Carb Value (grams)"])

        if glucose_col:
            result["glucose"].append(event_frame(times, df[glucose_col], "Dexcom glucose"))
        if insulin_col:
            result["bolus"].append(event_frame(times, df[insulin_col], "Dexcom insulin"))
        if carb_col:
            result["carbs"].append(event_frame(times, df[carb_col], "Dexcom carbs"))

    return result


def load_raw_events(
    dataset_root: Path,
    patient: str,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> dict[str, pd.DataFrame]:
    raw_patient_dir = dataset_root / "Raw_Data" / patient
    event_lists: dict[str, list[pd.DataFrame]] = {"glucose": [], "bolus": [], "carbs": [], "basal": []}
    if not raw_patient_dir.exists():
        return {name: pd.DataFrame(columns=["time", "value", "source"]) for name in event_lists}

    for loader in [load_freestyle_events, load_medtronic_events, load_roche_events, load_dexcom_events]:
        loaded = loader(raw_patient_dir)
        for name, frames in loaded.items():
            event_lists.setdefault(name, []).extend(frames)

    events: dict[str, pd.DataFrame] = {}
    for name, frames in event_lists.items():
        if frames:
            combined = pd.concat(frames, ignore_index=True).dropna(subset=["time", "value"])
            combined = combined.sort_values("time").reset_index(drop=True)
            events[name] = filter_time(combined, start, end)
        else:
            events[name] = pd.DataFrame(columns=["time", "value", "source"])
    return events


def infer_bar_width_ms(df: pd.DataFrame) -> float:
    deltas = df["time"].sort_values().diff().dropna()
    if deltas.empty:
        return 5 * 60 * 1000
    minutes = deltas.dt.total_seconds().median() / 60
    if not np.isfinite(minutes) or minutes <= 0:
        minutes = 5
    return minutes * 60 * 1000 * 0.85


def nonzero(df: pd.DataFrame, column: str) -> pd.DataFrame:
    return df.loc[df[column].fillna(0) > 0, ["time", column]].copy()


def convert_glucose(values: pd.Series, unit: str) -> pd.Series:
    if unit == "mg/dL":
        return values
    return values * MGDL_TO_MMOLL


def glucose_target_range(unit: str) -> tuple[float, float]:
    low, high = GLUCOSE_TARGET_MGDL
    if unit == "mg/dL":
        return low, high
    return low * MGDL_TO_MMOLL, high * MGDL_TO_MMOLL


def glucose_axis_title(unit: str) -> str:
    return f"Glucose {unit}"


def glucose_hover_template(unit: str, extra: str, source: bool = False) -> str:
    precision = ".1f" if unit == "mg/dL" else ".2f"
    source_line = "<br>%{customdata}" if source else ""
    return f"%{{x|%Y-%m-%d %H:%M}}<br>%{{y:{precision}}} {unit}{source_line}<extra>{extra}</extra>"


def default_output_path(patient: str, plotted: pd.DataFrame) -> Path:
    start = plotted["time"].min().strftime("%Y%m%d")
    end = plotted["time"].max().strftime("%Y%m%d")
    return Path("outputs") / f"{patient}_{start}_{end}_overview.html"


def ensure_html_output_path(output: Path) -> Path:
    if output.suffix.lower() in {".html", ".htm"}:
        return output
    html_output = output.with_suffix(".html")
    print(f"Plotly output is HTML; writing to {html_output} instead of {output}.")
    return html_output


def plot_patient(
    patient: str,
    df: pd.DataFrame,
    raw_events: dict[str, pd.DataFrame],
    output: Path,
    show: bool,
    glucose_unit: str,
) -> None:
    bar_width = infer_bar_width_ms(df)
    initial_unit = glucose_unit
    initial_mgdl = initial_unit == "mg/dL"
    glucose_trace_indices: dict[str, list[int]] = {unit: [] for unit in GLUCOSE_UNITS}
    fig = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.30, 0.17, 0.12, 0.18, 0.15],
        vertical_spacing=0.035,
        specs=[[{}], [{}], [{}], [{"secondary_y": True}], [{}]],
    )

    for unit in GLUCOSE_UNITS:
        low, high = glucose_target_range(unit)
        visible = unit == initial_unit
        fig.add_hrect(y0=low, y1=high, fillcolor="#d8f0d2", opacity=0.45, line_width=0, visible=visible, row=1, col=1)
        fig.add_hline(y=low, line_color="#d62728", line_width=1, opacity=0.75, visible=visible, row=1, col=1)
        fig.add_hline(y=high, line_color="#ff7f0e", line_width=1, opacity=0.75, visible=visible, row=1, col=1)

    for unit in GLUCOSE_UNITS:
        fig.add_trace(
            go.Scatter(
                x=df["time"],
                y=convert_glucose(df["glucose"], unit),
                name=f"Preprocessed glucose ({unit})",
                mode="lines",
                line=dict(color="#1f77b4", width=1.6),
                visible=unit == initial_unit,
                hovertemplate=glucose_hover_template(unit, "Glucose"),
            ),
            row=1,
            col=1,
        )
        glucose_trace_indices[unit].append(len(fig.data) - 1)

    raw_glucose = raw_events.get("glucose", pd.DataFrame())
    if not raw_glucose.empty:
        for unit in GLUCOSE_UNITS:
            fig.add_trace(
                go.Scatter(
                    x=raw_glucose["time"],
                    y=convert_glucose(raw_glucose["value"], unit),
                    customdata=raw_glucose["source"],
                    name=f"Raw glucose points ({unit})",
                    mode="markers",
                    marker=dict(symbol="circle-open", size=6, color="#111111", opacity=0.45),
                    visible=unit == initial_unit,
                    hovertemplate=glucose_hover_template(unit, "Raw glucose", source=True),
                ),
                row=1,
                col=1,
            )
            glucose_trace_indices[unit].append(len(fig.data) - 1)

    fig.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["basal_rate"],
            name="Basal rate (preprocessed units)",
            mode="lines",
            line=dict(color="#9467bd", width=1.3, shape="hv"),
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.3f}<extra>Basal</extra>",
        ),
        row=2,
        col=1,
    )
    bolus = nonzero(df, "bolus_volume_delivered")
    if not bolus.empty:
        fig.add_trace(
            go.Bar(
                x=bolus["time"],
                y=bolus["bolus_volume_delivered"],
                width=bar_width,
                name="Bolus delivered",
                marker=dict(color="#d62728", opacity=0.65),
                hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.2f} U<extra>Bolus</extra>",
            ),
            row=2,
            col=1,
        )
    raw_bolus = raw_events.get("bolus", pd.DataFrame())
    if not raw_bolus.empty:
        fig.add_trace(
            go.Scatter(
                x=raw_bolus["time"],
                y=raw_bolus["value"],
                customdata=raw_bolus["source"],
                name="Raw insulin events",
                mode="markers",
                marker=dict(symbol="triangle-down", size=8, color="#111111", opacity=0.55),
                hovertemplate=(
                    "%{x|%Y-%m-%d %H:%M}<br>%{y:.2f} U<br>%{customdata}"
                    "<extra>Raw insulin</extra>"
                ),
            ),
            row=2,
            col=1,
        )

    carbs = nonzero(df, "carb_input")
    if not carbs.empty:
        fig.add_trace(
            go.Bar(
                x=carbs["time"],
                y=carbs["carb_input"],
                width=bar_width,
                name="Carb input",
                marker=dict(color="#ffbf00", line=dict(color="#8a6a00", width=0.4), opacity=0.78),
                hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.2f}<extra>Carbs</extra>",
            ),
            row=3,
            col=1,
        )
    raw_carbs = raw_events.get("carbs", pd.DataFrame())
    if not raw_carbs.empty:
        fig.add_trace(
            go.Scatter(
                x=raw_carbs["time"],
                y=raw_carbs["value"],
                customdata=raw_carbs["source"],
                name="Raw carb events",
                mode="markers",
                marker=dict(symbol="triangle-up", size=8, color="#111111", opacity=0.55),
                hovertemplate=(
                    "%{x|%Y-%m-%d %H:%M}<br>%{y:.2f}<br>%{customdata}"
                    "<extra>Raw carbs</extra>"
                ),
            ),
            row=3,
            col=1,
        )

    fig.add_trace(
        go.Bar(
            x=df["time"],
            y=df["steps"].fillna(0),
            width=bar_width,
            name="Steps",
            marker=dict(color="#2ca02c", opacity=0.45),
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.0f} steps<extra>Steps</extra>",
        ),
        row=4,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["calories"],
            name="Calories",
            mode="lines",
            line=dict(color="#8c564b", width=1.1),
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.2f}<extra>Calories</extra>",
        ),
        row=4,
        col=1,
        secondary_y=True,
    )

    fig.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["heart_rate"],
            name="Heart rate",
            mode="lines",
            line=dict(color="#e377c2", width=1.1),
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.1f} bpm<extra>Heart rate</extra>",
        ),
        row=5,
        col=1,
    )

    start_label = df["time"].min().strftime("%Y-%m-%d %H:%M")
    end_label = df["time"].max().strftime("%Y-%m-%d %H:%M")
    raw_counts = ", ".join(
        f"{name}={len(raw_events.get(name, []))}" for name in ["glucose", "bolus", "carbs"] if name in raw_events
    )
    title = (
        f"{patient}: glucose with insulin, food, and activity "
        f"({start_label} to {end_label})<br>"
        f"<sup>Preprocessed rows={len(df)}"
        + (f"; raw overlay events: {raw_counts}" if raw_counts else "")
        + "</sup>"
    )

    fig.update_yaxes(title_text=glucose_axis_title(initial_unit), row=1, col=1)
    fig.update_yaxes(title_text="Insulin U", row=2, col=1)
    fig.update_yaxes(title_text="Food carbs", row=3, col=1)
    fig.update_yaxes(title_text="Steps", row=4, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Calories", row=4, col=1, secondary_y=True)
    fig.update_yaxes(title_text="Heart rate bpm", row=5, col=1)
    fig.update_xaxes(title_text="Time", row=5, col=1, rangeslider_visible=True)
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikecolor="#999999", spikethickness=1)

    unit_buttons = []
    for unit_index, unit in enumerate(GLUCOSE_UNITS):
        visibility = []
        for index, _trace in enumerate(fig.data):
            if index in glucose_trace_indices["mg/dL"]:
                visibility.append(unit == "mg/dL")
            elif index in glucose_trace_indices["mmol/L"]:
                visibility.append(unit == "mmol/L")
            else:
                visibility.append(True)

        shape_updates: dict[str, object] = {"yaxis.title.text": glucose_axis_title(unit)}
        for shape_index in range(len(GLUCOSE_UNITS) * 3):
            shape_unit = GLUCOSE_UNITS[shape_index // 3]
            shape_updates[f"shapes[{shape_index}].visible"] = shape_unit == unit

        unit_buttons.append(
            dict(
                label=unit,
                method="update",
                args=[{"visible": visibility}, shape_updates],
            )
        )

    fig.update_layout(
        title=title,
        template="plotly_white",
        height=1120,
        hovermode="x unified",
        bargap=0.05,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        updatemenus=[
            dict(
                type="buttons",
                direction="right",
                x=1,
                xanchor="right",
                y=1.105,
                yanchor="top",
                active=0 if initial_mgdl else 1,
                buttons=unit_buttons,
            )
        ],
        margin=dict(l=80, r=70, t=115, b=65),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(
        output,
        include_plotlyjs=True,
        full_html=True,
        auto_open=show,
        config={
            "displaylogo": False,
            "responsive": True,
            "modeBarButtonsToAdd": ["drawline", "drawrect", "eraseshape"],
        },
    )
    print(f"Saved interactive Plotly plot to {output.resolve()}")


def main() -> None:
    args = parse_args()
    dataset_root = find_dataset_root(args.dataset)
    df = load_preprocessed(dataset_root, args.patient)
    start, end = resolve_range(df, args.start, args.end, args.days)
    plotted = filter_time(df, start, end)
    if plotted.empty:
        available_start = df["time"].min().strftime("%Y-%m-%d %H:%M")
        available_end = df["time"].max().strftime("%Y-%m-%d %H:%M")
        raise ValueError(
            f"No rows for requested range. {args.patient} has data from "
            f"{available_start} to {available_end}."
        )

    raw_events = (
        {name: pd.DataFrame(columns=["time", "value", "source"]) for name in ["glucose", "bolus", "carbs", "basal"]}
        if args.no_raw
        else load_raw_events(dataset_root, args.patient, start, end)
    )
    output = ensure_html_output_path(args.output or default_output_path(args.patient, plotted))
    plot_patient(args.patient, plotted, raw_events, output, args.show, args.glucose_unit)


if __name__ == "__main__":
    main()

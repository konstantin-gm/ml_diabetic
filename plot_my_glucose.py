#!/usr/bin/env python3
"""Plot personal glucose monitor data with Plotly."""

from __future__ import annotations

import argparse
from collections import Counter
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go


OOXML_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RELS_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DEFAULT_MONITOR_GLOB = "Hematonix*_dat.xls*"
DEFAULT_EVENTS_GLOB = "*melstudio*.txt"
NON_FOOD_NOTE_PATTERNS = ("велосипед",)
FOOD_ALIASES = {
    "картошка": "картофель",
}
MONTHS_RU = {
    "янв": 1,
    "янв.": 1,
    "января": 1,
    "фев": 2,
    "фев.": 2,
    "февраля": 2,
    "мар": 3,
    "мар.": 3,
    "марта": 3,
    "апр": 4,
    "апр.": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июн.": 6,
    "июня": 6,
    "июл": 7,
    "июл.": 7,
    "июля": 7,
    "авг": 8,
    "авг.": 8,
    "августа": 8,
    "сен": 9,
    "сен.": 9,
    "сентября": 9,
    "окт": 10,
    "окт.": 10,
    "октября": 10,
    "ноя": 11,
    "ноя.": 11,
    "ноября": 11,
    "дек": 12,
    "дек.": 12,
    "декабря": 12,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot personal glucose monitor data as an interactive Plotly chart.")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Directory with personal dataset files. Defaults to current/parent 'My Dataset'.",
    )
    parser.add_argument(
        "--monitor-file",
        type=Path,
        default=None,
        help="Glucose monitor OOXML file. Defaults to Hematonix*_dat.xls* in dataset dir.",
    )
    parser.add_argument(
        "--events-file",
        type=Path,
        default=None,
        help="Food/insulin text file. Defaults to *melstudio*.txt in dataset dir.",
    )
    parser.add_argument(
        "--events-year",
        type=int,
        default=None,
        help="Year for food/insulin records because the text file stores dates without year.",
    )
    parser.add_argument(
        "--events-output",
        type=Path,
        default=Path("outputs/my_events_dataset.csv"),
        help="CSV output for parsed food/insulin event dataset.",
    )
    parser.add_argument(
        "--food-vocab-output",
        type=Path,
        default=Path("outputs/my_food_vocabulary.csv"),
        help="CSV output for numbered food vocabulary extracted from notes.",
    )
    parser.add_argument(
        "--vectorized-events-output",
        type=Path,
        default=Path("outputs/my_events_vectorized.csv"),
        help="CSV output for events with numeric multi-hot food vector columns.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/my_glucose_overview.html"),
        help="Output HTML file.",
    )
    parser.add_argument("--no-events", action="store_true", help="Do not parse or overlay food/insulin events.")
    show_group = parser.add_mutually_exclusive_group()
    show_group.add_argument(
        "--show",
        dest="show",
        action="store_true",
        default=True,
        help="Open the generated HTML in a browser. This is the default.",
    )
    show_group.add_argument(
        "--no-show",
        dest="show",
        action="store_false",
        help="Only save the HTML file, without opening it.",
    )
    return parser.parse_args()


def find_dataset_dir(explicit_dir: Path | None) -> Path:
    candidates = []
    if explicit_dir:
        candidates.append(explicit_dir)
    candidates.extend([Path.cwd() / "My Dataset", Path.cwd().parent / "My Dataset"])

    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()

    searched = "\n".join(f"  - {candidate}" for candidate in candidates)
    raise FileNotFoundError(f"Could not find dataset directory. Searched:\n{searched}")


def find_monitor_file(dataset_dir: Path, explicit_file: Path | None) -> Path:
    if explicit_file:
        path = explicit_file if explicit_file.is_absolute() else Path.cwd() / explicit_file
        if path.exists():
            return path.resolve()
        raise FileNotFoundError(f"Monitor file not found: {path}")

    matches = sorted(dataset_dir.glob(DEFAULT_MONITOR_GLOB))
    if not matches:
        matches = sorted(dataset_dir.glob("*.xls*"))
    if not matches:
        raise FileNotFoundError(f"No .xls/.xlsx monitor file found in {dataset_dir}")
    return matches[0].resolve()


def find_events_file(dataset_dir: Path, explicit_file: Path | None) -> Path:
    if explicit_file:
        candidates = [explicit_file] if explicit_file.is_absolute() else [Path.cwd() / explicit_file, dataset_dir / explicit_file]
        for path in candidates:
            if path.exists():
                return path.resolve()
        searched = "\n".join(f"  - {path}" for path in candidates)
        raise FileNotFoundError(f"Events file not found. Searched:\n{searched}")

    matches = sorted(dataset_dir.glob(DEFAULT_EVENTS_GLOB))
    if not matches:
        matches = sorted(dataset_dir.glob("*.txt"))
    if not matches:
        raise FileNotFoundError(f"No food/insulin text file found in {dataset_dir}")
    return matches[0].resolve()


def cell_column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {cell_ref}")

    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("main:si", OOXML_NS):
        strings.append("".join(text.text or "" for text in item.findall(".//main:t", OOXML_NS)))
    return strings


def get_sheet_path(archive: zipfile.ZipFile, sheet_name: str | None = None) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("rel:Relationship", RELS_NS)
        if "Id" in rel.attrib and "Target" in rel.attrib
    }

    sheets = workbook.findall("main:sheets/main:sheet", OOXML_NS)
    if not sheets:
        raise ValueError("Workbook has no sheets.")

    selected = None
    for sheet in sheets:
        if sheet_name is None or sheet.attrib.get("name") == sheet_name:
            selected = sheet
            break
    if selected is None:
        available = ", ".join(sheet.attrib.get("name", "<unnamed>") for sheet in sheets)
        raise ValueError(f"Sheet {sheet_name!r} not found. Available sheets: {available}")

    rel_id = selected.attrib[f"{{{OFFICE_REL_NS}}}id"]
    target = rel_targets[rel_id].lstrip("/")
    if not target.startswith("xl/"):
        target = f"xl/{target}"
    return target


def read_ooxml_table(path: Path, sheet_name: str | None = None) -> pd.DataFrame:
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is not an OOXML spreadsheet.")

    with zipfile.ZipFile(path) as archive:
        shared_strings = load_shared_strings(archive)
        sheet_path = get_sheet_path(archive, sheet_name)
        root = ET.fromstring(archive.read(sheet_path))

    rows: list[list[object]] = []
    max_width = 0
    for row in root.findall(".//main:sheetData/main:row", OOXML_NS):
        values: list[object] = []
        for cell in row.findall("main:c", OOXML_NS):
            index = cell_column_index(cell.attrib["r"])
            while len(values) <= index:
                values.append(None)
            cell_type = cell.attrib.get("t")
            raw_value = cell.findtext("main:v", namespaces=OOXML_NS)
            inline_value = cell.findtext("main:is/main:t", namespaces=OOXML_NS)
            if cell_type == "s" and raw_value is not None:
                values[index] = shared_strings[int(raw_value)]
            elif cell_type == "inlineStr":
                values[index] = inline_value
            else:
                values[index] = raw_value
        max_width = max(max_width, len(values))
        rows.append(values)

    if not rows:
        raise ValueError(f"{path} has no rows.")

    normalized_rows = [row + [None] * (max_width - len(row)) for row in rows]
    header = [str(value).strip() if value is not None else f"column_{idx + 1}" for idx, value in enumerate(normalized_rows[0])]
    return pd.DataFrame(normalized_rows[1:], columns=header)


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text or text == "-":
        return None
    return pd.to_numeric(text, errors="coerce")


def parse_optional_amount(value: object) -> float:
    number = parse_number(value)
    if pd.isna(number):
        return 0.0
    return float(number)


def parse_insulin_pair(value: object) -> tuple[float, float]:
    text = "" if value is None else str(value).strip()
    if "/" not in text:
        return 0.0, 0.0
    long_text, short_text = text.split("/", 1)
    return parse_optional_amount(long_text), parse_optional_amount(short_text)


def parse_russian_date(date_text: object, time_text: object, year: int) -> pd.Timestamp:
    date = "" if date_text is None else str(date_text).strip().lower()
    time = "" if time_text is None else str(time_text).strip()
    match = re.match(r"^(\d{1,2})\s+([а-яё.]+)$", date)
    if not match:
        return pd.NaT

    day = int(match.group(1))
    month_text = match.group(2)
    month = MONTHS_RU.get(month_text)
    if month is None:
        return pd.NaT

    return pd.to_datetime(f"{year:04d}-{month:02d}-{day:02d} {time}", errors="coerce")


def detect_glucose_column(df: pd.DataFrame) -> str:
    for column in df.columns:
        lowered = column.lower()
        if "глюк" in lowered or "glucose" in lowered or "monitor" in lowered:
            return column
    if len(df.columns) < 2:
        raise ValueError("Cannot detect glucose column.")
    return df.columns[1]


def detect_time_column(df: pd.DataFrame) -> str:
    for column in df.columns:
        lowered = column.lower()
        if "время" in lowered or "time" in lowered or "date" in lowered:
            return column
    return df.columns[0]


def detect_glucose_unit(column: str) -> str:
    lowered = column.lower()
    if "ммоль" in lowered or "mmol" in lowered:
        return "mmol/L"
    if "mg/dl" in lowered or "мг" in lowered:
        return "mg/dL"
    return "mmol/L"


def load_glucose_monitor(path: Path) -> tuple[pd.DataFrame, str]:
    raw = read_ooxml_table(path)
    time_col = detect_time_column(raw)
    glucose_col = detect_glucose_column(raw)
    unit = detect_glucose_unit(glucose_col)

    data = pd.DataFrame(
        {
            "time": pd.to_datetime(raw[time_col], format="%d.%m.%Y %H:%M", errors="coerce"),
            "glucose": raw[glucose_col].map(parse_number),
        }
    )
    data["glucose"] = pd.to_numeric(data["glucose"], errors="coerce")
    data = data.dropna(subset=["time", "glucose"]).sort_values("time").reset_index(drop=True)
    if data.empty:
        raise ValueError(f"No valid glucose rows parsed from {path}")
    return data, unit


def infer_events_year(glucose_data: pd.DataFrame, explicit_year: int | None) -> int:
    if explicit_year is not None:
        return explicit_year
    return int(glucose_data["time"].dt.year.mode().iloc[0])


def load_food_insulin_events(path: Path, year: int) -> pd.DataFrame:
    raw = pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8-sig")
    raw = raw.loc[:, ~raw.columns.str.startswith("Unnamed")]
    required = {"Дата", "Время", "Длинный инсулин/Короткий инсулин", "ХЕ", "Примечания"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")

    records = []
    for _, row in raw.iterrows():
        long_insulin, short_insulin = parse_insulin_pair(row["Длинный инсулин/Короткий инсулин"])
        note = "" if pd.isna(row["Примечания"]) else str(row["Примечания"]).strip()
        if note == "-":
            note = ""
        records.append(
            {
                "time": parse_russian_date(row["Дата"], row["Время"], year),
                "carbs_xe": parse_optional_amount(row["ХЕ"]),
                "long_insulin_units": long_insulin,
                "short_insulin_units": short_insulin,
                "notes": note,
            }
        )

    events = pd.DataFrame(records).dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    if events.empty:
        raise ValueError(f"No valid food/insulin rows parsed from {path}")
    return events


def add_glucose_at_event(events: pd.DataFrame, glucose_data: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        events["glucose_at_event"] = pd.Series(dtype=float)
        return events

    glucose_times = glucose_data["time"].astype("int64").to_numpy()
    glucose_values = glucose_data["glucose"].to_numpy(dtype=float)
    event_times = events["time"].astype("int64").to_numpy()
    interpolated = np.interp(event_times, glucose_times, glucose_values, left=np.nan, right=np.nan)

    events = events.copy()
    events["glucose_at_event"] = interpolated
    return events


def save_events_dataset(events: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(output, index=False)
    print(f"Saved parsed food/insulin dataset to {output.resolve()}")


def normalize_food_token(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None

    token = str(value).strip().lower().replace("ё", "е")
    token = re.sub(r"\s+", " ", token)
    token = token.strip(" .,:;")
    if not token or token == "-":
        return None

    return FOOD_ALIASES.get(token, token)


def extract_food_tokens(note: object) -> list[str]:
    if note is None or pd.isna(note):
        return []

    note_text = str(note).strip()
    normalized_note = note_text.lower().replace("ё", "е")
    if not normalized_note or normalized_note == "-":
        return []
    if any(pattern in normalized_note for pattern in NON_FOOD_NOTE_PATTERNS):
        return []

    tokens: list[str] = []
    for part in re.split(r"[,;]", note_text):
        token = normalize_food_token(part)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def build_food_vocabulary(events: pd.DataFrame) -> pd.DataFrame:
    counts: Counter[str] = Counter()
    first_seen: dict[str, pd.Timestamp] = {}

    for _, row in events.iterrows():
        for token in extract_food_tokens(row["notes"]):
            counts[token] += 1
            first_seen.setdefault(token, row["time"])

    rows = []
    for food_name in sorted(counts, key=lambda name: (first_seen[name], name)):
        rows.append(
            {
                "food_id": len(rows) + 1,
                "food_name": food_name,
                "count": counts[food_name],
                "first_seen": first_seen[food_name],
            }
        )
    return pd.DataFrame(rows)


def vectorize_food_notes(events: pd.DataFrame, vocabulary: pd.DataFrame) -> pd.DataFrame:
    vectorized = events.copy()
    tokens_by_row = [extract_food_tokens(note) for note in vectorized["notes"]]
    food_id_by_name = dict(zip(vocabulary["food_name"], vocabulary["food_id"]))

    vectorized["food_ids"] = [
        "|".join(str(food_id_by_name[token]) for token in tokens if token in food_id_by_name)
        for tokens in tokens_by_row
    ]
    vectorized["food_names"] = ["|".join(tokens) for tokens in tokens_by_row]

    for _, row in vocabulary.iterrows():
        column = f"food_{int(row['food_id']):03d}"
        food_name = row["food_name"]
        vectorized[column] = [1 if food_name in tokens else 0 for tokens in tokens_by_row]

    return vectorized


def save_food_features(
    events: pd.DataFrame,
    vocabulary_output: Path,
    vectorized_output: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    vocabulary = build_food_vocabulary(events)
    vectorized = vectorize_food_notes(events, vocabulary)

    vocabulary_output.parent.mkdir(parents=True, exist_ok=True)
    vectorized_output.parent.mkdir(parents=True, exist_ok=True)
    vocabulary.to_csv(vocabulary_output, index=False)
    vectorized.to_csv(vectorized_output, index=False)

    print(f"Saved numbered food vocabulary to {vocabulary_output.resolve()}")
    print(f"Saved vectorized event dataset to {vectorized_output.resolve()}")
    return vocabulary, vectorized


def target_range(unit: str) -> tuple[float, float]:
    if unit == "mg/dL":
        return 70.0, 180.0
    return 3.9, 10.0


def marker_sizes(values: pd.Series, base: float = 9.0, scale: float = 1.6, max_size: float = 26.0) -> pd.Series:
    return (base + values.fillna(0) * scale).clip(upper=max_size)


def add_event_trace(
    fig: go.Figure,
    events: pd.DataFrame,
    value_column: str,
    name: str,
    unit_label: str,
    color: str,
    symbol: str,
    glucose_unit: str,
) -> None:
    selected = events.loc[(events[value_column] > 0) & events["glucose_at_event"].notna()].copy()
    if selected.empty:
        return

    customdata = np.column_stack(
        [
            selected[value_column].to_numpy(),
            selected["carbs_xe"].to_numpy(),
            selected["short_insulin_units"].to_numpy(),
            selected["long_insulin_units"].to_numpy(),
            selected["notes"].to_numpy(),
        ]
    )
    fig.add_trace(
        go.Scatter(
            x=selected["time"],
            y=selected["glucose_at_event"],
            mode="markers",
            name=name,
            customdata=customdata,
            marker=dict(
                color=color,
                symbol=symbol,
                size=marker_sizes(selected[value_column]),
                line=dict(color="#111111", width=0.8),
                opacity=0.82,
            ),
            hovertemplate=(
                "%{x|%Y-%m-%d %H:%M}<br>"
                f"Глюкоза: %{{y:.2f}} {glucose_unit}<br>"
                f"{unit_label}: %{{customdata[0]:.2f}}<br>"
                "ХЕ: %{customdata[1]:.2f}<br>"
                "Короткий инсулин: %{customdata[2]:.2f} U<br>"
                "Длинный инсулин: %{customdata[3]:.2f} U<br>"
                "Примечание: %{customdata[4]}"
                f"<extra>{name}</extra>"
            ),
        )
    )


def plot_glucose(
    data: pd.DataFrame,
    unit: str,
    source_path: Path,
    output: Path,
    show: bool,
    events: pd.DataFrame | None = None,
    events_source_path: Path | None = None,
) -> None:
    low, high = target_range(unit)
    start = data["time"].min().strftime("%Y-%m-%d %H:%M")
    end = data["time"].max().strftime("%Y-%m-%d %H:%M")
    events = pd.DataFrame() if events is None else events

    fig = go.Figure()
    fig.add_hrect(y0=low, y1=high, fillcolor="#d8f0d2", opacity=0.45, line_width=0)
    fig.add_hline(y=low, line_color="#d62728", line_width=1, opacity=0.75)
    fig.add_hline(y=high, line_color="#ff7f0e", line_width=1, opacity=0.75)
    fig.add_trace(
        go.Scatter(
            x=data["time"],
            y=data["glucose"],
            mode="lines",
            name=f"Glucose ({unit})",
            line=dict(color="#1f77b4", width=1.6),
            hovertemplate=f"%{{x|%Y-%m-%d %H:%M}}<br>%{{y:.2f}} {unit}<extra>Glucose</extra>",
        )
    )
    add_event_trace(fig, events, "carbs_xe", "Углеводы", "ХЕ", "#ffbf00", "circle", unit)
    add_event_trace(fig, events, "short_insulin_units", "Короткий инсулин", "Инсулин U", "#d62728", "triangle-down", unit)
    add_event_trace(fig, events, "long_insulin_units", "Длинный инсулин", "Инсулин U", "#9467bd", "diamond", unit)

    event_summary = ""
    if not events.empty:
        carbs_count = int((events["carbs_xe"] > 0).sum())
        short_count = int((events["short_insulin_units"] > 0).sum())
        long_count = int((events["long_insulin_units"] > 0).sum())
        event_source = f"; {events_source_path.name}" if events_source_path else ""
        event_summary = (
            f"; events={len(events)}{event_source}; "
            f"carbs={carbs_count}, short insulin={short_count}, long insulin={long_count}"
        )

    fig.update_layout(
        title=(
            f"Personal glucose monitor data ({start} to {end})<br>"
            f"<sup>{source_path.name}; glucose rows={len(data)}{event_summary}</sup>"
        ),
        template="plotly_white",
        height=720,
        hovermode="x unified",
        xaxis=dict(title="Time", rangeslider=dict(visible=True), showspikes=True),
        yaxis=dict(title=f"Glucose {unit}"),
        margin=dict(l=80, r=40, t=90, b=60),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(
        output,
        include_plotlyjs=True,
        full_html=True,
        auto_open=show,
        config={"displaylogo": False, "responsive": True},
    )
    print(f"Parsed {len(data)} glucose rows from {source_path}")
    print(f"Saved interactive glucose plot to {output.resolve()}")


def main() -> None:
    args = parse_args()
    dataset_dir = find_dataset_dir(args.dataset_dir)
    monitor_file = find_monitor_file(dataset_dir, args.monitor_file)
    data, unit = load_glucose_monitor(monitor_file)

    events = None
    events_file = None
    if not args.no_events:
        events_file = find_events_file(dataset_dir, args.events_file)
        events_year = infer_events_year(data, args.events_year)
        events = load_food_insulin_events(events_file, events_year)
        events = add_glucose_at_event(events, data)
        save_events_dataset(events, args.events_output)
        vocabulary, _vectorized = save_food_features(events, args.food_vocab_output, args.vectorized_events_output)
        print(f"Parsed {len(events)} food/insulin rows from {events_file}")
        print(f"Extracted {len(vocabulary)} numbered food variants from notes")

    plot_glucose(data, unit, monitor_file, args.output, args.show, events, events_file)


if __name__ == "__main__":
    main()

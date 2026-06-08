#!/usr/bin/env python3
"""Plot personal glucose monitor data with Plotly."""

from __future__ import annotations

import argparse
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


OOXML_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RELS_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DEFAULT_MONITOR_GLOB = "Hematonix*_dat.xls*"


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
        "--output",
        type=Path,
        default=Path("outputs/my_glucose_overview.html"),
        help="Output HTML file.",
    )
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
    if not text:
        return None
    return pd.to_numeric(text, errors="coerce")


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


def target_range(unit: str) -> tuple[float, float]:
    if unit == "mg/dL":
        return 70.0, 180.0
    return 3.9, 10.0


def plot_glucose(data: pd.DataFrame, unit: str, source_path: Path, output: Path, show: bool) -> None:
    low, high = target_range(unit)
    start = data["time"].min().strftime("%Y-%m-%d %H:%M")
    end = data["time"].max().strftime("%Y-%m-%d %H:%M")

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
    fig.update_layout(
        title=(
            f"Personal glucose monitor data ({start} to {end})<br>"
            f"<sup>{source_path.name}; rows={len(data)}</sup>"
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
    plot_glucose(data, unit, monitor_file, args.output, args.show)


if __name__ == "__main__":
    main()

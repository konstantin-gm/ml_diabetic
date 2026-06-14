from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app.agent.schemas import JournalEntryCreate
from app.services.carbs import bread_units_to_carbohydrates

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_SPREADSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"x": _SPREADSHEET_NAMESPACE}
_MONTHS = {
    "янв": 1,
    "января": 1,
    "фев": 2,
    "февраля": 2,
    "мар": 3,
    "марта": 3,
    "апр": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июня": 6,
    "июл": 7,
    "июля": 7,
    "авг": 8,
    "августа": 8,
    "сен": 9,
    "сент": 9,
    "сентября": 9,
    "окт": 10,
    "октября": 10,
    "ноя": 11,
    "ноября": 11,
    "дек": 12,
    "декабря": 12,
}
_ACTIVITY_WORDS = (
    "бег",
    "велосипед",
    "зарядк",
    "плаван",
    "прогул",
    "спорт",
    "трениров",
    "ходьб",
)
_DURATION = re.compile(r"(?P<minutes>\d+)\s*(?:мин(?:ут[аы]?)?|min)\b", re.IGNORECASE)
_DATE_WITHOUT_YEAR = re.compile(
    r"^(?P<day>\d{1,2})\s+(?P<month>[а-яё.]+)(?:\s+(?P<year>\d{4}))?$",
    re.IGNORECASE,
)


class JournalImportError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedJournalImport:
    source: str
    entries: list[JournalEntryCreate]


def parse_journal_file(
    payload: bytes,
    filename: str,
    default_year: int,
    timezone: tzinfo,
    carbs_per_bread_unit: Decimal = Decimal("12"),
) -> ParsedJournalImport:
    if not payload:
        raise JournalImportError("Файл пуст.")
    if len(payload) > MAX_IMPORT_BYTES:
        raise JournalImportError("Размер файла превышает 10 МБ.")

    suffix = PurePath(filename).suffix.lower()
    if payload.startswith(b"PK\x03\x04") or suffix in {".xls", ".xlsx"}:
        return ParsedJournalImport("Hematonix", _parse_hematonix(payload, timezone))
    return ParsedJournalImport(
        "MelStudio",
        _parse_melstudio(payload, default_year, timezone, carbs_per_bread_unit),
    )


def parse_import_year(value: str | None, fallback: int) -> int:
    if not value or not value.strip():
        return fallback
    match = re.search(r"\b(20\d{2})\b", value)
    if match is None:
        raise JournalImportError("Укажите год так: /import 2026")
    return int(match.group(1))


def _parse_hematonix(payload: bytes, timezone: tzinfo) -> list[JournalEntryCreate]:
    try:
        with ZipFile(io.BytesIO(payload)) as workbook:
            if sum(item.file_size for item in workbook.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise JournalImportError("Распакованный файл Hematonix превышает 50 МБ.")
            shared_strings = _read_shared_strings(workbook)
            sheet_path = _first_sheet_path(workbook)
            root = ElementTree.fromstring(workbook.read(sheet_path))
    except (BadZipFile, KeyError, ElementTree.ParseError) as error:
        raise JournalImportError("Не удалось прочитать файл монитора Hematonix.") from error

    rows = [_read_row(row, shared_strings) for row in root.findall(".//x:row", _NS)]
    if not rows or "время" not in rows[0].get("A", "").lower():
        raise JournalImportError("Не найдена колонка времени Hematonix.")
    if "ммоль/л" not in rows[0].get("B", "").lower():
        raise JournalImportError("Не найдена колонка показаний Hematonix в ммоль/л.")

    entries: list[JournalEntryCreate] = []
    for line_number, row in enumerate(rows[1:], start=2):
        timestamp = row.get("A", "").strip()
        glucose = row.get("B", "").strip()
        if not timestamp and not glucose:
            continue
        try:
            occurred_at = datetime.strptime(timestamp, "%d.%m.%Y %H:%M").replace(tzinfo=timezone)
            entries.append(
                JournalEntryCreate(
                    occurred_at=occurred_at,
                    blood_glucose_mmol_l=_decimal(glucose),
                )
            )
        except (ValueError, InvalidOperation) as error:
            raise JournalImportError(
                f"Некорректные данные Hematonix в строке {line_number}."
            ) from error

    if not entries:
        raise JournalImportError("В файле Hematonix нет показаний.")
    return entries


def _parse_melstudio(
    payload: bytes,
    default_year: int,
    timezone: tzinfo,
    carbs_per_bread_unit: Decimal,
) -> list[JournalEntryCreate]:
    text = _decode_text(payload)
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    if not rows or len(rows[0]) < 5 or rows[0][0].strip().lower() != "дата":
        raise JournalImportError("Не найден заголовок дневника MelStudio.")

    entries: list[JournalEntryCreate] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < 5:
            raise JournalImportError(f"Недостаточно колонок в строке {line_number}.")
        try:
            occurred_at = _parse_melstudio_datetime(
                row[0].strip(), row[1].strip(), default_year, timezone
            )
            long_insulin, short_insulin = _parse_insulin(row[2])
            bread_units = _optional_decimal(row[3])
            note = _optional_text(row[4])
            activity = note if note and _is_activity(note) else None
            entries.append(
                JournalEntryCreate(
                    occurred_at=occurred_at,
                    duration_minutes=_parse_duration(activity),
                    short_insulin_units=short_insulin,
                    long_insulin_units=long_insulin,
                    food=None if activity else note,
                    carbohydrates_grams=(
                        bread_units_to_carbohydrates(bread_units, carbs_per_bread_unit)
                        if bread_units is not None
                        else None
                    ),
                    physical_activity=activity,
                )
            )
        except (ValueError, InvalidOperation) as error:
            raise JournalImportError(
                f"Некорректные данные MelStudio в строке {line_number}."
            ) from error

    if not entries:
        raise JournalImportError("В дневнике MelStudio нет записей.")
    return entries


def _read_shared_strings(workbook: ZipFile) -> list[str]:
    root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", _NS))
        for item in root.findall("x:si", _NS)
    ]


def _first_sheet_path(workbook: ZipFile) -> str:
    preferred = "xl/worksheets/sheet1.xml"
    if preferred in workbook.namelist():
        return preferred
    sheets = sorted(name for name in workbook.namelist() if name.startswith("xl/worksheets/"))
    if not sheets:
        raise KeyError("worksheet")
    return sheets[0]


def _read_row(row: ElementTree.Element, shared_strings: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for cell in row.findall("x:c", _NS):
        reference = cell.get("r", "")
        column = re.sub(r"\d", "", reference)
        value = cell.find("x:v", _NS)
        if not column or value is None or value.text is None:
            continue
        raw = value.text
        values[column] = shared_strings[int(raw)] if cell.get("t") == "s" else raw
    return values


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise JournalImportError("Текстовый файл должен быть в UTF-8, UTF-16 или Windows-1251.")


def _parse_melstudio_datetime(
    date_value: str,
    time_value: str,
    default_year: int,
    timezone: tzinfo,
) -> datetime:
    match = _DATE_WITHOUT_YEAR.fullmatch(date_value.strip())
    if match is None:
        raise ValueError("invalid date")
    month_name = match.group("month").lower().rstrip(".")
    month = _MONTHS.get(month_name)
    if month is None:
        raise ValueError("invalid month")
    year = int(match.group("year") or default_year)
    hour, minute = (int(part) for part in time_value.split(":"))
    return datetime(year, month, int(match.group("day")), hour, minute, tzinfo=timezone)


def _parse_insulin(value: str) -> tuple[Decimal | None, Decimal | None]:
    parts = value.strip().split("/")
    if len(parts) != 2:
        raise ValueError("invalid insulin")
    return _optional_decimal(parts[0]), _optional_decimal(parts[1])


def _optional_decimal(value: str) -> Decimal | None:
    normalized = value.strip().replace(",", ".")
    if normalized in {"", "-"}:
        return None
    return Decimal(normalized)


def _decimal(value: str) -> Decimal:
    return Decimal(value.strip().replace(",", "."))


def _optional_text(value: str) -> str | None:
    normalized = value.strip()
    return None if normalized in {"", "-"} else normalized


def _is_activity(value: str) -> bool:
    normalized = value.lower().replace("ё", "е")
    return any(word in normalized for word in _ACTIVITY_WORDS)


def _parse_duration(value: str | None) -> int | None:
    if value is None:
        return None
    match = _DURATION.search(value)
    return int(match.group("minutes")) if match else None

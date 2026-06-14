from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import datetime, tzinfo
from decimal import Decimal

from app.agent.schemas import JournalEntryRecord

CSV_COLUMNS = (
    "id",
    "occurred_at",
    "duration_minutes",
    "short_insulin_units",
    "long_insulin_units",
    "food",
    "carbohydrates_grams",
    "physical_activity",
    "blood_glucose_mmol_l",
    "created_at",
)


def build_journal_csv(
    entries: Sequence[JournalEntryRecord],
    display_timezone: tzinfo,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for entry in entries:
        writer.writerow(
            {
                "id": entry.id,
                "occurred_at": _local_datetime(entry.occurred_at, display_timezone),
                "duration_minutes": entry.duration_minutes or "",
                "short_insulin_units": _optional_decimal(entry.short_insulin_units),
                "long_insulin_units": _optional_decimal(entry.long_insulin_units),
                "food": _csv_text(entry.food or ""),
                "carbohydrates_grams": _optional_decimal(entry.carbohydrates_grams),
                "physical_activity": _csv_text(entry.physical_activity or ""),
                "blood_glucose_mmol_l": _optional_decimal(entry.blood_glucose_mmol_l),
                "created_at": _local_datetime(entry.created_at, display_timezone),
            }
        )
    return output.getvalue().encode("utf-8-sig")


def _local_datetime(value: datetime, display_timezone: tzinfo) -> str:
    return value.astimezone(display_timezone).isoformat(timespec="seconds")


def _optional_decimal(value: Decimal | None) -> str:
    return format(value.normalize(), "f") if value is not None else ""


def _csv_text(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value

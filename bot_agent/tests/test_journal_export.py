import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.agent.schemas import JournalEntryRecord
from app.services.journal_export import build_journal_csv


def test_builds_excel_friendly_journal_csv_in_display_timezone() -> None:
    entry = _entry()

    payload = build_journal_csv([entry], ZoneInfo("Europe/Moscow"))
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    assert payload.startswith(b"\xef\xbb\xbf")
    assert rows[0]["occurred_at"] == "2026-06-14T12:30:00+03:00"
    assert rows[0]["short_insulin_units"] == "3"
    assert rows[0]["carbohydrates_grams"] == "35.5"
    assert rows[0]["blood_glucose_mmol_l"] == "6.4"
    assert rows[0]["food"] == "гречка"


def test_journal_csv_escapes_spreadsheet_formulas() -> None:
    entry = _entry(food="=IMPORTXML()", physical_activity="+CMD")

    payload = build_journal_csv([entry], ZoneInfo("UTC"))
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    assert rows[0]["food"] == "'=IMPORTXML()"
    assert rows[0]["physical_activity"] == "'+CMD"


def _entry(
    food: str = "гречка",
    physical_activity: str = "прогулка",
) -> JournalEntryRecord:
    return JournalEntryRecord(
        id=1,
        telegram_user_id=1001,
        occurred_at=datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
        duration_minutes=30,
        short_insulin_units=Decimal("3"),
        long_insulin_units=None,
        food=food,
        carbohydrates_grams=Decimal("35.5"),
        physical_activity=physical_activity,
        blood_glucose_mmol_l=Decimal("6.4"),
        created_at=datetime(2026, 6, 14, 9, 31, tzinfo=UTC),
    )

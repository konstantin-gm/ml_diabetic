import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

from app.agent.schemas import FoodRecord
from app.services.food_export import build_foods_csv, format_food_messages


def _food(index: int, name: str = "Хлеб") -> FoodRecord:
    timestamp = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    return FoodRecord(
        id=index,
        canonical_name=f"food_{index}",
        ru_name=name,
        en_name="bread",
        carbs_per_100g=Decimal("42.50"),
        protein_per_100g=Decimal("8.10"),
        fat_per_100g=Decimal("2.00"),
        kcal_per_100g=Decimal("230"),
        source="user_provided",
        confidence=Decimal("1.00"),
        aliases=["хлеб", "мой хлеб"],
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_formats_food_list_and_respects_message_limit() -> None:
    messages = format_food_messages(
        [_food(index, name=f"Очень длинное название продукта {index}") for index in range(1, 8)],
        max_length=180,
    )

    assert len(messages) > 1
    assert all(len(message) <= 180 for message in messages)
    assert "Продукты в базе: 7" in messages[0]
    assert "42.5 г углеводов/100 г" in messages[0]


def test_builds_excel_friendly_utf8_csv() -> None:
    payload = build_foods_csv([_food(1)])
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    assert payload.startswith(b"\xef\xbb\xbf")
    assert rows[0]["ru_name"] == "Хлеб"
    assert rows[0]["carbs_per_100g"] == "42.5"
    assert rows[0]["aliases"] == "хлеб;мой хлеб"


def test_csv_escapes_spreadsheet_formulas() -> None:
    food = _food(1, name="=IMPORTXML()")
    payload = build_foods_csv([food])
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    assert rows[0]["ru_name"] == "'=IMPORTXML()"

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agent.schemas import FoodRecord
from app.services.food_export import build_foods_csv
from app.services.food_import import FoodImportError, parse_foods_csv


def test_imports_exported_food_csv_round_trip() -> None:
    timestamp = datetime(2026, 6, 14, tzinfo=UTC)
    payload = build_foods_csv(
        [
            FoodRecord(
                id=1,
                canonical_name="buckwheat_cooked",
                ru_name="гречка вареная",
                en_name="cooked buckwheat",
                carbs_per_100g=Decimal("19.9"),
                protein_per_100g=Decimal("3.6"),
                fat_per_100g=Decimal("0.6"),
                kcal_per_100g=Decimal("92"),
                glycemic_index=Decimal("49"),
                source="https://example.com/buckwheat",
                confidence=Decimal("0.9"),
                aliases=["греча", "гречневая каша"],
                created_at=timestamp,
                updated_at=timestamp,
            )
        ]
    )

    parsed = parse_foods_csv(payload)

    assert len(parsed.foods) == 1
    assert parsed.foods[0].canonical_name == "buckwheat_cooked"
    assert parsed.foods[0].protein_per_100g == Decimal("3.6")
    assert parsed.foods[0].glycemic_index == Decimal("49")
    assert parsed.foods[0].aliases == ["греча", "гречневая каша"]


def test_imports_semicolon_csv_with_decimal_comma_and_defaults() -> None:
    payload = (
        "canonical_name;ru_name;carbs_per_100g;protein_per_100g\n"
        "apple_raw;яблоко;13,8;0,3\n"
    ).encode()

    food = parse_foods_csv(payload).foods[0]

    assert food.carbs_per_100g == Decimal("13.8")
    assert food.protein_per_100g == Decimal("0.3")
    assert food.source == "csv_import"
    assert food.confidence == Decimal("1")


def test_rejects_missing_columns_and_duplicate_products() -> None:
    with pytest.raises(FoodImportError, match="обязательные колонки"):
        parse_foods_csv(b"ru_name,carbs_per_100g\nApple,13.8\n")

    duplicate = (
        b"canonical_name,ru_name,carbs_per_100g\n"
        b"apple_raw,Apple,13.8\n"
        b"apple_raw,Another apple,14\n"
    )
    with pytest.raises(FoodImportError, match="Повтор canonical_name"):
        parse_foods_csv(duplicate)


def test_reports_invalid_row_number() -> None:
    payload = (
        b"canonical_name,ru_name,carbs_per_100g\n"
        b"apple_raw,Apple,not-a-number\n"
    )

    with pytest.raises(FoodImportError, match="строке 2"):
        parse_foods_csv(payload)


def test_rejects_extra_columns_without_headers() -> None:
    payload = (
        b"canonical_name,ru_name,carbs_per_100g\n"
        b"apple_raw,Apple,13.8,unexpected\n"
    )

    with pytest.raises(FoodImportError, match="Лишние колонки"):
        parse_foods_csv(payload)

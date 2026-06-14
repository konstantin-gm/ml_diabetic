from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from decimal import Decimal

from app.agent.schemas import FoodRecord

TELEGRAM_MESSAGE_LIMIT = 4096
CSV_COLUMNS = (
    "id",
    "canonical_name",
    "ru_name",
    "en_name",
    "carbs_per_100g",
    "protein_per_100g",
    "fat_per_100g",
    "kcal_per_100g",
    "glycemic_index",
    "source",
    "confidence",
    "aliases",
    "created_at",
    "updated_at",
)


def format_food_messages(
    foods: Sequence[FoodRecord], max_length: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    if not foods:
        return ["База продуктов пока пуста."]

    header = f"Продукты в базе: {len(foods)}\n"
    lines = [_format_food_line(index, food) for index, food in enumerate(foods, start=1)]
    return _chunk_lines(header, lines, max_length)


def build_foods_csv(foods: Sequence[FoodRecord]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for food in foods:
        writer.writerow(
            {
                "id": food.id,
                "canonical_name": food.canonical_name,
                "ru_name": _csv_text(food.ru_name),
                "en_name": _csv_text(food.en_name or ""),
                "carbs_per_100g": _decimal(food.carbs_per_100g),
                "protein_per_100g": _optional_decimal(food.protein_per_100g),
                "fat_per_100g": _optional_decimal(food.fat_per_100g),
                "kcal_per_100g": _optional_decimal(food.kcal_per_100g),
                "glycemic_index": _optional_decimal(food.glycemic_index),
                "source": _csv_text(food.source),
                "confidence": _decimal(food.confidence),
                "aliases": _csv_text(";".join(food.aliases)),
                "created_at": food.created_at.isoformat(),
                "updated_at": food.updated_at.isoformat(),
            }
        )
    return output.getvalue().encode("utf-8-sig")


def _chunk_lines(header: str, lines: Sequence[str], max_length: int) -> list[str]:
    if max_length <= len(header):
        raise ValueError("max_length is too small for the header")

    messages: list[str] = []
    current = header
    for line in lines:
        addition = f"\n{line}"
        if len(current) + len(addition) > max_length:
            messages.append(current)
            current = line
        else:
            current += addition
    messages.append(current)
    return messages


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _optional_decimal(value: Decimal | None) -> str:
    return _decimal(value) if value is not None else ""


def _source_label(source: str) -> str:
    return "пользователь" if source == "user_provided" else "онлайн"


def _format_food_line(index: int, food: FoodRecord) -> str:
    nutrients = [f"У {_decimal(food.carbs_per_100g)} г"]
    if food.protein_per_100g is not None:
        nutrients.append(f"Б {_decimal(food.protein_per_100g)} г")
    if food.fat_per_100g is not None:
        nutrients.append(f"Ж {_decimal(food.fat_per_100g)} г")
    if food.kcal_per_100g is not None:
        nutrients.append(f"{_decimal(food.kcal_per_100g)} ккал")
    if food.glycemic_index is not None:
        nutrients.append(f"ГИ {_decimal(food.glycemic_index)}")
    return (
        f"{index}. {food.ru_name} — {', '.join(nutrients)} на 100 г "
        f"({_source_label(food.source)})"
    )


def _csv_text(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value

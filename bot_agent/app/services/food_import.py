from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.agent.schemas import FoodData

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
REQUIRED_COLUMNS = {"canonical_name", "ru_name", "carbs_per_100g"}


class FoodImportError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedFoodImport:
    foods: list[FoodData]


def parse_foods_csv(payload: bytes) -> ParsedFoodImport:
    if not payload:
        raise FoodImportError("Файл пуст.")
    if len(payload) > MAX_IMPORT_BYTES:
        raise FoodImportError("Размер CSV превышает 10 МБ.")

    text = _decode_text(payload)
    reader = csv.DictReader(io.StringIO(text), dialect=_detect_dialect(text))
    columns = {column.strip() for column in reader.fieldnames or [] if column}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise FoodImportError(
            "Отсутствуют обязательные колонки: " + ", ".join(sorted(missing))
        )

    foods: list[FoodData] = []
    canonical_names: set[str] = set()
    for line_number, raw_row in enumerate(reader, start=2):
        if line_number > MAX_IMPORT_ROWS + 1:
            raise FoodImportError(f"CSV содержит больше {MAX_IMPORT_ROWS} строк.")
        if None in raw_row:
            raise FoodImportError(f"Лишние колонки без заголовка в строке {line_number}.")
        row = {(key or "").strip(): (value or "").strip() for key, value in raw_row.items()}
        if not any(row.values()):
            continue
        try:
            food = FoodData(
                canonical_name=_text(row, "canonical_name", required=True),
                ru_name=_text(row, "ru_name", required=True),
                en_name=_optional_text(row.get("en_name", "")),
                carbs_per_100g=_decimal(row, "carbs_per_100g", required=True),
                protein_per_100g=_optional_decimal(row.get("protein_per_100g", "")),
                fat_per_100g=_optional_decimal(row.get("fat_per_100g", "")),
                kcal_per_100g=_optional_decimal(row.get("kcal_per_100g", "")),
                glycemic_index=_optional_decimal(row.get("glycemic_index", "")),
                source=_optional_text(row.get("source", "")) or "csv_import",
                confidence=_optional_decimal(row.get("confidence", "")) or Decimal("1"),
                aliases=_aliases(row.get("aliases", "")),
            )
        except (InvalidOperation, ValidationError, ValueError) as error:
            raise FoodImportError(f"Некорректные данные в строке {line_number}: {error}") from error
        if food.canonical_name in canonical_names:
            raise FoodImportError(
                f"Повтор canonical_name '{food.canonical_name}' в строке {line_number}."
            )
        canonical_names.add(food.canonical_name)
        foods.append(food)

    if not foods:
        raise FoodImportError("В CSV нет продуктов.")
    return ParsedFoodImport(foods)


def _detect_dialect(text: str) -> type[csv.Dialect] | csv.Dialect:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FoodImportError("CSV должен быть в UTF-8, UTF-16 или Windows-1251.")


def _text(row: dict[str, str], column: str, required: bool) -> str:
    value = _unescape_csv_text(row.get(column, ""))
    if required and not value:
        raise ValueError(f"поле {column} обязательно")
    return value


def _optional_text(value: str) -> str | None:
    normalized = _unescape_csv_text(value)
    return normalized or None


def _decimal(row: dict[str, str], column: str, required: bool) -> Decimal:
    value = row.get(column, "").strip()
    if required and not value:
        raise ValueError(f"поле {column} обязательно")
    return Decimal(value.replace(",", "."))


def _optional_decimal(value: str) -> Decimal | None:
    normalized = value.strip()
    return Decimal(normalized.replace(",", ".")) if normalized else None


def _aliases(value: str) -> list[str]:
    normalized = _unescape_csv_text(value)
    return [alias.strip() for alias in normalized.split(";") if alias.strip()]


def _unescape_csv_text(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > 1 and normalized[0] == "'" and normalized[1] in "=+-@":
        return normalized[1:]
    return normalized

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def calculate_carbs_per_100g(carbs_grams: Decimal, amount_grams: Decimal) -> Decimal:
    if carbs_grams < 0:
        raise ValueError("carbs_grams cannot be negative")
    if amount_grams <= 0:
        raise ValueError("amount_grams must be positive")
    if carbs_grams > amount_grams:
        raise ValueError("carbs_grams cannot exceed amount_grams")

    result = carbs_grams * Decimal(100) / amount_grams
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_carbohydrates(carbs_per_100g: Decimal, amount_grams: Decimal) -> Decimal:
    if carbs_per_100g < 0:
        raise ValueError("carbs_per_100g cannot be negative")
    if amount_grams <= 0:
        raise ValueError("amount_grams must be positive")

    result = amount_grams * carbs_per_100g / Decimal(100)
    return result.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def bread_units_to_carbohydrates(
    bread_units: Decimal,
    carbs_per_bread_unit: Decimal,
) -> Decimal:
    if bread_units < 0:
        raise ValueError("bread_units cannot be negative")
    if carbs_per_bread_unit <= 0:
        raise ValueError("carbs_per_bread_unit must be positive")
    return (bread_units * carbs_per_bread_unit).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def resolve_journal_carbohydrates(
    carbohydrates_grams: Decimal | None,
    bread_units: Decimal | None,
    carbs_per_bread_unit: Decimal,
) -> Decimal | None:
    if carbohydrates_grams is not None and bread_units is not None:
        raise ValueError("specify carbohydrates either in grams or bread units, not both")
    if carbohydrates_grams is not None:
        if carbohydrates_grams < 0:
            raise ValueError("carbohydrates_grams cannot be negative")
        return carbohydrates_grams.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if bread_units is not None:
        return bread_units_to_carbohydrates(bread_units, carbs_per_bread_unit)
    return None

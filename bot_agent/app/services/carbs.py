from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def calculate_carbohydrates(carbs_per_100g: Decimal, amount_grams: Decimal) -> Decimal:
    if carbs_per_100g < 0:
        raise ValueError("carbs_per_100g cannot be negative")
    if amount_grams <= 0:
        raise ValueError("amount_grams must be positive")

    result = amount_grams * carbs_per_100g / Decimal(100)
    return result.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

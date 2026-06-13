from decimal import Decimal

import pytest

from app.services.carbs import calculate_carbohydrates, calculate_carbs_per_100g


def test_calculates_carbs_and_rounds_to_one_decimal() -> None:
    assert calculate_carbohydrates(Decimal("19.9"), Decimal("150")) == Decimal("29.9")


def test_normalizes_user_carbs_to_100_grams() -> None:
    assert calculate_carbs_per_100g(Decimal("18"), Decimal("30")) == Decimal("60.00")


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1")])
def test_rejects_non_positive_amount(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        calculate_carbohydrates(Decimal("19.9"), amount)


def test_rejects_more_carbs_than_product_mass() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        calculate_carbs_per_100g(Decimal("31"), Decimal("30"))

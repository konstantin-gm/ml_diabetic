from decimal import Decimal

import pytest

from app.services.carbs import calculate_carbohydrates


def test_calculates_carbs_and_rounds_to_one_decimal() -> None:
    assert calculate_carbohydrates(Decimal("19.9"), Decimal("150")) == Decimal("29.9")


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1")])
def test_rejects_non_positive_amount(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        calculate_carbohydrates(Decimal("19.9"), amount)

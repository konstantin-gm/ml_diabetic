from decimal import Decimal

import pytest

from app.config import Settings


def test_parses_unique_admin_ids() -> None:
    settings = Settings(
        telegram_bot_token="token",
        openai_api_key="key",
        telegram_admin_ids="1001, 2002,1001",
    )

    assert settings.parsed_telegram_admin_ids() == [1001, 2002]


def test_requires_bootstrap_admin() -> None:
    settings = Settings(
        telegram_bot_token="token",
        openai_api_key="key",
        telegram_admin_ids="",
    )

    with pytest.raises(ValueError, match="at least one"):
        settings.parsed_telegram_admin_ids()


def test_configures_bread_unit_conversion() -> None:
    settings = Settings(
        telegram_bot_token="token",
        openai_api_key="key",
        telegram_admin_ids="1001",
        journal_xe_carbs_grams=Decimal("10"),
    )

    assert settings.journal_xe_carbs_grams == Decimal("10")

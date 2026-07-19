from __future__ import annotations

from decimal import Decimal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://diabet:diabet@localhost:5432/diabet"
    admin_api_token: SecretStr = Field(min_length=32)
    journal_timezone: str = "Europe/Moscow"
    journal_xe_carbs_grams: Decimal = Field(default=Decimal("12"), gt=0, le=100)

    @field_validator("admin_api_token")
    @classmethod
    def reject_placeholder_token(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value().lower().startswith("replace"):
            raise ValueError("ADMIN_API_TOKEN must be replaced with a random token")
        return value

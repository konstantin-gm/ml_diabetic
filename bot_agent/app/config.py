from __future__ import annotations

from pydantic import SecretStr
from pydantic_ai.models.openai import OpenAIModelName
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    openai_api_key: SecretStr
    openai_model: OpenAIModelName = "gpt-5.4-mini"
    database_url: str = "postgresql+asyncpg://diabet:diabet@localhost:5432/diabet"
    telegram_admin_ids: str = ""
    log_level: str = "INFO"

    def parsed_telegram_admin_ids(self) -> list[int]:
        values = [value.strip() for value in self.telegram_admin_ids.split(",")]
        try:
            admin_ids = [int(value) for value in values if value]
        except ValueError as error:
            raise ValueError("TELEGRAM_ADMIN_IDS must contain comma-separated integers") from error
        if not admin_ids or any(admin_id <= 0 for admin_id in admin_ids):
            raise ValueError("TELEGRAM_ADMIN_IDS must contain at least one positive Telegram ID")
        return list(dict.fromkeys(admin_ids))

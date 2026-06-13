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
    log_level: str = "INFO"

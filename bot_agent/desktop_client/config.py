from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DesktopSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.desktop",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_base_url: str = "http://127.0.0.1:8000"
    admin_api_token: SecretStr = Field(min_length=32)
    api_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("api_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("API_BASE_URL must start with http:// or https://")
        return normalized

    @field_validator("admin_api_token")
    @classmethod
    def reject_placeholder_token(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value().lower().startswith("replace"):
            raise ValueError("ADMIN_API_TOKEN must be replaced with the VDS token")
        return value

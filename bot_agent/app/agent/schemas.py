from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FoodData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    canonical_name: str = Field(min_length=1, max_length=200)
    ru_name: str = Field(min_length=1, max_length=200)
    en_name: str | None = Field(default=None, max_length=200)
    carbs_per_100g: Decimal = Field(ge=0, le=100)
    protein_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    fat_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    kcal_per_100g: Decimal | None = Field(default=None, ge=0)
    source: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)
    aliases: list[str] = Field(default_factory=list)

    @field_validator("canonical_name")
    @classmethod
    def canonical_name_is_machine_readable(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", normalized) is None:
            raise ValueError("canonical_name must be lowercase English snake_case")
        return normalized


class OnlineFoodData(BaseModel):
    canonical_name: str
    ru_name: str
    en_name: str | None
    carbs_per_100g: Decimal
    protein_per_100g: Decimal | None
    fat_per_100g: Decimal | None
    kcal_per_100g: Decimal | None
    confidence: Decimal
    aliases: list[str]


class FoodRecord(FoodData):
    id: int
    created_at: datetime
    updated_at: datetime


class TelegramUserRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    telegram_user_id: int
    username: str | None
    full_name: str | None
    is_admin: bool
    is_active: bool
    added_by_telegram_id: int | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class JournalEntryCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    occurred_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=10080)
    short_insulin_units: Decimal | None = Field(default=None, ge=0, le=1000)
    long_insulin_units: Decimal | None = Field(default=None, ge=0, le=1000)
    food: str | None = Field(default=None, max_length=2000)
    carbohydrates_grams: Decimal | None = Field(default=None, ge=0, le=10000)
    physical_activity: str | None = Field(default=None, max_length=2000)
    blood_glucose_mmol_l: Decimal | None = Field(default=None, gt=0, le=100)

    @field_validator("food", "physical_activity")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def has_content(self) -> JournalEntryCreate:
        values = (
            self.duration_minutes,
            self.short_insulin_units,
            self.long_insulin_units,
            self.food,
            self.carbohydrates_grams,
            self.physical_activity,
            self.blood_glucose_mmol_l,
        )
        if all(value is None for value in values):
            raise ValueError("journal entry must contain at least one value")
        return self


class JournalEntryRecord(JournalEntryCreate):
    id: int
    telegram_user_id: int
    occurred_at: datetime
    created_at: datetime

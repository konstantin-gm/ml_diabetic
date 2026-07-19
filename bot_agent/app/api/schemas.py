from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CANONICAL_NAME = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


class RowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TableField(BaseModel):
    name: str
    type: Literal["integer", "decimal", "string", "boolean", "datetime"]
    required: bool
    nullable: bool
    read_only: bool = False
    editable: bool = True


class TableInfo(BaseModel):
    name: str
    title: str
    primary_key: str
    fields: list[TableField]
    delete_warning: str | None = None


class TablePage(BaseModel):
    table: str
    primary_key: str
    offset: int
    limit: int
    total: int
    rows: list[dict[str, Any]]


class FoodRowCreate(RowPayload):
    canonical_name: str = Field(min_length=1, max_length=200)
    ru_name: str = Field(min_length=1, max_length=200)
    en_name: str | None = Field(default=None, max_length=200)
    carbs_per_100g: Decimal = Field(ge=0, le=100)
    protein_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    fat_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    kcal_per_100g: Decimal | None = Field(default=None, ge=0)
    glycemic_index: Decimal | None = Field(default=None, ge=0, le=100)
    source: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)

    @field_validator("canonical_name")
    @classmethod
    def normalize_canonical_name(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if _CANONICAL_NAME.fullmatch(normalized) is None:
            raise ValueError("canonical_name must be lowercase English snake_case")
        return normalized

    @field_validator("ru_name", "en_name", "source")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class FoodRowUpdate(RowPayload):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=200)
    ru_name: str | None = Field(default=None, min_length=1, max_length=200)
    en_name: str | None = Field(default=None, max_length=200)
    carbs_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    protein_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    fat_per_100g: Decimal | None = Field(default=None, ge=0, le=100)
    kcal_per_100g: Decimal | None = Field(default=None, ge=0)
    glycemic_index: Decimal | None = Field(default=None, ge=0, le=100)
    source: str | None = Field(default=None, min_length=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)

    @field_validator("canonical_name")
    @classmethod
    def normalize_canonical_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower().replace(" ", "_")
        if _CANONICAL_NAME.fullmatch(normalized) is None:
            raise ValueError("canonical_name must be lowercase English snake_case")
        return normalized

    @field_validator("ru_name", "en_name", "source")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def has_changes(self) -> FoodRowUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        return self


class FoodAliasRowCreate(RowPayload):
    food_id: int = Field(gt=0)
    alias: str = Field(min_length=1, max_length=200)


class FoodAliasRowUpdate(RowPayload):
    food_id: int | None = Field(default=None, gt=0)
    alias: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def has_changes(self) -> FoodAliasRowUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        return self


class TelegramUserRowCreate(RowPayload):
    telegram_user_id: int = Field(gt=0)
    username: str | None = Field(default=None, max_length=64)
    full_name: str | None = Field(default=None, max_length=255)
    is_admin: bool = False
    is_active: bool = True
    added_by_telegram_id: int | None = Field(default=None, gt=0)


class TelegramUserRowUpdate(RowPayload):
    username: str | None = Field(default=None, max_length=64)
    full_name: str | None = Field(default=None, max_length=255)
    is_admin: bool | None = None
    is_active: bool | None = None
    added_by_telegram_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def has_changes(self) -> TelegramUserRowUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        return self


class JournalRowCreate(RowPayload):
    telegram_user_id: int = Field(gt=0)
    occurred_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=10080)
    short_insulin_units: Decimal | None = Field(default=None, ge=0, le=1000)
    long_insulin_units: Decimal | None = Field(default=None, ge=0, le=1000)
    food: str | None = Field(default=None, max_length=2000)
    carbohydrates_grams: Decimal | None = Field(default=None, ge=0, le=10000)
    physical_activity: str | None = Field(default=None, max_length=2000)
    blood_glucose_mmol_l: Decimal | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def has_content(self) -> JournalRowCreate:
        content = self.model_dump(exclude={"telegram_user_id", "occurred_at"})
        if all(value is None for value in content.values()):
            raise ValueError("journal entry must contain at least one value")
        return self


class JournalRowUpdate(RowPayload):
    telegram_user_id: int | None = Field(default=None, gt=0)
    occurred_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=10080)
    short_insulin_units: Decimal | None = Field(default=None, ge=0, le=1000)
    long_insulin_units: Decimal | None = Field(default=None, ge=0, le=1000)
    food: str | None = Field(default=None, max_length=2000)
    carbohydrates_grams: Decimal | None = Field(default=None, ge=0, le=10000)
    physical_activity: str | None = Field(default=None, max_length=2000)
    blood_glucose_mmol_l: Decimal | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def has_changes(self) -> JournalRowUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        return self

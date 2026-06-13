from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

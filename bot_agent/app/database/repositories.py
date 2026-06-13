from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.schemas import FoodData
from app.database.models import Food, FoodAlias

_WHITESPACE = re.compile(r"\s+")


def normalize_food_name(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip().lower().replace("ё", "е"))


class FoodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_name(self, name: str) -> FoodData | None:
        normalized = normalize_food_name(name)
        statement = (
            select(Food)
            .outerjoin(FoodAlias)
            .options(selectinload(Food.aliases))
            .where(
                or_(
                    Food.canonical_name == normalized.replace(" ", "_"),
                    FoodAlias.alias == normalized,
                )
            )
            .limit(1)
        )
        food = await self._session.scalar(statement)
        return self._to_data(food) if food is not None else None

    async def save(self, data: FoodData) -> FoodData:
        existing = await self._session.scalar(
            select(Food)
            .options(selectinload(Food.aliases))
            .where(Food.canonical_name == data.canonical_name)
        )
        if existing is not None:
            return self._to_data(existing)

        food = Food(
            canonical_name=data.canonical_name,
            ru_name=data.ru_name.strip(),
            en_name=data.en_name.strip() if data.en_name else None,
            carbs_per_100g=data.carbs_per_100g,
            protein_per_100g=data.protein_per_100g,
            fat_per_100g=data.fat_per_100g,
            kcal_per_100g=data.kcal_per_100g,
            source=data.source,
            confidence=data.confidence,
        )
        aliases = {data.ru_name, data.canonical_name.replace("_", " "), *data.aliases}
        if data.en_name:
            aliases.add(data.en_name)
        food.aliases = [
            FoodAlias(alias=alias)
            for alias in sorted({normalize_food_name(alias) for alias in aliases if alias.strip()})
        ]
        self._session.add(food)
        await self._session.flush()
        return self._to_data(food)

    @staticmethod
    def _to_data(food: Food) -> FoodData:
        return FoodData(
            canonical_name=food.canonical_name,
            ru_name=food.ru_name,
            en_name=food.en_name,
            carbs_per_100g=food.carbs_per_100g,
            protein_per_100g=food.protein_per_100g,
            fat_per_100g=food.fat_per_100g,
            kcal_per_100g=food.kcal_per_100g,
            source=food.source,
            confidence=food.confidence,
            aliases=[alias.alias for alias in food.aliases],
        )

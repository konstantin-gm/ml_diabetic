from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic_ai import RunContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import FoodData
from app.database.repositories import FoodRepository
from app.services.carbs import calculate_carbohydrates, calculate_carbs_per_100g
from app.services.online_food import OnlineFoodLookup


@dataclass(slots=True)
class FoodAgentDeps:
    session: AsyncSession
    online_lookup: OnlineFoodLookup


async def find_food(ctx: RunContext[FoodAgentDeps], name: str) -> FoodData | None:
    """Find a food in the local database by Russian, English, canonical name, or alias."""
    return await FoodRepository(ctx.deps.session).find_by_name(name)


async def lookup_food_online(ctx: RunContext[FoodAgentDeps], name: str) -> FoodData:
    """Search reliable web sources for nutrition data when local food data is missing."""
    return await ctx.deps.online_lookup.lookup(name)


async def save_food(ctx: RunContext[FoodAgentDeps], food: FoodData) -> FoodData:
    """Save web-verified food data to the local database cache."""
    return await FoodRepository(ctx.deps.session).save(food)


async def save_user_food(
    ctx: RunContext[FoodAgentDeps],
    name: str,
    carbs_grams: Decimal,
    amount_grams: Decimal = Decimal(100),
) -> FoodData:
    """Save carbs explicitly provided by the user.

    Use only when the user states that a named food contains `carbs_grams` of
    carbohydrates in `amount_grams` of product. The value is normalized to 100 g.
    """
    carbs_per_100g = calculate_carbs_per_100g(carbs_grams, amount_grams)
    return await FoodRepository(ctx.deps.session).save_user_carbs(name, carbs_per_100g)


def calculate_carbs(
    ctx: RunContext[FoodAgentDeps], food: FoodData, amount_grams: Decimal
) -> Decimal:
    """Calculate carbohydrate grams for a positive food amount in grams."""
    del ctx
    return calculate_carbohydrates(food.carbs_per_100g, amount_grams)

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal

from pydantic_ai import RunContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import (
    FoodData,
    JournalEntryCreate,
    JournalEntryRecord,
    JournalEntryUpdate,
)
from app.database.repositories import (
    AmbiguousJournalEntryError,
    FoodRepository,
    JournalRepository,
)
from app.services.carbs import (
    calculate_carbohydrates,
    calculate_carbs_per_100g,
    resolve_journal_carbohydrates,
)
from app.services.online_food import OnlineFoodLookup

JOURNAL_ENTRY_NOT_FOUND_MESSAGE = "Отсутствует запись в данное время."


@dataclass(slots=True)
class FoodAgentDeps:
    session: AsyncSession
    online_lookup: OnlineFoodLookup
    telegram_user_id: int
    journal_timezone: tzinfo
    journal_xe_carbs_grams: Decimal


def _journal_record_for_user(
    record: JournalEntryRecord,
    display_timezone: tzinfo,
) -> JournalEntryRecord:
    def localize(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(display_timezone)

    return record.model_copy(
        update={
            "occurred_at": localize(record.occurred_at),
            "created_at": localize(record.created_at),
        }
    )


async def find_food(ctx: RunContext[FoodAgentDeps], name: str) -> FoodData | None:
    """Find a food in the local database by Russian, English, canonical name, or alias."""
    return await FoodRepository(ctx.deps.session).find_by_name(name)


async def lookup_food_online(ctx: RunContext[FoodAgentDeps], name: str) -> FoodData:
    """Search reliable web sources for carbs, protein, fat, kcal, and glycemic index."""
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


async def add_journal_entry(
    ctx: RunContext[FoodAgentDeps],
    occurred_at: datetime | None = None,
    duration_minutes: int | None = None,
    short_insulin_units: Decimal | None = None,
    long_insulin_units: Decimal | None = None,
    food: str | None = None,
    carbohydrates_grams: Decimal | None = None,
    bread_units: Decimal | None = None,
    physical_activity: str | None = None,
    blood_glucose_mmol_l: Decimal | None = None,
) -> JournalEntryRecord:
    """Add a health journal entry for the current authorized Telegram user.

    Record only values explicitly stated by the user. Carbohydrates are stored
    in grams. Pass bread_units only when the user explicitly uses ХЕ; the tool
    converts them using the configured grams-per-ХЕ value. Do not pass both
    carbohydrates_grams and bread_units. Never infer or recommend an insulin
    dose. Omit occurred_at to use the current time.
    """
    resolved_carbohydrates = resolve_journal_carbohydrates(
        carbohydrates_grams,
        bread_units,
        ctx.deps.journal_xe_carbs_grams,
    )
    data = JournalEntryCreate(
        occurred_at=occurred_at,
        duration_minutes=duration_minutes,
        short_insulin_units=short_insulin_units,
        long_insulin_units=long_insulin_units,
        food=food,
        carbohydrates_grams=resolved_carbohydrates,
        physical_activity=physical_activity,
        blood_glucose_mmol_l=blood_glucose_mmol_l,
    )
    record = await JournalRepository(ctx.deps.session).add(
        ctx.deps.telegram_user_id,
        data,
        ctx.deps.journal_timezone,
    )
    return _journal_record_for_user(record, ctx.deps.journal_timezone)


async def edit_journal_entry(
    ctx: RunContext[FoodAgentDeps],
    target_occurred_at: datetime,
    new_occurred_at: datetime | None = None,
    duration_minutes: int | None = None,
    short_insulin_units: Decimal | None = None,
    long_insulin_units: Decimal | None = None,
    food: str | None = None,
    carbohydrates_grams: Decimal | None = None,
    bread_units: Decimal | None = None,
    physical_activity: str | None = None,
    blood_glucose_mmol_l: Decimal | None = None,
) -> JournalEntryRecord | str | None:
    """Edit one journal entry belonging to the current Telegram user.

    `target_occurred_at` is the existing entry's exact local date and time to the
    minute. Pass only fields explicitly corrected by the user; omitted fields stay
    unchanged. Use `new_occurred_at` only when the entry timestamp itself changes.
    Carbohydrates may be supplied in grams or bread units, but not both.
    """
    resolved_carbohydrates = resolve_journal_carbohydrates(
        carbohydrates_grams,
        bread_units,
        ctx.deps.journal_xe_carbs_grams,
    )
    data = JournalEntryUpdate(
        occurred_at=new_occurred_at,
        duration_minutes=duration_minutes,
        short_insulin_units=short_insulin_units,
        long_insulin_units=long_insulin_units,
        food=food,
        carbohydrates_grams=resolved_carbohydrates,
        physical_activity=physical_activity,
        blood_glucose_mmol_l=blood_glucose_mmol_l,
    )
    try:
        result = await JournalRepository(ctx.deps.session).update_at(
            ctx.deps.telegram_user_id,
            target_occurred_at,
            data,
            ctx.deps.journal_timezone,
        )
        if isinstance(result, JournalEntryRecord):
            return _journal_record_for_user(result, ctx.deps.journal_timezone)
        if result is None:
            return JOURNAL_ENTRY_NOT_FOUND_MESSAGE
        return result
    except AmbiguousJournalEntryError as error:
        return str(error)


async def delete_last_journal_entry(
    ctx: RunContext[FoodAgentDeps],
) -> JournalEntryRecord | None:
    """Delete the current Telegram user's most recent journal entry.

    Use this when the user explicitly asks to delete their last or most recent
    journal entry. No date or time is required. Return null when the journal is empty.
    """
    record = await JournalRepository(ctx.deps.session).delete_last(
        ctx.deps.telegram_user_id
    )
    if record is None:
        return None
    return _journal_record_for_user(record, ctx.deps.journal_timezone)


def calculate_carbs(
    ctx: RunContext[FoodAgentDeps], food: FoodData, amount_grams: Decimal
) -> Decimal:
    """Calculate carbohydrate grams for a positive food amount in grams."""
    del ctx
    return calculate_carbohydrates(food.carbs_per_100g, amount_grams)

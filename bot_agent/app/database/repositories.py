from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.schemas import (
    FoodData,
    FoodRecord,
    JournalEntryCreate,
    JournalEntryRecord,
    JournalEntryUpdate,
    TelegramUserRecord,
)
from app.database.models import Food, FoodAlias, JournalEntry, TelegramUser

_WHITESPACE = re.compile(r"\s+")


class AmbiguousJournalEntryError(ValueError):
    pass


def normalize_food_name(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip().lower().replace("ё", "е"))


class FoodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_name(self, name: str) -> FoodData | None:
        food = await self._find_model_by_name(name)
        return self._to_data(food) if food is not None else None

    async def list_all(self) -> list[FoodRecord]:
        statement = select(Food).options(selectinload(Food.aliases)).order_by(Food.ru_name, Food.id)
        foods = (await self._session.scalars(statement)).all()
        return [self._to_record(food) for food in foods]

    async def save_user_carbs(self, name: str, carbs_per_100g: Decimal) -> FoodData:
        normalized = normalize_food_name(name)
        if not normalized:
            raise ValueError("food name cannot be empty")

        food = await self._find_model_by_name(normalized)
        if food is None:
            digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
            food = Food(
                canonical_name=f"user_food_{digest}",
                ru_name=name.strip(),
                en_name=None,
                carbs_per_100g=carbs_per_100g,
                protein_per_100g=None,
                fat_per_100g=None,
                kcal_per_100g=None,
                glycemic_index=None,
                source="user_provided",
                confidence=Decimal("1.00"),
                aliases=[FoodAlias(alias=normalized)],
            )
            self._session.add(food)
        else:
            food.carbs_per_100g = carbs_per_100g
            food.source = "user_provided"
            food.confidence = Decimal("1.00")

        await self._session.flush()
        return self._to_data(food)

    async def _find_model_by_name(self, name: str) -> Food | None:
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
        food: Food | None = await self._session.scalar(statement)
        return food

    async def save(self, data: FoodData) -> FoodData:
        existing = await self._session.scalar(
            select(Food)
            .options(selectinload(Food.aliases))
            .where(Food.canonical_name == data.canonical_name)
        )
        if existing is not None:
            if existing.source != "user_provided":
                existing.ru_name = data.ru_name.strip()
                existing.en_name = data.en_name.strip() if data.en_name else None
                existing.carbs_per_100g = data.carbs_per_100g
                existing.protein_per_100g = data.protein_per_100g
                existing.fat_per_100g = data.fat_per_100g
                existing.kcal_per_100g = data.kcal_per_100g
                existing.glycemic_index = data.glycemic_index
                existing.source = data.source
                existing.confidence = data.confidence
                await self._session.flush()
            return self._to_data(existing)

        food = Food(
            canonical_name=data.canonical_name,
            ru_name=data.ru_name.strip(),
            en_name=data.en_name.strip() if data.en_name else None,
            carbs_per_100g=data.carbs_per_100g,
            protein_per_100g=data.protein_per_100g,
            fat_per_100g=data.fat_per_100g,
            kcal_per_100g=data.kcal_per_100g,
            glycemic_index=data.glycemic_index,
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

    async def upsert_import(self, data: FoodData) -> bool:
        food = await self._session.scalar(
            select(Food)
            .options(selectinload(Food.aliases))
            .where(Food.canonical_name == data.canonical_name)
        )
        created = food is None
        if food is None:
            food = Food(canonical_name=data.canonical_name, aliases=[])
            self._session.add(food)

        food.ru_name = data.ru_name.strip()
        food.carbs_per_100g = data.carbs_per_100g
        if created or data.en_name is not None:
            food.en_name = data.en_name.strip() if data.en_name else None
        if created or data.protein_per_100g is not None:
            food.protein_per_100g = data.protein_per_100g
        if created or data.fat_per_100g is not None:
            food.fat_per_100g = data.fat_per_100g
        if created or data.kcal_per_100g is not None:
            food.kcal_per_100g = data.kcal_per_100g
        if created or data.glycemic_index is not None:
            food.glycemic_index = data.glycemic_index
        has_explicit_metadata = data.source != "csv_import"
        if created or has_explicit_metadata:
            food.source = data.source
        if created or has_explicit_metadata or data.confidence != Decimal("1"):
            food.confidence = data.confidence
        await self._session.flush()

        desired_aliases = {data.ru_name, data.canonical_name.replace("_", " "), *data.aliases}
        if data.en_name:
            desired_aliases.add(data.en_name)
        normalized_aliases = {
            normalize_food_name(alias) for alias in desired_aliases if alias.strip()
        }
        current_aliases = {alias.alias for alias in food.aliases}
        if normalized_aliases - current_aliases:
            occupied = set(
                await self._session.scalars(
                    select(FoodAlias.alias).where(FoodAlias.alias.in_(normalized_aliases))
                )
            )
            food.aliases.extend(
                FoodAlias(alias=alias)
                for alias in sorted(normalized_aliases - current_aliases - occupied)
            )
            await self._session.flush()
        return created

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
            glycemic_index=food.glycemic_index,
            source=food.source,
            confidence=food.confidence,
            aliases=[alias.alias for alias in food.aliases],
        )

    @staticmethod
    def _to_record(food: Food) -> FoodRecord:
        return FoodRecord(
            id=food.id,
            canonical_name=food.canonical_name,
            ru_name=food.ru_name,
            en_name=food.en_name,
            carbs_per_100g=food.carbs_per_100g,
            protein_per_100g=food.protein_per_100g,
            fat_per_100g=food.fat_per_100g,
            kcal_per_100g=food.kcal_per_100g,
            glycemic_index=food.glycemic_index,
            source=food.source,
            confidence=food.confidence,
            aliases=[alias.alias for alias in food.aliases],
            created_at=food.created_at,
            updated_at=food.updated_at,
        )


class TelegramUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, telegram_user_id: int) -> TelegramUserRecord | None:
        user = await self._session.get(TelegramUser, telegram_user_id)
        return TelegramUserRecord.model_validate(user) if user is not None else None

    async def is_admin(self, telegram_user_id: int) -> bool:
        statement = select(TelegramUser.is_admin).where(
            TelegramUser.telegram_user_id == telegram_user_id,
            TelegramUser.is_active.is_(True),
        )
        return bool(await self._session.scalar(statement))

    async def touch_authorized(
        self,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
    ) -> TelegramUserRecord | None:
        user = await self._session.get(TelegramUser, telegram_user_id)
        if user is None or not user.is_active:
            return None

        user.username = username
        user.full_name = full_name
        user.last_seen_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(user)
        return TelegramUserRecord.model_validate(user)

    async def add_user(
        self,
        telegram_user_id: int,
        added_by_telegram_id: int,
        full_name: str | None = None,
    ) -> tuple[TelegramUserRecord, bool]:
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")

        user = await self._session.get(TelegramUser, telegram_user_id)
        created = user is None
        if user is None:
            user = TelegramUser(
                telegram_user_id=telegram_user_id,
                username=None,
                full_name=full_name,
                is_admin=False,
                is_active=True,
                added_by_telegram_id=added_by_telegram_id,
            )
            self._session.add(user)
        else:
            user.is_active = True
            if full_name:
                user.full_name = full_name
            if user.added_by_telegram_id is None:
                user.added_by_telegram_id = added_by_telegram_id

        await self._session.flush()
        await self._session.refresh(user)
        return TelegramUserRecord.model_validate(user), created

    async def bootstrap_admins(self, telegram_user_ids: list[int]) -> None:
        for telegram_user_id in telegram_user_ids:
            user = await self._session.get(TelegramUser, telegram_user_id)
            if user is None:
                self._session.add(
                    TelegramUser(
                        telegram_user_id=telegram_user_id,
                        username=None,
                        full_name=None,
                        is_admin=True,
                        is_active=True,
                        added_by_telegram_id=None,
                    )
                )
            else:
                user.is_admin = True
                user.is_active = True
        await self._session.flush()

    async def list_all(self) -> list[TelegramUserRecord]:
        statement = select(TelegramUser).order_by(
            TelegramUser.is_admin.desc(), TelegramUser.telegram_user_id
        )
        users = (await self._session.scalars(statement)).all()
        return [TelegramUserRecord.model_validate(user) for user in users]


class JournalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        telegram_user_id: int,
        data: JournalEntryCreate,
        default_timezone: tzinfo,
    ) -> JournalEntryRecord:
        occurred_at = data.occurred_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=default_timezone)

        entry = JournalEntry(
            telegram_user_id=telegram_user_id,
            occurred_at=occurred_at,
            duration_minutes=data.duration_minutes,
            short_insulin_units=data.short_insulin_units,
            long_insulin_units=data.long_insulin_units,
            food=data.food,
            carbohydrates_grams=data.carbohydrates_grams,
            physical_activity=data.physical_activity,
            blood_glucose_mmol_l=data.blood_glucose_mmol_l,
        )
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        record = JournalEntryRecord.model_validate(entry)
        return record.model_copy(update={"occurred_at": occurred_at})

    async def list_recent(self, telegram_user_id: int, limit: int = 20) -> list[JournalEntryRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        statement = (
            select(JournalEntry)
            .where(JournalEntry.telegram_user_id == telegram_user_id)
            .order_by(JournalEntry.occurred_at.desc(), JournalEntry.id.desc())
            .limit(limit)
        )
        entries = (await self._session.scalars(statement)).all()
        return [JournalEntryRecord.model_validate(entry) for entry in entries]

    async def list_all(self, telegram_user_id: int) -> list[JournalEntryRecord]:
        statement = (
            select(JournalEntry)
            .where(JournalEntry.telegram_user_id == telegram_user_id)
            .order_by(JournalEntry.occurred_at, JournalEntry.id)
        )
        entries = (await self._session.scalars(statement)).all()
        return [JournalEntryRecord.model_validate(entry) for entry in entries]

    async def delete_last(self, telegram_user_id: int) -> JournalEntryRecord | None:
        statement = (
            select(JournalEntry)
            .where(JournalEntry.telegram_user_id == telegram_user_id)
            .order_by(JournalEntry.occurred_at.desc(), JournalEntry.id.desc())
            .limit(1)
        )
        entry = await self._session.scalar(statement)
        if entry is None:
            return None

        record = JournalEntryRecord.model_validate(entry)
        await self._session.delete(entry)
        await self._session.flush()
        return record

    async def update_at(
        self,
        telegram_user_id: int,
        target_occurred_at: datetime,
        data: JournalEntryUpdate,
        default_timezone: tzinfo,
    ) -> JournalEntryRecord | None:
        target = target_occurred_at
        if target.tzinfo is None:
            target = target.replace(tzinfo=default_timezone)
        minute_start = target.replace(second=0, microsecond=0).astimezone(UTC)
        minute_end = minute_start + timedelta(minutes=1)
        statement = (
            select(JournalEntry)
            .where(
                JournalEntry.telegram_user_id == telegram_user_id,
                JournalEntry.occurred_at >= minute_start,
                JournalEntry.occurred_at < minute_end,
            )
            .order_by(JournalEntry.id)
            .limit(2)
        )
        entries = (await self._session.scalars(statement)).all()
        if not entries:
            return None
        if len(entries) > 1:
            raise AmbiguousJournalEntryError(
                "В указанную минуту найдено несколько записей. Уточните запись в журнале."
            )

        entry = entries[0]
        changes = data.model_dump(exclude_none=True)
        new_occurred_at = changes.pop("occurred_at", None)
        if new_occurred_at is not None:
            if new_occurred_at.tzinfo is None:
                new_occurred_at = new_occurred_at.replace(tzinfo=default_timezone)
            entry.occurred_at = new_occurred_at
        for field, value in changes.items():
            setattr(entry, field, value)

        await self._session.flush()
        await self._session.refresh(entry)
        return JournalEntryRecord.model_validate(entry)

    async def add_many(
        self,
        telegram_user_id: int,
        entries: Sequence[JournalEntryCreate],
        default_timezone: tzinfo,
    ) -> tuple[int, int]:
        if not entries:
            return 0, 0

        normalized = [self._normalize_entry(entry, default_timezone) for entry in entries]
        timestamps = [occurred_at for occurred_at, _ in normalized]
        statement = select(JournalEntry).where(
            JournalEntry.telegram_user_id == telegram_user_id,
            JournalEntry.occurred_at >= min(timestamps),
            JournalEntry.occurred_at <= max(timestamps),
        )
        existing = (await self._session.scalars(statement)).all()
        known_keys = {self._model_key(entry, default_timezone) for entry in existing}

        models: list[JournalEntry] = []
        skipped = 0
        for occurred_at, entry in normalized:
            key = self._data_key(occurred_at, entry)
            if key in known_keys:
                skipped += 1
                continue
            known_keys.add(key)
            models.append(
                JournalEntry(
                    telegram_user_id=telegram_user_id,
                    occurred_at=occurred_at,
                    duration_minutes=entry.duration_minutes,
                    short_insulin_units=entry.short_insulin_units,
                    long_insulin_units=entry.long_insulin_units,
                    food=entry.food,
                    carbohydrates_grams=entry.carbohydrates_grams,
                    physical_activity=entry.physical_activity,
                    blood_glucose_mmol_l=entry.blood_glucose_mmol_l,
                )
            )

        self._session.add_all(models)
        await self._session.flush()
        return len(models), skipped

    @staticmethod
    def _normalize_entry(
        entry: JournalEntryCreate,
        default_timezone: tzinfo,
    ) -> tuple[datetime, JournalEntryCreate]:
        occurred_at = entry.occurred_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=default_timezone)
        return occurred_at, entry

    @staticmethod
    def _data_key(occurred_at: datetime, entry: JournalEntryCreate) -> tuple[object, ...]:
        return (
            occurred_at.astimezone(UTC),
            entry.duration_minutes,
            entry.short_insulin_units,
            entry.long_insulin_units,
            entry.food,
            entry.carbohydrates_grams,
            entry.physical_activity,
            entry.blood_glucose_mmol_l,
        )

    @classmethod
    def _model_key(cls, entry: JournalEntry, default_timezone: tzinfo) -> tuple[object, ...]:
        occurred_at = entry.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=default_timezone)
        return (
            occurred_at.astimezone(UTC),
            entry.duration_minutes,
            entry.short_insulin_units,
            entry.long_insulin_units,
            entry.food,
            entry.carbohydrates_grams,
            entry.physical_activity,
            entry.blood_glucose_mmol_l,
        )

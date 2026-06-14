from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.schemas import JournalEntryCreate, JournalEntryRecord, JournalEntryUpdate
from app.agent.tools import FoodAgentDeps, delete_last_journal_entry
from app.database.models import Base, TelegramUser
from app.database.repositories import JournalRepository
from app.services.journal import format_journal_messages, parse_journal_limit


async def test_journal_entries_are_isolated_by_user(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'journal.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(
            [
                TelegramUser(telegram_user_id=1001, is_admin=False, is_active=True),
                TelegramUser(telegram_user_id=2002, is_admin=False, is_active=True),
            ]
        )
        await session.flush()
        repository = JournalRepository(session)
        first = await repository.add(
            1001,
            JournalEntryCreate(
                blood_glucose_mmol_l=Decimal("6.4"),
                short_insulin_units=Decimal("3"),
                food="гречка",
                carbohydrates_grams=Decimal("35.5"),
            ),
            ZoneInfo("Europe/Moscow"),
        )
        await repository.add(
            2002,
            JournalEntryCreate(physical_activity="бег", duration_minutes=30),
            ZoneInfo("Europe/Moscow"),
        )
        await session.commit()

    assert first.telegram_user_id == 1001
    assert first.blood_glucose_mmol_l == Decimal("6.40")
    assert first.carbohydrates_grams == Decimal("35.50")

    async with sessions() as session:
        first_user_entries = await JournalRepository(session).list_recent(1001)
        second_user_entries = await JournalRepository(session).list_recent(2002)
        first_user_export = await JournalRepository(session).list_all(1001)

    assert len(first_user_entries) == 1
    assert first_user_entries[0].food == "гречка"
    assert len(second_user_entries) == 1
    assert second_user_entries[0].physical_activity == "бег"
    assert [entry.telegram_user_id for entry in first_user_export] == [1001]
    await engine.dispose()


async def test_naive_event_time_uses_configured_timezone(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'timezone.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(TelegramUser(telegram_user_id=1001, is_admin=False, is_active=True))
        await session.flush()
        entry = await JournalRepository(session).add(
            1001,
            JournalEntryCreate(
                occurred_at=datetime(2026, 6, 14, 9, 30),
                blood_glucose_mmol_l=Decimal("5.8"),
            ),
            ZoneInfo("Europe/Moscow"),
        )
        await session.commit()

    assert entry.occurred_at.utcoffset() is not None
    await engine.dispose()


async def test_delete_last_removes_only_current_users_latest_entry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delete-last.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    timezone = ZoneInfo("Europe/Moscow")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(
            [
                TelegramUser(telegram_user_id=1001, is_admin=False, is_active=True),
                TelegramUser(telegram_user_id=2002, is_admin=False, is_active=True),
            ]
        )
        await session.flush()
        repository = JournalRepository(session)
        older = await repository.add(
            1001,
            JournalEntryCreate(
                occurred_at=datetime(2026, 6, 14, 9, 0, tzinfo=UTC),
                food="завтрак",
            ),
            timezone,
        )
        latest = await repository.add(
            1001,
            JournalEntryCreate(
                occurred_at=datetime(2026, 6, 14, 10, 0, tzinfo=UTC),
                food="обед",
            ),
            timezone,
        )
        await repository.add(
            2002,
            JournalEntryCreate(
                occurred_at=datetime(2026, 6, 14, 11, 0, tzinfo=UTC),
                food="чужая запись",
            ),
            timezone,
        )
        await session.commit()

    async with sessions() as session:
        repository = JournalRepository(session)
        context = cast(
            Any,
            SimpleNamespace(
                deps=FoodAgentDeps(
                    session=session,
                    online_lookup=cast(Any, object()),
                    telegram_user_id=1001,
                    journal_timezone=timezone,
                    journal_xe_carbs_grams=Decimal("12"),
                )
            ),
        )
        deleted = await delete_last_journal_entry(context)
        await session.commit()

    assert deleted is not None
    assert deleted.id == latest.id

    async with sessions() as session:
        repository = JournalRepository(session)
        own_entries = await repository.list_recent(1001)
        other_entries = await repository.list_recent(2002)
        empty = await repository.delete_last(9999)

    assert [entry.id for entry in own_entries] == [older.id]
    assert [entry.food for entry in other_entries] == ["чужая запись"]
    assert empty is None
    await engine.dispose()


async def test_update_at_changes_only_selected_users_explicit_fields(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'edit-entry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    timezone = ZoneInfo("Europe/Moscow")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(
            [
                TelegramUser(telegram_user_id=1001, is_admin=False, is_active=True),
                TelegramUser(telegram_user_id=2002, is_admin=False, is_active=True),
            ]
        )
        await session.flush()
        repository = JournalRepository(session)
        own = await repository.add(
            1001,
            JournalEntryCreate(
                occurred_at=datetime(2026, 6, 14, 9, 30, 45, tzinfo=UTC),
                short_insulin_units=Decimal("3"),
                food="гречка",
                carbohydrates_grams=Decimal("35.5"),
                blood_glucose_mmol_l=Decimal("6.4"),
            ),
            timezone,
        )
        other = await repository.add(
            2002,
            JournalEntryCreate(
                occurred_at=datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
                blood_glucose_mmol_l=Decimal("8.1"),
            ),
            timezone,
        )
        await session.commit()

    async with sessions() as session:
        repository = JournalRepository(session)
        updated = await repository.update_at(
            1001,
            datetime(2026, 6, 14, 12, 30),
            JournalEntryUpdate(blood_glucose_mmol_l=Decimal("5.8")),
            timezone,
        )
        missing = await repository.update_at(
            1001,
            datetime(2026, 6, 14, 14, 0),
            JournalEntryUpdate(food="ужин"),
            timezone,
        )
        await session.commit()

    assert updated is not None
    assert updated.id == own.id
    assert updated.blood_glucose_mmol_l == Decimal("5.80")
    assert updated.short_insulin_units == Decimal("3.00")
    assert updated.food == "гречка"
    assert updated.carbohydrates_grams == Decimal("35.50")
    assert missing is None

    async with sessions() as session:
        other_entries = await JournalRepository(session).list_recent(2002)

    assert other_entries[0].id == other.id
    assert other_entries[0].blood_glucose_mmol_l == Decimal("8.10")
    await engine.dispose()


async def test_update_at_rejects_multiple_entries_in_same_minute(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ambiguous-edit.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    timezone = ZoneInfo("Europe/Moscow")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(TelegramUser(telegram_user_id=1001, is_admin=False, is_active=True))
        await session.flush()
        repository = JournalRepository(session)
        for second in (10, 40):
            await repository.add(
                1001,
                JournalEntryCreate(
                    occurred_at=datetime(2026, 6, 14, 9, 30, second, tzinfo=UTC),
                    food=f"запись {second}",
                ),
                timezone,
            )
        await session.commit()

    async with sessions() as session:
        with pytest.raises(ValueError, match="несколько записей"):
            await JournalRepository(session).update_at(
                1001,
                datetime(2026, 6, 14, 12, 30),
                JournalEntryUpdate(food="исправлено"),
                timezone,
            )

    await engine.dispose()


def test_journal_entry_requires_content() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        JournalEntryCreate()


def test_formats_journal_and_parses_limit() -> None:
    entry = JournalEntryRecord(
        id=1,
        telegram_user_id=1001,
        occurred_at=datetime(2026, 6, 14, 9, 30, tzinfo=UTC),
        duration_minutes=30,
        short_insulin_units=Decimal("3"),
        long_insulin_units=None,
        food="гречка",
        carbohydrates_grams=Decimal("35.5"),
        physical_activity="прогулка",
        blood_glucose_mmol_l=Decimal("6.4"),
        created_at=datetime(2026, 6, 14, 9, 31, tzinfo=UTC),
    )

    messages = format_journal_messages([entry], ZoneInfo("Europe/Moscow"))

    assert parse_journal_limit(None) == 20
    assert parse_journal_limit("50") == 50
    assert "14.06.2026 12:30" in messages[0]
    assert "сахар 6.4 ммоль/л" in messages[0]
    assert "короткий инсулин 3 ед." in messages[0]
    assert "углеводы 35.5 г" in messages[0]
    assert "продолжительность 30 мин." in messages[0]


@pytest.mark.parametrize("value", ["0", "101", "abc"])
def test_rejects_invalid_journal_limit(value: str) -> None:
    with pytest.raises(ValueError):
        parse_journal_limit(value)

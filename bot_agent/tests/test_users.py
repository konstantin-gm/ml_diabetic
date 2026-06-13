from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, TelegramUser
from app.database.repositories import TelegramUserRepository
from app.services.user_access import format_user_messages, parse_add_user_args


async def test_bootstrap_admin_and_add_user(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        repository = TelegramUserRepository(session)
        await repository.bootstrap_admins([1001])
        user, created = await repository.add_user(2002, 1001, "Иван")
        await session.commit()

    assert created is True
    assert user.telegram_user_id == 2002
    assert user.is_admin is False

    async with sessions() as session:
        repository = TelegramUserRepository(session)
        assert await repository.is_admin(1001) is True
        touched = await repository.touch_authorized(2002, "ivan", "Иван Иванов")
        await session.commit()
        users = await repository.list_all()

    assert touched is not None
    assert touched.username == "ivan"
    assert touched.last_seen_at is not None
    assert [user.telegram_user_id for user in users] == [1001, 2002]
    await engine.dispose()


async def test_inactive_user_is_denied_and_can_be_reactivated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inactive.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            TelegramUser(
                telegram_user_id=3003,
                username=None,
                full_name="Отключённый",
                is_admin=False,
                is_active=False,
                added_by_telegram_id=1001,
            )
        )
        await session.commit()

    async with sessions() as session:
        repository = TelegramUserRepository(session)
        assert await repository.touch_authorized(3003, None, "Отключённый") is None
        user, created = await repository.add_user(3003, 1001)
        await session.commit()

    assert created is False
    assert user.is_active is True
    await engine.dispose()


def test_parse_add_user_args() -> None:
    assert parse_add_user_args("123456789 Иван Иванов") == (123456789, "Иван Иванов")
    assert parse_add_user_args("123456789") == (123456789, None)


def test_format_user_messages_respects_limit() -> None:
    from datetime import UTC, datetime

    from app.agent.schemas import TelegramUserRecord

    timestamp = datetime(2026, 6, 13, tzinfo=UTC)
    users = [
        TelegramUserRecord(
            telegram_user_id=index,
            username=f"user{index}",
            full_name=f"Очень длинное имя пользователя {index}",
            is_admin=index == 1,
            is_active=True,
            added_by_telegram_id=None,
            created_at=timestamp,
            updated_at=timestamp,
            last_seen_at=None,
        )
        for index in range(1, 8)
    ]

    messages = format_user_messages(users, max_length=180)

    assert len(messages) > 1
    assert all(len(message) <= 180 for message in messages)
    assert "Пользователи в белом списке: 7" in messages[0]

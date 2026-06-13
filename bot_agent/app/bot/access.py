from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import TelegramUserRepository

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class WhitelistMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        telegram_user = event.from_user
        async with self._session_factory() as session:
            user = await TelegramUserRepository(session).touch_authorized(
                telegram_user.id,
                telegram_user.username,
                telegram_user.full_name,
            )
            if user is None:
                await session.rollback()
                await event.answer(
                    "Доступ к боту закрыт. Ваш Telegram ID: "
                    f"{telegram_user.id}. Передайте его администратору для добавления "
                    "в белый список."
                )
                return None
            await session.commit()

        data["authorized_user"] = user
        return await handler(event, data)

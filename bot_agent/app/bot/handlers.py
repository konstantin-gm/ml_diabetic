from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import FoodAgentDeps
from app.services.online_food import OnlineFoodLookup

logger = logging.getLogger(__name__)


def create_router(
    agent: Agent[FoodAgentDeps, str],
    session_factory: async_sessionmaker[AsyncSession],
    online_lookup: OnlineFoodLookup,
) -> Router:
    router = Router(name="food")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Напишите продукт и массу, например: «Сколько углеводов в 150 г вареной гречки?»\n\n"
            "Бот только считает углеводы и не рекомендует дозы инсулина."
        )

    @router.message(F.text)
    async def food_question(message: Message) -> None:
        if message.text is None:
            return

        if message.bot is not None:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        async with session_factory() as session:
            try:
                result = await agent.run(
                    message.text,
                    deps=FoodAgentDeps(session=session, online_lookup=online_lookup),
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Failed to process food question")
                await message.answer(
                    "Не удалось проверить продукт. Попробуйте уточнить название "
                    "и способ приготовления."
                )
                return

        await message.answer(result.output)

    return router

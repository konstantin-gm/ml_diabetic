from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import BufferedInputFile, Message
from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import FoodAgentDeps
from app.bot.access import WhitelistMiddleware
from app.database.repositories import FoodRepository, TelegramUserRepository
from app.services.food_export import build_foods_csv, format_food_messages
from app.services.online_food import OnlineFoodLookup
from app.services.user_access import format_user_messages, parse_add_user_args

logger = logging.getLogger(__name__)


def create_router(
    agent: Agent[FoodAgentDeps, str],
    session_factory: async_sessionmaker[AsyncSession],
    online_lookup: OnlineFoodLookup,
) -> Router:
    router = Router(name="food")
    router.message.middleware(WhitelistMiddleware(session_factory))

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Напишите продукт и массу, например: «Сколько углеводов в 150 г вареной гречки?»\n\n"
            "Можно сохранить своё значение: «В моём хлебе 42 г углеводов на 100 г».\n\n"
            "Команды:\n"
            "/foods — показать базу продуктов\n"
            "/export_csv — скачать базу в CSV\n\n"
            "Администратор:\n"
            "/add_user ID Имя — добавить пользователя\n"
            "/users — показать белый список\n\n"
            "Бот только считает углеводы и не рекомендует дозы инсулина."
        )

    @router.message(Command("add_user"))
    async def add_user(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        async with session_factory() as session:
            repository = TelegramUserRepository(session)
            if not await repository.is_admin(message.from_user.id):
                await message.answer("Эта команда доступна только администратору.")
                return

            try:
                telegram_user_id, full_name = parse_add_user_args(command.args)
            except ValueError as error:
                await message.answer(str(error))
                return

            user, created = await repository.add_user(
                telegram_user_id=telegram_user_id,
                added_by_telegram_id=message.from_user.id,
                full_name=full_name,
            )
            await session.commit()

        action = "добавлен" if created else "обновлён и активирован"
        await message.answer(
            f"Пользователь {user.telegram_user_id} {action}. "
            "Он может открыть бота и отправить /start."
        )

    @router.message(Command("users"))
    async def list_users(message: Message) -> None:
        if message.from_user is None:
            return

        async with session_factory() as session:
            repository = TelegramUserRepository(session)
            if not await repository.is_admin(message.from_user.id):
                await message.answer("Эта команда доступна только администратору.")
                return
            users = await repository.list_all()

        for text in format_user_messages(users):
            await message.answer(text)

    @router.message(Command("foods"))
    async def list_foods(message: Message) -> None:
        async with session_factory() as session:
            foods = await FoodRepository(session).list_all()

        for text in format_food_messages(foods):
            await message.answer(text)

    @router.message(Command("export_csv"))
    async def export_foods_csv(message: Message) -> None:
        async with session_factory() as session:
            foods = await FoodRepository(session).list_all()

        if not foods:
            await message.answer("База продуктов пока пуста, экспортировать нечего.")
            return

        filename = f"foods_{datetime.now(UTC):%Y%m%d_%H%M%S}.csv"
        document = BufferedInputFile(build_foods_csv(foods), filename=filename)
        await message.answer_document(
            document,
            caption=f"Экспорт базы продуктов: {len(foods)} записей.",
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

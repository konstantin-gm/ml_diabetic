from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import BufferedInputFile, Message
from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import FoodAgentDeps
from app.bot.access import WhitelistMiddleware
from app.database.repositories import (
    FoodRepository,
    JournalRepository,
    TelegramUserRepository,
)
from app.services.food_export import build_foods_csv, format_food_messages
from app.services.journal import format_journal_messages, parse_journal_limit
from app.services.online_food import OnlineFoodLookup
from app.services.user_access import format_user_messages, parse_add_user_args

logger = logging.getLogger(__name__)


def create_router(
    agent: Agent[FoodAgentDeps, str],
    session_factory: async_sessionmaker[AsyncSession],
    online_lookup: OnlineFoodLookup,
    journal_timezone: tzinfo,
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
            "Журнал:\n"
            "/log данные — добавить запись\n"
            "/journal [количество] — показать свои записи\n"
            "Можно написать: «Запиши сахар 6.4, короткий 3 ед., прогулка 30 минут».\n\n"
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

    async def run_agent(message: Message, prompt: str) -> None:
        if message.from_user is None:
            return
        if message.bot is not None:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        async with session_factory() as session:
            try:
                result = await agent.run(
                    prompt,
                    deps=FoodAgentDeps(
                        session=session,
                        online_lookup=online_lookup,
                        telegram_user_id=message.from_user.id,
                        journal_timezone=journal_timezone,
                    ),
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Failed to process user message")
                await message.answer(
                    "Не удалось обработать сообщение. Проверьте значения и единицы измерения."
                )
                return
        await message.answer(result.output)

    @router.message(Command("log"))
    async def add_log_entry(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.strip():
            await message.answer(
                "Использование: /log сахар 6.4 ммоль/л, короткий инсулин 3 ед., прогулка 30 минут"
            )
            return
        await run_agent(message, f"Запиши в мой журнал: {command.args}")

    @router.message(Command("journal"))
    async def list_journal(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        try:
            limit = parse_journal_limit(command.args)
        except ValueError as error:
            await message.answer(str(error))
            return

        async with session_factory() as session:
            entries = await JournalRepository(session).list_recent(message.from_user.id, limit)
        for text in format_journal_messages(entries, journal_timezone):
            await message.answer(text)

    @router.message(F.text)
    async def food_question(message: Message) -> None:
        if message.text is None:
            return
        await run_agent(message, message.text)

    return router

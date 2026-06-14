from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo
from decimal import Decimal

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
from app.services.food_import import MAX_IMPORT_BYTES, FoodImportError, parse_foods_csv
from app.services.journal import format_journal_messages, parse_journal_limit
from app.services.journal_export import build_journal_csv
from app.services.journal_import import (
    JournalImportError,
    parse_import_year,
    parse_journal_file,
)
from app.services.online_food import OnlineFoodLookup
from app.services.user_access import format_user_messages, parse_add_user_args

logger = logging.getLogger(__name__)


def create_router(
    agent: Agent[FoodAgentDeps, str],
    session_factory: async_sessionmaker[AsyncSession],
    online_lookup: OnlineFoodLookup,
    journal_timezone: tzinfo,
    journal_xe_carbs_grams: Decimal,
) -> Router:
    router = Router(name="food")
    router.message.middleware(WhitelistMiddleware(session_factory))
    xe_grams = format(journal_xe_carbs_grams.normalize(), "f")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Напишите продукт и массу, например: «Сколько углеводов в 150 г вареной гречки?»\n\n"
            "Можно сохранить своё значение: «В моём хлебе 42 г углеводов на 100 г».\n\n"
            "Команды:\n"
            "/foods — показать базу продуктов\n"
            "/export_csv — скачать базу в CSV\n\n"
            "/import_foods_csv — загрузить продукты из CSV\n\n"
            "Журнал:\n"
            "/log данные — добавить запись\n"
            "/journal [количество] — показать свои записи\n"
            "/export_journal_csv — скачать свой журнал в CSV\n"
            "/import [год] — загрузить файл монитора или дневника\n"
            "Можно написать: «Запиши сахар 6.4, углеводы 48 г, короткий 3 ед.» "
            f"или указать углеводы в ХЕ. Сейчас 1 ХЕ = {xe_grams} г углеводов.\n\n"
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

    async def import_foods_document(message: Message) -> None:
        if message.document is None or message.bot is None:
            return
        try:
            if message.document.file_size and message.document.file_size > MAX_IMPORT_BYTES:
                raise FoodImportError("Размер CSV превышает 10 МБ.")
            downloaded = await message.bot.download(message.document)
            if downloaded is None:
                raise FoodImportError("Telegram не вернул содержимое файла.")
            parsed = parse_foods_csv(downloaded.read())
            created = 0
            updated = 0
            async with session_factory() as session:
                repository = FoodRepository(session)
                for food in parsed.foods:
                    if await repository.upsert_import(food):
                        created += 1
                    else:
                        updated += 1
                await session.commit()
        except FoodImportError as error:
            await message.answer(f"Не удалось импортировать продукты: {error}")
            return
        except Exception:
            logger.exception("Failed to import foods CSV")
            await message.answer("Не удалось импортировать продукты из-за внутренней ошибки.")
            return

        await message.answer(
            f"Импорт продуктов завершён: добавлено {created}, обновлено {updated}."
        )

    @router.message(Command("import_foods_csv"), F.document)
    async def import_foods_csv_document(message: Message) -> None:
        await import_foods_document(message)

    @router.message(Command("import_foods_csv"))
    async def import_foods_csv_help(message: Message) -> None:
        await message.answer(
            "Прикрепите CSV к команде /import_foods_csv. Поддерживается формат файла, "
            "созданного командой /export_csv. Обязательные колонки: canonical_name, "
            "ru_name, carbs_per_100g."
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
                        journal_xe_carbs_grams=journal_xe_carbs_grams,
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
                "Использование: /log сахар 6.4 ммоль/л, углеводы 4 ХЕ, "
                "короткий инсулин 3 ед., прогулка 30 минут"
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

    @router.message(Command("export_journal_csv"))
    async def export_journal_csv(message: Message) -> None:
        if message.from_user is None:
            return
        async with session_factory() as session:
            entries = await JournalRepository(session).list_all(message.from_user.id)

        if not entries:
            await message.answer("Ваш журнал пока пуст, экспортировать нечего.")
            return

        timestamp = datetime.now(journal_timezone).strftime("%Y%m%d_%H%M%S")
        document = BufferedInputFile(
            build_journal_csv(entries, journal_timezone),
            filename=f"journal_{timestamp}.csv",
        )
        await message.answer_document(
            document,
            caption=f"Экспорт вашего журнала: {len(entries)} записей.",
        )

    async def import_document(message: Message, year_value: str | None) -> None:
        if message.from_user is None or message.document is None or message.bot is None:
            return
        try:
            current_year = datetime.now(journal_timezone).year
            year = parse_import_year(year_value, current_year)
            downloaded = await message.bot.download(message.document)
            if downloaded is None:
                raise JournalImportError("Telegram не вернул содержимое файла.")
            payload = downloaded.read()
            parsed = parse_journal_file(
                payload,
                message.document.file_name or "journal.txt",
                year,
                journal_timezone,
                journal_xe_carbs_grams,
            )
            async with session_factory() as session:
                added, skipped = await JournalRepository(session).add_many(
                    message.from_user.id,
                    parsed.entries,
                    journal_timezone,
                )
                await session.commit()
        except JournalImportError as error:
            await message.answer(f"Не удалось импортировать файл: {error}")
            return
        except Exception:
            logger.exception("Failed to import journal document")
            await message.answer("Не удалось импортировать файл из-за внутренней ошибки.")
            return

        await message.answer(
            f"Импорт {parsed.source} завершён: добавлено {added}, "
            f"пропущено повторов {skipped}."
        )

    @router.message(Command("import"), F.document)
    async def import_document_with_year(message: Message, command: CommandObject) -> None:
        await import_document(message, command.args)

    @router.message(F.document)
    async def import_document_without_command(message: Message) -> None:
        filename = (message.document.file_name or "").lower() if message.document else ""
        if filename.endswith(".csv"):
            await import_foods_document(message)
            return
        caption = message.caption or ""
        year_value = caption if caption.lstrip().startswith("/import") else None
        await import_document(message, year_value)

    @router.message(Command("import"))
    async def import_help(message: Message) -> None:
        await message.answer(
            "Прикрепите файл Hematonix (.xls/.xlsx) или MelStudio (.txt). "
            "Если в дневнике нет года, добавьте подпись /import 2026. "
            "Без подписи используется текущий год."
        )

    @router.message(F.text)
    async def food_question(message: Message) -> None:
        if message.text is None:
            return
        await run_agent(message, message.text)

    return router

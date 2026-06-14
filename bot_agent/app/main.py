from __future__ import annotations

import asyncio
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from openai import AsyncOpenAI

from app.agent.food_agent import create_food_agent
from app.bot.handlers import create_router
from app.config import Settings
from app.database.repositories import TelegramUserRepository
from app.database.session import create_engine_and_session_factory
from app.services.online_food import OnlineFoodLookup


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine, session_factory = create_engine_and_session_factory(settings.database_url)
    admin_ids = settings.parsed_telegram_admin_ids()
    try:
        journal_timezone = ZoneInfo(settings.journal_timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown JOURNAL_TIMEZONE: {settings.journal_timezone}") from error
    async with session_factory() as session:
        await TelegramUserRepository(session).bootstrap_admins(admin_ids)
        await session.commit()

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    online_lookup = OnlineFoodLookup(openai_client, settings.openai_model)
    agent = create_food_agent(settings.openai_model, openai_client)

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            agent,
            session_factory,
            online_lookup,
            journal_timezone,
            settings.journal_xe_carbs_grams,
        )
    )

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть справку"),
                BotCommand(command="foods", description="Показать базу продуктов"),
                BotCommand(command="export_csv", description="Скачать базу в CSV"),
                BotCommand(command="log", description="Добавить запись в журнал"),
                BotCommand(command="journal", description="Показать мой журнал"),
                BotCommand(command="export_journal_csv", description="Скачать журнал в CSV"),
                BotCommand(command="import", description="Импортировать монитор или дневник"),
                BotCommand(command="add_user", description="Добавить пользователя"),
                BotCommand(command="users", description="Показать белый список"),
            ]
        )
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await openai_client.close()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

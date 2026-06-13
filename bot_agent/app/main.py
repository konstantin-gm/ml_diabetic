from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from openai import AsyncOpenAI

from app.agent.food_agent import create_food_agent
from app.bot.handlers import create_router
from app.config import Settings
from app.database.session import create_engine_and_session_factory
from app.services.online_food import OnlineFoodLookup


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine, session_factory = create_engine_and_session_factory(settings.database_url)
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    online_lookup = OnlineFoodLookup(openai_client, settings.openai_model)
    agent = create_food_agent(settings.openai_model, openai_client)

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(agent, session_factory, online_lookup))

    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await openai_client.close()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

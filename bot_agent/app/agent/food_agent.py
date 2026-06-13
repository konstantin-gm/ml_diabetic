from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModelName, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.agent.tools import (
    FoodAgentDeps,
    calculate_carbs,
    find_food,
    lookup_food_online,
    save_food,
)

INSTRUCTIONS = """
Ты помощник по подсчету углеводов в продуктах для человека с диабетом 1 типа.

Правила:
- Отвечай по-русски и только по вопросам содержания и подсчета углеводов.
- Никогда не рекомендуй дозу инсулина и не давай медицинских советов.
- Извлеки из запроса точный продукт, способ приготовления и массу в граммах.
- Если масса или продукт не указаны однозначно, задай один уточняющий вопрос.
- Всегда сначала вызови find_food.
- Вызывай lookup_food_online только если find_food вернул null.
- После успешного lookup_food_online обязательно вызови save_food с результатом поиска.
- Для итогового подсчета обязательно вызови calculate_carbs; не считай самостоятельно.
- Не выдумывай пищевую ценность и не используй собственные знания вместо инструментов.
- В ответе укажи продукт, массу, примерное количество углеводов и значение на 100 г.
- Кратко отметь, что фактическое значение зависит от рецепта или производителя.
""".strip()


def create_food_agent(
    model_name: OpenAIModelName, openai_client: AsyncOpenAI
) -> Agent[FoodAgentDeps, str]:
    model = OpenAIResponsesModel(
        model_name,
        provider=OpenAIProvider(openai_client=openai_client),
    )
    return Agent(
        model,
        deps_type=FoodAgentDeps,
        tools=[find_food, lookup_food_online, save_food, calculate_carbs],
        instructions=INSTRUCTIONS,
    )

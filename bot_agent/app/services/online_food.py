from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from openai import AsyncOpenAI

from app.agent.schemas import FoodData, OnlineFoodData


class OnlineFoodLookupError(RuntimeError):
    pass


class OnlineFoodLookup:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def lookup(self, name: str) -> FoodData:
        response = await self._client.responses.parse(
            model=self._model,
            tools=[{"type": "web_search"}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            input=[
                {
                    "role": "system",
                    "content": (
                        "Find reliable nutrition data for the exact food and preparation state. "
                        "Use web search. Return nutrients per 100 grams of edible product. "
                        "Prefer government, university, manufacturer, or established nutrition "
                        "databases. canonical_name must be lowercase snake_case in English. "
                        "confidence is 0 to 1. Include common Russian aliases. If the food is "
                        "ambiguous, choose the interpretation explicitly named by the user."
                    ),
                },
                {"role": "user", "content": f"Food requested in Russian: {name}"},
            ],
            text_format=OnlineFoodData,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise OnlineFoodLookupError("OpenAI returned no structured food data")

        source_urls = _collect_urls(response.model_dump(mode="json"))
        if not source_urls:
            raise OnlineFoodLookupError("Web lookup returned no source URL")

        return FoodData(**parsed.model_dump(), source=source_urls[0])


def _collect_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if (
                    key == "url"
                    and isinstance(child, str)
                    and child.startswith(("http://", "https://"))
                ):
                    urls.append(child)
                else:
                    visit(child)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(urls))

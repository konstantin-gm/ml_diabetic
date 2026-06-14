from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.schemas import OnlineFoodData
from app.services.online_food import OnlineFoodLookup, _collect_urls


def test_collect_urls_deduplicates_nested_sources() -> None:
    payload = {
        "output": [
            {"action": {"sources": [{"url": "https://example.com/food"}]}},
            {"annotations": [{"url": "https://example.com/food"}]},
        ]
    }

    assert _collect_urls(payload) == ["https://example.com/food"]


def test_online_food_requires_macros_and_calories() -> None:
    with pytest.raises(ValidationError):
        OnlineFoodData(
            canonical_name="apple_raw",
            ru_name="яблоко",
            en_name="raw apple",
            carbs_per_100g=Decimal("13.8"),
            glycemic_index=Decimal("36"),
            confidence=Decimal("0.9"),
            aliases=["яблоко свежее"],
        )


def test_online_food_accepts_reliable_glycemic_index_or_null() -> None:
    data = OnlineFoodData(
        canonical_name="apple_raw",
        ru_name="яблоко",
        en_name="raw apple",
        carbs_per_100g=Decimal("13.8"),
        protein_per_100g=Decimal("0.3"),
        fat_per_100g=Decimal("0.2"),
        kcal_per_100g=Decimal("52"),
        glycemic_index=Decimal("36"),
        confidence=Decimal("0.9"),
        aliases=["яблоко свежее"],
    )

    assert data.glycemic_index == Decimal("36")
    assert data.model_copy(update={"glycemic_index": None}).glycemic_index is None


async def test_lookup_requests_and_returns_complete_nutrition_data() -> None:
    responses = _FakeResponses()
    lookup = OnlineFoodLookup(_FakeClient(responses), "gpt-test")  # type: ignore[arg-type]

    result = await lookup.lookup("яблоко сырое")

    assert result.protein_per_100g == Decimal("0.3")
    assert result.fat_per_100g == Decimal("0.2")
    assert result.kcal_per_100g == Decimal("52")
    assert result.glycemic_index == Decimal("36")
    assert result.source == "https://example.com/apple"
    system_prompt = responses.kwargs["input"][0]["content"]
    assert "protein, fat, and kcal" in system_prompt
    assert "glycemic index" in system_prompt
    assert "Do not calculate or infer" in system_prompt


class _FakeClient:
    def __init__(self, responses: "_FakeResponses") -> None:
        self.responses = responses


class _FakeResponses:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> "_FakeResponse":
        self.kwargs = kwargs
        return _FakeResponse()


class _FakeResponse:
    output_parsed = OnlineFoodData(
        canonical_name="apple_raw",
        ru_name="яблоко",
        en_name="raw apple",
        carbs_per_100g=Decimal("13.8"),
        protein_per_100g=Decimal("0.3"),
        fat_per_100g=Decimal("0.2"),
        kcal_per_100g=Decimal("52"),
        glycemic_index=Decimal("36"),
        confidence=Decimal("0.9"),
        aliases=["яблоко свежее"],
    )

    def model_dump(self, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {"sources": [{"url": "https://example.com/apple"}]}

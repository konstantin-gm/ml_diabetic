from app.services.online_food import _collect_urls


def test_collect_urls_deduplicates_nested_sources() -> None:
    payload = {
        "output": [
            {"action": {"sources": [{"url": "https://example.com/food"}]}},
            {"annotations": [{"url": "https://example.com/food"}]},
        ]
    }

    assert _collect_urls(payload) == ["https://example.com/food"]

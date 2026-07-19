from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.config import ApiSettings
from app.api.main import create_app
from app.database.models import Base

API_TOKEN = "test-admin-api-token-that-is-long-enough"
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


def test_api_rejects_placeholder_token() -> None:
    with pytest.raises(ValidationError, match="must be replaced"):
        ApiSettings(admin_api_token="replace-with-a-random-token-of-at-least-32-characters")


async def test_api_requires_bearer_token(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, engine = await _create_test_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/v1/tables")
        invalid = await client.get(
            "/api/v1/tables",
            headers={"Authorization": "Bearer wrong-token"},
        )
        valid = await client.get("/api/v1/tables", headers=AUTH_HEADERS)

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    assert [table["name"] for table in valid.json()] == [
        "foods",
        "food_aliases",
        "telegram_users",
        "journal_entries",
    ]
    await engine.dispose()


async def test_api_crud_for_database_tables(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, engine = await _create_test_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        user = await client.post(
            "/api/v1/tables/telegram_users/rows",
            headers=AUTH_HEADERS,
            json={
                "telegram_user_id": 1001,
                "full_name": "Иван",
                "is_admin": True,
                "is_active": True,
            },
        )
        food = await client.post(
            "/api/v1/tables/foods/rows",
            headers=AUTH_HEADERS,
            json={
                "canonical_name": "buckwheat_cooked",
                "ru_name": "гречка вареная",
                "carbs_per_100g": "19.9",
                "source": "api",
                "confidence": "1",
            },
        )
        food_id = food.json()["id"]
        alias = await client.post(
            "/api/v1/tables/food_aliases/rows",
            headers=AUTH_HEADERS,
            json={"food_id": food_id, "alias": "  ГРЕЧА  "},
        )
        journal = await client.post(
            "/api/v1/tables/journal_entries/rows",
            headers=AUTH_HEADERS,
            json={
                "telegram_user_id": 1001,
                "occurred_at": "2026-07-19T12:30:00+03:00",
                "carbohydrates_grams": "35.5",
            },
        )

        assert user.status_code == 201
        assert food.status_code == 201
        assert alias.status_code == 201
        assert alias.json()["alias"] == "греча"
        assert journal.status_code == 201
        assert journal.json()["occurred_at"] == datetime(
            2026, 7, 19, 9, 30, tzinfo=UTC
        ).isoformat()

        updated = await client.patch(
            f"/api/v1/tables/foods/rows/{food_id}",
            headers=AUTH_HEADERS,
            json={"carbs_per_100g": "20.5", "glycemic_index": "49"},
        )
        page = await client.get(
            "/api/v1/tables/foods/rows?offset=0&limit=25",
            headers=AUTH_HEADERS,
        )

        assert updated.status_code == 200
        assert updated.json()["carbs_per_100g"] == "20.50"
        assert updated.json()["glycemic_index"] == "49.00"
        assert page.status_code == 200
        assert page.json()["total"] == 1
        assert page.json()["rows"][0]["canonical_name"] == "buckwheat_cooked"

        deleted = await client.delete(
            f"/api/v1/tables/journal_entries/rows/{journal.json()['id']}",
            headers=AUTH_HEADERS,
        )
        missing = await client.delete(
            f"/api/v1/tables/journal_entries/rows/{journal.json()['id']}",
            headers=AUTH_HEADERS,
        )

        assert deleted.status_code == 204
        assert missing.status_code == 404

    await engine.dispose()


async def test_api_rejects_unknown_tables_and_invalid_updates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app, engine = await _create_test_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unknown = await client.get("/api/v1/tables/secrets/rows", headers=AUTH_HEADERS)
        user = await client.post(
            "/api/v1/tables/telegram_users/rows",
            headers=AUTH_HEADERS,
            json={"telegram_user_id": 1001},
        )
        invalid = await client.patch(
            "/api/v1/tables/telegram_users/rows/1001",
            headers=AUTH_HEADERS,
            json={"is_active": None},
        )
        unknown_field = await client.patch(
            "/api/v1/tables/telegram_users/rows/1001",
            headers=AUTH_HEADERS,
            json={"is_actve": False},
        )

    assert unknown.status_code == 404
    assert user.status_code == 201
    assert invalid.status_code == 422
    assert unknown_field.status_code == 422
    await engine.dispose()


async def _create_test_app(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = ApiSettings(
        database_url="sqlite+aiosqlite://",
        admin_api_token=API_TOKEN,
        journal_timezone="Europe/Moscow",
    )
    return create_app(settings, sessions), engine

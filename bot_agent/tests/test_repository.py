from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.schemas import FoodData
from app.database.models import Base
from app.database.repositories import FoodRepository, normalize_food_name


async def test_food_is_found_by_normalized_alias(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'foods.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repository = FoodRepository(session)
        await repository.save(
            FoodData(
                canonical_name="buckwheat_cooked",
                ru_name="гречка вареная",
                en_name="cooked buckwheat",
                carbs_per_100g=Decimal("19.9"),
                protein_per_100g=Decimal("3.6"),
                fat_per_100g=Decimal("0.6"),
                kcal_per_100g=Decimal("92"),
                source="https://example.com/buckwheat",
                confidence=Decimal("0.9"),
                aliases=["греча", "гречневая каша"],
            )
        )
        await session.commit()

    async with session_factory() as session:
        found = await FoodRepository(session).find_by_name("  ГРЕЧА ")

    assert found is not None
    assert found.canonical_name == "buckwheat_cooked"
    assert found.carbs_per_100g == Decimal("19.90")
    await engine.dispose()


def test_normalizes_russian_yo_and_whitespace() -> None:
    assert normalize_food_name("  ТЁПЛАЯ   КАША ") == "теплая каша"


async def test_user_carbs_are_saved_and_can_update_cached_food(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'user-foods.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repository = FoodRepository(session)
        created = await repository.save_user_carbs("Мой хлеб", Decimal("42.00"))
        await session.commit()

    assert created.carbs_per_100g == Decimal("42.00")
    assert created.source == "user_provided"

    async with session_factory() as session:
        repository = FoodRepository(session)
        updated = await repository.save_user_carbs("  мой   хлеб ", Decimal("45.50"))
        await session.commit()

    assert updated.canonical_name == created.canonical_name
    assert updated.carbs_per_100g == Decimal("45.50")
    assert updated.source == "user_provided"

    async with session_factory() as session:
        found = await FoodRepository(session).find_by_name("МОЙ ХЛЕБ")
        foods = await FoodRepository(session).list_all()

    assert found is not None
    assert found.carbs_per_100g == Decimal("45.50")
    assert len(foods) == 1
    assert foods[0].id > 0
    assert foods[0].created_at is not None
    await engine.dispose()

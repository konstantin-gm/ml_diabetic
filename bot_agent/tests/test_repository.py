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

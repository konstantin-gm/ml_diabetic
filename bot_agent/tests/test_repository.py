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
                glycemic_index=Decimal("49"),
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
    assert found.protein_per_100g == Decimal("3.60")
    assert found.fat_per_100g == Decimal("0.60")
    assert found.kcal_per_100g == Decimal("92.00")
    assert found.glycemic_index == Decimal("49.00")
    await engine.dispose()


async def test_online_food_can_be_enriched_with_missing_nutrients(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'enrichment.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    base = FoodData(
        canonical_name="apple_raw",
        ru_name="яблоко",
        en_name="raw apple",
        carbs_per_100g=Decimal("13.8"),
        source="https://example.com/old",
        confidence=Decimal("0.7"),
        aliases=["яблоко свежее"],
    )
    enriched = base.model_copy(
        update={
            "protein_per_100g": Decimal("0.3"),
            "fat_per_100g": Decimal("0.2"),
            "kcal_per_100g": Decimal("52"),
            "glycemic_index": Decimal("36"),
            "source": "https://example.com/new",
            "confidence": Decimal("0.9"),
        }
    )
    async with sessions() as session:
        repository = FoodRepository(session)
        await repository.save(base)
        updated = await repository.save(enriched)
        await session.commit()

    assert updated.protein_per_100g == Decimal("0.3")
    assert updated.fat_per_100g == Decimal("0.2")
    assert updated.kcal_per_100g == Decimal("52")
    assert updated.glycemic_index == Decimal("36")
    assert updated.source == "https://example.com/new"
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


async def test_csv_import_creates_and_updates_food(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'food-import.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original = FoodData(
        canonical_name="apple_raw",
        ru_name="яблоко",
        en_name="raw apple",
        carbs_per_100g=Decimal("13.8"),
        source="https://example.com/apple",
        confidence=Decimal("0.7"),
        aliases=["яблоко свежее"],
    )
    updated = original.model_copy(
        update={
            "ru_name": "яблоко сырое",
            "protein_per_100g": Decimal("0.3"),
            "fat_per_100g": Decimal("0.2"),
            "kcal_per_100g": Decimal("52"),
            "glycemic_index": Decimal("36"),
            "source": "csv_import",
            "confidence": Decimal("1"),
            "aliases": ["яблоко без кожуры"],
        }
    )
    async with sessions() as session:
        repository = FoodRepository(session)
        created = await repository.upsert_import(original)
        changed = await repository.upsert_import(updated)
        await session.commit()

    async with sessions() as session:
        found = await FoodRepository(session).find_by_name("яблоко без кожуры")
        foods = await FoodRepository(session).list_all()

    assert created is True
    assert changed is False
    assert found is not None
    assert found.ru_name == "яблоко сырое"
    assert found.protein_per_100g == Decimal("0.30")
    assert found.fat_per_100g == Decimal("0.20")
    assert found.kcal_per_100g == Decimal("52.00")
    assert found.glycemic_index == Decimal("36.00")
    assert found.source == "https://example.com/apple"
    assert found.confidence == Decimal("0.70")
    assert len(foods) == 1
    await engine.dispose()

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.api.schemas import (
    FoodAliasRowCreate,
    FoodAliasRowUpdate,
    FoodRowCreate,
    FoodRowUpdate,
    JournalRowCreate,
    JournalRowUpdate,
    TableField,
    TableInfo,
    TelegramUserRowCreate,
    TelegramUserRowUpdate,
)
from app.database.models import Base, Food, FoodAlias, JournalEntry, TelegramUser


@dataclass(frozen=True, slots=True)
class TableSpec:
    info: TableInfo
    model: type[Base]
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    non_nullable_fields: frozenset[str]


def _field(
    name: str,
    field_type: str,
    *,
    required: bool = False,
    nullable: bool = True,
    read_only: bool = False,
    editable: bool = True,
) -> TableField:
    return TableField(
        name=name,
        type=field_type,
        required=required,
        nullable=nullable,
        read_only=read_only,
        editable=editable,
    )


TABLE_SPECS = (
    TableSpec(
        info=TableInfo(
            name="foods",
            title="Продукты",
            primary_key="id",
            delete_warning="Связанные псевдонимы продукта также будут удалены.",
            fields=[
                _field("id", "integer", nullable=False, read_only=True, editable=False),
                _field("canonical_name", "string", required=True, nullable=False),
                _field("ru_name", "string", required=True, nullable=False),
                _field("en_name", "string"),
                _field("carbs_per_100g", "decimal", required=True, nullable=False),
                _field("protein_per_100g", "decimal"),
                _field("fat_per_100g", "decimal"),
                _field("kcal_per_100g", "decimal"),
                _field("glycemic_index", "decimal"),
                _field("source", "string", required=True, nullable=False),
                _field("confidence", "decimal", required=True, nullable=False),
                _field("created_at", "datetime", nullable=False, read_only=True, editable=False),
                _field("updated_at", "datetime", nullable=False, read_only=True, editable=False),
            ],
        ),
        model=Food,
        create_schema=FoodRowCreate,
        update_schema=FoodRowUpdate,
        non_nullable_fields=frozenset(
            {"canonical_name", "ru_name", "carbs_per_100g", "source", "confidence"}
        ),
    ),
    TableSpec(
        info=TableInfo(
            name="food_aliases",
            title="Псевдонимы продуктов",
            primary_key="id",
            fields=[
                _field("id", "integer", nullable=False, read_only=True, editable=False),
                _field("food_id", "integer", required=True, nullable=False),
                _field("alias", "string", required=True, nullable=False),
            ],
        ),
        model=FoodAlias,
        create_schema=FoodAliasRowCreate,
        update_schema=FoodAliasRowUpdate,
        non_nullable_fields=frozenset({"food_id", "alias"}),
    ),
    TableSpec(
        info=TableInfo(
            name="telegram_users",
            title="Пользователи Telegram",
            primary_key="telegram_user_id",
            delete_warning="Журнал этого пользователя также будет удалён.",
            fields=[
                _field(
                    "telegram_user_id",
                    "integer",
                    required=True,
                    nullable=False,
                    editable=False,
                ),
                _field("username", "string"),
                _field("full_name", "string"),
                _field("is_admin", "boolean", nullable=False),
                _field("is_active", "boolean", nullable=False),
                _field("added_by_telegram_id", "integer"),
                _field("created_at", "datetime", nullable=False, read_only=True, editable=False),
                _field("updated_at", "datetime", nullable=False, read_only=True, editable=False),
                _field("last_seen_at", "datetime", read_only=True, editable=False),
            ],
        ),
        model=TelegramUser,
        create_schema=TelegramUserRowCreate,
        update_schema=TelegramUserRowUpdate,
        non_nullable_fields=frozenset({"is_admin", "is_active"}),
    ),
    TableSpec(
        info=TableInfo(
            name="journal_entries",
            title="Записи журнала",
            primary_key="id",
            fields=[
                _field("id", "integer", nullable=False, read_only=True, editable=False),
                _field("telegram_user_id", "integer", required=True, nullable=False),
                _field("occurred_at", "datetime", nullable=False),
                _field("duration_minutes", "integer"),
                _field("short_insulin_units", "decimal"),
                _field("long_insulin_units", "decimal"),
                _field("food", "string"),
                _field("carbohydrates_grams", "decimal"),
                _field("physical_activity", "string"),
                _field("blood_glucose_mmol_l", "decimal"),
                _field("created_at", "datetime", nullable=False, read_only=True, editable=False),
            ],
        ),
        model=JournalEntry,
        create_schema=JournalRowCreate,
        update_schema=JournalRowUpdate,
        non_nullable_fields=frozenset({"telegram_user_id", "occurred_at"}),
    ),
)

TABLES_BY_NAME = {spec.info.name: spec for spec in TABLE_SPECS}


def get_table_spec(table_name: str) -> TableSpec | None:
    return TABLES_BY_NAME.get(table_name)

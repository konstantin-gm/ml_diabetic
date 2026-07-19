from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TablePage
from app.api.tables import TableSpec
from app.database.models import Base
from app.database.repositories import normalize_food_name


class RowNotFoundError(LookupError):
    pass


class TableService:
    def __init__(self, session: AsyncSession, journal_timezone: tzinfo) -> None:
        self._session = session
        self._journal_timezone = journal_timezone

    async def list_rows(self, spec: TableSpec, offset: int, limit: int) -> TablePage:
        primary_key = getattr(spec.model, spec.info.primary_key)
        total = int(
            await self._session.scalar(select(func.count()).select_from(spec.model)) or 0
        )
        statement = select(spec.model).order_by(primary_key).offset(offset).limit(limit)
        rows = (await self._session.scalars(statement)).all()
        return TablePage(
            table=spec.info.name,
            primary_key=spec.info.primary_key,
            offset=offset,
            limit=limit,
            total=total,
            rows=[self._serialize(spec, row) for row in rows],
        )

    async def create_row(self, spec: TableSpec, payload: BaseModel) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        self._prepare_values(spec, values, creating=True)
        row = spec.model(**values)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return self._serialize(spec, row)

    async def update_row(
        self,
        spec: TableSpec,
        row_id: int,
        payload: BaseModel,
    ) -> dict[str, Any]:
        row = await self._get_row(spec, row_id)
        values = payload.model_dump(exclude_unset=True)
        null_fields = spec.non_nullable_fields.intersection(
            name for name, value in values.items() if value is None
        )
        if null_fields:
            names = ", ".join(sorted(null_fields))
            raise ValueError(f"Fields cannot be null: {names}")
        self._prepare_values(spec, values, creating=False)
        for name, value in values.items():
            setattr(row, name, value)
        await self._session.flush()
        await self._session.refresh(row)
        return self._serialize(spec, row)

    async def delete_row(self, spec: TableSpec, row_id: int) -> None:
        row = await self._get_row(spec, row_id)
        await self._session.delete(row)
        await self._session.flush()

    async def _get_row(self, spec: TableSpec, row_id: int) -> Base:
        row = await self._session.get(spec.model, row_id)
        if row is None:
            raise RowNotFoundError
        return row

    def _prepare_values(
        self,
        spec: TableSpec,
        values: dict[str, Any],
        *,
        creating: bool,
    ) -> None:
        if spec.info.name == "food_aliases" and "alias" in values:
            values["alias"] = normalize_food_name(values["alias"])
            if not values["alias"]:
                raise ValueError("alias cannot be empty")

        if spec.info.name != "journal_entries":
            return
        if creating and values.get("occurred_at") is None:
            values["occurred_at"] = datetime.now(UTC)
        occurred_at = values.get("occurred_at")
        if isinstance(occurred_at, datetime):
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=self._journal_timezone)
            values["occurred_at"] = occurred_at.astimezone(UTC)

    @staticmethod
    def _serialize(spec: TableSpec, row: Base) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field in spec.info.fields:
            value = getattr(row, field.name)
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=UTC)
                result[field.name] = value.isoformat()
            elif isinstance(value, Decimal):
                result[field.name] = str(value)
            else:
                result[field.name] = value
        return result

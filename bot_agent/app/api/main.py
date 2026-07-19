import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Response, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.config import ApiSettings
from app.api.schemas import TableInfo, TablePage
from app.api.service import RowNotFoundError, TableService
from app.api.tables import TABLE_SPECS, TableSpec, get_table_spec
from app.database.session import create_engine_and_session_factory

_bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: ApiSettings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    active_settings = settings or ApiSettings()
    engine: AsyncEngine | None = None
    if session_factory is None:
        engine, session_factory = create_engine_and_session_factory(active_settings.database_url)

    try:
        journal_timezone = ZoneInfo(active_settings.journal_timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            f"Unknown JOURNAL_TIMEZONE: {active_settings.journal_timezone}"
        ) from error

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        title="Diabetes Bot Database API",
        version="1.0.0",
        lifespan=lifespan,
    )

    async def authorize(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_bearer),
        ],
    ) -> None:
        expected = active_settings.admin_api_token.get_secret_value()
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not secrets.compare_digest(credentials.credentials, expected)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def get_session() -> AsyncIterator[AsyncSession]:
        assert session_factory is not None
        async with session_factory() as session:
            yield session

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])
    Session = Annotated[AsyncSession, Depends(get_session)]

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/tables", response_model=list[TableInfo])
    async def list_tables() -> list[TableInfo]:
        return [spec.info for spec in TABLE_SPECS]

    @router.get("/tables/{table_name}/rows", response_model=TablePage)
    async def list_rows(
        table_name: str,
        session: Session,
        offset: int = 0,
        limit: int = 100,
    ) -> TablePage:
        if offset < 0 or not 1 <= limit <= 500:
            raise HTTPException(status_code=422, detail="Invalid pagination parameters")
        spec = _require_table(table_name)
        return await TableService(session, journal_timezone).list_rows(spec, offset, limit)

    @router.post("/tables/{table_name}/rows", status_code=status.HTTP_201_CREATED)
    async def create_row(
        table_name: str,
        session: Session,
        payload: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        spec = _require_table(table_name)
        validated = _validate_payload(spec.create_schema, payload)
        try:
            row = await TableService(session, journal_timezone).create_row(spec, validated)
            await session.commit()
            return row
        except ValueError as error:
            await session.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Database constraint violation") from error

    @router.patch("/tables/{table_name}/rows/{row_id}")
    async def update_row(
        table_name: str,
        row_id: int,
        session: Session,
        payload: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        spec = _require_table(table_name)
        validated = _validate_payload(spec.update_schema, payload)
        try:
            row = await TableService(session, journal_timezone).update_row(
                spec, row_id, validated
            )
            await session.commit()
            return row
        except RowNotFoundError as error:
            await session.rollback()
            raise HTTPException(status_code=404, detail="Row not found") from error
        except ValueError as error:
            await session.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Database constraint violation") from error

    @router.delete("/tables/{table_name}/rows/{row_id}", status_code=204)
    async def delete_row(table_name: str, row_id: int, session: Session) -> Response:
        spec = _require_table(table_name)
        try:
            await TableService(session, journal_timezone).delete_row(spec, row_id)
            await session.commit()
            return Response(status_code=204)
        except RowNotFoundError as error:
            await session.rollback()
            raise HTTPException(status_code=404, detail="Row not found") from error
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Database constraint violation") from error

    app.include_router(router)
    return app


def _require_table(table_name: str) -> TableSpec:
    spec = get_table_spec(table_name)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown table")
    return spec


def _validate_payload(schema: type[BaseModel], payload: dict[str, Any]) -> BaseModel:
    try:
        return schema.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=jsonable_encoder(error.errors(include_url=False)),
        ) from error

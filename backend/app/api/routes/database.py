from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from app.bi import SalesSemanticLayer
from app.config import Settings, get_settings
from app.database import OdooDatabase
from app.schemas.database import (
    DatabaseHealthResponse,
    DatabaseSchemaResponse,
    SchemaColumnResponse,
    SchemaTableResponse,
)


router = APIRouter(prefix="/database", tags=["database"])


@router.get("/status", response_model=DatabaseHealthResponse)
async def database_status(
    settings: Settings = Depends(get_settings),
) -> DatabaseHealthResponse:
    health = await OdooDatabase(settings.database()).healthcheck()
    return DatabaseHealthResponse(**asdict(health))


@router.get("/schema", response_model=DatabaseSchemaResponse)
async def database_schema(
    settings: Settings = Depends(get_settings),
) -> DatabaseSchemaResponse:
    semantics = SalesSemanticLayer.load()
    columns = await OdooDatabase(settings.database()).discover_columns(
        semantics.table_columns
    )
    tables = [
        SchemaTableResponse(
            name=name,
            description=semantics.table_description(name),
            columns=[SchemaColumnResponse(**column) for column in columns[name]],
        )
        for name in sorted(columns)
    ]
    return DatabaseSchemaResponse(version=semantics.version, tables=tables)

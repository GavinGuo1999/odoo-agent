from __future__ import annotations

from pydantic import BaseModel


class DatabaseHealthResponse(BaseModel):
    connected: bool
    read_only: bool
    user: str
    database: str
    company_id: int
    company_name: str | None
    currency: str | None
    order_count: int
    response_ms: float
    error_type: str | None = None


class SchemaColumnResponse(BaseModel):
    name: str
    type: str


class SchemaTableResponse(BaseModel):
    name: str
    description: str
    columns: list[SchemaColumnResponse]


class DatabaseSchemaResponse(BaseModel):
    version: str
    tables: list[SchemaTableResponse]

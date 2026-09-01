"""Strictly read-only access to the local Odoo PostgreSQL database."""

from .client import (
    DatabaseConnectionError,
    DatabaseHealth,
    OdooDatabase,
    QueryResult,
)
from .sql_guard import ReadOnlySqlGuard, SqlComplexityLimits, SqlValidationResult

__all__ = [
    "DatabaseConnectionError",
    "DatabaseHealth",
    "OdooDatabase",
    "QueryResult",
    "ReadOnlySqlGuard",
    "SqlComplexityLimits",
    "SqlValidationResult",
]

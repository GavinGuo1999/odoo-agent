"""Strictly read-only access to the local Odoo PostgreSQL database."""

from .client import (
    DatabaseConnectionError,
    DatabaseHealth,
    OdooDatabase,
    QueryCostExceededError,
    QueryResult,
)
from .sql_guard import ReadOnlySqlGuard, classify_sql_error, SqlComplexityLimits, SqlValidationResult

__all__ = [
    "DatabaseConnectionError",
    "DatabaseHealth",
    "OdooDatabase",
    "QueryCostExceededError",
    "QueryResult",
    "ReadOnlySqlGuard",
    "classify_sql_error",
    "SqlComplexityLimits",
    "SqlValidationResult",
]

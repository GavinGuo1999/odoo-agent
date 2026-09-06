"""Strictly read-only access to the local Odoo PostgreSQL database."""

from .client import (
    DatabaseConnectionError,
    DatabaseHealth,
    OdooDatabase,
    QueryCostExceededError,
    QueryResult,
)
from .sql_guard import ReadOnlySqlGuard, SqlComplexityLimits, SqlValidationResult

__all__ = [
    "DatabaseConnectionError",
    "DatabaseHealth",
    "OdooDatabase",
    "QueryCostExceededError",
    "QueryResult",
    "ReadOnlySqlGuard",
    "SqlComplexityLimits",
    "SqlValidationResult",
]

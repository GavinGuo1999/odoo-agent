from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from app.config import StateDatabaseConfig


class AgentStateStore:
    """Own one durable checkpointer for the FastAPI process.

    The Odoo database is never reused here. If the independent state database is
    disabled or unavailable, the application stays usable with process-local
    memory and reports the degraded mode through the settings API.
    """

    def __init__(self) -> None:
        self._memory = InMemorySaver()
        self._checkpointer: Any = self._memory
        self._context: AbstractAsyncContextManager[Any] | None = None
        self._mode = "memory"
        self._error_type: str | None = None

    @property
    def checkpointer(self) -> Any:
        return self._checkpointer

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def error_type(self) -> str | None:
        return self._error_type

    async def start(self, config: StateDatabaseConfig) -> None:
        await self.close()
        self._checkpointer = self._memory
        self._mode = "memory"
        self._error_type = None
        if not config.configured:
            return

        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
        conninfo_fields: dict[str, Any] = {
            "host": config.host,
            "port": config.port,
            "dbname": config.database,
            "user": config.user,
            "connect_timeout": 5,
            "application_name": "odoo-agent-state",
        }
        if config.password:
            conninfo_fields["password"] = config.password
        conninfo = make_conninfo(**conninfo_fields)

        context = AsyncPostgresSaver.from_conn_string(conninfo)
        try:
            checkpointer = await context.__aenter__()
            await checkpointer.setup()
        except Exception as exc:
            self._error_type = type(exc).__name__
            try:
                await context.__aexit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
            return

        self._context = context
        self._checkpointer = checkpointer
        self._mode = "postgres"

    async def close(self) -> None:
        if self._context is not None:
            await self._context.__aexit__(None, None, None)
            self._context = None
        self._checkpointer = self._memory
        self._mode = "memory"


@lru_cache(maxsize=1)
def get_state_store() -> AgentStateStore:
    return AgentStateStore()

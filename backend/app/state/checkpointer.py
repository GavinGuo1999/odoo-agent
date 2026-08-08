from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from app.config import StateDatabaseConfig


@dataclass(frozen=True)
class ConversationRecord:
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime


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
        self._memory_conversations: dict[str, ConversationRecord] = {}

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
            await self._setup_conversation_table(checkpointer)
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

    @staticmethod
    async def _setup_conversation_table(checkpointer: Any) -> None:
        async with checkpointer.lock:
            async with checkpointer.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS odoo_agent_conversations (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )

    async def touch_conversation(
        self,
        session_id: str,
        *,
        title: str | None = None,
        update_activity: bool = True,
    ) -> ConversationRecord | None:
        """Create a conversation from its first question or update its activity time."""

        now = datetime.now(timezone.utc)
        normalized_title = " ".join(title.split())[:80] if title else None

        if self._mode == "postgres":
            async with self._checkpointer.lock:
                async with self._checkpointer.conn.cursor() as cursor:
                    if normalized_title:
                        await cursor.execute(
                            """
                            INSERT INTO odoo_agent_conversations (
                                session_id, title, created_at, updated_at
                            )
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (session_id) DO UPDATE
                            SET updated_at = CASE
                                WHEN %s THEN EXCLUDED.updated_at
                                ELSE odoo_agent_conversations.updated_at
                            END
                            RETURNING session_id, title, created_at, updated_at
                            """,
                            (session_id, normalized_title, now, now, update_activity),
                        )
                    else:
                        await cursor.execute(
                            """
                            UPDATE odoo_agent_conversations
                            SET updated_at = %s
                            WHERE session_id = %s
                            RETURNING session_id, title, created_at, updated_at
                            """,
                            (now, session_id),
                        )
                    row = await cursor.fetchone()
            return self._record_from_row(row) if row else None

        current = self._memory_conversations.get(session_id)
        if current is None:
            if not normalized_title:
                return None
            current = ConversationRecord(
                session_id=session_id,
                title=normalized_title,
                created_at=now,
                updated_at=now,
            )
        else:
            current = ConversationRecord(
                session_id=current.session_id,
                title=current.title,
                created_at=current.created_at,
                updated_at=now if update_activity else current.updated_at,
            )
        self._memory_conversations[session_id] = current
        return current

    async def list_conversations(self, *, limit: int = 100) -> list[ConversationRecord]:
        safe_limit = max(1, min(limit, 500))
        if self._mode == "postgres":
            async with self._checkpointer.lock:
                async with self._checkpointer.conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT session_id, title, created_at, updated_at
                        FROM odoo_agent_conversations
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (safe_limit,),
                    )
                    rows = await cursor.fetchall()
            return [self._record_from_row(row) for row in rows]

        records = sorted(
            self._memory_conversations.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        return records[:safe_limit]

    async def delete_conversation(self, session_id: str) -> bool:
        """Delete both LangGraph checkpoints and the conversation directory entry."""

        await self._checkpointer.adelete_thread(session_id)
        if self._mode == "postgres":
            async with self._checkpointer.lock:
                async with self._checkpointer.conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        DELETE FROM odoo_agent_conversations
                        WHERE session_id = %s
                        RETURNING session_id
                        """,
                        (session_id,),
                    )
                    row = await cursor.fetchone()
            return row is not None
        return self._memory_conversations.pop(session_id, None) is not None

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> ConversationRecord:
        return ConversationRecord(
            session_id=str(row["session_id"]),
            title=str(row["title"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def close(self) -> None:
        if self._context is not None:
            await self._context.__aexit__(None, None, None)
            self._context = None
        self._checkpointer = self._memory
        self._mode = "memory"


@lru_cache(maxsize=1)
def get_state_store() -> AgentStateStore:
    return AgentStateStore()

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from app.config import StateDatabaseConfig


@dataclass(frozen=True)
class ConversationRecord:
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    pending_question: str | None = None
    run_status: str = "completed"


class AgentStateStore:
    """Own one durable checkpointer for the FastAPI process.

    The Odoo database is never reused here. If the independent state database is
    disabled or unavailable, the application stays usable with process-local
    memory and reports the degraded mode through the settings API.
    """

    def __init__(self) -> None:
        self._memory = InMemorySaver()
        self._checkpointer: Any = self._memory
        self._pool: AsyncConnectionPool | None = None
        self._mode = "memory"
        self._error_type: str | None = None
        self._memory_conversations: dict[str, ConversationRecord] = {}
        self._memory_artifacts: dict[str, dict[int, dict[str, Any]]] = {}

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

        pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            check=AsyncConnectionPool.check_connection,
            name="odoo-agent-state",
        )
        try:
            await pool.open(wait=True, timeout=5)
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            await self._setup_conversation_table(checkpointer, pool)
        except Exception as exc:
            self._error_type = type(exc).__name__
            try:
                await pool.close()
            except Exception:
                pass
            return

        self._pool = pool
        self._checkpointer = checkpointer
        self._mode = "postgres"

    @staticmethod
    async def _setup_conversation_table(
        checkpointer: Any,
        pool: AsyncConnectionPool,
    ) -> None:
        async with checkpointer.lock:
            async with pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS odoo_agent_conversations (
                            session_id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            pending_question TEXT,
                            run_status TEXT NOT NULL DEFAULT 'completed'
                        )
                        """
                    )
                    await cursor.execute(
                        """
                        ALTER TABLE odoo_agent_conversations
                        ADD COLUMN IF NOT EXISTS pending_question TEXT
                        """
                    )
                    # 消息的渲染产物（图表规格、明细行、SQL、引用）单独存。
                    # 以前它跟在检查点的一个 channel 里，而检查点每轮整体重写，
                    # 那份列表又是累积的——第 N 轮的 blob 装着前 N 轮的全部产物。
                    # 为了压住写放大只好限制"最近 6 轮 / 单轮 200 行"，那两个上限
                    # 是放错位置的症状。搬出来之后写一次读一次，存储是严格线性的。
                    await cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS odoo_agent_message_artifacts (
                            session_id TEXT NOT NULL,
                            message_index INTEGER NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            PRIMARY KEY (session_id, message_index)
                        )
                        """
                    )
                    await cursor.execute(
                        """
                        ALTER TABLE odoo_agent_conversations
                        ADD COLUMN IF NOT EXISTS run_status TEXT NOT NULL DEFAULT 'completed'
                        """
                    )
                    await cursor.execute(
                        """
                        UPDATE odoo_agent_conversations
                        SET run_status = 'cancelled'
                        WHERE run_status = 'running'
                        """
                    )

    @asynccontextmanager
    async def _conversation_cursor(self) -> AsyncIterator[Any]:
        if self._pool is None:
            raise RuntimeError("PostgreSQL state pool is not available")
        async with self._checkpointer.lock:
            async with self._pool.connection() as connection:
                async with connection.cursor() as cursor:
                    yield cursor

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
            async with self._conversation_cursor() as cursor:
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
                        RETURNING session_id, title, created_at, updated_at,
                                  pending_question, run_status
                        """,
                        (session_id, normalized_title, now, now, update_activity),
                    )
                else:
                    await cursor.execute(
                        """
                        UPDATE odoo_agent_conversations
                        SET updated_at = %s
                        WHERE session_id = %s
                        RETURNING session_id, title, created_at, updated_at,
                                  pending_question, run_status
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
                pending_question=None,
                run_status="completed",
            )
        else:
            current = ConversationRecord(
                session_id=current.session_id,
                title=current.title,
                created_at=current.created_at,
                updated_at=now if update_activity else current.updated_at,
                pending_question=current.pending_question,
                run_status=current.run_status,
            )
        self._memory_conversations[session_id] = current
        return current

    async def list_conversations(self, *, limit: int = 100) -> list[ConversationRecord]:
        safe_limit = max(1, min(limit, 500))
        if self._mode == "postgres":
            async with self._conversation_cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT session_id, title, created_at, updated_at,
                           pending_question, run_status
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

    async def get_conversation(self, session_id: str) -> ConversationRecord | None:
        if self._mode == "postgres":
            async with self._conversation_cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT session_id, title, created_at, updated_at,
                           pending_question, run_status
                    FROM odoo_agent_conversations
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = await cursor.fetchone()
            return self._record_from_row(row) if row else None
        return self._memory_conversations.get(session_id)

    async def begin_conversation_turn(
        self,
        session_id: str,
        *,
        question: str,
        title: str | None = None,
    ) -> ConversationRecord | None:
        """Persist the user's message before starting a potentially slow model call."""

        record = await self.touch_conversation(session_id, title=title)
        if record is None:
            return None
        now = datetime.now(timezone.utc)
        if self._mode == "postgres":
            async with self._conversation_cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE odoo_agent_conversations
                    SET pending_question = %s,
                        run_status = 'running',
                        updated_at = %s
                    WHERE session_id = %s
                    RETURNING session_id, title, created_at, updated_at,
                              pending_question, run_status
                    """,
                    (question, now, session_id),
                )
                row = await cursor.fetchone()
            return self._record_from_row(row) if row else None

        current = ConversationRecord(
            session_id=record.session_id,
            title=record.title,
            created_at=record.created_at,
            updated_at=now,
            pending_question=question,
            run_status="running",
        )
        self._memory_conversations[session_id] = current
        return current

    async def finish_conversation_turn(
        self,
        session_id: str,
        *,
        run_status: str,
        keep_pending_question: bool = False,
        only_if_running: bool = False,
    ) -> ConversationRecord | None:
        """Mark a turn completed, interrupted, failed, or cancelled."""

        now = datetime.now(timezone.utc)
        if self._mode == "postgres":
            async with self._conversation_cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE odoo_agent_conversations
                    SET pending_question = CASE WHEN %s THEN pending_question ELSE NULL END,
                        run_status = %s,
                        updated_at = %s
                    WHERE session_id = %s
                      AND (%s = FALSE OR run_status = 'running')
                    RETURNING session_id, title, created_at, updated_at,
                              pending_question, run_status
                    """,
                    (
                        keep_pending_question,
                        run_status,
                        now,
                        session_id,
                        only_if_running,
                    ),
                )
                row = await cursor.fetchone()
            return self._record_from_row(row) if row else None

        record = self._memory_conversations.get(session_id)
        if record is None:
            return None
        if only_if_running and record.run_status != "running":
            return record
        current = ConversationRecord(
            session_id=record.session_id,
            title=record.title,
            created_at=record.created_at,
            updated_at=now,
            pending_question=record.pending_question if keep_pending_question else None,
            run_status=run_status,
        )
        self._memory_conversations[session_id] = current
        return current

    async def delete_conversation(self, session_id: str) -> bool:
        # 会话删了，它的渲染产物就是孤儿行，一并清掉。
        try:
            if self._mode == "postgres":
                async with self._conversation_cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM odoo_agent_message_artifacts WHERE session_id = %s",
                        (session_id,),
                    )
            else:
                self._memory_artifacts.pop(session_id, None)
        except Exception:
            # 清不掉不该阻止用户删除会话本身。
            pass
        """Delete both LangGraph checkpoints and the conversation directory entry."""

        await self._checkpointer.adelete_thread(session_id)
        if self._mode == "postgres":
            async with self._conversation_cursor() as cursor:
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

    async def save_message_artifact(
        self,
        session_id: str,
        message_index: int,
        payload: dict[str, Any],
    ) -> None:
        """存一条消息的渲染产物。

        调用方必须把失败当作非致命：产物只影响"翻回去还能不能看到图"，
        丢了也不该让这一轮的回答失败。
        """

        if self._mode != "postgres":
            self._memory_artifacts.setdefault(session_id, {})[message_index] = payload
            return
        async with self._conversation_cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO odoo_agent_message_artifacts
                    (session_id, message_index, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id, message_index)
                DO UPDATE SET payload = EXCLUDED.payload
                """,
                (session_id, message_index, Json(payload)),
            )

    async def load_message_artifacts(self, session_id: str) -> dict[int, dict[str, Any]]:
        if self._mode != "postgres":
            return dict(self._memory_artifacts.get(session_id, {}))
        async with self._conversation_cursor() as cursor:
            await cursor.execute(
                """
                SELECT message_index, payload
                FROM odoo_agent_message_artifacts
                WHERE session_id = %s
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
        return {int(row["message_index"]): row["payload"] for row in rows}

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> ConversationRecord:
        return ConversationRecord(
            session_id=str(row["session_id"]),
            title=str(row["title"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            pending_question=(
                str(row["pending_question"])
                if row.get("pending_question") is not None
                else None
            ),
            run_status=str(row.get("run_status") or "completed"),
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        self._checkpointer = self._memory
        self._mode = "memory"


@lru_cache(maxsize=1)
def get_state_store() -> AgentStateStore:
    return AgentStateStore()

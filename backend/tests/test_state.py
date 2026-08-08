from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import StateDatabaseConfig  # noqa: E402
from app.state.checkpointer import AgentStateStore  # noqa: E402


class _FailingContext:
    async def __aenter__(self):
        raise ConnectionError("not available")

    async def __aexit__(self, *_args):
        return None


class StateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_state_database_uses_memory(self) -> None:
        store = AgentStateStore()
        await store.start(
            StateDatabaseConfig(
                enabled=False,
                host="127.0.0.1",
                port=55432,
                database="odoo_agent_state",
                user="odoo_agent_state",
                password=None,
            )
        )

        self.assertEqual(store.mode, "memory")
        self.assertIsNone(store.error_type)

    async def test_failed_postgres_state_database_degrades_to_memory(self) -> None:
        store = AgentStateStore()
        with patch(
            "app.state.checkpointer.AsyncPostgresSaver.from_conn_string",
            return_value=_FailingContext(),
        ):
            await store.start(
                StateDatabaseConfig(
                    enabled=True,
                    host="127.0.0.1",
                    port=55432,
                    database="odoo_agent_state",
                    user="odoo_agent_state",
                    password="secret",
                )
            )

        self.assertEqual(store.mode, "memory")
        self.assertEqual(store.error_type, "ConnectionError")

    async def test_memory_conversation_directory_supports_create_list_touch_delete(self) -> None:
        store = AgentStateStore()
        await store.start(
            StateDatabaseConfig(
                enabled=False,
                host="127.0.0.1",
                port=55432,
                database="odoo_agent_state",
                user="odoo_agent_state",
                password=None,
            )
        )

        created = await store.touch_conversation(
            "sales-session",
            title="  本月\n销售额是多少？  ",
        )
        self.assertIsNotNone(created)
        self.assertEqual(created.title, "本月 销售额是多少？")

        touched = await store.touch_conversation("sales-session", title="不会覆盖原标题")
        self.assertEqual(touched.title, "本月 销售额是多少？")
        self.assertGreaterEqual(touched.updated_at, created.updated_at)
        restored = await store.touch_conversation(
            "sales-session",
            title="恢复会话不应改变排序",
            update_activity=False,
        )
        self.assertEqual(restored.updated_at, touched.updated_at)

        records = await store.list_conversations()
        self.assertEqual([record.session_id for record in records], ["sales-session"])
        self.assertTrue(await store.delete_conversation("sales-session"))
        self.assertFalse(await store.delete_conversation("sales-session"))
        self.assertEqual(await store.list_conversations(), [])


if __name__ == "__main__":
    unittest.main()

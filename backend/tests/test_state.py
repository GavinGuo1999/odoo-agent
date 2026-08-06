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


if __name__ == "__main__":
    unittest.main()

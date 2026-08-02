"""Start Uvicorn briefly and verify the public local HTTP endpoints."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import uvicorn


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    port = 8090
    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    server_task = asyncio.create_task(server.serve())

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(40):
                if server.started:
                    break
                if server_task.done():
                    await server_task
                    return 1
                await asyncio.sleep(0.1)
            else:
                print("Backend did not become ready.")
                return 1

            health = await client.get("/api/health")
            docs = await client.get("/docs")
            health.raise_for_status()
            docs.raise_for_status()

            payload = health.json()
            print(f"health_status={health.status_code}")
            print(f"docs_status={docs.status_code}")
            print(f"app_status={payload['status']}")
            print(f"langfuse_configured={payload['services']['langfuse']['configured']}")
            print(f"selected_provider={payload['selected_provider']}")
            print(f"model_ready={payload['ready_for_model_calls']}")
            print(
                "odoo_access_mode="
                f"{payload['services']['odoo_database']['access_mode']}"
            )
            return 0
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

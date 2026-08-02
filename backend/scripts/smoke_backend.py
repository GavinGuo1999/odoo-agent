"""Start Uvicorn briefly and verify the public local HTTP endpoints."""

from __future__ import annotations

import asyncio
import argparse
import logging
import sys
from pathlib import Path

import httpx
import uvicorn


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main(*, with_model: bool) -> int:
    logging.getLogger("langfuse").setLevel(logging.CRITICAL)
    logging.getLogger("openai").setLevel(logging.CRITICAL)
    if with_model:
        logging.disable(logging.CRITICAL)

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

            if with_model:
                chat = await client.post(
                    "/api/chat",
                    json={
                        "question": (
                            "请用一句话确认模型连接正常，并明确说明尚未查询 "
                            "Odoo 数据。"
                        )
                    },
                    timeout=120,
                )
                if chat.status_code != 200:
                    error_type = chat.json().get("detail", "unknown-error")
                    print(f"chat_status={chat.status_code}")
                    print(f"chat_error={error_type}")
                    return 2
                chat_payload = chat.json()
                print(f"chat_status={chat.status_code}")
                print(f"chat_provider={chat_payload['provider']}")
                print(f"chat_model={chat_payload['model']}")
                print(f"chat_data_accessed={chat_payload['data_accessed']}")
                print(f"chat_total_tokens={chat_payload['usage']['total_tokens']}")
                print(f"chat_trace_id={chat_payload['trace_id']}")
            return 0
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-model", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(with_model=arguments.with_model)))

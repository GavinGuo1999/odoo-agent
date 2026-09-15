from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.api.router import api_router
from app.config import get_settings
from app.observability import flush_langfuse, langfuse_is_configured, warm_langfuse_client
from app.state import get_state_store


_PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
_UI_FILES = {
    "app.js",
    "auth.js",
    "chat.html",
    "dashboard.html",
    "echarts.min.js",
    "index.html",
    "markdown.js",
    "quality.html",
    "quality.js",
    "sidebar.js",
    "settings.html",
    "styles.css",
    "wiki.html",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    state_store = get_state_store()
    await state_store.start(settings.state_database())
    if langfuse_is_configured():
        await asyncio.to_thread(warm_langfuse_client)
    yield

    await state_store.close()

    if langfuse_is_configured():
        flush_langfuse()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Read-only Odoo sales ChatBI API",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=settings.api_prefix)

    @application.get("/ui", include_in_schema=False)
    async def ui_root() -> RedirectResponse:
        return RedirectResponse(url="/ui/index.html")

    @application.get("/ui/{filename:path}", include_in_schema=False)
    async def ui_file(request: Request, filename: str) -> Response:
        if filename not in _UI_FILES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        path = _PROJECT_DIRECTORY / filename
        # 手工的 `?v=N` 版本号漏改一次就会产生“改了没生效”的假缺陷，因此改由
        # 服务端保证：每次都校验，内容没变则 304，改了立刻生效。
        try:
            stat = path.stat()
        except OSError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
        etag = f'"{int(stat.st_mtime_ns):x}-{stat.st_size:x}"'
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        return FileResponse(path, headers=headers)

    @application.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/index.html")

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return application


app = create_app()

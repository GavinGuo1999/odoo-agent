from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.api.router import api_router
from app.config import get_settings
from app.observability import langfuse_is_configured


_PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
_UI_FILES = {
    "app.js",
    "chat.html",
    "dashboard.html",
    "index.html",
    "settings.html",
    "styles.css",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield

    if langfuse_is_configured():
        from langfuse import get_client

        get_client().flush()


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
    async def ui_file(filename: str) -> FileResponse:
        if filename not in _UI_FILES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(_PROJECT_DIRECTORY / filename)

    @application.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/index.html")

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return application


app = create_app()

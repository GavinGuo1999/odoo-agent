from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.schemas.wiki import WikiSearchResponse, WikiStatusResponse
from app.services.wiki_knowledge import get_wiki_service


router = APIRouter(prefix="/wiki", tags=["wiki"])


@router.get("/status", response_model=WikiStatusResponse)
async def wiki_status(settings: Settings = Depends(get_settings)) -> WikiStatusResponse:
    status = await asyncio.to_thread(get_wiki_service(settings.wiki()).status)
    return WikiStatusResponse.model_validate(asdict(status))


@router.get("/search", response_model=WikiSearchResponse)
async def wiki_search(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=6, ge=1, le=12),
    settings: Settings = Depends(get_settings),
) -> WikiSearchResponse:
    result = await asyncio.to_thread(
        get_wiki_service(settings.wiki()).search,
        q,
        limit=limit,
    )
    return WikiSearchResponse(
        query=result.query,
        index_fingerprint=result.index_fingerprint,
        hits=[hit.citation() for hit in result.hits],
    )


@router.post("/reindex", response_model=WikiStatusResponse)
async def wiki_reindex(settings: Settings = Depends(get_settings)) -> WikiStatusResponse:
    status = await asyncio.to_thread(
        get_wiki_service(settings.wiki()).ensure_index,
        force=True,
    )
    return WikiStatusResponse.model_validate(asdict(status))

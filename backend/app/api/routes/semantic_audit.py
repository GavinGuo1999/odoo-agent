from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.schemas.semantic_audit import SemanticAuditResponse
from app.services.semantic_sync import SemanticSyncError, SemanticSyncService


router = APIRouter(prefix="/semantic-audit", tags=["semantic-audit"])
_audit_lock = asyncio.Lock()


def _service(settings: Settings) -> SemanticSyncService:
    return SemanticSyncService(settings.database(), settings.semantic_sync())


@router.get("/latest", response_model=SemanticAuditResponse)
async def latest_semantic_audit(
    settings: Settings = Depends(get_settings),
) -> SemanticAuditResponse:
    try:
        result = _service(settings).latest()
    except SemanticSyncError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"语义审计结果读取失败：{exc}",
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="尚未运行过语义一致性审计。",
        )
    return SemanticAuditResponse.model_validate(result)


@router.post("", response_model=SemanticAuditResponse)
async def run_semantic_audit(
    settings: Settings = Depends(get_settings),
) -> SemanticAuditResponse:
    if _audit_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="语义一致性审计正在运行，请稍后再试。",
        )
    async with _audit_lock:
        try:
            result = await _service(settings).audit()
        except SemanticSyncError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"语义一致性审计失败：{exc}",
            ) from exc
    return SemanticAuditResponse.model_validate(result)

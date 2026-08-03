from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.database import OdooDatabase
from app.observability import langfuse_is_configured


router = APIRouter(tags=["system"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    providers: dict[str, dict[str, object]] = {}
    for name in ("deepseek", "siliconflow"):
        config = settings.provider(name)  # type: ignore[arg-type]
        providers[name] = {
            "configured": config.configured,
            "model": config.model,
            "base_url": config.base_url,
        }

    selected = settings.provider()
    database_health = await OdooDatabase(settings.database()).healthcheck()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "ready_for_model_calls": selected.configured,
        "selected_provider": settings.llm_provider,
        "services": {
            "langfuse": {"configured": langfuse_is_configured()},
            "providers": providers,
            "odoo_database": {
                "configured": settings.database().configured,
                "access_mode": "read-only",
                "status": "connected" if database_health.connected else "not-connected",
                "read_only": database_health.read_only,
                "company_id": database_health.company_id,
                "company_name": database_health.company_name,
                "currency": database_health.currency,
                "order_count": database_health.order_count,
                "response_ms": database_health.response_ms,
            },
        },
    }

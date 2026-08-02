from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status

from app.config import get_settings
from app.observability import langfuse_is_configured
from app.schemas.settings import (
    LangfuseSettingsView,
    ProviderSettingsView,
    SettingsUpdateRequest,
    SettingsView,
)
from app.services import set_user_environment


router = APIRouter(prefix="/settings", tags=["settings"])
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _secret_value(secret: object | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value().strip()  # type: ignore[attr-defined]
    return value or None


def _settings_view(*, restart_required: bool = False) -> SettingsView:
    settings = get_settings()
    providers = {}
    for name in ("deepseek", "siliconflow"):
        provider = settings.provider(name)  # type: ignore[arg-type]
        providers[name] = ProviderSettingsView(
            configured=provider.configured,
            base_url=provider.base_url,
            model=provider.model,
        )

    langfuse_enabled = (
        os.getenv("LANGFUSE_ENABLED", "true").strip().lower() in _TRUE_VALUES
        and os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower()
        in _TRUE_VALUES
    )
    return SettingsView(
        selected_provider=settings.llm_provider,
        providers=providers,  # type: ignore[arg-type]
        langfuse=LangfuseSettingsView(
            configured=bool(
                os.getenv("LANGFUSE_PUBLIC_KEY")
                and os.getenv("LANGFUSE_SECRET_KEY")
                and os.getenv("LANGFUSE_BASE_URL")
            ),
            base_url=os.getenv(
                "LANGFUSE_BASE_URL",
                "https://cloud.langfuse.com",
            ).rstrip("/"),
            enabled=langfuse_enabled,
        ),
        restart_required=restart_required,
    )


@router.get("", response_model=SettingsView)
async def read_settings() -> SettingsView:
    """Return safe configuration metadata without returning any credential."""

    return _settings_view()


@router.put("", response_model=SettingsView)
async def update_settings(payload: SettingsUpdateRequest) -> SettingsView:
    """Save application settings in the current Windows user environment."""

    deepseek_key = _secret_value(payload.deepseek.api_key)
    siliconflow_key = _secret_value(payload.siliconflow.api_key)
    langfuse_public_key = _secret_value(payload.langfuse.public_key)
    langfuse_secret_key = _secret_value(payload.langfuse.secret_key)

    if bool(langfuse_public_key) != bool(langfuse_secret_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Langfuse Public Key 和 Secret Key 必须同时填写。",
        )
    if langfuse_public_key and not langfuse_public_key.startswith("pk-lf-"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Langfuse Key 格式不正确。",
        )
    if langfuse_secret_key and not langfuse_secret_key.startswith("sk-lf-"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Langfuse Key 格式不正确。",
        )

    langfuse_was_configured = langfuse_is_configured()
    old_langfuse_base_url = os.getenv("LANGFUSE_BASE_URL", "").rstrip("/")
    new_langfuse_base_url = str(payload.langfuse.base_url).rstrip("/")
    langfuse_credentials_changed = bool(
        langfuse_public_key
        and (
            langfuse_public_key != os.getenv("LANGFUSE_PUBLIC_KEY")
            or langfuse_secret_key != os.getenv("LANGFUSE_SECRET_KEY")
        )
    )
    langfuse_connection_changed = (
        old_langfuse_base_url != new_langfuse_base_url
        or langfuse_credentials_changed
    )

    updates = {
        "LLM_PROVIDER": payload.selected_provider,
        "DEEPSEEK_BASE_URL": str(payload.deepseek.base_url).rstrip("/"),
        "DEEPSEEK_MODEL": payload.deepseek.model.strip(),
        "SILICONFLOW_BASE_URL": str(payload.siliconflow.base_url).rstrip("/"),
        "SILICONFLOW_MODEL": payload.siliconflow.model.strip(),
        "LANGFUSE_BASE_URL": new_langfuse_base_url,
        "LANGFUSE_ENABLED": str(payload.langfuse.enabled).lower(),
        "LANGFUSE_TRACING_ENABLED": str(payload.langfuse.enabled).lower(),
    }
    if deepseek_key:
        updates["DEEPSEEK_API_KEY"] = deepseek_key
    if siliconflow_key:
        updates["SILICONFLOW_API_KEY"] = siliconflow_key
    if langfuse_public_key and langfuse_secret_key:
        updates["LANGFUSE_PUBLIC_KEY"] = langfuse_public_key
        updates["LANGFUSE_SECRET_KEY"] = langfuse_secret_key

    try:
        set_user_environment(updates)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="配置未保存，请确认应用有权写入当前 Windows 用户设置。",
        ) from exc

    get_settings.cache_clear()
    return _settings_view(
        restart_required=langfuse_was_configured and langfuse_connection_changed,
    )

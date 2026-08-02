"""Persist an allow-listed set of settings for the current Windows user."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Mapping


_ALLOWED_NAMES = {
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_ENABLED",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_TRACING_ENABLED",
    "LLM_PROVIDER",
    "SILICONFLOW_API_KEY",
    "SILICONFLOW_BASE_URL",
    "SILICONFLOW_MODEL",
}


def _notify_windows_environment_changed() -> None:
    # SendNotifyMessage returns immediately and lets Explorer refresh the
    # environment inherited by applications launched later.
    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    ctypes.windll.user32.SendNotifyMessageW(  # type: ignore[attr-defined]
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
    )


def set_user_environment(updates: Mapping[str, str]) -> None:
    unknown = set(updates) - _ALLOWED_NAMES
    if unknown:
        raise ValueError("Unsupported environment setting.")
    if os.name != "nt":
        raise RuntimeError("Windows user environment storage is unavailable.")

    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        for name, value in updates.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    # Apply the same values to this FastAPI process so model changes work
    # immediately; secrets are never returned or logged by this service.
    os.environ.update(updates)
    _notify_windows_environment_changed()

from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, field_validator

from app.config import ProviderName


class ProviderSettingsUpdate(BaseModel):
    api_key: SecretStr | None = None
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Model name must not be blank.")
        return normalized


class LangfuseSettingsUpdate(BaseModel):
    public_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    base_url: AnyHttpUrl = "https://cloud.langfuse.com"
    enabled: bool = True


class SettingsUpdateRequest(BaseModel):
    selected_provider: ProviderName
    deepseek: ProviderSettingsUpdate
    siliconflow: ProviderSettingsUpdate
    langfuse: LangfuseSettingsUpdate


class ProviderSettingsView(BaseModel):
    configured: bool
    base_url: str
    model: str


class ProviderModelsView(BaseModel):
    provider: ProviderName
    current_model: str
    models: list[str]


class LangfuseSettingsView(BaseModel):
    configured: bool
    base_url: str
    enabled: bool


class SettingsView(BaseModel):
    selected_provider: ProviderName
    providers: dict[ProviderName, ProviderSettingsView]
    langfuse: LangfuseSettingsView
    persistence: Literal["windows-user-environment"] = "windows-user-environment"
    restart_required: bool = False

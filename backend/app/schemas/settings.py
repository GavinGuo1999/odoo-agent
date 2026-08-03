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


class DatabaseSettingsUpdate(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    user: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = None
    company_id: int = Field(ge=1)
    statement_timeout_ms: int = Field(ge=1_000, le=120_000)
    max_rows: int = Field(ge=1, le=5_000)


class SettingsUpdateRequest(BaseModel):
    selected_provider: ProviderName
    deepseek: ProviderSettingsUpdate
    siliconflow: ProviderSettingsUpdate
    langfuse: LangfuseSettingsUpdate
    database: DatabaseSettingsUpdate


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


class DatabaseSettingsView(BaseModel):
    configured: bool
    password_configured: bool
    host: str
    port: int
    database: str
    user: str
    company_id: int
    statement_timeout_ms: int
    max_rows: int


class SettingsView(BaseModel):
    selected_provider: ProviderName
    providers: dict[ProviderName, ProviderSettingsView]
    langfuse: LangfuseSettingsView
    database: DatabaseSettingsView
    persistence: Literal["windows-user-environment"] = "windows-user-environment"
    restart_required: bool = False

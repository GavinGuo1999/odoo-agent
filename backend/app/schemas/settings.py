from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, field_validator

from app.config import ProviderName, SemanticProviderName, ThinkingMode


class ProviderSettingsUpdate(BaseModel):
    api_key: SecretStr | None = None
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)

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


class ModelRoleSettingsUpdate(BaseModel):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)


class ModelRoutingSettingsUpdate(BaseModel):
    sql: ModelRoleSettingsUpdate
    answer: ModelRoleSettingsUpdate
    general: ModelRoleSettingsUpdate


class StateDatabaseSettingsUpdate(BaseModel):
    enabled: bool = False
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=55432, ge=1, le=65535)
    database: str = Field(default="odoo_agent_state", min_length=1, max_length=128)
    user: str = Field(default="odoo_agent_state", min_length=1, max_length=128)
    password: SecretStr | None = None


class SemanticSettingsUpdate(BaseModel):
    provider: SemanticProviderName = "native"


class SettingsUpdateRequest(BaseModel):
    selected_provider: ProviderName
    deepseek: ProviderSettingsUpdate
    siliconflow: ProviderSettingsUpdate
    routing: ModelRoutingSettingsUpdate
    langfuse: LangfuseSettingsUpdate
    database: DatabaseSettingsUpdate
    state_database: StateDatabaseSettingsUpdate
    semantic: SemanticSettingsUpdate = Field(default_factory=SemanticSettingsUpdate)
    sql_thinking_mode: ThinkingMode = "disabled"


class ProviderSettingsView(BaseModel):
    configured: bool
    base_url: str
    model: str
    input_price_per_million: float
    output_price_per_million: float
    pricing_currency: Literal["USD", "CNY"]


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


class ModelRoleSettingsView(BaseModel):
    provider: ProviderName
    model: str


class ModelRoutingSettingsView(BaseModel):
    sql: ModelRoleSettingsView
    answer: ModelRoleSettingsView
    general: ModelRoleSettingsView


class StateDatabaseSettingsView(BaseModel):
    enabled: bool
    configured: bool
    password_configured: bool
    host: str
    port: int
    database: str
    user: str
    active_mode: Literal["memory", "postgres"]
    error_type: str | None = None


class SemanticSettingsView(BaseModel):
    provider: SemanticProviderName
    native_version: str
    wren_project_path: str
    wren_project_configured: bool
    wren_executable_configured: bool


class SettingsView(BaseModel):
    selected_provider: ProviderName
    providers: dict[ProviderName, ProviderSettingsView]
    routing: ModelRoutingSettingsView
    langfuse: LangfuseSettingsView
    database: DatabaseSettingsView
    state_database: StateDatabaseSettingsView
    semantic: SemanticSettingsView
    sql_thinking_mode: ThinkingMode
    persistence: Literal["windows-user-environment"] = "windows-user-environment"
    restart_required: bool = False

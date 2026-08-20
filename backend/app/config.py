"""Application configuration loaded from operating-system environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ProviderName = Literal["deepseek", "siliconflow"]
ModelRole = Literal["sql", "answer", "general"]
SemanticProviderName = Literal["native", "wren"]
ThinkingMode = Literal["auto", "enabled", "disabled"]


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: ProviderName
    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    pricing_currency: Literal["USD", "CNY"] = "USD"
    cny_per_usd: float = 7.2
    thinking_mode: ThinkingMode = "auto"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def estimated_cost_usd(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> tuple[float, float]:
        input_cost = (input_tokens or 0) * self.input_price_per_million / 1_000_000
        output_cost = (output_tokens or 0) * self.output_price_per_million / 1_000_000
        if self.pricing_currency == "CNY":
            input_cost /= self.cny_per_usd
            output_cost /= self.cny_per_usd
        return input_cost, output_cost


@dataclass(frozen=True, slots=True)
class ModelRoutingConfig:
    sql: ProviderConfig
    answer: ProviderConfig
    general: ProviderConfig

    def for_role(self, role: ModelRole) -> ProviderConfig:
        return getattr(self, role)


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str | None
    company_id: int
    statement_timeout_ms: int
    max_rows: int

    @property
    def configured(self) -> bool:
        return bool(self.host and self.port and self.database and self.user)


@dataclass(frozen=True, slots=True)
class StateDatabaseConfig:
    enabled: bool
    host: str
    port: int
    database: str
    user: str
    password: str | None

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.host
            and self.port
            and self.database
            and self.user
        )


@dataclass(frozen=True, slots=True)
class SemanticConfig:
    provider: SemanticProviderName
    wren_project_path: Path
    wren_executable: str | None
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class SemanticSyncConfig:
    source_path: Path
    output_path: Path
    formal_wren_project_path: Path


@dataclass(frozen=True, slots=True)
class WikiConfig:
    root_path: Path
    index_path: Path
    allowed_statuses: tuple[str, ...]
    max_results: int


class Settings(BaseSettings):
    """Settings intentionally avoid `.env` files so credentials stay out of Git."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(
        default="Odoo Sales Agent",
        validation_alias="ODOO_AGENT_APP_NAME",
    )
    app_version: str = "0.2.0"
    environment: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias="ODOO_AGENT_ENVIRONMENT",
    )
    api_prefix: str = "/api"

    llm_provider: ProviderName = Field(
        default="deepseek",
        validation_alias="LLM_PROVIDER",
    )
    llm_timeout_seconds: float = Field(
        default=90.0,
        ge=1.0,
        le=300.0,
        validation_alias="LLM_TIMEOUT_SECONDS",
    )

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_base_url: AnyHttpUrl = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(
        default="deepseek-v4-pro",
        validation_alias="DEEPSEEK_MODEL",
    )
    deepseek_input_price_per_million: float = Field(
        default=0.435,
        ge=0,
        validation_alias="DEEPSEEK_INPUT_PRICE_PER_MILLION",
    )
    deepseek_output_price_per_million: float = Field(
        default=0.87,
        ge=0,
        validation_alias="DEEPSEEK_OUTPUT_PRICE_PER_MILLION",
    )

    siliconflow_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="SILICONFLOW_API_KEY",
    )
    siliconflow_base_url: AnyHttpUrl = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias="SILICONFLOW_BASE_URL",
    )
    siliconflow_model: str = Field(
        default="deepseek-ai/DeepSeek-V3.1-Terminus",
        validation_alias="SILICONFLOW_MODEL",
    )
    siliconflow_input_price_per_million: float = Field(
        default=4.0,
        ge=0,
        validation_alias="SILICONFLOW_INPUT_PRICE_PER_MILLION",
    )
    siliconflow_output_price_per_million: float = Field(
        default=12.0,
        ge=0,
        validation_alias="SILICONFLOW_OUTPUT_PRICE_PER_MILLION",
    )
    cny_per_usd: float = Field(
        default=7.2,
        gt=0,
        validation_alias="CNY_PER_USD",
    )

    sql_llm_provider: ProviderName | None = Field(
        default=None,
        validation_alias="SQL_LLM_PROVIDER",
    )
    sql_llm_model: str | None = Field(
        default=None,
        validation_alias="SQL_LLM_MODEL",
    )
    sql_llm_thinking_mode: ThinkingMode = Field(
        default="disabled",
        validation_alias="SQL_LLM_THINKING_MODE",
    )
    answer_llm_provider: ProviderName | None = Field(
        default=None,
        validation_alias="ANSWER_LLM_PROVIDER",
    )
    answer_llm_model: str | None = Field(
        default=None,
        validation_alias="ANSWER_LLM_MODEL",
    )
    general_llm_provider: ProviderName | None = Field(
        default=None,
        validation_alias="GENERAL_LLM_PROVIDER",
    )
    general_llm_model: str | None = Field(
        default=None,
        validation_alias="GENERAL_LLM_MODEL",
    )

    semantic_provider: SemanticProviderName = Field(
        default="native",
        validation_alias="SEMANTIC_PROVIDER",
    )
    wren_project_path: str | None = Field(
        default=None,
        validation_alias="WREN_PROJECT_PATH",
    )
    wren_executable: str | None = Field(
        default=None,
        validation_alias="WREN_EXECUTABLE",
    )
    wren_timeout_seconds: float = Field(
        default=20.0,
        ge=1.0,
        le=120.0,
        validation_alias="WREN_TIMEOUT_SECONDS",
    )
    odoo_source_path: str | None = Field(
        default=None,
        validation_alias="ODOO_SOURCE_PATH",
    )
    semantic_sync_output_path: str | None = Field(
        default=None,
        validation_alias="SEMANTIC_SYNC_OUTPUT_PATH",
    )
    wiki_path: str | None = Field(
        default=None,
        validation_alias="WIKI_PATH",
    )
    wiki_index_path: str | None = Field(
        default=None,
        validation_alias="WIKI_INDEX_PATH",
    )
    wiki_max_results: int = Field(
        default=6,
        ge=1,
        le=12,
        validation_alias="WIKI_MAX_RESULTS",
    )

    odoo_db_host: str = Field(
        default="127.0.0.1",
        validation_alias="ODOO_DB_HOST",
    )
    odoo_db_port: int = Field(
        default=55432,
        ge=1,
        le=65535,
        validation_alias="ODOO_DB_PORT",
    )
    odoo_db_name: str = Field(
        default="odoo19_dev",
        validation_alias="ODOO_DB_NAME",
    )
    odoo_db_user: str = Field(
        default="codex_readonly",
        validation_alias="ODOO_DB_USER",
    )
    odoo_db_password: SecretStr | None = Field(
        default=None,
        validation_alias="ODOO_DB_PASSWORD",
    )
    odoo_company_id: int = Field(
        default=1,
        ge=1,
        validation_alias="ODOO_COMPANY_ID",
    )
    odoo_statement_timeout_ms: int = Field(
        default=15_000,
        ge=1_000,
        le=120_000,
        validation_alias="ODOO_STATEMENT_TIMEOUT_MS",
    )
    odoo_max_rows: int = Field(
        default=500,
        ge=1,
        le=5_000,
        validation_alias="ODOO_MAX_ROWS",
    )

    agent_state_enabled: bool = Field(
        default=False,
        validation_alias="AGENT_STATE_ENABLED",
    )
    agent_state_db_host: str = Field(
        default="127.0.0.1",
        validation_alias="AGENT_STATE_DB_HOST",
    )
    agent_state_db_port: int = Field(
        default=55432,
        ge=1,
        le=65535,
        validation_alias="AGENT_STATE_DB_PORT",
    )
    agent_state_db_name: str = Field(
        default="odoo_agent_state",
        validation_alias="AGENT_STATE_DB_NAME",
    )
    agent_state_db_user: str = Field(
        default="odoo_agent_state",
        validation_alias="AGENT_STATE_DB_USER",
    )
    agent_state_db_password: SecretStr | None = Field(
        default=None,
        validation_alias="AGENT_STATE_DB_PASSWORD",
    )

    def provider(
        self,
        name: ProviderName | None = None,
        *,
        model: str | None = None,
        thinking_mode: ThinkingMode = "auto",
    ) -> ProviderConfig:
        selected = name or self.llm_provider
        if selected == "deepseek":
            secret = (
                self.deepseek_api_key.get_secret_value()
                if self.deepseek_api_key
                else None
            )
            return ProviderConfig(
                name="deepseek",
                api_key=secret,
                base_url=str(self.deepseek_base_url).rstrip("/"),
                model=model or self.deepseek_model,
                timeout_seconds=self.llm_timeout_seconds,
                input_price_per_million=self.deepseek_input_price_per_million,
                output_price_per_million=self.deepseek_output_price_per_million,
                pricing_currency="USD",
                cny_per_usd=self.cny_per_usd,
                thinking_mode=thinking_mode,
            )

        secret = (
            self.siliconflow_api_key.get_secret_value()
            if self.siliconflow_api_key
            else None
        )
        return ProviderConfig(
            name="siliconflow",
            api_key=secret,
            base_url=str(self.siliconflow_base_url).rstrip("/"),
            model=model or self.siliconflow_model,
            timeout_seconds=self.llm_timeout_seconds,
            input_price_per_million=self.siliconflow_input_price_per_million,
            output_price_per_million=self.siliconflow_output_price_per_million,
            pricing_currency="CNY",
            cny_per_usd=self.cny_per_usd,
            thinking_mode=thinking_mode,
        )

    def routing(self, override_provider: ProviderName | None = None) -> ModelRoutingConfig:
        if override_provider:
            return ModelRoutingConfig(
                sql=self.provider(
                    override_provider,
                    thinking_mode=self.sql_llm_thinking_mode,
                ),
                answer=self.provider(override_provider),
                general=self.provider(override_provider),
            )

        return ModelRoutingConfig(
            sql=self.provider(
                self.sql_llm_provider or self.llm_provider,
                model=self.sql_llm_model,
                thinking_mode=self.sql_llm_thinking_mode,
            ),
            answer=self.provider(
                self.answer_llm_provider or self.llm_provider,
                model=self.answer_llm_model,
            ),
            general=self.provider(
                self.general_llm_provider or self.llm_provider,
                model=self.general_llm_model,
            ),
        )

    def database(self) -> DatabaseConfig:
        password = (
            self.odoo_db_password.get_secret_value()
            if self.odoo_db_password
            else None
        )
        return DatabaseConfig(
            host=self.odoo_db_host,
            port=self.odoo_db_port,
            database=self.odoo_db_name,
            user=self.odoo_db_user,
            password=password,
            company_id=self.odoo_company_id,
            statement_timeout_ms=self.odoo_statement_timeout_ms,
            max_rows=self.odoo_max_rows,
        )

    def state_database(self) -> StateDatabaseConfig:
        password = (
            self.agent_state_db_password.get_secret_value()
            if self.agent_state_db_password
            else None
        )
        return StateDatabaseConfig(
            enabled=self.agent_state_enabled,
            host=self.agent_state_db_host,
            port=self.agent_state_db_port,
            database=self.agent_state_db_name,
            user=self.agent_state_db_user,
            password=password,
        )

    def semantic(self) -> SemanticConfig:
        bundled_project = Path(__file__).resolve().parent / "bi" / "wren_project"
        project_path = (
            Path(self.wren_project_path).expanduser().resolve()
            if self.wren_project_path
            else bundled_project
        )
        executable = self.wren_executable.strip() if self.wren_executable else None
        return SemanticConfig(
            provider=self.semantic_provider,
            wren_project_path=project_path,
            wren_executable=executable or None,
            timeout_seconds=self.wren_timeout_seconds,
        )

    def semantic_sync(self) -> SemanticSyncConfig:
        project_path = Path(__file__).resolve().parents[2]
        workspace_path = project_path.parent
        source_path = (
            Path(self.odoo_source_path).expanduser().resolve()
            if self.odoo_source_path
            else workspace_path / "odoo-19.0+e.20250917"
        )
        output_path = (
            Path(self.semantic_sync_output_path).expanduser().resolve()
            if self.semantic_sync_output_path
            else project_path / ".semantic-sync"
        )
        return SemanticSyncConfig(
            source_path=source_path,
            output_path=output_path,
            formal_wren_project_path=self.semantic().wren_project_path,
        )

    def wiki(self) -> WikiConfig:
        project_path = Path(__file__).resolve().parents[2]
        workspace_path = project_path.parent
        root_path = (
            Path(self.wiki_path).expanduser().resolve()
            if self.wiki_path
            else workspace_path / "learn_odoo"
        )
        index_path = (
            Path(self.wiki_index_path).expanduser().resolve()
            if self.wiki_index_path
            else project_path / ".wiki-index" / "wiki.db"
        )
        return WikiConfig(
            root_path=root_path,
            index_path=index_path,
            allowed_statuses=("reviewed", "evergreen"),
            max_results=self.wiki_max_results,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

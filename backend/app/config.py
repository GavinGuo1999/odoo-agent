"""Application configuration loaded from operating-system environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ProviderName = Literal["deepseek", "siliconflow"]


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: ProviderName
    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


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
    app_version: str = "0.1.0"
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

    def provider(self, name: ProviderName | None = None) -> ProviderConfig:
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
                model=self.deepseek_model,
                timeout_seconds=self.llm_timeout_seconds,
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
            model=self.siliconflow_model,
            timeout_seconds=self.llm_timeout_seconds,
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

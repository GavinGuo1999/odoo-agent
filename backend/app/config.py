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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


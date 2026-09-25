"""Application configuration loaded from operating-system environment variables."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ProviderName = Literal["deepseek", "siliconflow"]
ModelRole = Literal["sql", "answer", "general"]
SemanticProviderName = Literal["native", "wren", "cube"]
WikiRetrievalMode = Literal["lexical", "hybrid"]
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
    # 命中提示缓存的输入 token 单价。留 0 表示"不区分"，按 input 全价算。
    cached_input_price_per_million: float = 0.0
    pricing_currency: Literal["USD", "CNY"] = "USD"
    cny_per_usd: float = 7.2
    # 分时折扣（DeepSeek 的谷时优惠是这种形式）。窗口按 Asia/Shanghai 的墙钟时间，
    # 允许跨零点（start > end 时表示"当天 start 到次日 end"）。
    # 折扣留 1.0 = 不打折，也就是保持改动前的行为。
    offpeak_start_hhmm: str = ""
    offpeak_end_hhmm: str = ""
    offpeak_input_multiplier: float = 1.0
    offpeak_output_multiplier: float = 1.0
    thinking_mode: ThinkingMode = "auto"
    max_retries: int = 1
    retry_backoff_seconds: float = 0.5

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def in_offpeak_window(self, moment: datetime | None = None) -> bool:
        """当前是否落在谷时窗口内。窗口未配置时恒为 False。"""

        if not (self.offpeak_start_hhmm and self.offpeak_end_hhmm):
            return False
        try:
            start = _parse_hhmm(self.offpeak_start_hhmm)
            end = _parse_hhmm(self.offpeak_end_hhmm)
        except ValueError:
            return False
        now = (moment or datetime.now(_PRICING_TZ)).astimezone(_PRICING_TZ)
        minutes = now.hour * 60 + now.minute
        if start <= end:
            return start <= minutes < end
        # 跨零点：例如 00:30~08:30 之外的写法 23:00~07:00。
        return minutes >= start or minutes < end

    def estimated_cost_usd(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None = None,
        moment: datetime | None = None,
    ) -> tuple[float, float]:
        """估算一次调用的成本。

        三件事以前没做，会让账单和这里的数字对不上：
          1. 谷时折扣——按墙钟时间打折，而不是全天一个价；
          2. 提示缓存命中的输入 token 单价远低于未命中，混在一起会高估；
          3. 折扣只作用于单价，不改变币种换算。
        """

        billed_input = max(0, (input_tokens or 0) - (cached_input_tokens or 0))
        cached = max(0, cached_input_tokens or 0)
        cached_rate = self.cached_input_price_per_million or self.input_price_per_million

        input_rate = self.input_price_per_million
        output_rate = self.output_price_per_million
        if self.in_offpeak_window(moment):
            input_rate *= self.offpeak_input_multiplier
            output_rate *= self.offpeak_output_multiplier
            cached_rate *= self.offpeak_input_multiplier

        input_cost = (billed_input * input_rate + cached * cached_rate) / 1_000_000
        output_cost = (output_tokens or 0) * output_rate / 1_000_000
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
    explain_total_cost_limit: float = 1_000_000.0

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
    # Cube 是常驻服务而非 CLI，所以这里只需要一个地址；模型定义与数据库连接
    # 都由 Cube 自己那份配置负责（见 app/bi/cube_project/cube.js）。
    cube_base_url: str = "http://127.0.0.1:4000/cubejs-api/v1"
    cube_api_token: str | None = None


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
    retrieval_mode: WikiRetrievalMode = "hybrid"
    vector_index_path: Path | None = None
    embedding_api_key: str | None = field(default=None, repr=False)
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    vector_candidates: int = 24
    rerank_candidates: int = 12
    api_timeout_seconds: float = 30.0


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

    # 演示门禁：留空 = 不启用（本机开发保持零摩擦）。存的是 PBKDF2 派生值，
    # 不是明文；用 `python -m app.security.session_gate` 生成。
    ui_password_hash: str = Field(
        default="",
        validation_alias="AGENT_UI_PASSWORD_HASH",
    )
    ui_session_ttl_seconds: int = Field(
        default=12 * 3600,
        ge=60,
        le=7 * 24 * 3600,
        validation_alias="AGENT_UI_SESSION_TTL_SECONDS",
    )

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
    llm_max_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        validation_alias="LLM_MAX_RETRIES",
    )
    llm_retry_backoff_seconds: float = Field(
        default=0.5,
        ge=0,
        le=10,
        validation_alias="LLM_RETRY_BACKOFF_SECONDS",
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
    deepseek_cached_input_price_per_million: float = Field(
        default=0.0,
        ge=0,
        validation_alias="DEEPSEEK_CACHED_INPUT_PRICE_PER_MILLION",
    )
    # 谷时窗口与折扣。**默认不打折**——供应商的档位会变，写死在代码里迟早对不上账，
    # 这里只提供机制，具体数值按当时的官方价目表填进环境变量。
    deepseek_offpeak_start: str = Field(
        default="",
        validation_alias="DEEPSEEK_OFFPEAK_START",
    )
    deepseek_offpeak_end: str = Field(
        default="",
        validation_alias="DEEPSEEK_OFFPEAK_END",
    )
    deepseek_offpeak_input_multiplier: float = Field(
        default=1.0, ge=0, le=1,
        validation_alias="DEEPSEEK_OFFPEAK_INPUT_MULTIPLIER",
    )
    deepseek_offpeak_output_multiplier: float = Field(
        default=1.0, ge=0, le=1,
        validation_alias="DEEPSEEK_OFFPEAK_OUTPUT_MULTIPLIER",
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
    cube_base_url: str = Field(
        default="http://127.0.0.1:4000/cubejs-api/v1",
        validation_alias="CUBE_BASE_URL",
    )
    cube_api_token: str | None = Field(
        default=None,
        validation_alias="CUBE_API_TOKEN",
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
    wiki_retrieval_mode: WikiRetrievalMode = Field(
        default="hybrid",
        validation_alias="WIKI_RETRIEVAL_MODE",
    )
    wiki_vector_index_path: str | None = Field(
        default=None,
        validation_alias="WIKI_VECTOR_INDEX_PATH",
    )
    wiki_embedding_model: str = Field(
        default="BAAI/bge-m3",
        validation_alias="WIKI_EMBEDDING_MODEL",
    )
    wiki_reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        validation_alias="WIKI_RERANKER_MODEL",
    )
    wiki_vector_candidates: int = Field(
        default=24,
        ge=6,
        le=100,
        validation_alias="WIKI_VECTOR_CANDIDATES",
    )
    wiki_rerank_candidates: int = Field(
        default=12,
        ge=1,
        le=50,
        validation_alias="WIKI_RERANK_CANDIDATES",
    )
    wiki_api_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        validation_alias="WIKI_API_TIMEOUT_SECONDS",
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
    odoo_explain_total_cost_limit: float = Field(
        default=1_000_000.0,
        gt=0,
        le=1_000_000_000.0,
        validation_alias="ODOO_EXPLAIN_TOTAL_COST_LIMIT",
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
                cached_input_price_per_million=self.deepseek_cached_input_price_per_million,
                offpeak_start_hhmm=self.deepseek_offpeak_start,
                offpeak_end_hhmm=self.deepseek_offpeak_end,
                offpeak_input_multiplier=self.deepseek_offpeak_input_multiplier,
                offpeak_output_multiplier=self.deepseek_offpeak_output_multiplier,
                pricing_currency="USD",
                cny_per_usd=self.cny_per_usd,
                thinking_mode=thinking_mode,
                max_retries=self.llm_max_retries,
                retry_backoff_seconds=self.llm_retry_backoff_seconds,
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
            max_retries=self.llm_max_retries,
            retry_backoff_seconds=self.llm_retry_backoff_seconds,
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
            explain_total_cost_limit=self.odoo_explain_total_cost_limit,
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
        token = self.cube_api_token.strip() if self.cube_api_token else None
        return SemanticConfig(
            provider=self.semantic_provider,
            wren_project_path=project_path,
            wren_executable=executable or None,
            timeout_seconds=self.wren_timeout_seconds,
            cube_base_url=self.cube_base_url.strip().rstrip("/"),
            cube_api_token=token or None,
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
        vector_index_path = (
            Path(self.wiki_vector_index_path).expanduser().resolve()
            if self.wiki_vector_index_path
            else project_path / ".wiki-index" / "wiki.faiss"
        )
        embedding_api_key = (
            self.siliconflow_api_key.get_secret_value()
            if self.siliconflow_api_key
            else None
        )
        return WikiConfig(
            root_path=root_path,
            index_path=index_path,
            allowed_statuses=("reviewed", "evergreen"),
            max_results=self.wiki_max_results,
            retrieval_mode=self.wiki_retrieval_mode,
            vector_index_path=vector_index_path,
            embedding_api_key=embedding_api_key,
            embedding_base_url=str(self.siliconflow_base_url).rstrip("/"),
            embedding_model=self.wiki_embedding_model,
            reranker_model=self.wiki_reranker_model,
            vector_candidates=self.wiki_vector_candidates,
            rerank_candidates=self.wiki_rerank_candidates,
            api_timeout_seconds=self.wiki_api_timeout_seconds,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

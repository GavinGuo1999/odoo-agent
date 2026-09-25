from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.bi.cube_client import (
    CubeClientError,
    compile_cube_sql,
    describe_cube_meta,
    fetch_cube_meta,
)
from app.bi.semantic import SalesSemanticLayer, SemanticContext
from app.config import SemanticConfig


class SemanticProviderError(RuntimeError):
    """Raised when the selected semantic compiler cannot prepare or plan SQL."""


class SemanticContextProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def table_columns(self) -> dict[str, list[str]]: ...

    def metric_explanation(self, question: str) -> str | None: ...

    async def retrieve(
        self,
        question: str,
        *,
        company_id: int,
        discovered_columns: dict[str, list[dict[str, str]]] | None = None,
    ) -> SemanticContext: ...

    async def plan_sql(self, sql: str) -> str: ...


class NativeSemanticProvider:
    def __init__(self, semantics: SalesSemanticLayer) -> None:
        self._semantics = semantics

    @property
    def name(self) -> str:
        return "native"

    @property
    def version(self) -> str:
        return self._semantics.version

    @property
    def table_columns(self) -> dict[str, list[str]]:
        return self._semantics.table_columns

    def metric_explanation(self, question: str) -> str | None:
        return self._semantics.metric_explanation(question)

    async def retrieve(
        self,
        question: str,
        *,
        company_id: int,
        discovered_columns: dict[str, list[dict[str, str]]] | None = None,
    ) -> SemanticContext:
        return self._semantics.retrieve(
            question,
            company_id=company_id,
            discovered_columns=discovered_columns,
        )

    async def plan_sql(self, sql: str) -> str:
        return sql.strip()


def _resolve_wren_executable(configured: str | None) -> str:
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise SemanticProviderError(f"找不到 Wren 可执行文件：{configured}")

    local_name = "wren.exe" if os.name == "nt" else "wren"
    local = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin") / local_name
    if local.exists():
        return str(local)
    discovered = shutil.which("wren")
    if discovered:
        return discovered
    raise SemanticProviderError("当前 Python 环境未安装 Wren CLI。")


def _run_wren(
    executable: str,
    project_path: str,
    timeout_seconds: float,
    *args: str,
) -> str:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["NO_COLOR"] = "1"
    try:
        completed = subprocess.run(
            [executable, *args],
            cwd=project_path,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise SemanticProviderError(f"Wren 执行失败：{detail[:500]}") from exc
    return completed.stdout.strip()


@lru_cache(maxsize=4)
def _prepare_wren_context(
    executable: str,
    project_path: str,
    timeout_seconds: float,
) -> tuple[str, str, str]:
    project = Path(project_path)
    if not (project / "wren_project.yml").exists():
        raise SemanticProviderError(f"Wren 项目不存在：{project}")

    _run_wren(
        executable,
        project_path,
        timeout_seconds,
        "context",
        "build",
        "--path",
        project_path,
    )
    mdl_path = project / "target" / "mdl.json"
    schema = _run_wren(
        executable,
        project_path,
        timeout_seconds,
        "memory",
        "describe",
        "--mdl",
        str(mdl_path),
    )
    rules = _run_wren(
        executable,
        project_path,
        timeout_seconds,
        "context",
        "instructions",
        "--path",
        project_path,
    )
    return str(mdl_path), schema, rules


class WrenSemanticProvider:
    """Use Wren as a deterministic MDL context and SQL compilation layer."""

    def __init__(
        self,
        semantics: SalesSemanticLayer,
        config: SemanticConfig,
        *,
        executable: str | None = None,
    ) -> None:
        self._semantics = semantics
        self._config = config
        self._executable = executable or _resolve_wren_executable(config.wren_executable)

    @property
    def name(self) -> str:
        return "wren"

    @property
    def version(self) -> str:
        return f"wren-mdl:{self._semantics.version}"

    @property
    def table_columns(self) -> dict[str, list[str]]:
        return self._semantics.table_columns

    def metric_explanation(self, question: str) -> str | None:
        return self._semantics.metric_explanation(question)

    async def _context(self) -> tuple[str, str, str]:
        return await asyncio.to_thread(
            _prepare_wren_context,
            self._executable,
            str(self._config.wren_project_path),
            self._config.timeout_seconds,
        )

    async def retrieve(
        self,
        question: str,
        *,
        company_id: int,
        discovered_columns: dict[str, list[dict[str, str]]] | None = None,
    ) -> SemanticContext:
        native = self._semantics.retrieve(
            question,
            company_id=company_id,
            discovered_columns=discovered_columns,
        )
        _, schema, rules = await self._context()
        return replace(
            native,
            version=self.version,
            provider="wren",
            compiled_schema=schema,
            business_rules=rules,
        )

    async def plan_sql(self, sql: str) -> str:
        mdl_path, _, _ = await self._context()
        return await asyncio.to_thread(
            _run_wren,
            self._executable,
            str(self._config.wren_project_path),
            self._config.timeout_seconds,
            "dry-plan",
            "--sql",
            sql.strip().rstrip(";"),
            "--datasource",
            "postgres",
            "--mdl",
            mdl_path,
        )


@lru_cache(maxsize=4)
def _prepare_cube_context(
    base_url: str,
    timeout_seconds: float,
    api_token: str | None,
) -> str:
    """取一次 /v1/meta 并整理成给模型看的 schema 说明。

    与 Wren 那份 `_prepare_wren_context` 同构：模型定义在进程外，启动后不变，
    所以缓存住，避免每次问答都打一次 Cube。
    """
    try:
        meta = fetch_cube_meta(base_url, timeout_seconds=timeout_seconds, api_token=api_token)
        return describe_cube_meta(meta)
    except CubeClientError as exc:
        raise SemanticProviderError(f"无法准备 Cube 语义上下文：{exc}") from exc


class CubeSemanticProvider:
    """Use Cube as a semantic layer: /v1/meta for context, /v1/sql for compilation.

    与 Wren 的形态刻意保持一致——模型写一段针对语义层的 SQL，语义层把它编译成
    物理 SQL，再由项目自己的守卫校验、自己的只读连接执行。三方对照因此只有
    "中间那层语义层"这一个变量。

    与 Wren 的唯一实质差异：Cube 返回参数化语句，所以 compile_cube_sql 内部
    会把参数回填成字面值。这不是绕过守卫——守卫本来就会改写 SQL 并返回规范化
    结果交给执行层，参数化形式到不了执行那一步。
    """

    def __init__(self, semantics: SalesSemanticLayer, config: SemanticConfig) -> None:
        self._semantics = semantics
        self._config = config

    @property
    def name(self) -> str:
        return "cube"

    @property
    def version(self) -> str:
        return f"cube-model:{self._semantics.version}"

    @property
    def table_columns(self) -> dict[str, list[str]]:
        return self._semantics.table_columns

    def metric_explanation(self, question: str) -> str | None:
        return self._semantics.metric_explanation(question)

    async def _schema(self) -> str:
        return await asyncio.to_thread(
            _prepare_cube_context,
            self._config.cube_base_url,
            self._config.timeout_seconds,
            self._config.cube_api_token,
        )

    async def retrieve(
        self,
        question: str,
        *,
        company_id: int,
        discovered_columns: dict[str, list[dict[str, str]]] | None = None,
    ) -> SemanticContext:
        native = self._semantics.retrieve(
            question,
            company_id=company_id,
            discovered_columns=discovered_columns,
        )
        schema = await self._schema()
        return replace(
            native,
            version=self.version,
            provider="cube",
            compiled_schema=schema,
        )

    async def plan_sql(self, sql: str) -> str:
        try:
            return await asyncio.to_thread(
                compile_cube_sql,
                self._config.cube_base_url,
                sql,
                timeout_seconds=self._config.timeout_seconds,
                api_token=self._config.cube_api_token,
            )
        except CubeClientError as exc:
            raise SemanticProviderError(f"Cube 编译失败：{exc}") from exc


def build_semantic_provider(
    config: SemanticConfig | None,
    *,
    semantics: SalesSemanticLayer | None = None,
) -> SemanticContextProvider:
    native = semantics or SalesSemanticLayer.load()
    if config is None or config.provider == "native":
        return NativeSemanticProvider(native)
    if config.provider == "cube":
        return CubeSemanticProvider(native, config)
    return WrenSemanticProvider(native, config)

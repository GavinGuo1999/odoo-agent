from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.services.eval_reports import EvalReportService


router = APIRouter(prefix="/quality", tags=["quality"])

_PROJECT_DIRECTORY = Path(__file__).resolve().parents[4]


@router.get("/summary")
async def quality_summary() -> dict[str, Any]:
    """只读汇总本地评测归档，供“评测与质量”页展示。

    没有数据也返回 200 —— `evals/reports/` 是 gitignore 的，"还没跑过评测" 是
    正常状态而不是错误，页面据 `available` 自行说明。
    """

    service = EvalReportService(_PROJECT_DIRECTORY / "evals" / "reports")
    return service.summary()

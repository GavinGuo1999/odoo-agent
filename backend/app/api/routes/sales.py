from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.bi import SalesSemanticLayer
from app.config import Settings, get_settings
from app.schemas.sales import (
    DashboardPeriod,
    MetricDefinitionResponse,
    SalesDashboardResponse,
    SalesMetricsResponse,
)
from app.services.sales_dashboard import SalesDashboardService


router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/dashboard", response_model=SalesDashboardResponse)
async def sales_dashboard(
    period: DashboardPeriod = Query(default="month"),
    salesperson_id: int | None = Query(default=None, ge=1),
    settings: Settings = Depends(get_settings),
) -> SalesDashboardResponse:
    return await SalesDashboardService(settings.database()).build(
        period=period,
        salesperson_id=salesperson_id,
    )


@router.get("/metrics", response_model=SalesMetricsResponse)
async def sales_metrics() -> SalesMetricsResponse:
    semantics = SalesSemanticLayer.load()
    return SalesMetricsResponse(
        version=semantics.version,
        metrics=[
            MetricDefinitionResponse(
                id=metric_id,
                name=str(metric["name"]),
                description=str(metric["description"]),
                expression=str(metric["expression"]),
                states=list(metric["states"]),
                date_field=str(metric["date_field"]),
            )
            for metric_id, metric in semantics.metric_definitions.items()
        ],
    )

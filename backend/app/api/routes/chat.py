from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.bi import SalesAgent
from app.config import Settings, get_settings
from app.llm import ProviderNotConfiguredError
from app.observability import get_current_trace_id, trace_chat_turn, update_observation
from app.schemas import ChartSpec, ChatRequest, ChatResponse, TokenUsage


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    provider = settings.provider(payload.provider)
    if not provider.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider.name} API key is not configured.",
        )

    session_id = payload.session_id or uuid4().hex
    agent = SalesAgent(provider, settings.database())

    with trace_chat_turn(
        session_id=session_id,
        question=payload.question,
        provider=provider.name,
    ) as turn:
        try:
            outcome = await agent.run(
                question=payload.question,
                history=[message.model_dump() for message in payload.history],
            )
        except ProviderNotConfiguredError as exc:
            update_observation(
                turn,
                output={"status": "error", "stage": "model-call", "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            update_observation(
                turn,
                output={"status": "error", "stage": "agent-run", "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Agent request failed: {type(exc).__name__}",
            ) from exc

        response = ChatResponse(
            answer=outcome.answer,
            session_id=session_id,
            provider=outcome.provider,
            model=outcome.model,
            usage=TokenUsage(
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                total_tokens=outcome.total_tokens,
            ),
            trace_id=get_current_trace_id(),
            data_accessed=outcome.data_accessed,
            phase=outcome.phase,
            intent=outcome.intent,
            sql=outcome.sql,
            columns=outcome.columns,
            rows=outcome.rows,
            chart=ChartSpec.model_validate(outcome.chart) if outcome.chart else None,
            metrics=outcome.metrics,
            query_ms=outcome.query_ms,
            truncated=outcome.truncated,
            warnings=outcome.warnings,
        )
        update_observation(
            turn,
            output={
                "status": "ok",
                "intent": response.intent,
                "provider": response.provider,
                "model": response.model,
                "data_accessed": response.data_accessed,
                "row_count": len(response.rows),
                "metrics": response.metrics,
                "warnings": response.warnings,
            },
        )
        return response

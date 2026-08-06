from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.bi import AgentOutcome, SalesAgent
from app.bi.presentation import presentation_metadata
from app.config import ModelRoutingConfig, Settings, get_settings
from app.llm import ProviderNotConfiguredError
from app.observability import (
    get_current_trace_id,
    get_current_trace_url,
    record_user_feedback,
    trace_chat_turn,
    update_observation,
)
from app.schemas import (
    ChartSpec,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatRequest,
    ChatResponse,
    ChatResumeRequest,
    ChatSessionView,
    InterruptInfo,
    TokenUsage,
)
from app.state import get_state_store


router = APIRouter(tags=["chat"])


def _routing_or_503(
    settings: Settings,
    override_provider: str | None,
) -> ModelRoutingConfig:
    routing = settings.routing(override_provider)  # type: ignore[arg-type]
    for role in ("sql", "answer", "general"):
        provider = routing.for_role(role)  # type: ignore[arg-type]
        if not provider.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{provider.name} API key is not configured.",
            )
    return routing


def _agent(settings: Settings, routing: ModelRoutingConfig) -> SalesAgent:
    return SalesAgent(
        routing.general,
        settings.database(),
        routing=routing,
        checkpointer=get_state_store().checkpointer,
    )


def _response_from_outcome(
    *,
    outcome: AgentOutcome,
    session_id: str,
    trace_id: str | None,
    trace_url: str | None,
) -> ChatResponse:
    column_labels, column_formats, metric_labels = presentation_metadata(
        outcome.columns,
        outcome.metrics,
    )
    return ChatResponse(
        answer=outcome.answer,
        session_id=session_id,
        provider=outcome.provider,
        model=outcome.model,
        usage=TokenUsage(
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            total_tokens=outcome.total_tokens,
            estimated_cost_usd=outcome.estimated_cost_usd,
        ),
        trace_id=trace_id,
        trace_url=trace_url,
        status="interrupted" if outcome.interrupted else "completed",
        interrupt=(
            InterruptInfo.model_validate(outcome.interrupt_payload)
            if outcome.interrupt_payload
            else None
        ),
        data_accessed=outcome.data_accessed,
        phase=outcome.phase,
        intent=outcome.intent,
        sql=outcome.sql,
        columns=outcome.columns,
        rows=outcome.rows,
        chart=ChartSpec.model_validate(outcome.chart) if outcome.chart else None,
        metrics=outcome.metrics,
        currency=outcome.currency,
        column_labels=column_labels,
        column_formats=column_formats,
        metric_labels=metric_labels,
        query_ms=outcome.query_ms,
        truncated=outcome.truncated,
        warnings=outcome.warnings,
        query_plan=outcome.query_plan,
        answer_mode=outcome.answer_mode,
        model_roles=outcome.model_roles,
    )


def _update_turn(turn: object | None, response: ChatResponse) -> None:
    update_observation(
        turn,
        output={
            "status": response.status,
            "answer": response.answer,
            "intent": response.intent,
            "provider": response.provider,
            "model": response.model,
            "model_roles": {
                role: execution.model for role, execution in response.model_roles.items()
            },
            "answer_mode": response.answer_mode,
            "data_accessed": response.data_accessed,
            "row_count": len(response.rows),
            "metrics": response.metrics,
            "warnings": response.warnings,
            "estimated_cost_usd": response.usage.estimated_cost_usd,
        },
    )


async def _run_turn(
    *,
    agent: SalesAgent,
    session_id: str,
    question: str,
    provider_name: str,
    history: list[dict[str, str]] | None = None,
    resume_answer: str | None = None,
) -> ChatResponse:
    with trace_chat_turn(
        session_id=session_id,
        question=question,
        provider=provider_name,
    ) as turn:
        try:
            if resume_answer is None:
                outcome = await agent.run(
                    question=question,
                    history=history or [],
                    session_id=session_id,
                )
            else:
                outcome = await agent.resume(session_id=session_id, answer=resume_answer)
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

        trace_id = get_current_trace_id()
        response = _response_from_outcome(
            outcome=outcome,
            session_id=session_id,
            trace_id=trace_id,
            trace_url=get_current_trace_url(trace_id),
        )
        _update_turn(turn, response)
        return response


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    routing = _routing_or_503(settings, payload.provider)
    session_id = payload.session_id or uuid4().hex
    return await _run_turn(
        agent=_agent(settings, routing),
        session_id=session_id,
        question=payload.question,
        provider_name=routing.general.name,
        history=[message.model_dump() for message in payload.history],
    )


@router.post("/chat/resume", response_model=ChatResponse)
async def resume_chat(
    payload: ChatResumeRequest,
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    routing = _routing_or_503(settings, payload.provider)
    return await _run_turn(
        agent=_agent(settings, routing),
        session_id=payload.session_id,
        question="继续已中断的查询",
        provider_name=routing.general.name,
        resume_answer=payload.answer,
    )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _streaming_response(
    *,
    agent: SalesAgent,
    session_id: str,
    provider_name: str,
    question: str | None,
    history: list[dict[str, str]] | None,
    resume_answer: str | None,
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        trace_question = question or "继续已中断的查询"
        with trace_chat_turn(
            session_id=session_id,
            question=trace_question,
            provider=provider_name,
        ) as turn:
            turn_trace_id = get_current_trace_id()
            turn_trace_url = get_current_trace_url(turn_trace_id)
            try:
                async for event in agent.stream(
                    question=question,
                    history=history,
                    session_id=session_id,
                    resume_answer=resume_answer,
                ):
                    if event.get("type") == "outcome":
                        outcome = event["outcome"]
                        response = _response_from_outcome(
                            outcome=outcome,
                            session_id=session_id,
                            trace_id=turn_trace_id,
                            trace_url=turn_trace_url,
                        )
                        _update_turn(turn, response)
                        yield _sse("result", response.model_dump(mode="json"))
                    else:
                        yield _sse("progress", event)
            except Exception as exc:
                update_observation(
                    turn,
                    output={"status": "error", "stage": "stream", "error_type": type(exc).__name__},
                )
                yield _sse(
                    "error",
                    {"detail": f"Agent request failed: {type(exc).__name__}"},
                )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/stream")
async def stream_chat(
    payload: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    routing = _routing_or_503(settings, payload.provider)
    session_id = payload.session_id or uuid4().hex
    return _streaming_response(
        agent=_agent(settings, routing),
        session_id=session_id,
        provider_name=routing.general.name,
        question=payload.question,
        history=[message.model_dump() for message in payload.history],
        resume_answer=None,
    )


@router.post("/chat/resume/stream")
async def stream_resume_chat(
    payload: ChatResumeRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    routing = _routing_or_503(settings, payload.provider)
    return _streaming_response(
        agent=_agent(settings, routing),
        session_id=payload.session_id,
        provider_name=routing.general.name,
        question=None,
        history=None,
        resume_answer=payload.answer,
    )


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionView)
async def read_chat_session(
    session_id: str,
    settings: Settings = Depends(get_settings),
) -> ChatSessionView:
    routing = settings.routing()
    agent = _agent(settings, routing)
    try:
        history, pending = await agent.session_state(session_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat session not found: {type(exc).__name__}",
        ) from exc
    return ChatSessionView(
        session_id=session_id,
        history=history,
        pending_interrupt=InterruptInfo.model_validate(pending) if pending else None,
        persistence_mode=get_state_store().mode,
    )


@router.post("/chat/feedback", response_model=ChatFeedbackResponse)
async def submit_chat_feedback(payload: ChatFeedbackRequest) -> ChatFeedbackResponse:
    try:
        recorded = await asyncio.to_thread(
            record_user_feedback,
            trace_id=payload.trace_id,
            positive=payload.positive,
            comment=payload.comment,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Langfuse feedback failed: {type(exc).__name__}",
        ) from exc
    if not recorded:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Langfuse is not configured.",
        )
    return ChatFeedbackResponse(recorded=True)

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.llm import LLMGateway, ProviderNotConfiguredError
from app.observability import trace_chat_turn, update_observation
from app.schemas import ChatRequest, ChatResponse, TokenUsage


router = APIRouter(tags=["chat"])

_SYSTEM_PROMPT = """你是 Odoo 销售数据分析助手。
当前系统只完成了模型连接，尚未连接 Odoo 数据库。
回答用户时必须明确说明没有查询真实业务数据，不得编造销售数字或订单情况。
用简洁中文回答。"""


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
    gateway = LLMGateway(provider)

    with trace_chat_turn(
        session_id=session_id,
        question=payload.question,
        provider=provider.name,
    ) as turn:
        try:
            result = await gateway.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": payload.question},
                ],
                generation_name="generate-model-response",
                metadata={
                    "feature": "model-connectivity",
                    "data_accessed": False,
                },
            )
        except ProviderNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Model request failed: {type(exc).__name__}",
            ) from exc

        response = ChatResponse(
            answer=result.content,
            session_id=session_id,
            provider=result.provider,
            model=result.model,
            usage=TokenUsage(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
            ),
        )
        update_observation(
            turn,
            output={
                "answer": response.answer,
                "provider": response.provider,
                "model": response.model,
                "data_accessed": response.data_accessed,
            },
        )
        return response


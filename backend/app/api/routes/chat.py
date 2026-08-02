from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.llm import LLMGateway, ProviderNotConfiguredError
from app.observability import (
    get_current_trace_id,
    trace_chat_turn,
    update_observation,
)
from app.schemas import ChatRequest, ChatResponse, TokenUsage


router = APIRouter(tags=["chat"])

def _system_prompt(*, provider_name: str, model: str) -> str:
    local_now = datetime.now().astimezone()
    return f"""你是 Odoo Agent，既是通用中文助手，也是 Odoo 业务数据分析助手。
当前服务器本地时间：{local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}。
当前模型服务商：{provider_name}；配置的模型：{model}。

工作规则：
1. 普通问题（日期、模型、概念解释、方案讨论等）直接正常回答，不要强行生成 BI、SQL 或图表，也不要无关地提及数据库状态。
2. 只有当用户询问 Odoo 业务数据时，才进入数据分析语境。当前尚未连接 Odoo 数据库，必须明确说明没有查询真实数据，绝不能编造销售额、订单、客户或产品数字。
3. 当前不能声称已经执行 SQL、完成查询或生成了真实业务结论，但可以帮助解释指标、设计分析方法和准备查询口径。
4. 默认用简洁、自然的中文回答。"""


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
                    {
                        "role": "system",
                        "content": _system_prompt(
                            provider_name=provider.name,
                            model=provider.model,
                        ),
                    },
                    *[
                        {"role": message.role, "content": message.content}
                        for message in payload.history
                    ],
                    {"role": "user", "content": payload.question},
                ],
                generation_name="generate-model-response",
                metadata={
                    "feature": "model-connectivity",
                    "data_accessed": False,
                },
            )
        except ProviderNotConfiguredError as exc:
            update_observation(
                turn,
                output={
                    "status": "error",
                    "stage": "model-call",
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            update_observation(
                turn,
                output={
                    "status": "error",
                    "stage": "model-call",
                    "error_type": type(exc).__name__,
                },
            )
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
            trace_id=get_current_trace_id(),
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

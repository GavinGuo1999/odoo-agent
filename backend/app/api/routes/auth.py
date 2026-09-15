"""登录与会话校验。本路由自身必须公开，其余所有 API 都挂 `require_session`。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.security.session_gate import SessionGate, get_gate, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _client(request: Request) -> str:
    """取真实来源 IP 用于失败计数。

    这个服务只在自己的 Nginx 后面跑，`X-Forwarded-For` 由它设置，取最左一段。
    如果哪天直接暴露，这个头就是可伪造的——那时候锁定会失效，但锁定本来就只是
    减速带，真正的边界是 Nginx 的 IP 白名单。
    """

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def require_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    gate: SessionGate = Depends(get_gate),
) -> None:
    """未配置口令时直接放行；配置了就必须持有有效 token。"""

    if not settings.ui_password_hash:
        return
    if gate.validate(bearer_token(request)):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="需要登录",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/session")
async def read_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    gate: SessionGate = Depends(get_gate),
) -> dict[str, object]:
    """前端启动时先问这里，决定要不要弹登录框。"""

    if not settings.ui_password_hash:
        return {"required": False, "authenticated": True}
    if gate.validate(bearer_token(request)):
        return {"required": True, "authenticated": True}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    gate: SessionGate = Depends(get_gate),
) -> dict[str, object]:
    if not settings.ui_password_hash:
        return {"required": False, "token": None, "expires_in": None}

    client = _client(request)
    remaining = gate.lock_remaining(client)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"尝试过于频繁，请 {int(remaining) + 1} 秒后再试",
        )

    if not verify_password(payload.password, settings.ui_password_hash):
        gate.register_failure(client)
        # 不区分"口令错"和"没配置口令"，避免把服务端状态透露出去。
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="口令不正确")

    gate.ttl_seconds = float(settings.ui_session_ttl_seconds)
    token, ttl = gate.issue(client)
    return {"required": True, "token": token, "expires_in": int(ttl)}


@router.post("/logout")
async def logout(
    request: Request,
    gate: SessionGate = Depends(get_gate),
) -> dict[str, bool]:
    gate.revoke(bearer_token(request))
    return {"ok": True}

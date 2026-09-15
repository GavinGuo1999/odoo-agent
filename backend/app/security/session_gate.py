"""演示用的单口令门禁：挡住整个 API，而不只是挡住界面。

为什么要挡 API 而不是只在前端加个登录框：前端的登录框只是不显示内容，任何人
`POST /api/chat/stream` 依然能触发模型调用、烧掉 API Key。所以校验必须在服务端，
且默认对**所有**路由生效，只放行 `/api/auth/*` 本身和存活探针。

口令不以明文形式存在于任何文件里。`.env` 里放的是 PBKDF2 派生值，形如
`pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`，用 `python -m
app.security.session_gate` 生成。

Token 只存在进程内存里：重启即全部失效，这对"给别人演示一下"的场景是合适的，
也省掉了一套持久化和撤销逻辑。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from threading import Lock


_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16

# 单口令 + 公网可达 = 必须防爆破。锁定是按来源 IP 的，因为口令只有一个，
# 按账号锁没有意义。
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 300.0


def hash_password(password: str) -> str:
    """生成可以安全写进 .env 的派生值。"""

    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "$".join([
        "",
        _ALGORITHM,
        str(_ITERATIONS),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    ])


def verify_password(password: str, encoded: str) -> bool:
    """常数时间比对。格式不认识一律返回 False，绝不"宽容降级"放行。"""

    try:
        _, algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        rounds = int(iterations)
    except (ValueError, TypeError):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(derived, expected)


@dataclass
class _Failures:
    count: int = 0
    locked_until: float = 0.0


@dataclass
class SessionGate:
    """进程内的 token 与失败计数。所有方法都是线程安全的。"""

    ttl_seconds: float = 12 * 3600.0
    _tokens: dict[str, float] = field(default_factory=dict)
    _failures: dict[str, _Failures] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def _now(self) -> float:
        return time.monotonic()

    def _sweep(self, now: float) -> None:
        expired = [token for token, deadline in self._tokens.items() if deadline <= now]
        for token in expired:
            self._tokens.pop(token, None)

    def lock_remaining(self, client: str) -> float:
        with self._lock:
            record = self._failures.get(client)
            if not record:
                return 0.0
            return max(0.0, record.locked_until - self._now())

    def register_failure(self, client: str) -> None:
        with self._lock:
            record = self._failures.setdefault(client, _Failures())
            record.count += 1
            if record.count >= _MAX_FAILURES:
                record.locked_until = self._now() + _LOCKOUT_SECONDS
                record.count = 0

    def issue(self, client: str) -> tuple[str, float]:
        with self._lock:
            self._failures.pop(client, None)
            now = self._now()
            self._sweep(now)
            token = secrets.token_urlsafe(32)
            self._tokens[token] = now + self.ttl_seconds
            return token, self.ttl_seconds

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            now = self._now()
            self._sweep(now)
            deadline = self._tokens.get(token)
            if deadline is None:
                return False
            # 演示中途被踢出去很扫兴，所以每次访问顺延有效期。
            self._tokens[token] = now + self.ttl_seconds
            return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._failures.clear()


_gate = SessionGate()


def get_gate() -> SessionGate:
    return _gate


if __name__ == "__main__":  # pragma: no cover - 生成哈希的小工具
    import getpass
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    first = getpass.getpass("口令: ")
    if first != getpass.getpass("再输一次: "):
        raise SystemExit("两次输入不一致。")
    if not first:
        raise SystemExit("口令不能为空。")
    print("\n把下面这一行加到服务端 .env（该文件必须是 0600，且不得提交）：\n")
    print(f"AGENT_UI_PASSWORD_HASH={hash_password(first)}")

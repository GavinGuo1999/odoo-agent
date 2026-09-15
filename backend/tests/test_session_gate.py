"""演示门禁：口令派生、防爆破、以及"默认所有路由都受保护"这条约束。

ProtectedSurfaceTests 是这里最重要的一组：它遍历整张路由表，确保除了
`/api/auth/*` 之外每条接口都挂着 require_session。以后新加路由忘了挂保护，
不需要有人记得去补探针，这个测试自己会红。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.security.session_gate import SessionGate, hash_password, verify_password


class PasswordHashTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        encoded = hash_password("Correct Horse Battery")
        self.assertTrue(verify_password("Correct Horse Battery", encoded))
        self.assertFalse(verify_password("correct horse battery", encoded))

    def test_hash_is_salted(self) -> None:
        # 同一口令两次派生必须不同，否则相同口令可以互相比对。
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_plaintext_never_appears_in_the_encoded_value(self) -> None:
        self.assertNotIn("Gzb-example-secret", hash_password("Gzb-example-secret"))

    def test_malformed_encoding_is_rejected_not_tolerated(self) -> None:
        # "格式不对就放行"是最常见的鉴权漏洞，这里必须一律 False。
        for broken in ("", "garbage", "$md5$1$aa$bb", "$pbkdf2_sha256$notanint$aa$bb"):
            with self.subTest(broken=broken):
                self.assertFalse(verify_password("anything", broken))


class SessionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = SessionGate(ttl_seconds=1000.0)

    def test_issued_token_validates_and_revokes(self) -> None:
        token, ttl = self.gate.issue("1.2.3.4")
        self.assertEqual(ttl, 1000.0)
        self.assertTrue(self.gate.validate(token))
        self.gate.revoke(token)
        self.assertFalse(self.gate.validate(token))

    def test_unknown_and_empty_tokens_are_rejected(self) -> None:
        self.assertFalse(self.gate.validate(None))
        self.assertFalse(self.gate.validate(""))
        self.assertFalse(self.gate.validate("made-up"))

    def test_expired_token_stops_validating(self) -> None:
        gate = SessionGate(ttl_seconds=10.0)
        with patch.object(SessionGate, "_now", return_value=0.0):
            token, _ = gate.issue("1.2.3.4")
        with patch.object(SessionGate, "_now", return_value=11.0):
            self.assertFalse(gate.validate(token))

    def test_repeated_failures_lock_the_client_out(self) -> None:
        client = "9.9.9.9"
        self.assertEqual(self.gate.lock_remaining(client), 0.0)
        for _ in range(5):
            self.gate.register_failure(client)
        self.assertGreater(self.gate.lock_remaining(client), 0.0)
        # 锁定是按来源分的，不能把别人一起锁掉。
        self.assertEqual(self.gate.lock_remaining("8.8.8.8"), 0.0)

    def test_successful_login_clears_the_failure_counter(self) -> None:
        client = "9.9.9.9"
        for _ in range(3):
            self.gate.register_failure(client)
        self.gate.issue(client)
        for _ in range(4):
            self.gate.register_failure(client)
        # 计数已被清零，4 次还不到 5 次上限。
        self.assertEqual(self.gate.lock_remaining(client), 0.0)


class ProtectedSurfaceTests(unittest.TestCase):
    """整个 API 面必须默认受保护。"""

    def test_every_non_auth_route_carries_the_session_dependency(self) -> None:
        """静态检查：新增路由如果没挂门禁，这里会红。

        比"逐个发请求"更严：不依赖测试作者记得去补探针，整张路由表都在检查范围内。
        """

        from fastapi.routing import APIRoute

        from app.api.routes.auth import require_session
        from app.main import create_app

        unprotected: list[str] = []
        for route in create_app().routes:
            if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
                continue
            if route.path.startswith("/api/auth/"):
                continue
            calls = {
                dependency.call
                for dependency in route.dependant.dependencies
            }
            if require_session not in calls:
                unprotected.append(f"{sorted(route.methods)} {route.path}")

        self.assertEqual(
            unprotected,
            [],
            "这些路由可以匿名访问，请挂到 router.py 的 protected 上：" + str(unprotected),
        )

    def test_anonymous_requests_are_rejected_on_every_non_auth_route(self) -> None:
        import os

        from app.config import get_settings

        previous = os.environ.get("AGENT_UI_PASSWORD_HASH")
        os.environ["AGENT_UI_PASSWORD_HASH"] = hash_password("demo-only")
        get_settings.cache_clear()
        try:
            from fastapi.testclient import TestClient

            from app.main import create_app

            with TestClient(create_app()) as client:
                # 只挑几条代表性的：烧钱的、泄露配置的、以及一条只读的。
                probes = [
                    ("post", "/api/chat/stream", {"question": "x"}),
                    ("get", "/api/health", None),
                    ("get", "/api/quality/summary", None),
                ]
                for method, path, body in probes:
                    with self.subTest(path=path):
                        call = getattr(client, method)
                        response = call(path, json=body) if body else call(path)
                        self.assertEqual(response.status_code, 401, path)

                # 登录接口本身必须公开，否则没人进得来。
                self.assertEqual(
                    client.post("/api/auth/login", json={"password": "wrong"}).status_code,
                    401,
                )
                token = client.post(
                    "/api/auth/login", json={"password": "demo-only"}
                ).json()["token"]
                headers = {"Authorization": f"Bearer {token}"}
                self.assertEqual(client.get("/api/health", headers=headers).status_code, 200)
        finally:
            if previous is None:
                os.environ.pop("AGENT_UI_PASSWORD_HASH", None)
            else:
                os.environ["AGENT_UI_PASSWORD_HASH"] = previous
            get_settings.cache_clear()

    def test_gate_is_disabled_when_no_password_is_configured(self) -> None:
        import os

        from app.config import get_settings

        previous = os.environ.pop("AGENT_UI_PASSWORD_HASH", None)
        get_settings.cache_clear()
        try:
            from fastapi.testclient import TestClient

            from app.main import create_app

            with TestClient(create_app()) as client:
                # 本机开发不该被门禁挡住。
                self.assertEqual(client.get("/api/quality/summary").status_code, 200)
                self.assertEqual(
                    client.get("/api/auth/session").json(),
                    {"required": False, "authenticated": True},
                )
        finally:
            if previous is not None:
                os.environ["AGENT_UI_PASSWORD_HASH"] = previous
            get_settings.cache_clear()


class AmbientIsolationTests(unittest.TestCase):
    """测试结果不能取决于开发者本机配了什么。

    实测缺陷：口令哈希存在用户环境变量里，新开的终端跑测试会红 17 个，
    而设置变量之前开的终端是绿的。
    """

    def test_gate_variables_are_stripped_from_the_test_environment(self) -> None:
        import os
        import sys
        from pathlib import Path

        tests_dir = str(Path(__file__).resolve().parent)
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from _isolation import AMBIENT_OVERRIDES, isolate_ambient_environment

        self.assertIn("AGENT_UI_PASSWORD_HASH", AMBIENT_OVERRIDES)

        with patch.dict(os.environ, {"AGENT_UI_PASSWORD_HASH": "whatever"}):
            self.assertEqual(os.environ["AGENT_UI_PASSWORD_HASH"], "whatever")
            patcher = isolate_ambient_environment()
            patcher.start()
            try:
                for name in AMBIENT_OVERRIDES:
                    self.assertNotIn(name, os.environ, name)
            finally:
                patcher.stop()
            # 剥离只在作用域内生效，不能污染调用方。
            self.assertEqual(os.environ["AGENT_UI_PASSWORD_HASH"], "whatever")


if __name__ == "__main__":
    unittest.main()

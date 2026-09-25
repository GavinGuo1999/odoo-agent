"""测试环境隔离：开发者本机的配置不能影响测试结果。

实测缺陷：演示门禁的口令哈希存在用户环境变量里，任何新开的进程都继承它，
于是测试匿名访问受保护路由全部返回 401，套件 17 个失败。当时本地是绿的，
只因为那个 shell 是在设置变量之前开的——换个终端或接 CI 就会红。

凡是"配了就会改变全局行为"的变量都必须列进 AMBIENT_OVERRIDES。需要这些行为的
测试（比如门禁自己的测试）应当在用例内显式设置，而不是依赖开发者机器的状态。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import get_settings  # noqa: E402


# 一旦在开发者机器上配置，就会悄悄改变被测行为的变量。
AMBIENT_OVERRIDES = (
    "AGENT_UI_PASSWORD_HASH",          # 配了就给所有 API 加门禁 → 匿名请求 401
    "AGENT_UI_SESSION_TTL_SECONDS",
    "LANGFUSE_PROMPTS_FETCH_ENABLED",  # 决定提示词取自注册表还是代码
)


class _AmbientPatcher:
    """剥掉环境变量，**并且**清掉已缓存的 Settings。

    只剥环境变量是不够的：`app/main.py` 有模块级 `app = create_app()`，所以
    `import app.main` 当场就会调用 `get_settings()` 并把结果缓存进 lru_cache。
    那次调用发生在测试收集阶段，早于任何 `setUpModule`，于是缓存里存的是
    **带着开发者本机口令**的配置，后面再怎么剥环境变量都没用。

    实测缺陷：`test_quality_api` 的隔离其实一直没生效，它能过只是因为
    `test_api`（字母序在前）在自己的用例里调了 `get_settings.cache_clear()`，
    顺带把缓存洗干净了。新增几个字母序在中间的测试模块就会把这个顺序依赖打破——
    单独跑 `test_quality_api` 一直是红的，只是没人单独跑过它。

    所以 start/stop 两端都清缓存：start 时清，让被测代码在干净环境下重新读；
    stop 时也清，避免把"干净"的配置泄漏给后面依赖真实环境的用例。
    """

    def __init__(self, patcher) -> None:
        self._patcher = patcher

    def start(self):
        result = self._patcher.start()
        get_settings.cache_clear()
        return result

    def stop(self) -> None:
        self._patcher.stop()
        get_settings.cache_clear()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()


def isolate_ambient_environment() -> _AmbientPatcher:
    """返回一个 patcher，剥掉上面那些变量并清掉配置缓存。

    用法（模块级，覆盖该文件里所有用例）：

        _ambient = isolate_ambient_environment()
        def setUpModule() -> None: _ambient.start()
        def tearDownModule() -> None: _ambient.stop()
    """

    cleaned = {
        key: value
        for key, value in os.environ.items()
        if key not in AMBIENT_OVERRIDES
    }
    return _AmbientPatcher(patch.dict(os.environ, cleaned, clear=True))

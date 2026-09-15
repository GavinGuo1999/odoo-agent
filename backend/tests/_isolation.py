"""测试环境隔离：开发者本机的配置不能影响测试结果。

实测缺陷：演示门禁的口令哈希存在用户环境变量里，任何新开的进程都继承它，
于是测试匿名访问受保护路由全部返回 401，套件 17 个失败。当时本地是绿的，
只因为那个 shell 是在设置变量之前开的——换个终端或接 CI 就会红。

凡是"配了就会改变全局行为"的变量都必须列进 AMBIENT_OVERRIDES。需要这些行为的
测试（比如门禁自己的测试）应当在用例内显式设置，而不是依赖开发者机器的状态。
"""

from __future__ import annotations

import os
from unittest.mock import patch


# 一旦在开发者机器上配置，就会悄悄改变被测行为的变量。
AMBIENT_OVERRIDES = (
    "AGENT_UI_PASSWORD_HASH",          # 配了就给所有 API 加门禁 → 匿名请求 401
    "AGENT_UI_SESSION_TTL_SECONDS",
    "LANGFUSE_PROMPTS_FETCH_ENABLED",  # 决定提示词取自注册表还是代码
)


def isolate_ambient_environment():
    """返回一个 patcher，剥掉上面那些变量。

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
    return patch.dict(os.environ, cleaned, clear=True)

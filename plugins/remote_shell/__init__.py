"""remote_shell：给 AI Agent 提供「群专属 Linux 账号」的远端终端能力。

加载本插件只会注册 AI 工具（terminal_exec / read / write / list / edit），
不会注册任何聊天命令。默认 `enabled: false`，需要在
`BotData/plugin_configs/remote_shell.json` 打开，并在后台 AI Agent 页
允许副作用工具、把对应工具名加入启用名单后，模型才会看到它们。
"""

from __future__ import annotations

import logging

from . import tools  # noqa: F401  # 导入即注册 AI 工具

logger = logging.getLogger("HikariBot.RemoteShell")

logger.info(
    "remote_shell 已加载：AI 终端工具 terminal_exec/read/write/list/edit"
    "（默认关闭，需在插件配置与 AI Agent 后台启用）"
)

try:  # pragma: no cover - 单元测试环境没有 NoneBot driver
    from nonebot import get_driver

    @get_driver().on_shutdown
    async def _close_remote_shell() -> None:
        await tools.close_service()

except Exception:  # noqa: BLE001 - 没有 driver 时忽略
    pass

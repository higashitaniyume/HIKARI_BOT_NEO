"""AstrBot 插件兼容层 — 让 AstrBot 插件在 HIKARI BOT NEO 上运行。

文件结构::

    plugins/astrbot_compat/
    ├── __init__.py              NoneBot 插件入口 + 启动自动加载
    ├── shim/                    astrbot API 胶水层 (加入 sys.path)
    │   └── astrbot/api/...
    ├── config.py                _conf_schema.json 解析
    ├── venv_manager.py          公共插件虚拟环境管理器
    ├── loader.py                动态加载 + handler 桥接
    └── manager.py               管理命令 + 生命周期管理

工作原理:
    1. 收到 /astrbot load <path> 命令
    2. 解压/校验插件目录
    3. 将 shim/ 加入 sys.path → 插件能 from astrbot.api.star import Star
    4. Star.__init_subclass__ 自动捕获插件类
    5. 扫描 @filter.command / @filter.regex / @filter.on_message 装饰器
    6. 注册到 core.command_router (优先级 0) 或 NoneBot matcher (优先级 2)
    7. yield event.plain_result(...) → 桥接为 bot.send()

支持的 API (v1):
    - Star 基类 + PluginKVStoreMixin (KV 存储)
    - Context (LLM 生成/tool_loop 桥接到 bot 内置 AI Agent)
    - @filter.command(name, alias, 参数自动解析)
    - @filter.regex(pattern, 匹配组注入)
    - @filter.command_group, @filter.permission, @filter.event_message_type
    - event.plain_result(), image_result(), chain_result()
    - 消息组件: Plain, Image, At, Reply, Record, Video, Share, Json ...
    - AstrBotConfig (字典式 JSON 配置)
    - _conf_schema.json → config.json
    - metadata.yaml 解析 (name/version/author/tags/repo)
    - requirements.txt → 公共 venv 自动装依赖
    - text_to_image() / html_render() → 委托 core.rendering

不支持的 (v1):
    - @register_platform_adapter
    - Plugin Pages (WebUI)
    - Context.get_db() (键值/向量存储)
    - 沙箱隔离
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from nonebot import get_driver

from core.config_loader import load_plugin_config
from plugins.astrbot_compat.constants import DEFAULT_CONFIG, PLUGINS_DIR

logger = logging.getLogger("AstrBotCompat")

# Ensure the shim package is importable before any loader code runs.
_shim_path = str((Path(__file__).resolve().parent / "shim").resolve())
if _shim_path not in sys.path:
    sys.path.insert(0, _shim_path)
    logger.debug("Shim path added to sys.path: %s", _shim_path)

# ---------------------------------------------------------------------------
# Entry point — loaded by NoneBot at startup
# ---------------------------------------------------------------------------

driver = get_driver()


@driver.on_startup
async def _on_startup() -> None:
    """Initialize the astrbot compat plugin at bot startup.

    - Create plugin directories
    - Ensure shared plugin venv exists
    - Optionally auto-load discovered plugins
    """
    from plugins.astrbot_compat.manager import ensure_plugin_dirs, auto_load_plugins
    from plugins.astrbot_compat.venv_manager import PluginVenvManager
    from plugins.astrbot_compat.conversion import clean_stale_temp_files

    config = load_plugin_config("astrbot_compat", DEFAULT_CONFIG)
    auto_load = config.get("auto_load", True)
    logger.info(
        "AstrBot compat initializing — auto_load=%s plugins_dir=%s",
        auto_load,
        PLUGINS_DIR,
    )

    # Ensure plugin storage directory exists
    ensure_plugin_dirs()
    clean_stale_temp_files()
    logger.debug("Plugin storage directory ready: %s", PLUGINS_DIR)

    # Ensure shared venv for plugin dependencies
    venv_mgr = PluginVenvManager(PLUGINS_DIR / ".venv")
    try:
        venv_mgr.ensure_venv()
        venv_mgr.add_to_path()
        logger.debug("Shared plugin venv ready at %s", venv_mgr.venv_dir)
    except RuntimeError as e:
        logger.error("Shared plugin venv setup failed: %s", e)

    # Auto-load plugins from disk
    if auto_load:
        count = await auto_load_plugins()
        logger.info(
            "AstrBot compat startup complete — %d plugin(s) auto-loaded",
            count,
        )
    else:
        logger.info("AstrBot compat startup complete — auto_load disabled")

    logger.debug("AstrBot compat fully initialized")

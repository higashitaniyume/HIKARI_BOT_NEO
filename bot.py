"""
HIKARI_BOT_NEO — NoneBot QQ 机器人入口。

启动方式：uv run nb run

职责：
1. 加载 BotData/config.json
2. 初始化目录
3. 初始化日志
4. 以程序方式配置 NapCat WebSocket 连接
5. 启动 NoneBot

本项目严禁使用 .env / dotenv 文件。
所有配置从 JSON 文件读取。
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 上，方便绝对导入
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ---- Step 1: 加载主配置 ----
from core.config_loader import load_main_config, init_directories

_config = load_main_config()

# ---- Step 2: 初始化目录 ----
init_directories(_config)

# ---- Step 3: 初始化日志 ----
from core.logger_setup import setup_logging

setup_logging(_config)

# ---- Step 4: 初始化 NoneBot ----
import nonebot

# 从配置中提取 NapCat 连接参数
napcat_cfg = _config.get("napcat", {})
ws_url = napcat_cfg.get("ws_url", "ws://192.168.31.2:54253/")
token = napcat_cfg.get("token", "")

# 从配置中提取机器人参数
bot_cfg = _config.get("bot", {})
superuser_id = bot_cfg.get("superuser_id", "3433559280")
log_level = bot_cfg.get("log_level", "INFO")

logger = nonebot.logger  # NoneBot 初始化前的临时 logger

logger.info("========================================")
logger.info(f"  {bot_cfg.get('name', 'HikariBotNeo')} 启动中...")
logger.info("========================================")

nonebot.init(
    # 驱动：使用 websockets 驱动（Bot 主动连接 NapCat WebSocket）
    driver="~websockets",

    # OneBot V11 适配器配置
    # Bot 主动连接 NapCat 的 WebSocket 地址
    onebot_ws_urls={ws_url},
    onebot_access_token=token,

    # 超级用户
    superusers={superuser_id},

    # 日志级别
    log_level=log_level,

    # 机器人名称
    nickname=[bot_cfg.get("name", "HikariBotNeo")],

    # API 调用超时时间 (解决大文件或合并转发超时问题)
    api_timeout=bot_cfg.get("api_timeout", 120),
)

logger.info(f"NoneBot 初始化完成")
logger.info(f"  NapCat WebSocket: {ws_url}")
logger.info(f"  SuperUser: {superuser_id}")
logger.info(f"  Log Level: {log_level}")

# ---- Step 5: 注册适配器 ----

# 导入 OneBot V11 Adapter 类
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 注册适配器实例（这会触发 Adapter.__init__ → _setup → _start_forward → 连接 NapCat）
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
logger.info("OneBot V11 适配器已注册，将在 Driver 就绪后连接 NapCat")

# ---- Step 6: 加载插件 ----

# 加载消息处理管道（必须在加载其他插件之前）
from core.message_pipeline import msg_pipeline  # noqa: F401 — 触发 on_message 注册

# 加载 plugins 目录下的所有插件
# 这将自动发现并加载 plugins/pixiv_parser/ 等
nonebot.load_plugins("plugins")

# ---- Step 6: 启动 ----

# nb run 会自动调用 nonebot.run()，这里不需要显式调用
# 但如果直接 python bot.py 运行，则需要：
if __name__ == "__main__":
    nonebot.run()

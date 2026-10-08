"""吉星派对观战插件配置。

配置文件：BotData/plugin_configs/astral_party_spectator.json
"""

from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.AstralPartySpectatorConfig")

DEFAULT_ASTRAL_PARTY_CONFIG: dict[str, Any] = {
    "enabled": True,
    # AstralPartySpectator 的 API 地址，默认指向局域网内的观战服务。
    "endpoint": "http://192.168.31.2:18743",
    # 单次观战查询需要登录官方服务器并等待房间快照，耗时明显高于普通 HTTP 请求。
    "timeout": 90,
    "verify_ssl": True,
    "proxy": "",
    "user_agent": "HikariBot/1.0 AstralPartySpectatorQuery",
    # 玩家昵称、卡牌名称、卡牌描述写进一条文本消息前的长度上限。
    "max_name_chars": 40,
    "max_description_chars": 60,
    # 整条回复的最大字符数；超出时优先裁掉卡牌描述，再裁掉多余的卡牌。
    "max_chars": 3000,
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("astral_party_spectator", DEFAULT_ASTRAL_PARTY_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "吉星派对观战配置加载完成 -> enabled=%s, endpoint=%s, timeout=%s, max_chars=%s",
            cfg.get("enabled"),
            cfg.get("endpoint"),
            cfg.get("timeout"),
            cfg.get("max_chars"),
        )
    return cfg

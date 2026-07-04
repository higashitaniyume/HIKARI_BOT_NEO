from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.Sts2WikiConfig")

DEFAULT_STS2_WIKI_CONFIG: dict[str, Any] = {
    "enabled": True,
    "source": "spire_codex",
    "api_url": "https://spire-codex.com/api",
    "site_url": "https://spire-codex.com",
    "language": "zhs",
    "version": "",
    "cache_ttl_seconds": 86400,
    "timeout": 10,
    "search_limit": 5,
    "summary_max_chars": 300,
    "query_max_chars": 80,
    "max_cache_entries": 500,
    "proxy": "",
    "user_agent": "HikariBot/1.0 SlayTheSpire2WikiQuery",
    "search_categories": [
        "cards",
        "characters",
        "relics",
        "potions",
        "powers",
        "keywords",
        "monsters",
        "events",
    ],
    "query_aliases": {
        "Ironclad": "铁甲战士",
        "Strike": "打击",
        "Perfected Strike": "完美打击",
        "铁甲": "铁甲战士",
        "战士": "铁甲战士",
        "机器人": "故障机器人",
    },
}

_LEGACY_WIKIGG_API_URL = "https://slaythespire.wiki.gg/api.php"
_LEGACY_MEDIAWIKI_ALIASES = {
    "铁甲战士": "Ironclad",
    "铁甲": "Ironclad",
    "战士": "Ironclad",
    "静默猎手": "Silent",
    "猎手": "Silent",
    "故障机器人": "Defect",
    "机器人": "Defect",
    "观者": "Watcher",
    "打击": "Strike",
    "防御": "Defend",
    "完美打击": "Perfected Strike",
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("sts2_wiki", DEFAULT_STS2_WIKI_CONFIG)
    cfg = _migrate_config(cfg)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "杀戮尖塔 2 Wiki 配置加载完成 -> enabled=%s, source=%s, api_url=%s, language=%s, version=%s, cache_ttl_seconds=%s",
            cfg.get("enabled"),
            cfg.get("source"),
            cfg.get("api_url"),
            cfg.get("language"),
            cfg.get("version") or "stable",
            cfg.get("cache_ttl_seconds"),
        )
    return cfg


def _migrate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    source = str(cfg.get("source") or "").strip().casefold()
    if source not in {"spire_codex", "spire-codex", "spirecodex"}:
        return cfg

    if str(cfg.get("api_url") or "").strip().rstrip("/") == _LEGACY_WIKIGG_API_URL:
        cfg["api_url"] = DEFAULT_STS2_WIKI_CONFIG["api_url"]

    aliases = cfg.get("query_aliases") if isinstance(cfg.get("query_aliases"), dict) else {}
    migrated_aliases = dict(DEFAULT_STS2_WIKI_CONFIG["query_aliases"])
    for key, value in aliases.items():
        key_text = str(key)
        value_text = str(value or "").strip()
        if _LEGACY_MEDIAWIKI_ALIASES.get(key_text) == value_text:
            continue
        migrated_aliases[key_text] = value_text
    cfg["query_aliases"] = migrated_aliases
    return cfg

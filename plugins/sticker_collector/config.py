from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.StickerCollector.Config")

CONFIG_PATH = Path("BotData/plugin_configs/sticker_collector.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "collect_group": True,
    "collect_private": True,
    "allowed_groups": [],
    "ignored_users": [],
    "max_pending": 1000,
    "max_download_mb": 30,
    "temp_root": "/tmp/hikari_bot/sticker_collector",
    "download_timeout_seconds": 30,
    # 定向收集：QQ 号 -> {"pack": 目标贴纸包, "name": 昵称, "groups": 限定群(空=不限), "enabled": bool}
    "target_packs": {},
}

_write_lock = threading.Lock()


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建贴纸静默收集配置文件: %s", CONFIG_PATH)
        return

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        _write_config(data)
        logger.info("已补全贴纸静默收集配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取贴纸静默收集配置失败: %s", e)
        return DEFAULT_CONFIG.copy()

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    return cfg


def _read_raw() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.exception("读取贴纸静默收集配置失败: %s", e)
        return {}


def _normalize_target(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    pack_name = str(value.get("pack") or "").strip()
    if not pack_name:
        return None
    groups = value.get("groups") or []
    if not isinstance(groups, list):
        groups = []
    return {
        "pack": pack_name,
        "name": str(value.get("name") or "").strip(),
        "groups": [str(item) for item in groups if str(item).strip()],
        "enabled": bool(value.get("enabled", True)),
    }


def get_targets() -> dict[str, dict[str, Any]]:
    """返回规范化后的定向收集目标：QQ 号 -> 目标配置。"""
    raw = _read_raw().get("target_packs") or {}
    if not isinstance(raw, dict):
        return {}
    targets: dict[str, dict[str, Any]] = {}
    for user_id, value in raw.items():
        normalized = _normalize_target(value)
        if normalized is not None:
            targets[str(user_id).strip()] = normalized
    return targets


def get_target(user_id: str) -> dict[str, Any] | None:
    return get_targets().get(str(user_id).strip())


def set_target(user_id: str, *, pack: str, name: str = "", groups: list[str] | None = None) -> None:
    """新增或更新定向收集目标并落盘。"""
    user_id = str(user_id).strip()
    pack = str(pack).strip()
    if not user_id or not pack:
        raise ValueError("QQ 号和贴纸包名称不能为空。")
    with _write_lock:
        data = _read_raw()
        targets = data.setdefault("target_packs", {})
        if not isinstance(targets, dict):
            targets = {}
            data["target_packs"] = targets
        current = targets.get(user_id)
        targets[user_id] = {
            "pack": pack,
            "name": str(name or "").strip(),
            "groups": [str(item) for item in (groups or []) if str(item).strip()],
            "enabled": bool(current.get("enabled", True)) if isinstance(current, dict) else True,
        }
        _write_config(data)


def remove_target(user_id: str) -> bool:
    """移除定向收集目标，返回是否移除成功。"""
    user_id = str(user_id).strip()
    with _write_lock:
        data = _read_raw()
        targets = data.get("target_packs")
        if not isinstance(targets, dict) or user_id not in targets:
            return False
        targets.pop(user_id, None)
        _write_config(data)
        return True

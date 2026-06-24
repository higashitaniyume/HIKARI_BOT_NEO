from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.Resources")

RESOURCE_DIR = Path("BotData/resources")
IMAGE_DEFAULT_RESOURCE_DIR = Path("/opt/hikaribot-defaults/BotData/resources")

_cache_lock = threading.RLock()
_json_cache: dict[Path, tuple[int, dict[str, Any]]] = {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def ensure_json_resource(file_name: str, defaults: dict[str, Any]) -> Path:
    target = RESOURCE_DIR / Path(file_name).name
    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    example_name = target.with_suffix(".example.json").name
    candidates = [
        RESOURCE_DIR / example_name,
        IMAGE_DEFAULT_RESOURCE_DIR / example_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            shutil.copy2(candidate, target)
            logger.info("已创建资源文件: %s", target)
            return target

    _atomic_write_json(target, defaults)
    logger.info("已创建默认资源文件: %s", target)
    return target


def load_json_resource(file_name: str, defaults: dict[str, Any]) -> dict[str, Any]:
    path = ensure_json_resource(file_name, defaults)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return defaults.copy()

    with _cache_lock:
        cached = _json_cache.get(path)
        if cached and cached[0] == mtime_ns:
            return cached[1]

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON 顶层必须是对象")
        except Exception as e:
            logger.exception("读取资源文件失败，将使用内置默认值: %s -> %s", path, e)
            data = defaults.copy()

        _json_cache[path] = (mtime_ns, data)
        return data

"""AstrBot plugin management — API backend for the admin panel."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("AstrBotCompat.Web")


# ---------------------------------------------------------------------------
# Plugin listing & detail
# ---------------------------------------------------------------------------

def list_plugins() -> list[dict[str, Any]]:
    """Return list of all known astrbot plugins (loaded + on-disk)."""
    from plugins.astrbot_compat.constants import PLUGINS_DIR
    from plugins.astrbot_compat.loader import get_loaded_plugins
    from plugins.astrbot_compat.manager import discover_plugins as scan_disk

    started_at = time.monotonic()
    loaded = get_loaded_plugins()
    result: list[dict[str, Any]] = []

    for name, handle in sorted(loaded.items()):
        info = handle.info
        result.append({
            "name": name,
            "display_name": info["display_name"],
            "author": info["author"],
            "version": info.get("version", ""),
            "commands": info["commands"],
            "path": info["path"],
            "status": "loaded",
            "has_config": _has_config(handle.module_path),
            "has_requirements": (handle.module_path / "requirements.txt").exists(),
            "config_keys": list(handle.config_obj.keys()) if handle.config_obj else [],
        })

    discovered = {d.name for d in scan_disk()}
    loaded_names = set(loaded.keys())
    for name in sorted(discovered - loaded_names):
        plugin_dir = PLUGINS_DIR / name
        result.append({
            "name": name,
            "display_name": "",
            "author": "",
            "commands": [],
            "path": str(plugin_dir),
            "status": "discovered",
            "has_config": _has_config(plugin_dir),
            "has_requirements": (plugin_dir / "requirements.txt").exists(),
            "config_keys": [],
        })

    elapsed = time.monotonic() - started_at
    logger.debug(
        "List plugins: %d loaded, %d discovered (%.2fs)",
        len(loaded),
        len(discovered - loaded_names),
        elapsed,
    )
    return result


def get_plugin_detail(name: str) -> dict[str, Any]:
    """Return detailed info about a single plugin, with schema if available."""
    from plugins.astrbot_compat.config import parse_schema
    from plugins.astrbot_compat.constants import PLUGINS_DIR
    from plugins.astrbot_compat.loader import get_loaded_plugins

    handle = get_loaded_plugins().get(name)
    plugin_dir: Path
    loaded = handle is not None

    if handle:
        plugin_dir = handle.module_path
    else:
        plugin_dir = PLUGINS_DIR / name
        if not plugin_dir.exists() or not (plugin_dir / "main.py").exists():
            raise ValueError(f"插件不存在: {name}")

    info = handle.info if handle else {}

    # Parse schema for config form
    schema_info = parse_schema(plugin_dir / "_conf_schema.json")
    schema = schema_info["schema"]
    defaults = schema_info["defaults"]

    # Current config values (merged over defaults)
    current_config: dict[str, Any] = dict(defaults)
    if handle and handle.config_obj:
        current_config.update(dict(handle.config_obj))

    # Metadata from metadata.yaml
    from plugins.astrbot_compat.config import parse_metadata
    metadata = parse_metadata(plugin_dir)

    # Requirements
    requirements = ""
    req_path = plugin_dir / "requirements.txt"
    if req_path.exists():
        requirements = req_path.read_text(encoding="utf-8").strip()

    return {
        "name": name,
        "display_name": info.get("display_name", "") if handle else "",
        "author": metadata.get("author", info.get("author", "") if handle else ""),
        "version": metadata.get("version", ""),
        "description": metadata.get("description", ""),
        "repo": metadata.get("repo", ""),
        "homepage": metadata.get("homepage", metadata.get("website", "")),
        "tags": metadata.get("tags", []),
        "commands": info.get("commands", []) if handle else [],
        "path": str(plugin_dir),
        "status": "loaded" if loaded else "discovered",
        "schema": schema,
        "config": current_config,
        "has_config": bool(schema),
        "requirements": requirements,
    }


def save_plugin_config(name: str, config_data: dict[str, Any]) -> dict[str, Any]:
    """Save a plugin's configuration and return the updated detail."""
    from plugins.astrbot_compat.loader import get_loaded_plugins
    from plugins.astrbot_compat.config import build_config_path

    handle = get_loaded_plugins().get(name)
    if handle is None:
        raise ValueError(f"插件未加载: {name}")

    started_at = time.monotonic()
    config_path = build_config_path(name)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if handle.config_obj:
        handle.config_obj.reload()
        for key, val in config_data.items():
            handle.config_obj[key] = val

    elapsed = time.monotonic() - started_at
    logger.info(
        "Config saved via web: plugin=[%s] keys=%d (%.2fs)",
        name,
        len(config_data),
        elapsed,
    )
    return get_plugin_detail(name)


def reload_plugin(name: str) -> dict[str, Any]:
    """Reload a plugin and return updated detail."""
    from plugins.astrbot_compat.loader import reload_plugin as _reload, set_loaded_plugin
    from plugins.astrbot_compat.loader import get_loaded_plugins

    if name not in get_loaded_plugins():
        raise ValueError(f"插件未加载: {name}")
    logger.info("Web admin triggered reload: plugin=[%s]", name)
    handle = _reload(name)
    set_loaded_plugin(name, handle)
    return get_plugin_detail(name)


def remove_plugin(name: str) -> dict[str, Any]:
    """Unload a plugin and return status."""
    from plugins.astrbot_compat.loader import get_loaded_plugins, unload_plugin

    if name not in get_loaded_plugins():
        raise ValueError(f"插件未加载: {name}")
    logger.info("Web admin triggered remove: plugin=[%s]", name)
    unload_plugin(name)
    return {"status": "removed", "name": name}


def load_plugin_from_path(plugin_path: str, plugin_name: str | None = None) -> dict[str, Any]:
    """Load a plugin from a directory or zip path."""
    import zipfile
    from plugins.astrbot_compat.loader import load_plugin as _load, set_loaded_plugin
    from plugins.astrbot_compat.manager import extract_plugin_zip

    target = Path(plugin_path).resolve()
    if not target.exists():
        raise ValueError(f"路径不存在: {target}")

    if target.is_file() and zipfile.is_zipfile(target):
        name = plugin_name or target.stem
        plugin_dir = extract_plugin_zip(target, name)
    elif target.is_dir():
        plugin_dir = target
        name = plugin_name or plugin_dir.name
    else:
        raise ValueError(f"不支持的文件: {target}")

    if not (plugin_dir / "main.py").exists():
        raise ValueError(f"目录中未找到 main.py: {plugin_dir}")

    logger.info("Web admin triggered load: plugin=[%s] source=%s", name, plugin_path)
    handle = _load(plugin_dir, plugin_name=name)
    set_loaded_plugin(name, handle)
    return get_plugin_detail(name)


def upload_and_load_plugin(
    archive_content: bytes,
    filename: str,
    plugin_name: str | None = None,
) -> dict[str, Any]:
    """Save an uploaded plugin zip to disk, extract it, and load it."""
    import zipfile
    import tempfile
    from plugins.astrbot_compat.manager import extract_plugin_zip
    from plugins.astrbot_compat.loader import load_plugin as _load, set_loaded_plugin

    # Validate it's a real zip before writing
    import io as _io
    try:
        with zipfile.ZipFile(_io.BytesIO(archive_content)) as _zf:
            if _zf.testzip() is not None:
                raise ValueError("上传的 zip 文件已损坏。")
    except (zipfile.BadZipFile, EOFError):
        raise ValueError("上传的文件不是有效的 zip 压缩包。")

    # Determine plugin name
    name = plugin_name or Path(filename).stem

    # Write zip to a temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        tmp.write(archive_content)
        tmp.close()
        zip_path = Path(tmp.name)

        # Extract
        logger.info("Processing uploaded zip: name=[%s] file=%s size=%d", name, filename, len(archive_content))
        plugin_dir = extract_plugin_zip(zip_path, name)

        # Load
        handle = _load(plugin_dir, plugin_name=name)
        set_loaded_plugin(name, handle)
        result = get_plugin_detail(name)
        result["message"] = "插件已上传并加载。"
        return result
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def rebuild_plugin_env() -> dict[str, Any]:
    """Rebuild the shared plugin venv."""
    from plugins.astrbot_compat.venv_manager import PluginVenvManager
    from plugins.astrbot_compat.constants import PLUGINS_DIR
    from plugins.astrbot_compat.loader import get_loaded_plugins

    logger.info("Web admin triggered venv rebuild")
    venv_mgr = PluginVenvManager(PLUGINS_DIR / ".venv")
    all_reqs: list[list[str]] = []

    for handle in get_loaded_plugins().values():
        req_path = handle.module_path / "requirements.txt"
        reqs = venv_mgr.parse_requirements(req_path)
        if reqs:
            all_reqs.append(reqs)

    venv_mgr.rebuild_all(all_reqs)
    venv_mgr.add_to_path()
    logger.info("Plugin venv rebuilt via web admin (%d plugin(s))", len(get_loaded_plugins()))
    return {"status": "ok", "plugin_count": len(get_loaded_plugins())}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_config(plugin_dir: Path) -> bool:
    return (plugin_dir / "_conf_schema.json").exists()

"""AstrBot plugin lifecycle management.

Provides the ``/astrbot`` management commands and handles startup loading
of pre-existing plugins.
"""

from __future__ import annotations

import logging
import time
import zipfile
from pathlib import Path
from typing import Any

from core.command_router import command, CommandContext

from plugins.astrbot_compat.config import parse_schema
from plugins.astrbot_compat.loader import (
    PluginHandle,
    get_loaded_plugins,
    load_plugin,
    reload_plugin,
    set_loaded_plugin,
    unload_plugin,
)

logger = logging.getLogger("AstrBotCompat.Manager")


def ensure_plugin_dirs() -> None:
    """Create the plugins storage directory if missing."""
    from plugins.astrbot_compat.constants import PLUGINS_DIR

    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)


def get_plugin_dir(name: str) -> Path:
    """Return the path where a plugin's extracted files live."""
    from plugins.astrbot_compat.constants import PLUGINS_DIR
    return PLUGINS_DIR / name


def discover_plugins() -> list[Path]:
    """Scan the plugins directory for potential astrbot plugins.

    A valid plugin dir has a ``main.py``.
    """
    from plugins.astrbot_compat.constants import PLUGINS_DIR

    if not PLUGINS_DIR.exists():
        return []

    candidates: list[Path] = []
    for entry in sorted(PLUGINS_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            if (entry / "main.py").exists():
                candidates.append(entry)

    if candidates:
        logger.debug(
            "Discovered %d plugin(s) on disk: %s",
            len(candidates),
            [d.name for d in candidates],
        )
    return candidates


def extract_plugin_zip(zip_path: Path, target_name: str | None = None) -> Path:
    """Extract a plugin zip to the plugins directory.

    Returns:
        The directory where the plugin was extracted.

    Raises:
        ValueError: If the zip doesn't contain a ``main.py``.
    """
    from plugins.astrbot_compat.constants import PLUGINS_DIR

    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Not a valid zip file: {zip_path}")

    if target_name is None:
        target_name = zip_path.stem

    started_at = time.monotonic()
    target_dir = PLUGINS_DIR / target_name
    if target_dir.exists():
        import shutil
        logger.warning("Removing existing plugin directory for zip extraction: %s", target_dir)
        shutil.rmtree(target_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.namelist()
        if any("/" in m for m in members):
            zf.extractall(target_dir)
            subdirs = [d for d in target_dir.iterdir() if d.is_dir()]
            if len(subdirs) == 1 and (subdirs[0] / "main.py").exists():
                flat_dir = subdirs[0]
                for item in flat_dir.iterdir():
                    item.rename(target_dir / item.name)
                import shutil as _sh
                _sh.rmtree(flat_dir)
                logger.debug("Flattened single-subdirectory zip structure for %s", target_name)
        else:
            zf.extractall(target_dir)

    # Verify main.py exists
    if not (target_dir / "main.py").exists():
        import shutil
        shutil.rmtree(target_dir)
        raise ValueError(
            f"Plugin zip does not contain main.py (extracted to {target_dir})"
        )

    elapsed = time.monotonic() - started_at
    logger.info("Plugin zip extracted to %s in %.2fs", target_dir, elapsed)
    return target_dir


async def auto_load_plugins() -> int:
    """Load all discovered plugins at startup.

    Returns:
        Number of plugins successfully loaded.
    """
    discovered = discover_plugins()
    if not discovered:
        logger.debug("No astrbot plugins found on disk to auto-load")
        return 0

    logger.info("Auto-loading %d astrbot plugin(s) ...", len(discovered))
    started_at = time.monotonic()
    count = 0
    for plugin_dir in discovered:
        name = plugin_dir.name
        try:
            handle = load_plugin(plugin_dir, plugin_name=name)
            set_loaded_plugin(name, handle)
            count += 1
        except (ValueError, ImportError) as e:
            logger.error("Failed to auto-load plugin [%s]: %s", name, e)

    elapsed = time.monotonic() - started_at
    logger.info(
        "Auto-loaded %d/%d astrbot plugin(s) in %.2fs",
        count,
        len(discovered),
        elapsed,
    )
    return count


# ---------------------------------------------------------------------------
# Management commands
# ---------------------------------------------------------------------------

@command(
    name="astrbot list",
    aliases=(),
    description="列出已加载的AstrBot兼容插件",
    usage="astrbot list",
    detail_key="astrbot_list",
    private_only=True,
)
async def cmd_astrbot_list(ctx: CommandContext) -> None:
    """List all loaded astrbot plugins."""
    plugins = get_loaded_plugins()
    logger.info("User requested plugin list (loaded=%d)", len(plugins))
    if not plugins:
        await ctx.send("没有已加载的 AstrBot 兼容插件。")
        return

    lines = ["📦 已加载的 AstrBot 插件："]
    for name, handle in sorted(plugins.items()):
        info = handle.info
        commands = info["commands"]
        cmd_str = ", ".join(f"/{c}" for c in commands) if commands else "(无命令)"
        author = info["author"] or "(未设置)"
        lines.append(
            f"  • {info['display_name']} ({name}) "
            f"by {author} — 命令: {cmd_str}"
        )
    lines.append(f"\n共 {len(plugins)} 个插件。")
    await ctx.send("\n".join(lines))


@command(
    name="astrbot load",
    aliases=(),
    description="加载一个AstrBot兼容插件（路径或zip）",
    usage="astrbot load <路径> [插件名]",
    detail_key="astrbot_load",
    private_only=True,
)
async def cmd_astrbot_load(ctx: CommandContext) -> None:
    """Load an astrbot plugin from a path or zip file."""
    args = ctx.args.strip()
    if not args:
        await ctx.send("用法：astrbot load <路径> [插件名]\n路径可以是目录或 zip 文件。")
        return

    parts = args.split(maxsplit=1)
    path_str = parts[0]
    plugin_name = parts[1] if len(parts) > 1 else None

    target_path = Path(path_str).resolve()

    if not target_path.exists():
        logger.warning("Load failed — path not found: %s", target_path)
        await ctx.send(f"路径不存在: {target_path}")
        return

    try:
        if target_path.is_file() and zipfile.is_zipfile(target_path):
            await ctx.send("正在解压插件 zip ...")
            if plugin_name is None:
                plugin_name = target_path.stem
            plugin_dir = extract_plugin_zip(target_path, plugin_name)
        elif target_path.is_dir():
            plugin_dir = target_path
            if plugin_name is None:
                plugin_name = plugin_dir.name
        else:
            await ctx.send(f"不支持的文件格式: {target_path}")
            return

        if plugin_name in get_loaded_plugins():
            logger.warning("Load skipped — already loaded: %s", plugin_name)
            await ctx.send(f"插件 {plugin_name} 已加载，请先 /astrbot remove {plugin_name}")
            return

        logger.info("User triggered load: plugin=[%s] source=%s", plugin_name, target_path)
        await ctx.send(f"正在加载插件 {plugin_name} ...")
        handle = load_plugin(plugin_dir, plugin_name=plugin_name)
        set_loaded_plugin(plugin_name, handle)

        info = handle.info
        cmd_count = len(info["commands"])
        await ctx.send(
            f"✅ 插件 {info['display_name']} ({plugin_name}) 加载成功！\n"
            f"   • 作者: {info['author'] or '(未设置)'}\n"
            f"   • 命令: {cmd_count} 个"
        )
    except (ValueError, ImportError, zipfile.BadZipFile) as e:
        await ctx.send(f"❌ 加载失败: {e}")
        logger.exception("User load failed: plugin=[%s] source=%s", plugin_name, target_path)


@command(
    name="astrbot remove",
    aliases=(),
    description="卸载一个AstrBot兼容插件",
    usage="astrbot remove <插件名>",
    detail_key="astrbot_remove",
    private_only=True,
)
async def cmd_astrbot_remove(ctx: CommandContext) -> None:
    """Unload a loaded astrbot plugin."""
    name = ctx.args.strip()
    if not name:
        await ctx.send("用法：astrbot remove <插件名>\n用 /astrbot list 查看已加载的插件。")
        return

    if name not in get_loaded_plugins():
        await ctx.send(f"插件 {name} 未加载。使用 /astrbot list 查看。")
        return

    try:
        logger.info("User triggered remove: plugin=[%s]", name)
        unload_plugin(name)
        await ctx.send(f"✅ 插件 {name} 已卸载。")
    except Exception as e:
        await ctx.send(f"❌ 卸载失败: {e}")
        logger.exception("User remove failed: plugin=[%s]", name)


@command(
    name="astrbot reload",
    aliases=(),
    description="重新加载一个AstrBot兼容插件",
    usage="astrbot reload <插件名>",
    detail_key="astrbot_reload",
    private_only=True,
)
async def cmd_astrbot_reload(ctx: CommandContext) -> None:
    """Reload a loaded astrbot plugin."""
    name = ctx.args.strip()
    if not name:
        await ctx.send("用法：astrbot reload <插件名>\n用 /astrbot list 查看已加载的插件。")
        return

    if name not in get_loaded_plugins():
        await ctx.send(f"插件 {name} 未加载。使用 /astrbot list 查看。")
        return

    try:
        logger.info("User triggered reload: plugin=[%s]", name)
        await ctx.send(f"正在重新加载 {name} ...")
        handle = reload_plugin(name)
        set_loaded_plugin(name, handle)
        await ctx.send(f"✅ 插件 {name} 已重新加载。")
    except Exception as e:
        await ctx.send(f"❌ 重新加载失败: {e}")
        logger.exception("User reload failed: plugin=[%s]", name)


@command(
    name="astrbot rebuild-env",
    aliases=(),
    description="重建AstrBot插件公共虚拟环境",
    usage="astrbot rebuild-env",
    detail_key="astrbot_rebuild_env",
    private_only=True,
)
async def cmd_astrbot_rebuild_env(ctx: CommandContext) -> None:
    """Rebuild the shared plugin venv from all loaded plugins' requirements."""
    from plugins.astrbot_compat.venv_manager import PluginVenvManager
    from plugins.astrbot_compat.constants import PLUGINS_DIR

    await ctx.send("正在重建插件公共虚拟环境 ... 这可能需要几分钟。")

    all_reqs: list[list[str]] = []
    venv_mgr = PluginVenvManager(PLUGINS_DIR / ".venv")

    for handle in get_loaded_plugins().values():
        req_path = handle.module_path / "requirements.txt"
        reqs = venv_mgr.parse_requirements(req_path)
        if reqs:
            all_reqs.append(reqs)

    try:
        logger.info("User triggered env rebuild (%d plugin(s) with deps)", len(all_reqs))
        venv_mgr.rebuild_all(all_reqs)
        venv_mgr.add_to_path()
        await ctx.send(
            f"✅ 插件公共虚拟环境已重建。包含 {len(all_reqs)} 个插件的依赖。"
        )
    except RuntimeError as e:
        logger.error("User env rebuild failed: %s", e)
        await ctx.send(f"❌ 重建失败: {e}")


@command(
    name="astrbot info",
    aliases=(),
    description="查看AstrBot插件的详细信息",
    usage="astrbot info <插件名>",
    detail_key="astrbot_info",
    private_only=True,
)
async def cmd_astrbot_info(ctx: CommandContext) -> None:
    """Show detailed info about a loaded plugin."""
    name = ctx.args.strip()
    if not name:
        await ctx.send("用法：astrbot info <插件名>")
        return

    handle = get_loaded_plugins().get(name)
    if handle is None:
        await ctx.send(f"插件 {name} 未加载。")
        return

    info = handle.info
    commands = info["commands"]
    cmd_str = ", ".join(f"/{c}" for c in commands) if commands else "(无命令)"

    lines = [
        f"📦 {info['display_name']}",
        f"   • 内部名: {info['name']}",
        f"   • 类名: {info['class']}",
        f"   • 作者: {info['author'] or '(未设置)'}",
        f"   • 路径: {info['path']}",
        f"   • 注册命令: {cmd_str}",
    ]

    # Schema info
    schema_path = handle.module_path / "_conf_schema.json"
    if schema_path.exists():
        from plugins.astrbot_compat.config import parse_schema
        schema = parse_schema(schema_path)
        if schema["defaults"]:
            lines.append("   • 配置项:")
            for key, val in schema["defaults"].items():
                current = handle.config_obj.get(key, val)
                lines.append(f"       {key} = {current!r}")

    # Requirements
    req_path = handle.module_path / "requirements.txt"
    if req_path.exists():
        deps = req_path.read_text(encoding="utf-8").strip()
        if deps:
            lines.append(f"   • 依赖:\n{deps}")

    await ctx.send("\n".join(lines))

"""AI Agent 聊天文件接收模块。

职责：
- 从 OneBot V11 消息（及可选的引用消息）中解析 `file` 段；
- 执行四级下载阶梯（本地挂载优先 -> get_file -> get_*_file_url -> 直链流式下载）；
- 100MB 硬上限保护（大小预检 + 流式计数双保险）；
- 会话隔离：文件保存至当前会话工作区 `UserData/aiagent_files/<scope>/<id>/incoming/`；
- 10 分钟重放去重与 7 天懒清理。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from .utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Attachments")

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_NAPCAT_TEMP_DIRS = (
    Path("/app/.config/QQ/NapCat/temp"),
    Path("./napcat/ntqq/NapCat/temp"),
)

# 10 分钟内去重：(session_key, file_unique_or_id) -> (saved_relative_path, cached_at)
_recent_downloads: dict[str, tuple[str, float, float]] = {}
_DEDUP_TTL_SECONDS = 600.0


def _workspace_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    return tools_cfg.get("workspace") if isinstance(tools_cfg.get("workspace"), dict) else {}


def clean_filename(name: str, fallback: str = "file.bin") -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", str(name or "").strip()).strip(". ")
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]


def _cleanup_expired_incoming(incoming_dir: Path, retention_days: int) -> None:
    if retention_days <= 0 or not incoming_dir.exists():
        return
    now = time.time()
    cutoff = now - (retention_days * 86400)
    try:
        for p in incoming_dir.iterdir():
            if p.is_file():
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                except OSError:
                    pass
    except Exception as e:
        logger.debug("[AIAgent] 懒清理 incoming 目录异常: %s", e)


def collect_file_segments(event: Any, include_quoted: bool = True) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def absorb(message: Any) -> None:
        for seg in message or []:
            if str(getattr(seg, "type", "") or "") != "file":
                continue
            data = getattr(seg, "data", None)
            if not isinstance(data, dict):
                continue
            file_id = str(data.get("file_id") or data.get("file_unique") or data.get("file") or "").strip()
            if file_id and file_id in seen_ids:
                continue
            if file_id:
                seen_ids.add(file_id)
            segments.append(data)

    if include_quoted:
        reply = getattr(event, "reply", None)
        if reply is not None:
            absorb(getattr(reply, "message", None))
    try:
        absorb(event.get_message())
    except Exception:
        pass
    return segments


async def _download_url_stream(
    url: str,
    dest: Path,
    max_bytes: int,
    timeout_seconds: int = 30,
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds), follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    logger.warning("[AIAgent] 文件 URL 下载 HTTP %d: %.80s", resp.status_code, url)
                    return False
                content_len = resp.headers.get("content-length")
                if content_len:
                    try:
                        if int(content_len) > max_bytes:
                            logger.warning("[AIAgent] 文件大小超过 %d 字节上限: %.80s", max_bytes, url)
                            return False
                    except ValueError:
                        pass

                buffer_size = 0
                with dest.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        buffer_size += len(chunk)
                        if buffer_size > max_bytes:
                            logger.warning("[AIAgent] 文件流式大小超过 %d 字节上限，中止下载: %.80s", max_bytes, url)
                            f.close()
                            if dest.exists():
                                dest.unlink()
                            return False
                        f.write(chunk)
                return True
    except Exception as e:
        logger.warning("[AIAgent] 文件下载异常 %.80s: %s", url, e)
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return False


async def download_file_attachment(
    bot: Any,
    event: Any,
    data: dict[str, Any],
    incoming_dir: Path,
    max_bytes: int,
) -> tuple[str | None, str | None, float]:
    """按四级阶梯下载单个文件段。

    返回: (relative_path, error_message, size_mb)
    """
    raw_name = str(data.get("name") or data.get("file_name") or "file.bin").strip()
    name = clean_filename(raw_name)

    # 1. 预检已知大小
    file_size_str = str(data.get("file_size") or "").strip()
    if file_size_str.isdigit():
        reported_size = int(file_size_str)
        if reported_size > max_bytes:
            return None, f"超过单文件 {max_bytes / 1024 / 1024:.0f}MB 上限（文件大小 {reported_size / 1024 / 1024:.1f}MB）", reported_size / 1024 / 1024

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    hash_prefix = hashlib.md5(f"{time.time()}_{name}".encode()).hexdigest()[:8]
    target_filename = f"{timestamp}_{hash_prefix}_{name}"
    target_path = incoming_dir / target_filename

    # 阶梯 1: 检查本地路径
    for local_candidate in (data.get("path"), data.get("file")):
        if not local_candidate:
            continue
        p = Path(str(local_candidate))
        if p.is_file():
            # 校验是否在 NapCat temp 目录或合法挂载目录
            is_valid_dir = any(
                p.resolve() == td.resolve() or td.resolve() in p.resolve().parents
                for td in _NAPCAT_TEMP_DIRS
                if td.exists()
            )
            # 即使不在 NapCat temp，只要本地存在且安全也可用
            if is_valid_dir or p.exists():
                size = p.stat().st_size
                if size > max_bytes:
                    return None, f"超过单文件 {max_bytes / 1024 / 1024:.0f}MB 上限", size / 1024 / 1024
                shutil.copy2(p, target_path)
                return f"incoming/{target_filename}", None, size / 1024 / 1024

    file_id = str(data.get("file_id") or "").strip()

    # 阶梯 2: 调用 get_file
    if file_id and bot is not None:
        try:
            res = await bot.call_api("get_file", file_id=file_id)
            if isinstance(res, dict):
                # 优先本地文件
                local_f = res.get("file")
                if local_f and Path(str(local_f)).is_file():
                    p = Path(str(local_f))
                    size = p.stat().st_size
                    if size > max_bytes:
                        return None, f"超过单文件 {max_bytes / 1024 / 1024:.0f}MB 上限", size / 1024 / 1024
                    shutil.copy2(p, target_path)
                    return f"incoming/{target_filename}", None, size / 1024 / 1024

                # 次选 url
                url = res.get("url")
                if url and str(url).startswith(("http://", "https://")):
                    ok = await _download_url_stream(str(url), target_path, max_bytes)
                    if ok and target_path.is_file():
                        size = target_path.stat().st_size
                        return f"incoming/{target_filename}", None, size / 1024 / 1024

                # 最后才考虑 base64（仅 ≤20MB）
                b64 = res.get("base64")
                if b64 and isinstance(b64, str):
                    if len(b64) <= 20 * 1024 * 1024 * 1.37:
                        content = base64.b64decode(b64)
                        if len(content) > max_bytes:
                            return None, f"超过单文件 {max_bytes / 1024 / 1024:.0f}MB 上限", len(content) / 1024 / 1024
                        target_path.write_bytes(content)
                        return f"incoming/{target_filename}", None, len(content) / 1024 / 1024
        except Exception as e:
            logger.debug("[AIAgent] get_file 失败 file_id=%s: %s", file_id, e)

    # 阶梯 3: get_group_file_url / get_private_file_url
    if file_id and bot is not None:
        group_id = getattr(event, "group_id", None)
        try:
            if group_id is not None:
                res = await bot.call_api("get_group_file_url", group_id=int(group_id), file_id=file_id)
            else:
                res = await bot.call_api("get_private_file_url", file_id=file_id)
            if isinstance(res, dict) and res.get("url"):
                url = str(res["url"])
                ok = await _download_url_stream(url, target_path, max_bytes)
                if ok and target_path.is_file():
                    size = target_path.stat().st_size
                    return f"incoming/{target_filename}", None, size / 1024 / 1024
        except Exception as e:
            logger.debug("[AIAgent] 获取文件直链失败 file_id=%s: %s", file_id, e)

    # 阶梯 4: 消息段自带的 url
    direct_url = str(data.get("url") or "").strip()
    if direct_url.startswith(("http://", "https://")):
        ok = await _download_url_stream(direct_url, target_path, max_bytes)
        if ok and target_path.is_file():
            size = target_path.stat().st_size
            return f"incoming/{target_filename}", None, size / 1024 / 1024

    return None, "无法下载文件（所有下载途径均失败）", 0.0


async def collect_and_save_attachments(
    bot: Any,
    event: Any,
    cfg: dict[str, Any],
    is_auto_reply: bool = False,
) -> list[str]:
    """提取消息中的文件并下载到当前会话的工作区 `incoming/` 目录。

    返回可追加到模型上下文的提示文本列表。
    """
    ws_cfg = _workspace_cfg(cfg)
    if not safe_bool(ws_cfg.get("enabled"), True):
        return []
    if not safe_bool(ws_cfg.get("ingest_incoming"), True):
        return []
    if is_auto_reply and not safe_bool(ws_cfg.get("allow_in_auto_reply"), False):
        return []

    from .tools.workspace import get_session_workspace_root

    root = get_session_workspace_root(type("Ctx", (), {"event": event}))
    incoming_dir = root / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)

    retention_days = safe_int(ws_cfg.get("incoming_retention_days"), 7, minimum=0, maximum=365)
    _cleanup_expired_incoming(incoming_dir, retention_days)

    include_quoted = safe_bool(ws_cfg.get("include_quoted_files"), True)
    file_segments = collect_file_segments(event, include_quoted=include_quoted)
    if not file_segments:
        return []

    max_files = safe_int(ws_cfg.get("incoming_max_files"), 3, minimum=1, maximum=10)
    max_mb = safe_int(ws_cfg.get("incoming_max_mb"), 100, minimum=1, maximum=500)
    max_bytes = max_mb * 1024 * 1024

    session_id = getattr(event, "group_id", None)
    sid_str = f"group:{session_id}" if session_id is not None else f"user:{getattr(event, 'user_id', '')}"
    now = time.time()

    prompts: list[str] = []

    for seg in file_segments[:max_files]:
        filename = clean_filename(str(seg.get("name") or seg.get("file_name") or "file.bin"))
        unique_key = str(seg.get("file_unique") or seg.get("file_id") or filename).strip()
        cache_key = f"{sid_str}:{unique_key}"

        # 检查 10 分钟重放缓存
        if cache_key in _recent_downloads:
            rel_path, cached_at, cached_size = _recent_downloads[cache_key]
            if now - cached_at < _DEDUP_TTL_SECONDS:
                prompts.append(f"（用户发送了文件：{rel_path}，大小 {cached_size:.1f} MB，已保存到你的工作区）")
                continue

        rel_path, err, size_mb = await download_file_attachment(bot, event, seg, incoming_dir, max_bytes)
        if rel_path is not None:
            _recent_downloads[cache_key] = (rel_path, now, size_mb)
            prompts.append(f"（用户发送了文件：{rel_path}，大小 {size_mb:.1f} MB，已保存到你的工作区）")
        else:
            prompts.append(f"（用户发送了文件：{filename}，下载失败：{err or '未知错误'}）")

    return prompts

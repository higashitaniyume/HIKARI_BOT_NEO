"""
NCM 文件解密发送模块。

负责：
1. 通过 NapCat get_file API 获取 NCM 文件信息
2. 下载 NCM 文件内容（URL 直链或本地路径）
3. 调用 core.ncm_decrypt 解密
4. 写出临时音频文件并写入元数据标签
5. 通过 upload_private_file / upload_group_file 发送解密后的音频文件
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from core.ncm_decrypt import decrypt_ncm
from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, register_temp_media_path

logger = logging.getLogger("HikariBot.NcmParserSender")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)


def _sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


def _read_local_file_safe(path: str, max_bytes: int, log_prefix: str) -> bytes:
    """安全读取本地文件，带大小检查和日志。"""
    local = Path(path)
    if not local.exists():
        logger.error("%s   本地文件不存在 → %s", log_prefix, path)
        raise FileNotFoundError(f"本地文件不存在: {path}")
    if not local.is_file():
        logger.error("%s   路径不是文件 → %s", log_prefix, path)
        raise RuntimeError(f"路径不是文件: {path}")

    local_size = local.stat().st_size
    logger.info(
        "%s   本地文件 → %s (%.1fMB)",
        log_prefix,
        path,
        local_size / 1024 / 1024,
    )

    if local_size > max_bytes:
        raise RuntimeError(
            f"NCM 文件超过大小限制：{local_size / 1024 / 1024:.1f}MB"
        )

    data = local.read_bytes()
    logger.info(
        "%s   本地读取成功 → %s (%.1fMB)",
        log_prefix,
        path,
        len(data) / 1024 / 1024,
    )
    return data


async def _download_ncm_content(
    url: str,
    max_bytes: int,
    timeout: float,
) -> bytes:
    """
    从 URL 下载 NCM 文件的完整内容到内存。

    Args:
        url: 文件下载 URL
        max_bytes: 最大允许大小（字节）
        timeout: 请求超时（秒）

    Returns:
        下载到的 NCM 文件二进制内容

    Raises:
        RuntimeError: 下载失败或超过大小限制
        httpx.TimeoutException: 下载超时
    """
    download_start = time.time()
    logger.debug("[NcmParser] 开始流式下载 → url=%s", url[:160])

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0, read=timeout),
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()

            status = resp.status_code
            cl = resp.headers.get("content-length")
            ct = resp.headers.get("content-type", "未知")

            logger.debug(
                "[NcmParser] 下载响应 → status=%d, content-type=%s, content-length=%s",
                status,
                ct,
                cl or "未知",
            )

            if cl and cl.isdigit():
                remote_size = int(cl)
                if remote_size > max_bytes:
                    logger.error(
                        "[NcmParser] 文件超过大小限制 → remote=%.1fMB, limit=%dMB",
                        remote_size / 1024 / 1024,
                        max_bytes / 1024 / 1024,
                    )
                    raise RuntimeError(
                        f"NCM 文件超过大小限制：{remote_size / 1024 / 1024:.1f}MB"
                    )

            data = bytearray()
            chunk_count = 0
            async for chunk in resp.aiter_bytes():
                if chunk:
                    data.extend(chunk)
                    chunk_count += 1
                    if len(data) > max_bytes:
                        raise RuntimeError(
                            f"NCM 文件超过大小限制：{len(data) / 1024 / 1024:.1f}MB"
                        )

            elapsed = time.time() - download_start
            logger.debug(
                "[NcmParser] 流式下载完成 → chunks=%d, size=%.1fMB (%.1fMB/s, %.1fs)",
                chunk_count,
                len(data) / 1024 / 1024,
                len(data) / 1024 / 1024 / elapsed if elapsed > 0 else 0,
                elapsed,
            )
            return bytes(data)


async def download_and_decrypt_ncm(
    file_id: str,
    bot: Any,
    cfg: dict[str, Any],
) -> tuple[str, Path] | None:
    """
    通过 NapCat get_file 获取 NCM 文件内容，解密并保存为临时音频文件。

    Args:
        file_id: OneBot 文件 ID
        bot: NoneBot Bot 实例
        cfg: 插件配置

    Returns:
        (filename, temp_path) 元组，filename 用于上传时的显示名

    Raises:
        RuntimeError: 获取文件/下载/解密环节失败
    """
    session_start = time.time()
    log_prefix = f"[NcmParser] file_id={file_id}"

    max_file_mb = max(1, int(cfg.get("max_file_mb", 50)))
    max_bytes = max_file_mb * 1024 * 1024
    timeout = float(cfg.get("api_timeout", 60))
    temp_root = str(cfg.get("temp_root", "/tmp/hikari_bot/ncm"))
    retry_count = max(0, int(cfg.get("retry_count", 2)))
    retry_delay = max(0.0, float(cfg.get("retry_delay_seconds", 2.0)))

    logger.info(
        "%s ═══ 开始处理 NCM ═══\n"
        "    max_file_mb=%s, timeout=%ss, temp_root=%s\n"
        "    retry_count=%s, retry_delay=%.1fs",
        log_prefix,
        max_file_mb,
        timeout,
        temp_root,
        retry_count,
        retry_delay,
    )

    Path(temp_root).mkdir(parents=True, exist_ok=True)
    logger.debug("%s 临时目录已就绪 → %s", log_prefix, temp_root)

    # ===== 步骤 1: 通过 NapCat get_file 获取文件信息（带重试） =====
    step_start = time.time()
    logger.info("%s ▶ 步骤 1/4: 调用 NapCat get_file API", log_prefix)

    file_info: dict[str, Any] | None = None
    last_error: Exception | None = None

    for attempt in range(1, retry_count + 2):
        attempt_start = time.time()
        try:
            file_info = await bot.call_api("get_file", file_id=file_id)
            step_elapsed = time.time() - attempt_start
            logger.info(
                "%s   get_file 成功 (第 %d 次, %.1fms)",
                log_prefix,
                attempt,
                step_elapsed * 1000,
            )
            break
        except Exception as e:
            last_error = e
            step_elapsed = time.time() - attempt_start
            if attempt <= retry_count:
                logger.warning(
                    "%s   get_file 失败 (第 %d/%d 次, %.1fms), %.1fs 后重试: %s",
                    log_prefix,
                    attempt,
                    retry_count + 1,
                    step_elapsed * 1000,
                    retry_delay,
                    e,
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    "%s   get_file 失败 (第 %d/%d 次, %.1fms), 已无重试次数: %s",
                    log_prefix,
                    attempt,
                    retry_count + 1,
                    step_elapsed * 1000,
                    e,
                )
                raise RuntimeError(f"获取 NCM 文件信息失败: {last_error}") from last_error

    if file_info is None:
        raise RuntimeError("获取 NCM 文件信息失败: 返回为空")

    # 打印原始 file_info 到日志，方便调试 NapCat 返回值
    logger.debug(
        "%s   get_file 原始响应 → %s",
        log_prefix,
        json.dumps(file_info, ensure_ascii=False, default=str),
    )

    raw_file_name = str(file_info.get("file_name", "unknown.ncm"))
    file_url_raw = str(file_info.get("url") or "").strip()
    local_path_raw = str(file_info.get("file") or "").strip()
    reported_size = str(file_info.get("file_size", ""))

    if not file_url_raw and not local_path_raw:
        raise RuntimeError("get_file 未返回 url 或 file 字段，NapCat 版本可能过旧或配置不完整")

    # 判断 URL 是否为 HTTP(S) 可下载链接
    url_scheme = urlparse(file_url_raw).scheme.lower()
    is_http_url = url_scheme in ("http", "https")

    step_elapsed = time.time() - step_start
    logger.info(
        "%s ✓ 步骤 1/4 完成 (%.1fs)\n"
        "    文件名: %s\n"
        "    文件大小: %s\n"
        "    url 字段: %s\n"
        "    file 字段: %s\n"
        "    url_scheme=%s → %s",
        log_prefix,
        step_elapsed,
        raw_file_name,
        reported_size or "未知",
        file_url_raw[:200] if file_url_raw else "(空)",
        local_path_raw[:200] if local_path_raw else "(空)",
        url_scheme or "(空)",
        "HTTP 下载" if is_http_url else "本地读取",
    )

    # ===== 步骤 2: 获取 NCM 文件内容 =====
    step_start = time.time()
    logger.info(
        "%s ▶ 步骤 2/4: 获取 NCM 文件内容 → max_size=%dMB, timeout=%ss",
        log_prefix,
        max_file_mb,
        timeout,
    )

    ncm_data: bytes | None = None
    last_error = None

    # -- 优先走 HTTP 下载 --
    if is_http_url:
        for attempt in range(1, retry_count + 2):
            attempt_start = time.time()
            try:
                ncm_data = await _download_ncm_content(file_url_raw, max_bytes, timeout)
                download_elapsed = time.time() - attempt_start
                speed = len(ncm_data) / 1024 / 1024 / download_elapsed if download_elapsed > 0 else 0
                logger.info(
                    "%s   下载成功 (第 %d/%d 次, %.1fs, %.1fMB/s) → %s (%.1fMB)",
                    log_prefix,
                    attempt,
                    retry_count + 1,
                    download_elapsed,
                    speed,
                    raw_file_name,
                    len(ncm_data) / 1024 / 1024,
                )
                break
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = e
                download_elapsed = time.time() - attempt_start
                if attempt <= retry_count:
                    logger.warning(
                        "%s   下载失败 (第 %d/%d 次, %.1fs), %.1fs 后重试: %s",
                        log_prefix,
                        attempt,
                        retry_count + 1,
                        download_elapsed,
                        retry_delay,
                        e,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        "%s   下载失败 (第 %d/%d 次, %.1fs), 已无重试次数: %s",
                        log_prefix,
                        attempt,
                        retry_count + 1,
                        download_elapsed,
                        e,
                    )
                    raise RuntimeError(f"NCM 文件下载失败: {last_error}") from last_error
            except Exception as e:
                logger.error(
                    "%s   下载异常: %s",
                    log_prefix,
                    e,
                )
                raise RuntimeError(f"NCM 文件下载异常: {e}") from e

    # -- URL 非 HTTP 时，尝试本地读取 --
    if ncm_data is None:
        # URL 是非 HTTP 协议（file:// 或裸路径）且还未成功读取
        if file_url_raw and not is_http_url:
            # 尝试从 file:// URL 中提取路径
            parsed = urlparse(file_url_raw)
            local_candidate = parsed.path if parsed.scheme in ("file", "") else file_url_raw
            logger.info(
                "%s   URL 非 HTTP 协议 (scheme=%r), 尝试本地读取 → candidate=%s",
                log_prefix,
                url_scheme,
                local_candidate[:200],
            )
            try:
                ncm_data = _read_local_file_safe(local_candidate, max_bytes, log_prefix)
            except Exception:
                logger.warning(
                    "%s   从 URL 提取路径读取失败, 尝试 file 字段 → %s",
                    log_prefix,
                    local_path_raw[:200] or "(空)",
                )

        # 降级到 file 字段（NapCat 本地路径）
        if ncm_data is None and local_path_raw:
            logger.info(
                "%s   降级读取 file 字段 → path=%s",
                log_prefix,
                local_path_raw[:200],
            )
            try:
                ncm_data = _read_local_file_safe(local_path_raw, max_bytes, log_prefix)
            except Exception as e:
                raise RuntimeError(f"读取 NCM 文件失败 (file 字段): {e}") from e

    if ncm_data is None:
        raise RuntimeError(
            f"无法获取 NCM 文件内容: "
            f"url_scheme={url_scheme!r}, "
            f"url={file_url_raw[:160]!r}, "
            f"file={local_path_raw[:160]!r}"
        )

    if ncm_data is None or len(ncm_data) == 0:
        raise RuntimeError("下载到的 NCM 文件内容为空")

    step_elapsed = time.time() - step_start
    logger.info(
        "%s ✓ 步骤 2/4 完成 (%.1fs) → 到手大小: %.1fMB",
        log_prefix,
        step_elapsed,
        len(ncm_data) / 1024 / 1024,
    )

    # ===== 步骤 3: 解密 NCM 文件（不重试 — 解密失败通常意味着文件损坏） =====
    step_start = time.time()
    logger.info(
        "%s ▶ 步骤 3/4: 解密 NCM → filename=%s, data_size=%.1fMB",
        log_prefix,
        raw_file_name,
        len(ncm_data) / 1024 / 1024,
    )

    # 验证魔数（提前失败，方便定位）
    if len(ncm_data) < 8:
        logger.error(
            "%s   文件过短: %d bytes（NCM 文件至少需要 8 字节魔数）",
            log_prefix,
            len(ncm_data),
        )
        raise RuntimeError(f"NCM 文件过短: {len(ncm_data)} bytes")

    from core.ncm_decrypt import NCM_MAGIC
    actual_magic = ncm_data[:8]
    if actual_magic != NCM_MAGIC:
        logger.error(
            "%s   魔数不匹配 → expected=%r, actual=%r",
            log_prefix,
            NCM_MAGIC,
            actual_magic,
        )
        raise ValueError(f"无效的 NCM 文件: 魔数不匹配 (got {actual_magic!r})")
    logger.debug(
        "%s   魔数验证通过 → %r",
        log_prefix,
        actual_magic,
    )

    try:
        result = decrypt_ncm(ncm_data, filename=raw_file_name)
    except ValueError as e:
        logger.error("%s   解密失败（格式错误）: %s", log_prefix, e)
        raise RuntimeError(f"NCM 解密失败（格式错误）: {e}") from e
    except ImportError as e:
        logger.error(
            "%s   解密依赖缺失: %s — 需要安装 pycryptodome",
            log_prefix,
            e,
        )
        raise
    except Exception as e:
        logger.error(
            "%s   解密异常: %s",
            log_prefix,
            e,
        )
        raise RuntimeError(f"NCM 解密异常: {e}") from e

    audio_data = result["audio_data"]
    if not audio_data or len(audio_data) == 0:
        logger.error("%s   解密结果音频数据为空", log_prefix)
        raise RuntimeError("NCM 解密结果音频数据为空")

    # 打印解密元数据
    title = result.get("title", Path(raw_file_name).stem)
    artist = result.get("artist", "")
    album = result.get("album", "")
    audio_format = result.get("format", "mp3")
    has_cover = bool(result.get("cover_data"))

    step_elapsed = time.time() - step_start
    logger.info(
        "%s ✓ 步骤 3/4 完成 (%.1fs)\n"
        "    标题: %s\n"
        "    艺术家: %s\n"
        "    专辑: %s\n"
        "    格式: %s\n"
        "    音频大小: %.1fMB\n"
        "    含封面: %s",
        log_prefix,
        step_elapsed,
        title or "(空)",
        artist or "(空)",
        album or "(空)",
        audio_format,
        len(audio_data) / 1024 / 1024,
        "是" if has_cover else "否",
    )

    # ===== 步骤 4: 写入临时音频文件 =====
    step_start = time.time()
    logger.info(
        "%s ▶ 步骤 4/4: 写入音频文件 → format=%s",
        log_prefix,
        audio_format,
    )

    # 构建文件名
    if artist:
        out_name = f"{artist} - {title}.{audio_format}"
    else:
        out_name = f"{title}.{audio_format}"

    out_name = _sanitize_filename(out_name)
    if not out_name:
        out_name = f"decrypted_{Path(raw_file_name).stem}.{audio_format}"
        logger.warning(
            "%s   文件名清理后为空，使用降级名 → %s",
            log_prefix,
            out_name,
        )

    delete_after_send = bool(cfg.get("delete_after_send", True))
    cache_ttl = int(cfg.get("cache_ttl_seconds", DEFAULT_TEMP_MEDIA_TTL_SECONDS))

    out_path = Path(temp_root) / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 使用 .part 文件避免部分写入
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    try:
        part_path.write_bytes(audio_data)
        logger.debug(
            "%s   .part 文件写入完成 → %s (%.1fMB)",
            log_prefix,
            part_path.name,
            part_path.stat().st_size / 1024 / 1024,
        )
        part_path.replace(out_path)
        logger.debug(
            "%s   重命名为最终文件 → %s",
            log_prefix,
            out_path.name,
        )
    except Exception as e:
        part_path.unlink(missing_ok=True)
        logger.error(
            "%s   文件写入失败 → %s: %s",
            log_prefix,
            out_path,
            e,
        )
        raise RuntimeError(f"写入音频文件失败: {e}") from e

    actual_size = out_path.stat().st_size / 1024 / 1024

    # 写入元数据标签（如果可用）
    meta_info = result.get("meta_info", {})
    if meta_info:
        try:
            from core.ncm_decrypt import _write_metadata

            logger.debug(
                "%s   正在写入音频元数据标签 → artist=%s, title=%s, album=%s",
                log_prefix,
                meta_info.get("artist"),
                meta_info.get("musicName"),
                meta_info.get("album"),
            )
            _write_metadata(str(out_path), meta_info, result.get("cover_data"))
            logger.debug("%s   元数据标签写入完成", log_prefix)
        except Exception as e:
            logger.warning(
                "%s   写入元数据标签失败（不影响使用）: %s",
                log_prefix,
                e,
            )

    if delete_after_send:
        register_temp_media_path(out_path, ttl_seconds=cache_ttl)
        logger.debug(
            "%s   已注册临时文件自动清理 → TTL=%ds",
            log_prefix,
            cache_ttl,
        )

    step_elapsed = time.time() - step_start
    total_elapsed = time.time() - session_start
    logger.info(
        "%s ✓ 步骤 4/4 完成 (%.1fs)\n"
        "    输出文件: %s\n"
        "    实际大小: %.1fMB\n"
        "    ═══ NCM 处理总耗时: %.1fs ═══",
        log_prefix,
        step_elapsed,
        out_name,
        actual_size,
        total_elapsed,
    )

    return out_name, out_path


async def send_decrypted_file(
    bot: Any,
    event: Any,
    file_name: str,
    file_path: Path,
    cfg: dict[str, Any],
) -> None:
    """
    发送解密后的音频文件到群聊或私聊。

    发送顺序：
    1. 先发送文本提示（"NCM 文件解密成功"）
    2. 通过 upload_group_file / upload_private_file 上传音频文件

    Args:
        bot: NoneBot Bot 实例
        event: 消息事件
        file_name: 上传文件名（给用户看到的显示名）
        file_path: 本地文件路径
        cfg: 插件配置
    """
    session_start = time.time()
    send_link_info = bool(cfg.get("send_link_info", True))

    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, PrivateMessageEvent

    from core.bot_messages import get_message as msg

    chat_label = f"group={event.group_id}" if isinstance(event, GroupMessageEvent) else "private"
    log_prefix = f"[NcmParser] send file={file_name} {chat_label}"

    file_size_mb = file_path.stat().st_size / 1024 / 1024 if file_path.exists() else 0

    # ── 发送文本提示 ──
    if send_link_info:
        step_start = time.time()
        info_text = msg("ncm.decrypt_success", title=Path(file_name).stem)
        logger.info(
            "%s  发送解密成功文本提示 → 「%s」",
            log_prefix,
            info_text,
        )
        try:
            await bot.send(event, Message(info_text))
            logger.debug(
                "%s  文本提示发送完成 (%.1fms)",
                log_prefix,
                (time.time() - step_start) * 1000,
            )
        except Exception as e:
            logger.warning(
                "%s  发送文本提示失败（不影响后续上传）: %s",
                log_prefix,
                e,
            )

    # ── 上传文件 ──
    upload_start = time.time()
    logger.info(
        "%s  上传音频文件 → path=%s (%.1fMB), display_name=%s",
        log_prefix,
        file_path.name,
        file_size_mb,
        file_name,
    )

    if isinstance(event, GroupMessageEvent):
        logger.info(
            "%s  → upload_group_file: group_id=%s",
            log_prefix,
            event.group_id,
        )
        try:
            await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=str(file_path),
                name=file_name,
            )
            elapsed = time.time() - upload_start
            logger.info(
                "%s  ✓ 群文件上传完成 → %s (%.1fs)",
                log_prefix,
                file_name,
                elapsed,
            )
        except Exception as e:
            logger.error(
                "%s  ✗ 群文件上传失败 → group=%s: %s",
                log_prefix,
                event.group_id,
                e,
            )
            raise
    elif isinstance(event, PrivateMessageEvent):
        logger.info(
            "%s  → upload_private_file: user_id=%s",
            log_prefix,
            event.user_id,
        )
        try:
            await bot.call_api(
                "upload_private_file",
                user_id=event.user_id,
                file=str(file_path),
                name=file_name,
            )
            elapsed = time.time() - upload_start
            logger.info(
                "%s  ✓ 私聊文件上传完成 → %s (%.1fs)",
                log_prefix,
                file_name,
                elapsed,
            )
        except Exception as e:
            logger.error(
                "%s  ✗ 私聊文件上传失败 → user=%s: %s",
                log_prefix,
                event.user_id,
                e,
            )
            raise
    else:
        # 未知事件类型，降级为语音消息发送
        from nonebot.adapters.onebot.v11 import MessageSegment

        uri = file_path.resolve().as_uri()
        logger.warning(
            "%s  ? 未知事件类型（非群聊/非私聊）, 降级为语音发送 → uri=%s",
            log_prefix,
            uri,
        )
        try:
            await bot.send(event, Message(MessageSegment.record(uri)))
            logger.info(
                "%s  ✓ 语音消息发送完成 (%.1fs)",
                log_prefix,
                time.time() - upload_start,
            )
        except Exception as e:
            logger.error(
                "%s  ✗ 语音消息发送失败: %s",
                log_prefix,
                e,
            )
            raise

    total_elapsed = time.time() - session_start
    logger.info(
        "%s  🎉 发送全部完成 (总耗时 %.1fs)",
        log_prefix,
        total_elapsed,
    )

"""
NCM 文件解密发送模块。

负责：
1. 通过 NapCat get_file API 获取 NCM 文件内容
2. 调用 core.ncm_decrypt 解密
3. 通过 upload_private_file / upload_group_file 发送解密后的音频文件
4. 清理临时文件
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

from core.ncm_decrypt import decrypt_ncm
from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, register_temp_media_path

logger = logging.getLogger("HikariBot.NcmParserSender")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)

# 下载 chunk 大小
CHUNK_SIZE = 256 * 1024


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
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0, read=timeout),
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()

            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                remote_size = int(cl)
                logger.info(
                    "[NcmParser] 下载: Content-Length=%.1fMB, 限制=%dMB",
                    remote_size / 1024 / 1024,
                    max_bytes / 1024 / 1024,
                )
                if remote_size > max_bytes:
                    raise RuntimeError(
                        f"NCM 文件超过大小限制：{remote_size / 1024 / 1024:.1f}MB"
                    )
            else:
                logger.info(
                    "[NcmParser] 下载: Content-Length=未知, 限制=%dMB",
                    max_bytes / 1024 / 1024,
                )

            data = bytearray()
            async for chunk in resp.aiter_bytes():
                if chunk:
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise RuntimeError(
                            f"NCM 文件超过大小限制：{len(data) / 1024 / 1024:.1f}MB"
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
    max_file_mb = max(1, int(cfg.get("max_file_mb", 50)))
    max_bytes = max_file_mb * 1024 * 1024
    timeout = float(cfg.get("api_timeout", 60))
    temp_root = str(cfg.get("temp_root", "/tmp/hikari_bot/ncm"))

    Path(temp_root).mkdir(parents=True, exist_ok=True)

    retry_count = max(0, int(cfg.get("retry_count", 2)))
    retry_delay = max(0.0, float(cfg.get("retry_delay_seconds", 2.0)))

    # 步骤 1: 通过 NapCat get_file 获取文件信息（带重试）
    file_info: dict[str, Any] | None = None
    last_error: Exception | None = None

    for attempt in range(1, retry_count + 2):
        try:
            file_info = await bot.call_api("get_file", file_id=file_id)
            break
        except Exception as e:
            last_error = e
            if attempt <= retry_count:
                logger.warning(
                    "[NcmParser] get_file 失败 (第 %d/%d 次), %.1fs 后重试: %s",
                    attempt,
                    retry_count,
                    retry_delay,
                    e,
                )
                await asyncio.sleep(retry_delay)
            else:
                raise RuntimeError(f"获取 NCM 文件信息失败: {last_error}") from last_error

    if file_info is None:
        raise RuntimeError("获取 NCM 文件信息失败: 返回为空")

    raw_file_name = str(file_info.get("file_name", "unknown.ncm"))
    file_url = str(file_info.get("url") or "")
    local_path = str(file_info.get("file") or "")

    if not file_url and not local_path:
        raise RuntimeError("get_file 未返回 url 或 file 字段")

    logger.info(
        "[NcmParser] 文件信息获取成功 → name=%s, url=%s, size=%s",
        raw_file_name,
        file_url[:120] if file_url else "(使用本地路径)",
        file_info.get("file_size", "未知"),
    )

    # 步骤 2: 下载 NCM 文件内容（带重试）
    ncm_data: bytes | None = None
    last_error = None

    if file_url:
        for attempt in range(1, retry_count + 2):
            try:
                ncm_data = await _download_ncm_content(file_url, max_bytes, timeout)
                break
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = e
                if attempt <= retry_count:
                    logger.warning(
                        "[NcmParser] 下载失败 (第 %d/%d 次), %.1fs 后重试: %s",
                        attempt,
                        retry_count,
                        retry_delay,
                        e,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    raise RuntimeError(f"NCM 文件下载失败: {last_error}") from last_error
            except Exception as e:
                raise RuntimeError(f"NCM 文件下载异常: {e}") from e
    else:
        # 无 URL 时降级读取本地文件（同机部署场景）
        try:
            local = Path(local_path)
            if local.exists():
                ncm_data = local.read_bytes()
                logger.info("[NcmParser] 从本地路径读取 → %s", local_path)
            else:
                raise RuntimeError(f"本地文件不存在: {local_path}")
        except Exception as e:
            raise RuntimeError(f"读取本地 NCM 文件失败: {e}") from e

    if ncm_data is None or len(ncm_data) == 0:
        raise RuntimeError("下载到的 NCM 文件为空")

    logger.info(
        "[NcmParser] 下载完成 → %s (%.1fMB)",
        raw_file_name,
        len(ncm_data) / 1024 / 1024,
    )

    # 步骤 3: 解密 NCM 文件（不重试 — 解密失败通常意味着文件损坏）
    try:
        result = decrypt_ncm(ncm_data, filename=raw_file_name)
    except Exception as e:
        raise RuntimeError(f"NCM 解密失败: {e}") from e

    audio_data = result["audio_data"]
    if not audio_data:
        raise RuntimeError("NCM 解密结果为空")

    # 步骤 4: 写入临时音频文件
    audio_format = result.get("format", "mp3")
    title = result.get("title", Path(raw_file_name).stem) or Path(raw_file_name).stem
    artist = result.get("artist", "")

    if artist:
        out_name = f"{artist} - {title}.{audio_format}"
    else:
        out_name = f"{title}.{audio_format}"

    # 清理文件名中的非法字符
    out_name = "".join(c for c in out_name if c.isprintable() and c not in r'<>:"/\|?*').strip()
    if not out_name:
        out_name = f"decrypted_{Path(raw_file_name).stem}.{audio_format}"

    # 写入临时文件
    delete_after_send = bool(cfg.get("delete_after_send", True))
    cache_ttl = int(cfg.get("cache_ttl_seconds", DEFAULT_TEMP_MEDIA_TTL_SECONDS))

    out_path = Path(temp_root) / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 使用 .part 文件避免部分写入
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    try:
        part_path.write_bytes(audio_data)
        part_path.replace(out_path)
    except Exception as e:
        part_path.unlink(missing_ok=True)
        raise RuntimeError(f"写入音频文件失败: {e}") from e

    # 写入元数据标签（如果可用）
    try:
        from core.ncm_decrypt import _write_metadata

        _write_metadata(str(out_path), result["meta_info"], result.get("cover_data"))
    except Exception as e:
        logger.warning("[NcmParser] 写入元数据标签失败（不影响使用）: %s", e)

    if delete_after_send:
        register_temp_media_path(out_path, ttl_seconds=cache_ttl)

    logger.info(
        "[NcmParser] 解密完成 → %s (%.1fMB, %s, %s)",
        out_name,
        len(audio_data) / 1024 / 1024,
        audio_format,
        title,
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

    Args:
        bot: NoneBot Bot 实例
        event: 消息事件
        file_name: 上传文件名
        file_path: 本地文件路径
        cfg: 插件配置
    """
    send_link_info = bool(cfg.get("send_link_info", True))

    # 先发送提示信息
    if send_link_info:
        from nonebot.adapters.onebot.v11 import Message

        from core.bot_messages import get_message as msg

        info_text = msg("ncm.decrypt_success", title=Path(file_name).stem)
        await bot.send(event, Message(info_text))
        logger.info("[NcmParser] 已发送解密成功提示")

    # 上传文件
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

    file_size_mb = file_path.stat().st_size / 1024 / 1024 if file_path.exists() else 0
    logger.info(
        "[NcmParser] 上传文件 → %s (%.1fMB, name=%s)",
        file_path.name,
        file_size_mb,
        file_name,
    )

    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(file_path),
            name=file_name,
        )
        logger.info("[NcmParser] 群文件上传完成 → %s", file_name)
    elif isinstance(event, PrivateMessageEvent):
        await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=str(file_path),
            name=file_name,
        )
        logger.info("[NcmParser] 私聊文件上传完成 → %s", file_name)
    else:
        # 未知事件类型，降级为语音消息发送
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        uri = file_path.resolve().as_uri()
        logger.warning("[NcmParser] 未知事件类型，降级为语音发送 → %s", file_path.name)
        await bot.send(event, Message(MessageSegment.record(uri)))

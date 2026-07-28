"""
网易云音乐多文件打包模块。

负责：
1. 将下载好的多首歌曲打包为 ZIP 文件
2. 支持设置大小限制和文件数限制，超出时自动拆分
"""

import asyncio
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Optional

from core.temp_media_cleaner import register_temp_media_path

logger = logging.getLogger("HikariBot.NeteasePacker")


def _sanitize_arcname(text: str) -> str:
    """清理 ZIP 内文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


def _format_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读字符串。"""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f}MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes}B"


async def pack_to_zip(
    files: list[tuple[Path, str]],
    zip_name: str,
    output_dir: str | Path = "/tmp/hikari_bot/netease",
    max_files: int = 50,
    max_size_mb: int = 200,
    cache_ttl_seconds: int = 600,
) -> list[Path]:
    """
    将多个文件打包为 ZIP（支持拆分）。

    Args:
        files: 列表，每项为 (源文件路径, ZIP 内文件名)
        zip_name: ZIP 基础文件名（不含扩展名）
        output_dir: 输出目录
        max_files: 单个 ZIP 最大文件数
        max_size_mb: 单个 ZIP 最大大小（MB）
        cache_ttl_seconds: 清理 TTL

    Returns:
        ZIP 文件路径列表（可能因拆分有多个）
    """
    if not files:
        raise ValueError("没有文件需要打包")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    max_size_bytes = max_size_mb * 1024 * 1024
    sanitized_base = _sanitize_arcname(zip_name) or "netease_album"
    zip_paths: list[Path] = []

    # 按 max_files 分片
    total_files = len(files)
    batch_size = max(1, min(max_files, total_files))

    for batch_idx in range(0, total_files, batch_size):
        batch = files[batch_idx:batch_idx + batch_size]
        part_suffix = f".part{batch_idx // batch_size + 1}" if total_files > batch_size else ""
        zip_filename = f"{sanitized_base}{part_suffix}.zip"
        zip_path = output_path / zip_filename

        temp_zip = zip_path.with_suffix(f".zip.tmp.{os.getpid()}")
        current_size = 0

        logger.info(
            "[Netease] 开始打包 → %s (%d 个文件, batch %d)",
            zip_filename, len(batch), batch_idx // batch_size + 1,
        )

        pack_start = time.time()
        try:
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for src_path_raw, arc_name in batch:
                    src_path = Path(src_path_raw) if not isinstance(src_path_raw, Path) else src_path_raw
                    if not src_path.exists():
                        logger.warning("[Netease] 打包跳过: 文件不存在 → %s", src_path.name)
                        continue

                    arc_name_clean = _sanitize_arcname(arc_name)
                    if not arc_name_clean:
                        arc_name_clean = src_path.name

                    file_size = src_path.stat().st_size

                    # 检查是否会超大小限制（不拆分，仅跳过过大文件）
                    if current_size + file_size > max_size_bytes:
                        logger.warning(
                            "[Netease] 打包跳过（超大小限制）→ %s (%s + %s > %dMB)",
                            arc_name_clean,
                            _format_size(current_size),
                            _format_size(file_size),
                            max_size_mb,
                        )
                        continue

                    # 写入 ZIP
                    zf.write(src_path, arc_name_clean)
                    current_size += file_size

            # 完成打包，rename
            if temp_zip.exists() and temp_zip.stat().st_size > 0:
                if zip_path.exists():
                    temp_zip.unlink(missing_ok=True)
                    logger.debug("[Netease] 打包跳过: ZIP 已被其他协程创建 → %s", zip_path.name)
                else:
                    temp_zip.replace(zip_path)
                zip_paths.append(zip_path)
                register_temp_media_path(zip_path, ttl_seconds=cache_ttl_seconds)

                elapsed = time.time() - pack_start
                logger.info(
                    "[Netease] 打包完成 (%.1fs) → %s (%s, %d 个文件)",
                    elapsed, zip_path.name,
                    _format_size(zip_path.stat().st_size),
                    len(batch),
                )
            else:
                temp_zip.unlink(missing_ok=True)
                logger.warning("[Netease] 打包结果为空 → %s", zip_filename)
                continue

        except Exception:
            temp_zip.unlink(missing_ok=True)
            logger.exception("[Netease] 打包失败 → %s", zip_filename)
            raise

        # 短暂等待避免同一目录写冲突
        await asyncio.sleep(0.1)

    if not zip_paths:
        raise RuntimeError("打包失败：未生成任何有效的 ZIP 文件")

    return zip_paths

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from plugins import sticker_library

from .utils import _safe_pack_name

def _archive_download_name(pack_name: str) -> str:
    safe_name = _safe_pack_name(pack_name) or "stickers"
    return f"{safe_name}.7z"


def _find_7z_command() -> str | None:
    for command in ("7z", "7zz", "7za"):
        path = shutil.which(command)
        if path:
            return path
    return None


def _stage_archive_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _create_pack_archive(pack_name: str) -> Path:
    seven_zip = _find_7z_command()
    if not seven_zip:
        raise RuntimeError("服务器缺少 7-Zip 命令行工具，请安装 7z、7zz 或 7za 后重试。")
    safe_name, archive_files = sticker_library.get_pack_archive_files(pack_name)
    if not archive_files:
        raise ValueError("这个贴纸包里没有可下载的贴纸文件。")

    fd, archive_name = tempfile.mkstemp(prefix=f"hikari_{safe_name}_", suffix=".7z")
    os.close(fd)
    archive_path = Path(archive_name)
    archive_path.unlink(missing_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"hikari_{safe_name}_archive_"))
    try:
        for path, arcname in archive_files:
            _stage_archive_file(path, staging_dir / arcname)
        command = [
            seven_zip,
            "a",
            "-t7z",
            "-mx=5",
            "-mmt=on",
            str(archive_path),
            ".",
        ]
        result = subprocess.run(
            command,
            cwd=staging_dir,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip().splitlines()
            detail = message[-1] if message else f"退出码 {result.returncode}"
            raise RuntimeError(f"7-Zip 压缩失败：{detail}")
        if not archive_path.is_file() or archive_path.stat().st_size <= 0:
            raise RuntimeError("7-Zip 未生成有效的压缩包。")
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return archive_path


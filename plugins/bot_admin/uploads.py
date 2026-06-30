from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from plugins import sticker_library
from plugins import voice_library
from plugins.media_transcoder import TranscodeError, ensure_sticker_gif
from plugins.tg_sticker_parser import find_saved_gifs, parse_sticker_set_to_gifs, save_gifs_to_pack
from plugins.tg_sticker_parser.config import get_config as get_tg_config
from plugins.tg_sticker_parser.tg_api import extract_sticker_set_names

from .constants import ALLOWED_EXTS, VOICE_ALLOWED_EXTS
from .stickers import _register_trigger, _voice_state
from .utils import _hash_content, _safe_filename, _temp_root

logger = logging.getLogger("HikariBot.BotAdmin")
_upload_jobs: dict[str, dict[str, Any]] = {}
_upload_jobs_lock = threading.Lock()

def _new_upload_job(pack_name: str, total: int) -> dict[str, Any]:
    now = time.time()
    job = {
        "id": uuid.uuid4().hex,
        "status": "queued",
        "pack": pack_name,
        "total": total,
        "processed": 0,
        "saved": 0,
        "reused": 0,
        "failed": [],
        "current": "",
        "message": "等待处理...",
        "created_at": now,
        "updated_at": now,
    }
    with _upload_jobs_lock:
        _upload_jobs[job["id"]] = job
    return job.copy()


def _update_upload_job(job_id: str, **updates: Any) -> None:
    with _upload_jobs_lock:
        job = _upload_jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _get_upload_job(job_id: str) -> dict[str, Any] | None:
    with _upload_jobs_lock:
        job = _upload_jobs.get(job_id)
        return job.copy() if job else None


def _process_upload_files(
    pack_name: str,
    keyword: str,
    file_infos: list[dict[str, Any]],
    job_id: str | None = None,
) -> dict[str, Any]:
    _register_trigger(pack_name, keyword)

    temp_dir = _temp_root()
    temp_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    reused: list[str] = []
    failed: list[str] = []

    if job_id:
        _update_upload_job(job_id, status="running", message="开始处理...")

    for index, file_info in enumerate(file_infos, start=1):
        filename = _safe_filename(str(file_info["filename"]))
        if job_id:
            _update_upload_job(
                job_id,
                current=filename,
                processed=index - 1,
                saved=len(saved),
                reused=len(reused),
                failed=failed.copy(),
                message=f"正在处理 {index}/{len(file_infos)}：{filename}",
            )

        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTS:
            failed.append(f"{filename}：不支持的文件格式 {suffix or '(无后缀)'}")
            if job_id:
                _update_upload_job(job_id, processed=index, failed=failed.copy())
            continue

        content = file_info["content"]
        content_hash = _hash_content(content)

        temp_path: Path | None = None
        temp_gif_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix=f"{content_hash[:16]}_",
                dir=temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_path = Path(temp_file.name)

            temp_gif_path = temp_dir / f"{content_hash[:16]}_{uuid.uuid4().hex}.gif"
            asyncio.run(ensure_sticker_gif(temp_path, temp_gif_path))
            saved_path, created = sticker_library.save_gif_to_pack(
                pack_name,
                temp_gif_path,
                source="upload",
                original_name=filename,
            )
            if created:
                saved.append(saved_path.name)
            else:
                reused.append(saved_path.name)
        except TranscodeError as e:
            failed.append(f"{filename}：转 GIF 失败：{e}")
        except Exception as e:
            logger.exception("贴纸上传处理失败: %s", e)
            failed.append(f"{filename}：处理失败，请检查服务日志")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if temp_gif_path is not None:
                temp_gif_path.unlink(missing_ok=True)

        if job_id:
            _update_upload_job(
                job_id,
                processed=index,
                saved=len(saved),
                reused=len(reused),
                failed=failed.copy(),
            )

    details = [f"上传完成：{pack_name}"]
    if saved:
        details.append(f"新增 {len(saved)} 个")
    if reused:
        details.append(f"复用 {len(reused)} 个")
    if failed:
        details.append(f"失败 {len(failed)} 个：{'；'.join(failed[:5])}")
        if len(failed) > 5:
            details.append(f"另有 {len(failed) - 5} 个失败项已省略")

    status = "failed" if failed and not saved and not reused else "done"
    summary = "，".join(details)
    if job_id:
        _update_upload_job(
            job_id,
            status=status,
            current="",
            processed=len(file_infos),
            saved=len(saved),
            reused=len(reused),
            failed=failed.copy(),
            message=summary,
        )

    return {
        "status": status,
        "message": summary,
        "saved": saved,
        "reused": reused,
        "failed": failed,
    }


def _process_voice_uploads(
    display_name: str,
    keyword: str,
    file_infos: list[dict[str, Any]],
) -> dict[str, Any]:
    temp_dir = _temp_root() / "voices"
    temp_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    reused: list[str] = []
    failed: list[str] = []

    for file_info in file_infos:
        filename = _safe_filename(str(file_info["filename"]))
        suffix = Path(filename).suffix.lower()
        if suffix not in VOICE_ALLOWED_EXTS:
            failed.append(f"{filename}：不支持的语音格式 {suffix or '(无后缀)'}")
            continue

        temp_path: Path | None = None
        try:
            content = file_info["content"]
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix=f"voice_{uuid.uuid4().hex}_",
                dir=temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_path = Path(temp_file.name)

            voice_name = display_name if len(file_infos) == 1 else Path(filename).stem
            saved_path, created = voice_library.save_voice_file(
                temp_path,
                display_name=voice_name,
                keywords=keyword,
                original_name=filename,
            )
            if created:
                saved.append(saved_path.name)
            else:
                reused.append(saved_path.name)
        except Exception as e:
            logger.exception("语音上传处理失败: %s", e)
            failed.append(f"{filename}：处理失败，请检查服务日志")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    details = ["语音上传完成"]
    if saved:
        details.append(f"新增 {len(saved)} 个")
    if reused:
        details.append(f"复用 {len(reused)} 个")
    if failed:
        details.append(f"失败 {len(failed)} 个：{'；'.join(failed[:5])}")
        if len(failed) > 5:
            details.append(f"另有 {len(failed) - 5} 个失败项已省略")

    return {
        "status": "failed" if failed and not saved and not reused else "done",
        "message": "，".join(details),
        "saved": saved,
        "reused": reused,
        "failed": failed,
        "state": _voice_state(),
    }


async def _process_tg_sticker_link_async(
    link: str,
    pack_name: str,
    keyword: str,
    refresh: bool,
    job_id: str,
) -> None:
    set_names = extract_sticker_set_names(link)
    if not set_names:
        _update_upload_job(
            job_id,
            status="failed",
            message="没有识别到 Telegram 贴纸包链接。",
        )
        return

    set_name = set_names[0]
    target_pack = pack_name or set_name
    cfg = get_tg_config()
    _update_upload_job(
        job_id,
        status="running",
        pack=target_pack,
        current=set_name,
        message=f"准备导入 Telegram 贴纸包：{set_name}",
    )

    cached_pack = target_pack
    cached_gifs = find_saved_gifs(target_pack)
    if not cached_gifs:
        cached_pack = set_name
        cached_gifs = find_saved_gifs(set_name)
    if cached_gifs and not refresh:
        saved_paths = cached_gifs if cached_pack == target_pack else save_gifs_to_pack(target_pack, cached_gifs)
        _register_trigger(target_pack, keyword)
        _update_upload_job(
            job_id,
            status="done",
            total=len(cached_gifs),
            processed=len(cached_gifs),
            saved=len(saved_paths),
            reused=len(cached_gifs),
            current="",
            message=f"已从本地缓存导入：{target_pack}，共 {len(saved_paths)} 个 GIF。",
        )
        return

    def report_progress(progress: dict[str, Any]) -> None:
        total = int(progress.get("total") or 0)
        processed = int(progress.get("processed") or 0)
        _update_upload_job(
            job_id,
            total=total,
            processed=processed,
            current=str(progress.get("title") or set_name),
            message=str(progress.get("message") or "正在导入 Telegram 贴纸包..."),
        )

    try:
        result = await parse_sticker_set_to_gifs(
            bot=None,
            event=None,
            set_name=set_name,
            cfg=cfg,
            progress_callback=report_progress,
        )
        gif_paths = result.get("gif_paths") or []
        total_count = int(result.get("total_count") or len(gif_paths))
        failed_count = int(result.get("failed_count") or 0)
        failed_items = [str(item) for item in result.get("failed_items") or []]

        if not gif_paths:
            _update_upload_job(
                job_id,
                status="failed",
                total=total_count,
                processed=total_count,
                failed=[f"{set_name}：没有成功转换出可保存的 GIF"],
                current="",
                message="没有成功转换出可保存的 GIF。",
            )
            return

        saved_paths = save_gifs_to_pack(target_pack, gif_paths)
        _register_trigger(target_pack, keyword)
        message = f"Telegram 贴纸包导入完成：{target_pack}，新增/覆盖 {len(saved_paths)} 个"
        if failed_count:
            message += f"，失败 {failed_count} 个"
            if failed_items:
                message += f"：{'；'.join(failed_items[:3])}"
                if len(failed_items) > 3:
                    message += f"；另有 {len(failed_items) - 3} 个失败项已省略"
        _update_upload_job(
            job_id,
            status="done",
            total=total_count,
            processed=total_count,
            saved=len(saved_paths),
            reused=0,
            failed=failed_items if failed_items else ([] if failed_count <= 0 else [f"转换失败 {failed_count} 个"]),
            current="",
            message=message,
        )
    except Exception as e:
        logger.exception("Telegram 贴纸包导入失败: %s", e)
        _update_upload_job(
            job_id,
            status="failed",
            current="",
            failed=[str(e)],
            message=f"Telegram 贴纸包导入失败：{e}",
        )


def _process_tg_sticker_link(link: str, pack_name: str, keyword: str, refresh: bool, job_id: str) -> None:
    asyncio.run(_process_tg_sticker_link_async(link, pack_name, keyword, refresh, job_id))


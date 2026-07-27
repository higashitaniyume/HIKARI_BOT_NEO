"""Sticker/voice/inbox handler mixin — upload, delete, keyword management.

Imports helpers from sibling modules; calls base methods (self._send_json,
self._send_sticker, self._send_pack_archive, self._send_inbox_item,
self._send_voice_file, self._parse_multipart_form, self._query_params, etc.)
on the combined BotAdminHandler via MRO.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from urllib.parse import unquote

from plugins import sticker_inbox
from plugins import sticker_library
from plugins import voice_library
from plugins.tg_sticker_parser.tg_api import extract_sticker_set_names

from .stickers import (
    _add_trigger_keyword,
    _inbox_state,
    _pack_detail_state,
    _pack_state,
    _remove_trigger_keyword,
    _split_keywords,
    _voice_state,
)
from .uploads import (
    _get_upload_job,
    _new_upload_job,
    _process_tg_sticker_link,
    _process_upload_files,
    _process_voice_uploads,
    _update_upload_job,
)
from .utils import _safe_pack_name, _safe_voice_name
from .constants import MAX_UPLOAD_FILES, MAX_VOICE_UPLOAD_FILES

logger = logging.getLogger("HikariBot.BotAdmin")


class StickerHandlerMixin:
    """Mixin providing sticker/voice/inbox handlers (GET, POST, DELETE)."""

    # ---- GET API path parameters --------------------------------------------

    def _handle_pack_download(self, name: str) -> None:
        self._send_pack_archive(unquote(name))

    def _handle_pack_detail(self, name: str) -> None:
        try:
            self._send_json(_pack_detail_state(unquote(name)))
        except ValueError as e:
            self._send_json({"error": str(e)}, 404)
        except Exception as e:
            logger.exception("读取贴纸包详情失败: %s", e)
            self._send_json({"error": "读取贴纸包详情失败，请检查服务日志。"}, 500)

    def _handle_sticker(self, sticker_id: str) -> None:
        self._send_sticker(sticker_id)

    def _handle_upload_status(self, job_id: str) -> None:
        job = _get_upload_job(job_id)
        if job is None:
            self._send_json({"error": "上传任务不存在。"}, 404)
            return
        self._send_json(job)

    def _handle_inbox_image(self, item_id: str) -> None:
        self._send_inbox_item(item_id)

    def _handle_voice_file(self, voice_id: str) -> None:
        self._send_voice_file(voice_id)

    # ---- POST API exact paths -----------------------------------------------

    def _handle_voice_keywords_add(self) -> None:
        try:
            data = self._read_json_body()
            voice_id = Path(str(data.get("voice", ""))).name
            keyword = str(data.get("keyword", "")).strip()
            if not voice_id or not voice_library.split_keywords(keyword):
                raise ValueError("语音和关键词都不能为空。")
            voice_library.add_keywords(voice_id, keyword)
            self._send_json(_voice_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("新增语音关键词失败: %s", e)
            self._send_json({"error": "新增语音关键词失败，请检查服务日志。"}, 500)

    def _handle_voices_upload(self) -> None:
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return

        display_name = _safe_voice_name(fields.get("voice_name", ""))
        keyword = fields.get("voice_keyword", "").strip()
        file_infos = [file_info for file_info in files.get("voice_file", []) if file_info.get("filename")]
        if not file_infos:
            self._send_json({"error": "请选择要上传的语音文件。"}, 400)
            return
        if len(file_infos) > MAX_VOICE_UPLOAD_FILES:
            self._send_json({"error": f"一次最多上传 {MAX_VOICE_UPLOAD_FILES} 个语音文件。"}, 400)
            return

        result = _process_voice_uploads(display_name, keyword, file_infos)
        status = 400 if result["status"] == "failed" else 200
        self._send_json(result, status)

    def _handle_keywords_add(self) -> None:
        try:
            data = self._read_json_body()
            pack_name = _safe_pack_name(str(data.get("pack", "")))
            keyword = str(data.get("keyword", "")).strip()
            if not pack_name or not _split_keywords(keyword):
                raise ValueError("贴纸包和关键词都不能为空。")
            _add_trigger_keyword(pack_name, keyword)
            self._send_json(_pack_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("新增贴纸关键词失败: %s", e)
            self._send_json({"error": "新增贴纸关键词失败，请检查服务日志。"}, 500)

    def _handle_pack_stickers_delete(self) -> None:
        try:
            data = self._read_json_body()
            pack_name = _safe_pack_name(str(data.get("pack", "")))
            sticker_ids = [str(sticker_id) for sticker_id in data.get("stickers") or [] if str(sticker_id).strip()]
            result = sticker_library.remove_stickers_from_pack(pack_name, sticker_ids)
            payload = _pack_state()
            payload["result"] = result
            payload["pack_detail"] = sticker_library.get_pack_detail(pack_name)
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除贴纸失败: %s", e)
            self._send_json({"error": "删除贴纸失败，请检查服务日志。"}, 500)

    def _handle_pack_stickers_move(self) -> None:
        try:
            data = self._read_json_body()
            source_pack = _safe_pack_name(str(data.get("source_pack", "")))
            target_pack = _safe_pack_name(str(data.get("target_pack", "")))
            sticker_ids = [str(sticker_id) for sticker_id in data.get("stickers") or [] if str(sticker_id).strip()]
            result = sticker_library.move_stickers_between_packs(source_pack, target_pack, sticker_ids)
            payload = _pack_state()
            payload["result"] = result
            payload["pack_detail"] = sticker_library.get_pack_detail(source_pack)
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("移动贴纸失败: %s", e)
            self._send_json({"error": "移动贴纸失败，请检查服务日志。"}, 500)

    def _handle_tg_stickers(self) -> None:
        try:
            data = self._read_json_body()
            link = str(data.get("url", "")).strip()
            set_names = extract_sticker_set_names(link)
            if not set_names:
                raise ValueError("请输入有效的 Telegram 贴纸包链接。")

            pack_name = _safe_pack_name(str(data.get("pack", "")))
            target_pack = pack_name or set_names[0]
            keyword = str(data.get("keyword", "")).strip()
            refresh = bool(data.get("refresh", False))
            job = _new_upload_job(target_pack, 0)
            _update_upload_job(
                job["id"],
                status="queued",
                current=set_names[0],
                message=f"已创建 Telegram 导入任务：{set_names[0]}",
            )
            thread = threading.Thread(
                target=_process_tg_sticker_link,
                args=(link, target_pack, keyword, refresh, job["id"]),
                name=f"StickerTgImport-{job['id'][:8]}",
                daemon=True,
            )
            thread.start()
            self._send_json(_get_upload_job(job["id"]) or job, 202)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("创建 Telegram 贴纸导入任务失败: %s", e)
            self._send_json({"error": "创建 Telegram 贴纸导入任务失败，请检查服务日志。"}, 500)

    def _handle_inbox_assign(self) -> None:
        try:
            data = self._read_json_body()
            item_ids = [str(item_id) for item_id in data.get("ids") or [] if str(item_id).strip()]
            pack_name = _safe_pack_name(str(data.get("pack", "")))
            keyword = str(data.get("keyword", "")).strip()
            if not item_ids:
                raise ValueError("请选择要整理的表情。")
            if not pack_name:
                raise ValueError("请选择或输入目标贴纸包。")
            result = sticker_inbox.assign_items(item_ids, pack_name, keyword)
            self._send_json({"result": result, "inbox": _inbox_state(), "state": _pack_state()})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("整理收集箱贴纸失败: %s", e)
            self._send_json({"error": "整理收集箱贴纸失败，请检查服务日志。"}, 500)

    def _handle_inbox_delete(self) -> None:
        try:
            data = self._read_json_body()
            item_ids = [str(item_id) for item_id in data.get("ids") or [] if str(item_id).strip()]
            if not item_ids:
                raise ValueError("请选择要删除的表情。")
            removed = sticker_inbox.delete_items(item_ids)
            self._send_json({"removed": removed, "inbox": _inbox_state()})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除收集箱贴纸失败: %s", e)
            self._send_json({"error": "删除收集箱贴纸失败，请检查服务日志。"}, 500)

    # ---- POST multipart upload (end-of-chain) -------------------------------

    def _handle_upload_html(self) -> None:
        """HTML form upload (synchronous)."""
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_html(_html_page(str(e)), 400)
            return

        existing_pack = _safe_pack_name(fields.get("existing_pack", ""))
        new_pack = _safe_pack_name(fields.get("new_pack", ""))
        keyword = fields.get("keyword", "").strip()
        pack_name = existing_pack or new_pack

        if not pack_name:
            self._send_html(_html_page("请先选择已有贴纸包，或输入新贴纸包名称。"), 400)
            return

        file_infos = [file_info for file_info in files.get("file", []) if file_info.get("filename")]
        if not file_infos:
            self._send_html(_html_page("请选择要上传的文件。"), 400)
            return

        if len(file_infos) > MAX_UPLOAD_FILES:
            self._send_html(_html_page(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。"), 400)
            return

        result = _process_upload_files(pack_name, keyword, file_infos)
        status = 400 if result["status"] == "failed" else 200
        self._send_html(_html_page(result["message"]), status)

    def _handle_api_uploads(self) -> None:
        """JSON API upload (async background job)."""
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return

        existing_pack = _safe_pack_name(fields.get("existing_pack", ""))
        new_pack = _safe_pack_name(fields.get("new_pack", ""))
        keyword = fields.get("keyword", "").strip()
        pack_name = existing_pack or new_pack

        if not pack_name:
            self._send_json({"error": "请先选择已有贴纸包，或输入新贴纸包名称。"}, 400)
            return

        file_infos = [file_info for file_info in files.get("file", []) if file_info.get("filename")]
        if not file_infos:
            self._send_json({"error": "请选择要上传的文件。"}, 400)
            return

        if len(file_infos) > MAX_UPLOAD_FILES:
            self._send_json({"error": f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。"}, 400)
            return

        job = _new_upload_job(pack_name, len(file_infos))
        thread = threading.Thread(
            target=_process_upload_files,
            args=(pack_name, keyword, file_infos, job["id"]),
            name=f"StickerUpload-{job['id'][:8]}",
            daemon=True,
        )
        thread.start()
        self._send_json(job, 202)

    # ---- DELETE -------------------------------------------------------------

    def _handle_packs_delete(self) -> None:
        pack_name = _safe_pack_name(self._query_params.get("pack", [""])[0])
        if not pack_name:
            self._send_json({"error": "贴纸包不能为空。"}, 400)
            return

        try:
            result = sticker_library.delete_pack(pack_name)
            payload = _pack_state()
            payload["result"] = result
            if not result.get("deleted"):
                payload["error"] = "没有找到这个贴纸包。"
                self._send_json(payload, 404)
                return
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除贴纸包失败: %s", e)
            self._send_json({"error": "删除贴纸包失败，请检查服务日志。"}, 500)

    def _handle_voices_delete(self) -> None:
        voice_id = Path(self._query_params.get("voice", [""])[0]).name
        if not voice_id:
            self._send_json({"error": "语音不能为空。"}, 400)
            return

        try:
            result = voice_library.delete_voice(voice_id)
            payload = _voice_state()
            payload["result"] = result
            if not result.get("deleted"):
                payload["error"] = "没有找到这个语音。"
                self._send_json(payload, 404)
                return
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除语音失败: %s", e)
            self._send_json({"error": "删除语音失败，请检查服务日志。"}, 500)

    def _handle_voice_keywords_delete(self) -> None:
        voice_id = Path(self._query_params.get("voice", [""])[0]).name
        keyword = self._query_params.get("keyword", [""])[0].strip()
        if not voice_id or not keyword:
            self._send_json({"error": "语音和关键词都不能为空。"}, 400)
            return

        removed = voice_library.remove_keyword(voice_id, keyword)
        status = 200 if removed else 404
        payload = _voice_state()
        if not removed:
            payload["error"] = "没有找到这个关键词关联。"
        self._send_json(payload, status)

    def _handle_keywords_delete(self) -> None:
        pack_name = _safe_pack_name(self._query_params.get("pack", [""])[0])
        keyword = self._query_params.get("keyword", [""])[0].strip()
        if not pack_name or not keyword:
            self._send_json({"error": "贴纸包和关键词都不能为空。"}, 400)
            return

        removed = _remove_trigger_keyword(pack_name, keyword)
        status = 200 if removed else 404
        payload = _pack_state()
        if not removed:
            payload["error"] = "没有找到这个关键词关联。"
        self._send_json(payload, status)

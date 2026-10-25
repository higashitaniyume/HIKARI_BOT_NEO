"""QZone handler mixin — web admin endpoints for the qzone plugin."""

from __future__ import annotations

import logging

from .qzone_admin import (
    check_comments_now,
    generate_draft,
    publish_text,
    qzone_config_state,
    save_qzone_config,
)

logger = logging.getLogger("HikariBot.BotAdmin")


class QzoneHandlerMixin:
    """Mixin providing QZone config / draft / publish handlers."""

    def _handle_qzone_config(self) -> None:
        try:
            self._send_json(qzone_config_state())
        except Exception as e:
            logger.exception("读取空间设置失败: %s", e)
            self._send_json({"error": "读取空间设置失败，请检查服务日志。"}, 500)

    def _handle_qzone_config_save(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json(save_qzone_config(data))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存空间设置失败: %s", e)
            self._send_json({"error": "保存空间设置失败，请检查服务日志。"}, 500)

    def _handle_qzone_generate(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json({"result": generate_draft(data)})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except TimeoutError as e:
            self._send_json({"error": str(e)}, 504)
        except RuntimeError as e:
            self._send_json({"error": str(e) or "空间操作失败。", "detail": type(e).__name__}, 409)
        except Exception as e:
            logger.exception("生成空间草稿失败: %s", e)
            self._send_json({"error": "生成空间草稿失败，请检查服务日志。"}, 500)

    def _handle_qzone_publish(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json({"result": publish_text(data), "message": "已发布到 QQ 空间。"})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except TimeoutError as e:
            self._send_json({"error": str(e)}, 504)
        except RuntimeError as e:
            self._send_json({"error": str(e) or "空间操作失败。", "detail": type(e).__name__}, 409)
        except Exception as e:
            logger.exception("发布空间说说失败: %s", e)
            self._send_json({"error": "发布空间说说失败，请检查服务日志。"}, 500)

    def _handle_qzone_check_comments(self) -> None:
        try:
            self._send_json(check_comments_now())
        except TimeoutError as e:
            self._send_json({"error": str(e)}, 504)
        except RuntimeError as e:
            self._send_json({"error": str(e) or "检查评论失败。", "detail": type(e).__name__}, 409)
        except Exception as e:
            logger.exception("检查空间评论失败: %s", e)
            self._send_json({"error": "检查空间评论失败，请检查服务日志。"}, 500)

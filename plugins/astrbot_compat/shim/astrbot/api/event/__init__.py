"""AstrBot AstrMessageEvent shim — wraps OneBot V11 MessageEvent."""

from __future__ import annotations

from typing import Any

from astrbot.api.message_components import BaseMessageComponent, Image, Plain
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
)


class AstrMessageEvent:
    """Shim for AstrBot's AstrMessageEvent.

    Wraps a OneBot V11 MessageEvent so astrbot plugins can interact with it.
    Created by our Loader when dispatching to plugin handlers.
    """

    def __init__(
        self,
        message_str: str,
        message_obj: Any = None,
        platform_meta: Any = None,
        session_id: str = "",
        bot=None,
        event=None,
    ):
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.session_id = session_id
        self.role: str = "member"
        self.is_wake: bool = False
        self.is_at_or_wake_command: bool = False

        # Internal refs for bridge sending
        self._bot = bot
        self._event = event

        # Result tracking
        self._result: MessageEventResult | None = None
        self._force_stopped: bool = False
        self._extras: dict[str, Any] = {}

    # --- Identity ---

    def get_platform_name(self) -> str:
        return getattr(self.platform_meta, "name", "qq") if self.platform_meta else "qq"

    def get_sender_id(self) -> str:
        if self._event and hasattr(self._event, "sender") and self._event.sender:
            return str(self._event.sender.user_id)
        return ""

    def get_sender_name(self) -> str:
        if self._event and hasattr(self._event, "sender") and self._event.sender:
            return self._event.sender.nickname or ""
        return ""

    def get_session_id(self) -> str:
        return self.session_id

    def get_group_id(self) -> str:
        if self._event and hasattr(self._event, "group_id"):
            return str(self._event.group_id)
        return ""

    def get_self_id(self) -> str:
        if self._event:
            return str(getattr(self._event, "self_id", ""))
        return ""

    def get_message_str(self) -> str:
        return self.message_str

    def get_message_type(self) -> str:
        from nonebot.adapters.onebot.v11 import GroupMessageEvent
        if isinstance(self._event, GroupMessageEvent):
            return "group_message"
        return "friend_message"

    def is_private_chat(self) -> bool:
        from nonebot.adapters.onebot.v11 import GroupMessageEvent
        return not isinstance(self._event, GroupMessageEvent)

    def is_admin(self) -> bool:
        return self.role == "admin"

    def is_wake_up(self) -> bool:
        return self.is_wake

    # --- Extra data ---

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_extra(self, key: str | None = None, default: Any = None) -> Any:
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    # --- Result factories ---

    def make_result(self) -> MessageEventResult:
        return MessageEventResult()

    def plain_result(self, text: str) -> MessageEventResult:
        return MessageEventResult().message(text)

    def image_result(self, url_or_path: str) -> MessageEventResult:
        result = MessageEventResult()
        if url_or_path.startswith(("http://", "https://")):
            result.url_image(url_or_path)
        else:
            result.file_image(url_or_path)
        return result

    def chain_result(self, chain: list[BaseMessageComponent]) -> MessageEventResult:
        return MessageEventResult(chain=list(chain))

    # --- Event control ---

    def set_result(self, result: MessageEventResult | str) -> None:
        if isinstance(result, str):
            result = MessageEventResult().message(result)
        self._result = result

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def clear_result(self) -> None:
        self._result = None

    def stop_event(self) -> None:
        self._force_stopped = True
        if self._result:
            self._result.stop_event()
        else:
            self._result = MessageEventResult().stop_event()

    def continue_event(self) -> None:
        self._force_stopped = False
        if self._result:
            self._result.continue_event()
        else:
            self._result = MessageEventResult().continue_event()

    def is_stopped(self) -> bool:
        if self._force_stopped:
            return True
        return self._result is not None and self._result.is_stopped()

    # --- Send ---

    async def send(self, message: str | MessageChain | list[BaseMessageComponent]) -> None:
        """Send a message via the bridged bot."""
        if self._bot is None or self._event is None:
            return
        from plugins.astrbot_compat.loader import convert_chain_to_onebot
        if isinstance(message, str):
            await self._bot.send(self._event, message)
        elif isinstance(message, MessageChain):
            ob_msg = convert_chain_to_onebot(message)
            await self._bot.send(self._event, ob_msg)
        elif isinstance(message, list):
            tmp = MessageChain(chain=message)
            ob_msg = convert_chain_to_onebot(tmp)
            await self._bot.send(self._event, ob_msg)

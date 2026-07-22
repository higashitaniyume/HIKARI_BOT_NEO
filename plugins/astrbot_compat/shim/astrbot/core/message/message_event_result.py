"""AstrBot MessageEventResult shim — chain builder for plugin responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import auto, Enum
from typing import AsyncGenerator

from astrbot.api.message_components import (
    At,
    AtAll,
    BaseMessageComponent,
    Image,
    Plain,
)


class EventResultType(Enum):
    CONTINUE = auto()
    STOP = auto()


class ResultContentType(Enum):
    LLM_RESULT = auto()
    AGENT_RUNNER_ERROR = auto()
    GENERAL_RESULT = auto()
    STREAMING_RESULT = auto()
    STREAMING_FINISH = auto()


@dataclass
class MessageChain:
    """A message component chain that can be built fluently."""

    chain: list[BaseMessageComponent] = field(default_factory=list)
    use_t2i_: bool | None = None
    use_markdown_: bool | None = None
    type: str | None = None

    def derive(self, chain: list[BaseMessageComponent] | None = None) -> "MessageChain":
        new = MessageChain(chain=chain if chain is not None else [])
        new.use_t2i_ = self.use_t2i_
        new.use_markdown_ = self.use_markdown_
        new.type = self.type
        return new

    def message(self, message: str) -> "MessageChain":
        self.chain.append(Plain(message))
        return self

    # Deprecated alias kept for compatibility
    def error(self, message: str) -> "MessageChain":
        return self.message(message)

    def at(self, name: str, qq: str | int) -> "MessageChain":
        self.chain.append(At(name=name, qq=qq))
        return self

    def at_all(self) -> "MessageChain":
        self.chain.append(AtAll())
        return self

    def url_image(self, url: str) -> "MessageChain":
        self.chain.append(Image.fromURL(url))
        return self

    def file_image(self, path: str) -> "MessageChain":
        self.chain.append(Image.fromFileSystem(path))
        return self

    def base64_image(self, b64_str: str) -> "MessageChain":
        self.chain.append(Image.fromBase64(b64_str))
        return self

    def use_t2i(self, use: bool = True) -> "MessageChain":
        self.use_t2i_ = use
        return self

    def use_markdown(self, use: bool | None = True) -> "MessageChain":
        self.use_markdown_ = use
        return self

    def get_plain_text(self, with_other_comps_mark: bool = False) -> str:
        texts: list[str] = []
        for comp in self.chain:
            if isinstance(comp, Plain):
                texts.append(comp.text)
            elif with_other_comps_mark:
                texts.append(f"[{comp.__class__.__name__}]")
        return " ".join(texts)

    def squash_plain(self) -> "MessageChain | None":
        if not self.chain:
            return None
        new_chain: list[BaseMessageComponent] = []
        first_plain = None
        plain_texts: list[str] = []
        for comp in self.chain:
            if isinstance(comp, Plain):
                if first_plain is None:
                    first_plain = comp
                    new_chain.append(comp)
                plain_texts.append(comp.text)
            else:
                new_chain.append(comp)
        if first_plain is not None:
            first_plain.text = "".join(plain_texts)
        self.chain = new_chain
        return self


@dataclass
class MessageEventResult(MessageChain):
    """Result of a plugin handler — can control event propagation."""

    result_type: EventResultType | None = field(
        default_factory=lambda: EventResultType.CONTINUE
    )
    result_content_type: ResultContentType | None = field(
        default_factory=lambda: ResultContentType.GENERAL_RESULT
    )
    async_stream: AsyncGenerator | None = None

    def stop_event(self) -> "MessageEventResult":
        self.result_type = EventResultType.STOP
        return self

    def continue_event(self) -> "MessageEventResult":
        self.result_type = EventResultType.CONTINUE
        return self

    def is_stopped(self) -> bool:
        return self.result_type == EventResultType.STOP

    def set_async_stream(
        self, stream: AsyncGenerator
    ) -> "MessageEventResult":
        self.async_stream = stream
        return self

    def set_result_content_type(
        self, typ: ResultContentType
    ) -> "MessageEventResult":
        self.result_content_type = typ
        return self

    def is_llm_result(self) -> bool:
        return self.result_content_type == ResultContentType.LLM_RESULT

    def is_model_result(self) -> bool:
        return self.result_content_type in (
            ResultContentType.LLM_RESULT,
            ResultContentType.AGENT_RUNNER_ERROR,
        )


CommandResult = MessageEventResult

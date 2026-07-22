"""AstrBot message components shim — data classes for message segments.

NOTE: We define ``__init__`` manually on common components to avoid
dataclass inheritance issues where parent fields (``type``) would
consume positional arguments meant for child fields.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar


class ComponentType(Enum):
    PLAIN = "plain"
    IMAGE = "image"
    RECORD = "record"
    VIDEO = "video"
    FILE = "file"
    FACE = "face"
    AT = "at"
    AT_ALL = "at_all"
    REPLY = "reply"
    POKE = "poke"
    FORWARD = "forward"
    NODE = "node"
    NODES = "nodes"
    SHARE = "share"
    CONTACT = "contact"
    LOCATION = "location"
    MUSIC = "music"
    JSON = "json"
    RPS = "rps"
    DICE = "dice"
    SHAKE = "shake"
    UNKNOWN = "unknown"


@dataclass
class BaseMessageComponent:
    type: ComponentType = ComponentType.PLAIN


class Plain(BaseMessageComponent):
    """Plain text component. Usage: ``Plain(\"hello\")`` or ``Plain(text=\"hello\")``."""
    text: str

    def __init__(self, text: str = ""):
        self.type = ComponentType.PLAIN
        self.text = text


class Image(BaseMessageComponent):
    """Image component. Usage: ``Image(file=url)`` or ``Image(url=url)``."""
    file: str | None
    _type: str | None
    url: str | None
    path: str | None

    def __init__(
        self,
        file: str | None = "",
        url: str | None = "",
        path: str | None = "",
        _type: str | None = "",
    ):
        self.type = ComponentType.IMAGE
        self.file = file
        self._type = _type
        self.url = url
        self.path = path

    @classmethod
    def fromURL(cls, url: str) -> "Image":
        return cls(file=url, url=url, _type="url")

    @classmethod
    def fromFileSystem(cls, path: str) -> "Image":
        return cls(file=path, path=path, _type="path")

    @classmethod
    def fromBase64(cls, b64_str: str) -> "Image":
        return cls(file=b64_str, _type="base64")


class Record(BaseMessageComponent):
    """Voice record component."""
    file: str | None
    url: str | None
    text: str | None
    path: str | None

    def __init__(
        self,
        file: str | None = "",
        url: str | None = "",
        text: str | None = None,
        path: str | None = None,
    ):
        self.type = ComponentType.RECORD
        self.file = file
        self.url = url
        self.text = text
        self.path = path


class Video(BaseMessageComponent):
    """Video component."""
    file: str
    url: str | None
    cover: str | None
    path: str | None

    def __init__(
        self,
        file: str = "",
        url: str | None = "",
        cover: str | None = "",
        path: str | None = "",
    ):
        self.type = ComponentType.VIDEO
        self.file = file
        self.url = url
        self.cover = cover
        self.path = path


@dataclass
class File(BaseMessageComponent):
    name: str | None = ""
    file_: str | None = ""
    url: str | None = ""

    def __post_init__(self):
        self.type = ComponentType.FILE

    @property
    def file(self) -> str | None:
        return self.file_

    @file.setter
    def file(self, value: str | None):
        self.file_ = value


class Face(BaseMessageComponent):
    """Face / sticker component."""
    id: int

    def __init__(self, id: int = 0):
        self.type = ComponentType.FACE
        self.id = id


class At(BaseMessageComponent):
    """At someone. Usage: ``At(qq=12345)`` or ``At(qq=12345, name=\"user\")``."""
    qq: int | str
    name: str | None

    def __init__(self, qq: int | str = 0, name: str | None = ""):
        self.type = ComponentType.AT
        self.qq = qq
        self.name = name


class AtAll(At):
    """At everyone."""
    def __init__(self):
        super().__init__(qq="all")
        self.type = ComponentType.AT_ALL


@dataclass
class Reply(BaseMessageComponent):
    id: str | int = ""
    chain: list[BaseMessageComponent] | None = None
    sender_id: int | str | None = 0
    sender_nickname: str | None = ""
    time: int | None = 0
    message_str: str | None = ""

    def __post_init__(self):
        self.type = ComponentType.REPLY


@dataclass
class Forward(BaseMessageComponent):
    id: str = ""

    def __post_init__(self):
        self.type = ComponentType.FORWARD


@dataclass
class Node(BaseMessageComponent):
    id: int | None = 0
    name: str | None = ""
    uin: str | None = "0"
    content: list[BaseMessageComponent] = field(default_factory=list)
    seq: str | list | None = ""
    time: int | None = 0

    def __post_init__(self):
        self.type = ComponentType.NODE


@dataclass
class Nodes(BaseMessageComponent):
    nodes: list[Node] = field(default_factory=list)

    def __post_init__(self):
        self.type = ComponentType.NODES


@dataclass
class Share(BaseMessageComponent):
    url: str = ""
    title: str = ""
    content: str | None = ""
    image: str | None = ""

    def __post_init__(self):
        self.type = ComponentType.SHARE


@dataclass
class Json(BaseMessageComponent):
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        self.type = ComponentType.JSON


@dataclass
class Poke(BaseMessageComponent):
    _type: str | int = "126"
    id: int | str | None = 0

    def __post_init__(self):
        self.type = ComponentType.POKE


__all__ = [
    "ComponentType", "BaseMessageComponent",
    "Plain", "Image", "Record", "Video", "File",
    "Face", "At", "AtAll", "Reply",
    "Forward", "Node", "Nodes",
    "Share", "Json", "Poke",
]

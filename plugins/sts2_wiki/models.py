from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Sts2WikiCandidate:
    title: str
    snippet: str = ""


@dataclass(slots=True)
class Sts2WikiResult:
    query: str
    title: str
    summary: str
    extract: str
    url: str
    updated_at: str = ""
    cache_hit: bool = False
    candidates: list[Sts2WikiCandidate] = field(default_factory=list)

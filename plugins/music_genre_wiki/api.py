from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("HikariBot.MusicGenreWiki")


class MusicGenreError(RuntimeError):
    pass


class MusicGenreNotFound(MusicGenreError):
    pass


@dataclass(slots=True)
class GenreResult:
    key: str
    name: str
    desc: str
    chapter: str
    aka: str
    related: str
    ups: list[str]
    downs: list[str]
    examples: list[str]


@dataclass(slots=True)
class ChapterInfo:
    name: str
    color: str
    genre_count: int
    tree: list[dict[str, Any]]


@dataclass(slots=True)
class RelationshipResult:
    name: str
    chapter: str
    parents: list[str]
    children: list[str]
    related: str


class MusicGenreClient:
    """Local data client for the electronic music genre encyclopedia."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._data: dict[str, Any] | None = None
        self._fuzzy = bool(config.get("fuzzy_match", True))
        self._max_results = max(1, min(int(config.get("max_results", 5)), 20))
        self._detail_max_chars = max(200, int(config.get("detail_max_chars", 1500)))

    def _load_data(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        data_file = str(self.config.get("data_file", "plugins/music_genre_wiki/data.json"))
        # Resolve relative to project root
        if not os.path.isabs(data_file):
            # Try relative to CWD
            pass
        if not os.path.exists(data_file):
            # Try relative to project root conventions
            alt = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), data_file)
            if os.path.exists(alt):
                data_file = alt
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except FileNotFoundError:
            raise MusicGenreError(f"音乐流派数据文件未找到: {data_file}")
        except json.JSONDecodeError as e:
            raise MusicGenreError(f"音乐流派数据文件格式错误: {e}")
        if not isinstance(self._data, dict) or "genres" not in self._data:
            raise MusicGenreError("音乐流派数据结构异常")
        return self._data

    @property
    def data(self) -> dict[str, Any]:
        return self._load_data()

    def _build_name_index(self) -> dict[str, list[str]]:
        """Build a mapping from lowercase name -> list of genre keys."""
        index: dict[str, list[str]] = {}
        for key, genre in self.data["genres"].items():
            name = (genre.get("name") or "").strip().lower()
            if name:
                index.setdefault(name, []).append(key)
            aka = (genre.get("aka") or "").strip()
            if aka:
                for alias in re.split(r"[、,，/]", aka):
                    alias = alias.strip().lower()
                    if alias:
                        index.setdefault(alias, []).append(key)
        return index

    def search(self, query: str) -> list[GenreResult]:
        """Search for a genre by name. Returns up to max_results matches."""
        keyword = query.strip()
        if not keyword:
            raise MusicGenreError("缺少搜索关键词")

        genres = self.data["genres"]
        keyword_lower = keyword.lower()

        # 0. Exact chapter name match — return chapter tree instead of genre list
        for chapter in self.data["chapters"]:
            ch_name = chapter["name"].strip().lower()
            if ch_name == keyword_lower:
                ch_results = self._get_genres_by_chapter(chapter["name"])
                if ch_results:
                    return ch_results[: self._max_results]

        # 1. Try exact name match first
        for key, genre in genres.items():
            name = (genre.get("name") or "").strip().lower()
            if name == keyword_lower:
                return [self._to_result(key, genre)]
            # Check aka
            aka = (genre.get("aka") or "").strip().lower()
            if aka and aka == keyword_lower:
                return [self._to_result(key, genre)]

        # 2. Try exact match on any name segment (after splitting aka)
        for key, genre in genres.items():
            name = (genre.get("name") or "").strip().lower()
            if keyword_lower in name:
                return [self._to_result(key, genre)]
            aka = (genre.get("aka") or "").strip().lower()
            if aka and keyword_lower in aka:
                return [self._to_result(key, genre)]

        # 3. Try fuzzy match if enabled
        if self._fuzzy:
            matches: list[tuple[str, dict[str, Any], int]] = []
            for key, genre in genres.items():
                name = (genre.get("name") or "").strip().lower()
                aka = (genre.get("aka") or "").strip().lower()

                score = 0
                if keyword_lower in name:
                    score = max(score, 10 - name.index(keyword_lower))
                elif name in keyword_lower or keyword_lower in name:
                    score = max(score, 5)

                if aka and (keyword_lower in aka or aka in keyword_lower):
                    score = max(score, 3)

                if score > 0:
                    matches.append((key, genre, score))

            if matches:
                matches.sort(key=lambda x: -x[2])
                results = [self._to_result(k, g) for k, g, _ in matches[: self._max_results]]
                return results

        # 4. Try chapter partial match
        for chapter in self.data["chapters"]:
            ch_name = chapter["name"].strip().lower()
            if keyword_lower in ch_name:
                ch_results = self._get_genres_by_chapter(chapter["name"])
                if ch_results:
                    return ch_results[: self._max_results]

        raise MusicGenreNotFound(f"没有找到「{keyword}」相关的音乐流派")

    def _get_genres_by_chapter(self, chapter_name: str) -> list[GenreResult]:
        genres = self.data["genres"]
        results = []
        for key, genre in genres.items():
            if (genre.get("chapter") or "").strip() == chapter_name:
                results.append(self._to_result(key, genre))
        results.sort(key=lambda r: r.name)
        return results

    def get_relationships(self, query: str) -> RelationshipResult | None:
        """Get parent/child/related info for a genre."""
        results = self.search(query)
        if not results:
            return None
        target = results[0]

        genres = self.data["genres"]
        # Find all genres that list this genre as parent (downs) or child (ups)
        children = []
        parents = target.ups.copy()
        for key, genre in genres.items():
            g_name = genre.get("name", "").strip()
            if g_name == target.name:
                continue
            g_ups = genre.get("ups") or []
            if target.name in g_ups:
                children.append(g_name)
            # Also check if target appears as parent in the hierarchy
            g_downs = genre.get("downs") or []
            if g_name in target.downs:
                children.append(g_name)

        # Combine and deduplicate
        seen = set()
        deduped_children = []
        for c in children:
            if c not in seen:
                seen.add(c)
                deduped_children.append(c)

        seen_parents = set()
        deduped_parents = []
        for p in parents:
            if p not in seen_parents:
                seen_parents.add(p)
                deduped_parents.append(p)

        return RelationshipResult(
            name=target.name,
            chapter=target.chapter,
            parents=deduped_parents,
            children=deduped_children,
            related=target.related,
        )

    def list_chapters(self) -> list[ChapterInfo]:
        """List all chapters with genre counts."""
        genres = self.data["genres"]
        chapters = self.data["chapters"]
        result = []
        for ch in chapters:
            ch_name = ch["name"].strip()
            count = sum(
                1 for g in genres.values() if (g.get("chapter") or "").strip() == ch_name
            )
            result.append(ChapterInfo(
                name=ch_name,
                color=ch.get("color", "#64748b"),
                genre_count=count,
                tree=ch.get("tree", []),
            ))
        return result

    def get_tree(self, chapter_name: str) -> list[ChapterInfo] | None:
        """Get the genre tree for a specific chapter."""
        chapters = self.data["chapters"]
        for ch in chapters:
            if ch["name"].strip().lower() == chapter_name.strip().lower():
                genres = self.data["genres"]
                ch_name = ch["name"].strip()
                count = sum(
                    1 for g in genres.values() if (g.get("chapter") or "").strip() == ch_name
                )
                return [ChapterInfo(
                    name=ch_name,
                    color=ch.get("color", "#64748b"),
                    genre_count=count,
                    tree=ch.get("tree", []),
                )]
        return None

    def _to_result(self, key: str, genre: dict[str, Any]) -> GenreResult:
        desc = (genre.get("desc") or "").strip()
        if len(desc) > self._detail_max_chars:
            desc = desc[: self._detail_max_chars - 1].rstrip() + "…"
        return GenreResult(
            key=key,
            name=(genre.get("name") or "").strip(),
            desc=desc,
            chapter=(genre.get("chapter") or "").strip(),
            aka=(genre.get("aka") or "").strip(),
            related=(genre.get("related") or "").strip(),
            ups=[u.strip() for u in (genre.get("ups") or []) if u.strip()],
            downs=[d.strip() for d in (genre.get("downs") or []) if d.strip()],
            examples=[e.strip() for e in (genre.get("examples") or []) if e.strip()],
        )

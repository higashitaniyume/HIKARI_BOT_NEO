"""AstrBot AstrBotConfig shim — dict-like config backed by JSON files."""

from __future__ import annotations

import json
import logging
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

logger = logging.getLogger("AstrBotCompat.Shim.Config")


class AstrBotConfig(MutableMapping):
    """Shim for AstrBot plugin configuration, backed by a JSON file.

    Behaves like a dict and persists changes to disk automatically.
    """

    def __init__(self, config_path: str | Path, initial: dict[str, Any] | None = None):
        self._path = Path(config_path)
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                self._data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read config %s: %s", self._path, e)
                self._data = dict(initial or {})
        else:
            self._data = dict(initial or {})
            self._save()

    # --- MutableMapping ---

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def __delitem__(self, key: str) -> None:
        del self._data[key]
        self._save()

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return repr(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    # --- Extra ---

    def save_config(self) -> None:
        """Immediately persist current data to disk."""
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("Failed to write config %s: %s", self._path, e)

    def reload(self) -> None:
        """Reload config from disk, discarding in-memory changes."""
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                self._data = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to reload config %s: %s", self._path, e)

"""AstrBot plugin config schema parsing and management.

Parses ``_conf_schema.json`` from plugin zips and provides default values.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("AstrBotCompat.Config")


def parse_schema(schema_path: Path) -> dict[str, Any]:
    """Parse a ``_conf_schema.json`` and return config defaults + metadata.

    Returns::

        {
            "defaults": {"key": value, ...},
            "schema": {original raw schema},
        }

    If the file doesn't exist or is invalid, returns empty defaults.
    """
    if not schema_path.exists():
        logger.debug("No schema file at %s, using empty defaults", schema_path)
        return {"defaults": {}, "schema": {}}

    try:
        raw = schema_path.read_text(encoding="utf-8")
        schema: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "Failed to parse schema at %s — falling back to empty defaults: %s",
            schema_path,
            e,
        )
        return {"defaults": {}, "schema": {}}

    if not isinstance(schema, dict):
        logger.warning(
            "Schema at %s is not a JSON object (type=%s), ignoring",
            schema_path,
            type(schema).__name__,
        )
        return {"defaults": {}, "schema": {}}

    defaults: dict[str, Any] = {}
    for key, definition in schema.items():
        if not isinstance(definition, dict):
            logger.debug("Schema key %r has non-dict definition, skipping", key)
            continue
        type_name = definition.get("type", "string")
        if "default" in definition:
            defaults[key] = _coerce_default(type_name, definition["default"])
        else:
            defaults[key] = _type_default(type_name)

    logger.debug(
        "Parsed schema %s: %d keys defined, %d with defaults",
        schema_path,
        len(schema),
        len(defaults),
    )
    return {"defaults": defaults, "schema": schema}


def _coerce_default(type_name: str, value: Any) -> Any:
    """Coerce a default value to the declared type."""
    try:
        if type_name == "int":
            return int(value)
        if type_name == "float":
            if isinstance(value, str):
                return float(value)
            return float(value)
        if type_name == "bool":
            return bool(value)
        if type_name in ("string", "text", "file"):
            return str(value)
        # list and object pass through as-is
    except (ValueError, TypeError) as e:
        logger.warning(
            "Cannot coerce default %r to type %s: %s — using raw value",
            value,
            type_name,
            e,
        )
    return value


def _type_default(type_name: str) -> Any:
    """Return the implicit default for a config type."""
    return {
        "string": "",
        "text": "",
        "file": "",
        "int": 0,
        "float": 0.0,
        "bool": False,
        "list": [],
        "object": {},
    }.get(type_name, None)


def build_config_path(plugin_name: str) -> Path:
    """Return the path to a plugin's config.json."""
    from plugins.astrbot_compat.constants import PLUGINS_DIR
    return PLUGINS_DIR / plugin_name / "config.json"


def build_schema_path(plugin_dir: Path) -> Path:
    """Return the path to a plugin's _conf_schema.json."""
    return plugin_dir / "_conf_schema.json"


# ---------------------------------------------------------------------------
# metadata.yaml parsing
# ---------------------------------------------------------------------------


def parse_metadata(plugin_dir: Path) -> dict[str, Any]:
    """Parse ``metadata.yaml`` (or ``metadata.yml``) from a plugin directory.

    Returns a dict with keys ``name``, ``version``, ``author``, ``description``,
    ``repo``, ``homepage``, ``deps``, ``tags`` — all falling back to empty
    defaults if the file is missing or unreadable.
    """
    for candidate in ("metadata.yaml", "metadata.yml"):
        path = plugin_dir / candidate
        if path.exists():
            return _read_metadata_file(path)
    return {}


def _read_metadata_file(path: Path) -> dict[str, Any]:
    """Read a YAML file, falling back to a basic key-value parser if
    PyYAML is not installed."""
    raw = path.read_text(encoding="utf-8")

    # Prefer real YAML parser
    try:
        import yaml
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
        logger.debug("metadata.yaml at %s is not a dict, ignoring", path)
        return {}
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Failed to parse metadata.yaml at %s with PyYAML: %s", path, e)
        return {}

    # Fallback: naive key-value parser for simple metadata
    metadata: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped and not stripped.startswith("-"):
            # New key
            if current_key and current_list is not None:
                metadata[current_key] = current_list
                current_list = None
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val:
                metadata[current_key] = val
            else:
                current_list = []
        elif stripped.startswith("- ") and current_key is not None:
            if current_list is not None:
                current_list.append(stripped[2:].strip())

    if current_key and current_list is not None:
        metadata[current_key] = current_list

    logger.debug("Parsed metadata (fallback parser) from %s: %s", path, metadata)
    return metadata

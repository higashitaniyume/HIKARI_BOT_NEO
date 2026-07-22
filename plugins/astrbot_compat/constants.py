"""Shared constants for the astrbot_compat plugin."""

from pathlib import Path

PLUGINS_DIR: Path = Path("UserData") / "astrbot_plugins"
PLUGINS_DIR = PLUGINS_DIR.resolve()

DEFAULT_CONFIG = {
    "auto_load": True,
    "auto_load_dir": str(PLUGINS_DIR),
    "shim_priority": 2,
}

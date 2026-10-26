"""remote_shell 插件配置与默认值。

配置由 `core.config_loader.load_plugin_config` 负责 deep-merge，运行时按
mtime/size 缓存自动热加载，因此每次工具调用都重新读取即可。
"""

from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

DEFAULT_CONFIG: dict[str, Any] = {
    # 默认关闭。打开后，群里任何人只要让 AI 用终端，就会在该群专属的
    # Linux 用户下执行命令。
    "enabled": False,
    "ssh": {
        "host": "127.0.0.1",
        "port": 22,
        "username": "root",
        # 私钥路径相对于 bot 进程工作目录（容器内为 /app）。
        "private_key": "BotData/ssh/id_ed25519",
        "passphrase": "",
        "password": "",
        # 首次连接采用 TOFU：文件不存在时写入服务器主机公钥，之后严格校验。
        "known_hosts": "BotData/ssh/known_hosts",
        "connect_timeout": 10,
        "keepalive": 30,
        "max_output_bytes": 2097152,
    },
    "users": {
        "prefix": "qqg_",
        "home_base": "/home",
        "shell": "/bin/bash",
        "auto_provision": True,
        # 私聊默认不开终端：需求是「一个群共享一个用户」。
        "allow_private": False,
    },
    "exec": {
        "default_timeout": 60,
        "max_timeout": 300,
        "max_command_chars": 4000,
        "max_output_chars": 6000,
        "max_processes": 256,
        "max_file_size_mb": 2048,
        "max_concurrent": 3,
        # 主动接话（未 @ 机器人）时是否允许调用终端工具。默认否，
        # 避免随便一句闲聊就把命令执行出去。
        "allow_auto_reply_calls": False,
    },
    "files": {
        "enabled": True,
        "max_read_chars": 20000,
        "max_write_bytes": 1048576,
        "max_edit_bytes": 262144,
        "max_list_entries": 200,
    },
    "limits": {
        "hourly_commands": 60,
        "daily_commands": 300,
        "disk_soft_limit_mb": 5120,
        "disk_check_interval_seconds": 600,
    },
    "groups": {
        "blocked": [],
    },
    "logs": {
        "root": "UserData/remote_shell/logs",
        "keep": 100,
    },
    "audit": {
        "enabled": True,
        "path": "UserData/remote_shell/audit.jsonl",
    },
    "state_path": "UserData/remote_shell/state.json",
}


def load_config() -> dict[str, Any]:
    """读取插件配置（含默认值合并与热加载缓存）。"""

    return load_plugin_config("remote_shell", DEFAULT_CONFIG)


def section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    value = cfg.get(name)
    return value if isinstance(value, dict) else {}


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n", ""}:
            return False
    return default


def as_int(
    value: Any,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def as_float(
    value: Any,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number

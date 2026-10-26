"""bot_admin 终端页数据层测试。"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.bot_admin.terminal_admin import read_terminal_log, terminal_overview


def _cfg(tmp: Path) -> dict:
    return {
        "enabled": True,
        "state_path": str(tmp / "state.json"),
        "audit": {"enabled": True, "path": str(tmp / "audit.jsonl")},
        "logs": {"root": str(tmp / "logs"), "keep": 10},
        "ssh": {"host": "192.168.31.131", "port": 22135},
        "users": {"allow_private": True, "prefix": "qqg_"},
        "root": {"enabled": True, "allow_in_groups": False},
        "groups": {"blocked": ["999"]},
    }


def _write_fixture(tmp: Path) -> dict:
    cfg = _cfg(tmp)
    today = time.strftime("%Y-%m-%d", time.localtime())
    now = int(time.time())
    state = {
        "version": 1,
        "groups": {
            "g111": {
                "user": "qqg_111",
                "home": "/home/qqg_111",
                "cwd": "/home/qqg_111/work",
                "created_at": now - 3600,
                "last_used": now - 60,
                "provisioned": True,
                "daily": {"date": today, "count": 2},
            },
            "p222": {
                "user": "qqp_222",
                "home": "/home/qqp_222",
                "cwd": "/home/qqp_222",
                "created_at": now - 7200,
                "last_used": now - 90000,
                "provisioned": True,
                "daily": {"date": today, "count": 0},
            },
        },
        "root_sessions": {"p222": {"cwd": "/root"}},
    }
    (tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")

    records = [
        {
            "ts": "2026-10-26 10:00:00",
            "session": "g111",
            "group_id": "111",
            "action": "exec",
            "user": "qqg_111",
            "command": "python3 -c \"print(1)\"",
            "exit_code": 0,
            "duration_ms": 120,
            "truncated": False,
            "log": str(Path(cfg["logs"]["root"]) / "g111" / "a.log"),
        },
        {
            "ts": "2026-10-26 10:05:00",
            "session": "p222",
            "group_id": "222",
            "action": "root_exec",
            "user": "root",
            "command": "apt install -y jq",
            "exit_code": 0,
            "duration_ms": 900,
            "log": "",
        },
        {
            "ts": "2026-10-26 10:06:00",
            "session": "g111",
            "group_id": "111",
            "action": "write",
            "user": "qqg_111",
            "path": "/home/qqg_111/work/a.py",
            "bytes": 42,
        },
    ]
    (tmp / "audit.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    session_dir = tmp / "logs" / "g111"
    session_dir.mkdir(parents=True)
    (session_dir / "a.log").write_text("hello output", encoding="utf-8")
    return cfg


class TerminalOverviewTests(unittest.TestCase):
    def test_overview_counts_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = _write_fixture(tmp)
            data = terminal_overview(cfg=cfg, limit=50)

            stats = data["stats"]
            self.assertEqual(stats["users"], 2)
            self.assertEqual(stats["groups"], 1)
            self.assertEqual(stats["privates"], 1)
            self.assertEqual(stats["today_calls"], 2)
            self.assertEqual(stats["total_calls"], 3)
            self.assertEqual(stats["target"], "192.168.31.131:22135")
            self.assertTrue(stats["private_enabled"])
            self.assertTrue(stats["root_enabled"])
            self.assertEqual(stats["blocked_groups"], 1)

            self.assertEqual([row["key"] for row in data["sessions"]], ["g111", "p222"])
            self.assertEqual(data["records"][0]["action"], "write")
            self.assertEqual(data["records"][-1]["action"], "exec")

    def test_overview_filters(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = _write_fixture(tmp)

            only_exec = terminal_overview(cfg=cfg, action="exec")
            self.assertEqual([item["action"] for item in only_exec["records"]], ["exec"])

            only_root = terminal_overview(cfg=cfg, action="root_exec")
            self.assertEqual(len(only_root["records"]), 1)
            self.assertEqual(only_root["records"][0]["user"], "root")

            searched = terminal_overview(cfg=cfg, query="apt")
            self.assertEqual(len(searched["records"]), 1)
            self.assertEqual(searched["records"][0]["session"], "p222")

    def test_overview_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = _cfg(tmp)
            data = terminal_overview(cfg=cfg)
            self.assertEqual(data["stats"]["users"], 0)
            self.assertEqual(data["records"], [])
            self.assertEqual(data["sessions"], [])


class TerminalLogTests(unittest.TestCase):
    def test_read_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = _write_fixture(tmp)
            data = read_terminal_log("g111", "a.log", cfg=cfg)
            self.assertEqual(data["content"], "hello output")
            self.assertFalse(data["truncated"])

    def test_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = _write_fixture(tmp)
            with self.assertRaises(ValueError):
                read_terminal_log("../etc", "passwd.log", cfg=cfg)
            with self.assertRaises(ValueError):
                read_terminal_log("g111", "../a.log", cfg=cfg)
            with self.assertRaises(ValueError):
                read_terminal_log("g111", "a.txt", cfg=cfg)

    def test_missing_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = _write_fixture(tmp)
            with self.assertRaises(FileNotFoundError):
                read_terminal_log("g111", "missing.log", cfg=cfg)


if __name__ == "__main__":
    unittest.main()

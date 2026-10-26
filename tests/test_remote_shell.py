"""remote_shell 单元测试：命令拼装、开通校验、状态存储与执行编排。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from plugins.remote_shell.config import DEFAULT_CONFIG
from plugins.remote_shell.provision import (
    build_provision_script,
    build_verify_script,
    home_for,
    linux_user,
    parse_verify,
    verify_problems,
)
from plugins.remote_shell.runner import (
    MARKER,
    build_remote_command,
    build_script,
    normalize_path,
    split_cwd_marker,
    strip_ansi,
    truncate_output,
)
from plugins.remote_shell.service import (
    RemoteShellService,
    SessionRef,
    _parse_find_output,
)
from plugins.remote_shell.ssh_client import ExecResult, RemoteShellError
from plugins.remote_shell.store import StateStore, append_audit, write_command_log

GROUP_REF = SessionRef(key="g123", raw_id="123", prefix="qqg_", is_group=True)


class FakeBackend:
    def __init__(self, mapping: dict[str, ExecResult] | None = None):
        self.mapping = mapping or {}
        self.calls: list[tuple[str, bytes | str | None]] = []

    async def run(self, cfg, command, *, stdin_data=None, timeout=60.0):
        self.calls.append((command, stdin_data))
        for needle, result in self.mapping.items():
            if needle in command:
                return result
        return ExecResult(exit_code=0, output="")

    async def close(self) -> None:
        return None


def make_service(mapping: dict[str, ExecResult] | None = None) -> tuple[RemoteShellService, FakeBackend, dict]:
    backend = FakeBackend(mapping)
    service = RemoteShellService(backend=backend)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["enabled"] = True
    cfg["limits"]["disk_soft_limit_mb"] = 0
    return service, backend, cfg


VERIFY_OUTPUT = "\n".join(
    [
        f"{MARKER}USER:qqg_123",
        f"{MARKER}UID:2001",
        f"{MARKER}GROUPS:qqg_123",
        f"{MARKER}SUDO:deny",
        f"{MARKER}PERM:700",
        f"{MARKER}LOCK:L",
        f"{MARKER}SHELL:/bin/bash",
        f"{MARKER}HOMEDIR:/home/qqg_123",
    ]
)


class RunnerTests(unittest.TestCase):
    def test_build_script_keeps_command_verbatim(self) -> None:
        script = build_script("echo '$(id)' && ls", cwd="/home/qqg_1", max_processes=64)
        self.assertIn("echo '$(id)' && ls", script)
        self.assertIn("trap", script)
        self.assertIn("ulimit -u 64", script)

    def test_build_remote_command_downgrades_user_and_timeout(self) -> None:
        command = build_remote_command(
            user="qqg_123",
            home="/home/qqg_123",
            script="echo hi",
            timeout=45,
        )
        self.assertTrue(command.startswith("/usr/sbin/runuser -u qqg_123 --"))
        self.assertIn("env -i", command)
        self.assertIn("HOME=/home/qqg_123", command)
        self.assertIn("/usr/bin/timeout -k 5 45 /bin/bash -c", command)
        self.assertIn("PATH=/home/qqg_123/.local/bin", command)

    def test_split_cwd_marker(self) -> None:
        output = f"hello\n{MARKER}CWD:/home/qqg_123/sub"
        text, cwd, failed = split_cwd_marker(output)
        self.assertEqual(text, "hello\n")
        self.assertEqual(cwd, "/home/qqg_123/sub")
        self.assertFalse(failed)

    def test_split_cwd_marker_chdir_failed(self) -> None:
        text, cwd, failed = split_cwd_marker(f"{MARKER}CHDIR_FAILED{MARKER}CWD:/home/qqg_123")
        self.assertEqual(cwd, "/home/qqg_123")
        self.assertTrue(failed)
        self.assertNotIn("CHDIR_FAILED", text)

    def test_strip_ansi(self) -> None:
        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m\r\nok"), "red\nok")

    def test_truncate_output(self) -> None:
        text, truncated = truncate_output("a" * 1000, 100)
        self.assertTrue(truncated)
        self.assertIn("已省略", text)
        self.assertLess(len(text), 1000)

    def test_normalize_path(self) -> None:
        self.assertEqual(
            normalize_path("src", cwd="/home/qqg_1", home="/home/qqg_1"),
            "/home/qqg_1/src",
        )
        self.assertEqual(
            normalize_path("../../etc/passwd", cwd="/home/qqg_1", home="/home/qqg_1"),
            "/etc/passwd",
        )

    def test_normalize_path_rejects_escape_from_home(self) -> None:
        with self.assertRaises(ValueError):
            normalize_path("../../etc/passwd", cwd="/home/qqg_1", home="/home/qqg_1", require_home=True)
        with self.assertRaises(ValueError):
            normalize_path("/etc/passwd", cwd="/home/qqg_1", home="/home/qqg_1", require_home=True)

    def test_normalize_path_rejects_control_chars(self) -> None:
        with self.assertRaises(ValueError):
            normalize_path("a\x00b", cwd="/home/qqg_1", home="/home/qqg_1")


class ProvisionTests(unittest.TestCase):
    def test_linux_user(self) -> None:
        self.assertEqual(linux_user("123456789"), "qqg_123456789")
        self.assertEqual(linux_user(" 9988 "), "qqg_9988")
        with self.assertRaises(ValueError):
            linux_user("abc")

    def test_home_for(self) -> None:
        self.assertEqual(home_for("qqg_1", "/home"), "/home/qqg_1")
        self.assertEqual(home_for("qqg_1", "/srv/homes/"), "/srv/homes/qqg_1")

    def test_provision_script_is_idempotent(self) -> None:
        script = build_provision_script("qqg_1", "/home/qqg_1")
        self.assertIn('if ! id -u "$u"', script)
        self.assertIn("useradd -m -U", script)
        self.assertIn('passwd -l "$u"', script)
        self.assertIn('chmod 700 "$h"', script)
        self.assertIn("grep -qx 'umask 077'", script)
        self.assertIn("PROVISION_OK", script)

    def test_verify_and_problems(self) -> None:
        info = parse_verify(VERIFY_OUTPUT)
        self.assertEqual(info["GROUPS"], "qqg_123")
        self.assertEqual(
            verify_problems(info, user="qqg_123", home="/home/qqg_123"),
            [],
        )

    def test_verify_flags_risky_states(self) -> None:
        info = parse_verify(VERIFY_OUTPUT)
        info["SUDO"] = "allow"
        info["GROUPS"] = "qqg_123,docker"
        info["PERM"] = "755"
        info["LOCK"] = "P"
        problems = verify_problems(info, user="qqg_123", home="/home/qqg_123")
        joined = "；".join(problems)
        self.assertIn("sudo", joined)
        self.assertIn("用户组异常", joined)
        self.assertIn("700", joined)
        self.assertIn("密码状态", joined)

    def test_verify_no_user(self) -> None:
        problems = verify_problems(
            parse_verify(f"{MARKER}NOUSER"), user="qqg_1", home="/home/qqg_1"
        )
        self.assertTrue(problems)

    def test_build_verify_script_checks_sudo(self) -> None:
        script = build_verify_script("qqg_1", "/home/qqg_1")
        self.assertIn("runuser -u", script)
        self.assertIn("sudo -n true", script)
        self.assertIn("GROUPS", script)


class StoreTests(unittest.TestCase):
    def test_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            self.assertEqual(store.load()["groups"], {})
            data = {"version": 1, "groups": {"g1": {"user": "qqg_1"}}}
            store.save(data)
            self.assertEqual(store.load()["groups"]["g1"]["user"], "qqg_1")

    def test_state_survives_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(StateStore(path).load()["groups"], {})

    def test_audit_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            append_audit(audit, {"action": "exec"})
            self.assertIn("exec", audit.read_text(encoding="utf-8"))

            log = write_command_log(Path(tmp) / "logs", "g1", command="ls", output="ok", exit_code=0)
            self.assertTrue(Path(log).exists())
            self.assertIn("ls", Path(log).read_text(encoding="utf-8"))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_exec_provisions_and_tracks_cwd(self) -> None:
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
            "echo hi": ExecResult(exit_code=0, output=f"hi\n{MARKER}CWD:/home/qqg_123/work"),
        }
        service, backend, cfg = make_service(mapping)

        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            result = await service.execute(cfg, GROUP_REF, "echo hi")

            self.assertEqual(result["exit_code"], 0)
            self.assertFalse(result["truncated"])
            self.assertEqual(result["output"], "hi")
            self.assertEqual(result["cwd"], "/home/qqg_123/work")

            state = json.loads(Path(cfg["state_path"]).read_text(encoding="utf-8"))
            entry = state["groups"]["g123"]
            self.assertEqual(entry["user"], "qqg_123")
            self.assertEqual(entry["cwd"], "/home/qqg_123/work")
            self.assertEqual(entry["daily"]["count"], 1)

        executed = backend.calls[-1][0]
        self.assertIn("/usr/sbin/runuser -u qqg_123", executed)
        self.assertIn("/usr/bin/timeout -k 5 60", executed)

    async def test_daily_quota(self) -> None:
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
        }
        service, _backend, cfg = make_service(mapping)

        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            cfg["limits"]["daily_commands"] = 1
            await service.execute(cfg, GROUP_REF, "true")
            with self.assertRaises(RemoteShellError):
                await service.execute(cfg, GROUP_REF, "true")

    async def test_provisioning_failure_is_reported(self) -> None:
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
        }
        service, _backend, cfg = make_service(mapping)

        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            cfg["users"]["auto_provision"] = False
            with self.assertRaises(RemoteShellError):
                await service.execute(cfg, GROUP_REF, "true")

    async def test_write_requires_home(self) -> None:
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
        }
        service, _backend, cfg = make_service(mapping)
        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            with self.assertRaises(ValueError):
                await service.write_file(cfg, GROUP_REF, "/etc/crontab", "x")

    async def test_write_streams_content(self) -> None:
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
            f"{MARKER}BYTES:": ExecResult(exit_code=0, output=f"{MARKER}BYTES:6"),
        }
        service, backend, cfg = make_service(mapping)
        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            result = await service.write_file(cfg, GROUP_REF, "notes.txt", "hello\n")
            self.assertEqual(result["bytes"], 6)

        payload = backend.calls[-1][1]
        self.assertEqual(payload, b"hello\n")
        command = backend.calls[-1][0]
        self.assertIn("cat > /home/qqg_123/notes.txt", command)

    async def test_read_numbers_lines(self) -> None:
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
            "wc -l -c <": ExecResult(exit_code=0, output="3 11"),
            "sed -n": ExecResult(exit_code=0, output="alpha\nbeta"),
        }
        service, _backend, cfg = make_service(mapping)
        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            result = await service.read_file(cfg, GROUP_REF, "a.txt")
            self.assertEqual(result["lines"], 3)
            self.assertIn("1\talpha", result["content"])
            self.assertIn("2\tbeta", result["content"])

    async def test_list_dir_parses_find(self) -> None:
        find_output = "d\t0\t2026-10-26 12:00\tsrc\nf\t12\t2026-10-26 12:01\ta.py\n"
        mapping = {
            "useradd -m -U": ExecResult(exit_code=0, output=""),
            f"{MARKER}GROUPS": ExecResult(exit_code=0, output=VERIFY_OUTPUT),
            "find ": ExecResult(exit_code=0, output=find_output),
        }
        service, _backend, cfg = make_service(mapping)
        with tempfile.TemporaryDirectory() as tmp:
            cfg["state_path"] = str(Path(tmp) / "state.json")
            result = await service.list_dir(cfg, GROUP_REF, ".")
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["entries"][0]["name"], "src")
            self.assertEqual(result["entries"][0]["type"], "dir")

    def test_parse_find_output_skips_garbage(self) -> None:
        entries = _parse_find_output("garbage\nf\t3\t2026-10-26 12:00\tx.txt")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "x.txt")


class ToolRegistrationTests(unittest.TestCase):
    def test_tools_are_registered_as_side_effect_tools(self) -> None:
        from core.ai_tool_registry import get_ai_tool

        for name in ("terminal_exec", "terminal_read", "terminal_write", "terminal_list", "terminal_edit"):
            spec = get_ai_tool(name)
            self.assertIsNotNone(spec, name)
            self.assertEqual(spec.plugin_name, "remote_shell")
            self.assertFalse(spec.readonly)
            self.assertFalse(spec.enabled_by_default)
            self.assertEqual(spec.parameters["type"], "object")


if __name__ == "__main__":
    unittest.main()

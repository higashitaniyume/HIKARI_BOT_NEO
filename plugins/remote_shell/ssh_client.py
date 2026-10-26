"""基于 asyncssh 的远端执行通道。

设计要点：
- 复用一条 SSH 连接（多 channel 并发），配置变化或连接断开时自动重连；
- 输出边读边丢，超过 `max_output_bytes` 的内容不再占用内存；
- 首次连接使用 TOFU 记录主机公钥，之后严格校验，避免中间人；
- 所有异常统一包装成 `RemoteShellError`，由工具层转成可读错误。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncssh

from .config import as_int

logger = logging.getLogger("HikariBot.RemoteShell.SSH")

_READ_CHUNK = 65536


class RemoteShellError(RuntimeError):
    """面向用户的远端执行错误。"""


@dataclass(slots=True)
class ExecResult:
    exit_code: int
    output: str
    timed_out: bool = False
    # 记录的工作目录已不存在（脚本里 cd 失败）
    chdir_failed: bool = False
    # 已自动回退到家目录重试过
    cwd_reset: bool = False


class SSHBackend:
    """维护到目标机的 root SSH 连接。"""

    def __init__(self) -> None:
        self._conn: asyncssh.SSHClientConnection | None = None
        self._target: tuple[Any, ...] | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        self._target = None
        if conn is not None:
            try:
                conn.close()
                await asyncio.wait_for(conn.wait_closed(), timeout=5)
            except Exception:  # noqa: BLE001 - 关闭失败不影响重连
                pass

    async def run(
        self,
        cfg: dict[str, Any],
        command: str,
        *,
        stdin_data: bytes | str | None = None,
        timeout: float = 60.0,
    ) -> ExecResult:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                return await self._run_once(cfg, command, stdin_data=stdin_data, timeout=timeout)
            except (asyncssh.ConnectionLost, asyncssh.ChannelOpenError, OSError) as e:
                last_error = e
                logger.warning("SSH 通道异常（第 %s 次）: %s", attempt, e)
                async with self._lock:
                    await self._drop()
                if attempt == 2:
                    break
            except asyncssh.Error as e:
                last_error = e
                break

        raise RemoteShellError(f"SSH 执行失败: {_short_error(last_error)}")

    async def _run_once(
        self,
        cfg: dict[str, Any],
        command: str,
        *,
        stdin_data: bytes | str | None,
        timeout: float,
    ) -> ExecResult:
        conn = await self._connection(cfg)
        ssh_cfg = cfg.get("ssh") if isinstance(cfg.get("ssh"), dict) else {}
        max_bytes = as_int(ssh_cfg.get("max_output_bytes"), 2 * 1024 * 1024, minimum=4096)

        process = await conn.create_process(
            command,
            stdin=asyncssh.PIPE if stdin_data is not None else asyncssh.DEVNULL,
            stdout=asyncssh.PIPE,
            stderr=asyncssh.STDOUT,
            encoding=None,
        )

        buffer = bytearray()
        truncated = False

        if stdin_data is not None:
            payload = stdin_data.encode("utf-8") if isinstance(stdin_data, str) else stdin_data
            if payload:
                process.stdin.write(payload)
            process.stdin.write_eof()

        async def drain() -> None:
            nonlocal truncated
            while True:
                chunk = await process.stdout.read(_READ_CHUNK)
                if not chunk:
                    break
                if len(buffer) < max_bytes:
                    take = min(len(chunk), max_bytes - len(buffer))
                    buffer.extend(chunk[:take])
                    if take < len(chunk):
                        truncated = True
                else:
                    truncated = True

        try:
            await asyncio.wait_for(drain(), timeout=timeout + 15)
            await asyncio.wait_for(process.wait(), timeout=10)
            exit_code = process.exit_status
            if exit_code is None:
                exit_code = 137
        except asyncio.TimeoutError:
            log_name = "命令" if stdin_data is None else "文件传输"
            logger.warning("远端%s超时: %.0fs", log_name, timeout)
            try:
                process.kill()
            except Exception:  # noqa: BLE001 - 进程可能已退出
                pass
            return ExecResult(
                exit_code=124,
                output=_decode(buffer) + f"\n[remote_shell] {log_name}超时，已被强制终止",
                timed_out=True,
            )
        finally:
            try:
                process.close()
            except Exception:  # noqa: BLE001
                pass

        if truncated:
            buffer.extend("\n[remote_shell] 输出超过上限，后续内容已被丢弃\n".encode("utf-8"))

        return ExecResult(exit_code=int(exit_code), output=_decode(buffer))

    async def _connection(self, cfg: dict[str, Any]) -> asyncssh.SSHClientConnection:
        ssh_cfg = cfg.get("ssh") if isinstance(cfg.get("ssh"), dict) else {}
        target = (
            str(ssh_cfg.get("host") or "").strip(),
            as_int(ssh_cfg.get("port"), 22, minimum=1, maximum=65535),
            str(ssh_cfg.get("username") or "root").strip(),
            str(ssh_cfg.get("private_key") or "").strip(),
            str(ssh_cfg.get("password") or "").strip(),
        )
        if not target[0]:
            raise RemoteShellError("remote_shell.ssh.host 未配置")

        async with self._lock:
            if self._conn is not None and not self._conn.is_closed() and self._target == target:
                return self._conn

            await self._drop()
            self._conn = await self._connect(cfg)
            self._target = target
            return self._conn

    async def _connect(self, cfg: dict[str, Any]) -> asyncssh.SSHClientConnection:
        ssh_cfg = cfg.get("ssh") if isinstance(cfg.get("ssh"), dict) else {}
        host = str(ssh_cfg.get("host") or "").strip()
        port = as_int(ssh_cfg.get("port"), 22, minimum=1, maximum=65535)
        username = str(ssh_cfg.get("username") or "root").strip()
        key_path = Path(str(ssh_cfg.get("private_key") or "").strip()) if ssh_cfg.get("private_key") else None
        passphrase = str(ssh_cfg.get("passphrase") or "") or None
        password = str(ssh_cfg.get("password") or "") or None
        known_hosts_path = (
            Path(str(ssh_cfg.get("known_hosts") or "").strip())
            if ssh_cfg.get("known_hosts")
            else None
        )

        kwargs: dict[str, Any] = {
            "host": host,
            "port": port,
            "username": username,
            "connect_timeout": as_int(ssh_cfg.get("connect_timeout"), 10, minimum=3, maximum=60),
            "keepalive_interval": as_int(ssh_cfg.get("keepalive"), 30, minimum=0, maximum=300),
        }

        if key_path is not None:
            if not key_path.exists():
                raise RemoteShellError(f"SSH 私钥不存在: {key_path}")
            kwargs["client_keys"] = [str(key_path)]
            if passphrase:
                kwargs["passphrase"] = passphrase
        elif not password:
            raise RemoteShellError("SSH 既没有配置 private_key 也没有配置 password")

        if password:
            kwargs["password"] = password

        trust_on_first_use = False
        if known_hosts_path is not None:
            if known_hosts_path.exists():
                kwargs["known_hosts"] = str(known_hosts_path)
            else:
                kwargs["known_hosts"] = None
                trust_on_first_use = True

        try:
            conn = await asyncssh.connect(**kwargs)
        except asyncssh.PermissionDenied as e:
            raise RemoteShellError(f"SSH 认证失败（{username}@{host}:{port}）: {e}") from e
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as e:
            raise RemoteShellError(f"SSH 连接失败（{host}:{port}）: {_short_error(e)}") from e

        if trust_on_first_use and known_hosts_path is not None:
            _remember_host_key(conn, known_hosts_path, host, port)

        logger.info("已连接远端 SSH: %s@%s:%s", username, host, port)
        return conn


def _remember_host_key(
    conn: asyncssh.SSHClientConnection,
    path: Path,
    host: str,
    port: int,
) -> None:
    try:
        key = conn.get_server_host_key()
        if key is None:
            return
        host_field = host if port == 22 else f"[{host}]:{port}"
        public = key.export_public_key("openssh")
        text = public.decode("utf-8", errors="replace").strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{host_field} {text}\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info("首次连接，已记录主机公钥到 %s", path)
    except Exception as e:  # noqa: BLE001 - 记录失败不应阻断执行
        logger.warning("记录 SSH 主机公钥失败: %s", e)


def _decode(data: bytes | bytearray) -> str:
    return bytes(data).decode("utf-8", errors="replace")


def _short_error(error: Exception | None) -> str:
    if error is None:
        return "未知错误"
    text = str(error).strip() or type(error).__name__
    return text[:300]

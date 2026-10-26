"""远端命令拼装与输出处理。

这里全部是纯函数，不依赖 asyncssh / NoneBot，方便单独跑单测：

- 把用户命令包进「群用户身份 + cwd 保持 + 资源限制 + 超时」的 bash 脚本；
- 解析执行结束后由 `trap EXIT` 打印的 cwd 标记；
- 清理 ANSI 控制符、截断超长输出；
- 校验并归一化远端路径。
"""

from __future__ import annotations

import posixpath
import re
import shlex

# 不在 stdout 里出现的记录分隔符，用作控制标记前缀。
MARKER = "\x1e"
CWD_MARKER = f"{MARKER}CWD:"
CHDIR_FAILED_MARKER = f"{MARKER}CHDIR_FAILED"

_PATH_ENV_TAIL = (
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
_ROOT_PATH = _PATH_ENV_TAIL

_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[@-_]"  # 其他两字节转义
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def path_env(home: str) -> str:
    """群用户执行命令时使用的 PATH（含用户级安装目录）。"""

    base = home.rstrip("/") or "/"
    return f"{base}/.local/bin:{base}/.cargo/bin:{base}/go/bin:{_PATH_ENV_TAIL}"


def build_script(
    command: str,
    *,
    cwd: str,
    max_processes: int = 256,
    max_file_size_mb: int = 2048,
    umask: str = "077",
) -> str:
    """生成在群用户 shell 里执行的脚本。

    `command` 是用户（模型）给的 shell 代码，会被原样放进脚本；外层再整体
    `shlex.quote` 交给 `bash -c`，所以引号/换行/`$()` 都不会被二次解释。
    """

    quoted_cwd = shlex.quote(cwd)
    return "\n".join(
        [
            f"umask {umask} 2>/dev/null || true",
            f'trap \'printf "{MARKER}CWD:%s" "$PWD"\' EXIT',
            f"cd -- {quoted_cwd} 2>/dev/null || {{ printf '{CHDIR_FAILED_MARKER}'; exit 97; }}",
            f"ulimit -u {int(max_processes)} 2>/dev/null || true",
            # bash 的 -f 以 1024 字节为块。
            f"ulimit -f {int(max_file_size_mb) * 1024} 2>/dev/null || true",
            "ulimit -c 0 2>/dev/null || true",
            command,
        ]
    )


def build_remote_command(
    *,
    user: str,
    home: str,
    script: str,
    timeout: int,
    shell: str = "/bin/bash",
    runuser_bin: str = "/usr/sbin/runuser",
    timeout_bin: str = "/usr/bin/timeout",
) -> str:
    """拼出 root 身份下发的完整命令：降权到群用户并用 timeout 限时。"""

    env_items = [
        f"HOME={shlex.quote(home)}",
        f"USER={shlex.quote(user)}",
        f"LOGNAME={shlex.quote(user)}",
        f"SHELL={shlex.quote(shell)}",
        "TERM=dumb",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        f"PATH={shlex.quote(path_env(home))}",
    ]
    return (
        f"{runuser_bin} -u {shlex.quote(user)} -- "
        f"env -i {' '.join(env_items)} "
        f"{timeout_bin} -k 5 {int(timeout)} {shlex.quote(shell)} -c {shlex.quote(script)}"
    )


def build_user_cat_command(
    *,
    user: str,
    home: str,
    path: str,
    cat_bin: str = "/bin/cat",
    runuser_bin: str = "/usr/sbin/runuser",
) -> str:
    """以群用户身份读取文件原始字节（用于把文件拉回本地）。

    不带 cwd/ulimit 包装，避免任何标记混进二进制内容。
    """

    env_items = [
        f"HOME={shlex.quote(home)}",
        f"USER={shlex.quote(user)}",
        f"LOGNAME={shlex.quote(user)}",
        "TERM=dumb",
        f"PATH={shlex.quote(path_env(home))}",
    ]
    return (
        f"{runuser_bin} -u {shlex.quote(user)} -- "
        f"env -i {' '.join(env_items)} {shlex.quote(cat_bin)} -- {shlex.quote(path)}"
    )


def build_user_raw_command(
    *,
    user: str,
    home: str,
    script: str,
    shell: str = "/bin/bash",
    runuser_bin: str = "/usr/sbin/runuser",
) -> str:
    """以群用户身份执行脚本，不加 cwd/ulimit/标记（用于二进制流）。"""

    env_items = [
        f"HOME={shlex.quote(home)}",
        f"USER={shlex.quote(user)}",
        f"LOGNAME={shlex.quote(user)}",
        f"SHELL={shlex.quote(shell)}",
        "TERM=dumb",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        f"PATH={shlex.quote(path_env(home))}",
    ]
    return (
        f"{runuser_bin} -u {shlex.quote(user)} -- "
        f"env -i {' '.join(env_items)} {shlex.quote(shell)} -c {shlex.quote(script)}"
    )


# ── 后台任务（job）────────────────────────────────────────────────────────


def build_job_launcher(
    *,
    command: str,
    cwd: str,
    max_runtime: int,
    status_path: str,
    pid_path: str,
    shell: str = "/bin/bash",
    timeout_bin: str = "/usr/bin/timeout",
) -> str:
    """生成后台任务的启动脚本（由 setsid 拉成独立会话）。

    脚本自己写 pid 和退出码，父进程只负责把它甩到后台。
    """

    return "\n".join(
        [
            "#!/bin/bash",
            f"echo $$ > {shlex.quote(pid_path)}",
            f"cd -- {shlex.quote(cwd)} 2>/dev/null || {{ echo 97 > {shlex.quote(status_path)}; exit 97; }}",
            f"{timeout_bin} -k 10 {int(max_runtime)} {shlex.quote(shell)} -c {shlex.quote(command)}",
            "code=$?",
            f"echo \"$code\" > {shlex.quote(status_path)}",
        ]
    )


def build_job_launch_command(job_dir: str) -> str:
    """在 job 目录里拉起 run.sh，等它写出 .pid 后打印出来。"""

    quoted = shlex.quote(job_dir)
    return "\n".join(
        [
            f"cd -- {quoted} || exit 90",
            "chmod 700 run.sh 2>/dev/null || true",
            "setsid ./run.sh > output.log 2>&1 < /dev/null &",
            "for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do",
            "  [ -s .pid ] && break",
            "  sleep 0.2",
            "done",
            "cat .pid 2>/dev/null || echo 0",
        ]
    )


def build_job_status_command(job_dir: str) -> str:
    quoted = shlex.quote(job_dir)
    return "\n".join(
        [
            f"d={quoted}",
            f"printf '{MARKER}SIZE:%s\\n' \"$(wc -c < \"$d/output.log\" 2>/dev/null || echo 0)\"",
            f"if [ -f \"$d/status\" ]; then printf '{MARKER}EXIT:%s\\n' \"$(cat \"$d/status\" 2>/dev/null)\"; fi",
            "p=$(cat \"$d/.pid\" 2>/dev/null || true)",
            f"if [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null; then printf '{MARKER}ALIVE:1'; "
            f"else printf '{MARKER}ALIVE:0'; fi",
        ]
    )


def build_job_kill_command(job_dir: str) -> str:
    quoted = shlex.quote(job_dir)
    return "\n".join(
        [
            f"d={quoted}",
            "p=$(cat \"$d/.pid\" 2>/dev/null || true)",
            "if [ -n \"$p\" ]; then",
            "  kill -TERM -- \"-$p\" 2>/dev/null || kill -TERM \"$p\" 2>/dev/null || true",
            "fi",
            "sleep 1",
            "if [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null; then",
            "  kill -KILL -- \"-$p\" 2>/dev/null || true",
            "fi",
            "[ -f \"$d/status\" ] || echo 143 > \"$d/status\"",
            "echo done",
        ]
    )


def parse_job_status(output: str) -> dict[str, object]:
    """解析 job 状态脚本输出。"""

    result: dict[str, object] = {"size": 0, "exit": None, "alive": False}
    for line in output.splitlines():
        line = line.strip().lstrip(MARKER)
        if line.startswith("SIZE:"):
            try:
                result["size"] = int(line[5:].strip() or 0)
            except ValueError:
                result["size"] = 0
        elif line.startswith("EXIT:"):
            try:
                result["exit"] = int(line[5:].strip())
            except ValueError:
                result["exit"] = None
        elif line.startswith("ALIVE:"):
            result["alive"] = line[6:].strip() == "1"
    return result


def build_root_command(
    *,
    script: str,
    timeout: int,
    shell: str = "/bin/bash",
    timeout_bin: str = "/usr/bin/timeout",
) -> str:
    """超级管理员模式：以 root 身份直接执行（不降权），仅限显式授权场景。"""

    env_items = [
        "HOME=/root",
        "USER=root",
        "LOGNAME=root",
        f"SHELL={shlex.quote(shell)}",
        "TERM=dumb",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        f"PATH={shlex.quote(_ROOT_PATH)}",
    ]
    return (
        f"env -i {' '.join(env_items)} "
        f"{timeout_bin} -k 5 {int(timeout)} {shlex.quote(shell)} -c {shlex.quote(script)}"
    )


def split_cwd_marker(output: str) -> tuple[str, str | None, bool]:
    """剥离尾部 cwd 标记，返回 (正文, 新 cwd, 是否 chdir 失败)。"""

    chdir_failed = CHDIR_FAILED_MARKER in output
    if chdir_failed:
        output = output.replace(CHDIR_FAILED_MARKER, "")

    index = output.rfind(CWD_MARKER)
    if index == -1:
        return output, None, chdir_failed

    tail = output[index + len(CWD_MARKER) :]
    newline = tail.find("\n")
    if newline != -1:
        tail = tail[:newline]
    new_cwd = tail.strip("\r ")
    return output[:index], (new_cwd or None), chdir_failed


def strip_ansi(text: str) -> str:
    """去掉 ANSI 颜色/光标控制与不可见控制符，保留换行和 tab。"""

    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "")
    return text


def truncate_output(text: str, limit: int) -> tuple[str, bool]:
    """头尾保留式截断，避免只剩开头看不到最终报错。"""

    if limit <= 0 or len(text) <= limit:
        return text, False

    note = f"\n…[输出过长，已省略 {len(text) - limit} 字符；可重跑并配合 head/tail/sed 查看]…\n"
    head_len = max(1, limit * 2 // 3)
    tail_len = max(1, limit - head_len)
    return text[:head_len] + note + text[-tail_len:], True


def normalize_path(
    raw: str,
    *,
    cwd: str,
    home: str,
    require_home: bool = False,
) -> str:
    """把工具参数里的路径解析成远端绝对路径，并可选限制在家目录内。"""

    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        value = "."
    if "\x00" in value or _CONTROL_RE.search(value):
        raise ValueError("路径包含控制字符")
    if value.startswith("~"):
        raise ValueError("不支持 ~ 展开，请使用绝对路径或相对当前目录的路径")

    if not value.startswith("/"):
        value = posixpath.join(cwd or "/", value)

    normalized = posixpath.normpath(value)
    if not normalized.startswith("/"):
        raise ValueError("路径不合法")

    if require_home:
        base = home.rstrip("/") or "/"
        if normalized != base and not normalized.startswith(base + "/"):
            raise ValueError(f"路径必须位于群用户家目录 {base} 内")

    return normalized


def decode_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")

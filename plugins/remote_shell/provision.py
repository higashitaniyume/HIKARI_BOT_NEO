"""群专属 Linux 用户的开通与安全校验。

所有脚本都跑在 root SSH 会话里，用 `printf '\\036KEY:value'` 回传结构化结果，
由插件解析。脚本本身幂等：重复执行不会重建用户、也不会重复追加 umask。
"""

from __future__ import annotations

import re

from .runner import MARKER

_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

_SAFE_SUDO_STATES = {"deny", "missing"}
_SAFE_LOCK_STATES = {"L", "LK", "NP"}


def linux_user(group_id: str, prefix: str = "qqg_") -> str:
    """把群号映射成合法、稳定的 Linux 用户名（qqg_<纯数字群号>）。"""

    digits = re.sub(r"\D", "", str(group_id or ""))
    if not digits:
        raise ValueError("群号不合法，无法创建 Linux 用户")

    name = f"{prefix}{digits}"
    if len(name) > 32:
        name = name[:32]
    if not _USER_RE.fullmatch(name):
        raise ValueError(f"生成的 Linux 用户名不合法: {name}")
    return name


def home_for(user: str, home_base: str = "/home") -> str:
    base = (home_base or "/home").rstrip("/") or "/"
    return f"{base}/{user}"


def build_provision_script(user: str, home: str, shell: str = "/bin/bash") -> str:
    """幂等开通：建号、锁密码、家目录 700、补 umask。"""

    return "\n".join(
        [
            "set -u",
            f"u={shell_quote(user)}",
            f"h={shell_quote(home)}",
            'if ! id -u "$u" >/dev/null 2>&1; then',
            f'  useradd -m -U -d "$h" -s {shell_quote(shell)} "$u" || exit 11',
            "fi",
            'if [ ! -d "$h" ]; then mkdir -p "$h" && chown "$u:$u" "$h" || exit 12; fi',
            'chmod 700 "$h" 2>/dev/null || true',
            'passwd -l "$u" >/dev/null 2>&1 || true',
            'for f in "$h/.profile" "$h/.bashrc"; do',
            '  [ -f "$f" ] || : > "$f"',
            '  chown "$u:$u" "$f" 2>/dev/null || true',
            "  grep -qx 'umask 077' \"$f\" 2>/dev/null || printf '\\numask 077\\n' >> \"$f\"",
            "done",
            f"printf '{MARKER}PROVISION_OK'",
        ]
    )


def build_verify_script(user: str, home: str) -> str:
    """输出用户组、sudo、家目录权限、密码锁定状态等，供插件断言。"""

    return "\n".join(
        [
            f"u={shell_quote(user)}",
            f"h={shell_quote(home)}",
            'if ! id -u "$u" >/dev/null 2>&1; then',
            f"  printf '{MARKER}NOUSER'",
            "  exit 0",
            "fi",
            f"printf '{MARKER}USER:%s\\n' \"$(id -un \"$u\")\"",
            f"printf '{MARKER}UID:%s\\n' \"$(id -u \"$u\")\"",
            f"printf '{MARKER}GROUPS:%s\\n' \"$(id -nG \"$u\" | tr ' ' ',')\"",
            "if command -v sudo >/dev/null 2>&1; then",
            # 注意：`sudo -l -U user` 在用户完全无权时也可能返回 0，只有真正
            # 降权执行一次才能确认真实权限。账号已锁密码，交互式 sudo 也进不去。
            '  if runuser -u "$u" -- sudo -n true >/dev/null 2>&1; then s=allow; else s=deny; fi',
            "else",
            "  s=missing",
            "fi",
            f"printf '{MARKER}SUDO:%s\\n' \"$s\"",
            f"printf '{MARKER}PERM:%s\\n' \"$(stat -c %a \"$h\" 2>/dev/null)\"",
            f"printf '{MARKER}LOCK:%s\\n' \"$(passwd -S \"$u\" 2>/dev/null | awk '{{print $2}}')\"",
            f"printf '{MARKER}SHELL:%s\\n' \"$(getent passwd \"$u\" | cut -d: -f7)\"",
            f"printf '{MARKER}HOMEDIR:%s\\n' \"$(getent passwd \"$u\" | cut -d: -f6)\"",
        ]
    )


def shell_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def parse_verify(output: str) -> dict[str, str]:
    """解析校验脚本的结构化输出。"""

    info: dict[str, str] = {}
    if f"{MARKER}NOUSER" in output:
        info["NOUSER"] = "1"
        return info

    for line in output.splitlines():
        line = line.strip().lstrip(MARKER)
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key in {"USER", "UID", "GROUPS", "SUDO", "PERM", "LOCK", "SHELL", "HOMEDIR"}:
            info[key] = value.strip()
    return info


def verify_problems(
    info: dict[str, str],
    *,
    user: str,
    home: str,
    shell: str = "/bin/bash",
) -> list[str]:
    """返回不满足隔离要求的问题列表；为空表示校验通过。"""

    if "NOUSER" in info:
        return ["用户不存在（useradd 未生效）"]

    problems: list[str] = []

    groups = [item for item in info.get("GROUPS", "").split(",") if item]
    if groups != [user]:
        problems.append(f"用户组异常: {info.get('GROUPS', '') or '-'}（应只有自身主组）")

    sudo_state = info.get("SUDO", "")
    if sudo_state not in _SAFE_SUDO_STATES:
        problems.append(f"该用户可执行 sudo（状态 {sudo_state or 'unknown'}）")

    perm = info.get("PERM", "")
    if perm != "700":
        problems.append(f"家目录权限为 {perm or 'unknown'}，应为 700")

    lock_state = info.get("LOCK", "")
    if lock_state not in _SAFE_LOCK_STATES:
        problems.append(f"密码状态为 {lock_state or 'unknown'}，应保持锁定")

    actual_shell = info.get("SHELL", "")
    if actual_shell != shell:
        problems.append(f"登录 shell 为 {actual_shell or 'unknown'}，应为 {shell}")

    actual_home = (info.get("HOMEDIR") or "").rstrip("/")
    if actual_home and actual_home != home.rstrip("/"):
        problems.append(f"家目录为 {actual_home}，应为 {home}")

    uid = info.get("UID", "")
    if not uid.isdigit() or int(uid) == 0:
        problems.append(f"UID 异常: {uid or 'unknown'}")

    return problems

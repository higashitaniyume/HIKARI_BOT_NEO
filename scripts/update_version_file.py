from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate HIKARI BOT NEO version.json from Git history.")
    parser.add_argument(
        "--project-root",
        default=Path(__file__).resolve().parent.parent,
        type=Path,
        help="Project root containing .git and pyproject.toml.",
    )
    parser.add_argument(
        "--output",
        default="version.json",
        help="Output path, absolute or relative to project root.",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = project_root / output

    versions = build_versions(project_root)
    data = {"versions": versions}
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current = versions[-1] if versions else {"version": "unknown", "git_hash": "unknown", "title": "unknown"}
    print(f"wrote {output} ({current['version']} {current['git_hash']})")
    return 0


def build_versions(project_root: Path) -> list[dict[str, str]]:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%h%x1f%s%x1e"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    versions: list[dict[str, str]] = []
    text = result.stdout.decode("utf-8", errors="replace")
    for record in text.split("\x1e"):
        record = record.strip("\r\n")
        if not record or "\x1f" not in record:
            continue
        git_hash, title = record.split("\x1f", 1)
        versions.append(
            {
                "version": f"0.0.{len(versions) + 1}",
                "git_hash": git_hash.strip() or "unknown",
                "title": title.strip() or "unknown",
            }
        )
    return versions


if __name__ == "__main__":
    raise SystemExit(main())

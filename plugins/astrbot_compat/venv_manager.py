"""Shared plugin virtual environment manager.

Uses a single venv at ``UserData/astrbot_plugins/.venv/`` for all
astrbot plugin dependencies, keeping them isolated from the bot's
core environment.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

logger = logging.getLogger("AstrBotCompat.Venv")


class PluginVenvManager:
    """Manages a shared virtual environment for astrbot plugin dependencies."""

    def __init__(self, venv_dir: Path):
        self.venv_dir = venv_dir.resolve()
        self._python = self._resolve_python()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_venv(self) -> Path:
        """Create the shared venv if it doesn't exist. Return python path."""
        if self.venv_dir.exists() and (self.venv_dir / "pyvenv.cfg").exists():
            logger.debug("Shared plugin venv already exists at %s", self.venv_dir)
            return self._python

        logger.info("Creating shared plugin venv at %s ...", self.venv_dir)
        started_at = time.monotonic()
        self.venv_dir.mkdir(parents=True, exist_ok=True)

        python_exe = self._find_host_python()
        result = subprocess.run(
            [str(python_exe), "-m", "venv", str(self.venv_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.error("Failed to create plugin venv: %s", stderr)
            raise RuntimeError(f"Failed to create plugin venv: {stderr}")

        self._python = self._resolve_python()
        elapsed = time.monotonic() - started_at
        logger.info("Shared plugin venv created in %.2fs at %s", elapsed, self.venv_dir)
        return self._python

    def install_deps(self, requirements: Sequence[str]) -> list[str]:
        """Install dependencies into the shared venv. Return installed package names."""
        if not requirements:
            return []

        self.ensure_venv()
        pip = self._venv_bin("pip")

        logger.info(
            "Installing %d package(s) into shared plugin venv: %s",
            len(requirements),
            requirements,
        )
        started_at = time.monotonic()
        result = subprocess.run(
            [
                str(pip), "install",
                "--quiet",
                *requirements,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed = time.monotonic() - started_at

        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.error(
                "pip install failed after %.2fs: %s",
                elapsed,
                stderr,
            )
            raise RuntimeError(f"Failed to install plugin deps: {stderr}")

        installed = [r for r in requirements if r.strip()]
        logger.info(
            "Installed %d package(s) in %.2fs: %s",
            len(installed),
            elapsed,
            installed,
        )
        return installed

    def rebuild_all(self, all_requirements: Sequence[Sequence[str]]) -> None:
        """Destroy and recreate the venv, installing all given requirements."""
        import shutil

        if self.venv_dir.exists():
            logger.info("Removing existing plugin venv at %s ...", self.venv_dir)
            shutil.rmtree(self.venv_dir)
            logger.debug("Plugin venv directory removed")

        all_packages = sorted({
            pkg.strip()
            for group in all_requirements
            for pkg in group
            if pkg.strip()
        })
        if all_packages:
            logger.info(
                "Rebuilding plugin venv with %d package(s) across %d plugin(s) ...",
                len(all_packages),
                len(all_requirements),
            )
            self.install_deps(all_packages)
        else:
            logger.info("No plugin dependencies to install, creating empty venv ...")
            self.ensure_venv()

        logger.info(
            "Plugin venv rebuilt — %d package(s) available",
            len(all_packages),
        )

    def add_to_path(self) -> None:
        """Insert the venv's site-packages into ``sys.path`` if not already present."""
        site_packages = self._find_site_packages()
        if site_packages and str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
            logger.debug("Added to sys.path: %s", site_packages)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_python(self) -> Path:
        """Return the path to python inside the venv."""
        if sys.platform == "win32":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def _venv_bin(self, name: str) -> Path:
        """Return path to a binary inside the venv's bin/Scripts dir."""
        if sys.platform == "win32":
            return self.venv_dir / "Scripts" / f"{name}.exe"
        return self.venv_dir / "bin" / name

    def _find_host_python(self) -> Path:
        """Return the current host python executable for creating venvs."""
        return Path(sys.executable)

    def _find_site_packages(self) -> Path | None:
        """Locate the site-packages directory inside the venv."""
        if not self.venv_dir.exists():
            return None

        python = self._resolve_python()
        if not python.exists():
            return None

        try:
            result = subprocess.run(
                [
                    str(python), "-c",
                    "import site; print(site.getsitepackages()[0])",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                p = Path(result.stdout.strip())
                if p.is_dir():
                    return p
        except (OSError, subprocess.TimeoutExpired):
            logger.debug("Failed to query site-packages path from venv python")

        # Fallback: guess based on common patterns
        candidates = [
            self.venv_dir / "lib" / "python3.*" / "site-packages",
        ]
        if sys.platform == "win32":
            candidates = [
                self.venv_dir / "Lib" / "site-packages",
            ]

        import glob
        for pattern in candidates:
            matches = glob.glob(str(pattern))
            if matches:
                return Path(matches[0])
        logger.debug("Could not locate site-packages in plugin venv (fallback guess missed)")
        return None

    def parse_requirements(self, requirements_path: Path) -> list[str]:
        """Read a ``requirements.txt`` and return a list of package specs."""
        if not requirements_path.exists():
            return []
        try:
            text = requirements_path.read_text(encoding="utf-8")
            lines: list[str] = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                lines.append(line)
            logger.debug(
                "Parsed %d deps from %s",
                len(lines),
                requirements_path,
            )
            return lines
        except OSError as e:
            logger.warning("Failed to read requirements %s: %s", requirements_path, e)
            return []

#!/usr/bin/env python3

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCH_SCRIPT = PROJECT_ROOT / "scripts" / "launch_sciplotter.py"
ICON_PATH = PROJECT_ROOT / "assets" / "launcher-icon.png"
APP_NAME = "SciPlotter"
COMMENT = "Launch SciPlotter"


def run_command(args: list[str]) -> None:
    subprocess.run(args, check=True)


def ensure_pip() -> None:
    try:
        import pip  # noqa: F401
    except ImportError:
        run_command([sys.executable, "-m", "ensurepip", "--upgrade"])


def install_dependencies() -> None:
    requirements = PROJECT_ROOT / "requirements.txt"
    ensure_pip()
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run_command([sys.executable, "-m", "pip", "install", "-r", str(requirements)])


def ensure_executable(path: Path) -> None:
    if os.name == "nt":
        return
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def quoted_windows_command(parts: list[str]) -> str:
    return subprocess.list2cmdline(parts)


def quoted_shell_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def quoted_desktop_exec(parts: list[str]) -> str:
    escaped_parts: list[str] = []
    for part in parts:
        escaped = part.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        escaped_parts.append(f'"{escaped}"')
    return " ".join(escaped_parts)


def install_linux() -> list[Path]:
    exec_command = quoted_desktop_exec([sys.executable, str(LAUNCH_SCRIPT)])
    desktop_entry = textwrap.dedent(
        f"""\
        [Desktop Entry]
        Version=1.0
        Type=Application
        Name={APP_NAME}
        Comment={COMMENT}
        Exec={exec_command}
        Path={PROJECT_ROOT}
        Icon={ICON_PATH}
        Terminal=false
        StartupNotify=true
        Categories=Science;
        """
    )
    app_entry = Path.home() / ".local/share/applications/sciplotter.desktop"
    desktop_shortcut = Path.home() / "Desktop/SciPlotter.desktop"
    write_text(app_entry, desktop_entry)
    write_text(desktop_shortcut, desktop_entry)
    ensure_executable(app_entry)
    ensure_executable(desktop_shortcut)
    return [app_entry, desktop_shortcut]


def install_macos() -> list[Path]:
    project_root = shlex.quote(str(PROJECT_ROOT))
    exec_command = quoted_shell_command([sys.executable, str(LAUNCH_SCRIPT)])
    command = textwrap.dedent(
        f"""\
        #!/bin/bash
        cd {project_root}
        exec {exec_command} "$@"
        """
    )
    applications_launcher = Path.home() / "Applications/SciPlotter.command"
    desktop_launcher = Path.home() / "Desktop/SciPlotter.command"
    write_text(applications_launcher, command)
    write_text(desktop_launcher, command)
    ensure_executable(applications_launcher)
    ensure_executable(desktop_launcher)
    return [applications_launcher, desktop_launcher]


def install_windows() -> list[Path]:
    project_root = quoted_windows_command([str(PROJECT_ROOT)])
    exec_command = quoted_windows_command([sys.executable, str(LAUNCH_SCRIPT)])
    command = textwrap.dedent(
        f"""\
        @echo off
        setlocal
        cd /d {project_root}
        {exec_command} %*
        endlocal
        """
    )
    desktop = Path.home() / "Desktop/SciPlotter.bat"
    start_menu = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs/SciPlotter.bat"
    write_text(desktop, command)
    write_text(start_menu, command)
    return [desktop, start_menu]


def install_launchers() -> list[Path]:
    if sys.platform == "win32":
        return install_windows()
    if sys.platform == "darwin":
        return install_macos()
    return install_linux()


def main() -> int:
    if not LAUNCH_SCRIPT.exists():
        print(f"Launch script not found: {LAUNCH_SCRIPT}", file=sys.stderr)
        return 1

    install_dependencies()
    ensure_executable(LAUNCH_SCRIPT)
    launchers = install_launchers()

    print("SciPlotter launcher installed:")
    for launcher in launchers:
        print(f"- {launcher}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
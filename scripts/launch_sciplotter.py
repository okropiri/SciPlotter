#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sciplotter_backend import linux_integration
from sciplotter_backend import runtime
from sciplotter_backend.server import run_server


DEFAULT_HOST = os.environ.get("SCIPLOTTER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("SCIPLOTTER_PORT", "5000"))
APP_ENTRY = PROJECT_ROOT / "app.py"


def launcher_log_path() -> Path:
    return runtime.user_cache_dir() / "launcher.log"


def log_launcher_event(message: str) -> None:
    try:
        runtime.ensure_runtime_dirs()
        with launcher_log_path().open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch SciPlotter and open it in the browser.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--server", action="store_true", help=argparse.SUPPRESS)
    argv = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    return parser.parse_args(argv)


def build_url(host: str, port: int, path: str = "/") -> str:
    return f"http://{host}:{port}{path}"


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def is_server_ready(host: str, port: int) -> bool:
    health_url = build_url(host, port, "/health")
    try:
        with urllib.request.urlopen(health_url, timeout=1.0) as response:
            return 200 <= getattr(response, "status", 0) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def build_server_command(host: str, port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--server", "--host", host, "--port", str(port)]
    return [sys.executable, str(APP_ENTRY), "--host", host, "--port", str(port)]


def start_server(host: str, port: int) -> subprocess.Popen[bytes]:
    runtime.ensure_runtime_dirs()
    log_dir = runtime.user_cache_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "server.log"
    log_handle = log_file.open("ab")

    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    command = build_server_command(host, port)
    log_launcher_event(f"Starting server command: {' '.join(command)}")
    log_launcher_event(f"Server cwd: {PROJECT_ROOT}")

    return subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
        close_fds=sys.platform != "win32",
    )


def wait_for_server(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_server_ready(host, port):
            return True
        time.sleep(0.5)
    return False


def launch_browser(url: str) -> None:
    if webbrowser.open(url, new=2):
        log_launcher_event(f"Opened browser via webbrowser for {url}")
        return

    if sys.platform == 'darwin':
        subprocess.run(['/usr/bin/open', url], check=True)
        log_launcher_event(f"Opened browser via macOS open for {url}")
        return

    if sys.platform.startswith('linux'):
        subprocess.run(['xdg-open', url], check=True)
        log_launcher_event(f"Opened browser via xdg-open for {url}")
        return

    raise RuntimeError(f"Unable to open browser for {url}")


def main() -> int:
    try:
        args = parse_args()
        log_launcher_event(f"Launcher starting on platform={sys.platform} frozen={getattr(sys, 'frozen', False)}")
        if args.server:
            log_launcher_event(f"Running embedded server on {args.host}:{args.port}")
            run_server(args.host, args.port)
            return 0

        if sys.platform.startswith('linux'):
            try:
                linux_integration.integrate_appimage()
            except Exception as exc:
                log_launcher_event(f"Linux integration skipped: {exc}")

        app_url = build_url(args.host, args.port)

        if not is_server_ready(args.host, args.port):
            if is_port_open(args.host, args.port):
                message = f"Port {args.port} is busy but SciPlotter is not responding on {app_url}."
                log_launcher_event(message)
                print(message, file=sys.stderr)
                return 1

            start_server(args.host, args.port)
            if not wait_for_server(args.host, args.port, args.timeout):
                message = f"SciPlotter did not become ready within {args.timeout} seconds."
                log_launcher_event(message)
                print(message, file=sys.stderr)
                return 1

        if not args.no_browser:
            launch_browser(app_url)

        log_launcher_event(f"Launcher ready: {app_url}")
        print(app_url)
        return 0
    except Exception:
        log_launcher_event("Launcher crashed:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
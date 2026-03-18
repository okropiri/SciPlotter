#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from sciplotter_backend import runtime
from sciplotter_backend.server import run_server


DEFAULT_HOST = os.environ.get("SCIPLOTTER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("SCIPLOTTER_PORT", "5000"))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ENTRY = PROJECT_ROOT / "app.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch SciPlotter and open it in the browser.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--server", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


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

    return subprocess.Popen(
        build_server_command(host, port),
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
    webbrowser.open(url, new=2)


def main() -> int:
    args = parse_args()
    if args.server:
        run_server(args.host, args.port)
        return 0

    app_url = build_url(args.host, args.port)

    if not is_server_ready(args.host, args.port):
        if is_port_open(args.host, args.port):
            print(f"Port {args.port} is busy but SciPlotter is not responding on {app_url}.", file=sys.stderr)
            return 1

        start_server(args.host, args.port)
        if not wait_for_server(args.host, args.port, args.timeout):
            print(f"SciPlotter did not become ready within {args.timeout} seconds.", file=sys.stderr)
            return 1

    if not args.no_browser:
        launch_browser(app_url)

    print(app_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
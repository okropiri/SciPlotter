from __future__ import annotations

import os
import socket
import subprocess

from . import create_app


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def run_server(host: str, port: int, *, force: bool = False) -> None:
    if _port_in_use(host, port):
        print(f'Port {port} is already in use on {host}.')
        if force and os.name == 'posix':
            try:
                subprocess.run(['fuser', '-k', f'{port}/tcp'], check=False)
            except Exception:
                pass
            if _port_in_use(host, port):
                print('Port still busy. Aborting.')
                return
        else:
            return

    app = create_app()
    print(f'Starting SciPlotter server on http://{host}:{port}')
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
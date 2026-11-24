"""Check that a packaged executable can serve the SciPlotter interface."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if os.name == 'nt':
        if process.poll() is None:
            subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def smoke_test(executable: Path) -> None:
    executable = executable.resolve(strict=True)
    host = '127.0.0.1'
    with socket.socket() as listener:
        listener.bind((host, 0))
        port = listener.getsockname()[1]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base_url = f'http://{host}:{port}'
    with tempfile.TemporaryDirectory(prefix='sciplotter-smoke-') as directory:
        log_path = Path(directory) / 'server.log'
        with log_path.open('wb') as log_file:
            process = subprocess.Popen(
                [str(executable), '--server', '--host', host, '--port', str(port)],
                cwd=directory,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != 'nt',
            )
            try:
                deadline = time.monotonic() + 45
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with opener.open(base_url + '/health', timeout=1) as response:
                            if json.load(response).get('status') == 'ok':
                                break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError(f'Server did not start (exit status {process.poll()}).')

                for path, expected in (
                    ('/', b'<title>Histogram</title>'),
                    ('/index.html', b'<title>Per-event waveform viewer</title>'),
                    ('/partials/header.html', b'SciPlotter'),
                    ('/vendor/plotly-2.27.0.min.js', b'plotly.js v2.27.0'),
                ):
                    with opener.open(base_url + path, timeout=5) as response:
                        if expected not in response.read():
                            raise RuntimeError(f'Unexpected response from {path}.')
            except Exception:
                print(log_path.read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                stop_server(process)

    print(f'SciPlotter started and served both pages and bundled assets from {executable}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    smoke_test(parser.parse_args().executable)

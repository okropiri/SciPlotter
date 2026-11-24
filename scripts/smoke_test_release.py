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
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import uproot


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


def smoke_test_root_histogram(opener, base_url: str, path: Path) -> None:
    with uproot.recreate(path) as root:
        edges = np.array([0.0, 1.0, 2.0])
        root['timing'] = (np.array([[1.0, 2.0], [3.0, 4.0]]), edges, edges)

    data = json.dumps({'kind': 'root', 'path': str(path), 'histogram_path': 'timing'}).encode()
    for action, metadata_key in (('preview', 'selected_histogram'), ('load', 'meta')):
        request = urllib.request.Request(
            f'{base_url}/api/data-importer/{action}',
            data=data,
            headers={'Content-Type': 'application/json'},
        )
        try:
            with opener.open(request, timeout=10) as response:
                metadata = json.load(response)[metadata_key]
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Packaged ROOT {action} failed: {error.read().decode()}') from error
        if metadata.get('dimension') != 2 or metadata.get('integral') != 10:
            raise RuntimeError(f'Unexpected ROOT histogram metadata from {action}: {metadata}')


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
                smoke_test_root_histogram(opener, base_url, Path(directory) / 'timing.root')
            except Exception:
                print(log_path.read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                stop_server(process)

    print(f'SciPlotter started, served the interface, and imported a ROOT histogram from {executable}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    smoke_test(parser.parse_args().executable)

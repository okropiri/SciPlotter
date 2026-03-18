from __future__ import annotations

import os
import socket
from flask import Flask, jsonify

from . import runtime


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(runtime.static_dir()))

    from .static_routes import bp as static_bp
    from .data import bp as data_bp
    from .markers import bp as markers_bp

    app.register_blueprint(static_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(markers_bp)

    @app.get('/health')
    def health():
        return jsonify({'status': 'ok', 'pid': os.getpid()})

    return app


def main() -> None:
    import argparse

    from .server import run_server

    parser = argparse.ArgumentParser(description='SciPlotter Flask backend (Waveforms + Histogram)')
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'))
    parser.add_argument('--port', default=int(os.environ.get('PORT', '5000')), type=int)
    parser.add_argument('--force', action='store_true', help='If port is busy, attempt to kill the process holding it (Linux only)')
    args = parser.parse_args()
    run_server(args.host, args.port, force=args.force)

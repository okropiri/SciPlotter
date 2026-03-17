from __future__ import annotations

import os
import socket
import subprocess
from flask import Flask, jsonify


def create_app() -> Flask:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    static_dir = os.path.join(root, 'static')
    app = Flask(__name__, static_folder=static_dir)

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

    parser = argparse.ArgumentParser(description='SciPlotter Flask backend (Waveforms + Histogram)')
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'))
    parser.add_argument('--port', default=int(os.environ.get('PORT', '5000')), type=int)
    parser.add_argument('--force', action='store_true', help='If port is busy, attempt to kill the process holding it (Linux only)')
    args = parser.parse_args()

    def _port_in_use(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0

    app = create_app()

    if _port_in_use(args.host, args.port):
        print(f"Port {args.port} is already in use on {args.host}.")
        if args.force and os.name == 'posix':
            try:
                subprocess.run(['fuser', '-k', f'{args.port}/tcp'], check=False)
            except Exception:
                pass
            if _port_in_use(args.host, args.port):
                print('Port still busy. Aborting.')
                return
        else:
            return

    print(f'Starting SciPlotter server on http://{args.host}:{args.port}')
    app.run(host=args.host, port=args.port, debug=False, threaded=True, use_reloader=False)

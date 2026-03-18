from __future__ import annotations

from datetime import datetime
from collections import deque
from pathlib import Path

from flask import Blueprint, Response, abort, request, send_from_directory

from . import runtime

bp = Blueprint('static_routes', __name__)

runtime.ensure_runtime_dirs()
STATIC_DIR = runtime.static_dir()
REQUEST_LOG_FILE = runtime.request_log_path()
RECENT_REQUESTS = deque(maxlen=200)


@bp.before_app_request
def _log_request():
    try:
        ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        path = request.path
        meth = request.method
        ua = request.headers.get('User-Agent', '-')
        line = f"[{ts}] {ip} {meth} {path} UA={ua}"
        try:
            print(line)
        except Exception:
            pass
        RECENT_REQUESTS.append({'ts': ts, 'ip': ip, 'method': meth, 'path': path, 'ua': ua})
        try:
            with REQUEST_LOG_FILE.open('a', encoding='utf-8') as lf:
                lf.write(line + "\n")
        except Exception:
            pass
    except Exception:
        pass


@bp.get('/healthz')
def healthz():
    return Response('ok', mimetype='text/plain')


@bp.get('/favicon.ico')
def favicon():
    png = STATIC_DIR / 'favicon.png'
    if png.exists():
        return send_from_directory(str(STATIC_DIR), 'favicon.png')
    svg = STATIC_DIR / 'favicon.svg'
    if svg.exists():
        return send_from_directory(str(STATIC_DIR), 'favicon.svg')
    return ('', 204)


@bp.get('/')
@bp.get('/index.html')
def index_html():
    return send_from_directory(str(STATIC_DIR), 'index.html')


@bp.get('/histogram.html')
def histogram_html():
    return send_from_directory(str(STATIC_DIR), 'histogram.html')


@bp.get('/admin/requests')
def admin_requests():
    # Keep this for debugging connectivity (optional).
    import json

    entries = list(RECENT_REQUESTS)[-50:]
    meta = {'count': len(entries), 'maxlen': RECENT_REQUESTS.maxlen, 'log_file': str(REQUEST_LOG_FILE)}
    return Response(json.dumps({'meta': meta, 'entries': entries}), mimetype='application/json')


@bp.get('/<path:filename>')
def passthrough(filename: str):
    # Do not intercept API paths.
    if filename.startswith('api/'):
        abort(404)
    fpath = STATIC_DIR / filename
    if fpath.is_file():
        return send_from_directory(str(STATIC_DIR), filename)
    return ('Not Found', 404)

from __future__ import annotations

import os
from datetime import datetime
from collections import deque

from flask import Blueprint, Response, abort, request, send_from_directory

bp = Blueprint('static_routes', __name__)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STATIC_DIR = os.path.join(ROOT, 'static')
REQUEST_LOG_FILE = os.path.join(ROOT, 'request.log')
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
            with open(REQUEST_LOG_FILE, 'a', encoding='utf-8') as lf:
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
    svg = os.path.join(STATIC_DIR, 'favicon.svg')
    if os.path.exists(svg):
        return send_from_directory(STATIC_DIR, 'favicon.svg')
    return ('', 204)


@bp.get('/')
@bp.get('/index.html')
def index_html():
    return send_from_directory(STATIC_DIR, 'index.html')


@bp.get('/histogram.html')
def histogram_html():
    return send_from_directory(STATIC_DIR, 'histogram.html')


@bp.get('/admin/requests')
def admin_requests():
    # Keep this for debugging connectivity (optional).
    import json

    entries = list(RECENT_REQUESTS)[-50:]
    meta = {'count': len(entries), 'maxlen': RECENT_REQUESTS.maxlen, 'log_file': REQUEST_LOG_FILE}
    return Response(json.dumps({'meta': meta, 'entries': entries}), mimetype='application/json')


@bp.get('/<path:filename>')
def passthrough(filename: str):
    # Do not intercept API paths.
    if filename.startswith('api/'):
        abort(404)
    fpath = os.path.join(STATIC_DIR, filename)
    if os.path.isfile(fpath):
        return send_from_directory(STATIC_DIR, filename)
    return ('Not Found', 404)

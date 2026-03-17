from __future__ import annotations

import os
from flask import Blueprint, Response, jsonify, request

from . import common

bp = Blueprint('data', __name__)


@bp.get('/api/config')
def api_config():
    try:
        return jsonify({
            'waveforms_directory': common.BASE_DIR,
            'summary_csv': common.get_summary_csv('original'),
            'smoothed_summary_csv': common.get_summary_csv('smoothed'),
            'summary_override': common.get_summary_override(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.post('/api/config/waveforms-directory')
def api_config_waveforms_directory():
    try:
        data = request.get_json(silent=True) or {}
        new_dir = (data.get('directory') or '').strip()
        if not new_dir:
            return jsonify({'error': 'directory parameter required'}), 400
        if not os.path.isabs(new_dir):
            new_dir = os.path.abspath(new_dir)
        if not os.path.isdir(new_dir):
            return jsonify({'error': f'Path is not a directory: {new_dir}'}), 400
        common.set_waveform_base_dir(new_dir)
        return jsonify({'success': True, 'new_directory': common.BASE_DIR})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.post('/api/config/summary-override')
def api_summary_override():
    try:
        data = request.get_json(silent=True) or {}
        path = data.get('path')
        if path and not os.path.isabs(path):
            path = os.path.abspath(path)
        if path and not os.path.exists(path):
            return jsonify({'error': f'file does not exist: {path}'}), 400
        common.set_summary_override(path)
        return jsonify({
            'success': True,
            'summary_override': common.get_summary_override(),
            'effective_summary': common.get_summary_csv('original'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.get('/api/browse')
def api_browse():
    """Browse directories for directory selection (directories only)."""
    try:
        path = (request.args.get('path') or '').strip()
        if not path:
            try:
                path = os.path.dirname(common.get_summary_csv('original'))
            except Exception:
                path = os.path.dirname(common.BASE_DIR)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if not os.path.isdir(path):
            return jsonify({'error': 'Directory does not exist or is not accessible'}), 400

        items = []
        parent_path = os.path.dirname(path)
        if parent_path != path:
            items.append({'name': '..', 'path': parent_path, 'type': 'parent', 'is_directory': True})

        for item_name in sorted(os.listdir(path)):
            item_path = os.path.join(path, item_name)
            try:
                if os.path.isdir(item_path):
                    items.append({'name': item_name, 'path': item_path, 'type': 'directory', 'is_directory': True})
            except Exception:
                continue

        return jsonify({'current_path': path, 'items': items})
    except PermissionError:
        return jsonify({'error': 'Permission denied to access directory'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.get('/api/browse-files')
def api_browse_files():
    """Browse directories and CSV files for picking a summary CSV."""
    try:
        path = (request.args.get('path') or '').strip()
        if not path:
            try:
                path = os.path.dirname(common.get_summary_csv('original'))
            except Exception:
                path = os.path.dirname(common.BASE_DIR)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if not os.path.isdir(path):
            return jsonify({'error': 'Directory does not exist or is not accessible'}), 400

        items = []
        parent_path = os.path.dirname(path)
        if parent_path != path:
            items.append({'name': '..', 'path': parent_path, 'type': 'parent', 'is_directory': True})

        for item_name in sorted(os.listdir(path)):
            item_path = os.path.join(path, item_name)
            try:
                if os.path.isdir(item_path):
                    items.append({'name': item_name, 'path': item_path, 'type': 'directory', 'is_directory': True})
                elif item_name.lower().endswith('.csv'):
                    items.append({'name': item_name, 'path': item_path, 'type': 'file', 'is_directory': False})
            except Exception:
                continue

        return jsonify({'current_path': path, 'items': items})
    except PermissionError:
        return jsonify({'error': 'Permission denied to access directory'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.get('/api/files')
def api_files():
    try:
        files = common.list_waveform_files()
        return jsonify({'base_dir': common.BASE_DIR, 'files': files})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.get('/api/raw_files')
def api_raw_files():
    try:
        raw = common.list_raw_waveform_files()
        return jsonify({'raw_dir': os.environ.get('RAW_WAVEFORM_DIR'), 'raw_files': raw, 'count': len(raw)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/data', methods=['GET', 'HEAD'])
def api_data():
    rel = (request.args.get('file') or '').strip()
    if not rel:
        return jsonify({'error': 'file parameter required'}), 400

    # HEAD is used by the UI to probe existence.
    if request.method == 'HEAD':
        try:
            lower = rel.lower()
            if lower.endswith('.txt') and (os.path.sep not in rel and not (os.path.altsep and os.path.altsep in rel)):
                # raw basename .txt might be in RAW_WAVEFORM_DIR or BASE_DIR
                try:
                    common.load_waveform_any(rel, full=False, max_points=10)
                    return Response(status=200)
                except Exception:
                    return Response(status=404)
            full = common.safe_path(rel)
            return Response(status=200 if os.path.exists(full) else 404)
        except Exception:
            return Response(status=404)

    full_flag = str(request.args.get('full', '')).lower() in ('1', 'true', 'yes', 'on')
    max_points = None
    if not full_flag:
        mp = request.args.get('max_points')
        if mp:
            try:
                max_points = max(1000, int(mp))
            except Exception:
                max_points = None

    try:
        payload = common.load_waveform_any(rel, full=full_flag, max_points=max_points)
        if not payload.get('times'):
            return jsonify({'error': 'no data parsed'}), 400
        return jsonify(payload)
    except FileNotFoundError:
        return jsonify({'error': 'file not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.get('/api/summaries')
def api_summaries():
    try:
        return jsonify(common.list_summaries())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.get('/api/summary')
def api_summary():
    src = (request.args.get('src') or 'original').strip().lower()
    if src not in ('original', 'smoothed', 'unsmoothed', 'raw'):
        src = 'original'
    if src == 'raw':
        src = 'unsmoothed'
    path = common.get_summary_csv(src)
    if not os.path.exists(path):
        return Response('Not found', status=404, mimetype='text/plain')
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            return Response(fh.read(), mimetype='text/csv')
    except Exception as e:
        return Response(str(e), status=500, mimetype='text/plain')


@bp.get('/api/combined_metadata')
def api_combined_metadata():
    j = common.load_optional_json('combined_metadata.json')
    if j is None:
        return jsonify({'error': 'combined_metadata.json not found'}), 404
    return jsonify(j)


@bp.get('/api/analysis_summary')
def api_analysis_summary():
    j = common.load_optional_json('Analysis_summary.json')
    if j is None:
        return jsonify({'error': 'Analysis_summary.json not found'}), 404
    return jsonify(j)


@bp.get('/api/event_to_event_dt_raw')
def api_event_to_event_dt_raw():
    p = common.find_latest_event_to_event_dt_raw()
    if not p or not os.path.exists(p):
        return Response('Not found', status=404, mimetype='text/plain')
    try:
        with open(p, 'r', encoding='utf-8', errors='ignore') as fh:
            return Response(fh.read(), mimetype='text/csv')
    except Exception as e:
        return Response(str(e), status=500, mimetype='text/plain')


@bp.get('/api/psd')
def api_psd():
    event_id = (request.args.get('event_id') or '').strip()
    if not event_id:
        return jsonify({'error': 'event_id required'}), 400
    rel = common.resolve_event_file(event_id)
    if not rel:
        return jsonify({'error': f'waveform file not found for event_id {event_id}'}), 404

    qtail = float(request.args.get('qtail_fraction') or 0.3)

    def _f(name: str) -> float | None:
        v = request.args.get(name)
        if v is None or v == '':
            return None
        try:
            return float(v)
        except Exception:
            return None

    event_start = _f('event_start')
    event_end = _f('event_end')
    peak_time = _f('event_peak_time')

    use_smoothed = str(request.args.get('use_smoothed', '')).lower() in ('1', 'true', 'yes', 'on')
    smoothing_method = (request.args.get('smoothing_method') or 'savgol').strip()
    smoothing_param = request.args.get('smoothing_param')

    wf = common.load_waveform_any(rel, full=True)
    times = wf.get('times') or []
    amps = wf.get('amps') or []

    if use_smoothed:
        times, amps = common.smooth_series(times, amps, method=smoothing_method, param=smoothing_param)

    res = common.compute_psd(times, amps, qtail_fraction=qtail, event_start=event_start, event_end=event_end, peak_time=peak_time)
    out = {'event_id': event_id}
    out.update(res)
    return jsonify(out)


@bp.get('/api/pileup')
def api_pileup():
    """Minimal pile-up detection for UI; returns reasonable fields but keeps logic simple."""
    event_id = (request.args.get('event_id') or '').strip()
    if not event_id:
        return jsonify({'error': 'event_id required'}), 400

    rel = common.resolve_event_file(event_id)
    if not rel:
        return jsonify({'error': f'waveform file not found for event_id {event_id}'}), 404

    try:
        alpha = float(request.args.get('fraction') or 0.5)
    except Exception:
        alpha = 0.5

    def _f(name: str, default: float = 0.0) -> float:
        try:
            return float(request.args.get(name) or default)
        except Exception:
            return default

    t_start = _f('event_start_ns', 0.0)
    t_end = _f('event_end_ns', 0.0)
    thr = _f('threshold_mV', 0.0)
    peak = _f('peak_amplitude_mV', thr)

    use_smoothed = str(request.args.get('use_smoothed', '')).lower() in ('1', 'true', 'yes', 'on')
    smoothing_method = (request.args.get('smoothing_method') or 'savgol').strip()
    smoothing_param = request.args.get('smoothing_param')

    wf = common.load_waveform_any(rel, full=True)
    times = wf.get('times') or []
    amps = wf.get('amps') or []
    if use_smoothed:
        times, amps = common.smooth_series(times, amps, method=smoothing_method, param=smoothing_param)

    quasi = thr + alpha * (peak - thr)

    # Filter window
    pairs = [(float(t), float(a)) for t, a in zip(times, amps) if (t_start <= float(t) <= t_end) or (t_start == 0.0 and t_end == 0.0)]
    if not pairs:
        return jsonify({'pileup': False, 'reason': 'no samples in window', 'quasi_threshold_mV': quasi})

    # Count contiguous segments below quasi threshold
    below = [a <= quasi for _, a in pairs]
    segs = 0
    in_seg = False
    for b in below:
        if b and not in_seg:
            segs += 1
            in_seg = True
        elif (not b) and in_seg:
            in_seg = False

    pileup = segs >= 2
    reason = 'multiple quasi-threshold crossings' if pileup else 'single segment'

    # Optional alpha scan (UI may request scan=1)
    scan = str(request.args.get('scan', '')).lower() in ('1', 'true', 'yes', 'on')
    hits = []
    alpha_hit = None
    if scan:
        scan_min = _f('scan_min', 0.1)
        scan_max = _f('scan_max', 0.9)
        scan_step = _f('scan_step', 0.05)
        a = scan_min
        while a <= scan_max + 1e-9:
            q = thr + a * (peak - thr)
            segs2 = 0
            in2 = False
            for _, amp in pairs:
                b2 = amp <= q
                if b2 and not in2:
                    segs2 += 1
                    in2 = True
                elif (not b2) and in2:
                    in2 = False
            if segs2 >= 2:
                hits.append({'alpha': a, 'segments': segs2})
                if alpha_hit is None:
                    alpha_hit = a
            a += scan_step

    return jsonify({
        'event_id': event_id,
        'pileup': pileup,
        'reason': reason,
        'quasi_threshold_mV': quasi,
        'alpha': alpha,
        'segments': segs,
        'hits': hits,
        'alpha_hit': alpha_hit,
    })

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import common

bp = Blueprint('markers', __name__)

# In-memory per-file overrides set by /api/update_markers
_OVERRIDES: dict[str, dict] = {}
_BASELINE_CACHE: dict[tuple[str, str], tuple[float, float]] = {}
ALGORITHM_VERSION = 5


def _channel_for_request(rel: str) -> str | None:
    channel = (request.args.get('channel') or '').strip() or None
    if channel:
        return channel.upper()
    return common.infer_channel_from_file(rel)


def _marker_payload(rel: str, markers: dict, *, source: str, channel: str | None,
                    exclude_margin: int, smoothed: bool, window: int | None = None,
                    poly: int | None = None) -> dict:
    base_mean = markers.get('base_mean_mV', 0.0)
    base_std = markers.get('base_std_mV', 0.0)
    peak_rel = markers.get('peak_excursion_mV')
    if peak_rel is None and markers.get('peak_amplitude_mV') is not None:
        try:
            peak_rel = abs(float(markers.get('peak_amplitude_mV')) - float(base_mean))
        except Exception:
            peak_rel = None
    payload = {
        'source': source,
        'file': rel,
        'markers': markers,
        'algorithm_version': ALGORITHM_VERSION,
        'implementation': 'compute_event_markers_ef_style' if source in ('efdynamic', 'batch') else 'compute_event_markers_threshold',
        'implementation_module': 'sciplotter_backend.common',
        'stats': {
            'base_mean': base_mean,
            'base_std': base_std,
            'width_level': markers.get('width_level_mV'),
            'std_multiplier': common.EF_STD_MULTIPLIER,
            'peak_rel': peak_rel,
            'exclude_margin': exclude_margin,
            'dwell_samples': common.EF_DWELL_SAMPLES,
            'fixed_level_mV': markers.get('fixed_level_mV'),
            'fixed_level_source': markers.get('fixed_level_source'),
        },
        'debug': {
            'start_idx': markers.get('threshold_crossing_sample_idx'),
            'end_idx': markers.get('event_end_sample_idx'),
            'left_last_inside_idx': markers.get('left_last_inside_idx'),
            'right_last_inside_idx': markers.get('right_last_inside_idx'),
            'channel_negative': markers.get('channel_negative'),
        },
        'params': {
            'exclude_margin': exclude_margin,
            'std_multiplier': common.EF_STD_MULTIPLIER,
            'dwell_samples': common.EF_DWELL_SAMPLES,
            'smoothed': smoothed,
            'boundary_mode': 'fixed',
            'fixed_level_mV': markers.get('fixed_level_mV'),
            'fixed_level_source': markers.get('fixed_level_source'),
            'channel': channel,
        }
    }
    if window is not None:
        payload['stats']['savgol_window'] = window
        payload['params']['savgol_window'] = window
    if poly is not None:
        payload['stats']['savgol_poly'] = poly
        payload['params']['savgol_poly'] = poly
    return payload


def _read_waveform_for_event(event_id: str, use_smoothed: bool, smoothing_method: str, smoothing_param: str | None):
    rel = common.resolve_event_file(event_id)
    if not rel:
        raise FileNotFoundError(f'waveform file not found for event_id {event_id}')

    wf = common.load_waveform_any(rel, full=True)
    times = wf.get('times') or []
    amps = wf.get('amps') or []

    if use_smoothed:
        times, amps = common.smooth_series(times, amps, method=smoothing_method, param=smoothing_param)

    return rel, times, amps


@bp.get('/api/markers/efdynamic')
def api_markers_efdynamic():
    rel = (request.args.get('file') or '').strip()
    if not rel:
        return jsonify({'error': 'file parameter required'}), 400

    channel = _channel_for_request(rel)
    exclude_margin = int(request.args.get('exclude_margin', common.EF_EXCLUDE_MARGIN) or common.EF_EXCLUDE_MARGIN)

    fixed_level_override = request.args.get('fixed_level_mV')
    thr = None
    if fixed_level_override not in (None, ''):
        try:
            thr = float(fixed_level_override)
        except Exception:
            thr = None

    try:
        wf = common.load_waveform_any(rel, full=True)
        times = wf.get('times') or []
        amps = wf.get('amps') or []
        cache_key = (rel, channel or '')
        cached = _BASELINE_CACHE.get(cache_key)
        markers = common.compute_event_markers_ef_style(
            times,
            amps,
            channel=channel,
            exclude_margin=exclude_margin,
            boundary_mode='fixed',
            fixed_level_mV=thr,
            preset_base_mean=cached[0] if cached else None,
            preset_base_std=cached[1] if cached else None,
        )
        bm = markers.get('base_mean_mV')
        bs = markers.get('base_std_mV')
        if bm is not None and bs is not None:
            _BASELINE_CACHE[cache_key] = (float(bm), float(bs))
        return jsonify(_marker_payload(rel, markers, source='efdynamic', channel=channel, exclude_margin=exclude_margin, smoothed=False))
    except FileNotFoundError:
        return jsonify({'error': 'file not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.get('/api/markers/batch')
def api_markers_batch():
    rel = (request.args.get('file') or '').strip()
    if not rel:
        return jsonify({'error': 'file parameter required'}), 400

    channel = _channel_for_request(rel)
    exclude_margin = int(request.args.get('exclude_margin', common.EF_EXCLUDE_MARGIN) or common.EF_EXCLUDE_MARGIN)

    window = request.args.get('window')
    poly = request.args.get('poly')
    param = None
    try:
        if window or poly:
            w = int(window) if window else 101
            p = int(poly) if poly else 3
            param = f'window={w},poly={p}'
    except Exception:
        param = None

    fixed_level_override = request.args.get('fixed_level_mV')
    thr = None
    if fixed_level_override not in (None, ''):
        try:
            thr = float(fixed_level_override)
        except Exception:
            thr = None

    try:
        wf = common.load_waveform_any(rel, full=True)
        times = wf.get('times') or []
        amps = wf.get('amps') or []
        st, sa = common.smooth_series(times, amps, method='savgol', param=param)
        cache_key = (rel + '|smooth', channel or '')
        cached = _BASELINE_CACHE.get(cache_key)
        markers = common.compute_event_markers_ef_style(
            st,
            sa,
            channel=channel,
            exclude_margin=exclude_margin,
            boundary_mode='fixed',
            fixed_level_mV=thr,
            preset_base_mean=cached[0] if cached else None,
            preset_base_std=cached[1] if cached else None,
        )
        bm = markers.get('base_mean_mV')
        bs = markers.get('base_std_mV')
        if bm is not None and bs is not None:
            _BASELINE_CACHE[cache_key] = (float(bm), float(bs))
        return jsonify(_marker_payload(rel, markers, source='batch', channel=channel, exclude_margin=exclude_margin, smoothed=True, window=(int(window) if window else 101), poly=(int(poly) if poly else 3)))
    except FileNotFoundError:
        return jsonify({'error': 'file not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.get('/api/thresholds')
def api_thresholds():
    out = common.get_threshold_overview()
    out['algorithm_version'] = ALGORITHM_VERSION
    return jsonify(out)


@bp.post('/api/smooth')
def api_smooth():
    data = request.get_json(silent=True) or {}
    rel = (data.get('file') or '').strip()
    if not rel:
        return jsonify({'error': 'file required'}), 400
    method = (data.get('method') or 'savgol').strip()
    param = data.get('param')

    try:
        wf = common.load_waveform_any(rel, full=True)
        times = wf.get('times') or []
        amps = wf.get('amps') or []
        st, sa = common.smooth_series(times, amps, method=method, param=param)
        return jsonify({
            'file': rel,
            'method': method,
            'param': param,
            'times': st,
            'amps': sa,
            'metadata': {'raw': bool(wf.get('raw', False))},
        })
    except FileNotFoundError:
        return jsonify({'error': 'file not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.post('/api/batch_smooth')
def api_batch_smooth():
    """Smoothing + marker recompute (batch-equivalent enough for the UI).

    Returns:
      smoothed_times, smoothed_amps, markers
    """
    data = request.get_json(silent=True) or {}
    rel = (data.get('file') or '').strip()
    if not rel:
        return jsonify({'error': 'file required'}), 400

    method = (data.get('method') or 'savgol').strip()
    param = data.get('param')
    channel = (data.get('channel') or common.infer_channel_from_file(rel) or '').upper() or None
    exclude_margin = int(data.get('exclude_margin', common.EF_EXCLUDE_MARGIN) or common.EF_EXCLUDE_MARGIN)

    try:
        wf = common.load_waveform_any(rel, full=True)
        times = wf.get('times') or []
        amps = wf.get('amps') or []
        st, sa = common.smooth_series(times, amps, method=method, param=param)

        thr = None
        # Prefer event context threshold if present
        ctx = data.get('event_context') or {}
        try:
            if ctx.get('detection_threshold_mV') is not None:
                thr = float(ctx.get('detection_threshold_mV'))
        except Exception:
            thr = None

        markers = common.compute_event_markers_ef_style(
            st,
            sa,
            channel=channel,
            exclude_margin=exclude_margin,
            boundary_mode='fixed',
            fixed_level_mV=thr,
        )

        payload = {
            'file': rel,
            'method': method,
            'param': param,
            'smoothed_times': st,
            'smoothed_amps': sa,
            'markers': markers,
            'algorithm_version': ALGORITHM_VERSION,
            'implementation': 'compute_event_markers_ef_style',
            'implementation_module': 'sciplotter_backend.common',
            'params': {
                'std_multiplier': common.EF_STD_MULTIPLIER,
                'dwell_samples': common.EF_DWELL_SAMPLES,
            },
            'metadata': {'algorithm_version': ALGORITHM_VERSION},
        }
        return jsonify(payload)
    except FileNotFoundError:
        return jsonify({'error': 'file not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.get('/api/update_markers')
def api_update_markers():
    event_id = (request.args.get('event_id') or '').strip()
    if not event_id:
        return jsonify({'error': 'event_id required'}), 400

    use_smoothed = str(request.args.get('use_smoothed', '')).lower() in ('1', 'true', 'yes', 'on')
    smoothing_method = (request.args.get('smoothing_method') or 'savgol').strip()
    smoothing_param = request.args.get('smoothing_param')

    thr_val = request.args.get('threshold_mV')
    thr = None
    if thr_val not in (None, ''):
        try:
            thr = float(thr_val)
        except Exception:
            thr = None

    try:
        rel, times, amps = _read_waveform_for_event(event_id, use_smoothed, smoothing_method, smoothing_param)
        channel = common.infer_channel_from_file(rel)
        if thr is None:
            auto = common.detect_markers(times, amps)
            thr = auto.get('threshold_mV')
        out = common.compute_event_markers_threshold(times, amps, threshold_mV=float(thr), channel=channel)
        _OVERRIDES[rel] = out
        return jsonify(out)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400

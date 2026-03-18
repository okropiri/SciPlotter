from __future__ import annotations

import csv
import glob
import math
import os
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from . import runtime

# BASE_DIR points at the "waveforms" directory by default.
DEFAULT_BASE_DIR = runtime.default_waveform_base_dir()
BASE_DIR = os.path.abspath(DEFAULT_BASE_DIR)

# Optional manual override for summary CSV path
SUMMARY_OVERRIDE: Optional[str] = None

ALLOWED_EXT = ('.csv', '.txt')
RAW_ALLOWED_EXT = ('.txt',)
NEGATIVE_PULSE_CHANNELS = {'C2', 'C3', 'C4'}
EF_STD_MULTIPLIER = 1.0
EF_DWELL_SAMPLES = 5
EF_EXCLUDE_MARGIN = 50
EF_MIN_LEVEL_MV = 0.3
BASELINE_TOL_FRACTION = 0.4
BASELINE_TOL_MIN_STD_MULT = 2.0
BASELINE_TOL_PEAK_CLAMP = 0.9


def set_waveform_base_dir(path: str) -> None:
    global BASE_DIR
    BASE_DIR = os.path.abspath(path)
    os.environ['WAVEFORM_BASE_DIR'] = BASE_DIR


def set_summary_override(path: Optional[str]) -> None:
    global SUMMARY_OVERRIDE
    if not path or str(path).strip() == '':
        SUMMARY_OVERRIDE = None
        return
    SUMMARY_OVERRIDE = os.path.abspath(str(path))


def get_summary_override() -> Optional[str]:
    return SUMMARY_OVERRIDE


def _summary_dir() -> str:
    # Summary CSV is usually next to waveforms/
    return os.path.dirname(os.path.abspath(BASE_DIR))


def _batched_mode() -> Optional[str]:
    try:
        parts = os.path.abspath(BASE_DIR).split(os.sep)
        if 'batched_analysis_exports' in parts:
            i = parts.index('batched_analysis_exports')
            if i + 1 < len(parts):
                sub = parts[i + 1].lower()
                if sub in ('smoothed', 'raw', 'unsmoothed'):
                    return 'unsmoothed' if sub == 'raw' else sub
    except Exception:
        pass
    return None


def _file_mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except Exception:
        return None


def get_summary_csv(label: str = 'original') -> str:
    """Resolve summary CSV path for label: original|smoothed|unsmoothed.
    Honors SUMMARY_OVERRIDE if it exists (regardless of label).
    """
    d = _summary_dir()

    if SUMMARY_OVERRIDE and os.path.isfile(SUMMARY_OVERRIDE):
        return os.path.abspath(SUMMARY_OVERRIDE)

    candidates: List[str] = []
    mode = _batched_mode()

    if label == 'smoothed' or mode == 'smoothed':
        candidates += sorted(glob.glob(os.path.join(d, 'batch_analyzed_smoothed_*.csv')), key=lambda p: -(os.path.getmtime(p) if os.path.exists(p) else 0))
        candidates.append(os.path.join(d, 'smoothed_simple_cycle_events_summary.csv'))

    if label == 'unsmoothed' or label == 'raw' or mode == 'unsmoothed':
        candidates += sorted(glob.glob(os.path.join(d, 'batch_analyzed_unsmoothed_*.csv')), key=lambda p: -(os.path.getmtime(p) if os.path.exists(p) else 0))
        candidates.append(os.path.join(d, 'unsmoothed_simple_cycle_events_summary.csv'))

    if label == 'original':
        candidates.append(os.path.join(d, 'EventsSummary_trace_[ALL].csv'))
        candidates.append(os.path.join(d, 'simple_cycle_events_summary.csv'))

    # Fallbacks for any label
    candidates.append(os.path.join(d, 'EventsSummary_trace_[ALL].csv'))
    candidates.append(os.path.join(d, 'simple_cycle_events_summary.csv'))

    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)

    # Last resort: newest EventsSummary_trace_*.csv
    tries = glob.glob(os.path.join(d, 'EventsSummary_trace_*.csv'))
    if tries:
        tries_sorted = sorted(tries, key=lambda p: (('[ALL]' not in os.path.basename(p)), -(os.path.getmtime(p) if os.path.exists(p) else 0)))
        return os.path.abspath(tries_sorted[0])

    return os.path.abspath(os.path.join(d, 'simple_cycle_events_summary.csv'))


def list_summaries() -> Dict[str, Any]:
    out = []
    for lbl in ('original', 'smoothed', 'unsmoothed'):
        p = get_summary_csv(lbl)
        out.append({'label': lbl, 'path': p, 'exists': os.path.exists(p), 'mtime': _file_mtime(p)})
    eff = get_summary_csv('original')
    return {'summary_dir': _summary_dir(), 'summary_override': SUMMARY_OVERRIDE, 'effective': eff, 'sources': out}


def safe_path(relpath: str) -> str:
    candidate = os.path.abspath(os.path.join(BASE_DIR, relpath))
    if not candidate.startswith(BASE_DIR):
        raise ValueError('Invalid path')
    return candidate


def list_waveform_files() -> List[str]:
    out: List[str] = []
    for root, _, files in os.walk(BASE_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith(ALLOWED_EXT):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, BASE_DIR)
            out.append(rel)
    return out


def _guess_raw_waveform_dir() -> Optional[str]:
    # Explicit env override first
    env_p = os.environ.get('RAW_WAVEFORM_DIR')
    if env_p and os.path.isdir(env_p):
        return os.path.abspath(env_p)

    # Heuristic: sibling Raw_data/.../Waveforms relative to BASE_DIR
    try:
        cand = os.path.abspath(os.path.join(BASE_DIR, '..', '..', 'Raw_data', 'Lectroy_Oscilloscope', 'Waveforms'))
        if os.path.isdir(cand):
            return cand
    except Exception:
        pass

    return None


def list_raw_waveform_files(limit: Optional[int] = None) -> List[str]:
    raw_dir = _guess_raw_waveform_dir()
    if not raw_dir:
        return []
    out: List[str] = []
    try:
        for fn in sorted(os.listdir(raw_dir)):
            if not fn.lower().endswith(RAW_ALLOWED_EXT):
                continue
            out.append(fn)
            if limit and len(out) >= limit:
                break
    except Exception:
        return []
    return out


def _safe_raw_file(basename: str) -> str:
    raw_dir = _guess_raw_waveform_dir()
    if not raw_dir:
        raise ValueError('Raw waveform directory unavailable')
    if os.path.sep in basename or (os.path.altsep and os.path.altsep in basename):
        raise ValueError('Nested paths not allowed for raw files')
    candidate = os.path.abspath(os.path.join(raw_dir, basename))
    if not candidate.startswith(raw_dir):
        raise ValueError('Invalid raw path')
    return candidate


def read_waveform_csv(path: str) -> Tuple[List[float], List[float]]:
    times: List[float] = []
    amps: List[float] = []
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            rows = list(csv.reader(fh))
            if not rows:
                return [], []

            def _is_number(s: str) -> bool:
                try:
                    float(s)
                    return True
                except Exception:
                    return False

            start = 0
            if len(rows[0]) >= 2 and (not _is_number(rows[0][0]) or not _is_number(rows[0][1])):
                start = 1

            for row in rows[start:]:
                if len(row) < 2:
                    continue
                try:
                    t = float(row[0])
                    a = float(row[1])
                except Exception:
                    continue
                times.append(t)
                amps.append(a)
    except Exception:
        return [], []

    return times, amps


def read_raw_lecroy_txt(path: str, target_points: Optional[int] = None):
    """Parse LeCroy exported waveform .txt.

    Returns (times_ns, amps_mV, full_length, decimated, decimation_mode)
    """
    segment_size = None
    header_seen = False
    times: List[float] = []
    amps: List[float] = []

    bucket_min_amp = None
    bucket_min_t = None
    bucket_max_amp = None
    bucket_max_t = None
    bucket_index = 0
    bucket_span = None
    sample_index = 0
    decimation_mode = 'none'

    def _flush_bucket():
        nonlocal bucket_min_amp, bucket_min_t, bucket_max_amp, bucket_max_t
        if bucket_min_t is None and bucket_max_t is None:
            return
        pairs = []
        if bucket_min_t is not None:
            pairs.append((bucket_min_t, bucket_min_amp))
        if bucket_max_t is not None and bucket_max_t != bucket_min_t:
            pairs.append((bucket_max_t, bucket_max_amp))
        pairs.sort(key=lambda x: x[0])
        for t, a in pairs:
            times.append(float(t))
            amps.append(float(a))
        bucket_min_amp = bucket_min_t = bucket_max_amp = bucket_max_t = None

    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if not header_seen:
                    if 'SegmentSize' in line:
                        parts = [p.strip() for p in line.split(',')]
                        try:
                            idx = parts.index('SegmentSize')
                            if idx + 1 < len(parts):
                                segment_size = int(parts[idx + 1])
                        except Exception:
                            pass
                    if line.startswith('Time'):
                        header_seen = True
                        if target_points and segment_size and segment_size > target_points:
                            bucket_span = max(1, int(math.floor(segment_size / target_points)))
                            decimation_mode = 'minmax'
                    continue

                parts = [p.strip() for p in line.replace('\t', ',').split(',') if p.strip()]
                if len(parts) < 2:
                    continue
                try:
                    t_s = float(parts[0])
                    a_s = float(parts[1])
                except Exception:
                    continue

                t_ns = t_s * 1e9
                a_mV = a_s * 1e3

                if decimation_mode == 'none':
                    times.append(t_ns)
                    amps.append(a_mV)
                else:
                    if bucket_min_amp is None or a_mV < bucket_min_amp:
                        bucket_min_amp = a_mV
                        bucket_min_t = t_ns
                    if bucket_max_amp is None or a_mV > bucket_max_amp:
                        bucket_max_amp = a_mV
                        bucket_max_t = t_ns
                    if bucket_span and (sample_index // bucket_span) > bucket_index:
                        _flush_bucket()
                        bucket_index = sample_index // bucket_span

                sample_index += 1

        if decimation_mode != 'none':
            _flush_bucket()

    except Exception:
        return [], [], 0, False, 'error'

    full_len = segment_size if segment_size is not None else sample_index
    decimated = decimation_mode != 'none' and len(times) < full_len
    return times, amps, full_len, decimated, decimation_mode


def _parse_smoothing_param(param: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not param:
        return out
    try:
        for part in str(param).split(','):
            if '=' not in part:
                continue
            k, v = part.split('=', 1)
            k = k.strip().lower()
            v = v.strip()
            try:
                out[k] = float(v) if '.' in v else int(v)
            except Exception:
                out[k] = v
    except Exception:
        return {}
    return out


def smooth_series(times: List[float], amps: List[float], method: str = 'savgol', param: Optional[str] = None):
    if not amps:
        return times, amps

    method = (method or 'savgol').lower()
    pmap = _parse_smoothing_param(param)

    if method in ('savgol', 'savitzky_golay', 'savitzky-golay'):
        # Lightweight Savitzky–Golay via convolution kernel.
        import numpy as np  # type: ignore

        window = int(pmap.get('window', 101))
        poly = int(pmap.get('poly', 3))
        window = max(5, window | 1)
        poly = max(0, min(poly, window - 2))
        half = window // 2

        x = np.arange(-half, half + 1, dtype=float)
        X = np.vander(x, N=poly + 1, increasing=True)
        XT = X.T
        try:
            P = np.linalg.inv(XT @ X) @ XT
        except Exception:
            P = np.linalg.pinv(XT @ X) @ XT
        ker = np.asarray(P[0, :], dtype=float)

        arr = np.asarray(amps, dtype=float)
        out = []
        n = len(arr)
        for i in range(n):
            lo = i - half
            acc = 0.0
            for k in range(window):
                j = lo + k
                if j < 0:
                    j = 0
                elif j >= n:
                    j = n - 1
                acc += arr[j] * ker[k]
            out.append(float(acc))
        return times, out

    # Default: moving average
    win = int(pmap.get('window', 51))
    win = max(3, win)
    out: List[float] = []
    s = 0.0
    for i, a in enumerate(amps):
        s += float(a)
        if i >= win:
            s -= float(amps[i - win])
        out.append(s / (win if i >= win - 1 else (i + 1)))
    return times, out


def compute_psd(times: List[float], amps: List[float], qtail_fraction: float = 0.3,
                event_start: Optional[float] = None, event_end: Optional[float] = None,
                peak_time: Optional[float] = None) -> Dict[str, Any]:
    if not times:
        return {
            'psd_value': None,
            'total_area_mV_ns': 0.0,
            'tail_area_mV_ns': 0.0,
            'qtail_start_time_ns': None,
            'samples_count': 0,
            'qtail_fraction': qtail_fraction,
        }

    f = max(0.0, min(1.0, float(qtail_fraction)))

    t0, t1 = float(times[0]), float(times[-1])
    t_start = float(event_start) if event_start is not None else t0
    t_end = float(event_end) if event_end is not None else t1
    if t_end < t_start:
        t_start, t_end = t0, t1

    if peak_time is None:
        # Assume negative pulse: peak is minimum
        p_idx = min(range(len(amps)), key=lambda i: float(amps[i]))
        peak_time = float(times[p_idx])

    qstart = float(peak_time) + f * max(0.0, float(t_end) - float(peak_time))
    qstart = max(t_start, min(t_end, qstart))

    dts = [abs(float(times[i]) - float(times[i - 1])) for i in range(1, len(times))]
    dt = median(dts) if dts else 1.0

    total = 0.0
    tail = 0.0

    for ti, ai in zip(times, amps):
        t = float(ti)
        if t < t_start or t > t_end:
            continue
        # Baseline reference is 0 mV: integrate negative area
        contrib = max(0.0, -float(ai))
        total += contrib
        if t >= qstart:
            tail += contrib

    total *= dt
    tail *= dt

    psd = (tail / total) if total > 0 else None
    return {
        'psd_value': psd,
        'total_area_mV_ns': total,
        'tail_area_mV_ns': tail,
        'qtail_start_time_ns': qstart,
        'samples_count': len(times),
        'qtail_fraction': f,
    }


def _interp_cross(t0: float, y0: float, t1: float, y1: float, level: float) -> float:
    if t1 == t0 or y1 == y0:
        return float(t0)
    frac = (level - y0) / (y1 - y0)
    frac = max(0.0, min(1.0, frac))
    return float(t0 + frac * (t1 - t0))


def _infer_negative_pulse(amps: List[float], channel: Optional[str] = None) -> bool:
    ch = (channel or '').upper()
    if ch in NEGATIVE_PULSE_CHANNELS:
        return True
    if not amps:
        return True
    try:
        mn = abs(float(min(amps)))
        mx = abs(float(max(amps)))
        return mn > 1.2 * mx
    except Exception:
        return True


def infer_channel_from_file(file_param: Optional[str]) -> Optional[str]:
    base = os.path.basename(file_param or '')
    root, _ = os.path.splitext(base)
    parts = root.split('_')
    for part in parts:
        up = part.upper()
        if len(up) == 2 and up.startswith('C') and up[1].isdigit():
            return up
    return None


def compute_event_markers_threshold(times: List[float], amps: List[float], threshold_mV: float,
                                    channel: Optional[str] = None) -> Dict[str, Any]:
    n = len(times)
    if n == 0:
        return {
            'event_start_time_ns': None,
            'event_end_time_ns': None,
            'peak_time_ns': None,
            'peak_amplitude_mV': None,
            'threshold_mV': threshold_mV,
            'threshold_crossing_sample_idx': None,
            'peak_sample_idx': None,
            'event_end_sample_idx': None,
        }

    neg = _infer_negative_pulse(amps, channel)
    try:
        p_idx = int(min(range(n), key=lambda i: float(amps[i]))) if neg else int(max(range(n), key=lambda i: float(amps[i])))
    except Exception:
        p_idx = 0

    thr_mag = abs(float(threshold_mV))
    signed_thr = -thr_mag if neg else thr_mag

    start_time = None
    start_idx = 0
    if neg:
        for i in range(p_idx - 1, -1, -1):
            y0, y1 = float(amps[i]), float(amps[i + 1])
            if y0 >= signed_thr and y1 < signed_thr:
                start_time = _interp_cross(float(times[i]), y0, float(times[i + 1]), y1, signed_thr)
                start_idx = i + 1
                break
    else:
        for i in range(p_idx - 1, -1, -1):
            y0, y1 = float(amps[i]), float(amps[i + 1])
            if y0 <= signed_thr and y1 > signed_thr:
                start_time = _interp_cross(float(times[i]), y0, float(times[i + 1]), y1, signed_thr)
                start_idx = i + 1
                break
    if start_time is None:
        start_time = float(times[0])
        start_idx = 0

    end_time = None
    end_idx = n - 1
    if neg:
        for i in range(p_idx, n - 1):
            y0, y1 = float(amps[i]), float(amps[i + 1])
            if y0 < signed_thr and y1 >= signed_thr:
                end_time = _interp_cross(float(times[i]), y0, float(times[i + 1]), y1, signed_thr)
                end_idx = i + 1
                break
    else:
        for i in range(p_idx, n - 1):
            y0, y1 = float(amps[i]), float(amps[i + 1])
            if y0 > signed_thr and y1 <= signed_thr:
                end_time = _interp_cross(float(times[i]), y0, float(times[i + 1]), y1, signed_thr)
                end_idx = i + 1
                break
    if end_time is None:
        end_time = float(times[-1])
        end_idx = n - 1

    return {
        'event_start_time_ns': float(start_time),
        'event_end_time_ns': float(end_time),
        'peak_time_ns': float(times[p_idx]),
        'peak_amplitude_mV': float(amps[p_idx]),
        'threshold_mV': float(signed_thr),
        'threshold_crossing_sample_idx': int(start_idx),
        'peak_sample_idx': int(p_idx),
        'event_end_sample_idx': int(end_idx),
        'channel_negative': bool(neg),
    }


def compute_event_markers_ef_style(times: List[float], amps: List[float],
                                   channel: Optional[str] = None,
                                   exclude_margin: int = EF_EXCLUDE_MARGIN,
                                   boundary_mode: str = 'fixed',
                                   fixed_level_mV: Optional[float] = None,
                                   preset_base_mean: Optional[float] = None,
                                   preset_base_std: Optional[float] = None) -> Dict[str, Any]:
    n = len(times)
    if n == 0:
        return {
            'event_start_time_ns': None,
            'event_end_time_ns': None,
            'peak_time_ns': None,
            'peak_amplitude_mV': None,
            'threshold_crossing_sample_idx': None,
            'peak_sample_idx': None,
            'event_end_sample_idx': None,
            'delta_t_ns': None,
            'peak_delta_t_ns': None,
            'phase': None,
        }

    neg = _infer_negative_pulse(amps, channel)
    try:
        p_idx = int(min(range(n), key=lambda i: float(amps[i]))) if neg else int(max(range(n), key=lambda i: float(amps[i])))
    except Exception:
        p_idx = 0

    if preset_base_mean is not None and preset_base_std is not None:
        base_mean = float(preset_base_mean)
        base_std = float(preset_base_std)
    else:
        s = max(0, int(p_idx) - int(exclude_margin))
        e = min(n - 1, int(p_idx) + int(exclude_margin))
        sample: List[float] = []
        if s > 0:
            sample.extend(float(v) for v in amps[:s])
        if e < n - 1:
            sample.extend(float(v) for v in amps[e + 1:])
        if len(sample) < 4:
            m = max(10, min(200, n // 10 if n > 0 else 10))
            sample = [float(v) for v in amps[:m]]
        if sample:
            base_mean = sum(sample) / len(sample)
            base_var = sum((x - base_mean) ** 2 for x in sample) / len(sample)
            base_std = math.sqrt(base_var) if base_var > 0 else 0.0
        else:
            base_mean = 0.0
            base_std = 0.0

    peak_rel = abs(float(amps[p_idx]) - float(base_mean))
    mode_norm = str(boundary_mode or '').lower()
    if mode_norm in ('fixed', 'fixed_level', 'fixed-level'):
        det_level = abs(float(fixed_level_mV)) if fixed_level_mV is not None else None
        level_source = 'explicit' if det_level is not None else 'auto'
        if det_level is None:
            det_level = max(EF_MIN_LEVEL_MV, peak_rel)
        baseline_tol = max(
            EF_MIN_LEVEL_MV,
            min(det_level * BASELINE_TOL_FRACTION, det_level),
            BASELINE_TOL_MIN_STD_MULT * (base_std if base_std > 0 else 0.0),
        )
        if peak_rel > 0:
            clamp_val = peak_rel * BASELINE_TOL_PEAK_CLAMP
            baseline_tol = clamp_val if baseline_tol >= peak_rel else min(baseline_tol, clamp_val)
        if peak_rel <= 0 or baseline_tol >= peak_rel:
            width_level = max(0.0, float(EF_STD_MULTIPLIER) * float(base_std))
            mode_norm = 'baseline_std_fallback'
            level_source = f'fallback_dynamic_std (peak_rel={peak_rel:.3g})'
        else:
            width_level = float(baseline_tol)
    else:
        width_level = max(0.0, float(EF_STD_MULTIPLIER) * float(base_std))
        width_level = min(width_level, peak_rel) if peak_rel > 0 else width_level
        level_source = 'baseline_std'

    dwell = max(1, int(EF_DWELL_SAMPLES))

    def _expand_side(start_idx: int, step: int) -> Tuple[int, int]:
        idx = start_idx
        consec = 0
        first_run_start = None
        last_inside = start_idx
        while 0 < idx < (n - 1) and consec < dwell:
            diff = abs(float(amps[idx]) - base_mean)
            inside = diff <= width_level
            if inside:
                if consec == 0:
                    first_run_start = idx
                consec += 1
                last_inside = idx
            else:
                consec = 0
                first_run_start = None
            idx += step
        if consec >= dwell and first_run_start is not None:
            return first_run_start, last_inside
        fallback_idx = idx - step
        fallback_idx = max(0, min(n - 1, fallback_idx))
        return fallback_idx, fallback_idx

    li, left_last_inside = _expand_side(int(p_idx), -1)
    ri, right_last_inside = _expand_side(int(p_idx), +1)

    try:
        if ri > li:
            best = li
            best_mag = -1.0
            for k in range(int(li), int(ri) + 1):
                mag = abs(float(amps[k]) - base_mean)
                if mag > best_mag:
                    best_mag = mag
                    best = k
            p_idx = int(best)
    except Exception:
        pass

    result = {
        'event_start_time_ns': float(times[li]),
        'event_end_time_ns': float(times[ri]),
        'peak_time_ns': float(times[p_idx]),
        'peak_amplitude_mV': float(amps[p_idx]),
        'threshold_crossing_sample_idx': int(li),
        'peak_sample_idx': int(p_idx),
        'event_end_sample_idx': int(ri),
        'boundary_mode': ('first_cross_after_dwell_fixed' if mode_norm.startswith('fixed') else 'first_cross_after_dwell_dynamic'),
        'left_last_inside_idx': int(left_last_inside),
        'right_last_inside_idx': int(right_last_inside),
        'width_level_mV': float(width_level),
        'base_mean_mV': float(base_mean),
        'base_std_mV': float(base_std),
        'peak_excursion_mV': float(peak_rel),
        'delta_t_ns': None,
        'peak_delta_t_ns': None,
        'phase': None,
        'fixed_level_mV': (float(abs(fixed_level_mV)) if fixed_level_mV is not None else None),
        'fixed_level_source': level_source,
        'baseline_tolerance_mV': float(width_level),
        'collapsed_window': bool(li == ri),
        'right_extended': False,
        'channel_negative': bool(neg),
    }
    return result


def detect_markers(times: List[float], amps: List[float], threshold_mV: Optional[float] = None) -> Dict[str, Any]:
    """Compatibility wrapper for richer EF-style markers."""
    channel = None
    neg = _infer_negative_pulse(amps, channel)
    base_n = min(100, n := len(times))
    if n == 0:
        return compute_event_markers_ef_style(times, amps, channel=channel)
    base = [float(a) for a in amps[:base_n]]
    base_mean = sum(base) / len(base)
    base_var = sum((x - base_mean) ** 2 for x in base) / max(1, len(base) - 1)
    base_std = math.sqrt(base_var)
    if threshold_mV is None:
        thr_mag = abs(4.0 * base_std)
        threshold_mV = (base_mean - thr_mag) if neg else (base_mean + thr_mag)
    out = compute_event_markers_threshold(times, amps, threshold_mV=threshold_mV, channel=channel)
    out['base_mean_mV'] = float(base_mean)
    out['base_std_mV'] = float(base_std)
    return out


def infer_channel_thresholds_from_summary() -> Dict[str, float]:
    path = get_summary_csv('original')
    if not os.path.exists(path):
        return {}
    acc: Dict[str, List[float]] = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ch = (row.get('channel') or '').upper()
                if not ch:
                    ch = infer_channel_from_file(row.get('file') or row.get('waveform_file') or row.get('event_id')) or ''
                if not ch:
                    continue
                raw = row.get('detection_threshold_mV') or row.get('threshold_mV')
                if raw in (None, ''):
                    continue
                try:
                    acc.setdefault(ch, []).append(float(raw))
                except Exception:
                    continue
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for ch, vals in acc.items():
        if vals:
            try:
                out[ch] = abs(float(median(vals)))
            except Exception:
                out[ch] = abs(float(vals[0]))
    return out


def get_threshold_overview() -> Dict[str, Any]:
    thresholds = infer_channel_thresholds_from_summary()
    return {
        'thresholds_mV': thresholds,
        'batch_thresholds_mV': dict(thresholds),
        'detection_thresholds_mV': dict(thresholds),
        'settings_file': None,
        'settings_mtime': None,
        'event_finder_settings_file': None,
        'event_finder_settings_mtime': None,
    }


def find_latest_event_to_event_dt_raw() -> Optional[str]:
    d = _summary_dir()
    patterns = [
        os.path.join(d, 'event_to_event_dt_neighborsN*_*.csv'),
        os.path.join(d, 'event_to_event_dt_neighborsN*.csv'),
    ]
    candidates: List[str] = []
    for pat in patterns:
        try:
            candidates.extend(glob.glob(pat))
        except Exception:
            continue
    if not candidates:
        return None
    try:
        return max(candidates, key=os.path.getmtime)
    except Exception:
        return None


def load_optional_json(name: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(_summary_dir(), name)
    if not os.path.exists(path):
        return None
    try:
        import json
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            return json.load(fh)
    except Exception:
        return None


def resolve_event_file(event_id: str) -> Optional[str]:
    """Resolve an event_id to a waveform file relative path under BASE_DIR."""
    if not event_id:
        return None

    # If event_id already contains an extension, try directly.
    if event_id.lower().endswith(('.csv', '.txt')):
        rel = event_id
        try:
            full = safe_path(rel)
            if os.path.exists(full):
                return rel
        except Exception:
            return None

    # Prefer event_id.csv
    rel = event_id + '.csv'
    try:
        full = safe_path(rel)
        if os.path.exists(full):
            return rel
    except Exception:
        pass

    # Fallback: find any file whose basename matches event_id
    try:
        for relpath in list_waveform_files():
            base = os.path.basename(relpath)
            if os.path.splitext(base)[0] == event_id:
                return relpath
    except Exception:
        pass

    return None


def load_waveform_any(file_param: str, full: bool = False, max_points: Optional[int] = None) -> Dict[str, Any]:
    """Load either per-event CSV waveform or raw LeCroy TXT."""
    if not file_param:
        raise ValueError('file parameter required')

    lower = file_param.lower()

    # If file includes separators, treat as relative under BASE_DIR.
    if os.path.sep in file_param or (os.path.altsep and os.path.altsep in file_param):
        full_path = safe_path(file_param)
        if not os.path.exists(full_path):
            raise FileNotFoundError('file not found')
        if lower.endswith('.csv'):
            t, a = read_waveform_csv(full_path)
            return {'file': file_param, 'times': t, 'amps': a, 'raw': False}
        # .txt under BASE_DIR
        t, a, full_len, decimated, mode = read_raw_lecroy_txt(full_path, None if full else max_points)
        return {'file': file_param, 'times': t, 'amps': a, 'raw': True, 'full_length': full_len, 'decimated': decimated, 'decimation_mode': mode}

    # Basename-only: .txt likely refers to RAW_WAVEFORM_DIR
    if lower.endswith('.txt'):
        # try BASE_DIR first
        try:
            base_try = safe_path(file_param)
            if os.path.exists(base_try):
                t, a, full_len, decimated, mode = read_raw_lecroy_txt(base_try, None if full else max_points)
                return {'file': file_param, 'times': t, 'amps': a, 'raw': True, 'full_length': full_len, 'decimated': decimated, 'decimation_mode': mode}
        except Exception:
            pass

        raw_full = _safe_raw_file(file_param)
        if not os.path.exists(raw_full):
            raise FileNotFoundError('file not found')
        t, a, full_len, decimated, mode = read_raw_lecroy_txt(raw_full, None if full else max_points)
        return {'file': file_param, 'times': t, 'amps': a, 'raw': True, 'full_length': full_len, 'decimated': decimated, 'decimation_mode': mode}

    # Default: CSV under BASE_DIR
    full_path = safe_path(file_param)
    if not os.path.exists(full_path):
        raise FileNotFoundError('file not found')
    t, a = read_waveform_csv(full_path)
    return {'file': file_param, 'times': t, 'amps': a, 'raw': False}

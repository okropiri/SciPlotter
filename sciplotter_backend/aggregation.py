from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Sequence

import numpy as np

from . import importer


ROOT_AGGREGATION_CHUNK_SIZE = 250_000
DATASET_SESSION_TTL_SECONDS = 6 * 60 * 60
MAX_BINS_PER_AXIS = 2048
MAX_AGGREGATE_CACHE_ENTRIES = 8
MAX_CACHED_AGGREGATE_CELLS = 300_000


class DatasetSessionNotFoundError(KeyError):
    pass


class UnsupportedAggregationError(ValueError):
    pass


@dataclass
class _DatasetSession:
    session_id: str
    kind: str
    source_path: str
    display_name: str
    tree_path: str
    headers: tuple[str, ...]
    entry_count: int
    branch_count: int
    loadable_branch_count: int
    channel_field: str
    created_at: float
    accessed_at: float
    cache: Dict[str, Any] = field(default_factory=dict)
    aggregate_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    aggregate_cache_order: list[str] = field(default_factory=list)

    def to_payload(self) -> Dict[str, Any]:
        return {
            'session_id': self.session_id,
            'kind': self.kind,
            'source_path': self.source_path,
            'display_name': self.display_name,
            'tree_path': self.tree_path,
            'headers': list(self.headers),
            'entry_count': self.entry_count,
            'branch_count': self.branch_count,
            'loadable_branch_count': self.loadable_branch_count,
            'channel_field': self.channel_field,
            'created_at': self.created_at,
            'supports': {
                'exact_1d': True,
                'exact_2d': True,
                'direct_scalar_features_only': True,
                'derived_features': False,
                'exact_median': False,
            },
        }


@dataclass
class _RunningStats:
    count: int = 0
    minimum: float = math.inf
    maximum: float = -math.inf
    mean: float = 0.0
    m2: float = 0.0
    sum_sq: float = 0.0

    def update(self, values: np.ndarray) -> None:
        if values.size == 0:
            return

        arr = np.asarray(values, dtype=np.float64)
        if arr.size == 0:
            return

        chunk_count = int(arr.size)
        chunk_min = float(np.min(arr))
        chunk_max = float(np.max(arr))
        chunk_mean = float(np.mean(arr))
        centered = arr - chunk_mean
        chunk_m2 = float(np.dot(centered, centered))
        chunk_sum_sq = float(np.dot(arr, arr))

        if self.count == 0:
            self.count = chunk_count
            self.minimum = chunk_min
            self.maximum = chunk_max
            self.mean = chunk_mean
            self.m2 = chunk_m2
            self.sum_sq = chunk_sum_sq
            return

        delta = chunk_mean - self.mean
        total_count = self.count + chunk_count
        if total_count <= 0:
            return

        self.minimum = min(self.minimum, chunk_min)
        self.maximum = max(self.maximum, chunk_max)
        self.mean = ((self.count * self.mean) + (chunk_count * chunk_mean)) / total_count
        self.m2 = self.m2 + chunk_m2 + ((delta * delta) * self.count * chunk_count / total_count)
        self.sum_sq += chunk_sum_sq
        self.count = total_count

    def to_payload(self) -> Dict[str, Any]:
        if self.count <= 0 or not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            return {
                'count': 0,
                'min': None,
                'max': None,
                'mean': None,
                'median': None,
                'std_dev': None,
                'rms': None,
                'exact_median_available': False,
            }

        variance = max(0.0, self.m2 / self.count)
        rms = math.sqrt(max(0.0, self.sum_sq / self.count))
        return {
            'count': self.count,
            'min': self.minimum,
            'max': self.maximum,
            'mean': self.mean,
            'median': None,
            'std_dev': math.sqrt(variance),
            'rms': rms,
            'exact_median_available': False,
        }


@dataclass(frozen=True)
class _C3FilterFields:
    channel_field: str
    event_id_field: str
    time_field: str
    trace_field: str
    cycle_field: str
    cycle_is_ns: bool


_SESSION_LOCK = threading.Lock()
_DATASET_SESSIONS: Dict[str, _DatasetSession] = {}


def create_root_dataset_session(path: str, *, tree_path: str, channel_field: str = '') -> Dict[str, Any]:
    file_path = importer._resolve_file(path, allowed_suffixes=importer.SUPPORTED_IMPORT_FILE_TYPES['root'])
    normalized_tree_path = str(tree_path or '').strip()
    if not normalized_tree_path:
        raise ValueError('tree_path parameter required')

    uproot = importer._require_uproot()
    with uproot.open(file_path) as root_file:
        tree = root_file[normalized_tree_path]
        branch_info, loadable_headers = importer._describe_root_tree(tree)
        if not loadable_headers:
            raise ValueError('Selected TTree does not expose scalar branches that can be aggregated')
        entry_count = int(tree.num_entries)

    now = time.time()
    session = _DatasetSession(
        session_id=uuid.uuid4().hex,
        kind='root',
        source_path=file_path,
        display_name=f"{importer.Path(file_path).name} :: {normalized_tree_path}",
        tree_path=normalized_tree_path,
        headers=tuple(loadable_headers),
        entry_count=entry_count,
        branch_count=len(branch_info),
        loadable_branch_count=len(loadable_headers),
        channel_field=str(channel_field or '').strip(),
        created_at=now,
        accessed_at=now,
    )

    with _SESSION_LOCK:
        _purge_expired_sessions_locked(now)
        _DATASET_SESSIONS[session.session_id] = session

    return session.to_payload()


def aggregate_dataset_session(session_id: str, query: Dict[str, Any]) -> Dict[str, Any]:
    session = _get_dataset_session(session_id)
    if session.kind != 'root':
        raise UnsupportedAggregationError(f'Unsupported dataset session kind: {session.kind}')
    return _aggregate_root_session(session, query)


def get_dataset_channel_values(session_id: str, *, channel_field: str) -> Dict[str, Any]:
    session = _get_dataset_session(session_id)
    if session.kind != 'root':
        raise UnsupportedAggregationError(f'Unsupported dataset session kind: {session.kind}')

    feature = _require_direct_feature(session, channel_field, 'channel_field')
    cache_key = f'channel-values::{feature}'
    if cache_key not in session.cache:
        session.cache[cache_key] = _collect_distinct_feature_values(session, feature)
    values = session.cache[cache_key]
    return {
        'session_id': session.session_id,
        'channel_field': feature,
        'values': list(values),
        'count': len(values),
        'exact': True,
    }


def get_dataset_feature_bounds(
    session_id: str,
    *,
    feature: str,
    channel_field: str = '',
    channel_values: Any = None,
) -> Dict[str, Any]:
    session = _get_dataset_session(session_id)
    if session.kind != 'root':
        raise UnsupportedAggregationError(f'Unsupported dataset session kind: {session.kind}')

    normalized_feature = _require_direct_feature(session, feature, 'feature')
    normalized_channel_field = _require_channel_field(session, channel_field, channel_values)
    normalized_channel_values = tuple(_parse_channel_values(channel_values))
    cache_key = 'feature-bounds::{feature}::{field}::{values}'.format(
        feature=normalized_feature,
        field=normalized_channel_field,
        values='|'.join(normalized_channel_values),
    )
    if cache_key not in session.cache:
        session.cache[cache_key] = _compute_feature_bounds(
            session,
            normalized_feature,
            channel_field=normalized_channel_field,
            channel_values=normalized_channel_values,
        )
    stats = session.cache[cache_key]
    payload = stats.to_payload()
    return {
        'session_id': session.session_id,
        'feature': normalized_feature,
        'channel_field': normalized_channel_field,
        'channel_values': list(normalized_channel_values),
        'count': payload['count'],
        'min': payload['min'],
        'max': payload['max'],
        'exact': True,
    }


def _get_dataset_session(session_id: str) -> _DatasetSession:
    normalized_session_id = str(session_id or '').strip()
    if not normalized_session_id:
        raise DatasetSessionNotFoundError('session_id parameter required')

    now = time.time()
    with _SESSION_LOCK:
        _purge_expired_sessions_locked(now)
        session = _DATASET_SESSIONS.get(normalized_session_id)
        if not session:
            raise DatasetSessionNotFoundError(f'Unknown or expired dataset session: {normalized_session_id}')
        session.accessed_at = now
        return session


def _purge_expired_sessions_locked(now: float) -> None:
    expired = [
        session_id
        for session_id, session in _DATASET_SESSIONS.items()
        if (now - session.accessed_at) > DATASET_SESSION_TTL_SECONDS
    ]
    for session_id in expired:
        _DATASET_SESSIONS.pop(session_id, None)


def _aggregate_root_session(session: _DatasetSession, query: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(query.get('mode') or '1d').strip().lower()
    if mode not in ('1d', '2d'):
        raise ValueError(f'Unsupported aggregation mode: {mode}')

    x_feature = _require_direct_feature(session, query.get('x_feature'), 'x_feature')
    y_feature = _require_direct_feature(session, query.get('y_feature'), 'y_feature', allow_empty=(mode == '1d'))
    filter_feature = _require_direct_feature(session, query.get('filter_feature'), 'filter_feature', allow_empty=True)
    channel_field = _require_channel_field(session, query.get('channel_field'), query.get('channel_values'))
    channel_values = _parse_channel_values(query.get('channel_values'))

    x_filter_range = _parse_range_spec(query.get('x_filter_range', query.get('x_range')))
    y_filter_range = _parse_range_spec(query.get('y_filter_range', query.get('y_range'))) if mode == '2d' else (None, None)
    filter_range = _parse_range_spec(query.get('filter_range')) if filter_feature else (None, None)
    x_hist_range = _parse_range_spec(query.get('x_hist_range'))
    y_hist_range = _parse_range_spec(query.get('y_hist_range')) if mode == '2d' else (None, None)
    pileup_field = _resolve_pileup_field(session, query)

    bins_x = _parse_bins(query.get('bins_x'), 'bins_x')
    bins_y = _parse_bins(query.get('bins_y'), 'bins_y') if mode == '2d' else None
    merge_channels = _parse_bool(query.get('merge_channels'), default=True)
    gray_out_1d = _parse_bool(query.get('gray_out_1d'), default=False) if mode == '1d' else False
    phase_shift_ns = _to_optional_float(query.get('phase_shift_ns'))
    wrap_phase = _parse_bool(query.get('wrap_phase'), default=False)
    dataset_period_ns = _to_optional_float(query.get('dataset_period_ns'))
    advanced_filter = str(query.get('advanced_filter') or '').strip().lower()
    c3_window_ns = _to_optional_float(query.get('c3_window_ns'))
    if advanced_filter not in ('', 'c3_filter'):
        raise UnsupportedAggregationError(f'Unsupported advanced_filter: {advanced_filter}')
    c3_filter_fields = _describe_c3_filter_fields(session) if advanced_filter == 'c3_filter' else None

    branch_names = [x_feature]
    if mode == '2d' and y_feature:
        branch_names.append(y_feature)
    if filter_feature:
        branch_names.append(filter_feature)
    if channel_field:
        branch_names.append(channel_field)
    if pileup_field:
        branch_names.append(pileup_field)
    if c3_filter_fields is not None:
        branch_names.append(c3_filter_fields.channel_field)
        branch_names.append(c3_filter_fields.event_id_field)
    branch_names = list(dict.fromkeys(branch_names))

    cache_key = _build_root_aggregate_cache_key(
        mode=mode,
        x_feature=x_feature,
        y_feature=y_feature,
        filter_feature=filter_feature,
        channel_field=channel_field,
        channel_values=channel_values,
        x_filter_range=x_filter_range,
        y_filter_range=y_filter_range,
        filter_range=filter_range,
        x_hist_range=x_hist_range,
        y_hist_range=y_hist_range,
        pileup_field=pileup_field,
        bins_x=bins_x,
        bins_y=bins_y,
        merge_channels=merge_channels,
        gray_out_1d=gray_out_1d,
        phase_shift_ns=phase_shift_ns,
        wrap_phase=wrap_phase,
        dataset_period_ns=dataset_period_ns,
        advanced_filter=advanced_filter,
        c3_window_ns=c3_window_ns,
    )
    cached_payload = _get_cached_root_aggregate_payload(session, cache_key)
    if cached_payload is not None:
        return cached_payload

    c3_allowed_event_ids = _get_c3_allowed_event_ids(session, c3_filter_fields, c3_window_ns) if c3_filter_fields is not None else None

    use_phase_shift = (wrap_phase or (phase_shift_ns is not None and phase_shift_ns != 0.0))
    use_extended_1d = mode == '1d' and (
        not merge_channels
        or gray_out_1d
        or (use_phase_shift and _is_phase_shiftable_feature(x_feature))
        or c3_filter_fields is not None
    )
    if use_extended_1d:
        return _aggregate_root_session_1d_extended(
            session,
            cache_key=cache_key,
            x_feature=x_feature,
            filter_feature=filter_feature,
            channel_field=channel_field,
            channel_values=channel_values,
            x_filter_range=x_filter_range,
            filter_range=filter_range,
            x_hist_range=x_hist_range,
            pileup_field=pileup_field,
            bins_x=bins_x,
            merge_channels=merge_channels,
            gray_out_1d=gray_out_1d,
            phase_shift_ns=phase_shift_ns,
            wrap_phase=wrap_phase,
            dataset_period_ns=dataset_period_ns,
            c3_filter_fields=c3_filter_fields,
            c3_allowed_event_ids=c3_allowed_event_ids,
        )

    fixed_x_hist_range = _coerce_fixed_histogram_range(x_hist_range) if _has_fixed_histogram_range(x_hist_range) else None
    fixed_y_hist_range = _coerce_fixed_histogram_range(y_hist_range) if mode == '2d' and _has_fixed_histogram_range(y_hist_range) else None

    if fixed_x_hist_range is not None and (mode == '1d' or fixed_y_hist_range is not None):
        x_stats = _RunningStats()
        y_stats = _RunningStats() if mode == '2d' else None

        if mode == '1d':
            counts = np.zeros(bins_x, dtype=np.int64)
            for arrays in _iterate_root_chunks(session, branch_names):
                x_values = _coerce_numeric_array(arrays[x_feature], x_feature)
                if use_phase_shift:
                    x_values = _apply_phase_shift_values(
                        x_values,
                        x_feature,
                        phase_shift_ns=phase_shift_ns,
                        wrap_phase=wrap_phase,
                        dataset_period_ns=dataset_period_ns,
                    )
                f_values = _coerce_numeric_array(arrays[filter_feature], filter_feature) if filter_feature else None
                selection = _build_selection_mask(
                    x_values=x_values,
                    y_values=None,
                    filter_values=f_values,
                    channel_values=channel_values,
                    channel_field_values=(arrays[channel_field] if channel_field else None),
                    x_filter_range=x_filter_range,
                    y_filter_range=(None, None),
                    filter_range=filter_range,
                    pileup_values=(arrays[pileup_field] if pileup_field else None),
                )
                if c3_filter_fields is not None and c3_allowed_event_ids is not None:
                    selection &= _build_c3_filter_mask(arrays, c3_filter_fields, c3_allowed_event_ids)
                selected_x = x_values[selection]
                x_stats.update(selected_x)
                if selected_x.size == 0:
                    continue
                hist, edges = np.histogram(selected_x, bins=bins_x, range=fixed_x_hist_range)
                counts += hist.astype(np.int64, copy=False)

            if x_stats.count <= 0:
                return _cache_root_aggregate_payload(
                    session,
                    cache_key,
                    _build_empty_root_aggregate_payload(
                        session,
                        mode=mode,
                        x_feature=x_feature,
                        y_feature=y_feature,
                        filter_feature=filter_feature,
                        scan_passes=1,
                    ),
                )

            edges_list = edges.tolist()
            centers_list = ((edges[:-1] + edges[1:]) / 2.0).tolist()
            return _cache_root_aggregate_payload(
                session,
                cache_key,
                {
                    'session_id': session.session_id,
                    'kind': session.kind,
                    'mode': mode,
                    'x_feature': x_feature,
                    'filter_feature': filter_feature,
                    'histogram': {
                        'counts': counts.tolist(),
                        'edges': edges_list,
                        'centers': centers_list,
                    },
                    'stats': x_stats.to_payload(),
                    'notes': ['Median is intentionally omitted until an exact large-data implementation is added.'],
                    'meta': {
                        'exact': True,
                        'direct_scalar_features_only': True,
                        'selected_count': x_stats.count,
                        'entry_count': session.entry_count,
                        'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
                        'x_range': [fixed_x_hist_range[0], fixed_x_hist_range[1]],
                        'bins_x': bins_x,
                        'cache_hit': False,
                        'scan_passes': 1,
                    },
                },
            )

        assert bins_y is not None and fixed_y_hist_range is not None and y_stats is not None and y_feature
        counts_2d = np.zeros((bins_y, bins_x), dtype=np.int64)
        for arrays in _iterate_root_chunks(session, branch_names):
            x_values = _coerce_numeric_array(arrays[x_feature], x_feature)
            y_values = _coerce_numeric_array(arrays[y_feature], y_feature)
            if use_phase_shift:
                x_values = _apply_phase_shift_values(
                    x_values,
                    x_feature,
                    phase_shift_ns=phase_shift_ns,
                    wrap_phase=wrap_phase,
                    dataset_period_ns=dataset_period_ns,
                )
                y_values = _apply_phase_shift_values(
                    y_values,
                    y_feature,
                    phase_shift_ns=phase_shift_ns,
                    wrap_phase=wrap_phase,
                    dataset_period_ns=dataset_period_ns,
                )
            f_values = _coerce_numeric_array(arrays[filter_feature], filter_feature) if filter_feature else None
            selection = _build_selection_mask(
                x_values=x_values,
                y_values=y_values,
                filter_values=f_values,
                channel_values=channel_values,
                channel_field_values=(arrays[channel_field] if channel_field else None),
                x_filter_range=x_filter_range,
                y_filter_range=y_filter_range,
                filter_range=filter_range,
                pileup_values=(arrays[pileup_field] if pileup_field else None),
            )
            if c3_filter_fields is not None and c3_allowed_event_ids is not None:
                selection &= _build_c3_filter_mask(arrays, c3_filter_fields, c3_allowed_event_ids)
            selected_x = x_values[selection]
            selected_y = y_values[selection]
            x_stats.update(selected_x)
            y_stats.update(selected_y)
            if selected_x.size == 0:
                continue
            hist2d, x_edges, y_edges = np.histogram2d(
                selected_x,
                selected_y,
                bins=[bins_x, bins_y],
                range=[list(fixed_x_hist_range), list(fixed_y_hist_range)],
            )
            counts_2d += hist2d.T.astype(np.int64, copy=False)

        if x_stats.count <= 0:
            return _cache_root_aggregate_payload(
                session,
                cache_key,
                _build_empty_root_aggregate_payload(
                    session,
                    mode=mode,
                    x_feature=x_feature,
                    y_feature=y_feature,
                    filter_feature=filter_feature,
                    scan_passes=1,
                ),
            )

        return _cache_root_aggregate_payload(
            session,
            cache_key,
            {
                'session_id': session.session_id,
                'kind': session.kind,
                'mode': mode,
                'x_feature': x_feature,
                'y_feature': y_feature,
                'filter_feature': filter_feature,
                'histogram': {
                    'counts': counts_2d.tolist(),
                    'x_edges': x_edges.tolist(),
                    'y_edges': y_edges.tolist(),
                    'x_centers': ((x_edges[:-1] + x_edges[1:]) / 2.0).tolist(),
                    'y_centers': ((y_edges[:-1] + y_edges[1:]) / 2.0).tolist(),
                },
                'stats': {
                    'x': x_stats.to_payload(),
                    'y': y_stats.to_payload(),
                },
                'notes': ['Median is intentionally omitted until an exact large-data implementation is added.'],
                'meta': {
                    'exact': True,
                    'direct_scalar_features_only': True,
                    'selected_count': x_stats.count,
                    'entry_count': session.entry_count,
                    'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
                    'x_range': [fixed_x_hist_range[0], fixed_x_hist_range[1]],
                    'y_range': [fixed_y_hist_range[0], fixed_y_hist_range[1]],
                    'bins_x': bins_x,
                    'bins_y': bins_y,
                    'cache_hit': False,
                    'scan_passes': 1,
                },
            },
        )

    x_stats = _RunningStats()
    y_stats = _RunningStats() if mode == '2d' else None

    for arrays in _iterate_root_chunks(session, branch_names):
        x_values = _coerce_numeric_array(arrays[x_feature], x_feature)
        if use_phase_shift:
            x_values = _apply_phase_shift_values(
                x_values,
                x_feature,
                phase_shift_ns=phase_shift_ns,
                wrap_phase=wrap_phase,
                dataset_period_ns=dataset_period_ns,
            )
        y_values = _coerce_numeric_array(arrays[y_feature], y_feature) if mode == '2d' and y_feature else None
        if use_phase_shift and y_values is not None:
            y_values = _apply_phase_shift_values(
                y_values,
                y_feature,
                phase_shift_ns=phase_shift_ns,
                wrap_phase=wrap_phase,
                dataset_period_ns=dataset_period_ns,
            )
        f_values = _coerce_numeric_array(arrays[filter_feature], filter_feature) if filter_feature else None
        selection = _build_selection_mask(
            x_values=x_values,
            y_values=y_values,
            filter_values=f_values,
            channel_values=channel_values,
            channel_field_values=(arrays[channel_field] if channel_field else None),
            x_filter_range=x_filter_range,
            y_filter_range=y_filter_range,
            filter_range=filter_range,
            pileup_values=(arrays[pileup_field] if pileup_field else None),
        )
        if c3_filter_fields is not None and c3_allowed_event_ids is not None:
            selection &= _build_c3_filter_mask(arrays, c3_filter_fields, c3_allowed_event_ids)
        x_stats.update(x_values[selection])
        if y_stats is not None and y_values is not None:
            y_stats.update(y_values[selection])

    if x_stats.count <= 0:
        return _cache_root_aggregate_payload(
            session,
            cache_key,
            _build_empty_root_aggregate_payload(
                session,
                mode=mode,
                x_feature=x_feature,
                y_feature=y_feature,
                filter_feature=filter_feature,
                scan_passes=1,
            ),
        )

    x_hist_min, x_hist_max = _finalize_histogram_range(x_hist_range, x_stats)
    y_hist_min, y_hist_max = _finalize_histogram_range(y_hist_range, y_stats) if mode == '2d' and y_stats is not None else (None, None)

    if mode == '1d':
        counts = np.zeros(bins_x, dtype=np.int64)
        for arrays in _iterate_root_chunks(session, branch_names):
            x_values = _coerce_numeric_array(arrays[x_feature], x_feature)
            if use_phase_shift:
                x_values = _apply_phase_shift_values(
                    x_values,
                    x_feature,
                    phase_shift_ns=phase_shift_ns,
                    wrap_phase=wrap_phase,
                    dataset_period_ns=dataset_period_ns,
                )
            f_values = _coerce_numeric_array(arrays[filter_feature], filter_feature) if filter_feature else None
            selection = _build_selection_mask(
                x_values=x_values,
                y_values=None,
                filter_values=f_values,
                channel_values=channel_values,
                channel_field_values=(arrays[channel_field] if channel_field else None),
                x_filter_range=x_filter_range,
                y_filter_range=(None, None),
                filter_range=filter_range,
                pileup_values=(arrays[pileup_field] if pileup_field else None),
            )
            if c3_filter_fields is not None and c3_allowed_event_ids is not None:
                selection &= _build_c3_filter_mask(arrays, c3_filter_fields, c3_allowed_event_ids)
            selected_x = x_values[selection]
            if selected_x.size == 0:
                continue
            hist, edges = np.histogram(selected_x, bins=bins_x, range=(x_hist_min, x_hist_max))
            counts += hist.astype(np.int64, copy=False)

        edges_list = edges.tolist()
        centers_list = ((edges[:-1] + edges[1:]) / 2.0).tolist()
        return _cache_root_aggregate_payload(
            session,
            cache_key,
            {
                'session_id': session.session_id,
                'kind': session.kind,
                'mode': mode,
                'x_feature': x_feature,
                'filter_feature': filter_feature,
                'histogram': {
                    'counts': counts.tolist(),
                    'edges': edges_list,
                    'centers': centers_list,
                },
                'stats': x_stats.to_payload(),
                'notes': ['Median is intentionally omitted until an exact large-data implementation is added.'],
                'meta': {
                    'exact': True,
                    'direct_scalar_features_only': True,
                    'selected_count': x_stats.count,
                    'entry_count': session.entry_count,
                    'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
                    'x_range': [x_hist_min, x_hist_max],
                    'bins_x': bins_x,
                    'cache_hit': False,
                    'scan_passes': 2,
                },
            },
        )

    assert bins_y is not None and y_hist_min is not None and y_hist_max is not None and y_stats is not None and y_feature
    counts_2d = np.zeros((bins_y, bins_x), dtype=np.int64)
    for arrays in _iterate_root_chunks(session, branch_names):
        x_values = _coerce_numeric_array(arrays[x_feature], x_feature)
        y_values = _coerce_numeric_array(arrays[y_feature], y_feature)
        if use_phase_shift:
            x_values = _apply_phase_shift_values(
                x_values,
                x_feature,
                phase_shift_ns=phase_shift_ns,
                wrap_phase=wrap_phase,
                dataset_period_ns=dataset_period_ns,
            )
            y_values = _apply_phase_shift_values(
                y_values,
                y_feature,
                phase_shift_ns=phase_shift_ns,
                wrap_phase=wrap_phase,
                dataset_period_ns=dataset_period_ns,
            )
        f_values = _coerce_numeric_array(arrays[filter_feature], filter_feature) if filter_feature else None
        selection = _build_selection_mask(
            x_values=x_values,
            y_values=y_values,
            filter_values=f_values,
            channel_values=channel_values,
            channel_field_values=(arrays[channel_field] if channel_field else None),
            x_filter_range=x_filter_range,
            y_filter_range=y_filter_range,
            filter_range=filter_range,
            pileup_values=(arrays[pileup_field] if pileup_field else None),
        )
        if c3_filter_fields is not None and c3_allowed_event_ids is not None:
            selection &= _build_c3_filter_mask(arrays, c3_filter_fields, c3_allowed_event_ids)
        selected_x = x_values[selection]
        selected_y = y_values[selection]
        if selected_x.size == 0:
            continue
        hist2d, x_edges, y_edges = np.histogram2d(
            selected_x,
            selected_y,
            bins=[bins_x, bins_y],
            range=[[x_hist_min, x_hist_max], [y_hist_min, y_hist_max]],
        )
        counts_2d += hist2d.T.astype(np.int64, copy=False)

    return _cache_root_aggregate_payload(
        session,
        cache_key,
        {
            'session_id': session.session_id,
            'kind': session.kind,
            'mode': mode,
            'x_feature': x_feature,
            'y_feature': y_feature,
            'filter_feature': filter_feature,
            'histogram': {
                'counts': counts_2d.tolist(),
                'x_edges': x_edges.tolist(),
                'y_edges': y_edges.tolist(),
                'x_centers': ((x_edges[:-1] + x_edges[1:]) / 2.0).tolist(),
                'y_centers': ((y_edges[:-1] + y_edges[1:]) / 2.0).tolist(),
            },
            'stats': {
                'x': x_stats.to_payload(),
                'y': y_stats.to_payload(),
            },
            'notes': ['Median is intentionally omitted until an exact large-data implementation is added.'],
            'meta': {
                'exact': True,
                'direct_scalar_features_only': True,
                'selected_count': x_stats.count,
                'entry_count': session.entry_count,
                'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
                'x_range': [x_hist_min, x_hist_max],
                'y_range': [y_hist_min, y_hist_max],
                'bins_x': bins_x,
                'bins_y': bins_y,
                'cache_hit': False,
                'scan_passes': 2,
            },
        },
    )


def _build_empty_root_aggregate_payload(
    session: _DatasetSession,
    *,
    mode: str,
    x_feature: str,
    y_feature: str,
    filter_feature: str,
    scan_passes: int,
) -> Dict[str, Any]:
    return {
        'session_id': session.session_id,
        'kind': session.kind,
        'mode': mode,
        'x_feature': x_feature,
        'y_feature': y_feature,
        'filter_feature': filter_feature,
        'histogram': None,
        'stats': None,
        'notes': ['No rows matched the requested filters.'],
        'meta': {
            'exact': True,
            'direct_scalar_features_only': True,
            'selected_count': 0,
            'entry_count': session.entry_count,
            'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
            'cache_hit': False,
            'scan_passes': scan_passes,
        },
    }


def _aggregate_root_session_1d_extended(
    session: _DatasetSession,
    *,
    cache_key: str,
    x_feature: str,
    filter_feature: str,
    channel_field: str,
    channel_values: Sequence[str],
    x_filter_range: tuple[Optional[float], Optional[float]],
    filter_range: tuple[Optional[float], Optional[float]],
    x_hist_range: tuple[Optional[float], Optional[float]],
    pileup_field: str,
    bins_x: int,
    merge_channels: bool,
    gray_out_1d: bool,
    phase_shift_ns: Optional[float],
    wrap_phase: bool,
    dataset_period_ns: Optional[float],
    c3_filter_fields: Optional[_C3FilterFields],
    c3_allowed_event_ids: Optional[frozenset[str]],
) -> Dict[str, Any]:
    branch_names = [x_feature]
    if filter_feature:
        branch_names.append(filter_feature)
    if channel_field:
        branch_names.append(channel_field)
    if pileup_field:
        branch_names.append(pileup_field)
    if c3_filter_fields is not None:
        branch_names.append(c3_filter_fields.channel_field)
        branch_names.append(c3_filter_fields.event_id_field)
    branch_names = list(dict.fromkeys(branch_names))

    fixed_hist_range = _coerce_fixed_histogram_range(x_hist_range) if _has_fixed_histogram_range(x_hist_range) else None
    selected_stats = _RunningStats()
    filtered_stats = _RunningStats() if gray_out_1d else None
    per_channel_stats: dict[str, _RunningStats] = {} if not merge_channels else {}

    selected_counts: Optional[np.ndarray] = None
    filtered_counts: Optional[np.ndarray] = None
    per_channel_counts: dict[str, np.ndarray] = {}
    edges: Optional[np.ndarray] = None

    def scan(*, collect_hist: bool, histogram_range: Optional[tuple[float, float]], update_stats: bool) -> None:
        nonlocal selected_counts, filtered_counts, edges
        if collect_hist and histogram_range is not None:
            selected_counts = np.zeros(bins_x, dtype=np.int64)
            if gray_out_1d:
                filtered_counts = np.zeros(bins_x, dtype=np.int64)

        for arrays in _iterate_root_chunks(session, branch_names):
            x_values = _coerce_numeric_array(arrays[x_feature], x_feature)
            x_values = _apply_phase_shift_values(
                x_values,
                x_feature,
                phase_shift_ns=phase_shift_ns,
                wrap_phase=wrap_phase,
                dataset_period_ns=dataset_period_ns,
            )

            filter_values = _coerce_numeric_array(arrays[filter_feature], filter_feature) if filter_feature else None
            channel_field_values = np.asarray(arrays[channel_field]) if channel_field else None
            pileup_values = np.asarray(arrays[pileup_field]) if pileup_field else None

            base_mask = np.isfinite(x_values)
            if channel_values:
                if channel_field_values is None:
                    raise ValueError('channel_field_values missing while channel_values are active')
                base_mask &= _match_channel_values(channel_field_values, channel_values)
            if pileup_values is not None:
                base_mask &= ~_match_truthy_values(pileup_values)
            if c3_filter_fields is not None and c3_allowed_event_ids is not None:
                c3_channel_values = np.asarray(arrays[c3_filter_fields.channel_field])
                c3_event_id_values = np.asarray(arrays[c3_filter_fields.event_id_field])
                base_mask &= _normalize_overlay_labels(c3_channel_values, x_values.shape[0]) == 'C4'

            event_mask = np.ones(base_mask.shape[0], dtype=bool)
            if filter_values is not None:
                event_mask &= np.isfinite(filter_values)
                if filter_range[0] is not None:
                    event_mask &= filter_values >= filter_range[0]
                if filter_range[1] is not None:
                    event_mask &= filter_values <= filter_range[1]
            if c3_filter_fields is not None and c3_allowed_event_ids is not None:
                event_mask &= _match_identifier_values(c3_event_id_values, c3_allowed_event_ids)

            x_range_mask = np.ones(base_mask.shape[0], dtype=bool)
            if x_filter_range[0] is not None:
                x_range_mask &= x_values >= x_filter_range[0]
            if x_filter_range[1] is not None:
                x_range_mask &= x_values <= x_filter_range[1]

            selected_mask = base_mask & event_mask & x_range_mask
            filtered_mask = (base_mask & ~(event_mask & x_range_mask)) if gray_out_1d else None

            selected_x = x_values[selected_mask]
            if update_stats:
                selected_stats.update(selected_x)

            if not merge_channels and selected_x.size > 0:
                selected_labels = _normalize_overlay_labels(channel_field_values, x_values.shape[0])[selected_mask]
                for label in np.unique(selected_labels):
                    label_mask = selected_labels == label
                    label_values = selected_x[label_mask]
                    if update_stats:
                        if label not in per_channel_stats:
                            per_channel_stats[label] = _RunningStats()
                        per_channel_stats[label].update(label_values)
                    if collect_hist and histogram_range is not None:
                        hist, local_edges = np.histogram(label_values, bins=bins_x, range=histogram_range)
                        if label not in per_channel_counts:
                            per_channel_counts[label] = np.zeros(bins_x, dtype=np.int64)
                        per_channel_counts[label] += hist.astype(np.int64, copy=False)
                        if edges is None:
                            edges = local_edges

            if gray_out_1d and filtered_stats is not None and filtered_mask is not None:
                filtered_x = x_values[filtered_mask]
                if update_stats:
                    filtered_stats.update(filtered_x)
                if collect_hist and histogram_range is not None and filtered_x.size > 0 and filtered_counts is not None:
                    hist, local_edges = np.histogram(filtered_x, bins=bins_x, range=histogram_range)
                    filtered_counts += hist.astype(np.int64, copy=False)
                    if edges is None:
                        edges = local_edges

            if merge_channels and collect_hist and histogram_range is not None and selected_x.size > 0 and selected_counts is not None:
                hist, local_edges = np.histogram(selected_x, bins=bins_x, range=histogram_range)
                selected_counts += hist.astype(np.int64, copy=False)
                if edges is None:
                    edges = local_edges

    if fixed_hist_range is not None:
        scan(collect_hist=True, histogram_range=fixed_hist_range, update_stats=True)
        scan_passes = 1
        x_hist_min, x_hist_max = fixed_hist_range
    else:
        scan(collect_hist=False, histogram_range=None, update_stats=True)
        axis_stats = selected_stats if selected_stats.count > 0 else filtered_stats
        if axis_stats is None or axis_stats.count <= 0:
            return _cache_root_aggregate_payload(
                session,
                cache_key,
                _build_empty_root_aggregate_payload(
                    session,
                    mode='1d',
                    x_feature=x_feature,
                    y_feature='',
                    filter_feature=filter_feature,
                    scan_passes=1,
                ),
            )
        x_hist_min, x_hist_max = _finalize_histogram_range(x_hist_range, axis_stats)
        scan(collect_hist=True, histogram_range=(x_hist_min, x_hist_max), update_stats=False)
        scan_passes = 2

    selected_count = selected_stats.count
    filtered_count = filtered_stats.count if filtered_stats is not None else 0
    if selected_count <= 0 and filtered_count <= 0:
        return _cache_root_aggregate_payload(
            session,
            cache_key,
            _build_empty_root_aggregate_payload(
                session,
                mode='1d',
                x_feature=x_feature,
                y_feature='',
                filter_feature=filter_feature,
                scan_passes=scan_passes,
            ),
        )

    if edges is None:
        edges = np.linspace(x_hist_min, x_hist_max, bins_x + 1, dtype=np.float64)
    edges_list = edges.tolist()
    centers_list = ((edges[:-1] + edges[1:]) / 2.0).tolist()

    if merge_channels and not gray_out_1d:
        return _cache_root_aggregate_payload(
            session,
            cache_key,
            {
                'session_id': session.session_id,
                'kind': session.kind,
                'mode': '1d',
                'x_feature': x_feature,
                'filter_feature': filter_feature,
                'histogram': {
                    'counts': (selected_counts.tolist() if selected_counts is not None else [0] * bins_x),
                    'edges': edges_list,
                    'centers': centers_list,
                },
                'stats': selected_stats.to_payload(),
                'notes': ['Median is intentionally omitted until an exact large-data implementation is added.'],
                'meta': {
                    'exact': True,
                    'direct_scalar_features_only': True,
                    'selected_count': selected_count,
                    'entry_count': session.entry_count,
                    'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
                    'x_range': [x_hist_min, x_hist_max],
                    'bins_x': bins_x,
                    'cache_hit': False,
                    'scan_passes': scan_passes,
                },
            },
        )

    series = []
    if not merge_channels:
        for label in sorted(per_channel_counts.keys(), key=_natural_sort_key):
            counts = per_channel_counts[label]
            stats_payload = per_channel_stats[label].to_payload() if label in per_channel_stats else None
            series.append({
                'label': label,
                'bucket': 'channel',
                'counts': counts.tolist(),
                'edges': edges_list,
                'centers': centers_list,
                'stats': stats_payload,
            })
    elif selected_counts is not None and selected_count > 0:
        series.append({
            'label': 'selected',
            'bucket': 'selected',
            'counts': selected_counts.tolist(),
            'edges': edges_list,
            'centers': centers_list,
            'stats': selected_stats.to_payload(),
        })

    if gray_out_1d and filtered_counts is not None and filtered_count > 0:
        series.append({
            'label': 'filtered',
            'bucket': 'filtered',
            'counts': filtered_counts.tolist(),
            'edges': edges_list,
            'centers': centers_list,
            'stats': filtered_stats.to_payload() if filtered_stats is not None else None,
        })

    return _cache_root_aggregate_payload(
        session,
        cache_key,
        {
            'session_id': session.session_id,
            'kind': session.kind,
            'mode': '1d',
            'x_feature': x_feature,
            'filter_feature': filter_feature,
            'histogram': {
                'series': series,
                'edges': edges_list,
                'centers': centers_list,
            },
            'stats': {
                'selected': selected_stats.to_payload() if selected_count > 0 else None,
                'filtered': filtered_stats.to_payload() if filtered_stats is not None and filtered_count > 0 else None,
                'series': [
                    {
                        'label': label,
                        **per_channel_stats[label].to_payload(),
                    }
                    for label in sorted(per_channel_stats.keys(), key=_natural_sort_key)
                    if per_channel_stats[label].count > 0
                ],
            },
            'notes': ['Median is intentionally omitted until an exact large-data implementation is added.'],
            'meta': {
                'exact': True,
                'direct_scalar_features_only': True,
                'selected_count': selected_count,
                'filtered_count': filtered_count,
                'entry_count': session.entry_count,
                'chunk_size': ROOT_AGGREGATION_CHUNK_SIZE,
                'x_range': [x_hist_min, x_hist_max],
                'bins_x': bins_x,
                'merge_channels': merge_channels,
                'gray_out_1d': gray_out_1d,
                'cache_hit': False,
                'scan_passes': scan_passes,
            },
        },
    )


def _build_root_aggregate_cache_key(
    *,
    mode: str,
    x_feature: str,
    y_feature: str,
    filter_feature: str,
    channel_field: str,
    channel_values: Sequence[str],
    x_filter_range: tuple[Optional[float], Optional[float]],
    y_filter_range: tuple[Optional[float], Optional[float]],
    filter_range: tuple[Optional[float], Optional[float]],
    x_hist_range: tuple[Optional[float], Optional[float]],
    y_hist_range: tuple[Optional[float], Optional[float]],
    pileup_field: str,
    bins_x: int,
    bins_y: Optional[int],
    merge_channels: bool,
    gray_out_1d: bool,
    phase_shift_ns: Optional[float],
    wrap_phase: bool,
    dataset_period_ns: Optional[float],
    advanced_filter: str,
    c3_window_ns: Optional[float],
) -> str:
    payload = {
        'mode': mode,
        'x_feature': x_feature,
        'y_feature': y_feature,
        'filter_feature': filter_feature,
        'channel_field': channel_field,
        'channel_values': sorted({str(value) for value in channel_values}),
        'x_filter_range': [x_filter_range[0], x_filter_range[1]],
        'y_filter_range': [y_filter_range[0], y_filter_range[1]],
        'filter_range': [filter_range[0], filter_range[1]],
        'x_hist_range': [x_hist_range[0], x_hist_range[1]],
        'y_hist_range': [y_hist_range[0], y_hist_range[1]],
        'pileup_field': pileup_field,
        'bins_x': bins_x,
        'bins_y': bins_y,
        'merge_channels': merge_channels,
        'gray_out_1d': gray_out_1d,
        'phase_shift_ns': phase_shift_ns,
        'wrap_phase': wrap_phase,
        'dataset_period_ns': dataset_period_ns,
        'advanced_filter': advanced_filter,
        'c3_window_ns': c3_window_ns,
    }
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def _get_cached_root_aggregate_payload(session: _DatasetSession, cache_key: str) -> Optional[Dict[str, Any]]:
    payload = session.aggregate_cache.get(cache_key)
    if payload is None:
        return None
    meta = dict(payload.get('meta') or {})
    meta['cache_hit'] = True
    meta['scan_passes'] = 0
    return {
        **payload,
        'meta': meta,
    }


def _cache_root_aggregate_payload(session: _DatasetSession, cache_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _should_cache_root_aggregate_payload(payload):
        return payload

    session.aggregate_cache[cache_key] = payload
    session.aggregate_cache_order = [key for key in session.aggregate_cache_order if key != cache_key]
    session.aggregate_cache_order.append(cache_key)
    while len(session.aggregate_cache_order) > MAX_AGGREGATE_CACHE_ENTRIES:
        oldest_key = session.aggregate_cache_order.pop(0)
        session.aggregate_cache.pop(oldest_key, None)
    return payload


def _should_cache_root_aggregate_payload(payload: Dict[str, Any]) -> bool:
    histogram = payload.get('histogram')
    if histogram is None:
        return True

    meta = payload.get('meta') or {}
    mode = str(payload.get('mode') or '').strip().lower()
    if mode == '1d':
        if isinstance(histogram, dict) and isinstance(histogram.get('series'), list):
            cells = sum(len(series.get('counts') or []) for series in histogram.get('series') or [])
        else:
            cells = int(meta.get('bins_x') or 0)
    elif mode == '2d':
        cells = int(meta.get('bins_x') or 0) * int(meta.get('bins_y') or 0)
    else:
        return False
    return 0 < cells <= MAX_CACHED_AGGREGATE_CELLS


def _has_fixed_histogram_range(requested_range: tuple[Optional[float], Optional[float]]) -> bool:
    return requested_range[0] is not None and requested_range[1] is not None


def _coerce_fixed_histogram_range(requested_range: tuple[Optional[float], Optional[float]]) -> tuple[float, float]:
    if requested_range[0] is None or requested_range[1] is None:
        raise ValueError('Requested histogram range must define both min and max for the single-pass path')
    start = float(requested_range[0])
    end = float(requested_range[1])
    if end == start:
        start -= 0.5
        end += 0.5
    return start, end


def _describe_c3_filter_fields(session: _DatasetSession) -> _C3FilterFields:
    headers = session.headers
    channel_field = session.channel_field if session.channel_field in headers else ''
    if not channel_field:
        for candidate in ('channel', 'Channel'):
            if candidate in headers:
                channel_field = candidate
                break

    event_id_candidates = ('event_id', 'EventID', 'Event_Id', 'id', 'Id')
    time_candidates = ('event_start_time_ns', 'EventStartTime', 'event_time_ns', 'EventTime', 'peak_time_ns', 'delta_t_ns', 'deltaT_ns')
    cycle_candidates = ('cycle_start_time_s', 'timeslice_start_time_s', 'TimesliceStartTime', 'timeslice_start_ns', 'TimesliceStartNs')
    trace_candidates = ('trace_id', 'TraceID', 'trace', 'Trace', 'file', 'File', 'filename', 'basename', 'source_file', 'source', 'run', 'Run', 'segment', 'Segment', 'cycle_id', 'CycleID', 'cycle', 'TimesliceID', 'timeslice_id')

    event_id_field = next((candidate for candidate in event_id_candidates if candidate in headers), '')
    time_field = next((candidate for candidate in time_candidates if candidate in headers), '')
    cycle_field = next((candidate for candidate in cycle_candidates if candidate in headers), '')
    trace_field = next((candidate for candidate in trace_candidates if candidate in headers), '')
    if not trace_field:
        trace_field = cycle_field

    if not channel_field or not event_id_field or not time_field or not trace_field:
        raise UnsupportedAggregationError('C3_Filter requires channel, event id, time, and trace fields that are not available in this dataset session')

    cycle_is_ns = bool(cycle_field and (re.search(r'ns$', cycle_field, re.IGNORECASE) or re.search(r'StartNs$', cycle_field, re.IGNORECASE)))
    return _C3FilterFields(
        channel_field=channel_field,
        event_id_field=event_id_field,
        time_field=time_field,
        trace_field=trace_field,
        cycle_field=cycle_field,
        cycle_is_ns=cycle_is_ns,
    )


def _get_c3_allowed_event_ids(
    session: _DatasetSession,
    fields: Optional[_C3FilterFields],
    window_ns: Optional[float],
) -> Optional[frozenset[str]]:
    if fields is None:
        return None
    normalized_window = abs(float(window_ns) if window_ns is not None else 80.0)
    cache_key = f'c3-filter::{fields.channel_field}::{fields.event_id_field}::{fields.time_field}::{fields.trace_field}::{fields.cycle_field}::{normalized_window}'
    cached = session.cache.get(cache_key)
    if cached is not None:
        return cached

    branch_names = [fields.channel_field, fields.event_id_field, fields.time_field, fields.trace_field]
    if fields.cycle_field:
        branch_names.append(fields.cycle_field)

    trace_id_lookup: dict[str, int] = {}
    c3_trace_chunks: list[np.ndarray] = []
    c3_time_chunks: list[np.ndarray] = []
    c4_trace_chunks: list[np.ndarray] = []
    c4_time_chunks: list[np.ndarray] = []
    c4_id_chunks: list[np.ndarray] = []

    for arrays in _iterate_root_chunks(session, branch_names):
        channel_values = _normalize_overlay_labels(np.asarray(arrays[fields.channel_field]), len(np.asarray(arrays[fields.channel_field])))
        c3_mask = channel_values == 'C3'
        c4_mask = channel_values == 'C4'
        union_mask = c3_mask | c4_mask
        if not np.any(union_mask):
            continue

        event_ids = _normalize_identifier_values(np.asarray(arrays[fields.event_id_field]))
        trace_values = _normalize_identifier_values(np.asarray(arrays[fields.trace_field]))
        time_values = _coerce_numeric_array(arrays[fields.time_field], fields.time_field)
        valid_mask = np.isfinite(time_values) & (event_ids != '') & (trace_values != '')
        if fields.cycle_field:
            cycle_values = _coerce_numeric_array(arrays[fields.cycle_field], fields.cycle_field)
            valid_mask &= np.isfinite(cycle_values)
            cycle_ns = cycle_values if fields.cycle_is_ns else (cycle_values * 1e9)
        else:
            cycle_ns = 0.0
        abs_times = time_values + cycle_ns

        union_mask &= valid_mask
        if not np.any(union_mask):
            continue

        union_channels = channel_values[union_mask]
        union_event_ids = event_ids[union_mask]
        union_traces = trace_values[union_mask]
        union_times = abs_times[union_mask]
        unique_traces, inverse = np.unique(union_traces, return_inverse=True)
        mapped_unique = np.empty(unique_traces.shape[0], dtype=np.int32)
        for idx, trace in enumerate(unique_traces):
            mapped_unique[idx] = trace_id_lookup.setdefault(str(trace), len(trace_id_lookup))
        union_trace_ids = mapped_unique[inverse]

        union_c3 = union_channels == 'C3'
        union_c4 = union_channels == 'C4'
        if np.any(union_c3):
            c3_trace_chunks.append(union_trace_ids[union_c3])
            c3_time_chunks.append(union_times[union_c3].astype(np.float64, copy=False))
        if np.any(union_c4):
            c4_trace_chunks.append(union_trace_ids[union_c4])
            c4_time_chunks.append(union_times[union_c4].astype(np.float64, copy=False))
            c4_id_chunks.append(union_event_ids[union_c4])

    if not c3_trace_chunks or not c4_trace_chunks:
        session.cache[cache_key] = frozenset()
        return session.cache[cache_key]

    c3_traces = np.concatenate(c3_trace_chunks)
    c3_times = np.concatenate(c3_time_chunks)
    c4_traces = np.concatenate(c4_trace_chunks)
    c4_times = np.concatenate(c4_time_chunks)
    c4_ids = np.concatenate(c4_id_chunks)

    c3_order = np.lexsort((c3_times, c3_traces))
    c4_order = np.lexsort((c4_times, c4_traces))
    c3_traces = c3_traces[c3_order]
    c3_times = c3_times[c3_order]
    c4_traces = c4_traces[c4_order]
    c4_times = c4_times[c4_order]
    c4_ids = c4_ids[c4_order]

    c3_unique, c3_starts = np.unique(c3_traces, return_index=True)
    c4_unique, c4_starts = np.unique(c4_traces, return_index=True)
    c3_bounds = {
        int(trace): (int(start), int(c3_starts[idx + 1]) if idx + 1 < len(c3_starts) else int(c3_traces.size))
        for idx, (trace, start) in enumerate(zip(c3_unique, c3_starts))
    }

    allowed: set[str] = set()
    for idx, trace in enumerate(c4_unique):
        c4_start = int(c4_starts[idx])
        c4_end = int(c4_starts[idx + 1]) if idx + 1 < len(c4_starts) else int(c4_traces.size)
        c3_slice = c3_bounds.get(int(trace))
        if c3_slice is None:
            continue
        c3_start, c3_end = c3_slice
        trace_c3_times = c3_times[c3_start:c3_end]
        if trace_c3_times.size == 0:
            continue
        j = 0
        for c4_index in range(c4_start, c4_end):
            t4 = c4_times[c4_index]
            while j < trace_c3_times.size and trace_c3_times[j] < t4 - normalized_window:
                j += 1
            passed = (j < trace_c3_times.size and abs(trace_c3_times[j] - t4) <= normalized_window)
            if not passed and j > 0:
                passed = abs(trace_c3_times[j - 1] - t4) <= normalized_window
            if passed:
                allowed.add(str(c4_ids[c4_index]))

    session.cache[cache_key] = frozenset(allowed)
    return session.cache[cache_key]


def _build_c3_filter_mask(
    arrays: Dict[str, Any],
    fields: _C3FilterFields,
    allowed_event_ids: frozenset[str],
) -> np.ndarray:
    channel_values = _normalize_overlay_labels(np.asarray(arrays[fields.channel_field]), len(np.asarray(arrays[fields.channel_field])))
    event_ids = _normalize_identifier_values(np.asarray(arrays[fields.event_id_field]))
    return (channel_values == 'C4') & _match_identifier_values(event_ids, allowed_event_ids)


def _parse_bool(raw_value: Any, *, default: bool = False) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in ('', '0', 'false', 'no', 'off')
    return bool(raw_value)


def _is_phase_shiftable_feature(feature_name: str) -> bool:
    return feature_name in {
        'event_start_time_ns',
        'EventStartTime',
        'event_time_ns',
        'EventTime',
        'peak_time_ns',
        'PeakTime',
        'peak_time',
        'PeakTimeNs',
        'event_end_time_ns',
        'EventEndTime',
        'event_end_time',
        'EventEndTimeNs',
    }


def _apply_phase_shift_values(
    values: np.ndarray,
    feature_name: str,
    *,
    phase_shift_ns: Optional[float],
    wrap_phase: bool,
    dataset_period_ns: Optional[float],
) -> np.ndarray:
    if not _is_phase_shiftable_feature(feature_name):
        return values
    shift = float(phase_shift_ns) if phase_shift_ns is not None else 0.0
    if shift == 0.0 and not wrap_phase:
        return values

    arr = np.asarray(values, dtype=np.float64)
    shifted = arr + shift
    if not wrap_phase:
        return shifted
    period = float(dataset_period_ns) if dataset_period_ns is not None and dataset_period_ns > 0 else 40.0
    return np.mod(np.mod(shifted, period) + period, period)


def _normalize_overlay_labels(raw_values: Optional[np.ndarray], row_count: int) -> np.ndarray:
    if raw_values is None:
        return np.full(row_count, 'ALL', dtype=object)
    if raw_values.ndim != 1:
        raise ValueError('overlay grouping field must be one-dimensional')
    if raw_values.dtype == np.bool_:
        return np.asarray(['TRUE' if bool(value) else 'FALSE' for value in raw_values], dtype=object)
    if np.issubdtype(raw_values.dtype, np.integer):
        return np.asarray([str(int(value)).strip().upper() for value in raw_values], dtype=object)
    if np.issubdtype(raw_values.dtype, np.floating):
        normalized = []
        for value in raw_values:
            as_float = float(value)
            if math.isfinite(as_float) and as_float.is_integer():
                normalized.append(str(int(as_float)))
            else:
                normalized.append(str(as_float))
        return np.asarray([value.strip().upper() for value in normalized], dtype=object)
    return np.char.upper(np.char.strip(raw_values.astype(str))).astype(object)


def _normalize_identifier_values(raw_values: np.ndarray) -> np.ndarray:
    if raw_values.ndim != 1:
        raise ValueError('identifier field must be one-dimensional')
    if raw_values.dtype == np.bool_:
        return np.asarray(['true' if bool(value) else 'false' for value in raw_values], dtype=object)
    if np.issubdtype(raw_values.dtype, np.integer):
        return np.asarray([str(int(value)).strip() for value in raw_values], dtype=object)
    if np.issubdtype(raw_values.dtype, np.floating):
        normalized = []
        for value in raw_values:
            as_float = float(value)
            if math.isfinite(as_float) and as_float.is_integer():
                normalized.append(str(int(as_float)))
            elif math.isfinite(as_float):
                normalized.append(str(as_float))
            else:
                normalized.append('')
        return np.asarray(normalized, dtype=object)
    return np.char.strip(raw_values.astype(str)).astype(object)


def _match_identifier_values(raw_values: np.ndarray, selected_values: Sequence[str] | frozenset[str]) -> np.ndarray:
    normalized = _normalize_identifier_values(raw_values)
    clean_selected = [str(value).strip() for value in selected_values if str(value).strip() != '']
    if not clean_selected:
        return np.zeros(normalized.shape[0], dtype=bool)
    return np.isin(normalized, np.asarray(clean_selected, dtype=object))


def _natural_sort_key(value: str) -> tuple[Any, ...]:
    parts = re.split(r'(\d+)', str(value))
    return tuple(int(part) if part.isdigit() else part.lower() for part in parts)


def _compute_feature_bounds(
    session: _DatasetSession,
    feature: str,
    *,
    channel_field: str = '',
    channel_values: Sequence[str] = (),
) -> _RunningStats:
    branch_names = [feature]
    if channel_field:
        branch_names.append(channel_field)

    stats = _RunningStats()
    for arrays in _iterate_root_chunks(session, branch_names):
        values = _coerce_numeric_array(arrays[feature], feature)
        selection = np.isfinite(values)
        if channel_field and channel_values:
            selection &= _match_channel_values(np.asarray(arrays[channel_field]), channel_values)
        stats.update(values[selection])
    return stats


def _collect_distinct_feature_values(session: _DatasetSession, feature: str) -> list[Any]:
    numeric_values: set[Any] = set()
    string_values: set[str] = set()
    saw_numeric = False

    for arrays in _iterate_root_chunks(session, [feature]):
        raw_values = np.asarray(arrays[feature])
        if raw_values.ndim != 1:
            raise UnsupportedAggregationError(
                f"Feature '{feature}' is not a one-dimensional scalar branch supported by the exact backend path"
            )

        if np.issubdtype(raw_values.dtype, np.integer):
            saw_numeric = True
            for value in np.unique(raw_values):
                numeric_values.add(int(value))
            continue
        if np.issubdtype(raw_values.dtype, np.floating):
            saw_numeric = True
            finite_values = raw_values[np.isfinite(raw_values)]
            for value in np.unique(finite_values):
                as_float = float(value)
                if math.isfinite(as_float) and as_float.is_integer():
                    numeric_values.add(int(as_float))
                else:
                    numeric_values.add(as_float)
            continue

        normalized = np.char.strip(raw_values.astype(str))
        for value in np.unique(normalized):
            if value:
                string_values.add(str(value))

    if saw_numeric and not string_values:
        return sorted(numeric_values, key=lambda value: (float(value), str(value)))
    if string_values and not numeric_values:
        return sorted(string_values, key=lambda value: value.lower())

    combined = [str(value) for value in numeric_values] + sorted(string_values, key=lambda value: value.lower())
    return sorted(combined, key=lambda value: value.lower())


def _require_direct_feature(session: _DatasetSession, raw_feature: Any, field_name: str, *, allow_empty: bool = False) -> str:
    feature = str(raw_feature or '').strip()
    if not feature:
        if allow_empty:
            return ''
        raise ValueError(f'{field_name} parameter required')
    if feature not in session.headers:
        raise UnsupportedAggregationError(
            f"Feature '{feature}' is not a direct scalar ROOT branch in this session. Derived or synthetic histogram features are not supported by the exact backend path yet."
        )
    return feature


def _require_channel_field(session: _DatasetSession, raw_channel_field: Any, raw_channel_values: Any) -> str:
    channel_field = str(raw_channel_field or session.channel_field or '').strip()
    if channel_field and channel_field not in session.headers:
        raise ValueError(f"Unknown channel_field '{channel_field}' for this dataset session")
    if _parse_channel_values(raw_channel_values) and not channel_field:
        raise ValueError('channel_field is required when channel_values are provided')
    return channel_field


def _parse_channel_values(raw_values: Any) -> list[str]:
    if raw_values is None:
        return []
    if isinstance(raw_values, str):
        values = [item.strip() for item in raw_values.split(',')]
    elif isinstance(raw_values, Iterable):
        values = [str(item).strip() for item in raw_values]
    else:
        values = [str(raw_values).strip()]
    return [value for value in values if value != '']


def _parse_range_spec(raw_range: Any) -> tuple[Optional[float], Optional[float]]:
    lower = None
    upper = None

    if raw_range is None:
        return lower, upper
    if isinstance(raw_range, dict):
        lower = _to_optional_float(raw_range.get('min'))
        upper = _to_optional_float(raw_range.get('max'))
    elif isinstance(raw_range, (list, tuple)):
        if len(raw_range) >= 1:
            lower = _to_optional_float(raw_range[0])
        if len(raw_range) >= 2:
            upper = _to_optional_float(raw_range[1])
    else:
        raise ValueError('Range parameters must be arrays or objects with min/max')

    if lower is not None and upper is not None and upper < lower:
        raise ValueError('Range max must be greater than or equal to range min')
    return lower, upper


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == '':
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def _parse_bins(raw_value: Any, field_name: str) -> int:
    try:
        bins = int(raw_value)
    except Exception as exc:
        raise ValueError(f'{field_name} parameter must be an integer') from exc
    if bins < 1 or bins > MAX_BINS_PER_AXIS:
        raise ValueError(f'{field_name} must be between 1 and {MAX_BINS_PER_AXIS}')
    return bins


def _resolve_pileup_field(session: _DatasetSession, query: Dict[str, Any]) -> str:
    exclude_pileup = query.get('exclude_pileup', True)
    if isinstance(exclude_pileup, str):
        exclude_pileup = exclude_pileup.strip().lower() not in ('0', 'false', 'no', 'off')
    if not exclude_pileup:
        return ''
    for candidate in ('pileup', 'Pileup'):
        if candidate in session.headers:
            return candidate
    return ''


def _iterate_root_chunks(session: _DatasetSession, branches: Sequence[str]):
    uproot = importer._require_uproot()
    with uproot.open(session.source_path) as root_file:
        tree = root_file[session.tree_path]
        for arrays in tree.iterate(branches, library='np', step_size=ROOT_AGGREGATION_CHUNK_SIZE):
            yield arrays


def _coerce_numeric_array(values: Any, feature_name: str) -> np.ndarray:
    arr = np.asarray(values)
    if arr.ndim != 1:
        raise UnsupportedAggregationError(
            f"Feature '{feature_name}' is not a one-dimensional scalar branch supported by the exact backend path"
        )
    if np.issubdtype(arr.dtype, np.number) or arr.dtype == np.bool_:
        return arr.astype(np.float64, copy=False)
    try:
        return arr.astype(np.float64)
    except Exception as exc:
        raise UnsupportedAggregationError(
            f"Feature '{feature_name}' is not numeric and cannot be aggregated exactly by the backend path"
        ) from exc


def _build_selection_mask(
    *,
    x_values: np.ndarray,
    y_values: Optional[np.ndarray],
    filter_values: Optional[np.ndarray],
    channel_values: Sequence[str],
    channel_field_values: Any,
    x_filter_range: tuple[Optional[float], Optional[float]],
    y_filter_range: tuple[Optional[float], Optional[float]],
    filter_range: tuple[Optional[float], Optional[float]],
    pileup_values: Any,
) -> np.ndarray:
    mask = np.isfinite(x_values)
    if x_filter_range[0] is not None:
        mask &= x_values >= x_filter_range[0]
    if x_filter_range[1] is not None:
        mask &= x_values <= x_filter_range[1]

    if y_values is not None:
        mask &= np.isfinite(y_values)
        if y_filter_range[0] is not None:
            mask &= y_values >= y_filter_range[0]
        if y_filter_range[1] is not None:
            mask &= y_values <= y_filter_range[1]

    if filter_values is not None:
        mask &= np.isfinite(filter_values)
        if filter_range[0] is not None:
            mask &= filter_values >= filter_range[0]
        if filter_range[1] is not None:
            mask &= filter_values <= filter_range[1]

    if channel_values:
        if channel_field_values is None:
            raise ValueError('channel_field_values missing while channel_values are active')
        mask &= _match_channel_values(np.asarray(channel_field_values), channel_values)

    if pileup_values is not None:
        mask &= ~_match_truthy_values(np.asarray(pileup_values))

    return mask


def _match_channel_values(raw_values: np.ndarray, selected_values: Sequence[str]) -> np.ndarray:
    if raw_values.ndim != 1:
        raise ValueError('channel field must be one-dimensional')

    clean_selected = [str(value).strip() for value in selected_values if str(value).strip() != '']
    if not clean_selected:
        return np.ones(raw_values.shape[0], dtype=bool)

    if np.issubdtype(raw_values.dtype, np.integer):
        typed: list[int] = []
        for value in clean_selected:
            try:
                typed.append(int(float(value)))
            except Exception:
                continue
        if typed:
            return np.isin(raw_values, np.asarray(typed, dtype=raw_values.dtype))
    elif np.issubdtype(raw_values.dtype, np.floating):
        typed_float: list[float] = []
        for value in clean_selected:
            try:
                typed_float.append(float(value))
            except Exception:
                continue
        if typed_float:
            return np.isin(raw_values, np.asarray(typed_float, dtype=raw_values.dtype))

    string_values = raw_values.astype(str)
    return np.isin(string_values, np.asarray(clean_selected, dtype=string_values.dtype))


def _match_truthy_values(raw_values: np.ndarray) -> np.ndarray:
    if raw_values.ndim != 1:
        raise ValueError('pileup field must be one-dimensional')
    if raw_values.dtype == np.bool_:
        return raw_values.astype(bool, copy=False)
    if np.issubdtype(raw_values.dtype, np.integer) or np.issubdtype(raw_values.dtype, np.floating):
        return np.nan_to_num(raw_values.astype(np.float64, copy=False), nan=0.0) != 0.0

    normalized = np.char.lower(np.char.strip(raw_values.astype(str)))
    return np.isin(normalized, np.asarray(['true', '1', 'yes'], dtype=normalized.dtype))


def _finalize_histogram_range(
    requested_range: tuple[Optional[float], Optional[float]],
    stats: Optional[_RunningStats],
) -> tuple[float, float]:
    if stats is None or stats.count <= 0:
        return 0.0, 1.0

    start = requested_range[0] if requested_range[0] is not None else stats.minimum
    end = requested_range[1] if requested_range[1] is not None else stats.maximum
    if not math.isfinite(start):
        start = 0.0
    if not math.isfinite(end):
        end = 1.0
    if end == start:
        start -= 0.5
        end += 0.5
    return float(start), float(end)
from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


PREVIEW_ROW_LIMIT = 100
ROOT_LOAD_ENTRY_LIMIT = 20_000
ROOT_HISTOGRAM_CLASSES = {
    f'TH{dimension}{storage}'
    for dimension in (1, 2)
    for storage in ('C', 'S', 'I', 'L', 'F', 'D')
}
SUPPORTED_IMPORT_FILE_TYPES: dict[str, tuple[str, ...]] = {
    'all': ('.csv', '.root'),
    'csv': ('.csv',),
    'root': ('.root',),
}
IMPORTER_DEFAULT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RootTreePart:
    path: str
    tree_path: str
    entry_count: int
    branch_count: int


@dataclass(frozen=True)
class RootTreeDataset:
    parts: tuple[RootTreePart, ...]
    title: str
    branches: List[Dict[str, Any]]
    headers: List[str]

    @property
    def entry_count(self) -> int:
        return sum(part.entry_count for part in self.parts)

    @property
    def display_name(self) -> str:
        label = Path(self.parts[0].path).name if len(self.parts) == 1 else f'{len(self.parts)} ROOT files'
        return f'{label} :: {self.parts[0].tree_path}'


def infer_import_kind(path: str) -> str:
    suffix = Path(str(path or '')).suffix.lower()
    if suffix == '.csv':
        return 'csv'
    if suffix == '.root':
        return 'root'
    raise ValueError(f'Unsupported file type: {suffix or "(none)"}')


def pick_importable_file(*, kind: str = 'all', start_path: Optional[str] = None) -> Dict[str, Any]:
    allowed_suffixes = _allowed_suffixes(kind)
    initial_dir = _resolve_dialog_start_dir(start_path)
    selected = _show_native_file_picker(initial_dir, allowed_suffixes=allowed_suffixes, kind=kind)
    if not selected:
        return {
            'kind': kind,
            'cancelled': True,
            'selected_path': None,
            'selected_paths': [],
            'current_path': str(initial_dir),
        }

    paths = [_resolve_file(path, allowed_suffixes=allowed_suffixes) for path in selected]
    paths.sort(key=_natural_file_order)
    resolved_path = paths[0]
    detected_kind = 'root' if len(paths) > 1 else infer_import_kind(resolved_path)
    return {
        'kind': kind,
        'detected_kind': detected_kind,
        'cancelled': False,
        'selected_path': resolved_path,
        'selected_paths': paths,
        'current_path': str(Path(resolved_path).parent),
        'display_name': Path(resolved_path).name,
    }


def _natural_file_order(path: str) -> tuple:
    parts = re.split(r'([0-9]+)', path.casefold())
    return tuple(int(part) if index % 2 else part for index, part in enumerate(parts)), path


def browse_importable_files(path: Optional[str], *, kind: str = 'all') -> Dict[str, Any]:
    current_path = _resolve_directory(path)
    allowed_suffixes = _allowed_suffixes(kind)

    items: List[Dict[str, Any]] = []
    parent_path = current_path.parent
    if parent_path != current_path:
        items.append({
            'name': '..',
            'path': str(parent_path),
            'type': 'parent',
            'is_directory': True,
        })

    for child in sorted(current_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        try:
            if child.is_dir():
                items.append({
                    'name': child.name,
                    'path': str(child),
                    'type': 'directory',
                    'is_directory': True,
                })
            elif child.is_file() and child.suffix.lower() in allowed_suffixes:
                items.append({
                    'name': child.name,
                    'path': str(child),
                    'type': 'file',
                    'is_directory': False,
                    'file_kind': child.suffix.lower().lstrip('.'),
                })
        except PermissionError:
            continue

    return {
        'current_path': str(current_path),
        'items': items,
        'kind': kind,
    }


def preview_csv_file(path: str, *, sample_rows: int = PREVIEW_ROW_LIMIT, source_label: Optional[str] = None) -> Dict[str, Any]:
    file_path = _resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['csv'])
    headers, sample, total_rows = _read_csv_rows(file_path, limit=sample_rows)
    display_name = Path(file_path).name
    return {
        'kind': 'csv',
        'supported': bool(headers),
        'path': file_path,
        'display_name': display_name,
        'source_label': source_label or display_name,
        'headers': headers,
        'sample_rows': sample,
        'sample_row_limit': sample_rows,
        'sample_row_count': len(sample),
        'total_rows': total_rows,
        'column_count': len(headers),
    }


def load_csv_file(path: str, *, source_label: Optional[str] = None, source_kind: str = 'csv') -> Dict[str, Any]:
    file_path = _resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['csv'])
    headers, rows, total_rows = _read_csv_rows(file_path, limit=None)
    display_name = Path(file_path).name
    return {
        'kind': source_kind,
        'name': display_name,
        'source_label': source_label or display_name,
        'source_path': file_path,
        'headers': headers,
        'rows': rows,
        'meta': {
            'row_count': total_rows,
            'column_count': len(headers),
        },
    }


def preview_root_file(
    path: str,
    *,
    tree_path: Optional[str] = None,
    histogram_path: Optional[str] = None,
    sample_rows: int = PREVIEW_ROW_LIMIT,
) -> Dict[str, Any]:
    file_path = _resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['root'])
    uproot = _require_uproot()

    with uproot.open(file_path) as root_file:
        objects = _root_objects(root_file)
        tree_options = _root_tree_options(root_file, objects)
        histogram_options = [
            {
                'path': item['path'],
                'class_name': item['class_name'],
                'supported': item['class_name'] in ROOT_HISTOGRAM_CLASSES,
            }
            for item in objects if item['kind'] == 'histogram'
        ]
        payload = {
            'kind': 'root',
            'path': file_path,
            'display_name': Path(file_path).name,
            'objects': objects,
            'tree_options': tree_options,
            'histogram_options': histogram_options,
        }
        if tree_path and histogram_path:
            raise ValueError('Select either a ROOT histogram or a TTree, not both')
        if histogram_path or not tree_options:
            supported_histograms = [item for item in histogram_options if item['supported']]
            if histogram_path or supported_histograms:
                selected_path = histogram_path or supported_histograms[0]['path']
                histogram = _require_root_histogram(root_file, selected_path)
                values, axes = _read_root_histogram_bins(histogram)
                selected = _describe_root_histogram(histogram, selected_path, values, axes)
                indices = np.argwhere(values != 0)[:sample_rows]
                selected['sample_bins'] = [
                    {
                        **{
                            f'{name}_center': float((axes[axis]['edges'][index[axis]] + axes[axis]['edges'][index[axis] + 1]) / 2)
                            for axis, name in enumerate(('x', 'y')[:values.ndim])
                        },
                        'content': float(values[tuple(index)]),
                    }
                    for index in indices
                ]
                return {
                    **payload,
                    'supported': True,
                    'selected_histogram': selected,
                    'message': 'Saved histogram: native bins are loaded directly, without reconstructing events.',
                }

        if not tree_options:
            return {
                **payload,
                'supported': False,
                'message': 'No supported TH1/TH2 histograms or TTrees were found. TH3, TH2Poly and TProfile objects are not supported.',
            }

        valid_tree_paths = {option['path'] for option in tree_options}
        selected_tree_path = tree_path if tree_path in valid_tree_paths else tree_options[0]['path']
        tree = root_file[selected_tree_path]

        branch_info, loadable_headers = _describe_root_tree(tree)
        sample_arrays = tree.arrays(loadable_headers, library='np', entry_stop=sample_rows) if loadable_headers else {}
        total_entries = int(tree.num_entries)
        sample_count = min(sample_rows, total_entries)
        sample = _arrays_to_rows(sample_arrays, loadable_headers, sample_count)
        return {
            **payload,
            'supported': bool(loadable_headers),
            'selected_tree': _root_tree_preview(
                selected_tree_path, tree.title, total_entries, branch_info, loadable_headers, sample, sample_rows,
            ),
            'message': None if loadable_headers else 'The selected TTree does not expose scalar branches that can be previewed yet.',
        }


def _require_root_histogram(root_file, histogram_path: str):
    normalized_path = str(histogram_path or '').strip()
    if not normalized_path:
        raise ValueError('histogram_path parameter required')
    class_name = root_file.classnames(recursive=True, cycle=False).get(normalized_path)
    if class_name is None:
        raise ValueError(f'ROOT histogram not found: {normalized_path}')
    if class_name not in ROOT_HISTOGRAM_CLASSES:
        raise ValueError(f'Unsupported ROOT histogram class {class_name}: select a TH1 or TH2 histogram')
    return root_file[normalized_path]


def _read_root_histogram_bins(histogram) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    values = np.asarray(histogram.values(flow=False), dtype=np.float64)
    if values.ndim not in (1, 2) or not values.size or not np.all(np.isfinite(values)):
        raise ValueError('ROOT histogram must contain finite 1D or 2D bin contents')
    axes = []
    for index, axis in enumerate(histogram.axes):
        edges = np.asarray(axis.edges(), dtype=np.float64)
        if len(edges) != values.shape[index] + 1 or not np.all(np.isfinite(edges)) or not np.all(np.diff(edges) > 0):
            raise ValueError('ROOT histogram bin edges must be finite and strictly increasing')
        labels = axis.labels()
        axes.append({
            'title': str(axis.member('fTitle') or ''),
            'edges': edges.tolist(),
            'bin_count': values.shape[index],
            'min': float(edges[0]),
            'max': float(edges[-1]),
            'labels': [str(label) for label in labels] if labels is not None else None,
        })
    return values, axes


def _describe_root_histogram(histogram, path: str, values: np.ndarray, axes: List[Dict[str, Any]]) -> Dict[str, Any]:
    flow_values = np.asarray(histogram.values(flow=True), dtype=np.float64)
    entries = float(histogram.member('fEntries'))
    if not np.all(np.isfinite(flow_values)) or not math.isfinite(entries):
        raise ValueError('ROOT histogram entries and flow bins must be finite')
    integral = float(values.sum())
    flow_integral = float(flow_values.sum() - integral)
    if not math.isfinite(integral) or not math.isfinite(flow_integral):
        raise ValueError('ROOT histogram regular and flow bin sums must be finite')
    return {
        'path': path,
        'class_name': histogram.classname,
        'title': str(histogram.title or ''),
        'dimension': values.ndim,
        'axes': axes,
        'bin_count': int(values.size),
        'nonzero_bin_count': int(np.count_nonzero(values)),
        'entry_count': entries,
        'integral': integral,
        'flow_integral': flow_integral,
        'has_negative_bins': bool(np.any(values < 0)),
        'min_bin_content': float(values.min()),
        'max_bin_content': float(values.max()),
    }


def _root_histogram_axis_stats(values: np.ndarray, edges: Sequence[float]) -> Optional[Dict[str, Any]]:
    total = float(values.sum())
    if total <= 0 or np.any(values < 0):
        return None
    centers = (np.asarray(edges[:-1]) + np.asarray(edges[1:])) / 2
    occupied = values != 0
    mean = float(np.dot(values, centers) / total)
    mean_square = float(np.dot(values, centers * centers) / total)
    return {
        'count': total,
        'min': float(centers[occupied][0]),
        'max': float(centers[occupied][-1]),
        'mean': mean,
        'median': None,
        'std_dev': math.sqrt(max(0, float(np.dot(values, (centers - mean) ** 2) / total))),
        'rms': math.sqrt(max(0, mean_square)),
    }


def load_root_histogram(path: str, *, histogram_path: str) -> Dict[str, Any]:
    file_path = _resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['root'])
    uproot = _require_uproot()
    with uproot.open(file_path) as root_file:
        histogram = _require_root_histogram(root_file, histogram_path)
        values, axes = _read_root_histogram_bins(histogram)
        metadata = _describe_root_histogram(histogram, histogram_path, values, axes)
        sumw2_size = len(histogram.member('fSumw2'))
        if sumw2_size and sumw2_size != math.prod(axis['bin_count'] + 2 for axis in axes):
            raise ValueError('ROOT histogram stored variances must include one value per regular and flow bin')
        if sumw2_size:
            variances = histogram.variances(flow=False)
            metadata['variance_source'] = 'stored_sumw2'
        elif metadata['has_negative_bins']:
            variances = None
            metadata['variance_source'] = 'unavailable'
        else:
            variances = values.copy()
            metadata['variance_source'] = 'poisson_default'
        if variances is not None:
            variances = np.asarray(variances, dtype=np.float64)
            if variances.shape != values.shape or not np.all(np.isfinite(variances)) or np.any(variances < 0):
                raise ValueError('ROOT histogram variances must be finite, nonnegative and match its bins')

    if values.ndim == 1:
        bins = {
            'counts': values.tolist(),
            'edges': axes[0]['edges'],
            'centers': ((np.asarray(axes[0]['edges'][:-1]) + np.asarray(axes[0]['edges'][1:])) / 2).tolist(),
            'variances': variances.tolist() if variances is not None else None,
        }
        stats = _root_histogram_axis_stats(values, axes[0]['edges'])
    else:
        bins = {
            'counts': values.T.tolist(),
            'x_edges': axes[0]['edges'],
            'y_edges': axes[1]['edges'],
            'x_centers': ((np.asarray(axes[0]['edges'][:-1]) + np.asarray(axes[0]['edges'][1:])) / 2).tolist(),
            'y_centers': ((np.asarray(axes[1]['edges'][:-1]) + np.asarray(axes[1]['edges'][1:])) / 2).tolist(),
            'variances': variances.T.tolist() if variances is not None else None,
        }
        stats = {
            'x': _root_histogram_axis_stats(values.sum(axis=1), axes[0]['edges']),
            'y': _root_histogram_axis_stats(values.sum(axis=0), axes[1]['edges']),
        } if not metadata['has_negative_bins'] else None
    display_name = f'{Path(file_path).name} :: {histogram_path}'
    notes = [
        f"ROOT entries: {metadata['entry_count']:g}; in-range bin sum: {metadata['integral']:g}; flow-bin sum: {metadata['flow_integral']:g}.",
        'Native bin edges and contents are preserved; underflow/overflow bins are not plotted.',
        'Statistics use weighted bin centers, not original events. Event filters are unavailable; rebinning merges existing whole bins.',
    ]
    if metadata['variance_source'] == 'poisson_default':
        notes.append('No Sumw2 array is stored. Bin variances use the default Poisson assumption (variance = bin content), not measured weighted uncertainties.')
    elif metadata['variance_source'] == 'unavailable':
        notes.append('No Sumw2 array is stored. Variances are unavailable for signed bins; no uncertainty model is invented.')
    else:
        notes.append('Stored Sumw2 bin variances are retained; inter-bin covariance is not available.')
    if metadata['has_negative_bins']:
        notes.append('Signed bin contents are preserved; event-like statistics are unavailable for negative weights.')
    return {
        'kind': 'root',
        'name': display_name,
        'source_label': display_name,
        'source_path': file_path,
        'headers': ['X', 'Y'][:values.ndim],
        'rows': [],
        'histogram': bins,
        'stats': stats,
        'notes': notes,
        'meta': {**metadata, 'root_object_kind': 'histogram', 'histogram_path': histogram_path, 'prebinned': True},
    }


def describe_root_tree_dataset(paths: Sequence[str], *, tree_path: str) -> RootTreeDataset:
    if not tree_path:
        raise ValueError('tree_path parameter required')

    if len(paths) > 1:
        return _describe_root_tree_parts(_match_root_tree_parts(paths, tree_path)[tree_path])

    path = _resolve_file(paths[0], allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['root'])
    uproot = _require_uproot()
    with uproot.open(path) as root_file:
        try:
            tree = root_file[tree_path]
        except KeyError as exc:
            raise ValueError(f'TTree not found: {tree_path}') from exc
        if getattr(tree, 'classname', None) != 'TTree':
            raise ValueError(f'ROOT object is not a TTree: {tree_path}')
        branches, headers = _describe_root_tree(tree)
        part = RootTreePart(path, tree_path, int(tree.num_entries), len(branches))
        return RootTreeDataset((part,), tree.title, branches, headers)


def _match_root_tree_parts(
    paths: Sequence[str], selected_tree: Optional[str],
) -> Dict[str, tuple[RootTreePart, ...]]:
    prefix = 'Cannot load these ROOT files together.'
    try:
        resolved = [_resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['root']) for path in paths]
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f'{prefix} {exc}') from exc
    if len(set(resolved)) != len(resolved):
        raise ValueError(f'{prefix} Select each file only once.')

    uproot = _require_uproot()
    files = []
    for path in resolved:
        try:
            with uproot.open(path) as root_file:
                trees = {}
                for item in _root_objects(root_file):
                    if item['kind'] == 'tree':
                        name = item['path']
                        tree = root_file[name]
                        schema = tree.typenames()
                        trees[name] = RootTreePart(path, name, int(tree.num_entries), len(schema)), schema
        except (OSError, ValueError, KeyError) as exc:
            raise ValueError(f"{prefix} Could not read TTrees from '{Path(path).name}'. {exc}") from exc
        if not trees:
            raise ValueError(f"{prefix} '{Path(path).name}' contains no TTrees.")
        files.append(trees)

    first = files[0]
    if selected_tree is not None and selected_tree not in first:
        raise ValueError(f"{prefix} '{Path(resolved[0]).name}' has no TTree named '{selected_tree}'.")

    compatible = {}
    errors = {}
    for name, (first_part, schema) in first.items():
        parts = [first_part]
        for path, trees in zip(resolved[1:], files[1:]):
            if name in trees:
                other_part, other_schema = trees[name]
                if schema != other_schema:
                    difference = _root_branch_difference(schema, other_schema)
                    errors[name] = (
                        f"'{Path(path).name}' and '{Path(first_part.path).name}' "
                        f"have different branches in TTree '{name}' ({difference})."
                    )
                    break
            else:
                matches = [part for part, candidate in trees.values() if candidate == schema]
                if len(matches) != 1:
                    detail = 'has no TTree' if not matches else 'has more than one TTree'
                    errors[name] = (
                        f"'{Path(path).name}' {detail} matching the branches of "
                        f"'{name}' in '{Path(first_part.path).name}'."
                    )
                    break
                other_part = matches[0]
            parts.append(other_part)
        else:
            compatible[name] = tuple(parts)

    if selected_tree in errors:
        raise ValueError(f'{prefix} {errors[selected_tree]}')
    if not compatible:
        raise ValueError(f'{prefix} {next(iter(errors.values()))}')
    return compatible


def _root_branch_difference(expected: Dict[str, str], actual: Dict[str, str]) -> str:
    differences = (
        ('missing', expected.keys() - actual.keys()),
        ('extra', actual.keys() - expected.keys()),
        ('different type', {name for name in expected.keys() & actual.keys() if expected[name] != actual[name]}),
    )
    return ', '.join(
        f'{label} {", ".join(sorted(names)[:5])}{"…" if len(names) > 5 else ""}'
        for label, names in differences if names
    )


def _describe_root_tree_parts(parts: tuple[RootTreePart, ...]) -> RootTreeDataset:
    example = next((part for part in parts if part.entry_count), parts[0])
    uproot = _require_uproot()
    with uproot.open(example.path) as root_file:
        tree = root_file[example.tree_path]
        branches, headers = _describe_root_tree(tree)
        return RootTreeDataset(parts, tree.title, branches, headers)


def _read_root_tree_rows(dataset: RootTreeDataset, limit: int) -> List[Dict[str, Any]]:
    rows = []
    uproot = _require_uproot()
    for part in dataset.parts:
        row_count = min(part.entry_count, limit - len(rows))
        if row_count <= 0:
            continue
        with uproot.open(part.path) as root_file:
            arrays = root_file[part.tree_path].arrays(dataset.headers, library='np', entry_stop=row_count)
            rows.extend(_arrays_to_rows(arrays, dataset.headers, row_count))
        if len(rows) >= limit:
            break
    return rows


def _root_tree_preview(path, title, entry_count, branches, headers, rows, sample_limit):
    return {
        'path': path,
        'title': title,
        'entry_count': entry_count,
        'branch_count': len(branches),
        'loadable_branch_count': len(headers),
        'headers': headers,
        'branches': branches,
        'sample_rows': rows,
        'sample_row_limit': sample_limit,
        'sample_row_count': len(rows),
        'load_entry_limit': ROOT_LOAD_ENTRY_LIMIT,
        'load_entry_count': min(entry_count, ROOT_LOAD_ENTRY_LIMIT) if headers else 0,
        'will_truncate_on_load': entry_count > ROOT_LOAD_ENTRY_LIMIT,
    }


def preview_root_files(paths: Sequence[str], *, tree_path: Optional[str] = None) -> Dict[str, Any]:
    options = _match_root_tree_parts(paths, tree_path)
    selected_path = tree_path or next(iter(options))
    dataset = _describe_root_tree_parts(options[selected_path])
    sample = _read_root_tree_rows(dataset, PREVIEW_ROW_LIMIT) if dataset.headers else []
    selected = _root_tree_preview(
        selected_path, dataset.title, dataset.entry_count, dataset.branches, dataset.headers, sample, PREVIEW_ROW_LIMIT,
    )
    selected['parts'] = [
        {'path': part.path, 'tree_path': part.tree_path, 'entry_count': part.entry_count}
        for part in dataset.parts
    ]
    return {
        'kind': 'root',
        'display_name': dataset.display_name,
        'path': dataset.parts[0].path,
        'source_paths': [part.path for part in dataset.parts],
        'tree_options': [
            {'path': name, 'entry_count': sum(part.entry_count for part in parts), 'branch_count': parts[0].branch_count}
            for name, parts in options.items()
        ],
        'supported': bool(dataset.headers),
        'selected_tree': selected,
        'message': None if dataset.headers else 'The selected TTrees do not expose scalar branches that can be previewed.',
    }


def load_root_tree(dataset: RootTreeDataset) -> Dict[str, Any]:
    if not dataset.headers:
        raise ValueError('Selected TTree does not expose scalar branches that can be loaded')
    total_entries = dataset.entry_count
    rows = _read_root_tree_rows(dataset, ROOT_LOAD_ENTRY_LIMIT)
    loaded_entry_count = len(rows)
    display_name = dataset.display_name
    truncated = total_entries > ROOT_LOAD_ENTRY_LIMIT
    source_label = display_name if not truncated else f"{display_name} (first {loaded_entry_count:,} / {total_entries:,} entries)"
    return {
        'kind': 'root',
        'name': display_name,
        'source_label': source_label,
        'source_path': dataset.parts[0].path,
        'source_paths': [part.path for part in dataset.parts],
        'headers': dataset.headers,
        'rows': rows,
        'meta': {
            'tree_path': dataset.parts[0].tree_path,
            'entry_count': total_entries,
            'loaded_entry_count': loaded_entry_count,
            'truncated': truncated,
            'load_entry_limit': ROOT_LOAD_ENTRY_LIMIT,
            'branch_count': len(dataset.branches),
            'loadable_branch_count': len(dataset.headers),
        },
    }


def _allowed_suffixes(kind: str) -> tuple[str, ...]:
    normalized = (kind or 'all').strip().lower()
    return SUPPORTED_IMPORT_FILE_TYPES.get(normalized, SUPPORTED_IMPORT_FILE_TYPES['all'])


def _resolve_dialog_start_dir(path: Optional[str]) -> Path:
    candidate = Path(path).expanduser() if path else IMPORTER_DEFAULT_ROOT
    candidate = candidate if candidate.is_absolute() else candidate.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    return IMPORTER_DEFAULT_ROOT.resolve()


def _show_native_file_picker(initial_dir: Path, *, allowed_suffixes: Sequence[str], kind: str) -> List[str]:
    if sys.platform == 'darwin':
        return _show_native_file_picker_macos(initial_dir, kind=kind)

    try:
        from tkinter import Tk, filedialog  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError('Native file picker is unavailable in this runtime.') from exc

    root = Tk()
    root.withdraw()
    try:
        root.attributes('-topmost', True)
    except Exception:
        pass

    try:
        selected = filedialog.askopenfilenames(
            title=_dialog_title(kind),
            initialdir=str(initial_dir),
            filetypes=_dialog_file_types(allowed_suffixes),
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    return list(selected)


def _show_native_file_picker_macos(initial_dir: Path, *, kind: str) -> List[str]:
    script_lines = [
        'use framework "Foundation"',
        'use scripting additions',
        f'set defaultLocation to POSIX file "{_escape_applescript_string(str(initial_dir))}"',
        'try',
        f'    set selectedFiles to choose file with prompt "{_escape_applescript_string(_dialog_title(kind))}" default location defaultLocation with multiple selections allowed',
        '    set selectedPaths to {}',
        '    repeat with selectedFile in selectedFiles',
        '        set end of selectedPaths to POSIX path of (contents of selectedFile)',
        '    end repeat',
        "    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:selectedPaths options:0 |error|:(missing value)",
        "    return (current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)) as text",
        'on error number -128',
        '    return "[]"',
        'end try',
    ]
    command: List[str] = ['osascript']
    for line in script_lines:
        command.extend(['-e', line])

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError('Native file picker is unavailable in this runtime.') from exc

    selected = (result.stdout or '').strip()
    if result.returncode == 0:
        return json.loads(selected)

    details = (result.stderr or selected or '').strip()
    if '-128' in details or 'User canceled' in details:
        return []
    raise RuntimeError(details or 'Native file picker failed.')


def _escape_applescript_string(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


def _dialog_title(kind: str) -> str:
    normalized = (kind or 'all').strip().lower()
    if normalized == 'csv':
        return 'Select CSV data file'
    if normalized == 'root':
        return 'Select ROOT files'
    return 'Select a CSV or Shift-click to select split ROOT files'


def _dialog_file_types(allowed_suffixes: Sequence[str]) -> List[Tuple[str, str]]:
    suffixes = tuple(dict.fromkeys(str(suffix).lower() for suffix in allowed_suffixes if suffix))
    file_types: List[Tuple[str, str]] = []
    if len(suffixes) > 1:
        file_types.append(('Supported data files', ' '.join(f'*{suffix}' for suffix in suffixes)))
    for suffix in suffixes:
        if suffix == '.csv':
            file_types.append(('CSV files', '*.csv'))
        elif suffix == '.root':
            file_types.append(('ROOT files', '*.root'))
        else:
            file_types.append((f'{suffix.upper()} files', f'*{suffix}'))
    file_types.append(('All files', '*'))
    return file_types


def _resolve_directory(path: Optional[str]) -> Path:
    current = Path(path).expanduser() if path else Path.home()
    current = current if current.is_absolute() else current.resolve()
    if current.is_file():
        current = current.parent
    if not current.exists() or not current.is_dir():
        raise FileNotFoundError(f'Directory does not exist: {current}')
    return current.resolve()


def _resolve_file(path: str, *, allowed_suffixes: Sequence[str]) -> str:
    if not path or str(path).strip() == '':
        raise ValueError('path parameter required')
    file_path = Path(path).expanduser()
    file_path = file_path if file_path.is_absolute() else file_path.resolve()
    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f'File does not exist: {file_path}')
    if file_path.suffix.lower() not in allowed_suffixes:
        raise ValueError(f'Unsupported file type: {file_path.suffix}')
    return str(file_path.resolve())


def _read_csv_rows(path: str, *, limit: Optional[int]) -> Tuple[List[str], List[Dict[str, str]], int]:
    headers: List[str] = []
    rows: List[Dict[str, str]] = []
    total_rows = 0

    with open(path, 'r', encoding='utf-8', errors='ignore', newline='') as handle:
        reader = csv.reader(handle)
        raw_headers = next(reader, None)
        if not raw_headers:
            return headers, rows, total_rows
        headers = _normalize_headers(raw_headers)

        for raw_row in reader:
            if not _row_has_content(raw_row):
                continue
            row = _csv_row_to_dict(headers, raw_row)
            total_rows += 1
            if limit is None or len(rows) < limit:
                rows.append(row)

    return headers, rows, total_rows


def _normalize_headers(raw_headers: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: dict[str, int] = {}
    for index, header in enumerate(raw_headers):
        base = str(header).strip() or f'column_{index + 1}'
        count = seen.get(base, 0) + 1
        seen[base] = count
        out.append(base if count == 1 else f'{base}_{count}')
    return out


def _row_has_content(values: Sequence[Any]) -> bool:
    return any(str(value).strip() != '' for value in values)


def _csv_row_to_dict(headers: Sequence[str], raw_row: Sequence[Any]) -> Dict[str, str]:
    row: Dict[str, str] = {}
    for index, header in enumerate(headers):
        value = raw_row[index] if index < len(raw_row) else ''
        row[header] = str(value)
    return row


def _require_uproot():
    try:
        import uproot  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("ROOT support requires the 'uproot' package. Install project runtime dependencies first.") from exc
    return uproot


def _root_objects(root_file) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    classnames = root_file.classnames(recursive=True, cycle=False)
    for raw_path, class_name in sorted(classnames.items(), key=lambda item: item[0].lower()):
        path = _strip_root_cycle(raw_path)
        kind = (
            'tree' if class_name == 'TTree'
            else 'histogram' if class_name.startswith(('TH1', 'TH2', 'TH3', 'TProfile'))
            else 'directory' if 'Directory' in class_name
            else 'object'
        )
        out.append({
            'path': path,
            'class_name': class_name,
            'kind': kind,
        })
    return out


def _root_tree_options(root_file, objects: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in objects:
        if item.get('kind') != 'tree':
            continue
        tree = root_file[item['path']]
        out.append({
            'path': item['path'],
            'title': getattr(tree, 'title', '') or '',
            'entry_count': int(tree.num_entries),
            'branch_count': len(tree.keys()),
        })
    return out


def _describe_root_tree(tree) -> Tuple[List[Dict[str, Any]], List[str]]:
    branch_info: List[Dict[str, Any]] = []
    loadable_headers: List[str] = []

    for branch_name in tree.keys():
        branch = tree[branch_name]
        loadable = _root_branch_is_scalar(branch)
        if loadable:
            loadable_headers.append(branch_name)
        branch_info.append({
            'name': branch_name,
            'title': getattr(branch, 'title', '') or '',
            'interpretation': str(getattr(branch, 'interpretation', '')),
            'loadable': loadable,
        })

    return branch_info, loadable_headers


def _root_branch_is_scalar(branch) -> bool:
    try:
        sample = branch.array(entry_stop=3, library='np')
    except Exception:
        return False

    if getattr(sample, 'ndim', None) != 1:
        return False

    dtype = getattr(sample, 'dtype', None)
    if dtype is None:
        return False
    if dtype != object:
        return True

    for value in sample[: min(3, len(sample))]:
        normalized = _normalize_root_cell(value)
        if isinstance(normalized, (list, dict, tuple)):
            return False
    return True


def _arrays_to_rows(arrays: Any, headers: Sequence[str], row_count: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if row_count <= 0 or not headers:
        return rows

    for row_index in range(row_count):
        row: Dict[str, Any] = {}
        for header in headers:
            column = arrays[header]
            row[header] = _normalize_root_cell(column[row_index])
        rows.append(row)
    return rows


def _normalize_root_cell(value: Any) -> Any:
    if hasattr(value, 'item'):
        try:
            value = value.item()
        except Exception:
            pass

    if isinstance(value, bytes):
        return value.decode('utf-8', errors='ignore')
    if isinstance(value, (str, bool, int, float)) or value is None:
        return _sanitize_json_value(value)
    if hasattr(value, 'tolist'):
        try:
            return _sanitize_json_value(value.tolist())
        except Exception:
            pass
    return _sanitize_json_value(str(value))


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(item) for key, item in value.items()}
    return value


def _strip_root_cycle(path: str) -> str:
    return str(path).split(';', 1)[0]

from __future__ import annotations

import csv
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PREVIEW_ROW_LIMIT = 100
ROOT_LOAD_ENTRY_LIMIT = 20_000
SUPPORTED_IMPORT_FILE_TYPES: dict[str, tuple[str, ...]] = {
    'all': ('.csv', '.root'),
    'csv': ('.csv',),
    'root': ('.root',),
}
IMPORTER_DEFAULT_ROOT = Path(__file__).resolve().parent.parent


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
    selected_path = _show_native_file_picker(initial_dir, allowed_suffixes=allowed_suffixes, kind=kind)
    if not selected_path:
        return {
            'kind': kind,
            'cancelled': True,
            'selected_path': None,
            'current_path': str(initial_dir),
        }

    resolved_path = _resolve_file(selected_path, allowed_suffixes=allowed_suffixes)
    detected_kind = infer_import_kind(resolved_path)
    return {
        'kind': kind,
        'detected_kind': detected_kind,
        'cancelled': False,
        'selected_path': resolved_path,
        'current_path': str(Path(resolved_path).parent),
        'display_name': Path(resolved_path).name,
    }


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


def preview_root_file(path: str, *, tree_path: Optional[str] = None, sample_rows: int = PREVIEW_ROW_LIMIT) -> Dict[str, Any]:
    file_path = _resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['root'])
    uproot = _require_uproot()

    with uproot.open(file_path) as root_file:
        objects = _root_objects(root_file)
        tree_options = _root_tree_options(root_file, objects)

        if not tree_options:
            return {
                'kind': 'root',
                'supported': False,
                'path': file_path,
                'display_name': Path(file_path).name,
                'objects': objects,
                'tree_options': [],
                'message': 'No TTree objects were found in this ROOT file.',
            }

        valid_tree_paths = {option['path'] for option in tree_options}
        selected_tree_path = tree_path if tree_path in valid_tree_paths else tree_options[0]['path']
        tree = root_file[selected_tree_path]

        branch_info, loadable_headers = _describe_root_tree(tree)
        sample_arrays = tree.arrays(loadable_headers, library='np', entry_stop=sample_rows) if loadable_headers else {}
        total_entries = int(tree.num_entries)
        sample_count = min(sample_rows, total_entries)
        sample = _arrays_to_rows(sample_arrays, loadable_headers, sample_count)
        load_count = min(total_entries, ROOT_LOAD_ENTRY_LIMIT) if loadable_headers else 0

        return {
            'kind': 'root',
            'supported': bool(loadable_headers),
            'path': file_path,
            'display_name': Path(file_path).name,
            'objects': objects,
            'tree_options': tree_options,
            'selected_tree': {
                'path': selected_tree_path,
                'title': getattr(tree, 'title', '') or '',
                'entry_count': total_entries,
                'branch_count': len(branch_info),
                'loadable_branch_count': len(loadable_headers),
                'headers': loadable_headers,
                'branches': branch_info,
                'sample_rows': sample,
                'sample_row_limit': sample_rows,
                'sample_row_count': len(sample),
                'load_entry_limit': ROOT_LOAD_ENTRY_LIMIT,
                'load_entry_count': load_count,
                'will_truncate_on_load': total_entries > ROOT_LOAD_ENTRY_LIMIT,
            },
            'message': None if loadable_headers else 'The selected TTree does not expose scalar branches that can be previewed yet.',
        }


def load_root_tree(path: str, *, tree_path: str) -> Dict[str, Any]:
    file_path = _resolve_file(path, allowed_suffixes=SUPPORTED_IMPORT_FILE_TYPES['root'])
    if not tree_path or str(tree_path).strip() == '':
        raise ValueError('tree_path parameter required')

    uproot = _require_uproot()
    with uproot.open(file_path) as root_file:
        tree = root_file[str(tree_path)]
        branch_info, loadable_headers = _describe_root_tree(tree)
        if not loadable_headers:
            raise ValueError('Selected TTree does not expose scalar branches that can be loaded')
        total_entries = int(tree.num_entries)
        loaded_entry_count = min(total_entries, ROOT_LOAD_ENTRY_LIMIT)
        arrays = tree.arrays(loadable_headers, library='np', entry_stop=loaded_entry_count)
        rows = _arrays_to_rows(arrays, loadable_headers, loaded_entry_count)

    display_name = f"{Path(file_path).name} :: {tree_path}"
    truncated = total_entries > ROOT_LOAD_ENTRY_LIMIT
    source_label = display_name if not truncated else f"{display_name} (first {loaded_entry_count:,} / {total_entries:,} entries)"
    return {
        'kind': 'root',
        'name': display_name,
        'source_label': source_label,
        'source_path': file_path,
        'headers': loadable_headers,
        'rows': rows,
        'meta': {
            'tree_path': str(tree_path),
            'entry_count': total_entries,
            'loaded_entry_count': loaded_entry_count,
            'truncated': truncated,
            'load_entry_limit': ROOT_LOAD_ENTRY_LIMIT,
            'branch_count': len(branch_info),
            'loadable_branch_count': len(loadable_headers),
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


def _show_native_file_picker(initial_dir: Path, *, allowed_suffixes: Sequence[str], kind: str) -> Optional[str]:
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
        selected = filedialog.askopenfilename(
            title=_dialog_title(kind),
            initialdir=str(initial_dir),
            filetypes=_dialog_file_types(allowed_suffixes),
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    normalized = str(selected).strip()
    return normalized or None


def _show_native_file_picker_macos(initial_dir: Path, *, kind: str) -> Optional[str]:
    script_lines = [
        f'set defaultLocation to POSIX file "{_escape_applescript_string(str(initial_dir))}"',
        'try',
        f'    set selectedFile to choose file with prompt "{_escape_applescript_string(_dialog_title(kind))}" default location defaultLocation',
        '    return POSIX path of selectedFile',
        'on error number -128',
        '    return ""',
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
        return selected or None

    details = (result.stderr or selected or '').strip()
    if '-128' in details or 'User canceled' in details:
        return None
    raise RuntimeError(details or 'Native file picker failed.')


def _escape_applescript_string(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


def _dialog_title(kind: str) -> str:
    normalized = (kind or 'all').strip().lower()
    if normalized == 'csv':
        return 'Select CSV data file'
    if normalized == 'root':
        return 'Select ROOT data file'
    return 'Select data file'


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
    classnames = root_file.classnames(recursive=True)
    for raw_path, class_name in sorted(classnames.items(), key=lambda item: item[0].lower()):
        path = _strip_root_cycle(raw_path)
        kind = 'tree' if class_name == 'TTree' else 'directory' if 'Directory' in class_name else 'object'
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
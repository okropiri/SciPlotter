from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = 'SciPlotter'


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def bundle_root() -> Path:
    if is_frozen() and hasattr(sys, '_MEIPASS'):
        return Path(getattr(sys, '_MEIPASS')).resolve()
    return Path(__file__).resolve().parent.parent


def project_root() -> Path:
    return bundle_root()


def resource_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def static_dir() -> Path:
    return resource_path('static')


def assets_dir() -> Path:
    return resource_path('assets')


def user_cache_dir() -> Path:
    if sys.platform == 'win32':
        root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
        return root / APP_NAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches' / APP_NAME
    root = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))
    return root / APP_NAME.lower()


def user_data_dir() -> Path:
    if sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming'))
        return root / APP_NAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support' / APP_NAME
    root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return root / APP_NAME.lower()


def request_log_path() -> Path:
    return user_cache_dir() / 'request.log'


def ensure_runtime_dirs() -> None:
    user_cache_dir().mkdir(parents=True, exist_ok=True)
    user_data_dir().mkdir(parents=True, exist_ok=True)


def default_waveform_base_dir() -> str:
    env_value = os.environ.get('WAVEFORM_BASE_DIR')
    if env_value:
        return str(Path(env_value).expanduser().resolve())

    bundled_waveforms = project_root() / 'waveforms'
    if bundled_waveforms.is_dir():
        return str(bundled_waveforms.resolve())

    return str((user_data_dir() / 'waveforms').resolve())
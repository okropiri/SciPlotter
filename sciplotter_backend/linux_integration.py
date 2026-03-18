from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import runtime


DESKTOP_FILENAME = 'sciplotter.desktop'
ICON_FILENAME = 'sciplotter.png'


def _linux_data_home() -> Path:
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')).expanduser().resolve()


def _appimage_path() -> Path | None:
    value = os.environ.get('APPIMAGE')
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    return path if path.exists() else None


def _desktop_entry_contents(exec_path: Path, icon_path: Path) -> str:
    return '\n'.join([
        '[Desktop Entry]',
        'Version=1.0',
        'Type=Application',
        'Name=SciPlotter',
        'Comment=Launch SciPlotter',
        f'Exec={exec_path}',
        f'TryExec={exec_path}',
        f'Icon={icon_path}',
        'Terminal=false',
        'StartupNotify=true',
        'Categories=Science;',
        '',
    ])


def integrate_appimage() -> bool:
    if os.name != 'posix' or not runtime.is_frozen():
        return False
    if os.environ.get('APPDIR') is None and os.environ.get('APPIMAGE') is None:
        return False

    appimage_path = _appimage_path()
    if not appimage_path:
        return False

    data_home = _linux_data_home()
    applications_dir = data_home / 'applications'
    icons_dir = data_home / 'icons' / 'hicolor' / '512x512' / 'apps'
    applications_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    source_icon = runtime.assets_dir() / 'launcher-icon.png'
    icon_dest = icons_dir / ICON_FILENAME
    if source_icon.exists():
        shutil.copy2(source_icon, icon_dest)

    desktop_file = applications_dir / DESKTOP_FILENAME
    desktop_file.write_text(_desktop_entry_contents(appimage_path, icon_dest), encoding='utf-8')

    for command in (
        ['update-desktop-database', str(applications_dir)],
        ['gtk-update-icon-cache', str(data_home / 'icons' / 'hicolor')],
    ):
        try:
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    return True
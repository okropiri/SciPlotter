#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path


APP_NAME = 'SciPlotter'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TOOL_URL = 'https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage'


def ensure_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def download_appimagetool(tool_path: Path, url: str = DEFAULT_TOOL_URL) -> Path:
    if tool_path.exists():
        ensure_executable(tool_path)
        return tool_path
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    print(f'Downloading appimagetool to {tool_path}')
    with urllib.request.urlopen(url) as response, tool_path.open('wb') as output:
        shutil.copyfileobj(response, output)
    ensure_executable(tool_path)
    return tool_path


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def build_appimage(pyinstaller_dir: Path, output_file: Path, *, icon_path: Path, tool_path: Path) -> Path:
    if sys.platform != 'linux':
        raise RuntimeError('AppImage builds are supported only on Linux.')
    if not pyinstaller_dir.is_dir():
        raise FileNotFoundError(f'PyInstaller output directory not found: {pyinstaller_dir}')
    executable_path = pyinstaller_dir / APP_NAME
    if not executable_path.exists():
        raise FileNotFoundError(f'Executable not found inside PyInstaller directory: {executable_path}')

    appdir = output_file.parent / f'{APP_NAME}.AppDir'
    if appdir.exists():
        shutil.rmtree(appdir)

    bundle_dir = appdir / 'usr' / 'bin' / APP_NAME
    shutil.copytree(pyinstaller_dir, bundle_dir)

    apprun = appdir / 'AppRun'
    write_text(
        apprun,
        '#!/bin/sh\nDIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexec "$DIR/usr/bin/SciPlotter/SciPlotter" "$@"\n',
    )
    ensure_executable(apprun)

    desktop_file = appdir / f'{APP_NAME}.desktop'
    write_text(
        desktop_file,
        '\n'.join([
            '[Desktop Entry]',
            'Type=Application',
            f'Name={APP_NAME}',
            'Comment=Launch SciPlotter',
            f'Exec={APP_NAME}',
            f'Icon={APP_NAME}',
            'Terminal=false',
            'Categories=Science;',
            '',
        ]),
    )

    icon_dest = appdir / f'{APP_NAME}.png'
    icon_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_path, icon_dest)
    share_icon = appdir / 'usr' / 'share' / 'icons' / 'hicolor' / '512x512' / 'apps' / f'{APP_NAME}.png'
    share_icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_path, share_icon)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    tool = download_appimagetool(tool_path)
    env = os.environ.copy()
    env.setdefault('ARCH', 'x86_64')
    env['APPIMAGE_EXTRACT_AND_RUN'] = '1'
    subprocess.run([str(tool), str(appdir), str(output_file)], check=True, env=env)
    return output_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build a SciPlotter AppImage from a PyInstaller onedir directory.')
    parser.add_argument('pyinstaller_dir', help='Path to the PyInstaller onedir output (dist/linux/SciPlotter).')
    parser.add_argument('output_file', help='Destination AppImage path.')
    parser.add_argument('--icon', default=str(PROJECT_ROOT / 'assets' / 'launcher-icon.png'))
    parser.add_argument('--tool-path', default=str(PROJECT_ROOT / '.tools' / 'appimagetool.AppImage'))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_appimage(
        Path(args.pyinstaller_dir).resolve(),
        Path(args.output_file).resolve(),
        icon_path=Path(args.icon).resolve(),
        tool_path=Path(args.tool_path).resolve(),
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
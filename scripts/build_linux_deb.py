#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path


APP_NAME = 'SciPlotter'
PACKAGE_NAME = 'sciplotter'
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def detect_architecture() -> str:
    try:
        result = subprocess.run(['dpkg', '--print-architecture'], capture_output=True, text=True, check=True)
        value = result.stdout.strip()
        if value:
            return value
    except Exception:
        pass
    machine = platform.machine().lower()
    if machine in ('x86_64', 'amd64'):
        return 'amd64'
    if machine in ('aarch64', 'arm64'):
        return 'arm64'
    return machine or 'amd64'


def build_deb(pyinstaller_dir: Path, output_file: Path, *, icon_path: Path, version: str) -> Path:
    if not pyinstaller_dir.is_dir():
        raise FileNotFoundError(f'PyInstaller output directory not found: {pyinstaller_dir}')

    package_root = output_file.parent / 'deb-root'
    if package_root.exists():
        shutil.rmtree(package_root)

    app_root = package_root / 'opt' / APP_NAME
    shutil.copytree(pyinstaller_dir, app_root)

    wrapper = package_root / 'usr' / 'bin' / PACKAGE_NAME
    write_text(wrapper, '#!/bin/sh\nexec /opt/SciPlotter/SciPlotter "$@"\n')
    wrapper.chmod(0o755)

    desktop_file = package_root / 'usr' / 'share' / 'applications' / f'{PACKAGE_NAME}.desktop'
    pixmaps_icon = Path('/usr/share/pixmaps') / f'{PACKAGE_NAME}.png'
    write_text(
        desktop_file,
        '\n'.join([
            '[Desktop Entry]',
            'Version=1.0',
            'Type=Application',
            'Name=SciPlotter',
            'Comment=Launch SciPlotter',
            f'Exec=/usr/bin/{PACKAGE_NAME}',
            f'Icon={pixmaps_icon}',
            'Terminal=false',
            'Categories=Science;',
            '',
        ]),
    )

    pixmaps_dest = package_root / 'usr' / 'share' / 'pixmaps' / f'{PACKAGE_NAME}.png'
    pixmaps_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_path, pixmaps_dest)

    icon_dest = package_root / 'usr' / 'share' / 'icons' / 'hicolor' / '512x512' / 'apps' / f'{PACKAGE_NAME}.png'
    icon_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_path, icon_dest)

    control_file = package_root / 'DEBIAN' / 'control'
    write_text(
        control_file,
        '\n'.join([
            f'Package: {PACKAGE_NAME}',
            f'Version: {version}',
            'Section: science',
            'Priority: optional',
            f'Architecture: {detect_architecture()}',
            'Maintainer: Dachi Okropiridze',
            'Depends: xdg-utils',
            'Description: SciPlotter desktop waveform and histogram analysis application',
            ' A desktop-friendly local analysis tool that launches a local web UI.',
            '',
        ]),
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['dpkg-deb', '--build', str(package_root), str(output_file)], check=True)
    return output_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build a SciPlotter .deb package from a PyInstaller onedir directory.')
    parser.add_argument('pyinstaller_dir', help='Path to the PyInstaller onedir output (dist/linux/SciPlotter).')
    parser.add_argument('output_file', help='Destination .deb path.')
    parser.add_argument('--icon', default=str(PROJECT_ROOT / 'assets' / 'launcher-icon.png'))
    parser.add_argument('--version', default=os.environ.get('SCIPLOTTER_VERSION', '1.0.0'))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_deb(
        Path(args.pyinstaller_dir).resolve(),
        Path(args.output_file).resolve(),
        icon_path=Path(args.icon).resolve(),
        version=args.version,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
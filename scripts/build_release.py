#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_linux_appimage import build_appimage  # noqa: E402


APP_NAME = 'SciPlotter'
DIST_ROOT = PROJECT_ROOT / 'dist'
RELEASE_ROOT = DIST_ROOT / 'release'
WORK_ROOT = PROJECT_ROOT / 'build' / 'pyinstaller'
ENTRY_SCRIPT = PROJECT_ROOT / 'scripts' / 'launch_sciplotter.py'
ICON_PATH = PROJECT_ROOT / 'assets' / 'launcher-icon.png'
SPEC_PATH = WORK_ROOT / 'spec'


def detect_target() -> str:
    if sys.platform == 'win32':
        return 'windows'
    if sys.platform == 'darwin':
        return 'macos'
    return 'linux'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build a standalone SciPlotter release artifact for the current OS.')
    parser.add_argument('--target', choices=['auto', 'windows', 'macos', 'linux'], default='auto')
    parser.add_argument('--clean', action='store_true', help='Delete previous build output for the selected target before packaging.')
    parser.add_argument('--skip-appimage', action='store_true', help='On Linux, keep the PyInstaller directory only and skip AppImage assembly.')
    return parser.parse_args()


def add_data_arg(path: Path, dest: str) -> str:
    sep = ';' if os.name == 'nt' else ':'
    return f'{path}{sep}{dest}'


def run_command(args: list[str]) -> None:
    print('>', ' '.join(str(arg) for arg in args))
    subprocess.run(args, check=True, cwd=str(PROJECT_ROOT))


def clean_target(target: str) -> None:
    for path in [DIST_ROOT / target, WORK_ROOT / target]:
        if path.exists():
            shutil.rmtree(path)
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)


def build_pyinstaller(target: str) -> Path:
    dist_dir = DIST_ROOT / target
    work_dir = WORK_ROOT / target
    SPEC_PATH.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--noconfirm',
        '--clean',
        '--name', APP_NAME,
        '--paths', str(PROJECT_ROOT),
        '--distpath', str(dist_dir),
        '--workpath', str(work_dir),
        '--specpath', str(SPEC_PATH),
        '--add-data', add_data_arg(PROJECT_ROOT / 'static', 'static'),
        '--add-data', add_data_arg(PROJECT_ROOT / 'assets', 'assets'),
        '--hidden-import', 'sciplotter_backend.static_routes',
        '--hidden-import', 'sciplotter_backend.data',
        '--hidden-import', 'sciplotter_backend.markers',
        '--hidden-import', 'werkzeug.serving',
    ]

    if target == 'windows':
        command.extend(['--onefile', '--windowed'])
    elif target == 'macos':
        command.extend(['--windowed'])
    elif target == 'linux':
        command.extend(['--windowed'])

    command.append(str(ENTRY_SCRIPT))
    run_command(command)
    return dist_dir


def package_release_artifact(target: str, dist_dir: Path, *, skip_appimage: bool) -> Path:
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)

    if target == 'windows':
        built_exe = dist_dir / f'{APP_NAME}.exe'
        artifact = RELEASE_ROOT / 'SciPlotter-windows.exe'
        shutil.copy2(built_exe, artifact)
        return artifact

    if target == 'macos':
        built_app = dist_dir / f'{APP_NAME}.app'
        archive_base = RELEASE_ROOT / 'SciPlotter-macos'
        archive_path = Path(shutil.make_archive(str(archive_base), 'zip', root_dir=built_app.parent, base_dir=built_app.name))
        return archive_path

    built_dir = dist_dir / APP_NAME
    if skip_appimage:
        archive_base = RELEASE_ROOT / 'SciPlotter-linux'
        archive_path = Path(shutil.make_archive(str(archive_base), 'gztar', root_dir=dist_dir, base_dir=APP_NAME))
        return archive_path

    artifact = RELEASE_ROOT / 'SciPlotter-linux.AppImage'
    return build_appimage(
        built_dir,
        artifact,
        icon_path=ICON_PATH,
        tool_path=PROJECT_ROOT / '.tools' / 'appimagetool.AppImage',
    )


def main() -> int:
    args = parse_args()
    target = detect_target() if args.target == 'auto' else args.target
    native_target = detect_target()
    if target != native_target:
        raise SystemExit(f'Native packaging is required. Requested {target}, but this machine can build only {native_target}.')

    if args.clean:
        clean_target(target)

    dist_dir = build_pyinstaller(target)
    artifact = package_release_artifact(target, dist_dir, skip_appimage=args.skip_appimage)
    print(f'Created release artifact: {artifact}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
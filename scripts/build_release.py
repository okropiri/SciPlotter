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
from build_linux_deb import build_deb  # noqa: E402


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
    parser.add_argument('--version', default=os.environ.get('SCIPLOTTER_VERSION') or os.environ.get('GITHUB_REF_NAME', 'v1.0.0').lstrip('v'))
    return parser.parse_args()


def add_data_arg(path: Path, dest: str) -> str:
    sep = ';' if os.name == 'nt' else ':'
    return f'{path}{sep}{dest}'


def run_command(args: list[str]) -> None:
    print('>', ' '.join(str(arg) for arg in args))
    subprocess.run(args, check=True, cwd=str(PROJECT_ROOT))


def env_or_none(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def macos_codesign_identity() -> str:
    return env_or_none('SCIPLOTTER_MACOS_CODESIGN_IDENTITY') or '-'


def macos_notary_auth_args() -> list[str]:
    key_path = env_or_none('SCIPLOTTER_MACOS_NOTARY_KEY_PATH')
    key_id = env_or_none('SCIPLOTTER_MACOS_NOTARY_KEY_ID')
    issuer = env_or_none('SCIPLOTTER_MACOS_NOTARY_ISSUER')
    if any([key_path, key_id, issuer]):
        if not all([key_path, key_id, issuer]):
            raise RuntimeError(
                'Incomplete App Store Connect notarization configuration. '
                'Set SCIPLOTTER_MACOS_NOTARY_KEY_PATH, SCIPLOTTER_MACOS_NOTARY_KEY_ID, and SCIPLOTTER_MACOS_NOTARY_ISSUER.'
            )
        return ['--key', key_path, '--key-id', key_id, '--issuer', issuer]

    apple_id = env_or_none('SCIPLOTTER_MACOS_NOTARY_APPLE_ID')
    team_id = env_or_none('SCIPLOTTER_MACOS_NOTARY_TEAM_ID')
    password = env_or_none('SCIPLOTTER_MACOS_NOTARY_PASSWORD')
    if any([apple_id, team_id, password]):
        if not all([apple_id, team_id, password]):
            raise RuntimeError(
                'Incomplete Apple ID notarization configuration. '
                'Set SCIPLOTTER_MACOS_NOTARY_APPLE_ID, SCIPLOTTER_MACOS_NOTARY_TEAM_ID, and SCIPLOTTER_MACOS_NOTARY_PASSWORD.'
            )
        return ['--apple-id', apple_id, '--team-id', team_id, '--password', password]

    return []


def codesign_macos_app(app_bundle: Path) -> None:
    if sys.platform != 'darwin':
        return
    identity = macos_codesign_identity()
    command = ['/usr/bin/codesign', '--force', '--deep']
    if identity != '-':
        command.extend(['--options', 'runtime', '--timestamp'])
    command.extend(['--sign', identity, str(app_bundle)])
    run_command(command)
    run_command(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app_bundle)])


def notarize_macos_archive(archive_path: Path) -> None:
    auth_args = macos_notary_auth_args()
    if not auth_args:
        return
    if macos_codesign_identity() == '-':
        raise RuntimeError(
            'macOS notarization requires SCIPLOTTER_MACOS_CODESIGN_IDENTITY to be set to a Developer ID Application identity.'
        )
    run_command(['/usr/bin/xcrun', 'notarytool', 'submit', str(archive_path), '--wait', *auth_args])


def staple_macos_app(app_bundle: Path) -> None:
    run_command(['/usr/bin/xcrun', 'stapler', 'staple', '-v', str(app_bundle)])
    run_command(['/usr/bin/xcrun', 'stapler', 'validate', '-v', str(app_bundle)])


def zip_macos_app(app_bundle: Path, archive_path: Path) -> Path:
    if archive_path.exists():
        archive_path.unlink()
    run_command([
        '/usr/bin/ditto',
        '-c',
        '-k',
        '--sequesterRsrc',
        '--keepParent',
        str(app_bundle),
        str(archive_path),
    ])
    return archive_path


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


def package_release_artifact(target: str, dist_dir: Path, *, skip_appimage: bool, version: str) -> list[Path]:
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)

    if target == 'windows':
        built_exe = dist_dir / f'{APP_NAME}.exe'
        artifact = RELEASE_ROOT / 'SciPlotter-windows.exe'
        shutil.copy2(built_exe, artifact)
        return [artifact]

    if target == 'macos':
        built_app = dist_dir / f'{APP_NAME}.app'
        codesign_macos_app(built_app)
        archive_path = zip_macos_app(built_app, RELEASE_ROOT / 'SciPlotter-macos.zip')
        if macos_notary_auth_args():
            notarize_macos_archive(archive_path)
            staple_macos_app(built_app)
            archive_path = zip_macos_app(built_app, RELEASE_ROOT / 'SciPlotter-macos.zip')
        return [archive_path]

    built_dir = dist_dir / APP_NAME
    artifacts: list[Path] = []
    if skip_appimage:
        archive_base = RELEASE_ROOT / 'SciPlotter-linux'
        archive_path = Path(shutil.make_archive(str(archive_base), 'gztar', root_dir=dist_dir, base_dir=APP_NAME))
        artifacts.append(archive_path)
    else:
        artifact = RELEASE_ROOT / 'SciPlotter-linux.AppImage'
        artifacts.append(
            build_appimage(
                built_dir,
                artifact,
                icon_path=ICON_PATH,
                tool_path=PROJECT_ROOT / '.tools' / 'appimagetool.AppImage',
            )
        )

    deb_artifact = RELEASE_ROOT / 'SciPlotter-linux.deb'
    artifacts.append(
        build_deb(
            built_dir,
            deb_artifact,
            icon_path=ICON_PATH,
            version=version,
        )
    )
    return artifacts


def main() -> int:
    args = parse_args()
    target = detect_target() if args.target == 'auto' else args.target
    native_target = detect_target()
    if target != native_target:
        raise SystemExit(f'Native packaging is required. Requested {target}, but this machine can build only {native_target}.')

    if args.clean:
        clean_target(target)

    dist_dir = build_pyinstaller(target)
    artifacts = package_release_artifact(target, dist_dir, skip_appimage=args.skip_appimage, version=args.version)
    for artifact in artifacts:
        print(f'Created release artifact: {artifact}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
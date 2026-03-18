#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENV = PROJECT_ROOT / '.venv'
RUNTIME_REQUIREMENTS = PROJECT_ROOT / 'requirements.txt'
BUILD_REQUIREMENTS = PROJECT_ROOT / 'requirements-build.txt'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Create a local SciPlotter environment and install runtime/build dependencies.')
    parser.add_argument('--venv-path', default=str(DEFAULT_VENV), help='Virtual environment directory to create or reuse.')
    parser.add_argument('--build', action='store_true', help='Install build dependencies such as PyInstaller in addition to runtime requirements.')
    parser.add_argument('--reuse-active', action='store_true', help='Reuse the current Python environment instead of creating a venv when already inside one.')
    parser.add_argument('--skip-runtime', action='store_true', help='Skip installing runtime requirements.txt.')
    parser.add_argument('--skip-upgrade-pip', action='store_true', help='Skip upgrading pip/setuptools/wheel in the target environment.')
    return parser.parse_args()


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, 'base_prefix', sys.prefix)


def venv_python(venv_path: Path) -> Path:
    if os.name == 'nt':
        return venv_path / 'Scripts' / 'python.exe'
    return venv_path / 'bin' / 'python'


def ensure_venv(venv_path: Path) -> Path:
    if not venv_path.exists():
        print(f'Creating virtual environment at {venv_path}')
        builder = venv.EnvBuilder(with_pip=True, clear=False, symlinks=os.name != 'nt')
        builder.create(venv_path)
    python_bin = venv_python(venv_path)
    if not python_bin.exists():
        raise RuntimeError(f'Virtual environment Python not found: {python_bin}')
    return python_bin


def run_command(args: list[str]) -> None:
    print('>', ' '.join(str(arg) for arg in args))
    subprocess.run(args, check=True, cwd=str(PROJECT_ROOT))


def main() -> int:
    args = parse_args()
    target_python: Path

    if args.reuse_active and in_virtualenv():
        target_python = Path(sys.executable)
    else:
        target_python = ensure_venv(Path(args.venv_path).expanduser().resolve())

    if not args.skip_upgrade_pip:
        run_command([str(target_python), '-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel'])

    if not args.skip_runtime and RUNTIME_REQUIREMENTS.exists():
        run_command([str(target_python), '-m', 'pip', 'install', '-r', str(RUNTIME_REQUIREMENTS)])

    if args.build and BUILD_REQUIREMENTS.exists():
        run_command([str(target_python), '-m', 'pip', 'install', '-r', str(BUILD_REQUIREMENTS)])

    print('\nSciPlotter bootstrap complete.')
    print(f'Python: {target_python}')
    print('Launch from source:')
    print(f'  {target_python} {PROJECT_ROOT / "scripts" / "launch_sciplotter.py"}')
    if args.build:
        print('Build packaged artifact for this OS:')
        print(f'  {target_python} {PROJECT_ROOT / "scripts" / "build_release.py"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
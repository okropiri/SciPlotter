# SciPlotter

SciPlotter is a desktop-friendly waveform and histogram analysis application for local scientific data inspection.

Creator: Dachi Okropiridze

SciPlotter runs as a local desktop app that starts its own local backend and opens the user interface in the browser. It can be used directly from source or installed from packaged desktop releases.

## Download

The easiest way to get SciPlotter is from the GitHub Releases page for this repository.

Download the package that matches your operating system:

- Windows: `SciPlotter-windows.exe`
- macOS: `SciPlotter-macos.zip`
- Linux: `SciPlotter-linux.AppImage`

## Install

### Windows

1. Download `SciPlotter-windows.exe` from the latest release.
2. Place it in any folder you want to keep it in.
3. Double-click the file to launch SciPlotter.

### macOS

1. Download `SciPlotter-macos.zip` from the latest release.
2. Unzip it.
3. Move `SciPlotter.app` into `Applications` if you want a standard app install.
4. Open `SciPlotter.app`.

If macOS blocks the app the first time, open it from Finder with `Open` so Gatekeeper can confirm the launch.

### Linux

1. Download `SciPlotter-linux.AppImage` from the latest release.
2. Make it executable:

```bash
chmod +x SciPlotter-linux.AppImage
```

3. Run it:

```bash
./SciPlotter-linux.AppImage
```

## Run From Source

If you want to run SciPlotter directly from the repository:

```bash
python scripts/bootstrap.py
```

Then launch it with:

```bash
.venv/bin/python scripts/launch_sciplotter.py
```

On Windows:

```powershell
.venv\Scripts\python.exe scripts\launch_sciplotter.py
```

## Build Releases

To build a native package for the current operating system:

```bash
python scripts/bootstrap.py --build
python scripts/build_release.py --clean
```

Generated release files are written to `dist/release/`.

## How SciPlotter Resolves Paths

SciPlotter is set up so it does not depend on the directory you launch it from.

- Bundled app resources are resolved relative to the source tree or packaged app bundle.
- User logs and runtime data go into standard per-user OS directories.
- Desktop launcher shortcuts use absolute executable paths on purpose so they still work when launched from menus, desktops, or other folders.

This means the software is intended to work correctly from any current working directory.

## More Detailed Packaging Notes

See [docs/install-guide.md](docs/install-guide.md) for the full packaging and release workflow, including GitHub Actions release automation.
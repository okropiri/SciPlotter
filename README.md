# SciPlotter

SciPlotter is a desktop-friendly waveform and histogram analysis application for local scientific data inspection.

Creator: Dachi Okropiridze

SciPlotter runs as a local desktop app that starts its own local backend and opens the user interface in the browser. It can be used directly from source or installed from packaged desktop releases.

## Where Do I Download It?

If you want to install SciPlotter, do not download files from the main repository file list.

The main repository page contains the source code.

For installation, go to the GitHub Releases page:

https://github.com/okropiri/SciPlotter/releases

Then open the latest release and download the file for your operating system from the Release assets section.

## Download

The easiest way to get SciPlotter is from the GitHub Releases page for this repository.

Download the package that matches your operating system:

- Windows: `SciPlotter-windows.exe`
- macOS: `SciPlotter-macos.zip`
- Linux: `SciPlotter-linux.deb` or `SciPlotter-linux.AppImage`

## Which Linux Download Should I Choose?

For most Linux users, the `.deb` package is the easiest and most traditional option.

- `.deb`: best for Debian, Ubuntu, Linux Mint, Pop!_OS, and similar systems. It installs SciPlotter like a normal application and adds it to the app menu.
- `AppImage`: a portable single-file version. You download one file, make it executable, and run it directly without a full install.
- Run from source: this means running the original Python project files yourself. This is mainly for developers or advanced users, not the usual end-user install method.

If you are not sure which one to choose on Linux, use the `.deb` package first.

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

For Debian or Ubuntu based systems, the easiest install is the `.deb` package.

#### Debian or Ubuntu

1. Download `SciPlotter-linux.deb` from the latest release.
2. Install it with your package manager or with:

```bash
sudo apt install ./SciPlotter-linux.deb
```

3. Launch `SciPlotter` from the app menu.

The `.deb` package installs a desktop entry, icon, and launcher automatically.

#### Portable AppImage

1. Download `SciPlotter-linux.AppImage` from the latest release.
2. Make it executable:

```bash
chmod +x SciPlotter-linux.AppImage
```

3. Run it:

```bash
./SciPlotter-linux.AppImage
```

When SciPlotter is launched from the AppImage on Linux, it now tries to create a desktop entry automatically in the user application menu so it appears in the app list more easily.

If you use AppImageLauncher, you can also choose its normal integration flow.

## Run From Source

Running from source means you launch SciPlotter directly from the project code with Python instead of using an installed desktop package.

This is mostly useful for development, testing, or advanced users who want direct access to the repository.

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

On Linux, the build now produces both:

- `SciPlotter-linux.AppImage`
- `SciPlotter-linux.deb`

## How SciPlotter Resolves Paths

SciPlotter is set up so it does not depend on the directory you launch it from.

- Bundled app resources are resolved relative to the source tree or packaged app bundle.
- User logs and runtime data go into standard per-user OS directories.
- Desktop launcher shortcuts use absolute executable paths on purpose so they still work when launched from menus, desktops, or other folders.

This means the software is intended to work correctly from any current working directory.

## More Detailed Packaging Notes

See [docs/install-guide.md](docs/install-guide.md) for the full packaging and release workflow, including GitHub Actions release automation.
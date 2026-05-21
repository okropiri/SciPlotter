# SciPlotter Install and Packaging Guide

Creator: Dachi Okropiridze

SciPlotter now supports two installation paths:

1. Source install for development or local analysis.
2. Native packaged desktop artifacts built with PyInstaller.

## Plain-Language Install Choices

For non-technical users, the packaging options mean:

- `.deb`: the normal Linux installer style for Debian-based systems such as Ubuntu. This is the most traditional installed-app experience.
- `AppImage`: a portable one-file app. You keep the file wherever you want, make it executable, and run it directly.
- Run from source: start SciPlotter from the original Python project files. This is mainly for development or advanced users.

Recommended default:

- On Debian or Ubuntu based systems, choose the `.deb` package.
- On other Linux distributions, choose the `AppImage`.

## Release Artifacts

The repository is configured to produce one native artifact per operating system:

- Windows: `SciPlotter-windows.exe`
- macOS: `SciPlotter-macos.zip` containing `SciPlotter.app`
- Linux: `SciPlotter-linux.AppImage`
- Linux: `SciPlotter-linux.deb`

Important constraint:

- Windows artifacts must be built on Windows.
- macOS artifacts must be built on macOS.
- Linux AppImage artifacts must be built on Linux.
- PyInstaller does not provide reliable cross-compilation for these targets from a single host OS.

The provided GitHub Actions workflow handles that by building on native runners for each OS.

## Source Install

Running from source means using the repository itself as the application rather than installing a packaged desktop build.

This is useful for development, debugging, or users who want to modify the project.

From the repository root:

```bash
python scripts/bootstrap.py
```

That creates `.venv` by default, upgrades pip tooling, and installs runtime dependencies from `requirements.txt`.

Launch the app from source with:

```bash
.venv/bin/python scripts/launch_sciplotter.py
```

On Windows:

```powershell
.venv\Scripts\python.exe scripts\launch_sciplotter.py
```

The launcher starts the local Flask backend, waits for `/health`, and opens the browser UI.

## Bootstrap Script

The cross-platform bootstrap entry point is:

```bash
python scripts/bootstrap.py --build
```

Useful flags:

- `--build`: install `requirements-build.txt` in addition to runtime dependencies.
- `--venv-path PATH`: choose a different virtualenv location.
- `--reuse-active`: reuse the current active environment instead of creating `.venv`.
- `--skip-runtime`: only install build dependencies.

## Build Desktop Packages Locally

Install build dependencies first:

```bash
python scripts/bootstrap.py --build
```

Then build the native package for the current OS:

```bash
python scripts/build_release.py --clean
```

Outputs go to `dist/release/`.

### Windows

Build result:

- `dist/release/SciPlotter-windows.exe`

### macOS

Build result:

- `dist/release/SciPlotter-macos.zip`

The zip contains `SciPlotter.app`.

The macOS release build always signs the generated app bundle and archives it with Apple's `ditto` tool so the bundle metadata and symlinks survive download and extraction correctly.

Default behavior without Apple credentials:

- The app is ad-hoc signed.
- This is enough for bundle integrity testing and direct binary execution, but normal Finder or LaunchServices opening may still be blocked by macOS security policy.

Optional notarized release behavior:

- If `SCIPLOTTER_MACOS_CODESIGN_IDENTITY` is set to a `Developer ID Application` identity, the build uses that identity with hardened runtime and timestamped signing.
- If notarization credentials are also configured, the build submits the macOS zip with `xcrun notarytool`, waits for approval, staples the app bundle, and then re-zips the stapled app for release.

Supported notarization authentication methods:

- App Store Connect API key: `SCIPLOTTER_MACOS_NOTARY_KEY_PATH`, `SCIPLOTTER_MACOS_NOTARY_KEY_ID`, `SCIPLOTTER_MACOS_NOTARY_ISSUER`
- Apple ID credentials: `SCIPLOTTER_MACOS_NOTARY_APPLE_ID`, `SCIPLOTTER_MACOS_NOTARY_TEAM_ID`, `SCIPLOTTER_MACOS_NOTARY_PASSWORD`

Important limitation:

- If you do not provide a Developer ID Application certificate and notarization credentials, Gatekeeper may still warn that Apple cannot verify the developer or block Finder launch entirely.
- Notarized output depends on Apple credentials being present at build time; otherwise the script falls back to ad-hoc signing.

If a user allows the app to open but no browser window appears, collect these files first:

- `~/Library/Caches/SciPlotter/launcher.log`
- `~/Library/Caches/SciPlotter/server.log`

Those logs capture launcher failures and backend startup errors for packaged macOS runs.

### Linux

Build result:

- `dist/release/SciPlotter-linux.AppImage`
- `dist/release/SciPlotter-linux.deb`

The Linux build script assembles the AppImage from the PyInstaller onedir output and downloads `appimagetool` automatically to `.tools/appimagetool.AppImage` if it is not already present.

The `.deb` package installs SciPlotter into `/opt/SciPlotter`, provides `/usr/bin/sciplotter`, and installs a desktop file and icon so the application appears in the menu immediately after installation.

The AppImage now performs user-level desktop integration on launch by writing a desktop entry into the user's local applications directory and copying the icon into the user's icon theme directory. That makes the AppImage show up in the app menu more easily after first launch.

If you only want the raw PyInstaller directory or a tarball instead of an AppImage:

```bash
python scripts/build_release.py --clean --skip-appimage
```

## GitHub Actions Release Workflow

The workflow file is at `.github/workflows/release.yml`.

It does the following:

1. Builds on `windows-latest`, `macos-latest`, and `ubuntu-latest`.
2. Runs `python scripts/bootstrap.py --build`.
3. Runs the build with the Python executable from the created `.venv`.
4. Uploads the per-OS artifact as a workflow artifact.
5. On `v*` tags, publishes those assets to the GitHub release.

Optional macOS signing and notarization in GitHub Actions:

- `MACOS_CERTIFICATE_P12_BASE64`: base64-encoded `.p12` containing your `Developer ID Application` certificate.
- `MACOS_CERTIFICATE_PASSWORD`: password for that `.p12` file.
- `MACOS_CODESIGN_IDENTITY`: full signing identity name, for example `Developer ID Application: Example Name (TEAMID)`.
- `MACOS_NOTARY_KEY_BASE64`: base64-encoded App Store Connect API key `.p8` content.
- `MACOS_NOTARY_KEY_ID`: App Store Connect key ID.
- `MACOS_NOTARY_ISSUER`: App Store Connect issuer UUID.

If the certificate secrets are absent, the workflow still produces an ad-hoc signed macOS zip. If both the signing identity and notarization credentials are present, the workflow will notarize and staple the macOS app automatically.

For a short operational checklist, see [docs/macos-release-checklist.md](docs/macos-release-checklist.md).

## Freeze-Aware Runtime Notes

The application now resolves bundled resources through `sciplotter_backend.runtime`.

That means packaged builds can locate:

- `static/`
- `assets/`
- runtime cache/log directories

without depending on the original source checkout path.

It also avoids depending on the current working directory at launch time.

- App resources resolve from the source tree or PyInstaller bundle root.
- Default user data and logs resolve into per-user OS directories.
- Desktop shortcut `Exec` and `Icon` entries remain absolute by design so launchers work from application menus and arbitrary directories.

## Recommended Release Process

1. Commit the packaging files.
2. Push to GitHub.
3. Create a version tag such as `v1.0.0`.
4. Let GitHub Actions build and publish the native artifacts.
5. Verify each artifact on its target OS before announcing the release.
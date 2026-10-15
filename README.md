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

## Sample Histogram Data

The repository also includes a large example summary CSV for the Histogram page:

- `examples/histograms/synthetic_detector_events_summary.csv`

This file is synthetic test data generated to look like detector event summaries. It is not real CERN data, but it is structured to produce sensible 1D and 2D histogram views with clear channel-to-channel differences.

To use it in SciPlotter, open the Histogram page and load the CSV with the file-picker control for custom summary files.

## Load Saved ROOT Histograms

The Histogram page imports both ROOT event tables (TTrees) and saved 1D/2D
histograms (TH1/TH2, including float, double and integer storage).

1. Click **Load**, then **Browse** and select a ROOT file.
2. Choose a saved histogram or event table in the grouped **ROOT object** selector.
   The preview shows the selected object's axes, bin counts and contents.
3. Click **Load This Data**. Reopening **Load** keeps the same file and selected
   object so you can choose another histogram. **Browse** changes the file;
   cancelling the picker or selecting the same file preserves the selection.

Saved histograms retain their native bin edges, weighted/signed bin contents,
titles and variances. Underflow/overflow totals are reported but not plotted.
Statistics are calculated from weighted bin centers, not reconstructed events.
Event filters and channel filtering are disabled for saved histograms.
To rebin, enable **Rebin**, choose fewer **Bins X / Bins Y**, and click **Render**.
Existing whole bins in the displayed/manual axis range are merged; their contents
and variances are summed. Range edges snap outward to native edges, and the
requested bin count is limited to the available bins in that range. Bins are
never split or interpolated, so increasing the original resolution is not
supported. Unchecking **Rebin** and rendering restores the original bins. Repeated
rebinning always starts from the original data, not the previous rebinned view.
Statistics continue to use the original native bin centers within the included
range; changing only the merge resolution does not shift the reported moments.
Zoom, colors, annotations, export and 2D projections remain available. Box, lasso
and ellipse selections include whole bins by their centers and are labelled as
bin selections. Original event-data controls are restored when loading CSVs or
TTrees again. **Swap Axes** transposes a TH2 view, including edges and variances,
without changing the source. For TH1, **Bins** sets the same whole-bin merging
resolution in either orientation.

All adjustments operate on display views; the loaded contents, edges, variances
and ROOT file are unchanged. Probability and percent divide by the displayed
in-range bin sum. Probability density also divides by each actual bin width, so
its width-weighted integral is one. Cumulative density displays cumulative
probability, not a sum of density heights. These normalizations are unavailable
for signed or zero-sum contents; raw cumulative bin sums remain available.
Bin variances stay in raw bin-content units, including in selections and
projections. Stored `Sumw2` is retained. When it is absent, nonnegative histograms
use an explicitly labelled default Poisson assumption (variance = bin content);
this is not evidence of measured weighted uncertainties. Signed histograms
without `Sumw2` have unavailable variances, not invented zero errors.
No inter-bin covariance or normalized-error model is inferred.

Log X/Y are coordinate-only displays and cannot show nonpositive coordinates
or contents; those source bins are retained. Log Z uses `log10(1 + bin content)`
for colors only, including zero bins. Hover values, markers and projections use
raw bin contents. Log Z is disabled for negative contents. Manual display bounds
must be supplied as pairs (or both cleared); logarithmic bounds must be positive.
Color limits, palettes, contours, text, drawings and export never rescale stored
weights. Contours interpolate a visual overlay, not histogram data. Clear removes
the plot, not the loaded histogram; Render restores it.

TH3, TH2Poly and TProfile objects are listed as unsupported rather than treated
as ordinary event-count histograms.

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

Official macOS releases can be notarized when the release workflow is configured with Apple signing credentials. If a build was produced without those credentials, macOS may still block normal app opening even though the bundled executable itself is valid.

If macOS says the app is damaged, that usually means the downloaded release asset itself needs to be refreshed rather than approved through Finder. In that case, download the newest macOS release asset again.

If the app is allowed to open but no browser window appears, check these log files in the user's home folder:

- `~/Library/Caches/SciPlotter/launcher.log`
- `~/Library/Caches/SciPlotter/server.log`

### Linux

For Debian or Ubuntu based systems, the easiest install is the `.deb` package.

#### Debian or Ubuntu

1. Download `SciPlotter-linux.deb` from the latest release.
2. Install it with your package manager or with:

```bash
sudo apt install ./SciPlotter-linux.deb
```

Use `./` so `apt` treats the `.deb` as a local file in the current folder. Without `./`, `apt` may try to look for a package with that name in online repositories instead.

3. Launch `SciPlotter` from the app menu.

The `.deb` package installs a desktop entry, icon, and launcher automatically.

If you see a warning saying the download was performed unsandboxed because the file could not be accessed by user `_apt`, the installation usually still succeeded. That warning can happen when installing a local `.deb` from a folder with restricted permissions, such as `Downloads`.

To remove the Debian package later, use:

```bash
sudo apt remove sciplotter
```

The installed application is shown to users as `SciPlotter`, but the Debian package name is lowercase `sciplotter`, which is the standard package-manager naming convention.

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
.venv/bin/python -m scripts.build_release --clean
```

On Windows, use `.venv\Scripts\python.exe -m scripts.build_release --clean`
for the second command.

Generated release files are written to `dist/release/`.

On Linux, the build now produces both:

- `SciPlotter-linux.AppImage`
- `SciPlotter-linux.deb`

## Validate Source Changes

Run the importer and exact ROOT aggregation regression tests with the project environment:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests generate temporary ROOT/CSV fixtures and check full-dataset aggregation,
selection geometry on linear/logarithmic and reversed axes, combined filters,
cache isolation, saved histogram bins/variances, mixed ROOT files, and API errors.
They do not modify the example data.

Frontend whole-bin rebin regressions use Node.js when available, or the built-in
JavaScriptCore engine on macOS. Those tests are explicitly skipped if neither
engine is available.

Exact 2D ROOT selections support box, lasso, and ellipse regions. On logarithmic
axes, lasso and ellipse geometry follows the displayed plot coordinates.

## How SciPlotter Resolves Paths

SciPlotter is set up so it does not depend on the directory you launch it from.

- Bundled app resources are resolved relative to the source tree or packaged app bundle.
- User logs and runtime data go into standard per-user OS directories.
- Desktop launcher shortcuts use absolute executable paths on purpose so they still work when launched from menus, desktops, or other folders.

This means the software is intended to work correctly from any current working directory.

## More Detailed Packaging Notes

See [docs/install-guide.md](docs/install-guide.md) for the full packaging and release workflow, including GitHub Actions release automation.
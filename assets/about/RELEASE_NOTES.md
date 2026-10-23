# Release Notes

---

# Interface redesign (testing ward)

> A new main window layout and an application-wide theme. No processing or analysis code changed; every existing menu entry is still where it was.

## Main window

- **Toolbar** with drawn icons under the menu bar: open folder / files, reload, the mouse modes (pan, zoom box, pick peaks, with *Pick into* next to it while picking), Log Y, Stacked, σ clip, grid, cross-lines, Peaks, dock peaks, legend, ratio, areas, clusters, an **Export** menu (PNG, SVG, PDF, PGF, batch, print) and copy. The buttons are the menu actions themselves, so both always agree.
- **Sidebar** (hide with **Ctrl+B**, resizable, width remembered) with three foldable cards:
  - **Data**: folder name and path, main spectrum toggle + colour + reload, Mode / dt / Separator filters, and a **file list** instead of a dropdown. Each LILBID file shows its sample name, then a coloured pos / neg tag, dt, date and processing tags. A filter box narrows the list; **Alt+↑ / Alt+↓** or the arrows under it step through the files.
  - **Overlays**: each overlay is a small card (colour, on/off, mode, dt, remove / file). **Add overlay** now turns the new overlay on straight away, showing the file after the main one.
  - **Display**: highlight and overlay sliders, σ clip level, View-all lower m/z, Log Y / Stacked / Dynamic scale.
- **Status bar**: m/z and intensity under the cursor (true intensity also in log mode) and the current mouse mode. The readout in the plot corner, which covered the legend, is now off by default (*View → Cursor Readout in Plot Corner*).
- **Dark mode** (Display → Dark, the moon button, or **Ctrl+Shift+D**) now darkens the whole interface, every tool window included, not only the plot.
- Minimum window width went from about 1030 px to about 840 px.

## Peaks list

- Opens at a usable size (it used to shrink to a one-row list every time it opened) and rows keep their natural height.
- The buttons are on top, the list in the middle, and the label / auto-peak options in a card at the bottom.
- **Dock under the plot** (Peaks menu, its File menu, or the toolbar): the peaks list becomes a panel of the main window. **Ctrl+P** shows / hides it. While docked, *Save* peak list is **Ctrl+Alt+S** (Ctrl+S saves the project).

## Legend

- **Colour + symbol** is the default legend mode.
- **Unticking a group** takes its peak lists out of the legend (as unticking each list did); ticking it brings them back.
- **File names**: the legend can name the spectra shown (with their colour) and the peak-list file, under its entries, on screen and in exports. On by default; *Show file names* in the Legend card or window.
- **Automatic placement**: the legend goes in the corner of the plot with the least data and moves when you zoom or pan. You can also choose a corner, or drag it (*Position → Dragged*). Legends dragged before 3.4 stay where they were.
- **Saved with the project**: the legend's look and position, and the name of the peak-list file, are stored in `.drp` files and restored when the project is opened.
- In **dark mode** the legend box is dark, matching the plot (it was a light grey box).
- The legend used the interface font in the preview but a serif font on the plot until a font was chosen; both now use the interface font.
- New **Legend card** in the sidebar with the everyday settings (show, file names, symbol mode, font size, columns, position, box). *More…* opens the Legend parameters window.
- The **Legend parameters** window was reorganised: the main switches on top, a full-width preview drawn on the plot's own background (it was always near-black), then tabs: *Entries*, *Look*, *Symbols on peaks*.

## Sidebar

- Drag the gap between two cards to share the height between them (remembered). A card made smaller than its content scrolls inside.
- A folded card is only as tall as its title; the room it frees goes to the Data card, or stays empty below the cards.
- Tool buttons show a label with the tool's **name**, shortcut and what it does, 250 ms after the pointer stops on them. It is drawn inside the window, so it also appears where native tooltips do not (e.g. some Wayland sessions).

## Toolbar and settings

- **Processing tools on the toolbar**, with their own icons: baseline correction, auto-recalibration, manual recalibration, normalization and the recalibration residuals. A **Batch ▾** button holds the folder versions (baseline, auto / manual recalibration, both, normalization, a plot per file). *Baseline + recalibration* and *ToF → mass* are available too, off by default.
- **Help → Edit Toolbar…** (or right-click the toolbar): tick the tools to show. Saved, so it is kept for the next sessions; *Reset to default* goes back to the standard set.
- **Help → Export Settings… / Import Settings…**: all settings and saved legend labels in one `.json` file, e.g. to set up Droplet on another computer. Recent folders, files and the last session (paths on this computer) are only included if asked for. After an import Droplet offers to restart so everything applies.
- The Processing menu's submenus (Baseline Correction, Recalibration, Baseline + Recalibration, Normalize Spectrum) show the same icons as the toolbar.
- Overlays start in mode **All**, and their mode / dt lists offer *All*, *neg* and *pos*.
- Dropdown lists show up to 30 values (Qt shows 10), and their last value is no longer half hidden behind a scroll arrow.
- The **test suite** and **updater** windows use Droplet's theme and follow it when you switch dark / bright in Droplet; the test results' colours read well in both.

## Recalibration residuals viewer

- Same look as the rest of Droplet, in both themes: the plots were always black, with white value labels.
- File list like the sidebar's: sample name, mode, dt and date, grouped under readable headings ("15.01.2026 10:20" for a "Residuals …" folder), with a filter box, previous / next, and each file's **Δ range** on the right. Files whose Δ range is more than twice the folder's median are flagged in red.
- The last residuals folder opens again next time (it used to open empty).
- Above the plots: the sample, its details, and chips for the number of anchors, Δ range, max |Δ|, **mean Δ** (a shift of the whole spectrum shows there) and the fit deviation.
- **Link m/z axes** (on by default): zoom or pan one plot and the other follows.
- **Values on anchors** can be switched off; the values have 4 decimals (they had 2, so most read 0.00 or ±0.01). Hovering an anchor rings it and gives its peak list, m/z and Δ.
- The legend gives each peak list's number of anchors and fit deviation; hovering still highlights the list.
- Short curves are drawn with dots, so a peak list with one anchor is visible on the left plot.
- Manual-recalibration preview: *Confirm & Save* is the main button; a hint explains that clicking an anchor finds its peak.
- Residuals without an anchor table (older files, or a dense curve only) show their curve over the full width, with the numbers and RMS taken from the curve. (With *Link m/z axes*, the curve was held to the empty anchor plot's 0–1 range and nothing was visible.)
- File names containing dots (e.g. `FL5.40J`) lost everything after the dot when shown without extension: mode, dt and tags are back.
- **Fit deviation** (named so as not to be confused with the calibration residuals, the Δm/z): how far each anchor is from the fitted calibration (its Δ minus the fitted Δ), so a wrong peak or a peak list that disagrees stands out. Taken from the file's dense fitted curve when it has one, otherwise from the recalibration's own model refitted to the anchors (corrected m/z quadratic in flight time, flight time ∝ √m). The anchor plot shows either the Δ, with the fit drawn dashed, or the deviations (*Deviation from the fit*); each peak list shows its fit deviation in the legend, and the file its overall fit deviation. With 3 anchors or fewer the fit passes through all of them, and no deviation is given.
- RMS removed from the files and peak lists: Δ is the correction each anchor needed, so its RMS mostly reflected where the peaks are in m/z. Files show their **Δ range** (highest minus lowest Δ) instead.
- *Anchor plot* option to show or hide the calibration anchors.
- The two plots are separate widgets in a splitter: the anchor plot no longer stays hidden or half drawn until the window is resized.
- The files / plots splitter can be moved in a narrow window (long titles and the chips row held the plots panel wide); the chips and options wrap onto more rows instead.
- Legend entries wrap onto several rows, each as wide as its name; past 4 rows the legend scrolls.

## Fixes

- **Dock peaks** did nothing visible when the peak list was closed (the usual case): it only remembered the choice. Docking now opens the list under the plot; undocking opens it in its own window.

- **Ctrl+Shift+P** was both *Print* and *add a peak row*, so neither worked. Print is now **Ctrl+Alt+P**.
- **Switching dark / bright was slow** (seconds with many peak lists, and much longer with the Legend parameters window open, which could look frozen): the style was installed twice per switch, a Python hook slowed every paint, and our event filters saw every re-style event. It is now done in one pass under the wait cursor: about 3× faster without the Legend window and 4× with it (150 peak lists: 4 s → 1.2 s, and 9.6 s → 2.4 s). The Legend parameters window also opens about 3× faster, and its rows and the peak rows no longer carry their own stylesheets.
- The legend box and the Legend window's preview now switch to dark / bright with the rest (they only did after a legend setting changed).
- The legend's frame lost half of its right and bottom lines, and text could touch the right edge with fractional display scaling.
- Tool buttons show their name after 250 ms (in-window label); other tooltips are back to Qt's usual delay, as the global 250 ms setting slowed down every repaint.
- **Save Project (Ctrl+S) never wrote the file**: it only remembered the path (and the "Project saved" message appeared anyway). Only *Save Project As…* saved. Both save now, and the message only appears when the file was written.
- Opening a project now remembers its path, so Ctrl+S saves back to it.
- Toggling Log Y from the View menu rendered the plot twice.
- *Open Peaks List* was connected twice.
- **Ctrl+O** opens a folder.
- Dropli's answers and the Quick Tour describe the new layout (and Ctrl+Scroll correctly: it shows overlays one at a time, it does not change opacity).

---

# v3.0 – v3.3

> Major release. Versions 3.0 to 3.3 are described together, as changes compared with v2.7.2: one-click installers and uninstallers for all platforms, a new project layout, an updater that runs at launch, side-by-side previous versions, Dropli (a clickable in-app guide), peak-list groups, a reworked legend, a peak boundary checker, a peak ratio tool, a residuals preview for manual recalibration, PGF export for LaTeX, processing metadata in output files, faster stacked mode, example spectra and a built-in test suite.

## Installation and packaging

### One-click installers

New installers set up Droplet with no manual Python steps:

| System  | Installer                        | Launcher created                                   |
| ------- | -------------------------------- | -------------------------------------------------- |
| Windows | `Install_Droplet_Windows.bat`    | `Droplet.lnk` on the Desktop (runs via `pythonw`, no console) |
| macOS   | `Install_Droplet_macOS.command`  | `~/Applications/Droplet.app` bundle with icon      |
| Linux   | `Install_Droplet_Linux.sh`       | `~/.local/share/applications/Droplet.desktop`      |

Each installer:

- Locates a working Python 3 interpreter: `PATH` first, then common Anaconda / Miniconda locations, plus the `py` launcher and python.org paths on Windows, and Homebrew and python.org framework paths on macOS. It skips the Windows Store stub, and on macOS it uses `/usr/bin/python3` only when the Xcode Command Line Tools are installed.
- Creates a dedicated virtual environment in `<Droplet folder>/.venv`, so dependencies never collide with system packages, Anaconda's base environment or other projects. A `.venv` whose interpreter no longer runs (e.g. after a Python upgrade) is rebuilt automatically.
- Bootstraps `pip` via `ensurepip`, and falls back to downloading `get-pip.py`.
- Installs the pinned `requirements.txt` with `--only-binary=:all:` so an unsupported Python fails fast, then falls back to `requirements_new.txt`.
- Detects an existing install from its launcher: the **same folder** is updated/repaired; **another Droplet folder** triggers a prompt showing both folders and versions (the other folder is never modified); a **missing folder** is treated as stale and replaced.
- Stops with a clear dialog when something fails (no Python, Python too old, missing `venv` / `pip`, no network, no write permission).

### Uninstallers

`Uninstall_Droplet_Windows.bat`, `Uninstall_Droplet_macOS.command` and `Uninstall_Droplet_Linux.sh` remove the launcher, then list Droplet's settings and data and offer to remove them (default: keep):

- Qt settings: `HKCU\Software\LILBID\PeakViewer` (Windows), `~/Library/Preferences/com.lilbid.PeakViewer.plist` (macOS, cleared through `defaults delete` so `cfprefsd` cannot write it back), `~/.config/LILBID/PeakViewer.conf` (Linux)
- Saved legend entries / labels: `~/.droplet`
- Updater backups: `droplet_backup_*` in the Droplet folder

The Droplet folder and its `.venv` are always left untouched.

### New project layout

| v2.7.x                         | v3.3                                                  |
| ------------------------------ | ----------------------------------------------------- |
| `Droplet_v2.7.2.py`            | `Droplet.py` (version-independent launcher name)      |
| `droplet/`                     | `droplet_pkg/`                                        |
| `updater.py`                   | `droplet_pkg/updater.py` (`python -m droplet_pkg.updater`) |
| `VERSION`, `LICENSE`, `RELEASE_NOTES.md` | `assets/about/`                             |
| `requirements.txt` (`>=` ranges) | `assets/assimilation_guides/requirements.txt` and `requirements_new.txt` (exact pins) |
| `test_suite.py`                | `assets/test/test_suite.py`                           |
| `docs/images/`                 | `assets/docs/images/`                                 |
| —                              | `assets/icons/` (`.ico`, `.icns`, `.png`)             |
| —                              | `assets/example_spectra/`, `assets/frames/` (Dropli)  |

The application version is read from `assets/about/VERSION` only, so Droplet no longer has to be started from inside its folder. matplotlib is no longer a dependency.

**Upgrading from v2.x:** install v3.3 into a new folder with the installer, then delete the old folder. Settings, saved legend labels and peak-list files are kept.

### Updater

- Droplet checks GitHub in the background at every launch and opens the updater window only when a newer version is available. It can also be opened from **Help → Check for Updates…**.
- The window shows what it is about to do (nothing, or update X → Y); nothing changes until you click **Update**.
- Updates via `git pull --ff-only` in a cloned folder, or by downloading the ZIP (every replaced file is backed up to `droplet_backup_YYYYMMDD_HHMMSS/`).
- Command-line options: `--check`, `--yes`, `--console`.

### Previous versions

**Help → Previous Versions…** lists older releases on GitHub and can **Install**, **Launch** or **Remove** each one. A previous version is downloaded into `previous_versions/<version>/` with its own Python environment holding library versions tested with it (every 2.x release needs NumPy < 2.4). The current version and its launcher are never changed. The same is available from a terminal: `python -m droplet_pkg.version_manager list | install | launch | remove <version>`.

### Start-up and feedback

- A start-up screen shows Droplet's icon, a progress bar and plain-language messages while libraries load and peak lists are restored. It runs in its own process, so it stays smooth while Droplet is busy.
- The system busy pointer is shown during long operations (peak detection, recalibration, batch processing, file loading…), and while progress dialogs are open.

## New features

### Dropli, the clickable guide

Dropli sits in the top-right corner of the main window (also **Help → Ask Dropli…**). Click it, pick what you're trying to do, and it tells you where to go in the menus, with buttons that open the right tool for you. Dropli is not an AI: it follows a fixed tree of questions and answers. It replaces the Help topic dialogs of v2.7.x, and the first-launch tutorial introduces it.

### Example spectra and test suite

- On first launch Droplet opens `assets/example_spectra/`: five synthetic LILBID spectra with known answers (negative- and positive-mode water calibrants, a NaCl cluster spectrum, a peak area / ratio standard and a later-delay-time water spectrum), with matching peak lists and a README.
- **Tests → Run Test Suite…** checks that Droplet works correctly on this computer. The report can be saved and sent along with a problem report. It also runs from a terminal: `python assets/test/test_suite.py [--console]`.

### Peak-list groups

Peak rows can now be organised into named groups in the Peaks window:

- Collapsible group headers, reordered by dragging the `⠿` handle; rows are moved between groups by drag-and-drop, with auto-scroll near the window edges.
- Per-group controls: highlight on/off, highlight band opacity (*Intensity* slider), and group-wide **L**, **1L** and envelope (`⌒`) toggles. Individual row states are preserved.
- Shift+click on a row's L / 1L button applies the new state to every row in that group.
- Deleting a group offers to move its rows to another group.
- Groups are saved in peak-list `.json` files (new `format_version: 2` with `groups` and `rows`). Old flat-list files still load, into an *Unclassified* group.
- The **Legend parameters…** dialog lists entries under collapsible group headers, and *Add missing* / *Rebuild* keep the group order.

### Peaks window editing

- Multi-row selection via the selection handle: click, Ctrl (toggle), Shift (range), Ctrl+Shift (extend). Click elsewhere to clear. Multi-row delete.
- **↑ Above / ↓ Below**: insert blank rows around the selection.
- **⧉ Dup**: duplicate selected rows below the selection.
- **+ Group**: add a new group.
- **⎘ Copy rows / ⎘ Paste rows**: internal clipboard for peak rows.
- **⊕ Aggregate**: merge the selected rows into one new row with all their m/z values combined, deduplicated and sorted.
- **Show edited only**: while you edit a row's text field, only that row is shown on the plot.
- **Search (`Ctrl+F`)**: find bar with next/previous (Enter / Shift+Enter / ↑ / ↓), match counter, *Highlight all*, Esc to close.
- Text edits and the sigma-clip slider are debounced (300 ms / 400 ms). The plot updates when you pause rather than on every keystroke or slider tick.
- Only the edited row's highlights are redrawn after a change, instead of the whole plot.

### Peak list overlap check

**⊛ Peak list check**: select one peak list as reference to list every other row sharing at least one m/z value within an adjustable tolerance, grouped by peak-list group. The popup's checkbox, L, 1L and envelope controls stay synced both ways with the Peaks window. Each row has a zoom button (`⊙`) and shows the matching values.

### Peak boundary checker (Peak Area)

Before a ratio CSV is exported (single file or batch), a new dialog shows the baseline-corrected spectrum with one coloured fill per peak:

- Click a fill to select a peak, then click-drag to redefine its integration bounds.
- Undo / redo (`Ctrl+Z` / `Ctrl+Y`), *Reset all* (`Ctrl+R`), optional peak-maximum dots.
- In batch mode: *Skip file* or *Cancel* for the whole batch. One dialog is reused across files, so its size and position persist.
- Areas are then integrated with the confirmed bounds.

The ratio-mode option is now labelled *Between peak lists (normalized to largest)*.

### Peak ratio tool

**Analysis → Measure Peak Ratio [R]…** (or press `R`) opens a floating window. Click two peak apices on the plot (each click snaps to the local apex) to get P1 / P2 and P2 / P1 intensity ratios, with markers drawn on the plot.

### Residuals preview for manual recalibration

Manual recalibration now opens the Residuals Viewer as a non-modal **Residuals Preview** before saving:

- Per-anchor Δm/z plot, coloured per peak list, with hover read-out.
- Updates live as peaks are toggled in the review window; clicking an anchor scrolls to and highlights its peak row.
- **Mode → Cascade untoggle**: unchecking a peak also unchecks all higher-mass peaks in the same group.
- **⇹ Lines**: vertical reference / detected lines with an arrow, shown only when |Δ| ≥ an adjustable threshold.
- Peaks with the same nominal m/z are kept in sync across groups.
- **Confirm & Save** proceeds; **Cancel** returns to peak review.
- In batch mode the file list also shows the residuals already saved in the current batch (most recent first), so earlier files can be compared with the current one.

### Residuals viewer

- Searches subfolders recursively for `*_residuals_spectrum.txt` files.
- Uses one shared colour map per peak list for the dense curve and the anchor bars, read from new `# peak_list_color:<name>=<hex>` headers in residual files.
- New custom legend: hovering an entry dims the other peak lists.
- Hover label on the anchor bar chart.
- The window can be maximised and snapped to screen edges.

### Peak-list legend

The on-screen legend is now built directly from the peak lists:

- Each peak list carries its own legend settings (shown or not, symbol, column), saved in the peak-list file. Renaming a label or changing a colour in the legend dialog changes the peak list itself, so there is no separate copy to keep in sync.
- Hovering a legend entry marks that peak list's peaks in the view with dashed lines; double-clicking it zooms to them.
- The legend can be dragged anywhere on the plot and keeps its corner when the window is resized. The legend dialog has a *Reset position* button.
- **⚡ Fill missing symbols** gives a free symbol to every peak list without one (new peak lists get one automatically); **Reassign all symbols…** replaces them all in legend order.
- Adjustable symbol size, and an option to keep unticked peak lists in the on-screen legend, greyed out (never in exports).
- Imported saved labels without a matching peak list are kept as *legend-only* labels.
- Ticks, symbols and columns are restored at the next start if the peak-list file has not changed.

### Processing metadata in output files

Processed spectra now record how they were produced as `#key=value` headers, placed before the original file's headers:

- Baseline: `#baseline_method=airPLS` (`lambda`, `porder`, `itermax`) or `SNIP` (`max_hwidth`, `smooth_iters`)
- Recalibration: `#recalibration_method=auto|manual`, `#recal_polynomial_degree`, `#recal_fitparam_a2/a1/a0`, one `#recal_pair_N=obs->ref` per anchor (manual pairs include `delta`), or `#recal_fallback=linear_scale`
- ToF → mass: `#tof2mass_unit`, `#tof2mass_ref_times`, `#tof2mass_ref_masses`, `#tof2mass_a`, `#tof2mass_b`, `#tof2mass_r2`

### Stacked mode

- **Dyn Scale** checkbox: ON normalises each spectrum to 0–1 (equal heights); OFF uses raw intensities on a shared Y axis so heights are directly comparable. Persisted in settings.
- Changing files, sigma clip, log-Y or colours now updates the existing sub-plots in place, with no rebuild and no zoom reset. A full rebuild happens only when the number of spectra changes.
- Right-click menu: new **Return to last zoom**.
- The crosshair line is propagated across all sub-plots, with a floating m/z label near the cursor.
- The grid toggle is honoured, and space is reserved under the last sub-plot so x-axis labels are never clipped.
- A short overlay notice appears when stacked mode is active with only one file loaded.
- The stacked-mode checkbox state is restored at startup.

### Display and plot

- **Plot → Export as PGF…**: the plot as a PGF picture for LaTeX (`\usepackage{pgf}`, `\input{plot.pgf}`), with native LaTeX text and vector lines. No LaTeX installation is needed.
- A button on the plot title bar hides it; **View → Show Plot Title Bar** brings it back.
- **Plot → Cursor info font** and **View → m/z cursor font size** controls.
- The crosshair on/off state is persisted (off by default).
- The dt filter is persisted across sessions.
- Plot title parsing: the sample name runs from the token after the date up to the flow-rate token (e.g. `0.22mlpmin`), and the main title is set to `<sample>  -  dt<value>`.
- Cluster labels containing `_n` are shown with a subscript ₙ in legends, and per-peak indices in cluster labels follow m/z order.
- Envelope toggle states are saved to settings and restored when no peak file is loaded.
- Spinboxes show values without trailing zeros.

### Batch PNG export

- Only exports files that match the current mode and dt filters.
- A pre-scan finds the global intensity maximum so no file gets Y-cropped (skipped when Dyn Scale is on). The X zoom is kept.
- Progress is shown in the window title (`Exporting i/N: <file>`), which is restored afterwards.

## Bug fixes and robustness

- **NumPy 2 compatibility**: `np.trapz` (removed in NumPy 2) is replaced by `np.trapezoid`, with a fallback for NumPy 1.x.
- **Peak boundary detection**: the valley walk now tolerates up to 3 consecutive rising points, so small noise blips no longer cut a peak short. The left bound is kept symmetric with the right, so shoulders no longer make highlights lop-sided or swallow neighbouring peaks.
- **Shutdown crash**: every pyqtgraph `GraphicsWidgetAnchor` (hover label, legends, area result label, stacked sub-labels) is detached before the ViewBoxes are destroyed. This fixes `RuntimeError` on exit, including when closing in stacked mode.
- Colour changes no longer trigger a full stacked layout rebuild.
- The dt filter is restored after all other initialisation, so it is no longer overwritten at startup.
- Removed unused helpers `_sigma3_floor` and `_normalise_and_clip` (superseded by the unified noise floor from v2.7.2).

## Removed

- The standalone **Plotting Tool** (`Ctrl+Shift+T`); its features are in the main window.
- The Help topic dialogs (Files & Overlays, View & Navigation, …), replaced by Dropli.

---

# v2.7.2

> Correction release — stacked mode rendering, symbol stacking, legend window behaviour, peak area export fixes, peak-finding robustness on Windows, and peak area noise floor consistency.

## Bug fixes

### Stacked mode: flickering and frozen mid-update states

Changing label parameters, toggling envelopes, or using L/1L buttons in stacked mode triggered a full layout rebuild via `render_plot()`. Multiple `app.processEvents()` calls inside `_build_stacked_layout` allowed the 80 ms render timer to fire re-entrantly mid-build, tearing down the partially constructed layout and leaving the plot frozen in a broken intermediate state.

Fixes applied:

- `plot_widget.setUpdatesEnabled(False/True)` wraps the entire build; Qt performs one clean paint after completion instead of painting every intermediate state.
- `processEvents()` calls removed from inside `_build_stacked_layout` — the build is now atomic with respect to the event loop.
- A generation counter (`_stacked_build_gen`) makes deferred nudge-timer closures self-cancel when a newer build has already superseded them, preventing stale nudges from corrupting zoom state.

### Stacked mode: label font, angle, and position controls caused full rebuilds

`label_font_spin`, `label_angle_spin`, and `label_altitude_spin` called `render_plot()` on every change, triggering a full stacked layout rebuild (including destroying and recreating all sub-plots and resetting zoom). They now call `_render_peaks_or_full()`, which routes to `_redraw_stacked_peaks_only()` in stacked mode — only peak/label items are replaced, the layout and zoom are untouched.

### Stacked mode: label position (`label_y_offset`) had no effect

`_draw_stacked_peak_labels` used hardcoded offsets (`0.06` / `1.04`) and never read `label_y_offset` from settings. The setting is now applied with the same formula as normal mode.

### Stacked mode: symbols placed at nearest sample, not peak maximum

Symbol and label y-positions used `int_vals[idx]` — the intensity at the nearest m/z sample index to the target — rather than the actual spectral maximum within the peak bounds. For peaks whose target m/z does not land exactly on a data point, symbols appeared below the true peak tip. Both now use the maximum of `int_vals` within the highlighted window.

### Stacked mode: same-row peaks at the same m/z produced only one symbol

The symbol-drawing loop iterated over `groups`, which deduplicates by label. If a single row had multiple peaks within `MERGE_TOL` of each other, only one symbol was drawn. The symbol stacking now mirrors `_draw_peak_symbols_on_plot` exactly: `_mz_sym_stack` is built one entry per peak per row (no deduplication), so multiple peaks from the same row at the same m/z each produce a separate stacked symbol, as in normal mode.

### Stacked mode: label height did not account for symbol stack count

`sym_count_this` for label y-positioning counted unique labels with symbols in the group. It now reads `len(_gs[1])` from `_mz_sym_stack` — the true total number of symbols stacked at that m/z — so the label always clears the highest symbol regardless of how many are stacked.

### Legend parameters window: always-on-top and raises on focus

`LegendParametersDialog` inherited from `QDialog`, which causes window managers to assign `_NET_WM_WINDOW_TYPE_DIALOG` and treat the window as transient — kept above the parent and raised whenever the parent is focused. Changed to `QWidget` with `Qt.WindowType.Window`, giving it a normal independent window type with no forced stacking or raise-on-focus behaviour. The "Pin on top" toggle in the Window menu still works for explicit always-on-top.

### Legend parameters window: did not close with the main window

The dialog was created with `parent=None`, so it had no Qt parent-child relationship with the main window and survived after the application closed. Now created with `parent=main_win`; Qt destroys child widgets when the parent is destroyed.

## Improvements

### Peak area CSV export: automatic `.csv` extension

`_export_ratios_csv` did not enforce the file extension. If the user omitted it in the save dialog, the file was written without `.csv`. The extension is now appended automatically when missing, matching the behaviour of other export functions.

### Peak area CSV export: noise floor written in file header

`_write_ratios_csv` now computes the 3-sigma clipped noise floor (via `_correct_spectrum`, the same path used for area computation) and writes it as a `#` metadata line before the ratio data:

```
#3_sigma_clip_noise_floor=<value>
```

This applies to both single-file and batch exports.

### Peak area ratio mode: order and persistence

The ratio mode combo box in the Peak Area window previously defaulted to "Between peak lists" on every open. The order is now "Normalized by global spectrum area" first (matching the more common workflow), and the selected mode is saved and restored via QSettings so the preference persists across sessions.

### Peak finding: `scipy.signal.peak_widths` replaced with pure-numpy walk

`find_peak_bounds` previously used `scipy.signal.peak_widths` at `rel_height=1.0` to locate valley-to-valley integration bounds. On Windows with certain NumPy/SciPy builds the fractional-index interpolation returned incorrect bounds, causing peak area integration to yield zero for most peaks.

The function is now a self-contained pure-numpy algorithm:

- Locates all strict local maxima within `±coarse_tol` of the nominal m/z that exceed the noise floor.
- When multiple candidates are found, picks the one closest to the nominal m/z.
- Walks left and right from that maximum, stopping at the first point where the signal starts rising again (valley boundary), preventing bounds from crossing into a neighbouring peak.
- Applies the existing `±max_hw` hard cap in Da.

Behaviour is now identical across all platforms. The `scipy` dependency is unchanged — it is still used elsewhere — but `find_peak_bounds` no longer depends on it.

### Peak area noise floor: inconsistent baseline between manual and batch paths

The batch export path in `PeakAreaWindow._batch_export_folder_ratios` computed the noise floor via `_sigma3_floor(int_all)` — which internally calls `estimate_noise_floor` with `n_sigma=1.0` (not 3.0 as the name implied) and operated on the full spectrum with no m/z threshold. The interactive and single-file ratio paths both used `_correct_spectrum`, which applies `estimate_noise_floor` with `n_sigma=3.0` on data restricted to `mz ≥ 10.9`. This produced different baseline corrections and therefore different integrated areas for the same peaks depending on which path was used.

The batch path now calls `self._correct_spectrum(fdata)` directly, making all three paths (total area, peak-list ratios, batch export) use identical baseline correction.

---

# v2.7.1

> Correction release — fixes and improvements to export and label positioning introduced in v2.7.

## Bug fixes

### Batch PNG export crash

`batch_export_plots` referenced `legend_font_spin`, which was removed in v2.7 when the legend font was moved to the **Legend parameters…** dialog. The function now reads the font size directly from settings (`legend/font_pt`), matching the behaviour of the single-file export functions.

### Spurious second legend in PNG / SVG / PDF exports

When the on-screen `PeakListLegendItem` was active, a second fallback `pg.LegendItem` was still being constructed and injected into the plot during export, producing a duplicate legend not visible in the viewing window. The fallback code path has been removed; exports now only scale the legend that is already on screen.

## Improvements

### Unified export font scaling

Export font sizes for all three text elements now scale by the same factor — the true pixel ratio (`export_width / plot_widget.width()`):

| Element               | v2.7                                        | v2.7.1                    |
| --------------------- | ------------------------------------------- | ------------------------- |
| Spectrum names legend | `screen_pt × (export_width / screen_width)` | unchanged                 |
| Peak list legend      | `screen_pt × 4` (hardcoded)                 | `screen_pt × pixel_ratio` |
| Peak labels           | `BASE_PT × 40` (hardcoded, 10× too large)   | `BASE_PT × pixel_ratio`   |

SVG exports use `pixel_ratio = 1.0` (vector format; the viewer handles scaling).

### Peak label height adapts to stacked symbols

Peak labels are now positioned above the **highest symbol** at each peak, rather than a fixed offset above the raw peak tip. When multiple symbols are stacked at the same m/z, the label rises by `sym_count × symbol_y_offset` — the same offset used to stack the symbols — so there is no overlap regardless of how many symbols are present. When symbols are disabled the behaviour is unchanged.

---

# v2.7

## Peak-list legend

A new **Legend parameters…** dialog provides full control over the on-screen peak-list legend.

### Features

- Live preview updates
- Color-only or Color + Symbol modes
- Configurable fonts, box styling, columns, shadows, rounded corners
- Symbol auto-generation
- Entry reordering
- Persistent saved label library

The legend persists in both normal and stacked modes.

---

## Stacked mode improvements

- Isotopic envelope curves render correctly on stacked plots
- Label-state buttons fully supported in stacked mode
- Correct label y-position handling in log and linear modes

---

## Peak highlight boundary fix

`find_peak_bounds` now uses zero-floor normalised intensity internally, preventing leaking peak boundaries in linear-scale view.

---

## Label y-position fix

Peak labels in normal non-log mode now use linear intensity scaling instead of logarithmic positioning.

---

## Pin-on-top support

All non-modal windows now support:

```text
Window → Pin on top
```

Cross-platform implementation:

- Linux/X11: `wmctrl`
- Windows/macOS: Qt `WindowStaysOnTopHint`

Window preference is saved in QSettings.

---

## Cluster detection redesign

### Improvements

- One row per cluster
- Compact table layout
- Click-to-exclude peak cells
- Improved screen-height handling
- Compact peak-list input table

---

## Plot title bar

Optional compact title bar displaying parsed filename metadata:

```text
date | mode | dt | sample | pt
```

Features:

- Auto-parsed metadata
- Editable fields
- HTML-rendered plot title
- Persistent font size and title settings

---

## Peak label controls

Label font size and angle controls moved into the Peaks window.

---

## Persistent menus

Checkable View/Display menu items no longer close menus after clicking.

---

## Version checking and updater

### Automatic version checking

- Background GitHub version fetch
- Non-intrusive update dialog
- Skip-version support

### `updater.py`

- Backup before updating
- Git or ZIP update modes
- Restart button after update

---

## Test suite

`test_suite.py` now includes:

- GUI mode
- Console mode
- 30+ tests across 9 categories

Coverage includes:

- imports
- algorithms
- I/O
- UI creation
- updater logic
- end-to-end processing

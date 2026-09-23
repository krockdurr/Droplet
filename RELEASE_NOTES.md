# Release Notes

---

# v3.0

> Major release: one-click installers and uninstallers for all platforms, a new project layout, peak-list groups, a peak boundary checker, a peak ratio tool, a residuals preview for manual recalibration, processing metadata in output files, and faster stacked mode.

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

| v2.7.x                         | v3.0                                                  |
| ------------------------------ | ----------------------------------------------------- |
| `Droplet_v2.7.2.py`            | `Droplet.py` (version-independent launcher name)      |
| `droplet/`                     | `droplet_pkg/`                                        |
| `requirements.txt` (`>=` ranges) | `assets/assimilation_guides/requirements.txt` and `requirements_new.txt` (exact pins) |
| `test_suite.py`                | `assets/test/test_suite.py`                           |
| —                              | `assets/icons/` (`.ico`, `.icns`, `.png`)             |

The application version is now read from the `VERSION` file only. `updater.py` backs up `droplet_pkg/`.

**Upgrading from v2.x:** install v3.0 into a new folder with the installer, then delete the old folder. Settings, saved legend labels and peak-list files are kept.

## New features

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

Manual recalibration now opens a non-modal **Residuals Preview** window before saving:

- Per-anchor Δm/z plot, coloured per peak list, with hover read-out.
- Updates live as peaks are toggled in the review window; clicking an anchor scrolls to and highlights its peak row.
- **Mode → Cascade untoggle**: unchecking a peak also unchecks all higher-mass peaks in the same group.
- **⇹ Lines**: vertical reference / detected lines with an arrow, shown only when |Δ| ≥ an adjustable threshold.
- Peaks with the same nominal m/z are kept in sync across groups.
- **Confirm & Save** proceeds; **Cancel** returns to peak review.

### Residuals viewer

- Searches subfolders recursively for `*_residuals_spectrum.txt` files.
- Uses one shared colour map per peak list for the dense curve and the anchor bars, read from new `# peak_list_color:<name>=<hex>` headers in residual files.
- New custom legend: hovering an entry dims the other peak lists.
- Hover label on the anchor bar chart.
- The window can be maximised and snapped to screen edges.

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

- **Plot → Show spectrum names legend** toggle (persisted).
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

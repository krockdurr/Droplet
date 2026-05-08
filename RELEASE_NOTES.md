# Release Notes

---

# v2.7.1

> Correction release — stacked mode rendering, symbol stacking, legend window behaviour, and peak area export fixes.

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

### Batch PNG export crash

`batch_export_plots` referenced `legend_font_spin`, which was removed in v2.7 when the legend font was moved to the **Legend parameters…** dialog. The function now reads the font size directly from settings (`legend/font_pt`), matching the behaviour of the single-file export functions.

### Spurious second legend in PNG / SVG / PDF exports

When the on-screen `PeakListLegendItem` was active, a second fallback `pg.LegendItem` was still being constructed and injected into the plot during export, producing a duplicate legend not visible in the viewing window. The fallback code path has been removed; exports now only scale the legend that is already on screen.

## Improvements

### Peak area CSV export: automatic `.csv` extension

`_export_ratios_csv` did not enforce the file extension. If the user omitted it in the save dialog, the file was written without `.csv`. The extension is now appended automatically when missing, matching the behaviour of other export functions.

### Peak area CSV export: noise floor written in file header

`_write_ratios_csv` now computes the 3-sigma clipped noise floor (via `_correct_spectrum`, the same path used for area computation) and writes it as a `#` metadata line before the ratio data:

```
#3_sigma_clip_noise_floor=<value>
```

This applies to both single-file and batch exports.

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

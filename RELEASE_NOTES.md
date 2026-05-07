# Release Notes

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

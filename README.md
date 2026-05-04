# Droplet

**Interactive viewer for LILBID mass spectrometry data.**

Droplet is a desktop application for loading, visualising, annotating, and processing LILBID (Laser-Induced Liquid Bead Ion Desorption) spectra. It supports peak annotation, baseline correction, automatic and manual mass recalibration, cluster detection, peak area integration, and publication-quality figure export - all in a single window.

---

## Features

| Category                | What it does                                                                                                                   |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Visualisation**       | Linear / log-Y plot, dark and bright themes, stacked multi-spectrum view (with mirror, fit-Y, lock-Y options)                 |
| **File handling**       | Load folders or individual files (`.txt`, `.csv`, `.tsv`, `.dat`, `.asc`); drag-and-drop; virtual folders; recent file history |
| **Overlays**            | Up to 10 simultaneous overlay spectra, each with its own colour, polarity filter, and dt filter                                |
| **Peak annotation**     | Named peak lists with per-row colours, undo/redo, import/export (JSON), click-to-pick mode                                     |
| **Baseline correction** | airPLS (LILBID standard) and SNIP algorithms; single file or batch                                                             |
| **Recalibration**       | Automatic (quadratic TOF polynomial) and manual (user-chosen anchor peaks); single file or batch                               |
| **Normalisation**       | Max-normalise or normalise to a specific m/z; single file or batch                                                             |
| **Batch normalize**     | Normalize entire folders at once                                                                                               |
| **Cluster detection**   | Find regularly-spaced series of peaks (e.g. water/solvent clusters); peak-list mode; click-to-select clusters                 |
| **Peak comparison**     | Highlight common and unique peaks across all visible spectra                                                                   |
| **Peak area**           | Interactive range measurement, total spectrum area, per-peak-list area ratios, batch export, ratio modes                      |
| **Minimap overlay**     | Thumbnail of the full spectrum with a viewport indicator for easy navigation                                                   |
| **Zoom history**        | "Go to last zoom" context menu on the plot                                                                                     |
| **Help system**         | Context-specific help dialogs for each feature area                                                                            |
| **Tutorial**            | Interactive step-by-step tutorial (Help → Start Tutorial)                                                                     |
| **Export**              | PNG, SVG, PDF, CSV; copy to clipboard; print                                                                                   |
| **Plotting tool**       | Separate figure editor (Appearance, Peaks, Annotations, Legend tabs) for publication figures                                   |
| **Residuals viewer**    | Browse Δm/z residual files produced by batch recalibration                                                                     |
| **Session / project**   | Save and restore the full window state, including overlays and peak lists (`.drp` project files)                               |

---

## New in v2.6.2

- **Minimap overlay**: thumbnail of the full spectrum with viewport indicator in the corner of the plot
- **Zoom history**: "Go to last zoom" context menu on the plot
- **Batch normalize**: normalize entire folders at once from the Processing menu
- **Enhanced manual recalibration**: new PeakReviewWindow with grouped peaks, sliders, SNR filtering, and zoom-to-peak button
- **Help system**: context-specific help dialogs for each feature area
- **Tutorial**: interactive step-by-step tutorial (Help → Start Tutorial)
- **Stacked mode enhancements**: mirror, fit-Y, lock-Y options
- **Cluster detection**: peak-list mode, improved UI with click-to-select clusters
- **Peak area**: batch export, ratio modes (between lists / normalized by global area), spectrum header preservation in exported CSV files

---

## Requirements

- Python ≥ 3.10
- PyQt6 ≥ 6.4
- pyqtgraph ≥ 0.13
- NumPy ≥ 1.24
- SciPy ≥ 1.10
- pandas ≥ 2.0
- matplotlib ≥ 3.7

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/<your-username>/Droplet.git
cd Droplet

# 2. (Recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Running

```bash
python Droplet_v2.6.2.py
```

On Windows you can also double-click `Droplet_v2.6.2.py` if Python is associated with `.py` files.

---

## Keyboard shortcuts

| Key                 | Action                            |
| ------------------- | --------------------------------- |
| `Z`                 | Toggle zoom-box mode              |
| `P`                 | Toggle click-to-pick peak mode    |
| `A`                 | Toggle peak area measurement mode |
| `F5`                | Reload current file               |
| `Ctrl+P`            | Open Peaks window                 |
| `Ctrl+Shift+P`      | Add a new peak row                |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo peak edits            |
| `Ctrl+Scroll`       | Cycle through overlays            |
| `Ctrl+↑` / `Ctrl+↓` | Cycle overlays up / down          |
| `Ctrl+Shift+T`      | Open Plotting Tool                |
| `Ctrl+Shift+C`      | Copy plot to clipboard            |
| `Ctrl+Q`            | Quit                              |

---

## File format

Droplet reads two-column text files (m/z  intensity) with any of the separators Tab, Comma, Semicolon, or Space. Comment lines starting with `#` are ignored. The separator is detected automatically, or can be overridden with the **Sep** dropdown.

Filenames are expected to contain `neg` or `pos` for polarity filtering, and optionally `_dt<value>` for dt filtering (e.g. `20240101_sample_neg_dt071.txt`).

---

## Project structure

```
Droplet_v2.6.2.py          Entry point
droplet/
├── app.py                 Main window, menus, render loop, all glue code
├── constants.py           Shared constants (colours, symbols, …)
├── io/
│   ├── file_utils.py      Directory scanning, polarity / dt filtering
│   └── spectrum_reader.py File parsing and writing (with header preservation)
├── processing/
│   ├── baseline.py        airPLS, SNIP, Whittaker (pure algorithms)
│   ├── normalization.py   Normalisation and noise-floor estimation
│   ├── calibration.py     Auto- and manual-recalibration pipelines
│   └── signal.py          Subtraction, tolerance helpers, pen utilities
├── analysis/
│   ├── peaks.py           Peak detection, highlight geometry
│   └── clusters.py        Cluster (regularly-spaced series) detection
└── ui/
    ├── widgets.py          Reusable Qt widget classes
    └── windows/
        ├── residuals_viewer.py
        ├── peak_comparison.py
        ├── peak_area.py
        ├── cluster_detection.py
        ├── tutorial.py
        ├── manual_recal.py    (stub – class in app.py)
        ├── peak_review.py     (stub – class in app.py)
        └── plotting_tool.py   (stub – class in app.py)
```

---

## Algorithms and credits

- **Baseline correction (airPLS)** and **auto-recalibration** algorithms adapted from [LILBID_GUI](https://github.com/mumair5393/LILBID_GUI) by M. Umair (MIT licence).
- Built with Python, PyQt6, pyqtgraph, NumPy, SciPy, pandas, and matplotlib.

---

## Licence

This project is released under the **MIT Licence** - see [`LICENSE`](LICENSE) for details.

The baseline correction (airPLS) and auto-recalibration algorithms were adapted from
[LILBID_GUI](https://github.com/mumair5393/LILBID_GUI) by M. Umair, also MIT-licensed.

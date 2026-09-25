"""Dropli's conversation tree.

Pure data, no Qt.  Each node is:

    "node_id": {
        "say":     [bubble, …],            # Dropli's messages (Qt rich text)
        "options": [(label, next_id), …],  # choices shown to the user
        "do":      [(label, action_key)],  # optional "do it for me" buttons
    }

A node without "options" is an answer: the chat then offers "I have another
question" / "That's all, thanks!" automatically.  Action keys are resolved by
the main window (see ``_dropli_actions`` in app.py); an unknown key is hidden.

Menu paths are written with ``_m("A", "B")`` so they all look the same.
"""


def _m(*parts):
    return "<b>" + " → ".join(parts) + "</b>"


START = "start"
BYE   = "bye"

NODES = {
    # ── Root ──────────────────────────────────────────────────────────────────
    "start": {
        "say": ["Hi, I'm <b>Dropli</b> 💧",
                "What are you trying to do?"],
        "options": [
            ("Open or load my data",           "load"),
            ("Compare spectra",                "compare"),
            ("Process a spectrum",             "process"),
            ("Work with peaks",                "peaks"),
            ("Move around the plot",           "view"),
            ("Save or export something",       "export"),
            ("Something else",                 "other"),
        ],
    },
    "bye": {
        "say": ["Happy analysing! Click me whenever you need me. 👋"],
        "options": [],
    },

    # ── Loading data ──────────────────────────────────────────────────────────
    "load": {
        "say": ["What do you want to open?"],
        "options": [
            ("A whole folder of spectra",      "load_folder"),
            ("A few specific files",           "load_files"),
            ("Something I opened recently",    "load_recent"),
            ("A session I saved before",       "load_project"),
            ("My file doesn't load properly",  "load_problem"),
            ("I only see some of my files",    "load_filters"),
        ],
    },
    "load_folder": {
        "say": [f"Use {_m('File', 'Open Folder…')}",
                "Droplet loads every spectrum file in it (.txt .csv .tsv .dat .asc). "
                "You can also drag and drop files onto the window."],
        "do": [("Open a folder", "open_folder")],
    },
    "load_files": {
        "say": [f"Use {_m('File', 'Open Individual Files…')} and pick the files you "
                "want, from any folder."],
        "do": [("Pick files", "open_files")],
    },
    "load_recent": {
        "say": [f"Look in {_m('File', 'Recent Folders')} or "
                f"{_m('File', 'Recent Files')}."],
    },
    "load_project": {
        "say": [f"Use {_m('File', 'Open Project…')} to get back your files, "
                "overlays, peak lists and view settings."],
        "do": [("Open a project", "open_project")],
    },
    "load_problem": {
        "say": ["Check the <b>Sep</b> dropdown in the Files row: it sets the column "
                "separator. <i>Auto-detect</i> works for most files, but you can force "
                "tab, comma, space…",
                "The ↺ button (or <b>F5</b>) reloads the file from disk."],
    },
    "load_filters": {
        "say": ["The file list is filtered by the <b>Mode</b> (neg / pos / All) and "
                "<b>dt</b> dropdowns, using <i>_neg_</i> / <i>_pos_</i> and "
                "<i>_dtNNN</i> in the file names.",
                "Set both to <b>All</b> to see every file."],
    },

    # ── Comparing spectra ─────────────────────────────────────────────────────
    "compare": {
        "say": ["How do you want to compare them?"],
        "options": [
            ("On top of each other",           "cmp_overlay"),
            ("One per row, stacked",           "cmp_stacked"),
            ("Scaled to the same height",      "cmp_dynscale"),
            ("See the difference between two", "cmp_subtract"),
            ("Find common / unique peaks",     "cmp_common"),
        ],
    },
    "cmp_overlay": {
        "say": ["Click <b>+ Add Overlay</b> under the main file and pick a spectrum. "
                "Each overlay has its own mode, dt and colour.",
                "The <b>Overlay opacity</b> slider (or <b>Ctrl+Scroll</b>) sets how "
                "transparent they are."],
        "do": [("Add an overlay", "add_overlay")],
    },
    "cmp_stacked": {
        "say": [f"Turn on {_m('View', 'Stacked Spectra Mode')} (or the <b>Stacked</b> "
                "checkbox). Each spectrum gets its own row, scaled 0–1, with a shared "
                "m/z axis.",
                "<b>Log Y</b> next to it switches all rows to a log scale."],
        "do": [("Turn on stacked mode", "stacked_on")],
    },
    "cmp_dynscale": {
        "say": [f"Turn on {_m('View', 'Dynamic Scale')} (or <b>Dyn Scale</b> in the "
                "toolbar). All spectra are scaled to the same tallest peak, with noise "
                "clipped so it doesn't skew the scaling."],
        "do": [("Turn on Dynamic Scale", "dynscale_on")],
    },
    "cmp_subtract": {
        "say": [f"Add an overlay, then turn on {_m('View', 'Subtract Overlay')}: the plot "
                "shows main − first overlay.",
                "<b>Dynamic Subtraction</b> scales both to the same height first."],
        "do": [("Turn on subtraction", "subtract_on")],
    },
    "cmp_common": {
        "say": [f"Use {_m('Analysis', 'Compare Common/Unique Peaks…')} It detects peaks "
                "in every visible spectrum: green lines = found in all of them, dashed "
                "coloured lines = found in only one."],
        "do": [("Open peak comparison", "peak_comparison")],
    },

    # ── Processing ────────────────────────────────────────────────────────────
    "process": {
        "say": ["What do you want to do to the spectrum?"],
        "options": [
            ("Remove the baseline",            "proc_baseline"),
            ("Correct the mass calibration",   "proc_recal"),
            ("Normalise intensities",          "proc_norm"),
            ("Hide noise (display only)",      "proc_sigma"),
            ("Convert time-of-flight to mass", "proc_tof"),
        ],
    },
    "proc_baseline": {
        "say": ["One spectrum or a whole folder?"],
        "options": [
            ("The spectrum I'm looking at",    "baseline_current"),
            ("A whole folder",                 "baseline_batch"),
            ("airPLS or SNIP?",                "baseline_method"),
        ],
    },
    "baseline_current": {
        "say": [f"Use {_m('Processing', 'Baseline Correction', 'Apply to Current Spectrum')}.",
                "The result appears as an overlay with a 💾 button to save it."],
        "do": [("Correct the baseline", "baseline_current")],
    },
    "baseline_batch": {
        "say": [f"Use {_m('Processing', 'Baseline Correction', 'Batch Process Folder…')} "
                "You can spread the work over several CPU cores."],
        "do": [("Start a batch", "baseline_batch")],
    },
    "baseline_method": {
        "say": ["<b>airPLS</b> is the LILBID standard, a smooth adaptive fit.",
                "<b>SNIP</b> is the classic for atomic MS: it clips peaks away "
                "iteratively.",
                f"Choose in {_m('Processing', 'Baseline Correction', 'Method')}."],
    },
    "proc_recal": {
        "say": ["Do you want Droplet to find the calibrant peaks itself, or do you want "
                "to choose them?"],
        "options": [
            ("Automatically",                  "recal_auto"),
            ("I'll choose the peaks",          "recal_manual"),
            ("Baseline and calibration at once", "recal_both"),
            ("Check how good a calibration is", "recal_residuals"),
        ],
    },
    "recal_auto": {
        "say": ["Droplet detects peaks, matches them to known LILBID calibrant ions and "
                "fits a TOF polynomial. A review dialog flags anything uncertain.",
                f"One spectrum: {_m('Processing', 'Recalibration', 'Auto-Recalibrate Current')}.",
                f"A folder: {_m('Processing', 'Recalibration', 'Batch Auto-Recalibrate…')}"],
        "do": [("Auto-recalibrate this spectrum", "auto_recal"),
               ("Batch auto-recalibrate", "auto_recal_batch")],
    },
    "recal_manual": {
        "say": ["You pick which peak-list rows are the anchors; Droplet finds each "
                "peak, and you can adjust or exclude them before applying.",
                f"One spectrum: {_m('Processing', 'Recalibration', 'Manual Recalibrate Current…')}",
                f"A folder, file by file: {_m('Processing', 'Recalibration', 'Batch Manual Recalibrate…')}"],
        "do": [("Manually recalibrate this spectrum", "manual_recal"),
               ("Batch manual recalibration", "manual_recal_batch")],
    },
    "recal_both": {
        "say": [f"Use {_m('Processing', 'Baseline + Recalibration')}: the baseline is "
                "removed first, then the spectrum is auto-recalibrated."],
        "do": [("Do both on this spectrum", "both_current"),
               ("Do both on a folder", "both_batch")],
    },
    "recal_residuals": {
        "say": ["Every recalibration saves its residuals (how far each anchor moved) in "
                "a <i>Residuals dd.mm.yyyy - hh.mm.ss</i> folder next to the output.",
                f"Browse them with {_m('Processing', 'View Recalibration Residuals…')}"],
        "do": [("Open the residuals viewer", "view_residuals")],
    },
    "proc_norm": {
        "say": [f"Use {_m('Processing', 'Normalize Spectrum')}: the tallest peak becomes "
                "1, or a m/z of your choice becomes 1.",
                "The result appears as an overlay with a 💾 button. A batch version "
                "does a whole folder."],
        "do": [("Normalise this spectrum", "normalize_current"),
               ("Normalise a folder", "normalize_batch")],
    },
    "proc_sigma": {
        "say": ["Tick <b>σ Clip</b> in the toolbar. It raises the noise floor on screen "
                "without touching your data; the slider next to it goes from 1 σ "
                "(clips more) to 4 σ (keeps more)."],
        "do": [("Turn on σ Clip", "sigma_on")],
    },
    "proc_tof": {
        "say": [f"Use {_m('Processing', 'ToF → Mass Converter…')} Give it a few known "
                "time/mass pairs; it can also convert a whole folder."],
        "do": [("Open the converter", "tof_to_mass")],
    },

    # ── Peaks ─────────────────────────────────────────────────────────────────
    "peaks": {
        "say": ["What do you want to do with peaks?"],
        "options": [
            ("Highlight / label known masses", "pk_list"),
            ("Add peaks by clicking on them",  "pk_pick"),
            ("Label every peak above a threshold", "pk_threshold"),
            ("Find repeating clusters",        "pk_clusters"),
            ("Measure an area or a ratio",     "pk_area"),
            ("Save or reuse a peak list",      "pk_saveload"),
        ],
    },
    "pk_list": {
        "say": [f"Open the peak list with {_m('Peaks', 'Open Peaks List')} "
                "(<b>Ctrl+P</b>). Each row is a set of m/z values with a colour and a "
                "label; type values separated by commas, or use range mode (≡).",
                "⌒ on a row joins the peak tops, handy for checking an isotopic envelope."],
        "do": [("Open the peak list", "peaks_window")],
    },
    "pk_pick": {
        "say": [f"Turn on {_m('Peaks', 'Pick Peaks Mode')} (<b>P</b>). "
                "Single-click adds the nearest peak to the selected row; double-click "
                "removes it."],
        "do": [("Turn on pick mode", "pick_on")],
    },
    "pk_threshold": {
        "say": ["In the peak-list window, tick <b>Show masses above threshold</b>. "
                "The threshold can be a % of the tallest peak, or a signal-to-noise "
                "ratio (SNR).",
                "<b>Auto-detect peaks</b> marks the same peaks with red triangles."],
        "do": [("Open the peak list", "peaks_window")],
    },
    "pk_clusters": {
        "say": [f"Use {_m('Analysis', 'Cluster Detection…')} It finds peak series with "
                "a fixed spacing: leave the spacing empty to let it discover them, or "
                "enter the spacings you're looking for.",
                "Found clusters can be added straight to your peak lists."],
        "do": [("Open cluster detection", "cluster_detection")],
    },
    "pk_area": {
        "say": ["Which one?"],
        "options": [
            ("The area of one peak / range",   "area_range"),
            ("A ratio between two peaks",      "area_ratio"),
            ("Areas of whole peak lists",      "area_lists"),
        ],
    },
    "area_range": {
        "say": ["Press <b>A</b> with the plot focused (or use the area tools window), "
                "then click twice on the plot to set the range. The area (trapezoidal "
                "rule) shows up right away."],
        "do": [("Turn on area mode", "area_mode_on")],
    },
    "area_ratio": {
        "say": [f"Use {_m('Analysis', 'Measure Peak Ratio')} (<b>R</b>)."],
        "do": [("Measure a ratio", "ratio_mode")],
    },
    "area_lists": {
        "say": [f"Use {_m('Analysis', 'Peak list area tools…')}: the area under each "
                "toggled peak list, their ratios, and the total spectrum area."],
        "do": [("Open area tools", "area_tools")],
    },
    "pk_saveload": {
        "say": ["In the peak-list window, the menu can save, import and export peak "
                "lists as JSON files, ranges included."],
        "do": [("Open the peak list", "peaks_window")],
    },

    # ── View / navigation ─────────────────────────────────────────────────────
    "view": {
        "say": ["What would help?"],
        "options": [
            ("Zoom in and out",                "view_zoom"),
            ("Keep my zoom when I change file", "view_lock"),
            ("Read the m/z under my mouse",    "view_cursor"),
            ("Dark or bright plot",            "view_theme"),
            ("Keyboard shortcuts",             "view_shortcuts"),
        ],
    },
    "view_zoom": {
        "say": ["Scroll to zoom and drag to pan. For a precise zoom, press <b>Z</b> and "
                "draw a box.",
                "Right-click the plot → <b>Go to last zoom</b> steps back through your "
                "zooms."],
        "do": [("Turn on zoom box", "zoom_box")],
    },
    "view_lock": {
        "say": [f"Turn on {_m('View', 'Lock Axes')}: the zoom stays put when you switch "
                "files."],
        "do": [("Lock the axes", "lock_axes_on")],
    },
    "view_cursor": {
        "say": [f"{_m('View', 'Show m/z at Cursor')} shows the m/z next to your mouse; "
                f"{_m('View', 'Cross-lines')} adds a crosshair with m/z and intensity."],
        "do": [("Show m/z at cursor", "mz_cursor_on")],
    },
    "view_theme": {
        "say": [f"Switch between dark and bright in the {_m('Display')} menu."],
        "do": [("Dark", "dark"), ("Bright", "bright")],
    },
    "view_shortcuts": {
        "say": ["The main ones: <b>Z</b> zoom box · <b>P</b> pick peaks · <b>A</b> "
                "peak area · <b>Ctrl+P</b> peak list · <b>F5</b> reload · "
                "<b>Ctrl+Shift+C</b> copy plot · <b>Ctrl+Q</b> quit."],
        "do": [("See all shortcuts", "shortcuts")],
    },

    # ── Saving / exporting ────────────────────────────────────────────────────
    "export": {
        "say": ["What do you want to save?"],
        "options": [
            ("An image of the plot",           "exp_image"),
            ("Images of many spectra at once", "exp_batch"),
            ("A processed spectrum",           "exp_spectrum"),
            ("The peak positions as a table",  "exp_csv"),
            ("My whole session",               "exp_project"),
            ("Print the plot",                 "exp_print"),
        ],
    },
    "exp_image": {
        "say": ["For a document: which format?"],
        "options": [
            ("PNG picture",                    "exp_png"),
            ("Vector (SVG / PDF)",             "exp_vector"),
            ("LaTeX (PGF)",                    "exp_pgf"),
            ("Just paste it somewhere",        "exp_clipboard"),
        ],
    },
    "exp_png": {
        "say": [f"Use {_m('Plot', 'Export as PNG…')} It saves the plot exactly as you "
                "see it, so resize the window to change the image size."],
        "do": [("Export a PNG", "export_png")],
    },
    "exp_vector": {
        "say": [f"Use {_m('Plot', 'Export as SVG…')} or {_m('Plot', 'Export as PDF…')}: "
                "the same view, but it stays sharp at any size."],
        "do": [("Export SVG", "export_svg"), ("Export PDF", "export_pdf")],
    },
    "exp_pgf": {
        "say": [f"Use {_m('Plot', 'Export as PGF…')}, then in LaTeX: "
                "<tt>\\usepackage{pgf}</tt> and <tt>\\input{plot.pgf}</tt>. The text "
                "is typeset in your document's font."],
        "do": [("Export PGF", "export_pgf")],
    },
    "exp_clipboard": {
        "say": [f"{_m('Plot', 'Copy Plot to Clipboard')} (<b>Ctrl+Shift+C</b>), then "
                "paste wherever you like."],
        "do": [("Copy the plot now", "copy_plot")],
    },
    "exp_batch": {
        "say": [f"Use {_m('Plot', 'Batch Export Plots…')} It exports every file visible "
                "under the Mode / dt filters with your current zoom, as PNG, PDF, SVG or "
                "PGF."],
        "do": [("Start a batch export", "batch_export")],
    },
    "exp_spectrum": {
        "say": ["Processed spectra (baseline, recalibrated, normalised) appear as "
                "overlays: click their 💾 button to save them.",
                "Batch processing saves straight into the folder you choose."],
    },
    "exp_csv": {
        "say": ["I can save every highlighted peak, with its m/z and intensity, as a "
                "CSV table for you."],
        "do": [("Export the peak table", "export_peaks_csv")],
    },
    "exp_project": {
        "say": [f"Use {_m('File', 'Save Project')}: files, overlays, peak lists and view "
                f"settings, reopened later with {_m('File', 'Open Project…')}"],
        "do": [("Save the project", "save_project")],
    },
    "exp_print": {
        "say": [f"{_m('Plot', 'Print…')} (<b>Ctrl+Shift+P</b>) prints the plot in "
                "landscape."],
        "do": [("Print", "print")],
    },

    # ── Other ─────────────────────────────────────────────────────────────────
    "other": {
        "say": ["Sure, what is it?"],
        "options": [
            ("Show me around Droplet",         "oth_tour"),
            ("Something isn't working",        "oth_broken"),
            ("Get the newest version",         "oth_update"),
            ("Use an older version",           "oth_previous"),
            ("What is Droplet?",               "oth_about"),
        ],
    },
    "oth_tour": {
        "say": ["The guided tour points at each part of the window, step by step."],
        "do": [("Start the tour", "tutorial")],
    },
    "oth_broken": {
        "say": [f"Run {_m('Tests', 'Run Test Suite…')}: it checks that Droplet works on "
                "this computer, and the report can be saved and sent with your problem "
                "description.",
                "Also make sure you have the latest version."],
        "do": [("Run the tests", "run_tests"), ("Check for updates", "check_updates")],
    },
    "oth_update": {
        "say": [f"Use {_m('Help', 'Check for Updates…')}"],
        "do": [("Check for updates", "check_updates")],
    },
    "oth_previous": {
        "say": [f"{_m('Help', 'Previous Versions…')} installs an older version next to "
                "this one, so you can reproduce old results."],
        "do": [("Open previous versions", "previous_versions")],
    },
    "oth_about": {
        "say": ["Droplet is a viewer for LILBID mass spectrometry data: spectra, peak "
                "annotation, baseline correction, recalibration, cluster detection and "
                "peak areas."],
        "do": [("About Droplet", "about")],
    },
}


def validate():
    """Return a list of problems (dangling links) — used by the test suite."""
    problems = []
    for nid, node in NODES.items():
        for _label, target in node.get("options", []):
            if target not in NODES:
                problems.append(f"{nid} → {target}: no such node")
    return problems

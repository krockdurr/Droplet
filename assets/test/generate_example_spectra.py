#!/usr/bin/env python3
"""
Generate the synthetic LILBID example spectra in assets/example_spectra/.

    python assets/test/generate_example_spectra.py

The files mimic real LILBID-MS exports: 36 000 points, a quadratic
time-of-flight → mass axis (m = a·t² + b·t + c, t = index × 2 ns, so the
axis first dips then rises to ≈ 445 Da), intensities in volts with a
digitizer floor, laser/ion ringing at the very start, slightly tailing
peaks ≈ 7 points wide, and the LILBID filename conventions
(<date>-<time>_<sample>_<pos|neg>_des_I<n>_dt<delay>).

Every peak is placed at its exact monoisotopic mass on the *true* axis.
"Raw" files are written with a slightly wrong mass axis (as before
recalibration); "recalibrated" files use the true axis.  The ground truth
for each file is written to expected_values.json and is what the test
suite checks against.  The random generator is seeded, so running this
script again reproduces the same files.
"""

import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "example_spectra"

N_POINTS = 36_000
TIMESTEP = 2e-3                       # same as calibration.AR_TIMESTEP
T = np.arange(N_POINTS) * TIMESTEP
TRUE_AXIS = (0.0878, -0.140, 0.90)    # a, b, c of the true t → m polynomial
DIGITIZER_FLOOR = 0.007               # V, real exports never go below this

# Monoisotopic building blocks (Da)
H2O, H = 18.010565, 1.007276                   # water, proton
NA, CL35, CL37 = 22.989221, 34.968853, 36.965903   # Na+ ion, Cl atoms
NACL = 22.989769 + CL35                            # neutral NaCl unit, 57.9586

rng = np.random.default_rng(20260115)
trapezoid = getattr(np, "trapezoid", None) or np.trapz   # NumPy < 2 has only trapz


# ── spectrum building blocks ─────────────────────────────────────────────────

def axis(a, b, c):
    return a * T**2 + b * T + c


def index_of_mass(m, params=TRUE_AXIS):
    """Fractional point index at which mass m sits on the rising branch."""
    a, b, c = params
    t = (-b + np.sqrt(b * b - 4 * a * (c - m))) / (2 * a)
    return t / TIMESTEP


def fwhm_points(m):
    return 6.5 + 0.012 * m            # real spectra: ≈ 6.5 pts at 100, 9 at 200


def add_peak(y, m, height, tail=1.3, width_scale=1.0):
    """Add a peak of apex `height` at mass m (asymmetric Gaussian in index space)."""
    j0 = index_of_mass(m)
    sigma = fwhm_points(m) * width_scale / 2.3548
    lo, hi = int(j0 - 8 * sigma), int(j0 + 8 * tail * sigma) + 2
    j = np.arange(max(lo, 0), min(hi, N_POINTS))
    s = np.where(j < j0, sigma, sigma * tail)
    y[j] += height * np.exp(-0.5 * ((j - j0) / s) ** 2)


def add_isotopes(y, m, height, n_o=0, n_cl=0, **kw):
    """Peak plus its isotopes: 18O (M+2) for n_o oxygens, 35/37Cl for n_cl chlorines."""
    add_peak(y, m, height, **kw)
    if n_o:
        add_peak(y, m + 1.003, height * 0.0005 * n_o, **kw)     # 17O / 2H
        add_peak(y, m + 2.004, height * 0.00205 * n_o, **kw)    # 18O
    if n_cl:
        from math import comb
        p37 = 0.2424
        base = (1 - p37) ** n_cl
        for k in range(1, n_cl + 1):
            rel = comb(n_cl, k) * p37**k * (1 - p37) ** (n_cl - k) / base
            add_peak(y, m + k * (CL37 - CL35), height * rel, **kw)


def instrument_background(level=0.0078, hump=0.0):
    """Digitizer offset + slow drift + optional broad unresolved hump + ringing."""
    m = axis(*TRUE_AXIS)
    y = np.full(N_POINTS, level)
    y += 0.0012 * np.sin(T / 9.0) * np.exp(-T / 40)          # slow drift
    if hump:
        y += hump * np.exp(-0.5 * ((m - 160) / 70) ** 2)      # cluster continuum
    ring = np.exp(-np.arange(N_POINTS) / 450.0)               # early ringing
    y += 0.09 * ring * np.abs(np.sin(np.arange(N_POINTS) / 3.1))
    y += ring * rng.normal(0, 0.03, N_POINTS)
    return y


def add_chemical_noise(y, count, max_height, avoid_masses, lo=12, hi=440):
    """Small unassigned peaks ("grass"), kept ≥ 1.5 Da away from `avoid_masses`
    so they never compete with calibrants or the peaks listed as ground truth."""
    avoid = np.asarray(sorted(avoid_masses))
    placed = 0
    while placed < count:
        m = rng.uniform(lo, hi)
        if avoid.size and np.min(np.abs(avoid - m)) < 1.5:
            continue
        add_peak(y, m, max_height * 10 ** rng.uniform(-1.3, 0))
        placed += 1


def add_noise(y, sigma):
    y += rng.normal(0, sigma, N_POINTS)
    y += rng.normal(0, 1, N_POINTS) * np.sqrt(np.clip(y, 0, None)) * 0.004
    return y


def write_txt(path, mz, y, headers=(), sep="\t", column_names=None):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for h in headers:
            fh.write(h + "\n")
        if column_names:
            fh.write(sep.join(column_names) + "\n")
        for m, v in zip(mz, y):
            fh.write(f"{m:.6f}{sep}{v:.7f}\n")


def processed_headers(tag, recal_pairs, extra=()):
    """Headers in the same format Droplet writes for batch-processed files."""
    a, b, c = TRUE_AXIS
    hs = [f"#processed={tag}",
          "#baseline_method=airPLS", "#baseline_lambda=100",
          "#baseline_porder=1", "#baseline_itermax=20",
          "#recalibration_method=auto", "#recal_polynomial_degree=2",
          f"#recal_fitparam_a2={a:.10g}", f"#recal_fitparam_a1={b:.10g}",
          f"#recal_fitparam_a0={c:.10g}"]
    for k, (obs, ref) in enumerate(recal_pairs, 1):
        hs.append(f"#recal_pair_{k}={obs:.4f}->{ref:.4f} (delta={ref - obs:+.4f})")
    return hs + list(extra) + ["##########"]


# ── the five spectra ─────────────────────────────────────────────────────────

# Calibrant tables, identical to droplet_pkg/processing/calibration.py
IONS_NEG = [17.0033, 35.0139, 43.0189, 46.0060, 53.0244, 71.0350, 89.0455,
            107.0561, 125.0667, 143.0772, 161.0878, 179.0984, 197.1089, 215.1195]
IONS_POS_WATER = [19.0178, 37.0284, 55.0390, 73.0495, 91.0601, 109.0707,
                  127.0812, 145.0918, 163.1024, 181.1129, 199.1235, 217.1341,
                  235.1446]
IONS_POS_SODIUM = [22.9898, 41.0003, 59.0109, 77.0215, 95.0320, 113.0426,
                   131.0532, 149.0637, 167.0743, 185.0849, 203.0954, 221.1060]
IONS_POS_MINOR = [18.0338, 30.0338, 36.0444, 43.0178, 60.0570]


def cluster_heights(n_values, n_max, width, peak_height):
    """Log-normal-ish cluster size distribution, like LILBID water clusters."""
    n = np.asarray(n_values, float) + 1
    return peak_height * np.exp(-0.5 * (np.log(n / (n_max + 1)) / width) ** 2)


def spectrum_water_neg():
    """1 · raw negative-mode water calibrant (auto-recal, baseline)."""
    y = instrument_background(hump=0.012)
    water_series = [17.0033 + k * H2O for k in range(0, 24)]        # up to ≈ 431
    heights = cluster_heights(range(24), n_max=4, width=0.55, peak_height=0.46)
    heights[0] = 0.52                                                # OH- itself
    peaks = []
    for k, (m, h) in enumerate(zip(water_series, heights)):
        add_isotopes(y, m, h, n_o=k + 1)
        peaks.append({"mz": round(m, 4), "height": round(float(h), 4),
                      "ion": f"OH-(H2O){k}"})
    for m, h, name in [(43.0189, 0.085, "table ion 43"), (46.0060, 0.06, "table ion 46")]:
        add_peak(y, m, h)
        peaks.append({"mz": m, "height": h, "ion": name})
    add_chemical_noise(y, 90, 0.03, [p["mz"] for p in peaks] + IONS_NEG)
    y = np.clip(add_noise(y, 0.0005), DIGITIZER_FLOOR, None)
    # Raw axis: off by -0.06 … -0.35 Da (grows with flight time), like an
    # uncalibrated acquisition.
    mz = axis(*TRUE_AXIS) - (0.06 + 0.0040 * T)
    return mz, y, sorted(peaks, key=lambda p: p["mz"])


def spectrum_water_pos(sodium_scale=1.0, n_max=5, width=0.6, raw=True):
    """Positive-mode water clusters H+(H2O)n and Na+(H2O)n."""
    y = instrument_background(hump=0.018 if raw else 0.0)
    peaks = []
    water = [19.0178 + k * H2O for k in range(0, 24)]
    wh = cluster_heights(range(24), n_max=n_max, width=width, peak_height=0.58)
    for k, (m, h) in enumerate(zip(water, wh)):
        add_isotopes(y, m, h, n_o=k + 1)
        peaks.append({"mz": round(m, 4), "height": round(float(h), 4),
                      "ion": f"H+(H2O){k + 1}"})
    sodium = [22.9898 + k * H2O for k in range(0, 23)]
    sh = cluster_heights(range(23), n_max=n_max - 1, width=width,
                         peak_height=0.30 * sodium_scale)
    for k, (m, h) in enumerate(zip(sodium, sh)):
        add_isotopes(y, m, h, n_o=k)
        peaks.append({"mz": round(m, 4), "height": round(float(h), 4),
                      "ion": f"Na+(H2O){k}"})
    for m, h in zip(IONS_POS_MINOR, [0.07, 0.03, 0.05, 0.04, 0.035]):
        add_peak(y, m, h)
        peaks.append({"mz": m, "height": h, "ion": "table ion"})
    add_chemical_noise(y, 90, 0.03, [p["mz"] for p in peaks]
                       + IONS_POS_WATER + IONS_POS_SODIUM + IONS_POS_MINOR)
    y = np.clip(add_noise(y, 0.0005), DIGITIZER_FLOOR, None)
    mz = axis(*TRUE_AXIS)
    if raw:
        mz = mz + (0.08 + 0.0045 * T)       # +0.08 … +0.40 Da
    return mz, y, sorted(peaks, key=lambda p: p["mz"])


def spectrum_nacl():
    """3 · baseline-corrected + recalibrated NaCl solution (clusters, envelopes)."""
    y = np.zeros(N_POINTS)
    peaks, envelopes = [], []
    for n, h in zip(range(0, 8), [0.62, 0.55, 0.40, 0.29, 0.20, 0.13, 0.08, 0.05]):
        m = NA + n * NACL
        add_isotopes(y, m, h, n_cl=n)
        peaks.append({"mz": round(m, 4), "height": h, "ion": f"Na+(NaCl){n}"})
        if n >= 1:
            envelopes.append({"mz": round(m, 4), "n_cl": n,
                              "spacing": round(CL37 - CL35, 4)})
    for k, h in zip(range(1, 12), [0.16, 0.21, 0.19, 0.15, 0.11, 0.08, 0.06,
                                   0.045, 0.03, 0.02, 0.015]):
        m = NA + k * H2O
        add_isotopes(y, m, h, n_o=k)
        peaks.append({"mz": round(m, 4), "height": h, "ion": f"Na+(H2O){k}"})
    for k, h in zip(range(1, 6), [0.06, 0.09, 0.07, 0.05, 0.03]):
        m = H + k * H2O
        add_isotopes(y, m, h, n_o=k)
        peaks.append({"mz": round(m, 4), "height": h, "ion": f"H+(H2O){k}"})
    add_chemical_noise(y, 60, 0.02, [p["mz"] for p in peaks]
                       + [e["mz"] + k * e["spacing"] for e in envelopes
                          for k in range(e["n_cl"] + 1)])
    # Residual noise around zero after airPLS; small negative excursions stay.
    y = add_noise(y, 0.0006)
    y[:2500] += np.exp(-np.arange(2500) / 450.0) * rng.normal(0, 0.02, 2500)
    return axis(*TRUE_AXIS), y, sorted(peaks, key=lambda p: p["mz"]), envelopes


def spectrum_peak_area_standard():
    """4 · baseline-corrected + recalibrated peak area / ratio / SNR standard."""
    noise = 0.001
    y = np.zeros(N_POINTS)
    m_true = axis(*TRUE_AXIS)
    groups = {}

    def peak(group, m, h, **kw):
        add_peak(y, m, h, tail=1.0, **kw)          # symmetric → exact areas
        groups.setdefault(group, []).append({"mz": m, "height": h})

    peak("reference", 17.0033, 0.60)
    peak("ratio 2:1", 71.0350, 0.40); peak("ratio 2:1", 89.0455, 0.20)
    peak("ratio 1:1", 107.0561, 0.30); peak("ratio 1:1", 125.0667, 0.30)
    peak("ratio 10:1", 143.0772, 0.50); peak("ratio 10:1", 161.0878, 0.05)
    peak("doublet 0.20 Da", 179.10, 0.30); peak("doublet 0.20 Da", 179.30, 0.15)
    peak("doublet 0.40 Da", 215.10, 0.25); peak("doublet 0.40 Da", 215.50, 0.25)
    for k, snr in enumerate([3, 5, 10, 20, 50]):
        peak("SNR ladder", 250.0 + 10 * k, round(snr * noise, 4))
    peak("broad", 350.0, 0.10, width_scale=12.0)

    # Exact areas (V·Da) of each isolated Gaussian, integrated on the m/z axis.
    for items in groups.values():
        for p in items:
            single = np.zeros(N_POINTS)
            ws = 12.0 if p["mz"] == 350.0 else 1.0
            add_peak(single, p["mz"], p["height"], tail=1.0, width_scale=ws)
            p["area"] = round(float(trapezoid(single, m_true)), 6)
    y = y + rng.normal(0, noise, N_POINTS)
    return m_true, y, groups, noise


# ── peak lists (Droplet's JSON format v2) ────────────────────────────────────

def range_row(label, start, end, step, color, group):
    return {"checked": True, "color": color, "label": label, "mode": "range",
            "range_start": start, "range_end": end, "range_step": step,
            "L_state": 0, "1L_state": 0, "envelope_btn": False, "group": group}


def series_end(start, step, n):
    """End value of a range row covering start … start + n·step."""
    return round(start + n * step + step / 2, 4)


def list_row(label, peaks, color, group):
    return {"checked": True, "color": color, "label": label, "mode": "manual",
            "peaks": ", ".join(f"{p:g}" for p in peaks),
            "L_state": 0, "1L_state": 0, "envelope_btn": False, "group": group}


def peak_list(groups, rows):
    return {"format_version": 2,
            "groups": [{"name": g, "alpha": 255, "collapsed": False} for g in groups],
            "rows": rows}


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    expected = {"_about": "Ground truth for the example spectra; generated by "
                          "assets/test/generate_example_spectra.py",
                "true_axis": dict(zip("abc", TRUE_AXIS)), "timestep": TIMESTEP,
                "files": {}}

    # 1 · raw neg calibrant
    name = "2026-01-15-101500_Water-calibrant_neg_des_I1152_dt080.txt"
    mz, y, peaks = spectrum_water_neg()
    write_txt(OUT / name, mz, y)
    expected["files"][name] = {
        "purpose": "raw negative-mode spectrum: auto-recalibration and baseline correction",
        "polarity": "neg", "dt": "080", "calibrated": False, "baseline_corrected": False,
        "axis_error_da": "true m/z = shown m/z + 0.06 + 0.0040·t  (-0.12 Da at 17, -0.35 Da at 431)",
        "calibrants": IONS_NEG, "peaks": peaks}

    # 2 · raw pos calibrant
    name = "2026-01-15-102200_Water-calibrant_pos_des_I1152_dt080.txt"
    mz, y, peaks = spectrum_water_pos(raw=True)
    write_txt(OUT / name, mz, y)
    expected["files"][name] = {
        "purpose": "raw positive-mode spectrum: auto-recalibration, baseline (broad hump)",
        "polarity": "pos", "dt": "080", "calibrated": False, "baseline_corrected": False,
        "axis_error_da": "true m/z = shown m/z - 0.08 - 0.0045·t  (+0.15 Da at 19, +0.40 Da at 445)",
        "calibrants": IONS_POS_WATER + IONS_POS_SODIUM, "peaks": peaks}

    # 3 · NaCl clusters, processed
    name = ("2026-01-15-103000_NaCl-10mM_pos_des_I1152_dt070"
            "_baseline+recalibrated.txt")
    mz, y, peaks, envelopes = spectrum_nacl()
    pairs = [(p["mz"] - (0.10 + 0.0016 * p["mz"]), p["mz"]) for p in peaks
             if p["ion"].startswith("Na+(H2O)")][:6]
    write_txt(OUT / name, mz, y, headers=processed_headers(
        "baseline+recalibrated", pairs,
        extra=["#sample=NaCl 10 mM in water (synthetic example)"]))
    expected["files"][name] = {
        "purpose": "processed positive-mode spectrum: cluster series, Cl isotope "
                   "envelopes, processed-file headers",
        "polarity": "pos", "dt": "070", "calibrated": True, "baseline_corrected": True,
        "cluster_spacings": {"NaCl": round(NACL, 4), "H2O": round(H2O, 4),
                             "37Cl-35Cl": round(CL37 - CL35, 4)},
        "envelopes": envelopes, "peaks": peaks}

    # 4 · peak area standard
    name = ("2026-01-15-104000_Peak-area-standard_neg_des_I1152_dt080"
            "_baseline+recalibrated.txt")
    mz, y, groups, noise = spectrum_peak_area_standard()
    write_txt(OUT / name, mz, y, headers=processed_headers(
        "baseline+recalibrated", [(17.0033 - 0.12, 17.0033), (71.035 - 0.18, 71.035)],
        extra=["#sample=peak area / ratio / SNR standard (synthetic example)"]))
    expected["files"][name] = {
        "purpose": "processed negative-mode standard: peak areas, intensity ratios, "
                   "overlapping doublets, SNR detection limits",
        "polarity": "neg", "dt": "080", "calibrated": True, "baseline_corrected": True,
        "noise_sigma": noise, "groups": groups,
        "note": "peaks are symmetric Gaussians; 'area' is the exact integral in V·Da "
                "of each peak on its own (doublet members overlap in the file)"}

    # 5 · pos, later delay time, CSV export with column names
    name = "2026-01-15-105000_Water-calibrant_pos_des_I1152_dt120_recalibrated.csv"
    mz, y, peaks = spectrum_water_pos(raw=False, n_max=9, width=0.5, sodium_scale=0.4)
    write_txt(OUT / name, mz, y, sep=",", column_names=("m/z", "intensity"),
              headers=["#processed=recalibrated", "#recalibration_method=auto",
                       "#sample=pure water, later delay time (synthetic example)"])
    expected["files"][name] = {
        "purpose": "same sample as file 2 at a longer delay time: comma-separated with a "
                   "column-name row, dt filter, overlay / comparison with file 2 "
                   "(cluster distribution shifts to larger n)",
        "polarity": "pos", "dt": "120", "calibrated": True, "baseline_corrected": False,
        "peaks": peaks}

    (OUT / "expected_values.json").write_text(json.dumps(expected, indent=1))

    # Peak lists users can load with Peaks → Import
    c = ["#ff3b30", "#34c759", "#007aff", "#ff9500", "#af52de", "#00c7be"]
    (OUT / "peak_list_water_clusters.json").write_text(json.dumps(peak_list(
        ["Negative mode", "Positive mode"],
        [range_row("OH-(H2O)n", 17.0033, series_end(17.0033, H2O, 23), round(H2O, 6),
                   c[0], "Negative mode"),
         range_row("H+(H2O)n", 19.0178, series_end(19.0178, H2O, 23), round(H2O, 6),
                   c[2], "Positive mode"),
         range_row("Na+(H2O)n", 22.9898, series_end(22.9898, H2O, 22), round(H2O, 6),
                   c[3], "Positive mode")]),
        indent=1))
    (OUT / "peak_list_NaCl_clusters.json").write_text(json.dumps(peak_list(
        ["NaCl clusters", "Water clusters"],
        [range_row("Na+(NaCl)n", 22.9898, series_end(22.9898, NACL, 7), round(NACL, 6),
                   c[1], "NaCl clusters"),
         range_row("Na+(H2O)n", 41.0003, series_end(41.0003, H2O, 10), round(H2O, 6),
                   c[3], "Water clusters"),
         range_row("H+(H2O)n", 19.0178, series_end(19.0178, H2O, 4), round(H2O, 6),
                   c[2], "Water clusters")]),
        indent=1))
    g = groups
    (OUT / "peak_list_peak_area_standard.json").write_text(json.dumps(peak_list(
        list(g),
        [list_row(grp, [p["mz"] for p in items], c[i % len(c)], grp)
         for i, (grp, items) in enumerate(g.items())]), indent=1))
    print(f"Wrote example spectra to {OUT}")


if __name__ == "__main__":
    main()

# Example spectra

Five synthetic LILBID-MS spectra to try Droplet out and to check that it
works. Droplet opens this folder on first launch.

They look like real exports: 36 000 points, the quadratic time-of-flight
mass axis (m/z first dips, then rises to ≈ 445), intensities in volts, the
digitizer floor and early ringing, peaks about 7 points wide with a slight
tail, and small unassigned "grass" peaks. Every labelled peak sits at its
exact monoisotopic mass. The exact values are in `expected_values.json`.
The Droplet test suite (`assets/test/test_suite.py`) checks against them.

The polarity filter starts on **neg**. Switch it to **pos** or **All** to
see the other files.

| # | File | Polarity / dt | State | What to try |
|---|------|---------------|-------|-------------|
| 1 | `…_Water-calibrant_neg_des_I1152_dt080.txt` | neg / 080 | raw | **Auto-recalibration** (OH⁻(H₂O)ₙ calibrants; shown m/z is 0.12–0.35 Da too low), **baseline correction** (broad hump, drift, 7 mV offset) |
| 2 | `…_Water-calibrant_pos_des_I1152_dt080.txt` | pos / 080 | raw | Auto-recalibration in positive mode (H⁺(H₂O)ₙ and Na⁺(H₂O)ₙ; m/z is 0.15–0.40 Da too high), baseline correction, **manual recalibration** |
| 3 | `…_NaCl-10mM_pos_des_I1152_dt070_baseline+recalibrated.txt` | pos / 070 | processed | **Cluster detection**: Na⁺(NaCl)ₙ every 57.9586 Da (n = 0–7), water series every 18.0106 Da, and **isotope envelopes** from ³⁵Cl/³⁷Cl every 1.997 Da. Its `#processed=…` headers match what Droplet writes |
| 4 | `…_Peak-area-standard_neg_des_I1152_dt080_baseline+recalibrated.txt` | neg / 080 | processed | **Peak areas and ratios** with known answers: pairs at 2 : 1, 1 : 1 and 10 : 1, doublets 0.20 Da (partly overlapping) and 0.40 Da apart, an SNR ladder at 250–290 Da (3, 5, 10, 20, 50 × noise), and a broad peak at 350 Da for the **peak boundary checker** |
| 5 | `…_Water-calibrant_pos_des_I1152_dt120_recalibrated.csv` | pos / 120 | recalibrated | Same water sample as file 2 at a longer delay time. Larger clusters dominate, so **overlay / compare** it with file 2 after recalibrating that one. Comma-separated with a column-name row; tests the **dt filter** |

## Peak lists

Import these from the Peaks window:

- `peak_list_water_clusters.json`: OH⁻(H₂O)ₙ, H⁺(H₂O)ₙ, Na⁺(H₂O)ₙ as range rows, for files 1, 2 and 5
- `peak_list_NaCl_clusters.json`: Na⁺(NaCl)ₙ and the water series, for file 3
- `peak_list_peak_area_standard.json`: one group per test in file 4

## Things worth knowing

- In file 4, the automatic bounds of the broad 350 Da peak stop at the first
  noise wiggle, so the automatic area is far too small. Drag the bounds out
  in the peak boundary checker; the true area is 0.3027 V·Da.
- The 3σ and 5σ peaks of the SNR ladder are barely distinguishable from noise
  by design.

## Regenerating

```bash
.venv/bin/python assets/test/generate_example_spectra.py
```

The generator is seeded, so it always produces the same files.

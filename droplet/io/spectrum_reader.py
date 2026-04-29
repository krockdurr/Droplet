"""Spectrum file I/O: format detection, parsing, and writing.

All functions are pure (no Qt, no application state).
"""

import os
import re
import pandas as pd


def parse_lilbid_name(path):
    """Strip date prefix and underscores from a LILBID filename to get a display label."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r'^\d{4}[-_]?\d{2}[-_]?\d{2}[-_]?', '', stem)
    stem = stem.replace('_', ' ').replace('-', ' ').strip()
    return stem if stem else os.path.splitext(os.path.basename(path))[0]


def detect_separator(first_lines):
    candidates = ['\t', ',', ';', ' ']
    scores = {c: 0 for c in candidates}
    for line in first_lines:
        if line.startswith('#'): continue
        for c in candidates:
            parts = line.split(c)
            if len(parts) >= 2: scores[c] += len(parts)
    return max(scores, key=scores.get)


def read_spectrum_file(path, sep=None):
    with open(path, 'r', errors='replace') as fh:
        sample = [fh.readline() for _ in range(20)]
    if sep is None:
        sep = detect_separator(sample)
    try:
        if sep == ' ':
            df = pd.read_csv(path, comment='#', sep=r'\s+', header=None,
                             engine='python', names=['mz', 'intensity'])
        else:
            df = pd.read_csv(path, comment='#', sep=sep, header=None,
                             names=['mz', 'intensity'])
    except Exception as e:
        raise ValueError(f"Could not parse file: {e}")
    df = df.dropna()
    try:
        df['mz']        = pd.to_numeric(df['mz'],        errors='raise')
        df['intensity'] = pd.to_numeric(df['intensity'], errors='raise')
    except Exception:
        df = df.iloc[1:].copy()
        try:
            df['mz']        = pd.to_numeric(df['mz'],        errors='raise')
            df['intensity'] = pd.to_numeric(df['intensity'], errors='raise')
        except Exception as e2:
            raise ValueError(f"File has unexpected columns or format: {e2}")
    if len(df) == 0:
        raise ValueError("File contains no usable data rows.")
    return df[['mz', 'intensity']].reset_index(drop=True)


def save_spectrum_df(data_df, path):
    data_df.to_csv(path, sep='\t', index=False, header=False)


def spectrum_display_name(path):
    return parse_lilbid_name(path) if path else "-"

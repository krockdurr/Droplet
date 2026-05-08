"""File-system utilities: directory scanning, dt-filter parsing, virtual folders.

Functions that depend on application state (base_dir, all_txt_files, etc.)
accept those as parameters rather than reading globals, so they remain testable.
The app module passes the live values when calling these.
"""

import os
import re

_DT_RE = re.compile(r'_dt(\d+)', re.IGNORECASE)

VIRTUAL_FOLDER_PREFIX = "VIRTUAL FOLDER: "
MAX_RECENT = 10


def list_all_txt_files(base_dir, is_virtual=False, virtual_file_list=None):
    if is_virtual:
        return list(virtual_file_list or [])
    if not base_dir or not os.path.exists(base_dir):
        return []
    return sorted(
        os.path.join(base_dir, f) for f in os.listdir(base_dir)
        if f.lower().endswith((".txt", ".csv", ".tsv", ".dat", ".asc"))
    )


def get_txt_files(all_txt_files, polarity):
    if polarity == "All":
        return list(all_txt_files)
    return [p for p in all_txt_files if polarity in os.path.basename(p)]


def extract_dt(path):
    """Return the dt value string from a filename, or None."""
    m = _DT_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def get_available_dt_values(all_txt_files, polarity=None):
    """Sorted list of unique dt values in files matching polarity."""
    if polarity and polarity != "All":
        candidates = [p for p in all_txt_files if polarity in os.path.basename(p)]
    else:
        candidates = all_txt_files
    vals = set()
    for p in candidates:
        v = extract_dt(p)
        if v:
            vals.add(v)
    return sorted(vals, key=lambda x: int(x))


def get_txt_files_filtered(all_txt_files, polarity, dt_value):
    """Filter files by polarity and dt. 'All' means no filter for that dimension."""
    files = get_txt_files(all_txt_files, polarity)
    if dt_value == "All":
        return files
    return [p for p in files if extract_dt(p) == dt_value]

# SetProcessDpiAwareness(2) has been intentionally removed - see droplet/app.py for details.
"""Droplet v2.6.4 - entry point.

Run this file directly:
    python Droplet_v2.6.4.py

All application logic lives in the droplet/ package:
    droplet/app.py              - main window, menus, render loop, glue code
    droplet/processing/         - baseline, normalisation, calibration algorithms
    droplet/analysis/           - peak detection, cluster detection
    droplet/io/                 - spectrum file reading/writing, file utilities
    droplet/ui/widgets.py       - reusable Qt widget classes
    droplet/ui/windows/         - individual tool windows
    droplet/constants.py        - shared constants (colours, symbol lists, …)
"""

import sys
import os

# Ensure the repository root (this file's directory) is on sys.path so that
# `import droplet` works whether the script is run from here or from elsewhere.
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

# app.py is a self-contained script: importing it starts Qt and enters the
# event loop.  We simply execute it as __main__ so that the if __name__ ==
# "__main__" guard in app.py (if any) is respected, and all module-level
# startup code runs.
import droplet.app  # noqa: F401  - side-effect import: runs the application

"""Droplet – LILBID mass-spectrometry viewer."""
from pathlib import Path

_version_file = Path(__file__).parent.parent / "VERSION"
APP_VERSION   = _version_file.read_text().strip() if _version_file.exists() else "2.6.5"

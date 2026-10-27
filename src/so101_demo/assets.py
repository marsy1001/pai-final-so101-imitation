"""Locations of the fetched upstream assets (see scripts/fetch-so101.sh)."""

from __future__ import annotations

import os
from pathlib import Path

SO101_REVISION = "eecbe3e0a9ebb23e25ad7b2759b03884c6660903"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def so101_dir() -> Path:
    """Directory holding SO-ARM100 `Simulation/SO101/` (override: SO101_ASSETS)."""
    d = Path(os.environ.get("SO101_ASSETS", _REPO_ROOT / "assets" / "so101"))
    if not (d / "so101_new_calib.xml").is_file():
        raise FileNotFoundError(f"SO-101 assets not found in {d}; run scripts/fetch-so101.sh")
    return d


def urdf_path() -> Path:
    return so101_dir() / "so101_new_calib.urdf"


def mjcf_path() -> Path:
    return so101_dir() / "so101_new_calib.xml"


def scene_path() -> Path:
    return so101_dir() / "scene.xml"

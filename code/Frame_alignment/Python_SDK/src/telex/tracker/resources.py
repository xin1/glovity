from __future__ import annotations

from pathlib import Path


def default_calibration_path() -> str:
    return str(Path(__file__).resolve().parent / "data" / "stereo_fisheye_calib.yml")

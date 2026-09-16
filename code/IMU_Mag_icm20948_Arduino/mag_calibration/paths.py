"""Shared paths for the magnetometer calibration tools."""

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
DATA_DIR = PACKAGE_DIR / "data"

MAGNETOMETER_CSV = DATA_DIR / "magnetometer_data.csv"
CALIBRATION_JSON = DATA_DIR / "calibration_matrix.json"
QUALITY_REPORT = DATA_DIR / "calibration_quality_report.md"
ARDUINO_HEADER = DATA_DIR / "mag_calibration.h"
CALIBRATED_CSV = DATA_DIR / "magnetometer_data_calibrated.csv"
CALIBRATION_RUNS_DIR = DATA_DIR / "calibration_runs"

ARDUINO_STREAM_HEADER = (
    PROJECT_DIR / "arduino" / "mpu6500_ak8963_imu_stream" / "mag_calibration.h"
)
ARDUINO_RAW_HEADER = (
    PROJECT_DIR / "arduino" / "mpu9250_ak8963_raw" / "mag_calibration.h"
)

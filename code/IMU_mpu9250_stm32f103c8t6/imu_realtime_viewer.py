#!/usr/bin/env python3
"""Realtime dashboard for STM32F103C8T6 + MPU6500/MPU9250 UART data.

Expected packet examples::

    A_mg=-2,-58,-928 G_mdps=855,1550,0 T_cC=2538 M_uT_x100=4087,9816,7002
    A_mg=-2,-58,-928 G_mdps=855,1550,0 T_cC=2538 M=NA

The firmware uses integer fixed-point units. This program converts them to
g, degree/second, degree Celsius, and microtesla before plotting.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import re
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Optional

try:
    import matplotlib.pyplot as plt
    import numpy as np
    import serial
    from matplotlib.animation import FuncAnimation
    from matplotlib.gridspec import GridSpec
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from serial.tools import list_ports
except ImportError as exc:
    print(f"Missing dependency: {exc}")
    print("Install with: python -m pip install -r requirements.txt")
    raise SystemExit(1)


NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
PACKET_RE = re.compile(
    rf"^\s*A_mg=\s*({NUMBER_RE})\s*,\s*({NUMBER_RE})\s*,\s*({NUMBER_RE})\s+"
    rf"G_mdps=\s*({NUMBER_RE})\s*,\s*({NUMBER_RE})\s*,\s*({NUMBER_RE})\s+"
    rf"T_cC=\s*({NUMBER_RE})\s+"
    rf"(?:M_uT_x100=\s*({NUMBER_RE})\s*,\s*({NUMBER_RE})\s*,\s*({NUMBER_RE})|M=NA)\s*$",
    re.IGNORECASE,
)

HISTORY_SECONDS = 20.0
MAX_HISTORY_SAMPLES = 4000
WINDOW_SIZE_INCHES = (15.0, 8.5)
PLOT_COLORS = ("#ef4444", "#22c55e", "#3b82f6")
DEFAULT_CALIBRATION_PATH = Path(__file__).with_name("mag_calibration.json")
MIN_MAG_CALIBRATION_SAMPLES = 200

# The AK8963 compass axes in an MPU9250 package do not align with the
# accelerometer/gyroscope axes. Convert [Mx, My, Mz] to the IMU frame for
# fusion as [My, Mx, -Mz]. Calibration is applied in the raw sensor frame
# before this fixed package-axis conversion.
MAG_TO_IMU_FRAME = np.array(
    [
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
    ],
    dtype=float,
)


@dataclass
class ImuSample:
    received_at: float
    accel_g: np.ndarray
    gyro_dps: np.ndarray
    temperature_c: float
    mag_ut: np.ndarray
    mag_valid: bool
    raw_line: str = ""


@dataclass
class MagCalibration:
    """Hard-iron offset and full soft-iron correction in the raw mag frame."""

    hard_iron_bias_ut: np.ndarray
    soft_iron_matrix: np.ndarray
    field_strength_ut: float
    residual_std_ut: float
    sample_count: int
    calibrated: bool = True

    @classmethod
    def identity(cls) -> "MagCalibration":
        return cls(
            hard_iron_bias_ut=np.zeros(3),
            soft_iron_matrix=np.eye(3),
            field_strength_ut=0.0,
            residual_std_ut=0.0,
            sample_count=0,
            calibrated=False,
        )

    def apply(self, raw_mag_ut: np.ndarray) -> np.ndarray:
        if not np.all(np.isfinite(raw_mag_ut)):
            return np.full(3, np.nan)
        return self.soft_iron_matrix @ (raw_mag_ut - self.hard_iron_bias_ut)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "hard_iron_bias_ut": self.hard_iron_bias_ut.tolist(),
            "soft_iron_matrix": self.soft_iron_matrix.tolist(),
            "field_strength_ut": self.field_strength_ut,
            "residual_std_ut": self.residual_std_ut,
            "sample_count": self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "MagCalibration":
        bias = np.asarray(data["hard_iron_bias_ut"], dtype=float)
        matrix = np.asarray(data["soft_iron_matrix"], dtype=float)
        if bias.shape != (3,) or matrix.shape != (3, 3):
            raise ValueError("invalid calibration array shape")
        if not np.all(np.isfinite(bias)) or not np.all(np.isfinite(matrix)):
            raise ValueError("calibration contains non-finite values")
        singular_values = np.linalg.svd(matrix, compute_uv=False)
        if float(np.min(singular_values)) <= 1e-6:
            raise ValueError("soft-iron matrix is singular")
        return cls(
            hard_iron_bias_ut=bias,
            soft_iron_matrix=matrix,
            field_strength_ut=float(data.get("field_strength_ut", 0.0)),
            residual_std_ut=float(data.get("residual_std_ut", 0.0)),
            sample_count=int(data.get("sample_count", 0)),
            calibrated=True,
        )


def _fit_mag_ellipsoid(samples: np.ndarray) -> MagCalibration:
    """Fit a displaced, rotated ellipsoid and transform it into a sphere."""
    x = samples[:, 0]
    y = samples[:, 1]
    z = samples[:, 2]
    design = np.column_stack(
        (
            x * x,
            y * y,
            z * z,
            2.0 * x * y,
            2.0 * x * z,
            2.0 * y * z,
            2.0 * x,
            2.0 * y,
            2.0 * z,
        )
    )
    coefficients, _, rank, _ = np.linalg.lstsq(
        design,
        np.ones(samples.shape[0]),
        rcond=None,
    )
    if rank < 9:
        raise ValueError("rotation coverage is insufficient for a 3D ellipsoid")

    quadratic = np.array(
        [
            [coefficients[0], coefficients[3], coefficients[4]],
            [coefficients[3], coefficients[1], coefficients[5]],
            [coefficients[4], coefficients[5], coefficients[2]],
        ],
        dtype=float,
    )
    linear = coefficients[6:9]
    try:
        inverse_quadratic = np.linalg.inv(quadratic)
    except np.linalg.LinAlgError as exc:
        raise ValueError("ellipsoid matrix is singular") from exc

    center = -(inverse_quadratic @ linear)
    normalization = 1.0 + float(linear @ inverse_quadratic @ linear)
    if abs(normalization) <= 1e-12:
        raise ValueError("invalid ellipsoid normalization")
    shape = 0.5 * (quadratic / normalization + (quadratic / normalization).T)
    eigenvalues, eigenvectors = np.linalg.eigh(shape)
    if np.any(eigenvalues <= 1e-10):
        raise ValueError("fitted ellipsoid is not positive definite")

    radii = 1.0 / np.sqrt(eigenvalues)
    target_radius = float(np.exp(np.mean(np.log(radii))))
    correction = (
        eigenvectors
        @ np.diag(target_radius * np.sqrt(eigenvalues))
        @ eigenvectors.T
    )
    corrected = (correction @ (samples - center).T).T
    norms = np.linalg.norm(corrected, axis=1)
    return MagCalibration(
        hard_iron_bias_ut=center,
        soft_iron_matrix=correction,
        field_strength_ut=float(np.mean(norms)),
        residual_std_ut=float(np.std(norms)),
        sample_count=int(samples.shape[0]),
        calibrated=True,
    )


def fit_mag_calibration(raw_samples: list[np.ndarray]) -> MagCalibration:
    """Validate coverage, reject large outliers, and fit magnetometer data."""
    if len(raw_samples) < MIN_MAG_CALIBRATION_SAMPLES:
        raise ValueError(
            f"need at least {MIN_MAG_CALIBRATION_SAMPLES} samples; got {len(raw_samples)}"
        )
    samples = np.asarray(raw_samples, dtype=float)
    if samples.ndim != 2 or samples.shape[1] != 3:
        raise ValueError("magnetometer samples must have shape N x 3")
    samples = samples[np.all(np.isfinite(samples), axis=1)]
    if samples.shape[0] < MIN_MAG_CALIBRATION_SAMPLES:
        raise ValueError("too many invalid magnetometer samples")

    axis_ranges = np.ptp(samples, axis=0)
    if np.any(axis_ranges < 20.0):
        raise ValueError(
            "rotation did not cover every axis; rotate through more 3D orientations"
        )
    covariance_eigenvalues = np.linalg.eigvalsh(np.cov(samples, rowvar=False))
    coverage = float(np.min(covariance_eigenvalues) / np.max(covariance_eigenvalues))
    if coverage < 0.015:
        raise ValueError("samples are nearly planar; include upside-down and edge orientations")

    calibration = _fit_mag_ellipsoid(samples)
    corrected = np.vstack([calibration.apply(sample) for sample in samples])
    norms = np.linalg.norm(corrected, axis=1)
    median = float(np.median(norms))
    median_deviation = float(np.median(np.abs(norms - median)))
    threshold = max(4.5 * 1.4826 * median_deviation, 0.06 * median)
    inliers = np.abs(norms - median) <= threshold
    if (
        int(np.count_nonzero(inliers)) >= MIN_MAG_CALIBRATION_SAMPLES
        and int(np.count_nonzero(inliers)) < samples.shape[0]
    ):
        calibration = _fit_mag_ellipsoid(samples[inliers])

    relative_residual = calibration.residual_std_ut / max(
        calibration.field_strength_ut,
        1e-6,
    )
    if relative_residual > 0.18:
        raise ValueError(
            "ellipsoid residual is too large; move away from changing magnetic interference and retry"
        )
    return calibration


def load_mag_calibration(path: Path) -> tuple[MagCalibration, str]:
    if not path.exists():
        return MagCalibration.identity(), f"No calibration file: {path.name}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        calibration = MagCalibration.from_dict(data)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return MagCalibration.identity(), f"Calibration load failed: {exc}"
    return calibration, f"Loaded magnetic calibration from {path.name}"


def save_mag_calibration(path: Path, calibration: MagCalibration) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(calibration.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@dataclass
class ReaderEvent:
    sample: Optional[ImuSample] = None
    raw_line: str = ""
    status: str = ""
    error: str = ""
    active_port: str = ""
    received_bytes: int = 0


def parse_sensor_line(
    line: str,
    received_at: Optional[float] = None,
) -> Optional[ImuSample]:
    """Parse one fixed-point STM32 packet and convert it to SI-like units."""
    match = PACKET_RE.match(line)
    if match is None:
        return None

    values = match.groups()
    timestamp = time.monotonic() if received_at is None else received_at
    accel_g = np.array([float(value) for value in values[0:3]]) / 1000.0
    gyro_dps = np.array([float(value) for value in values[3:6]]) / 1000.0
    temperature_c = float(values[6]) / 100.0
    mag_valid = all(value is not None for value in values[7:10])
    if mag_valid:
        mag_ut = np.array([float(value) for value in values[7:10]]) / 100.0
    else:
        mag_ut = np.full(3, np.nan)

    return ImuSample(
        received_at=timestamp,
        accel_g=accel_g,
        gyro_dps=gyro_dps,
        temperature_c=temperature_c,
        mag_ut=mag_ut,
        mag_valid=mag_valid,
        raw_line=line,
    )


def list_available_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    print("Available serial ports:")
    for port in ports:
        print(f"  {port.device:8s} {port.description}")


def resolve_serial_port(requested: Optional[str]) -> str:
    """Return the requested port or automatically select a USB-UART adapter."""
    if requested:
        return requested

    ports = list(list_ports.comports())
    for port in ports:
        identity = " ".join(
            filter(
                None,
                (
                    port.description,
                    port.manufacturer,
                    port.product,
                    port.hwid,
                ),
            )
        ).upper()
        if any(name in identity for name in ("CH340", "CH341", "USB-SERIAL", "CP210", "FTDI")):
            return port.device

    if len(ports) == 1:
        return ports[0].device

    available = ", ".join(port.device for port in ports) or "none"
    raise RuntimeError(
        "Could not automatically select a USB-UART serial port. "
        f"Available ports: {available}. Use --port COMxx."
    )


def serial_reader(
    requested_port: Optional[str],
    baudrate: int,
    events: "queue.Queue[ReaderEvent]",
    stop_event: threading.Event,
) -> None:
    """Read UART lines in a background thread and reconnect after failures."""
    while not stop_event.is_set():
        try:
            active_port = resolve_serial_port(requested_port)
        except RuntimeError as exc:
            events.put(ReaderEvent(status="Waiting for USB-UART", error=str(exc)))
            stop_event.wait(1.0)
            continue

        try:
            events.put(ReaderEvent(status="Opening", active_port=active_port))
            with serial.Serial(active_port, baudrate=baudrate, timeout=0.1) as uart:
                uart.reset_input_buffer()
                events.put(ReaderEvent(status="Connected", active_port=active_port))
                pending = bytearray()
                received_bytes = 0

                while not stop_event.is_set():
                    waiting = uart.in_waiting
                    chunk = uart.read(min(max(waiting, 1), 4096))
                    if not chunk:
                        continue

                    pending.extend(chunk)
                    received_bytes += len(chunk)

                    while b"\n" in pending:
                        raw, _, remaining = pending.partition(b"\n")
                        pending = bytearray(remaining)
                        line = raw.decode("ascii", errors="ignore").strip("\r \t")
                        if not line:
                            continue
                        events.put(
                            ReaderEvent(
                                sample=parse_sensor_line(line),
                                raw_line=line,
                                status="Connected",
                                active_port=active_port,
                                received_bytes=received_bytes,
                            )
                        )

                    if len(pending) > 8192:
                        pending.clear()
                        events.put(
                            ReaderEvent(
                                status="Connected; invalid stream",
                                error="UART data has no newline; firmware should terminate packets with CRLF.",
                                active_port=active_port,
                                received_bytes=received_bytes,
                            )
                        )
        except (serial.SerialException, OSError) as exc:
            events.put(
                ReaderEvent(
                    status="Disconnected; retrying",
                    error=f"Serial error: {exc}",
                    active_port=active_port,
                )
            )
            stop_event.wait(1.0)


def euler_to_quaternion(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Convert ZYX Euler angles in radians to quaternion [w, x, y, z]."""
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    return np.array(
        [
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        ],
        dtype=float,
    )


def quaternion_to_matrix(quaternion: np.ndarray) -> np.ndarray:
    """Convert quaternion [w, x, y, z] to a body-to-world rotation matrix."""
    w, x, y, z = quaternion
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
            [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)],
            [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=float,
    )


def matrix_to_euler_deg(rotation: np.ndarray) -> np.ndarray:
    """Convert a rotation matrix to ZYX roll, pitch, yaw in degrees."""
    sin_pitch = float(np.clip(-rotation[2, 0], -1.0, 1.0))
    pitch = math.asin(sin_pitch)
    if abs(math.cos(pitch)) > 1e-6:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = 0.0
    return np.rad2deg(np.array([roll, pitch, yaw], dtype=float))


def initial_quaternion_from_accel(accel_g: np.ndarray) -> np.ndarray:
    """Initialize roll and pitch from gravity while leaving yaw at zero."""
    ax, ay, az = accel_g
    roll = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
    quaternion = euler_to_quaternion(roll, pitch, 0.0)
    return quaternion / np.linalg.norm(quaternion)


@dataclass
class GyroCalibrator:
    """Estimate stationary gyroscope bias during the first few seconds."""

    duration_s: float = 2.5
    gyro_limit_dps: float = 8.0
    accel_tolerance_g: float = 0.35
    started_at: Optional[float] = None
    last_sample_at: Optional[float] = None
    samples: list[np.ndarray] = field(default_factory=list)
    bias_dps: np.ndarray = field(default_factory=lambda: np.zeros(3))
    ready: bool = False

    def reset(self) -> None:
        self.started_at = None
        self.last_sample_at = None
        self.samples.clear()
        self.bias_dps = np.zeros(3)
        self.ready = False

    def update(self, sample: ImuSample) -> None:
        if self.ready:
            return
        accel_norm = float(np.linalg.norm(sample.accel_g))
        gyro_norm = float(np.linalg.norm(sample.gyro_dps))
        stationary = (
            abs(accel_norm - 1.0) <= self.accel_tolerance_g
            and gyro_norm <= self.gyro_limit_dps
        )
        if not stationary:
            self.started_at = None
            self.last_sample_at = None
            self.samples.clear()
            return
        if self.started_at is None:
            self.started_at = sample.received_at
        self.last_sample_at = sample.received_at
        self.samples.append(sample.gyro_dps.copy())
        if sample.received_at - self.started_at >= self.duration_s and len(self.samples) >= 10:
            self.bias_dps = np.median(np.vstack(self.samples), axis=0)
            self.ready = True

    @property
    def progress(self) -> float:
        if self.ready:
            return 1.0
        if self.started_at is None or self.last_sample_at is None:
            return 0.0
        return min(1.0, (self.last_sample_at - self.started_at) / self.duration_s)


class Mahony9D:
    """Nine-axis Mahony-style attitude filter with magnetic yaw correction."""

    def __init__(
        self,
        accel_gain: float = 2.0,
        mag_gain: float = 0.55,
        integral_gain: float = 0.04,
    ) -> None:
        self.accel_gain = accel_gain
        self.mag_gain = mag_gain
        self.integral_gain = integral_gain
        self.quaternion = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)
        self.integral = np.zeros(3)
        self.initialized = False
        self.accel_used = False
        self.mag_used = False

    def reset(self) -> None:
        self.quaternion[:] = [1.0, 0.0, 0.0, 0.0]
        self.integral[:] = 0.0
        self.initialized = False
        self.accel_used = False
        self.mag_used = False

    def set_from_accel(self, accel_g: np.ndarray) -> None:
        norm = float(np.linalg.norm(accel_g))
        if norm > 1e-6:
            self.quaternion = initial_quaternion_from_accel(accel_g / norm)
            self.initialized = True

    def update(
        self,
        gyro_dps: np.ndarray,
        accel_g: np.ndarray,
        mag_imu_ut: np.ndarray,
        dt: float,
    ) -> None:
        if not self.initialized:
            self.set_from_accel(accel_g)
        if not self.initialized or not 0.001 <= dt <= 0.30:
            return

        rotation = quaternion_to_matrix(self.quaternion)
        correction = np.zeros(3)
        accel_norm = float(np.linalg.norm(accel_g))
        self.accel_used = 0.65 <= accel_norm <= 1.35
        if self.accel_used:
            measured_gravity = accel_g / accel_norm
            predicted_gravity = rotation.T @ np.array([0.0, 0.0, 1.0])
            correction += self.accel_gain * np.cross(measured_gravity, predicted_gravity)

        mag_norm = float(np.linalg.norm(mag_imu_ut))
        self.mag_used = bool(np.all(np.isfinite(mag_imu_ut)) and 5.0 <= mag_norm <= 1000.0)
        if self.mag_used:
            measured_mag = mag_imu_ut / mag_norm
            field_world = rotation @ measured_mag
            reference_world = np.array(
                [math.hypot(field_world[0], field_world[1]), 0.0, field_world[2]],
                dtype=float,
            )
            reference_norm = float(np.linalg.norm(reference_world))
            if reference_norm > 1e-6:
                reference_world /= reference_norm
                predicted_mag = rotation.T @ reference_world
                correction += self.mag_gain * np.cross(measured_mag, predicted_mag)
            else:
                self.mag_used = False

        self.integral += self.integral_gain * correction * dt
        self.integral = np.clip(self.integral, -0.15, 0.15)
        gyro_rad = np.deg2rad(gyro_dps.astype(float)) + correction + self.integral

        w, x, y, z = self.quaternion
        gx, gy, gz = gyro_rad
        quaternion_dot = 0.5 * np.array(
            [
                -x * gx - y * gy - z * gz,
                w * gx + y * gz - z * gy,
                w * gy - x * gz + z * gx,
                w * gz + x * gy - y * gx,
            ],
            dtype=float,
        )
        self.quaternion += quaternion_dot * dt
        norm = float(np.linalg.norm(self.quaternion))
        if norm < 1e-9:
            self.reset()
        else:
            self.quaternion /= norm

    @property
    def rotation_matrix(self) -> np.ndarray:
        return quaternion_to_matrix(self.quaternion)


def cuboid_faces(
    size: tuple[float, float, float],
    rotation: np.ndarray,
) -> list[list[np.ndarray]]:
    """Return the rotated faces of a cuboid centered at the origin."""
    sx, sy, sz = np.asarray(size, dtype=float) / 2.0
    corners = np.array(
        [
            [-sx, -sy, -sz], [sx, -sy, -sz],
            [sx, sy, -sz], [-sx, sy, -sz],
            [-sx, -sy, sz], [sx, -sy, sz],
            [sx, sy, sz], [-sx, sy, sz],
        ],
        dtype=float,
    )
    corners = corners @ rotation.T
    indices = (
        (0, 1, 2, 3),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (2, 3, 7, 6),
        (1, 2, 6, 5),
        (0, 3, 7, 4),
    )
    return [[corners[index] for index in face] for face in indices]


def draw_3d(axis, state: "DashboardState") -> None:
    """Redraw the board and sensor vectors using the latest fused attitude."""
    elevation, azimuth = axis.elev, axis.azim
    axis.cla()
    axis.view_init(elev=elevation, azim=azimuth)
    axis.set_xlim(-1.8, 1.8)
    axis.set_ylim(-1.8, 1.8)
    axis.set_zlim(-1.8, 1.8)
    axis.set_box_aspect((1.0, 1.0, 1.0))
    axis.set_xlabel("World X")
    axis.set_ylabel("World Y")
    axis.set_zlabel("World Z")
    calibration = (
        "READY"
        if state.gyro_calibrator.ready
        else f"calibrating {state.gyro_calibrator.progress * 100:.0f}%"
    )
    axis.set_title(f"Realtime 3D attitude (gyro {calibration})")

    for vector, color, label in (
        (np.array([1.45, 0.0, 0.0]), "#ef4444", "World X"),
        (np.array([0.0, 1.45, 0.0]), "#22c55e", "World Y"),
        (np.array([0.0, 0.0, 1.45]), "#3b82f6", "World Z"),
    ):
        axis.plot(
            [0.0, vector[0]],
            [0.0, vector[1]],
            [0.0, vector[2]],
            color=color,
            linestyle="--",
            alpha=0.28,
        )
        axis.text(*vector, label, color=color, alpha=0.75, fontsize=8)

    rotation = state.relative_rotation
    board = Poly3DCollection(
        cuboid_faces((1.25, 2.25, 0.18), rotation),
        facecolors="#38bdf8",
        edgecolors="#0f172a",
        linewidths=1.0,
        alpha=0.72,
    )
    axis.add_collection3d(board)

    for body_vector, color, label in (
        (np.array([1.35, 0.0, 0.0]), "red", "X"),
        (np.array([0.0, 1.35, 0.0]), "green", "Y"),
        (np.array([0.0, 0.0, 1.35]), "blue", "Z"),
    ):
        tip = rotation @ body_vector
        axis.quiver(0.0, 0.0, 0.0, *tip, color=color, linewidth=2.0, arrow_length_ratio=0.10)
        axis.text(*tip, label, color=color, fontweight="bold")

    front = rotation @ np.array([1.22, 0.0, 0.25])
    axis.scatter(*front, color="orange", s=50)

    if state.latest is not None:
        accel_norm = float(np.linalg.norm(state.latest.accel_g))
        if accel_norm > 1e-6:
            accel_world = rotation @ (state.latest.accel_g / accel_norm)
            axis.quiver(
                0.0, 0.0, 0.0, *accel_world,
                color="magenta", linewidth=1.8, arrow_length_ratio=0.12,
            )
            axis.text(*accel_world, "Accel", color="magenta", fontsize=8)


@dataclass
class DashboardState:
    history: Deque[ImuSample] = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY_SAMPLES)
    )
    latest: Optional[ImuSample] = None
    parsed_count: int = 0
    ignored_count: int = 0
    received_bytes: int = 0
    active_port: str = ""
    connection_status: str = "Starting"
    serial_error: str = ""
    latest_raw_line: str = ""
    last_packet_at: Optional[float] = None
    paused: bool = False
    rotation_matrix: np.ndarray = field(default_factory=lambda: np.eye(3))
    zero_rotation: np.ndarray = field(default_factory=lambda: np.eye(3))
    gyro_calibrator: GyroCalibrator = field(default_factory=GyroCalibrator)
    attitude_filter: Mahony9D = field(default_factory=Mahony9D)
    previous_sample_at: Optional[float] = None
    mag_calibration: MagCalibration = field(default_factory=MagCalibration.identity)
    mag_calibration_path: Path = DEFAULT_CALIBRATION_PATH
    mag_calibrating: bool = False
    mag_calibration_samples: list[np.ndarray] = field(default_factory=list)
    mag_calibration_message: str = "Magnetic calibration has not been loaded"

    def consume(self, sample: ImuSample) -> None:
        self.latest = sample
        self.history.append(sample)
        self.parsed_count += 1
        self.last_packet_at = time.monotonic()
        self.gyro_calibrator.update(sample)
        if self.mag_calibrating and sample.mag_valid:
            self.mag_calibration_samples.append(sample.mag_ut.copy())

        if not self.attitude_filter.initialized:
            self.attitude_filter.set_from_accel(sample.accel_g)
        if self.previous_sample_at is not None and self.gyro_calibrator.ready:
            dt = sample.received_at - self.previous_sample_at
            corrected_gyro = sample.gyro_dps - self.gyro_calibrator.bias_dps
            mag_imu = (
                MAG_TO_IMU_FRAME @ self.corrected_mag(sample.mag_ut)
                if sample.mag_valid
                else np.full(3, np.nan)
            )
            self.attitude_filter.update(corrected_gyro, sample.accel_g, mag_imu, dt)
        self.previous_sample_at = sample.received_at
        self.rotation_matrix = self.attitude_filter.rotation_matrix

    def clear_history(self) -> None:
        self.history.clear()

    def corrected_mag(self, raw_mag_ut: np.ndarray) -> np.ndarray:
        return self.mag_calibration.apply(raw_mag_ut)

    def start_mag_calibration(self) -> None:
        self.mag_calibrating = True
        self.mag_calibration_samples.clear()
        self.mag_calibration_message = (
            "Collecting raw magnetic samples; rotate slowly through every 3D orientation"
        )

    def finish_mag_calibration(self) -> bool:
        self.mag_calibrating = False
        sample_count = len(self.mag_calibration_samples)
        try:
            calibration = fit_mag_calibration(self.mag_calibration_samples)
            save_mag_calibration(self.mag_calibration_path, calibration)
        except (OSError, ValueError) as exc:
            self.mag_calibration_message = (
                f"Magnetic calibration failed ({sample_count} samples): {exc}"
            )
            return False

        self.mag_calibration = calibration
        self.mag_calibration_samples.clear()
        self.mag_calibration_message = (
            f"Saved magnetic calibration to {self.mag_calibration_path.name}"
        )
        self.reset_fusion()
        return True

    def reset_fusion(self) -> None:
        self.history.clear()
        self.gyro_calibrator.reset()
        self.attitude_filter.reset()
        self.rotation_matrix = np.eye(3)
        self.zero_rotation = np.eye(3)
        self.previous_sample_at = None

    @property
    def relative_rotation(self) -> np.ndarray:
        return self.zero_rotation.T @ self.rotation_matrix

    @property
    def rpy_deg(self) -> np.ndarray:
        return matrix_to_euler_deg(self.relative_rotation)


def demo_reader(
    events: "queue.Queue[ReaderEvent]",
    stop_event: threading.Event,
) -> None:
    """Generate plausible nine-axis data without requiring hardware."""
    start = time.monotonic()
    rng = np.random.default_rng(9250)
    while not stop_event.is_set():
        now = time.monotonic()
        elapsed = now - start
        if elapsed < 3.0:
            motion_time = 0.0
            roll = pitch = yaw = 0.0
            gyro = np.zeros(3)
        else:
            motion_time = elapsed - 3.0
            roll = math.radians(25.0 * math.sin(0.55 * motion_time))
            pitch = math.radians(18.0 * math.sin(0.37 * motion_time))
            yaw = math.radians(35.0 * math.sin(0.20 * motion_time))
            gyro = np.array(
                [
                    25.0 * 0.55 * math.cos(0.55 * motion_time),
                    18.0 * 0.37 * math.cos(0.37 * motion_time),
                    35.0 * 0.20 * math.cos(0.20 * motion_time),
                ]
            )

        true_rotation = quaternion_to_matrix(euler_to_quaternion(roll, pitch, yaw))
        accel = true_rotation.T @ np.array([0.0, 0.0, 1.0])
        accel += rng.normal(0.0, 0.006, 3)
        gyro += rng.normal(0.0, 0.08, 3)
        mag_world = np.array([25.0, 3.0, 41.0])
        mag_imu = true_rotation.T @ mag_world
        mag = MAG_TO_IMU_FRAME @ mag_imu
        mag += rng.normal(0.0, 0.10, 3)
        temperature = 25.0 + 0.6 * math.sin(0.04 * elapsed)
        sample = ImuSample(
            received_at=now,
            accel_g=accel,
            gyro_dps=gyro,
            temperature_c=temperature,
            mag_ut=mag,
            mag_valid=True,
            raw_line="demo",
        )
        events.put(ReaderEvent(sample=sample, raw_line="demo", status="Demo"))
        stop_event.wait(0.05)


def recent_arrays(
    state: DashboardState,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return recent timestamps and sensor arrays relative to the newest sample."""
    if not state.history:
        empty = np.empty(0)
        return empty, np.empty((0, 3)), np.empty((0, 3)), empty, np.empty((0, 3))

    samples = list(state.history)
    latest_time = samples[-1].received_at
    samples = [sample for sample in samples if sample.received_at >= latest_time - HISTORY_SECONDS]
    times = np.array([sample.received_at - latest_time for sample in samples])
    accel = np.vstack([sample.accel_g for sample in samples])
    gyro = np.vstack([sample.gyro_dps for sample in samples])
    temperature = np.array([sample.temperature_c for sample in samples])
    mag = np.vstack(
        [
            state.corrected_mag(sample.mag_ut)
            if sample.mag_valid
            else np.full(3, np.nan)
            for sample in samples
        ]
    )
    return times, accel, gyro, temperature, mag


def update_axis_limits(axis, values: np.ndarray, minimum_span: float) -> None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return
    low = float(np.min(finite))
    high = float(np.max(finite))
    span = max(high - low, minimum_span)
    center = (low + high) * 0.5
    margin = span * 0.12
    axis.set_ylim(center - span * 0.5 - margin, center + span * 0.5 + margin)


def run_self_test() -> int:
    line = "A_mg=-2,-58,-928 G_mdps=855,1550,0 T_cC=2538 M_uT_x100=4087,9816,7002"
    sample = parse_sensor_line(line, received_at=1.0)
    assert sample is not None
    assert np.allclose(sample.accel_g, [-0.002, -0.058, -0.928])
    assert np.allclose(sample.gyro_dps, [0.855, 1.550, 0.0])
    assert abs(sample.temperature_c - 25.38) < 1e-9
    assert np.allclose(sample.mag_ut, [40.87, 98.16, 70.02])
    assert sample.mag_valid

    no_mag = parse_sensor_line(
        "A_mg=1,2,3 G_mdps=4,5,6 T_cC=2500 M=NA",
        received_at=2.0,
    )
    assert no_mag is not None
    assert not no_mag.mag_valid
    assert np.all(np.isnan(no_mag.mag_ut))
    assert parse_sensor_line("BOOT: MPU9250 firmware starting") is None

    attitude_filter = Mahony9D()
    attitude_filter.set_from_accel(np.array([0.0, 0.0, 1.0]))
    for _ in range(200):
        attitude_filter.update(
            np.zeros(3),
            np.array([0.0, 0.0, 1.0]),
            np.array([25.0, 3.0, 41.0]),
            0.01,
        )
    rotation = attitude_filter.rotation_matrix
    assert np.all(np.isfinite(rotation))
    assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
    assert abs(float(np.linalg.det(rotation)) - 1.0) < 1e-6

    faces = cuboid_faces((2.0, 1.0, 0.2), rotation)
    assert len(faces) == 6 and all(len(face) == 4 for face in faces)

    rng = np.random.default_rng(9250)
    directions = rng.normal(size=(1800, 3))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    true_field = 49.0 * directions
    distortion = np.array(
        [
            [1.32, 0.13, -0.05],
            [0.07, 0.76, 0.08],
            [-0.04, 0.11, 1.16],
        ]
    )
    true_bias = np.array([28.0, -17.0, 9.0])
    distorted = (distortion @ true_field.T).T + true_bias
    distorted += rng.normal(0.0, 0.08, distorted.shape)
    mag_calibration = fit_mag_calibration([row for row in distorted])
    corrected = np.vstack([mag_calibration.apply(row) for row in distorted])
    corrected_norms = np.linalg.norm(corrected, axis=1)
    assert np.linalg.norm(mag_calibration.hard_iron_bias_ut - true_bias) < 0.5
    assert float(np.std(corrected_norms) / np.mean(corrected_norms)) < 0.01
    restored = MagCalibration.from_dict(mag_calibration.to_dict())
    assert np.allclose(restored.hard_iron_bias_ut, mag_calibration.hard_iron_bias_ut)
    assert np.allclose(restored.soft_iron_matrix, mag_calibration.soft_iron_matrix)
    with tempfile.TemporaryDirectory() as temporary_directory:
        calibration_path = Path(temporary_directory) / "mag_calibration.json"
        save_mag_calibration(calibration_path, mag_calibration)
        loaded, message = load_mag_calibration(calibration_path)
        assert loaded.calibrated and "Loaded" in message
        assert np.allclose(loaded.hard_iron_bias_ut, mag_calibration.hard_iron_bias_ut)
        assert np.allclose(loaded.soft_iron_matrix, mag_calibration.soft_iron_matrix)

    print(
        "Self-test passed: parser, unit conversions, magnetic ellipsoid calibration, "
        "9-axis fusion and 3D geometry are correct."
    )
    return 0


def monitor_stream(
    requested_port: Optional[str],
    baudrate: int,
    duration_s: float,
) -> int:
    """Print UART parsing results without opening the plotting window."""
    events: "queue.Queue[ReaderEvent]" = queue.Queue()
    stop_event = threading.Event()
    reader = threading.Thread(
        target=serial_reader,
        args=(requested_port, baudrate, events, stop_event),
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + max(duration_s, 1.0)
    parsed = 0
    lines = 0
    try:
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if event.error:
                print(event.error)
            if event.raw_line:
                lines += 1
                if event.sample is None:
                    print(f"[IGNORED] {event.raw_line}")
                else:
                    parsed += 1
                    print(f"[OK] {event.raw_line}")
    finally:
        stop_event.set()
        reader.join(timeout=1.0)
    print(f"Monitor result: {parsed} parsed / {lines} received lines")
    return 0 if parsed > 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="STM32F103 + MPU6500/MPU9250 realtime sensor dashboard"
    )
    parser.add_argument("--port", help="Serial port, for example COM27")
    parser.add_argument("--baud", type=int, default=115200, help="Baudrate (default: 115200)")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    parser.add_argument("--demo", action="store_true", help="Show simulated data without hardware")
    parser.add_argument("--self-test", action="store_true", help="Test packet parsing and exit")
    parser.add_argument(
        "--mag-calibration",
        type=Path,
        default=DEFAULT_CALIBRATION_PATH,
        help="Magnetometer calibration JSON path (default: beside this script)",
    )
    parser.add_argument(
        "--monitor",
        type=float,
        metavar="SECONDS",
        help="Monitor and parse UART for N seconds without plots",
    )
    args = parser.parse_args()

    if args.list_ports:
        list_available_ports()
        return 0
    if args.self_test:
        return run_self_test()
    if args.monitor is not None:
        return monitor_stream(args.port, args.baud, args.monitor)

    events: "queue.Queue[ReaderEvent]" = queue.Queue()
    stop_event = threading.Event()
    mag_calibration, mag_calibration_message = load_mag_calibration(
        args.mag_calibration
    )
    state = DashboardState(
        mag_calibration=mag_calibration,
        mag_calibration_path=args.mag_calibration,
        mag_calibration_message=mag_calibration_message,
    )
    reader_target = demo_reader if args.demo else serial_reader
    reader_args = (events, stop_event) if args.demo else (
        args.port,
        args.baud,
        events,
        stop_event,
    )
    reader = threading.Thread(target=reader_target, args=reader_args, daemon=True)
    reader.start()

    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
        }
    )
    figure = plt.figure(figsize=WINDOW_SIZE_INCHES)
    try:
        figure.canvas.manager.set_window_title("STM32 + MPU9250 Realtime Dashboard")
    except AttributeError:
        pass

    grid = GridSpec(
        3,
        4,
        figure=figure,
        width_ratios=(1.15, 1.15, 1.0, 1.0),
        height_ratios=(1.0, 1.0, 0.72),
    )
    attitude_axis = figure.add_subplot(grid[:, :2], projection="3d")
    accel_axis = figure.add_subplot(grid[0, 2])
    gyro_axis = figure.add_subplot(grid[0, 3])
    mag_axis = figure.add_subplot(grid[1, 2])
    temp_axis = figure.add_subplot(grid[1, 3])
    status_axis = figure.add_subplot(grid[2, 2:])

    axes = (accel_axis, gyro_axis, mag_axis, temp_axis)
    titles = (
        "Acceleration",
        "Angular velocity",
        "Magnetic field",
        "Temperature",
    )
    units = ("g", "deg/s", "uT", "deg C")
    for axis, title, unit in zip(axes, titles, units):
        axis.set_title(title)
        axis.set_xlabel("Time relative to latest sample (s)")
        axis.set_ylabel(unit)
        axis.set_xlim(-HISTORY_SECONDS, 0.3)
        axis.grid(True, alpha=0.25)

    accel_lines = [
        accel_axis.plot([], [], color=color, label=label, linewidth=1.4)[0]
        for color, label in zip(PLOT_COLORS, ("Ax", "Ay", "Az"))
    ]
    gyro_lines = [
        gyro_axis.plot([], [], color=color, label=label, linewidth=1.4)[0]
        for color, label in zip(PLOT_COLORS, ("Gx", "Gy", "Gz"))
    ]
    mag_lines = [
        mag_axis.plot([], [], color=color, label=label, linewidth=1.4)[0]
        for color, label in zip(PLOT_COLORS, ("Mx", "My", "Mz"))
    ]
    temp_line = temp_axis.plot([], [], color="#f59e0b", label="Temperature", linewidth=1.6)[0]
    for axis in (accel_axis, gyro_axis, mag_axis, temp_axis):
        axis.legend(loc="upper left", ncol=3)

    status_axis.axis("off")
    status_text = status_axis.text(
        0.0,
        1.0,
        "Starting...",
        va="top",
        ha="left",
        family="monospace",
        fontsize=9,
    )
    draw_3d(attitude_axis, state)
    figure.tight_layout()

    def on_key(event) -> None:
        key = (event.key or "").lower()
        if key == "r":
            state.zero_rotation = state.rotation_matrix.copy()
        elif key == "c":
            state.reset_fusion()
        elif key == "m":
            if state.mag_calibrating:
                state.finish_mag_calibration()
            else:
                state.start_mag_calibration()
        elif key == " ":
            state.paused = not state.paused
        elif key in ("q", "escape"):
            plt.close(figure)

    def update(_frame):
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event.status:
                state.connection_status = event.status
                if event.status in ("Connected", "Demo"):
                    state.serial_error = ""
            if event.error:
                state.serial_error = event.error
            if event.active_port:
                state.active_port = event.active_port
            state.received_bytes = max(state.received_bytes, event.received_bytes)
            if event.raw_line:
                state.latest_raw_line = event.raw_line
            if event.sample is not None:
                state.consume(event.sample)
            elif event.raw_line:
                state.ignored_count += 1

        if state.paused:
            return []

        draw_3d(attitude_axis, state)
        times, accel, gyro, temperature, mag = recent_arrays(state)
        for index, line in enumerate(accel_lines):
            line.set_data(times, accel[:, index] if accel.size else [])
        for index, line in enumerate(gyro_lines):
            line.set_data(times, gyro[:, index] if gyro.size else [])
        for index, line in enumerate(mag_lines):
            line.set_data(times, mag[:, index] if mag.size else [])
        temp_line.set_data(times, temperature)

        if accel.size:
            update_axis_limits(accel_axis, accel, 0.2)
            update_axis_limits(gyro_axis, gyro, 2.0)
            update_axis_limits(temp_axis, temperature, 1.0)
        if mag.size and np.any(np.isfinite(mag)):
            update_axis_limits(mag_axis, mag, 10.0)

        source = "DEMO" if args.demo else (state.active_port or args.port or "auto")
        lines = [
            f"Source: {source} @ {args.baud}    Serial: {state.connection_status}",
            f"Parsed: {state.parsed_count}    Ignored: {state.ignored_count}    UART bytes: {state.received_bytes}",
        ]
        if state.last_packet_at is not None:
            lines[1] += f"    Last packet: {time.monotonic() - state.last_packet_at:.2f} s ago"
        if state.serial_error:
            lines.append(f"Error: {state.serial_error}")
        if state.latest is None:
            lines.append("Waiting for sensor packets...")
        else:
            sample = state.latest
            calibration = (
                "READY"
                if state.gyro_calibrator.ready
                else f"{state.gyro_calibrator.progress * 100:.0f}% (keep still)"
            )
            rpy = state.rpy_deg
            lines.append(
                f"Fusion: gyro calibration={calibration}  "
                f"ACC={'USED' if state.attitude_filter.accel_used else 'WAIT'}  "
                f"MAG={'USED' if state.attitude_filter.mag_used else 'WAIT'}"
            )
            lines.append(
                f"Roll={rpy[0]:7.2f} deg  Pitch={rpy[1]:7.2f} deg  Yaw={rpy[2]:7.2f} deg"
            )
            lines.append(
                "Accel[g]: "
                f"{sample.accel_g[0]:8.3f} {sample.accel_g[1]:8.3f} {sample.accel_g[2]:8.3f}"
                f"    |A|={np.linalg.norm(sample.accel_g):.3f} g"
            )
            lines.append(
                "Gyro[dps]:"
                f"{sample.gyro_dps[0]:8.3f} {sample.gyro_dps[1]:8.3f} {sample.gyro_dps[2]:8.3f}"
                f"    Temp={sample.temperature_c:.2f} C"
            )
            if sample.mag_valid:
                corrected_mag = state.corrected_mag(sample.mag_ut)
                lines.append(
                    "Mag calibrated[uT]:"
                    f"{corrected_mag[0]:8.2f} {corrected_mag[1]:8.2f} {corrected_mag[2]:8.2f}"
                    f"    |M|={np.linalg.norm(corrected_mag):.2f} uT"
                )
            else:
                lines.append("Mag[uT]: NA")
            lines.append(f"Last UART: {sample.raw_line[:150]}")
        if state.mag_calibrating:
            lines.append(
                "MAG CAL: COLLECTING "
                f"{len(state.mag_calibration_samples)} samples — rotate all axes; press M to fit"
            )
        elif state.mag_calibration.calibrated:
            lines.append(
                f"MAG CAL: READY ({state.mag_calibration.sample_count} samples, "
                f"field={state.mag_calibration.field_strength_ut:.2f} uT, "
                f"residual={state.mag_calibration.residual_std_ut:.2f} uT)"
            )
            lines.append(state.mag_calibration_message)
        else:
            lines.append(f"MAG CAL: NOT CALIBRATED — {state.mag_calibration_message}")
        lines.append(
            "Keys: M=start/finish mag calibration  R=set zero  C=gyro recalibrate  "
            "Space=pause  Q/Esc=quit"
        )
        status_text.set_text("\n".join(lines))
        return [*accel_lines, *gyro_lines, *mag_lines, temp_line, status_text]

    figure.canvas.mpl_connect("key_press_event", on_key)
    animation = FuncAnimation(figure, update, interval=50, cache_frame_data=False)
    try:
        plt.show()
    finally:
        _ = animation
        stop_event.set()
        reader.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

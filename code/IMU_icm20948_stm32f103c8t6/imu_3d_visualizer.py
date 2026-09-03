#!/usr/bin/env python3
"""Realtime 3D viewer for STM32F103C8T6 + ICM-20948 UART output.

Expected serial line:
    A:0.001,0.002,1.003 g  G:0.010,-0.020,0.030 dps  T:27.93 C  \
M:25.10,-3.20,41.80 uT  R:1.20,-2.30,87.40  USE:1,1

The STM32 performs calibration, gyro filtering and nine-axis Mahony fusion.
This viewer uses the firmware R/P/Y result directly instead of filtering the
same sensor data a second time on the PC.
"""

from __future__ import annotations

import argparse
import math
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
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


FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
BASE_PACKET_RE = (
    rf"A:\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*g\s+"
    rf"G:\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*dps\s+"
    rf"T:\s*({FLOAT_RE})\s*C"
)
FUSION_LINE_RE = re.compile(
    BASE_PACKET_RE
    + rf"\s+"
    rf"M:\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*uT\s+"
    rf"R:\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s*,\s*({FLOAT_RE})\s+"
    rf"USE:\s*([01])\s*,\s*([01])\s*$",
    re.IGNORECASE,
)
LEGACY_LINE_RE = re.compile(BASE_PACKET_RE + r"\s*$", re.IGNORECASE)

PREFERRED_PORT = "COM28"
WINDOW_SIZE_INCHES = (12.0, 6.5)


@dataclass
class ImuSample:
    received_at: float
    acc_g: np.ndarray
    gyro_dps: np.ndarray
    temperature_c: float
    mag_ut: np.ndarray
    rpy_deg: np.ndarray
    acc_used: bool
    mag_used: bool
    firmware_fusion: bool
    raw_line: str = ""


@dataclass
class ReaderEvent:
    sample: Optional[ImuSample] = None
    raw_line: str = ""
    error: str = ""
    status: str = ""
    active_port: str = ""
    received_bytes: int = 0


def parse_sensor_line(line: str, received_at: Optional[float] = None) -> Optional[ImuSample]:
    """Parse either a legacy A/G/T line or a full firmware-fusion line."""
    timestamp = time.monotonic() if received_at is None else received_at
    fusion_match = FUSION_LINE_RE.search(line)
    if fusion_match:
        groups = fusion_match.groups()
        values = [float(value) for value in groups[:13]]
        return ImuSample(
            received_at=timestamp,
            acc_g=np.array(values[0:3], dtype=float),
            gyro_dps=np.array(values[3:6], dtype=float),
            temperature_c=values[6],
            mag_ut=np.array(values[7:10], dtype=float),
            rpy_deg=np.array(values[10:13], dtype=float),
            acc_used=groups[13] == "1",
            mag_used=groups[14] == "1",
            firmware_fusion=True,
            raw_line=line,
        )

    legacy_match = LEGACY_LINE_RE.search(line)
    if not legacy_match:
        return None
    values = [float(value) for value in legacy_match.groups()]
    acc = np.array(values[0:3], dtype=float)
    return ImuSample(
        received_at=timestamp,
        acc_g=acc,
        gyro_dps=np.array(values[3:6], dtype=float),
        temperature_c=values[6],
        mag_ut=np.full(3, np.nan),
        rpy_deg=np.zeros(3),
        acc_used=0.75 <= float(np.linalg.norm(acc)) <= 1.25,
        mag_used=False,
        firmware_fusion=False,
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
    """Use an explicit port, otherwise prefer COM28 and then a CH340 adapter."""
    if requested:
        return requested
    ports = list(list_ports.comports())
    for port in ports:
        if port.device.upper() == PREFERRED_PORT:
            return port.device
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
        if "CH340" in identity or "USB-SERIAL" in identity:
            return port.device
    if len(ports) == 1:
        return ports[0].device
    available = ", ".join(port.device for port in ports) or "none"
    raise RuntimeError(
        f"Could not find {PREFERRED_PORT} or automatically select a CH340 USB-TTL port. "
        f"Available ports: {available}. Use --port COMxx."
    )


def serial_reader(
    requested_port: Optional[str],
    baudrate: int,
    events: "queue.Queue[ReaderEvent]",
    stop_event: threading.Event,
) -> None:
    """Read complete UART lines and automatically reconnect after failures."""
    while not stop_event.is_set():
        try:
            active_port = resolve_serial_port(requested_port)
        except RuntimeError as exc:
            events.put(ReaderEvent(error=str(exc), status="Waiting for USB-TTL"))
            stop_event.wait(1.0)
            continue

        try:
            events.put(ReaderEvent(status="Opening serial port", active_port=active_port))
            with serial.Serial(active_port, baudrate=baudrate, timeout=0.1) as ser:
                ser.reset_input_buffer()
                events.put(ReaderEvent(status="Connected", active_port=active_port))
                pending = bytearray()
                total_bytes = 0
                last_byte_at = time.monotonic()
                last_report_at = 0.0

                while not stop_event.is_set():
                    waiting = ser.in_waiting
                    chunk = ser.read(min(max(waiting, 1), 4096))
                    if not chunk:
                        # Also accept a complete ASCII packet without CR/LF after
                        # 200 ms of silence. Normal STM32 output should use CRLF.
                        if pending and time.monotonic() - last_byte_at >= 0.2:
                            line = pending.decode("ascii", errors="ignore").strip("\r \t")
                            pending.clear()
                            if line:
                                events.put(
                                    ReaderEvent(
                                        sample=parse_sensor_line(line),
                                        raw_line=line,
                                        status="Connected",
                                        active_port=active_port,
                                        received_bytes=total_bytes,
                                    )
                                )
                        continue
                    pending.extend(chunk)
                    total_bytes += len(chunk)
                    last_byte_at = time.monotonic()

                    # STM32 sends CRLF. Splitting on LF also accepts LF-only data.
                    while b"\n" in pending:
                        raw_line, _, remaining = pending.partition(b"\n")
                        pending = bytearray(remaining)
                        line = raw_line.decode("ascii", errors="ignore").strip("\r \t")
                        if not line:
                            continue
                        events.put(
                            ReaderEvent(
                                sample=parse_sensor_line(line),
                                raw_line=line,
                                status="Connected",
                                active_port=active_port,
                                received_bytes=total_bytes,
                            )
                        )

                    if time.monotonic() - last_report_at >= 0.5:
                        events.put(
                            ReaderEvent(
                                status="Connected",
                                active_port=active_port,
                                received_bytes=total_bytes,
                            )
                        )
                        last_report_at = time.monotonic()

                    # Prevent an invalid stream without line endings from growing forever.
                    if len(pending) > 8192:
                        preview = pending[-240:].decode("ascii", errors="ignore")
                        events.put(
                            ReaderEvent(
                                raw_line=preview,
                                error="Received data without a newline; check STM32 \\r\\n output.",
                                status="Connected, invalid line format",
                                active_port=active_port,
                                received_bytes=total_bytes,
                            )
                        )
                        pending.clear()
        except (serial.SerialException, OSError) as exc:
            events.put(
                ReaderEvent(
                    error=f"Serial error: {exc}",
                    status="Disconnected; retrying every second",
                    active_port=active_port,
                )
            )
            stop_event.wait(1.0)


def euler_to_quaternion(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """ZYX Euler radians to quaternion [w, x, y, z]."""
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


def quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=float,
    )


def matrix_to_euler_deg(rotation: np.ndarray) -> np.ndarray:
    """Rotation matrix to ZYX roll, pitch, yaw in degrees."""
    sin_pitch = float(np.clip(-rotation[2, 0], -1.0, 1.0))
    pitch = math.asin(sin_pitch)
    if abs(math.cos(pitch)) > 1e-6:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = 0.0
    return np.rad2deg(np.array([roll, pitch, yaw], dtype=float))


def initial_quaternion_from_accel(acc_g: np.ndarray) -> np.ndarray:
    """Create initial roll/pitch from gravity and keep yaw at zero."""
    ax, ay, az = acc_g
    roll = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
    q = euler_to_quaternion(roll, pitch, 0.0)
    return q / np.linalg.norm(q)


@dataclass
class GyroCalibrator:
    duration_s: float = 2.5
    gyro_limit_dps: float = 12.0
    accel_tolerance_g: float = 0.40
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
        acc_norm = float(np.linalg.norm(sample.acc_g))
        gyro_norm = float(np.linalg.norm(sample.gyro_dps))
        stationary = (
            abs(acc_norm - 1.0) <= self.accel_tolerance_g
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
        elapsed = sample.received_at - self.started_at
        if elapsed >= self.duration_s and len(self.samples) >= 10:
            data = np.vstack(self.samples)
            self.bias_dps = np.median(data, axis=0)
            self.ready = True

    @property
    def progress(self) -> float:
        if self.ready:
            return 1.0
        if self.started_at is None or self.last_sample_at is None or not self.samples:
            return 0.0
        return min(1.0, (self.last_sample_at - self.started_at) / self.duration_s)


class Mahony6D:
    """Six-axis Mahony filter using gyro and the gravity direction."""

    def __init__(self, kp: float = 2.0, ki: float = 0.05) -> None:
        self.kp = kp
        self.ki = ki
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)
        self.integral = np.zeros(3)
        self.initialized = False

    def reset(self) -> None:
        self.q[:] = [1.0, 0.0, 0.0, 0.0]
        self.integral[:] = 0.0
        self.initialized = False

    def set_from_accel(self, acc_g: np.ndarray) -> None:
        norm = float(np.linalg.norm(acc_g))
        if norm > 1e-6:
            self.q = initial_quaternion_from_accel(acc_g / norm)
            self.initialized = True

    def update(self, gyro_dps: np.ndarray, acc_g: np.ndarray, dt: float) -> None:
        if not self.initialized:
            self.set_from_accel(acc_g)
        if not self.initialized or not 0.001 <= dt <= 0.25:
            return

        gyro = np.deg2rad(gyro_dps.astype(float))
        acc_norm = float(np.linalg.norm(acc_g))
        acc_valid = 0.75 <= acc_norm <= 1.25

        if acc_valid:
            ax, ay, az = acc_g / acc_norm
            w, x, y, z = self.q

            # Predicted gravity direction in the sensor/body coordinate frame.
            vx = 2.0 * (x * z - w * y)
            vy = 2.0 * (w * x + y * z)
            vz = w * w - x * x - y * y + z * z

            # Cross product: measured gravity x predicted gravity.
            error = np.array(
                [ay * vz - az * vy, az * vx - ax * vz, ax * vy - ay * vx],
                dtype=float,
            )
            self.integral += self.ki * error * dt
            self.integral = np.clip(self.integral, -0.1, 0.1)
            gyro += self.kp * error + self.integral

        w, x, y, z = self.q
        gx, gy, gz = gyro
        q_dot = 0.5 * np.array(
            [
                -x * gx - y * gy - z * gz,
                w * gx + y * gz - z * gy,
                w * gy - x * gz + z * gx,
                w * gz + x * gy - y * gx,
            ],
            dtype=float,
        )
        self.q += q_dot * dt
        norm = float(np.linalg.norm(self.q))
        if norm < 1e-9:
            self.reset()
        else:
            self.q /= norm

    @property
    def rotation_matrix(self) -> np.ndarray:
        return quaternion_to_matrix(self.q)


def cuboid_faces(size: tuple[float, float, float], rotation: np.ndarray) -> list[list[np.ndarray]]:
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
        (0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
        (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4),
    )
    return [[corners[index] for index in face] for face in indices]


@dataclass
class ViewerState:
    latest: Optional[ImuSample] = None
    rotation_matrix: np.ndarray = field(default_factory=lambda: np.eye(3))
    zero_rotation: np.ndarray = field(default_factory=lambda: np.eye(3))
    history: Deque[tuple[float, np.ndarray]] = field(default_factory=lambda: deque(maxlen=1000))
    raw_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=8))
    parsed_count: int = 0
    ignored_count: int = 0
    serial_error: str = ""
    connection_status: str = "Starting"
    active_port: str = ""
    last_packet_at: Optional[float] = None
    received_bytes: int = 0
    paused: bool = False
    gyro_calibrator: GyroCalibrator = field(default_factory=GyroCalibrator)
    mahony6d: Mahony6D = field(default_factory=Mahony6D)
    previous_sample_at: Optional[float] = None

    def consume(self, sample: ImuSample) -> None:
        self.latest = sample
        self.last_packet_at = time.monotonic()
        self.parsed_count += 1
        if sample.firmware_fusion:
            roll, pitch, yaw = np.deg2rad(sample.rpy_deg)
            quaternion = euler_to_quaternion(float(roll), float(pitch), float(yaw))
            self.rotation_matrix = quaternion_to_matrix(quaternion)
        else:
            self.gyro_calibrator.update(sample)
            if not self.mahony6d.initialized:
                self.mahony6d.set_from_accel(sample.acc_g)
            if self.previous_sample_at is not None and self.gyro_calibrator.ready:
                dt = sample.received_at - self.previous_sample_at
                corrected_gyro = sample.gyro_dps - self.gyro_calibrator.bias_dps
                self.mahony6d.update(corrected_gyro, sample.acc_g, dt)
            self.rotation_matrix = self.mahony6d.rotation_matrix
        self.previous_sample_at = sample.received_at
        relative = self.zero_rotation.T @ self.rotation_matrix
        rpy = matrix_to_euler_deg(relative)
        self.history.append((sample.received_at, rpy))

    def reset_legacy_fusion(self) -> None:
        self.gyro_calibrator.reset()
        self.mahony6d.reset()
        self.previous_sample_at = None
        self.history.clear()
        self.zero_rotation = np.eye(3)


def draw_3d(ax, state: ViewerState) -> None:
    elev, azim = ax.elev, ax.azim
    ax.cla()
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlim(-1.8, 1.8)
    ax.set_ylim(-1.8, 1.8)
    ax.set_zlim(-1.8, 1.8)
    ax.set_box_aspect((1, 1, 1))
    ax.set_xlabel("World X")
    ax.set_ylabel("World Y")
    ax.set_zlabel("World Z")
    fusion_name = "firmware 9-axis" if state.latest and state.latest.firmware_fusion else "PC Mahony 6-axis"
    ax.set_title(f"ICM-20948 orientation ({fusion_name})")

    for vector, color, label in (
        (np.array([1.4, 0.0, 0.0]), "#ef4444", "World X"),
        (np.array([0.0, 1.4, 0.0]), "#22c55e", "World Y"),
        (np.array([0.0, 0.0, 1.4]), "#3b82f6", "World Z"),
    ):
        ax.plot([0, vector[0]], [0, vector[1]], [0, vector[2]], color=color, alpha=0.3, linestyle="--")
        ax.text(*vector, label, color=color, alpha=0.7)

    rotation = state.zero_rotation.T @ state.rotation_matrix
    board = Poly3DCollection(
        cuboid_faces((2.2, 1.2, 0.18), rotation),
        facecolors="#38bdf8",
        edgecolors="#0f172a",
        linewidths=1.0,
        alpha=0.70,
    )
    ax.add_collection3d(board)

    for body_vector, color, label in (
        (np.array([1.35, 0.0, 0.0]), "red", "X"),
        (np.array([0.0, 1.35, 0.0]), "green", "Y"),
        (np.array([0.0, 0.0, 1.35]), "blue", "Z"),
    ):
        tip = rotation @ body_vector
        ax.quiver(0, 0, 0, *tip, color=color, linewidth=2, arrow_length_ratio=0.10)
        ax.text(*tip, label, color=color, fontsize=9, fontweight="bold")

    # A short front marker makes yaw direction visually obvious.
    front = rotation @ np.array([1.25, 0.0, 0.25])
    ax.scatter(*front, color="orange", s=55, label="board front")

    if state.latest is not None:
        acc_norm = float(np.linalg.norm(state.latest.acc_g))
        if acc_norm > 1e-6:
            acc_world = rotation @ (state.latest.acc_g / acc_norm)
            ax.quiver(0, 0, 0, *acc_world, color="magenta", linewidth=2, arrow_length_ratio=0.12)
            ax.text(*acc_world, "Accel", color="magenta")


def draw_history(ax, state: ViewerState) -> None:
    ax.cla()
    ax.set_title("Roll / Pitch / Yaw history")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Angle (deg)")
    ax.grid(True, alpha=0.25)
    if len(state.history) < 2:
        ax.text(0.5, 0.5, "Waiting for calibration and samples...", ha="center", va="center", transform=ax.transAxes)
        return
    history = list(state.history)
    latest_time = history[-1][0]
    times = np.array([item[0] - latest_time for item in history])
    values = np.vstack([item[1] for item in history])
    visible = times >= -20.0
    for index, label, color in (
        (0, "roll", "tab:red"),
        (1, "pitch", "tab:green"),
        (2, "yaw", "tab:blue"),
    ):
        ax.plot(times[visible], values[visible, index], label=label, color=color, linewidth=1.5)
    ax.set_xlim(-20.0, 0.5)
    ax.legend(loc="upper left", fontsize=7)


def draw_status(ax, state: ViewerState, requested_port: str, baudrate: int, demo: bool) -> None:
    ax.cla()
    ax.axis("off")
    displayed_port = state.active_port or requested_port
    source = "DEMO" if demo else f"{displayed_port} @ {baudrate}"
    lines = [
        f"Source: {source}",
        f"Serial: {state.connection_status}",
        f"Parsed samples: {state.parsed_count}",
        f"Ignored lines: {state.ignored_count}",
        f"UART bytes: {state.received_bytes}",
    ]
    if state.last_packet_at is not None:
        lines.append(f"Last packet age: {time.monotonic() - state.last_packet_at:5.2f} s")
    if state.serial_error:
        lines.extend(["", state.serial_error])
    if state.latest is None:
        lines.extend(["", "Waiting for STM32 data..."])
        if state.connection_status == "Connected" and state.received_bytes == 0:
            lines.extend(["No UART bytes yet.", "Run STM32 and press RESET; check PA9 TX -> CH340 RX."])
        elif state.received_bytes > 0:
            lines.extend(["Bytes are arriving, but no line matched.", "Check ASCII format and 115200 baud."])
    else:
        acc = state.latest.acc_g
        gyro = state.latest.gyro_dps
        mag = state.latest.mag_ut
        relative = state.zero_rotation.T @ state.rotation_matrix
        rpy = matrix_to_euler_deg(relative)
        acc_status = "USED" if state.latest.acc_used else "REJECTED"
        mag_status = "USED" if state.latest.mag_used else "REJECTED"
        lines.extend(["", f"Accel: {acc[0]:7.3f}, {acc[1]:7.3f}, {acc[2]:7.3f} g"])
        lines.extend(
            [
                f"|A|:   {np.linalg.norm(acc):7.3f} g",
                f"Gyro: {gyro[0]:7.3f}, {gyro[1]:7.3f}, {gyro[2]:7.3f} dps",
                f"Temp:  {state.latest.temperature_c:7.2f} C",
            ]
        )
        if state.latest.firmware_fusion:
            lines.insert(5, f"Fusion: firmware 9-axis, ACC={acc_status}, MAG={mag_status}")
            lines.extend(
                [
                    f"Mag:   {mag[0]:7.2f}, {mag[1]:7.2f}, {mag[2]:7.2f} uT",
                    f"|M|:   {np.linalg.norm(mag):7.2f} uT",
                ]
            )
        else:
            calibration = "READY" if state.gyro_calibrator.ready else f"{state.gyro_calibrator.progress * 100:3.0f}%"
            lines.insert(5, f"Fusion: PC Mahony 6-axis, gyro calibration={calibration}")
        lines.extend(
            [
                "",
                f"Roll:  {rpy[0]:7.2f} deg",
                f"Pitch: {rpy[1]:7.2f} deg",
                f"Yaw:   {rpy[2]:7.2f} deg",
                "",
                "Keys: R=zero, C=recalibrate, Space=pause, Q=quit",
                f"Last UART: {state.latest.raw_line[:85]}",
            ]
        )
    ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left", family="monospace", fontsize=8)


def demo_reader(events: "queue.Queue[ReaderEvent]", stop_event: threading.Event) -> None:
    start = time.monotonic()
    rng = np.random.default_rng(20948)
    previous = start
    while not stop_event.is_set():
        now = time.monotonic()
        t = now - start
        if t < 3.0:
            roll = pitch = yaw = 0.0
            roll_rate = pitch_rate = yaw_rate = 0.0
        else:
            motion_t = t - 3.0
            roll = math.radians(28.0 * math.sin(0.65 * motion_t))
            pitch = math.radians(20.0 * math.sin(0.43 * motion_t))
            yaw = math.radians(35.0 * math.sin(0.22 * motion_t))
            roll_rate = 28.0 * 0.65 * math.cos(0.65 * motion_t)
            pitch_rate = 20.0 * 0.43 * math.cos(0.43 * motion_t)
            yaw_rate = 35.0 * 0.22 * math.cos(0.22 * motion_t)
        rotation = quaternion_to_matrix(euler_to_quaternion(roll, pitch, yaw))
        acc = rotation.T @ np.array([0.0, 0.0, 1.0]) + rng.normal(0.0, 0.005, 3)
        gyro = np.array([roll_rate, pitch_rate, yaw_rate]) + np.array([0.35, -0.22, 0.18]) + rng.normal(0.0, 0.03, 3)
        mag_world = np.array([24.0, 2.0, 41.0])
        mag = rotation.T @ mag_world + rng.normal(0.0, 0.05, 3)
        sample = ImuSample(
            received_at=now,
            acc_g=acc,
            gyro_dps=gyro,
            temperature_c=27.5,
            mag_ut=mag,
            rpy_deg=np.rad2deg(np.array([roll, pitch, yaw])),
            acc_used=True,
            mag_used=True,
            firmware_fusion=True,
            raw_line="demo",
        )
        events.put(ReaderEvent(sample=sample, raw_line="demo"))
        previous = now
        time.sleep(max(0.0, 0.02 - (time.monotonic() - previous)))


def run_self_test() -> int:
    fusion_line = (
        "A:-0.127,-1.301,-1.002 g  G:-5.527,-11.634,-4.702 dps  "
        "T:27.93 C  M:25.10,-3.20,41.80 uT  R:1.20,-2.30,87.40  USE:1,1"
    )
    sample = parse_sensor_line(fusion_line, received_at=1.0)
    assert sample is not None
    assert np.allclose(sample.acc_g, [-0.127, -1.301, -1.002])
    assert np.allclose(sample.gyro_dps, [-5.527, -11.634, -4.702])
    assert abs(sample.temperature_c - 27.93) < 1e-9
    assert np.allclose(sample.mag_ut, [25.10, -3.20, 41.80])
    assert np.allclose(sample.rpy_deg, [1.20, -2.30, 87.40])
    assert sample.acc_used and sample.mag_used
    assert sample.firmware_fusion

    legacy_line = "A:-0.127,-1.301,-1.002 g  G:-5.527,-11.634,-4.702 dps  T:27.93 C"
    legacy = parse_sensor_line(legacy_line, received_at=2.0)
    assert legacy is not None
    assert np.allclose(legacy.acc_g, [-0.127, -1.301, -1.002])
    assert np.allclose(legacy.gyro_dps, [-5.527, -11.634, -4.702])
    assert not legacy.firmware_fusion
    assert not legacy.mag_used

    state = ViewerState()
    state.consume(sample)
    assert np.all(np.isfinite(state.rotation_matrix))
    assert np.allclose(state.rotation_matrix.T @ state.rotation_matrix, np.eye(3), atol=1e-6)
    print("Self-test passed: legacy/full packet parsers and 3D orientation are working.")
    return 0


def monitor_stream(requested_port: Optional[str], baudrate: int, duration_s: float) -> int:
    """Print live parse results without opening the 3D window."""
    events: "queue.Queue[ReaderEvent]" = queue.Queue()
    stop_event = threading.Event()
    reader = threading.Thread(
        target=serial_reader,
        args=(requested_port, baudrate, events, stop_event),
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + max(1.0, duration_s)
    parsed = 0
    raw_count = 0
    received_bytes = 0
    last_status = ""
    try:
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if event.status and event.status != last_status:
                print(f"[{event.active_port or 'serial'}] {event.status}")
                last_status = event.status
            if event.error:
                print(event.error)
            received_bytes = max(received_bytes, event.received_bytes)
            if event.raw_line:
                raw_count += 1
                if event.sample is not None:
                    parsed += 1
                    print(f"[OK] {event.raw_line}")
                else:
                    print(f"[IGNORED] {event.raw_line}")
    finally:
        stop_event.set()
        reader.join(timeout=1.0)
    print(
        f"Monitor result: {parsed} parsed / {raw_count} received lines / "
        f"{received_bytes} UART bytes"
    )
    return 0 if parsed > 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="ICM-20948 realtime 3D viewer")
    parser.add_argument(
        "--port",
        help=f"Serial port, for example COM27; default: prefer {PREFERRED_PORT}, then auto-detect CH340",
    )
    parser.add_argument("--baud", type=int, default=115200, help="Baudrate, default: 115200")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    parser.add_argument("--demo", action="store_true", help="Run without hardware using simulated data")
    parser.add_argument("--self-test", action="store_true", help="Test parser/filter and exit")
    parser.add_argument(
        "--monitor",
        type=float,
        metavar="SECONDS",
        help="Read and print live UART parsing for N seconds without opening 3D",
    )
    args = parser.parse_args()

    if args.list_ports:
        list_available_ports()
        return 0
    if args.self_test:
        return run_self_test()
    if args.monitor is not None:
        return monitor_stream(args.port, args.baud, args.monitor)

    port_label = "DEMO" if args.demo else (args.port or f"{PREFERRED_PORT} / auto CH340")

    events: "queue.Queue[ReaderEvent]" = queue.Queue()
    stop_event = threading.Event()
    state = ViewerState()
    reader_target = demo_reader if args.demo else serial_reader
    reader_args = (events, stop_event) if args.demo else (args.port, args.baud, events, stop_event)
    reader = threading.Thread(target=reader_target, args=reader_args, daemon=True)
    reader.start()

    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
        }
    )
    fig = plt.figure(figsize=WINDOW_SIZE_INCHES)
    try:
        fig.canvas.manager.set_window_title("STM32F103 + ICM-20948 Realtime 3D")
    except AttributeError:
        pass
    grid = GridSpec(2, 3, figure=fig, width_ratios=(1.2, 1.2, 1.0), height_ratios=(1.0, 1.0))
    ax_3d = fig.add_subplot(grid[:, :2], projection="3d")
    ax_history = fig.add_subplot(grid[0, 2])
    ax_status = fig.add_subplot(grid[1, 2])
    draw_3d(ax_3d, state)
    draw_history(ax_history, state)
    draw_status(ax_status, state, port_label, args.baud, args.demo)
    fig.tight_layout()

    def on_key(event) -> None:
        key = (event.key or "").lower()
        if key == "r":
            state.zero_rotation = state.rotation_matrix.copy()
            state.history.clear()
        elif key == "c":
            state.reset_legacy_fusion()
        elif key == " ":
            state.paused = not state.paused
        elif key in ("q", "escape"):
            plt.close(fig)

    def update(_frame):
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event.error:
                state.serial_error = event.error
            if event.status:
                state.connection_status = event.status
                if event.status == "Connected":
                    state.serial_error = ""
            if event.active_port:
                state.active_port = event.active_port
            state.received_bytes = max(state.received_bytes, event.received_bytes)
            if event.raw_line:
                state.raw_lines.append(event.raw_line)
            if event.sample is not None:
                state.consume(event.sample)
            elif event.raw_line:
                state.ignored_count += 1
        if not state.paused:
            draw_3d(ax_3d, state)
            draw_history(ax_history, state)
            draw_status(ax_status, state, port_label, args.baud, args.demo)
        return []

    fig.canvas.mpl_connect("key_press_event", on_key)
    animation = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
    try:
        plt.show()
    finally:
        # Keep a reference so matplotlib does not garbage-collect the animation.
        _ = animation
        stop_event.set()
        reader.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

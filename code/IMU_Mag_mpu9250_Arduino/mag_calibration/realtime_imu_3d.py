"""Real-time MPU6500 + AK8963 filtering, Mahony fusion, and 3D orientation display.

Expected serial columns (emitted by mpu6500_ak8963_imu_stream.ino):
    micros,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,mx_cal,my_cal,mz_cal

The quaternion represents the body frame orientation in a local reference
frame. The local X axis is initialized toward magnetic north, Z points up.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import serial
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from serial.tools import list_ports


ACCEL_LSB_PER_G = 8192.0       # MPU6500 +/-4 g
GYRO_LSB_PER_DPS = 16.4        # MPU6500 +/-2000 degrees/s
MAG_UT_PER_LSB = 0.15          # AK8963 16-bit nominal sensitivity
NOMINAL_DT = 0.01              # 100 Hz fallback


@dataclass
class ImuSample:
    timestamp_us: int
    accel_raw: np.ndarray
    gyro_raw: np.ndarray
    mag_calibrated: np.ndarray


def parse_axis_map(text: str) -> np.ndarray:
    """Convert e.g. 'x,-y,z' into a 3x3 remapping matrix."""
    tokens = [token.strip().lower() for token in text.split(",")]
    if len(tokens) != 3:
        raise argparse.ArgumentTypeError("轴映射必须有三项，例如 x,y,z 或 y,x,-z")

    basis = {
        "x": np.array([1.0, 0.0, 0.0]),
        "y": np.array([0.0, 1.0, 0.0]),
        "z": np.array([0.0, 0.0, 1.0]),
    }
    rows = []
    used = set()
    for token in tokens:
        sign = -1.0 if token.startswith("-") else 1.0
        axis = token.lstrip("+-")
        if axis not in basis or axis in used:
            raise argparse.ArgumentTypeError("每个轴 x/y/z 必须且只能使用一次")
        used.add(axis)
        rows.append(sign * basis[axis])
    return np.vstack(rows)


def available_ports() -> str:
    ports = list(list_ports.comports())
    if not ports:
        return "  (未发现串口)"
    return "\n".join(f"  {port.device}: {port.description}" for port in ports)


def parse_serial_line(line: str) -> ImuSample | None:
    line = line.strip()
    if not line or line.startswith("#"):
        if line.startswith("#"):
            print(line)
        return None

    fields = line.split(",")
    if len(fields) != 10:
        return None
    try:
        values = [float(field) for field in fields]
    except ValueError:
        return None

    return ImuSample(
        timestamp_us=int(values[0]),
        accel_raw=np.asarray(values[1:4], dtype=float),
        gyro_raw=np.asarray(values[4:7], dtype=float),
        mag_calibrated=np.asarray(values[7:10], dtype=float),
    )


def read_sample(port: serial.Serial, deadline: float | None = None) -> ImuSample | None:
    while deadline is None or time.monotonic() < deadline:
        raw_line = port.readline()
        if not raw_line:
            continue
        line = raw_line.decode("ascii", errors="ignore")
        sample = parse_serial_line(line)
        if sample is not None:
            return sample
    return None


class LowPass:
    def __init__(self, cutoff_hz: float):
        self.cutoff_hz = cutoff_hz
        self.state: np.ndarray | None = None

    def update(self, value: np.ndarray, dt: float) -> np.ndarray:
        if self.state is None:
            self.state = value.astype(float).copy()
            return self.state.copy()
        if self.cutoff_hz <= 0.0:
            self.state = value.astype(float).copy()
            return self.state.copy()

        rc = 1.0 / (2.0 * math.pi * self.cutoff_hz)
        alpha = dt / (rc + dt)
        self.state += alpha * (value - self.state)
        return self.state.copy()


def normalize(vector: np.ndarray) -> np.ndarray | None:
    length = float(np.linalg.norm(vector))
    if not math.isfinite(length) or length < 1e-9:
        return None
    return vector / length


def quaternion_from_matrix(matrix: np.ndarray) -> np.ndarray:
    """Return scalar-first quaternion [w, x, y, z] from a rotation matrix."""
    m = matrix
    trace = float(np.trace(m))
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = np.array([
            0.25 * s,
            (m[2, 1] - m[1, 2]) / s,
            (m[0, 2] - m[2, 0]) / s,
            (m[1, 0] - m[0, 1]) / s,
        ])
    else:
        index = int(np.argmax(np.diag(m)))
        if index == 0:
            s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
            q = np.array([
                (m[2, 1] - m[1, 2]) / s,
                0.25 * s,
                (m[0, 1] + m[1, 0]) / s,
                (m[0, 2] + m[2, 0]) / s,
            ])
        elif index == 1:
            s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
            q = np.array([
                (m[0, 2] - m[2, 0]) / s,
                (m[0, 1] + m[1, 0]) / s,
                0.25 * s,
                (m[1, 2] + m[2, 1]) / s,
            ])
        else:
            s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
            q = np.array([
                (m[1, 0] - m[0, 1]) / s,
                (m[0, 2] + m[2, 0]) / s,
                (m[1, 2] + m[2, 1]) / s,
                0.25 * s,
            ])
    return q / np.linalg.norm(q)


def initial_orientation(accel_g: np.ndarray, mag_ut: np.ndarray) -> np.ndarray:
    """TRIAD initialization: local X=north, local Z=up."""
    up_body = normalize(accel_g)
    if up_body is None:
        return np.array([1.0, 0.0, 0.0, 0.0])

    north_body = mag_ut - np.dot(mag_ut, up_body) * up_body
    north_body = normalize(north_body)
    if north_body is None:
        return np.array([1.0, 0.0, 0.0, 0.0])

    # X=north, Y=west, Z=up is right-handed. Columns contain local reference
    # axes expressed in body coordinates, so transpose gives body -> local.
    west_body = normalize(np.cross(up_body, north_body))
    if west_body is None:
        return np.array([1.0, 0.0, 0.0, 0.0])
    north_body = normalize(np.cross(west_body, up_body))
    reference_to_body = np.column_stack((north_body, west_body, up_body))
    return quaternion_from_matrix(reference_to_body.T)


def quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z),
         2.0 * (x * z + w * y)],
        [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z),
         2.0 * (y * z - w * x)],
        [2.0 * (x * z - w * y), 2.0 * (y * z + w * x),
         1.0 - 2.0 * (x * x + y * y)],
    ])


def matrix_to_euler_degrees(rotation: np.ndarray) -> np.ndarray:
    """ZYX Euler angles as roll, pitch, yaw in degrees."""
    pitch = math.asin(float(np.clip(-rotation[2, 0], -1.0, 1.0)))
    if abs(math.cos(pitch)) > 1e-6:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = 0.0
    return np.degrees([roll, pitch, yaw])


class MahonyAhrs:
    """Mahony explicit complementary filter with optional magnetic correction."""

    def __init__(self, kp: float, ki: float, initial_q: np.ndarray):
        self.two_kp = 2.0 * kp
        self.two_ki = 2.0 * ki
        self.integral = np.zeros(3)
        self.q = initial_q.astype(float).copy()

    def update(self, gyro_rad_s: np.ndarray, accel_g: np.ndarray,
               mag_ut: np.ndarray | None, dt: float) -> np.ndarray:
        q0, q1, q2, q3 = self.q
        gyro = gyro_rad_s.astype(float).copy()

        accel_norm = float(np.linalg.norm(accel_g))
        accel_valid = 0.65 <= accel_norm <= 1.35
        accel = normalize(accel_g) if accel_valid else None
        mag = normalize(mag_ut) if mag_ut is not None else None

        if accel is not None:
            ax, ay, az = accel
            q0q0 = q0 * q0
            q0q1 = q0 * q1
            q0q2 = q0 * q2
            q0q3 = q0 * q3
            q1q1 = q1 * q1
            q1q2 = q1 * q2
            q1q3 = q1 * q3
            q2q2 = q2 * q2
            q2q3 = q2 * q3
            q3q3 = q3 * q3

            # Estimated gravity direction in the body frame.
            half_v = np.array([
                q1q3 - q0q2,
                q0q1 + q2q3,
                q0q0 - 0.5 + q3q3,
            ])
            half_error = np.cross(accel, 2.0 * half_v) * 0.5

            if mag is not None:
                mx, my, mz = mag
                hx = 2.0 * (
                    mx * (0.5 - q2q2 - q3q3)
                    + my * (q1q2 - q0q3)
                    + mz * (q1q3 + q0q2)
                )
                hy = 2.0 * (
                    mx * (q1q2 + q0q3)
                    + my * (0.5 - q1q1 - q3q3)
                    + mz * (q2q3 - q0q1)
                )
                bx = math.sqrt(hx * hx + hy * hy)
                bz = 2.0 * (
                    mx * (q1q3 - q0q2)
                    + my * (q2q3 + q0q1)
                    + mz * (0.5 - q1q1 - q2q2)
                )
                half_w = np.array([
                    bx * (0.5 - q2q2 - q3q3) + bz * (q1q3 - q0q2),
                    bx * (q1q2 - q0q3) + bz * (q0q1 + q2q3),
                    bx * (q0q2 + q1q3) + bz * (0.5 - q1q1 - q2q2),
                ])
                half_error += np.cross(mag, 2.0 * half_w) * 0.5

            if self.two_ki > 0.0:
                self.integral += self.two_ki * half_error * dt
                gyro += self.integral
            else:
                self.integral.fill(0.0)
            gyro += self.two_kp * half_error

        # Quaternion derivative from corrected angular velocity.
        gx, gy, gz = 0.5 * gyro * dt
        qa, qb, qc = q0, q1, q2
        q0 += -qb * gx - qc * gy - q3 * gz
        q1 += qa * gx + qc * gz - q3 * gy
        q2 += qa * gy - qb * gz + q3 * gx
        q3 += qa * gz + qb * gy - qc * gx

        self.q = np.array([q0, q1, q2, q3])
        self.q /= np.linalg.norm(self.q)
        return self.q.copy()


class LivePlot:
    def __init__(self, history_seconds: float = 10.0):
        plt.ion()
        self.figure = plt.figure(figsize=(13, 7))
        self.axis_3d = self.figure.add_subplot(1, 2, 1, projection="3d")
        self.axis_history = self.figure.add_subplot(1, 2, 2)
        try:
            self.figure.canvas.manager.set_window_title("MPU6500 + AK8963 实时姿态")
        except AttributeError:
            pass

        ax = self.axis_3d
        ax.set_xlim(-1.2, 1.2)
        ax.set_ylim(-1.2, 1.2)
        ax.set_zlim(-1.2, 1.2)
        ax.set_box_aspect((1, 1, 1))
        ax.set_xlabel("North / X")
        ax.set_ylabel("West / Y")
        ax.set_zlabel("Up / Z")
        ax.set_title("实时三维姿态")

        # Body axes: X red, Y green, Z blue.
        self.body_axes = [
            ax.plot([0, 1], [0, 0], [0, 0], color="r", linewidth=3)[0],
            ax.plot([0, 0], [0, 1], [0, 0], color="g", linewidth=3)[0],
            ax.plot([0, 0], [0, 0], [0, 1], color="b", linewidth=3)[0],
        ]
        self.block_faces = Poly3DCollection(
            [],
            facecolors=["#88aadd", "#88aadd", "#6f91c4",
                        "#6f91c4", "#789bd0", "#789bd0"],
            edgecolors="#26364d",
            linewidths=1.5,
            alpha=0.72,
        )
        ax.add_collection3d(self.block_faces)
        self.nose_line = ax.plot([], [], [], color="#ff8800", linewidth=5)[0]
        self.status_text = ax.text2D(0.02, 0.96, "", transform=ax.transAxes,
                                     va="top", family="monospace")

        hist = self.axis_history
        hist.set_title("最近姿态角")
        hist.set_xlabel("时间 (s)")
        hist.set_ylabel("角度 (deg)")
        hist.set_ylim(-185, 185)
        hist.grid(True, alpha=0.3)
        self.history_seconds = history_seconds
        self.times: deque[float] = deque(maxlen=2000)
        self.rolls: deque[float] = deque(maxlen=2000)
        self.pitches: deque[float] = deque(maxlen=2000)
        self.yaws: deque[float] = deque(maxlen=2000)
        self.history_lines = [
            hist.plot([], [], "r-", label="Roll")[0],
            hist.plot([], [], "g-", label="Pitch")[0],
            hist.plot([], [], "b-", label="Yaw")[0],
        ]
        hist.legend(loc="upper right")
        self.figure.tight_layout()
        plt.show(block=False)

        # The previous flat rectangle was 1.40 along X and 0.84 along Y.
        # Swap length/width and add 0.30 thickness so it reads as a small block.
        hx, hy, hz = 0.42, 0.70, 0.15
        self.block_vertices_body = np.array([
            [-hx, -hy, -hz],
            [ hx, -hy, -hz],
            [ hx,  hy, -hz],
            [-hx,  hy, -hz],
            [-hx, -hy,  hz],
            [ hx, -hy,  hz],
            [ hx,  hy,  hz],
            [-hx,  hy,  hz],
        ])
        self.block_face_indices = (
            (0, 1, 2, 3),  # bottom
            (4, 5, 6, 7),  # top
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        )
        self.nose_body = np.array([
            [0.10, 0.0, hz + 0.03],
            [hx, 0.0, hz + 0.03],
        ]).T

    def is_open(self) -> bool:
        return plt.fignum_exists(self.figure.number)

    @staticmethod
    def set_line_3d(line, points: np.ndarray) -> None:
        line.set_data(points[0], points[1])
        line.set_3d_properties(points[2])

    def update(self, rotation: np.ndarray, euler: np.ndarray, elapsed: float,
               accel_norm: float, gyro_norm: float, mag_norm: float,
               magnetic_correction: bool) -> None:
        for index, line in enumerate(self.body_axes):
            endpoint = rotation[:, index]
            self.set_line_3d(line, np.column_stack((np.zeros(3), endpoint)))

        rotated_vertices = (rotation @ self.block_vertices_body.T).T
        self.block_faces.set_verts([
            rotated_vertices[list(face)] for face in self.block_face_indices
        ])
        self.set_line_3d(self.nose_line, rotation @ self.nose_body)

        roll, pitch, yaw = euler
        self.status_text.set_text(
            f"Roll  {roll:8.2f} deg\n"
            f"Pitch {pitch:8.2f} deg\n"
            f"Yaw   {yaw:8.2f} deg\n\n"
            f"|a|   {accel_norm:8.3f} g\n"
            f"|gyro|{gyro_norm:8.2f} dps\n"
            f"|mag| {mag_norm:8.2f} uT\n"
            f"Mag correction: {'ON' if magnetic_correction else 'REJECTED'}"
        )

        self.times.append(elapsed)
        self.rolls.append(roll)
        self.pitches.append(pitch)
        self.yaws.append(yaw)
        x_values = np.asarray(self.times)
        for line, values in zip(
            self.history_lines, (self.rolls, self.pitches, self.yaws)
        ):
            line.set_data(x_values, np.asarray(values))

        left = max(0.0, elapsed - self.history_seconds)
        self.axis_history.set_xlim(left, max(self.history_seconds, elapsed))
        self.figure.canvas.draw_idle()
        self.figure.canvas.flush_events()


def calculate_dt(timestamp_us: int, previous_us: int | None) -> float:
    if previous_us is None:
        return NOMINAL_DT
    delta_us = (timestamp_us - previous_us) & 0xFFFFFFFF
    dt = delta_us * 1e-6
    if not 0.001 <= dt <= 0.1:
        return NOMINAL_DT
    return dt


def collect_stationary_calibration(
    port: serial.Serial,
    duration: float,
    ag_map: np.ndarray,
    mag_map: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    print(f"\n请将设备静止、平放 {duration:.1f} 秒，正在估算陀螺仪零偏……")
    gyro_samples = []
    accel_samples = []
    mag_samples = []
    started: float | None = None
    overall_deadline = time.monotonic() + duration + 8.0

    while time.monotonic() < overall_deadline:
        sample = read_sample(port, deadline=time.monotonic() + 0.5)
        if sample is None:
            continue
        if started is None:
            started = time.monotonic()

        accel_samples.append(ag_map @ (sample.accel_raw / ACCEL_LSB_PER_G))
        gyro_samples.append(ag_map @ (sample.gyro_raw / GYRO_LSB_PER_DPS))
        mag_samples.append(mag_map @ (sample.mag_calibrated * MAG_UT_PER_LSB))
        if time.monotonic() - started >= duration:
            break

    if len(gyro_samples) < 30:
        raise RuntimeError("静止校准期间收到的数据不足；请检查端口和波特率")

    gyro_bias = np.mean(gyro_samples, axis=0)
    accel_mean = np.mean(accel_samples, axis=0)
    mag_mean = np.mean(mag_samples, axis=0)
    accel_spread = np.std(np.linalg.norm(accel_samples, axis=1))
    gyro_spread = np.std(np.linalg.norm(gyro_samples, axis=1))

    print(f"收到 {len(gyro_samples)} 个静止样本")
    print("陀螺零偏 (dps): " + np.array2string(gyro_bias, precision=4))
    print(f"静止稳定度: accel std={accel_spread:.5f} g, gyro std={gyro_spread:.5f} dps")
    if accel_spread > 0.02 or gyro_spread > 1.0:
        print("警告：静止阶段移动较明显，建议关闭程序并重新运行。")
    return gyro_bias, accel_mean, mag_mean


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MPU6500 + AK8963 实时滤波、Mahony 九轴融合和 3D 可视化"
    )
    parser.add_argument("port", help="串口，例如 COM5 或 /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--stationary-seconds", type=float, default=3.0,
                        help="启动时静止校准时间，默认 3 秒")
    parser.add_argument("--kp", type=float, default=0.8,
                        help="Mahony 比例增益，默认 0.8")
    parser.add_argument("--ki", type=float, default=0.02,
                        help="Mahony 积分增益，默认 0.02")
    parser.add_argument("--accel-cutoff", type=float, default=20.0,
                        help="加速度软件低通截止频率 Hz")
    parser.add_argument("--gyro-cutoff", type=float, default=25.0,
                        help="陀螺仪软件低通截止频率 Hz")
    parser.add_argument("--mag-cutoff", type=float, default=10.0,
                        help="磁力计软件低通截止频率 Hz")
    parser.add_argument("--ag-map", type=parse_axis_map, default=parse_axis_map("x,y,z"),
                        metavar="MAP", help="加速度/陀螺轴映射，例如 x,y,z")
    parser.add_argument(
        "--mag-map",
        type=parse_axis_map,
        default=parse_axis_map("y,-x,-z"),
        metavar="MAP",
        help="磁力计轴映射，默认 y,-x,-z",
    )
    parser.add_argument("--log", type=Path,
                        help="可选：把滤波后的物理量和姿态保存为 CSV")
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    print("可用串口：")
    print(available_ports())
    print(f"\n连接 {args.port} @ {args.baud} baud")

    log_file = None
    log_writer = None
    try:
        port = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as exc:
        print(f"无法打开串口：{exc}", file=sys.stderr)
        return 1

    try:
        # Opening an Uno/Nano serial port normally resets the board.
        time.sleep(2.0)
        port.reset_input_buffer()

        gyro_bias, accel_mean, mag_mean = collect_stationary_calibration(
            port, args.stationary_seconds, args.ag_map, args.mag_map
        )
        initial_q = initial_orientation(accel_mean, mag_mean)
        ahrs = MahonyAhrs(args.kp, args.ki, initial_q)

        accel_filter = LowPass(args.accel_cutoff)
        gyro_filter = LowPass(args.gyro_cutoff)
        mag_filter = LowPass(args.mag_cutoff)
        magnetic_reference = float(np.linalg.norm(mag_mean))
        plot = LivePlot()

        if args.log:
            log_file = args.log.open("w", newline="", encoding="utf-8")
            log_writer = csv.writer(log_file)
            log_writer.writerow([
                "time_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps",
                "gz_dps", "mx_uT", "my_uT", "mz_uT", "roll_deg",
                "pitch_deg", "yaw_deg", "qw", "qx", "qy", "qz",
            ])

        previous_timestamp = None
        elapsed = 0.0
        last_plot_time = 0.0
        print("\n融合开始。关闭图窗或按 Ctrl+C 结束。")
        print("3D 红/绿/蓝线分别是传感器 X/Y/Z 轴，橙色短线是板体前向。")

        while plot.is_open():
            sample = read_sample(port, deadline=time.monotonic() + 0.2)
            if sample is None:
                continue

            dt = calculate_dt(sample.timestamp_us, previous_timestamp)
            previous_timestamp = sample.timestamp_us
            elapsed += dt

            accel_g = args.ag_map @ (sample.accel_raw / ACCEL_LSB_PER_G)
            gyro_dps = args.ag_map @ (sample.gyro_raw / GYRO_LSB_PER_DPS)
            gyro_dps -= gyro_bias
            mag_ut = args.mag_map @ (sample.mag_calibrated * MAG_UT_PER_LSB)

            accel_g = accel_filter.update(accel_g, dt)
            gyro_dps = gyro_filter.update(gyro_dps, dt)
            mag_ut = mag_filter.update(mag_ut, dt)

            mag_norm = float(np.linalg.norm(mag_ut))
            # Reject magnetic correction when field strength differs strongly
            # from the slowly tracked local reference.
            magnetic_correction = (
                magnetic_reference > 1e-6
                and 0.70 * magnetic_reference <= mag_norm <= 1.30 * magnetic_reference
            )
            if magnetic_correction:
                tracking_alpha = min(1.0, dt / 30.0)
                magnetic_reference += tracking_alpha * (mag_norm - magnetic_reference)
                mag_for_fusion = mag_ut
            else:
                mag_for_fusion = None

            q = ahrs.update(
                np.radians(gyro_dps), accel_g, mag_for_fusion, dt
            )
            rotation = quaternion_to_matrix(q)
            euler = matrix_to_euler_degrees(rotation)

            if log_writer is not None:
                log_writer.writerow([
                    elapsed, *accel_g, *gyro_dps, *mag_ut, *euler, *q
                ])

            now = time.monotonic()
            if now - last_plot_time >= 1.0 / 30.0:
                plot.update(
                    rotation,
                    euler,
                    elapsed,
                    float(np.linalg.norm(accel_g)),
                    float(np.linalg.norm(gyro_dps)),
                    mag_norm,
                    magnetic_correction,
                )
                plt.pause(0.001)
                # Use the time after rendering so a slow GUI draw does not
                # immediately trigger another draw and starve serial parsing.
                last_plot_time = time.monotonic()

    except KeyboardInterrupt:
        print("\n用户停止。")
    except (RuntimeError, serial.SerialException) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
    finally:
        port.close()
        if log_file is not None:
            log_file.close()
            print(f"日志已保存：{args.log.resolve()}")
        plt.close("all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

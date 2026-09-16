#!/usr/bin/env python3
"""Matplotlib real-time 3D attitude display for JY901P LIVE serial data."""

from __future__ import annotations

import argparse
import math
import queue
import re
import threading
import time
from dataclasses import dataclass

try:
    import numpy as np
    import serial
    from serial.tools import list_ports

    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.patches import Circle, Rectangle
    from matplotlib.transforms import Affine2D
    from matplotlib.widgets import Button, TextBox
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
except ImportError as exc:  # pragma: no cover - user environment check
    raise SystemExit(
        "Missing dependency. Run: python -m pip install -r requirements.txt"
    ) from exc


LIVE_PATTERN = re.compile(
    r"^LIVE,ms=(?P<ms>\d+),address=(?P<address>0x[0-9A-Fa-f]+),"
    r"rpy_deg=(?P<roll>-?[0-9.]+),(?P<pitch>-?[0-9.]+),(?P<yaw>-?[0-9.]+),"
    r"gyro_dps=(?P<gx>-?[0-9.]+),(?P<gy>-?[0-9.]+),(?P<gz>-?[0-9.]+),"
    r"mag_raw=(?P<mx>-?\d+),(?P<my>-?\d+),(?P<mz>-?\d+)"
    r"(?P<tail>.*)$"
)

DEFAULT_PORT = "COM6"


@dataclass(slots=True)
class LiveSample:
    device_ms: int
    address: str
    roll: float
    pitch: float
    yaw: float
    gyro: tuple[float, float, float]
    cal_mode: str = "-"
    cal_progress: str = "-"
    cal_quality: str = "-"


def tail_value(tail: str, field: str) -> str:
    match = re.search(rf"(?:^|,){re.escape(field)}=([^,]+)", tail)
    return match.group(1) if match else "-"


def parse_live_line(line: str) -> LiveSample | None:
    match = LIVE_PATTERN.match(line.strip())
    if not match:
        return None
    data = match.groupdict()
    return LiveSample(
        device_ms=int(data["ms"]),
        address=data["address"],
        roll=float(data["roll"]),
        pitch=float(data["pitch"]),
        yaw=float(data["yaw"]),
        gyro=(float(data["gx"]), float(data["gy"]), float(data["gz"])),
        cal_mode=tail_value(data["tail"], "cal_mode"),
        cal_progress=tail_value(data["tail"], "cal_progress"),
        cal_quality=tail_value(data["tail"], "cal_quality"),
    )


class SerialReader:
    def __init__(self, events: queue.Queue[tuple[str, object]]) -> None:
        self.events = events
        self.port: serial.Serial | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.port is not None and self.port.is_open

    def connect(self, port_name: str, baud: int) -> None:
        self.close()
        self.stop_event.clear()
        self.port = serial.Serial(port_name, baud, timeout=0.2)
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        assert self.port is not None
        try:
            while not self.stop_event.is_set():
                raw = self.port.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                sample = parse_live_line(line)
                if sample is not None:
                    self.events.put(("sample", sample))
                elif line:
                    self.events.put(("log", line))
        except Exception as exc:
            if not self.stop_event.is_set():
                self.events.put(("error", str(exc)))

    def close(self) -> None:
        self.stop_event.set()
        port = self.port
        self.port = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def send(self, command: str) -> None:
        if not self.connected:
            raise RuntimeError("串口尚未连接")
        assert self.port is not None
        self.port.write(command.encode("ascii"))
        self.port.flush()


def rotation_zyx(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    roll, pitch, yaw = np.radians([roll_deg, pitch_deg, yaw_deg])
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=float)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=float)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=float)
    return rz @ ry @ rx


class AttitudeViewer:
    BG = "#fefefe"
    PANEL = "#f8f9f9"
    TEXT = "#011726"
    MUTED = "#020C13"
    RED = "#ff667a"
    GREEN = "#65e6a5"
    CYAN = "#45d7ff"
    YELLOW = "#ffd166"

    def __init__(self, initial_port: str | None, baud: int, demo: bool) -> None:
        self.baud = baud
        self.demo = demo
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.reader = SerialReader(self.events)
        self.sample: LiveSample | None = None
        self.last_sample_wall_time = 0.0
        self.status_message = "DEMO模式" if demo else "请选择串口并连接"

        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        self.figure = plt.figure(figsize=(14, 8.5), facecolor=self.BG)
        try:
            self.figure.canvas.manager.set_window_title("JY901P Matplotlib 实时三维姿态")
        except Exception:
            pass

        grid = self.figure.add_gridspec(
            2,
            2,
            width_ratios=(1.75, 1.0),
            height_ratios=(1.0, 1.0),
            left=0.04,
            right=0.97,
            bottom=0.17,
            top=0.92,
            wspace=0.18,
            hspace=0.28,
        )
        self.ax_3d = self.figure.add_subplot(grid[:, 0], projection="3d")
        self.ax_horizon = self.figure.add_subplot(grid[0, 1])
        self.ax_compass = self.figure.add_subplot(grid[1, 1], projection="polar")

        self._create_3d_model()
        self._create_text()
        self._create_controls(initial_port)
        self.figure.canvas.mpl_connect("close_event", self._on_close)

        if demo:
            self.connect_button.label.set_text("DEMO")
            self.port_box.set_val("DEMO")
        elif initial_port:
            self._connect_to(initial_port)

        self.animation = FuncAnimation(
            self.figure,
            self._update,
            interval=33,
            cache_frame_data=False,
        )

    def _create_3d_model(self) -> None:
        ax = self.ax_3d
        ax.set_facecolor(self.PANEL)
        ax.set_title("三维 IMU 姿态", color=self.TEXT, fontsize=18, pad=18)
        ax.set_xlim(-2.8, 2.8)
        ax.set_ylim(-2.8, 2.8)
        ax.set_zlim(-2.4, 2.4)
        ax.set_box_aspect((1, 1, 0.85))
        ax.view_init(elev=25, azim=-45)
        ax.set_xlabel("世界 X", color=self.MUTED)
        ax.set_ylabel("世界 Y", color=self.MUTED)
        ax.set_zlabel("世界 Z", color=self.MUTED)
        ax.tick_params(colors=self.MUTED)
        ax.grid(True, alpha=0.25)
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            try:
                axis.pane.set_facecolor(self.PANEL)
                axis.pane.set_edgecolor("#29465b")
            except Exception:
                pass

        self.base_vertices = np.array(
            [
                [-1.6, -0.95, -0.28], [1.6, -0.95, -0.28],
                [1.6, 0.95, -0.28], [-1.6, 0.95, -0.28],
                [-1.6, -0.95, 0.28], [1.6, -0.95, 0.28],
                [1.6, 0.95, 0.28], [-1.6, 0.95, 0.28],
            ],
            dtype=float,
        )
        self.face_indices = (
            (0, 1, 2, 3), (4, 5, 6, 7),
            (0, 1, 5, 4), (1, 2, 6, 5),
            (2, 3, 7, 6), (3, 0, 4, 7),
        )
        face_colors = ("#142736", "#35a6cc", "#1f5067", "#287c9b", "#1d4558", "#24627a")
        initial_faces = [self.base_vertices[list(face)] for face in self.face_indices]
        self.body = Poly3DCollection(
            initial_faces,
            facecolors=face_colors,
            edgecolors="#b8ecff",
            linewidths=1.5,
            alpha=0.96,
        )
        ax.add_collection3d(self.body)

        self.axis_lines = []
        for color in (self.RED, self.GREEN, self.CYAN):
            line, = ax.plot([0, 0], [0, 0], [0, 0], color=color, linewidth=4)
            self.axis_lines.append(line)
        self.front_line, = ax.plot([0, 1.25], [0, 0], [0.31, 0.31], color=self.YELLOW, linewidth=5)

        ax.text(2.35, 0, 0, "X / 前", color=self.RED, fontsize=11)
        ax.text(0, 1.9, 0, "Y", color=self.GREEN, fontsize=11)
        ax.text(0, 0, 1.6, "Z / 上", color=self.CYAN, fontsize=11)

    def _create_text(self) -> None:
        self.figure.suptitle("JY901P 实时姿态仪表", color=self.TEXT, fontsize=22, fontweight="bold")
        self.roll_text = self.figure.text(0.055, 0.115, "ROLL       --.--°", color=self.RED, fontsize=17, family="monospace", weight="bold")
        self.pitch_text = self.figure.text(0.285, 0.115, "PITCH      --.--°", color=self.GREEN, fontsize=17, family="monospace", weight="bold")
        self.yaw_text = self.figure.text(0.535, 0.115, "YAW        --.--°", color=self.CYAN, fontsize=17, family="monospace", weight="bold")
        self.device_text = self.figure.text(0.80, 0.12, "设备 --", color=self.TEXT, fontsize=12)
        self.cal_text = self.figure.text(0.80, 0.095, "磁校准 --", color=self.YELLOW, fontsize=10)
        self.status_text = self.figure.text(0.055, 0.025, self.status_message, color=self.MUTED, fontsize=10)

    def _default_port(self) -> str:
        ports = list(list_ports.comports())
        for item in ports:
            if item.device.upper() == DEFAULT_PORT:
                return item.device
        return DEFAULT_PORT

    def _create_controls(self, initial_port: str | None) -> None:
        port_axis = self.figure.add_axes((0.055, 0.06, 0.14, 0.038), facecolor="#dfe8ee")
        self.port_box = TextBox(port_axis, "COM口 ", initial=initial_port or self._default_port())
        self.port_box.label.set_color(self.TEXT)

        connect_axis = self.figure.add_axes((0.21, 0.06, 0.085, 0.038))
        refresh_axis = self.figure.add_axes((0.305, 0.06, 0.075, 0.038))
        fullscreen_axis = self.figure.add_axes((0.39, 0.06, 0.09, 0.038))
        mag_start_axis = self.figure.add_axes((0.50, 0.06, 0.105, 0.038))
        mag_stop_axis = self.figure.add_axes((0.615, 0.06, 0.105, 0.038))
        self.connect_button = Button(connect_axis, "连接", color="#dce8ef", hovercolor="#b7d8e8")
        self.refresh_button = Button(refresh_axis, "刷新串口", color="#dce8ef", hovercolor="#b7d8e8")
        self.fullscreen_button = Button(fullscreen_axis, "全屏", color="#dce8ef", hovercolor="#b7d8e8")
        self.mag_start_button = Button(mag_start_axis, "开始磁校准 m", color="#d8f3e5", hovercolor="#9de0bd")
        self.mag_stop_button = Button(mag_stop_axis, "结束磁校准 e", color="#f4dddd", hovercolor="#eab0b0")
        self.connect_button.on_clicked(self._toggle_connection)
        self.refresh_button.on_clicked(self._refresh_port)
        self.fullscreen_button.on_clicked(self._toggle_fullscreen)
        self.mag_start_button.on_clicked(lambda _event: self._send_command("m"))
        self.mag_stop_button.on_clicked(lambda _event: self._send_command("e"))

    def _toggle_connection(self, _event: object) -> None:
        if self.demo:
            return
        if self.reader.connected:
            self.reader.close()
            self.connect_button.label.set_text("连接")
            self.status_message = "串口已断开"
            return
        self._connect_to(self.port_box.text.strip())

    def _connect_to(self, port_name: str) -> None:
        if not port_name:
            self.status_message = "请输入COM口，例如 COM5"
            return
        try:
            self.reader.connect(port_name, self.baud)
        except Exception as exc:
            self.status_message = f"连接失败：{exc}；请关闭Arduino串口监视器"
            return
        self.connect_button.label.set_text("断开")
        self.status_message = f"已连接 {port_name}，等待 LIVE 数据"

    def _refresh_port(self, _event: object) -> None:
        ports = list(list_ports.comports())
        if ports:
            self.port_box.set_val(ports[0].device)
            names = ", ".join(item.device for item in ports)
            self.status_message = f"发现串口：{names}"
        else:
            self.status_message = "没有发现串口"

    def _toggle_fullscreen(self, _event: object) -> None:
        try:
            self.figure.canvas.manager.full_screen_toggle()
        except Exception as exc:
            self.status_message = f"当前Matplotlib后端不支持全屏：{exc}"

    def _send_command(self, command: str) -> None:
        if self.demo:
            self.status_message = "DEMO模式不发送串口命令"
            return
        try:
            self.reader.send(command)
            if command == "m":
                self.status_message = (
                    "已发送 m：Arduino将调用维特官方 WitStartMagCali()"
                )
            elif command == "e":
                self.status_message = (
                    "已发送 e：Arduino将调用维特官方 WitStopMagCali()"
                )
        except Exception as exc:
            self.status_message = f"命令发送失败：{exc}"

    def _demo_sample(self) -> LiveSample:
        elapsed = time.monotonic()
        return LiveSample(
            device_ms=int(elapsed * 1000),
            address="DEMO",
            roll=35.0 * math.sin(elapsed * 0.7),
            pitch=22.0 * math.sin(elapsed * 0.43),
            yaw=((elapsed * 18.0 + 180.0) % 360.0) - 180.0,
            gyro=(0.0, 0.0, 18.0),
        )

    def _update(self, _frame: int) -> tuple[object, ...]:
        latest: LiveSample | None = self._demo_sample() if self.demo else None
        while not self.demo:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event_type == "sample":
                latest = payload  # type: ignore[assignment]
            elif event_type == "log":
                self.status_message = str(payload)
            elif event_type == "error":
                self.status_message = f"串口读取失败：{payload}"
                self.reader.close()
                self.connect_button.label.set_text("连接")

        if latest is not None:
            self.sample = latest
            self.last_sample_wall_time = time.monotonic()

        sample = self.sample
        if sample is None:
            self.status_text.set_text(self.status_message)
            return (self.status_text,)

        self._update_3d(sample)
        self._draw_horizon(sample)
        self._draw_compass(sample)
        self.roll_text.set_text(f"ROLL    {sample.roll:8.2f}°")
        self.pitch_text.set_text(f"PITCH   {sample.pitch:8.2f}°")
        self.yaw_text.set_text(f"YAW     {sample.yaw:8.2f}°")
        self.device_text.set_text(f"设备 {sample.address}   t={sample.device_ms / 1000:.1f}s")
        self.cal_text.set_text(
            f"磁校准模式 {sample.cal_mode}   "
            f"进度 {sample.cal_progress}   质量 {sample.cal_quality}"
        )

        stale = time.monotonic() - self.last_sample_wall_time
        if not self.demo and stale > 1.0:
            self.status_text.set_color(self.RED)
            self.status_text.set_text(f"数据已中断 {stale:.1f} 秒")
        else:
            self.status_text.set_color(self.MUTED)
            gx, gy, gz = sample.gyro
            self.status_text.set_text(
                f"{self.status_message}    Gyro [{gx:.2f}, {gy:.2f}, {gz:.2f}] dps"
            )
        return (self.body, *self.axis_lines, self.front_line, self.status_text)

    def _update_3d(self, sample: LiveSample) -> None:
        rotation = rotation_zyx(sample.roll, sample.pitch, sample.yaw)
        vertices = self.base_vertices @ rotation.T
        self.body.set_verts([vertices[list(face)] for face in self.face_indices])

        endpoints = (
            rotation @ np.array([2.3, 0.0, 0.0]),
            rotation @ np.array([0.0, 1.9, 0.0]),
            rotation @ np.array([0.0, 0.0, 1.6]),
        )
        for line, endpoint in zip(self.axis_lines, endpoints):
            line.set_data_3d([0, endpoint[0]], [0, endpoint[1]], [0, endpoint[2]])

        front_start = rotation @ np.array([0.0, 0.0, 0.31])
        front_end = rotation @ np.array([1.3, 0.0, 0.31])
        self.front_line.set_data_3d(
            [front_start[0], front_end[0]],
            [front_start[1], front_end[1]],
            [front_start[2], front_end[2]],
        )

    def _draw_horizon(self, sample: LiveSample) -> None:
        ax = self.ax_horizon
        ax.clear()
        ax.set_facecolor(self.PANEL)
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-1.15, 1.15)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title("人工地平仪", color=self.TEXT, fontsize=15, pad=8)

        clip_circle = Circle((0, 0), 1.0, transform=ax.transData)
        sky = Circle((0, 0), 1.0, facecolor="#174f78", edgecolor="#8cb9d1", linewidth=2)
        ax.add_patch(sky)

        pitch_shift = max(-1.25, min(1.25, -sample.pitch / 45.0))
        transform = Affine2D().rotate_deg(-sample.roll).translate(0, pitch_shift) + ax.transData
        ground = Rectangle((-2.5, -2.5), 5.0, 2.5, facecolor="#6b4b32", transform=transform)
        ground.set_clip_path(clip_circle)
        ax.add_patch(ground)

        horizon, = ax.plot([-2.5, 2.5], [0, 0], color=self.YELLOW, linewidth=2.5, transform=transform)
        horizon.set_clip_path(clip_circle)
        for degree in (-30, -20, -10, 10, 20, 30):
            y = degree / 45.0
            length = 0.35 if degree % 20 else 0.5
            line, = ax.plot([-length, length], [y, y], color="#d5e4ec", linewidth=1, transform=transform)
            line.set_clip_path(clip_circle)

        ax.plot([-0.62, -0.15], [0, 0], color=self.YELLOW, linewidth=4)
        ax.plot([0.15, 0.62], [0, 0], color=self.YELLOW, linewidth=4)
        ax.plot([-0.15, 0, 0.15], [0, -0.10, 0], color=self.YELLOW, linewidth=3)
        ax.text(0, -1.12, f"Roll {sample.roll:.2f}°    Pitch {sample.pitch:.2f}°", ha="center", color=self.TEXT, fontsize=11)

    def _draw_compass(self, sample: LiveSample) -> None:
        ax = self.ax_compass
        ax.clear()
        ax.set_facecolor(self.PANEL)
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_ylim(0, 1.05)
        ax.set_yticks([])
        ax.set_xticks(np.radians([0, 45, 90, 135, 180, 225, 270, 315]))
        ax.set_xticklabels(["N", "45", "E", "135", "S", "225", "W", "315"])
        ax.tick_params(colors=self.TEXT, labelsize=10)
        ax.grid(color="#6f8798", alpha=0.35)
        ax.spines["polar"].set_color("#8cb9d1")
        ax.set_title("航向罗盘", color=self.TEXT, fontsize=15, pad=16)

        theta = math.radians(sample.yaw % 360.0)
        ax.plot([theta, theta], [0, 0.88], color=self.RED, linewidth=4)
        ax.scatter([theta], [0.88], s=85, color=self.YELLOW, zorder=5)
        ax.text(0.5, 0.46, f"{sample.yaw:.2f}°", transform=ax.transAxes, ha="center", va="center", color=self.CYAN, fontsize=21, fontweight="bold")

    def _on_close(self, _event: object) -> None:
        self.reader.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JY901P Matplotlib attitude viewer")
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help=f"Arduino serial port (default: {DEFAULT_PORT})",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--demo", action="store_true", help="animate without hardware")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    AttitudeViewer(args.port, args.baud, args.demo)
    plt.show()


if __name__ == "__main__":
    main()

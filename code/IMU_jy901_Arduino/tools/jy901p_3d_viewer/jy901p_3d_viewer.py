#!/usr/bin/env python3
"""Real-time 3D pose and magnetometer viewer for JY901P Arduino output."""

from __future__ import annotations

import math
import queue
import re
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Iterable

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - user environment check
    raise SystemExit(
        "Missing pyserial. Run: python -m pip install pyserial"
    ) from exc


LIVE_PATTERN = re.compile(
    r"^LIVE,ms=(?P<ms>\d+),address=(?P<address>0x[0-9A-Fa-f]+),"
    r"rpy_deg=(?P<roll>-?[0-9.]+),(?P<pitch>-?[0-9.]+),(?P<yaw>-?[0-9.]+),"
    r"gyro_dps=(?P<gx>-?[0-9.]+),(?P<gy>-?[0-9.]+),(?P<gz>-?[0-9.]+),"
    r"mag_raw=(?P<mx>-?\d+),(?P<my>-?\d+),(?P<mz>-?\d+)"
    r"(?P<tail>.*)$"
)


@dataclass(slots=True)
class LiveSample:
    device_ms: int
    address: str
    roll: float
    pitch: float
    yaw: float
    gyro: tuple[float, float, float]
    mag: tuple[int, int, int]
    cal_mode: str = "-"
    cal_progress: str = "-"
    cal_quality: str = "-"


def tail_value(tail: str, name: str, default: str = "-") -> str:
    match = re.search(rf"(?:^|,){re.escape(name)}=([^,]+)", tail)
    return match.group(1) if match else default


def parse_live_line(line: str) -> LiveSample | None:
    match = LIVE_PATTERN.match(line.strip())
    if not match:
        return None
    data = match.groupdict()
    tail = data["tail"]
    return LiveSample(
        device_ms=int(data["ms"]),
        address=data["address"],
        roll=float(data["roll"]),
        pitch=float(data["pitch"]),
        yaw=float(data["yaw"]),
        gyro=(float(data["gx"]), float(data["gy"]), float(data["gz"])),
        mag=(int(data["mx"]), int(data["my"]), int(data["mz"])),
        cal_mode=tail_value(tail, "cal_mode"),
        cal_progress=tail_value(tail, "cal_progress"),
        cal_quality=tail_value(tail, "cal_quality"),
    )


class SerialWorker:
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
        except Exception as exc:  # serial cable removal, reset, etc.
            if not self.stop_event.is_set():
                self.events.put(("error", str(exc)))

    def send(self, command: str) -> None:
        if not self.connected:
            raise RuntimeError("Serial port is not connected")
        assert self.port is not None
        self.port.write(command.encode("ascii"))
        self.port.flush()

    def close(self) -> None:
        self.stop_event.set()
        port = self.port
        self.port = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass


def matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3)]
        for row in range(3)
    ]


def matvec(matrix: list[list[float]], point: Iterable[float]) -> tuple[float, float, float]:
    values = tuple(point)
    return tuple(
        sum(matrix[row][col] * values[col] for col in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def rotation_zyx(roll_deg: float, pitch_deg: float, yaw_deg: float) -> list[list[float]]:
    roll, pitch, yaw = map(math.radians, (roll_deg, pitch_deg, yaw_deg))
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = [[1, 0, 0], [0, cr, -sr], [0, sr, cr]]
    ry = [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]
    rz = [[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]
    return matmul(rz, matmul(ry, rx))


def camera_rotation() -> list[list[float]]:
    # Fixed isometric camera: rotate world for a readable 3D projection.
    return rotation_zyx(25.0, -20.0, -35.0)


class ViewerApp:
    BG = "#10151d"
    PANEL = "#18212c"
    TEXT = "#e6edf3"
    MUTED = "#92a4b7"
    ACCENT = "#4fc3f7"
    GREEN = "#68d391"
    RED = "#ff6b6b"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("JY901P 三维姿态与磁力计实时显示")
        self.root.geometry("1380x820")
        self.root.minsize(1050, 680)
        self.root.configure(bg=self.BG)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.serial_worker = SerialWorker(self.events)
        self.sample: LiveSample | None = None
        self.last_sample_wall_time = 0.0
        self.mag_points: deque[tuple[int, int, int]] = deque(maxlen=4000)
        self.record_magnetometer = tk.BooleanVar(value=True)
        self.port_map: dict[str, str] = {}
        self.camera = camera_rotation()

        self.connection_text = tk.StringVar(value="未连接")
        self.pose_text = tk.StringVar(value="等待 LIVE 数据")
        self.cal_text = tk.StringVar(value="校准：-")
        self.mag_text = tk.StringVar(value="磁场点云：0 点")
        self.log_text = tk.StringVar(value="请连接串口")

        self._build_ui()
        self.refresh_ports()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(20, self.update_ui)

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=self.BG)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("TCheckbutton", background=self.BG, foreground=self.TEXT)
        style.configure("TButton", padding=6)

        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill=tk.X)
        ttk.Label(top, text="串口").pack(side=tk.LEFT)
        self.port_combo = ttk.Combobox(top, width=38, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=(7, 5))
        ttk.Button(top, text="刷新", command=self.refresh_ports).pack(side=tk.LEFT, padx=3)
        self.connect_button = ttk.Button(top, text="连接", command=self.toggle_connection)
        self.connect_button.pack(side=tk.LEFT, padx=3)
        ttk.Label(top, text="115200 baud").pack(side=tk.LEFT, padx=12)
        ttk.Label(top, textvariable=self.connection_text).pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(self.root, bg=self.BG, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        controls = ttk.Frame(self.root, padding=(12, 5))
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="开始磁校准  m", command=lambda: self.send_command("m")).pack(side=tk.LEFT, padx=3)
        ttk.Button(controls, text="结束磁校准  e", command=lambda: self.send_command("e")).pack(side=tk.LEFT, padx=3)
        ttk.Button(controls, text="重新设置100Hz  r", command=lambda: self.send_command("r")).pack(side=tk.LEFT, padx=3)
        ttk.Button(controls, text="清空磁场轨迹", command=self.clear_mag).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(
            controls,
            text="记录磁场轨迹",
            variable=self.record_magnetometer,
        ).pack(side=tk.LEFT, padx=12)

        info = ttk.Frame(self.root, padding=(12, 5))
        info.pack(fill=tk.X)
        ttk.Label(info, textvariable=self.pose_text).pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.cal_text).pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.mag_text).pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.log_text, foreground=self.MUTED).pack(anchor=tk.W)

        self.root.bind("<Key-m>", lambda _event: self.send_command("m"))
        self.root.bind("<Key-e>", lambda _event: self.send_command("e"))
        self.root.bind("<Key-r>", lambda _event: self.send_command("r"))

    def refresh_ports(self) -> None:
        ports = list(list_ports.comports())
        self.port_map = {
            f"{item.device} — {item.description}": item.device for item in ports
        }
        labels = list(self.port_map)
        self.port_combo["values"] = labels
        if labels and not self.port_combo.get():
            self.port_combo.current(0)
        if not labels:
            self.port_combo.set("")
            self.connection_text.set("没有发现串口")

    def toggle_connection(self) -> None:
        if self.serial_worker.connected:
            self.serial_worker.close()
            self.connect_button.configure(text="连接")
            self.connection_text.set("已断开")
            return

        label = self.port_combo.get()
        port_name = self.port_map.get(label)
        if not port_name:
            messagebox.showwarning("未选择串口", "请先选择 Arduino 对应的 COM 口。")
            return
        try:
            self.serial_worker.connect(port_name, 115200)
        except Exception as exc:
            messagebox.showerror(
                "串口连接失败",
                f"无法打开 {port_name}:\n{exc}\n\n请关闭 Arduino 串口监视器后重试。",
            )
            return
        self.connect_button.configure(text="断开")
        self.connection_text.set(f"已连接 {port_name}")
        self.log_text.set("等待 Arduino 的 LIVE 数据……")

    def send_command(self, command: str) -> None:
        try:
            if command == "m":
                self.clear_mag()
                self.record_magnetometer.set(True)
            self.serial_worker.send(command)
            self.log_text.set(f"已发送命令：{command}")
        except Exception as exc:
            messagebox.showwarning("发送失败", str(exc))

    def clear_mag(self) -> None:
        self.mag_points.clear()
        self.mag_text.set("磁场点云：0 点")

    def update_ui(self) -> None:
        latest_sample: LiveSample | None = None
        while True:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event_type == "sample":
                latest_sample = payload  # type: ignore[assignment]
            elif event_type == "log":
                self.log_text.set(str(payload))
            elif event_type == "error":
                self.connection_text.set("串口读取失败")
                self.log_text.set(str(payload))
                self.serial_worker.close()
                self.connect_button.configure(text="连接")

        if latest_sample is not None:
            self.sample = latest_sample
            self.last_sample_wall_time = time.monotonic()
            if self.record_magnetometer.get():
                self.mag_points.append(latest_sample.mag)
            self._update_labels(latest_sample)

        self.draw_scene()
        self.root.after(33, self.update_ui)

    def _update_labels(self, sample: LiveSample) -> None:
        gx, gy, gz = sample.gyro
        self.pose_text.set(
            f"{sample.address}   Roll {sample.roll:8.2f}°   "
            f"Pitch {sample.pitch:8.2f}°   Yaw {sample.yaw:8.2f}°   "
            f"Gyro [{gx:.2f}, {gy:.2f}, {gz:.2f}] dps"
        )
        self.cal_text.set(
            f"校准模式 {sample.cal_mode}   进度 {sample.cal_progress}   "
            f"质量 {sample.cal_quality}"
        )
        self.mag_text.set(self.magnetic_summary())

    def project(
        self,
        point: tuple[float, float, float],
        center_x: float,
        center_y: float,
        scale: float,
    ) -> tuple[float, float, float]:
        x, y, z = matvec(self.camera, point)
        return center_x + x * scale, center_y - z * scale, y

    def draw_scene(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        split = width / 2
        self.canvas.create_rectangle(8, 8, split - 5, height - 8, fill=self.PANEL, outline="#2b3948")
        self.canvas.create_rectangle(split + 5, 8, width - 8, height - 8, fill=self.PANEL, outline="#2b3948")
        self.canvas.create_text(28, 28, text="三维姿态", fill=self.TEXT, anchor=tk.W, font=("Microsoft YaHei UI", 15, "bold"))
        self.canvas.create_text(split + 25, 28, text="磁力计三维覆盖", fill=self.TEXT, anchor=tk.W, font=("Microsoft YaHei UI", 15, "bold"))

        sample = self.sample
        if sample is None:
            self.canvas.create_text(width / 2, height / 2, text="连接串口后等待 LIVE 数据", fill=self.MUTED, font=("Microsoft YaHei UI", 16))
            return

        stale_seconds = time.monotonic() - self.last_sample_wall_time
        if stale_seconds > 1.0:
            self.canvas.create_text(width / 2, 55, text=f"数据已停止 {stale_seconds:.1f} s", fill=self.RED, font=("Microsoft YaHei UI", 12, "bold"))

        self.draw_pose(sample, split / 2, height / 2 + 20, min(split, height) * 0.18)
        self.draw_magnetometer(split + split / 2, height / 2 + 20, min(split, height) * 0.35)

    def draw_pose(self, sample: LiveSample, cx: float, cy: float, scale: float) -> None:
        vertices = [
            (-1.5, -0.9, -0.25), (1.5, -0.9, -0.25),
            (1.5, 0.9, -0.25), (-1.5, 0.9, -0.25),
            (-1.5, -0.9, 0.25), (1.5, -0.9, 0.25),
            (1.5, 0.9, 0.25), (-1.5, 0.9, 0.25),
        ]
        faces = [
            ((0, 1, 2, 3), "#263746"), ((4, 5, 6, 7), "#4096b7"),
            ((0, 1, 5, 4), "#31566b"), ((1, 2, 6, 5), "#2d718c"),
            ((2, 3, 7, 6), "#294c60"), ((3, 0, 4, 7), "#376f86"),
        ]
        sensor_rotation = rotation_zyx(sample.roll, sample.pitch, sample.yaw)
        world_vertices = [matvec(sensor_rotation, vertex) for vertex in vertices]
        projected = [self.project(vertex, cx, cy, scale) for vertex in world_vertices]
        sorted_faces = sorted(
            faces,
            key=lambda face: sum(projected[index][2] for index in face[0]) / 4,
        )
        for indices, color in sorted_faces:
            coords: list[float] = []
            for index in indices:
                coords.extend(projected[index][:2])
            self.canvas.create_polygon(coords, fill=color, outline="#a7d8eb", width=2)

        # Body axes: X red (front), Y green, Z blue.
        axis_colors = (self.RED, self.GREEN, self.ACCENT)
        axis_names = ("X/前", "Y", "Z/上")
        origin = self.project((0.0, 0.0, 0.0), cx, cy, scale)
        body_axes = ((2.1, 0.0, 0.0), (0.0, 1.7, 0.0), (0.0, 0.0, 1.4))
        for body_axis, color, name in zip(body_axes, axis_colors, axis_names):
            endpoint_world = matvec(sensor_rotation, body_axis)
            endpoint = self.project(endpoint_world, cx, cy, scale)
            self.canvas.create_line(origin[0], origin[1], endpoint[0], endpoint[1], fill=color, width=4, arrow=tk.LAST)
            self.canvas.create_text(endpoint[0], endpoint[1], text=name, fill=color, anchor=tk.SW, font=("Consolas", 11, "bold"))

        self.canvas.create_text(
            cx,
            cy + scale * 2.2,
            text=f"R {sample.roll:.2f}°    P {sample.pitch:.2f}°    Y {sample.yaw:.2f}°",
            fill=self.TEXT,
            font=("Consolas", 13, "bold"),
        )

    def magnetic_bounds(self) -> tuple[tuple[float, float, float], float, tuple[float, float, float]]:
        if not self.mag_points:
            return (0.0, 0.0, 0.0), 1.0, (0.0, 0.0, 0.0)
        mins = [min(point[i] for point in self.mag_points) for i in range(3)]
        maxs = [max(point[i] for point in self.mag_points) for i in range(3)]
        center = tuple((mins[i] + maxs[i]) / 2.0 for i in range(3))
        spans = tuple(maxs[i] - mins[i] for i in range(3))
        radius = max(max(spans) / 2.0, 1.0)
        return center, radius, spans

    def draw_magnetometer(self, cx: float, cy: float, scale: float) -> None:
        center, radius, _spans = self.magnetic_bounds()
        # Reference axes and unit circle projections provide depth cues.
        for axis, color, name in (
            ((1.0, 0.0, 0.0), self.RED, "Mx"),
            ((0.0, 1.0, 0.0), self.GREEN, "My"),
            ((0.0, 0.0, 1.0), self.ACCENT, "Mz"),
        ):
            start = self.project((0.0, 0.0, 0.0), cx, cy, scale)
            end = self.project(axis, cx, cy, scale)
            self.canvas.create_line(start[0], start[1], end[0], end[1], fill=color, width=2, arrow=tk.LAST)
            self.canvas.create_text(end[0], end[1], text=name, fill=color, anchor=tk.SW)

        points = list(self.mag_points)
        step = max(1, len(points) // 1000)
        for index, point in enumerate(points[::step]):
            normalized = tuple((point[i] - center[i]) / radius for i in range(3))
            x, y, depth = self.project(normalized, cx, cy, scale)
            brightness = max(100, min(230, int(165 + depth * 45)))
            color = f"#{40:02x}{brightness:02x}{220:02x}"
            size = 2 if index < max(0, len(points[::step]) - 1) else 5
            self.canvas.create_oval(x - size, y - size, x + size, y + size, fill=color, outline="")

        self.canvas.create_text(
            cx,
            cy + scale * 1.35,
            text=self.magnetic_summary(),
            fill=self.TEXT,
            font=("Consolas", 11),
            justify=tk.CENTER,
        )

    def magnetic_summary(self) -> str:
        center, _radius, spans = self.magnetic_bounds()
        max_span = max(spans)
        balance = min(spans) / max_span * 100.0 if max_span > 0 else 0.0
        return (
            f"磁场点云：{len(self.mag_points)} 点   "
            f"span [{spans[0]:.0f}, {spans[1]:.0f}, {spans[2]:.0f}]   "
            f"balance {balance:.1f}%\n"
            f"center [{center[0]:.0f}, {center[1]:.0f}, {center[2]:.0f}]"
        )

    def on_close(self) -> None:
        self.serial_worker.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

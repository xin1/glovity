from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import serial
from serial.tools import list_ports

APP_NAME = "六轴手套离线配对烧录工具"
APP_VERSION = "2.2.0"
NVS_ADDRESS = "0x9000"
NVS_SIZE = "0x5000"


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


class FlasherError(RuntimeError):
    pass


class SixAxisFlasher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry("880x620")
        self.minsize(800, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.devices: dict[str, str] = {}
        self.left_port = tk.StringVar()
        self.receiver_port = tk.StringVar()
        self.left_mac = tk.StringVar(value="未读取")
        self.receiver_mac = tk.StringVar(value="未读取")
        self.status = tk.StringVar(value="无需联网。请连接左手和接收器。")
        self._build_ui()
        self.after(100, self._drain_messages)
        self.after(300, self.refresh_ports)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=APP_NAME, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(root, text="内置预编译固件和烧录器：自动读取 MAC、清除旧配对、烧录并写入新配对，无需 Arduino 或网络。").pack(anchor="w", pady=(2, 12))
        resources = ttk.LabelFrame(root, text="1. 离线资源", padding=10)
        resources.pack(fill="x")
        ttk.Button(resources, text="检查内置资源", command=self.check_resources).pack(side="left")
        ttk.Label(resources, text="只包含烧录必需文件，不包含 Arduino 编译器。", foreground="#166534").pack(side="left", padx=12)
        devices = ttk.LabelFrame(root, text="2. 设备与角色", padding=10)
        devices.pack(fill="x", pady=10)
        ttk.Button(devices, text="扫描并读取 MAC", command=self.scan_devices).grid(row=0, column=0, padx=(0, 10), pady=4, sticky="w")
        ttk.Button(devices, text="刷新串口", command=self.refresh_ports).grid(row=0, column=1, pady=4, sticky="w")
        ttk.Label(devices, text="左手串口").grid(row=1, column=0, sticky="e", padx=5, pady=4)
        self.left_combo = ttk.Combobox(devices, textvariable=self.left_port, state="readonly", width=46)
        self.left_combo.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(devices, textvariable=self.left_mac, width=22).grid(row=1, column=2, sticky="w", padx=10)
        ttk.Label(devices, text="接收器串口").grid(row=2, column=0, sticky="e", padx=5, pady=4)
        self.receiver_combo = ttk.Combobox(devices, textvariable=self.receiver_port, state="readonly", width=46)
        self.receiver_combo.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(devices, textvariable=self.receiver_mac, width=22).grid(row=2, column=2, sticky="w", padx=10)
        ttk.Label(devices, text="两块硬件相同时程序无法判断物理角色，请确认串口对应关系。烧录会覆盖旧固件和旧配对。", foreground="#8a5200").grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))
        devices.columnconfigure(1, weight=1)
        self.left_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_macs())
        self.receiver_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_macs())
        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 10))
        self.flash_button = ttk.Button(actions, text="开始离线配对并烧录", command=self.flash_pair)
        self.flash_button.pack(side="left")
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=12)
        log_frame = ttk.LabelFrame(root, text="运行日志", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=18, wrap="word", state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno("任务正在运行", "烧录仍在运行，确定关闭吗？"):
            return
        self.destroy()

    def emit(self, text: str) -> None:
        self.messages.put(("log", text))

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _worker(self, target) -> None:
        if self.running:
            return
        self.running = True
        self.flash_button.configure(state="disabled")
        self.status.set("正在运行……")
        def run() -> None:
            try:
                target()
            except Exception as exc:
                self.messages.put(("error", str(exc)))
            finally:
                self.messages.put(("done", None))
        threading.Thread(target=run, daemon=True).start()

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log": self._append_log(str(payload))
                elif kind == "devices": self._apply_devices(payload)  # type: ignore[arg-type]
                elif kind == "success":
                    self._append_log(str(payload)); self.status.set("离线配对烧录完成。"); messagebox.showinfo("完成", str(payload))
                elif kind == "error":
                    self._append_log(f"错误：{payload}"); self.status.set("操作失败，请查看日志。"); messagebox.showerror("操作失败", str(payload))
                elif kind == "done":
                    self.running = False; self.flash_button.configure(state="normal")
                    if self.status.get() == "正在运行……": self.status.set("操作已完成。")
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    @staticmethod
    def _assets() -> tuple[Path, Path, Path]:
        base = resource_path("offline")
        return base / "esptool.exe", base / "GloveLeft.merged.bin", base / "Receiver.merged.bin"

    def check_resources(self) -> None:
        try:
            assets = self._assets(); missing = [p.name for p in assets if not p.is_file()]
            if missing: raise FlasherError("缺少内置文件：" + ", ".join(missing))
            size = sum(p.stat().st_size for p in assets) / 1024 / 1024
            self._append_log(f"离线资源完整，共 {size:.1f} MiB。"); self.status.set("离线资源完整，可以扫描设备。")
        except Exception as exc:
            messagebox.showerror("资源检查失败", str(exc))

    def refresh_ports(self) -> None:
        labels = [f"{p.device} — {p.description}" for p in list_ports.comports()]
        self.left_combo["values"] = labels; self.receiver_combo["values"] = labels
        if labels and not self.left_port.get(): self.left_port.set(labels[0])
        if len(labels) > 1 and not self.receiver_port.get(): self.receiver_port.set(labels[1])

    @staticmethod
    def _port_name(label: str) -> str:
        match = re.match(r"(COM\d+)", label, re.IGNORECASE)
        if not match: raise FlasherError(f"无效串口：{label or '未选择'}")
        return match.group(1).upper()

    def scan_devices(self) -> None:
        self.refresh_ports(); self._worker(self._scan_devices)

    def _scan_devices(self) -> None:
        esptool, _, _ = self._assets()
        if not esptool.exists(): raise FlasherError("EXE 内缺少 esptool.exe。")
        ports = list(list_ports.comports())
        if not ports: raise FlasherError("没有发现串口。请连接两块板，并关闭串口监视器。")
        found: dict[str, str] = {}
        for port in ports:
            self.emit(f"检测 {port.device}（{port.description}）……")
            output = self._esptool(["--port", port.device, "read_mac"], check=False)
            match = re.search(r"MAC:\s*([0-9A-Fa-f:]{17})", output)
            if match:
                label = f"{port.device} — {port.description}"; found[label] = match.group(1).upper()
                self.emit(f"发现 ESP32-S3：{port.device}  MAC {found[label]}")
            else: self.emit(f"跳过 {port.device}：无法读取 ESP MAC。")
        if len(found) < 2: raise FlasherError("识别到不足两块 ESP。检查数据线；必要时按住 BOOT 后重新扫描。")
        self.messages.put(("devices", found))

    def _apply_devices(self, devices: dict[str, str]) -> None:
        self.devices = devices; labels = list(devices)
        self.left_combo["values"] = labels; self.receiver_combo["values"] = labels
        self.left_port.set(labels[0]); self.receiver_port.set(labels[1]); self._sync_macs()
        self.status.set("已读取 MAC，请确认两块板的角色。")

    def _sync_macs(self) -> None:
        self.left_mac.set(self.devices.get(self.left_port.get(), "未读取")); self.receiver_mac.set(self.devices.get(self.receiver_port.get(), "未读取"))

    def flash_pair(self) -> None:
        left_label, receiver_label = self.left_port.get(), self.receiver_port.get()
        left_mac, receiver_mac = self.devices.get(left_label), self.devices.get(receiver_label)
        if left_label == receiver_label: messagebox.showerror("无法开始", "左手和接收器不能选择同一个串口。"); return
        if not left_mac or not receiver_mac: messagebox.showerror("无法开始", "请先扫描并读取两块板的 MAC。"); return
        prompt = f"左手：{left_label}\nMAC：{left_mac}\n\n接收器：{receiver_label}\nMAC：{receiver_mac}\n\n将清除旧配对并覆盖固件，确定继续吗？"
        if not messagebox.askyesno("确认覆盖并烧录", prompt): return
        params = (self._port_name(left_label), left_mac, self._port_name(receiver_label), receiver_mac)
        self._worker(lambda: self._flash_pair(*params))

    def _flash_pair(self, left_port: str, left_mac: str, receiver_port: str, receiver_mac: str) -> None:
        _, glove_image, receiver_image = self._assets()
        if not glove_image.exists() or not receiver_image.exists(): raise FlasherError("EXE 内缺少预编译固件。")
        self.emit(f"配对关系：左手 {left_mac}  ↔  接收器 {receiver_mac}")
        self._flash_one("左手", left_port, glove_image); self._provision(left_port, 921600, receiver_mac, left_mac, "左手")
        self._flash_one("接收器", receiver_port, receiver_image); self._provision(receiver_port, 2000000, left_mac, receiver_mac, "接收器")
        self.messages.put(("success", "左手和接收器均已离线烧录并保存配对。重新上电后，接收器 LED 常亮表示收到左手数据。"))

    def _flash_one(self, role: str, port: str, image: Path) -> None:
        # Boards wired through USB-UART usually need default_reset. Native USB
        # ESP32-S3 boards may instead require usb_reset. Try both automatically.
        reset_modes = ("default_reset", "usb_reset")
        selected_mode = ""
        self.emit(f"{role}：进入下载模式并清除旧配对区……")
        for mode in reset_modes:
            try:
                self.emit(f"{role}：尝试 {mode} 自动复位（115200 baud）……")
                self._esptool([
                    "--port", port, "--baud", "115200", "--before", mode,
                    "--connect-attempts", "15", "erase_region", NVS_ADDRESS, NVS_SIZE,
                ], timeout=75)
                selected_mode = mode
                break
            except FlasherError:
                self.emit(f"{role}：{mode} 未能进入下载模式，继续尝试其他复位方式。")
        if not selected_mode:
            raise FlasherError(
                f"{role}（{port}）无法进入下载模式。请关闭 Arduino 串口监视器和其他占用串口的软件；"
                "按住板子的 BOOT 键，再点击烧录，日志出现 Connecting 后松开 BOOT。"
                "如果没有 BOOT 键，可按住 BOOT、短按 RESET，再松开 BOOT。"
            )

        self.emit(f"{role}：烧录预编译固件……")
        attempts = (
            (selected_mode, "921600"),
            (selected_mode, "460800"),
            (selected_mode, "115200"),
            ("usb_reset" if selected_mode == "default_reset" else "default_reset", "115200"),
        )
        for mode, baud in attempts:
            try:
                self.emit(f"{role}：使用 {mode} / {baud} baud 烧录……")
                self._esptool([
                    "--port", port, "--baud", baud, "--before", mode,
                    "--connect-attempts", "15", "write_flash", "0x0", str(image),
                ], timeout=240)
                return
            except FlasherError:
                self.emit(f"{role}：本次烧录尝试失败，自动降速重试。")
        raise FlasherError(
            f"{role}（{port}）已清除旧配对，但固件写入失败。请按住 BOOT 键重新执行；"
            "同时确认 USB 线支持数据传输且供电稳定。"
        )

    @staticmethod
    def _resolve_port(preferred: str, own_mac: str) -> str:
        wanted = own_mac.replace(":", "").upper()
        for item in list_ports.comports():
            serial_number = (item.serial_number or "").replace(":", "").upper()
            if serial_number == wanted:
                return item.device
        return preferred

    def _provision(self, port: str, baud: int, peer_mac: str, own_mac: str, role: str) -> None:
        self.emit(f"{role}：等待 USB CDC 重新出现并写入对方 MAC {peer_mac}……")
        deadline = time.monotonic() + 40; last_error = ""
        while time.monotonic() < deadline:
            current_port = self._resolve_port(port, own_mac)
            try:
                connection = serial.Serial()
                connection.port = current_port
                connection.baudrate = baud
                connection.timeout = 0.2
                connection.write_timeout = 3
                connection.rtscts = False
                connection.dsrdtr = False
                connection.dtr = False
                connection.rts = False
                connection.open()
                with connection:
                    time.sleep(0.8); connection.reset_input_buffer(); command = f"PAIR={peer_mac}\n".encode("ascii")
                    for _ in range(12):
                        connection.write(command); connection.flush(); until = time.monotonic() + 0.6; received = bytearray()
                        while time.monotonic() < until:
                            received.extend(connection.read(connection.in_waiting or 1))
                            if b"PAIR_OK" in received: self.emit(f"{role}：配对参数保存成功。"); return
                        time.sleep(0.15)
            except (serial.SerialException, OSError) as exc:
                last_error = str(exc); time.sleep(0.5)
        raise FlasherError(f"{role}固件已烧录，但写入配对参数失败。{last_error}")

    def _esptool(self, arguments: list[str], check: bool = True, timeout: int = 60) -> str:
        esptool, _, _ = self._assets()
        return self._run([str(esptool), "--chip", "esp32s3", *arguments], check=check, timeout=timeout)

    def _run(self, command: list[str], check: bool = True, timeout: int = 60) -> str:
        self.emit("> " + subprocess.list2cmdline(command)); creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=creationflags)
        except subprocess.TimeoutExpired as exc:
            raise FlasherError(f"命令运行超时：{command[0]}") from exc
        self.emit(result.stdout.rstrip())
        if check and result.returncode: raise FlasherError(f"烧录命令失败（退出码 {result.returncode}），请查看日志。")
        return result.stdout


if __name__ == "__main__":
    SixAxisFlasher().mainloop()

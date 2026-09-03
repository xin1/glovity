"""Modern desktop test panel for an Arduino-controlled vibration motor."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

try:
    import customtkinter as ctk
    import serial
    from serial.tools import list_ports
except ModuleNotFoundError as error:
    raise SystemExit(
        "缺少界面或串口依赖。请先在 motor 文件夹运行：\n"
        "py -m pip install -r requirements.txt"
    ) from error


BAUD_RATE = 115200
KEEPALIVE_INTERVAL_MS = 1000
PORT_SCAN_INTERVAL_MS = 1500

BACKGROUND = "#F3F6FB"
CARD = "#FFFFFF"
TEXT = "#152238"
MUTED = "#667085"
PRIMARY = "#4F6BFF"
PRIMARY_HOVER = "#3E58E8"
PRIMARY_SOFT = "#EEF1FF"
SUCCESS = "#12A66A"
SUCCESS_SOFT = "#EAF8F2"
DANGER = "#E5484D"
DANGER_SOFT = "#FFF0F0"
BORDER = "#E3E8F2"


class MotorTestUI:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title("震动马达测试台")
        self.root.geometry("780x610")
        self.root.minsize(700, 570)
        self.root.configure(fg_color=BACKGROUND)

        self.serial_port: serial.Serial | None = None
        self.serial_lock = threading.Lock()
        self.reader_thread: threading.Thread | None = None
        self.reader_stop = threading.Event()
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.running = False
        self.last_ports: list[str] = []
        self.slider_job: str | None = None

        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="未连接")
        self.strength_var = tk.DoubleVar(value=0)
        self.percent_var = tk.StringVar(value="0%")
        self.pwm_var = tk.StringVar(value="PWM 0 / 255")
        self.output_var = tk.StringVar(value="输出已停止")

        self._build_ui()
        self._scan_ports()
        self._process_events()
        self._keepalive()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=34, pady=28)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", pady=(0, 20))

        logo = ctk.CTkLabel(
            header,
            text="〽",
            width=54,
            height=54,
            corner_radius=17,
            fg_color=PRIMARY,
            text_color="white",
            font=("Microsoft YaHei UI", 23, "bold"),
        )
        logo.pack(side="left", padx=(0, 14))

        heading = ctk.CTkFrame(header, fg_color="transparent")
        heading.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            heading,
            text="震动马达测试台",
            text_color=TEXT,
            font=("Microsoft YaHei UI", 24, "bold"),
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            heading,
            text="Arduino UNO  ·  PWM 强度控制  ·  失联自动停机",
            text_color=MUTED,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        connection = ctk.CTkFrame(
            outer,
            height=78,
            corner_radius=20,
            fg_color=CARD,
            border_width=1,
            border_color=BORDER,
        )
        connection.pack(fill="x")
        connection.pack_propagate(False)

        ctk.CTkLabel(
            connection,
            text="串口设备",
            text_color=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=(22, 14))

        self.port_box = ctk.CTkOptionMenu(
            connection,
            variable=self.port_var,
            values=["未检测到串口"],
            width=205,
            height=40,
            corner_radius=12,
            fg_color="#F6F8FC",
            button_color="#E8ECF5",
            button_hover_color="#DDE3EF",
            text_color=TEXT,
            dropdown_fg_color=CARD,
            dropdown_hover_color=PRIMARY_SOFT,
            dropdown_text_color=TEXT,
            font=("Microsoft YaHei UI", 11),
        )
        self.port_box.pack(side="left")

        self.connect_button = ctk.CTkButton(
            connection,
            text="连接设备",
            width=104,
            height=40,
            corner_radius=12,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            font=("Microsoft YaHei UI", 11, "bold"),
            command=self._toggle_connection,
        )
        self.connect_button.pack(side="left", padx=12)

        self.status_badge = ctk.CTkLabel(
            connection,
            textvariable=self.status_var,
            width=92,
            height=30,
            corner_radius=15,
            fg_color="#F2F4F7",
            text_color=MUTED,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.status_badge.pack(side="right", padx=22)

        control = ctk.CTkFrame(
            outer,
            corner_radius=24,
            fg_color=CARD,
            border_width=1,
            border_color=BORDER,
        )
        control.pack(fill="both", expand=True, pady=(16, 14))

        control_header = ctk.CTkFrame(control, fg_color="transparent")
        control_header.pack(fill="x", padx=25, pady=(22, 4))
        ctk.CTkLabel(
            control_header,
            text="震动强度",
            text_color=TEXT,
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left")
        self.output_badge = ctk.CTkLabel(
            control_header,
            textvariable=self.output_var,
            height=29,
            corner_radius=14,
            fg_color="#F2F4F7",
            text_color=MUTED,
            font=("Microsoft YaHei UI", 10),
        )
        self.output_badge.pack(side="right")

        meter = ctk.CTkFrame(
            control,
            width=158,
            height=158,
            corner_radius=79,
            fg_color=PRIMARY_SOFT,
            border_width=8,
            border_color="#F7F8FF",
        )
        meter.pack(pady=(4, 14))
        meter.pack_propagate(False)
        ctk.CTkLabel(
            meter,
            textvariable=self.percent_var,
            text_color=PRIMARY,
            font=("Microsoft YaHei UI", 38, "bold"),
        ).pack(expand=True, pady=(22, 0))
        ctk.CTkLabel(
            meter,
            textvariable=self.pwm_var,
            text_color=MUTED,
            font=("Consolas", 10),
        ).pack(pady=(0, 23))

        slider_area = ctk.CTkFrame(control, fg_color="transparent")
        slider_area.pack(fill="x", padx=42)
        self.slider = ctk.CTkSlider(
            slider_area,
            from_=0,
            to=100,
            variable=self.strength_var,
            command=self._on_slider,
            height=22,
            corner_radius=11,
            button_length=24,
            button_corner_radius=12,
            fg_color="#E7EBF3",
            progress_color=PRIMARY,
            button_color="white",
            button_hover_color="#F8F9FF",
            border_width=3,
            border_color=PRIMARY,
        )
        self.slider.pack(fill="x")

        scale_labels = ctk.CTkFrame(slider_area, fg_color="transparent")
        scale_labels.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(
            scale_labels,
            text="0%  关闭",
            text_color=MUTED,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="left")
        ctk.CTkLabel(
            scale_labels,
            text="100%  最大",
            text_color=MUTED,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="right")

        button_row = ctk.CTkFrame(control, fg_color="transparent")
        button_row.pack(pady=(16, 20))
        self.start_button = ctk.CTkButton(
            button_row,
            text="▶  开始输出",
            width=156,
            height=46,
            corner_radius=15,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            font=("Microsoft YaHei UI", 12, "bold"),
            command=self._start,
            state="disabled",
        )
        self.start_button.pack(side="left", padx=6)
        self.stop_button = ctk.CTkButton(
            button_row,
            text="■  立即停止",
            width=156,
            height=46,
            corner_radius=15,
            fg_color=DANGER_SOFT,
            hover_color="#FFE3E3",
            text_color=DANGER,
            font=("Microsoft YaHei UI", 12, "bold"),
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=6)

        tip = ctk.CTkFrame(outer, height=46, corner_radius=15, fg_color="#EAF1FF")
        tip.pack(fill="x")
        tip.pack_propagate(False)
        ctk.CTkLabel(
            tip,
            text="ⓘ  建议从 20%–30% 开始测试；低占空比下马达不转属于正常现象。",
            text_color="#3659B8",
            font=("Microsoft YaHei UI", 10),
        ).pack(side="left", padx=17, expand=True)

    def _set_connection_status(self, text: str, state: str = "idle") -> None:
        self.status_var.set(text)
        colors = {
            "idle": ("#F2F4F7", MUTED),
            "connected": (SUCCESS_SOFT, SUCCESS),
            "error": (DANGER_SOFT, DANGER),
        }
        background, foreground = colors[state]
        self.status_badge.configure(fg_color=background, text_color=foreground)

    def _set_output_status(self, text: str, active: bool = False) -> None:
        self.output_var.set(text)
        if active:
            self.output_badge.configure(fg_color=SUCCESS_SOFT, text_color=SUCCESS)
        else:
            self.output_badge.configure(fg_color="#F2F4F7", text_color=MUTED)

    def _available_ports(self) -> list[tuple[str, str]]:
        ports = sorted(list_ports.comports(), key=lambda item: item.device)
        return [(port.device, f"{port.device} — {port.description}") for port in ports]

    def _scan_ports(self) -> None:
        if self.serial_port is None:
            port_items = self._available_ports()
            devices = [device for device, _ in port_items]
            if devices != self.last_ports:
                selected = self.port_var.get()
                self.port_box.configure(values=devices or ["未检测到串口"])
                if selected in devices:
                    self.port_var.set(selected)
                elif devices:
                    descriptions = {device: label.lower() for device, label in port_items}
                    preferred = next(
                        (
                            device
                            for device in devices
                            if "arduino" in descriptions[device]
                            or "ch340" in descriptions[device]
                        ),
                        devices[0],
                    )
                    self.port_var.set(preferred)
                else:
                    self.port_var.set("未检测到串口")
                self.last_ports = devices
        self.root.after(PORT_SCAN_INTERVAL_MS, self._scan_ports)

    def _toggle_connection(self) -> None:
        if self.serial_port is not None:
            self._disconnect("已断开")
            return

        port_name = self.port_var.get()
        if not port_name or port_name == "未检测到串口":
            messagebox.showwarning(
                "未找到串口",
                "请连接 Arduino，或在设备管理器中确认串口驱动已安装。",
            )
            return

        port: serial.Serial | None = None
        try:
            self._set_connection_status("连接中…")
            self.root.update_idletasks()
            port = serial.Serial(
                port_name,
                BAUD_RATE,
                timeout=0.2,
                write_timeout=0.5,
            )
            time.sleep(1.8)  # UNO normally resets when its serial port opens.
            port.reset_input_buffer()
            self.serial_port = port
            self.reader_stop.clear()
            self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self.reader_thread.start()
            self._write("STOP")
        except (serial.SerialException, OSError) as error:
            if port is not None and port.is_open:
                port.close()
            self._set_connection_status("连接失败", "error")
            messagebox.showerror("连接失败", f"无法打开 {port_name}：\n{error}")
            return

        self._set_connection_status(f"已连接 {port_name}", "connected")
        self.connect_button.configure(
            text="断开连接",
            fg_color="#EFF2F8",
            hover_color="#E4E8F1",
            text_color=TEXT,
        )
        self.port_box.configure(state="disabled")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="normal")

    def _reader_loop(self) -> None:
        while not self.reader_stop.is_set():
            try:
                with self.serial_lock:
                    port = self.serial_port
                    line = port.readline() if port is not None else b""
                if line:
                    self.events.put(
                        ("line", line.decode("utf-8", errors="replace").strip())
                    )
            except (serial.SerialException, OSError) as error:
                self.events.put(("error", str(error)))
                return

    def _process_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "error":
                    self._disconnect("连接丢失", error=True)
                    messagebox.showerror("串口连接丢失", value)
                elif value == "TIMEOUT:STOPPED":
                    self.running = False
                    self._set_output_status("Arduino 超时，输出已停止")
                elif value.startswith("ERR:"):
                    self._set_connection_status(f"设备返回 {value}", "error")
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _write(self, command: str) -> bool:
        try:
            with self.serial_lock:
                if self.serial_port is None:
                    return False
                self.serial_port.write((command + "\n").encode("ascii"))
            return True
        except (serial.SerialException, OSError) as error:
            self.events.put(("error", str(error)))
            return False

    @staticmethod
    def _percent_to_pwm(percent: float) -> int:
        return max(0, min(255, round(percent * 255 / 100)))

    def _on_slider(self, _value: float) -> None:
        percent = round(self.strength_var.get())
        pwm = self._percent_to_pwm(percent)
        self.percent_var.set(f"{percent}%")
        self.pwm_var.set(f"PWM {pwm} / 255")
        if self.running:
            if self.slider_job is not None:
                self.root.after_cancel(self.slider_job)
            if pwm > 0:
                self._set_output_status("正在输出", active=True)
            else:
                self._set_output_status("输出为 0（已停止震动）")
            self.slider_job = self.root.after(40, self._send_slider_value)

    def _send_slider_value(self) -> None:
        self.slider_job = None
        pwm = self._percent_to_pwm(self.strength_var.get())
        self._write(f"SET:{pwm}")

    def _start(self) -> None:
        pwm = self._percent_to_pwm(self.strength_var.get())
        if pwm == 0:
            messagebox.showinfo("强度为 0", "请先把强度滑块向右拖动。")
            return
        if self._write(f"SET:{pwm}"):
            self.running = True
            self._set_output_status("正在输出", active=True)

    def _stop(self) -> None:
        self.running = False
        self._write("STOP")
        self._set_output_status("输出已停止")

    def _keepalive(self) -> None:
        if self.serial_port is not None:
            if self.running:
                pwm = self._percent_to_pwm(self.strength_var.get())
                self._write(f"SET:{pwm}")
            else:
                self._write("PING")
        self.root.after(KEEPALIVE_INTERVAL_MS, self._keepalive)

    def _disconnect(self, status: str, error: bool = False) -> None:
        self.running = False
        self.reader_stop.set()
        with self.serial_lock:
            if self.serial_port is not None:
                try:
                    self.serial_port.write(b"STOP\n")
                    self.serial_port.close()
                except (serial.SerialException, OSError):
                    pass
                self.serial_port = None
        self._set_connection_status(status, "error" if error else "idle")
        self._set_output_status("输出已停止")
        self.connect_button.configure(
            text="连接设备",
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            text_color="white",
        )
        self.port_box.configure(state="normal")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")

    def _on_close(self) -> None:
        self._disconnect("已关闭")
        self.root.destroy()


def main() -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    MotorTestUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

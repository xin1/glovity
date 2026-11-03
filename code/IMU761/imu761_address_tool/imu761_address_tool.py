from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import messagebox, ttk

import serial
from serial.tools import list_ports

from protocol import (
    ConfigResponse,
    Hi91Frame,
    StreamParser,
    build_query_command,
    build_set_address_command,
    format_hex,
)


APP_TITLE = "IMU761 地址配置工具"
APP_VERSION = "2.0.0"
BAUD_RATE = 115200

COLORS = {
    "bg": "#08111F",
    "card": "#101C2D",
    "card_alt": "#14243A",
    "border": "#203551",
    "text": "#EDF4FF",
    "muted": "#8FA5BF",
    "primary": "#3B82F6",
    "primary_active": "#2563EB",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "entry": "#0B1728",
}


class AddressToolApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE}  v{APP_VERSION}")
        self.root.geometry("960x790")
        self.root.minsize(900, 760)
        self.root.configure(bg=COLORS["bg"])

        self.serial_port: serial.Serial | None = None
        self.serial_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.rx_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.parser = StreamParser()

        self.current_address: int | None = None
        self.pending_target: int | None = None
        self.pending_deadline = 0.0
        self.pending_source: int | None = None
        self.verify_sent = False
        self.next_verify_at = 0.0
        self.query_address: int | None = None
        self.query_deadline = 0.0
        self.scan_addresses: deque[int] = deque()
        self.scanning = False
        self.rx_bytes = 0
        self.frame_times: deque[float] = deque()
        self.frame_count = 0
        self.last_crc_count = 0

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=str(BAUD_RATE))
        self.query_var = tk.StringVar(value="0x01")
        self.connection_var = tk.StringVar(value="未连接")
        self.address_var = tk.StringVar(value="--")
        self.address_hint_var = tk.StringVar(value="连接后主动查询指定地址")
        self.new_address_var = tk.StringVar(value="0x05")
        self.temperature_var = tk.StringVar(value="-- ℃")
        self.status_var = tk.StringVar(value="----")
        self.rate_var = tk.StringVar(value="-- Hz")
        self.timestamp_var = tk.StringVar(value="-- ms")
        self.frames_var = tk.StringVar(value="0")

        self._configure_styles()
        self._build_ui()
        self.refresh_ports(initial=True)
        self.root.after(30, self._process_rx_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dark.TCombobox",
            fieldbackground=COLORS["entry"],
            background=COLORS["card_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=8,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", COLORS["entry"])],
            foreground=[("readonly", COLORS["text"])],
            selectbackground=[("readonly", COLORS["entry"])],
            selectforeground=[("readonly", COLORS["text"])],
        )

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=COLORS["bg"])
        outer.pack(fill="both", expand=True, padx=28, pady=22)

        header = tk.Frame(outer, bg=COLORS["bg"])
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header,
            text="IMU761 地址配置工具",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 21, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="新版查询固件：主动查询地址 → 修改地址 → 查询新地址验证",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        connection_card = self._card(outer)
        connection_card.pack(fill="x", pady=(0, 14))
        self._section_title(connection_card, "串口连接", "8N1 · 默认 115200 · 二进制 68 协议")

        connection_row = tk.Frame(connection_card, bg=COLORS["card"])
        connection_row.pack(fill="x", padx=20, pady=(4, 18))
        self.port_combo = ttk.Combobox(
            connection_row,
            textvariable=self.port_var,
            state="readonly",
            style="Dark.TCombobox",
            font=("Microsoft YaHei UI", 10),
            width=22,
        )
        self.port_combo.pack(side="left", fill="x", expand=True)
        self.baud_combo = ttk.Combobox(
            connection_row, textvariable=self.baud_var,
            values=("115200", "9600", "230400", "256000", "460800", "921600"),
            state="readonly", style="Dark.TCombobox", width=9,
        )
        self.baud_combo.pack(side="left", padx=(10, 0))

        self.refresh_button = self._button(
            connection_row, "刷新串口", self.refresh_ports, COLORS["card_alt"]
        )
        self.refresh_button.pack(side="left", padx=(10, 0))
        self.connect_button = self._button(
            connection_row, "连接", self.toggle_connection, COLORS["primary"]
        )
        self.connect_button.pack(side="left", padx=(10, 0))

        self.connection_badge = tk.Label(
            connection_card,
            textvariable=self.connection_var,
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=5,
        )
        self.connection_badge.place(relx=1.0, x=-20, y=18, anchor="ne")

        query_row = tk.Frame(connection_card, bg=COLORS["card"])
        query_row.pack(fill="x", padx=20, pady=(0, 16))
        tk.Label(query_row, text="查询地址", bg=COLORS["card"],
                 fg=COLORS["text"]).pack(side="left")
        self.query_combo = ttk.Combobox(
            query_row, textvariable=self.query_var, width=8,
            values=[f"0x{i:02X}" for i in range(0xFF)], style="Dark.TCombobox",
        )
        self.query_combo.pack(side="left", padx=8)
        self.scan_button = self._button(query_row, "扫描未知地址", self.start_scan, COLORS["card_alt"])
        self.scan_button.pack(side="left", padx=8)
        self.scan_button.configure(state="disabled")
        self.stop_scan_button = self._button(query_row, "停止扫描", self.stop_scan, COLORS["card_alt"])
        self.stop_scan_button.pack(side="left")
        self.stop_scan_button.configure(state="disabled")
        tk.Label(query_row, text="地址可填 0x01；连接后自动查询一次", bg=COLORS["card"],
                 fg=COLORS["muted"]).pack(side="left", padx=12)

        address_card = self._card(outer)
        address_card.pack(fill="x", pady=(0, 14))
        self._section_title(address_card, "设备地址", "地址范围 0x00–0xFE")

        address_body = tk.Frame(address_card, bg=COLORS["card"])
        address_body.pack(fill="x", padx=20, pady=(0, 20))
        address_body.grid_columnconfigure(0, weight=1)
        address_body.grid_columnconfigure(1, weight=1)

        current_box = tk.Frame(
            address_body,
            bg=COLORS["card_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        current_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tk.Label(
            current_box,
            text="当前地址",
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", padx=18, pady=(14, 0))
        self.address_label = tk.Label(
            current_box,
            textvariable=self.address_var,
            bg=COLORS["card_alt"],
            fg=COLORS["success"],
            font=("Consolas", 30, "bold"),
        )
        self.address_label.pack(anchor="w", padx=18, pady=(2, 0))
        tk.Label(
            current_box,
            textvariable=self.address_hint_var,
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=18, pady=(0, 14))

        modify_box = tk.Frame(
            address_body,
            bg=COLORS["card_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        modify_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        tk.Label(
            modify_box,
            text="修改为",
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", padx=18, pady=(14, 8))

        modify_row = tk.Frame(modify_box, bg=COLORS["card_alt"])
        modify_row.pack(fill="x", padx=18)
        values = [f"0x{value:02X}" for value in range(0xFF)]
        self.new_address_combo = ttk.Combobox(
            modify_row,
            textvariable=self.new_address_var,
            values=values,
            style="Dark.TCombobox",
            font=("Consolas", 12, "bold"),
            width=10,
        )
        self.new_address_combo.pack(side="left", fill="x", expand=True)
        self.modify_button = self._button(
            modify_row, "修改地址", self.modify_address, COLORS["primary"]
        )
        self.modify_button.pack(side="left", padx=(10, 0))
        self.modify_button.configure(state="disabled")

        self.read_button = self._button(
            modify_box, "查询并读取地址", self.request_current_address, COLORS["card_alt"]
        )
        self.read_button.pack(anchor="w", padx=18, pady=(11, 14))
        self.read_button.configure(state="disabled")

        metrics_card = self._card(outer)
        metrics_card.pack(fill="x", pady=(0, 14))
        metrics = tk.Frame(metrics_card, bg=COLORS["card"])
        metrics.pack(fill="x", padx=10, pady=12)
        for column, (name, variable) in enumerate(
            (
                ("温度", self.temperature_var),
                ("状态字", self.status_var),
                ("近1秒收帧数", self.rate_var),
                ("系统时间", self.timestamp_var),
                ("有效帧", self.frames_var),
            )
        ):
            metrics.grid_columnconfigure(column, weight=1)
            box = tk.Frame(metrics, bg=COLORS["card"])
            box.grid(row=0, column=column, sticky="nsew", padx=8)
            tk.Label(
                box,
                text=name,
                bg=COLORS["card"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 9),
            ).pack()
            tk.Label(
                box,
                textvariable=variable,
                bg=COLORS["card"],
                fg=COLORS["text"],
                font=("Consolas", 11, "bold"),
            ).pack(pady=(4, 0))

        log_card = self._card(outer)
        log_card.pack(fill="both", expand=True)
        log_header = tk.Frame(log_card, bg=COLORS["card"])
        log_header.pack(fill="x", padx=20, pady=(14, 7))
        tk.Label(
            log_header,
            text="通信日志",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        clear_button = self._button(log_header, "清空", self.clear_log, COLORS["card_alt"])
        clear_button.pack(side="right")

        log_frame = tk.Frame(log_card, bg=COLORS["card"])
        log_frame.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.log_text = tk.Text(
            log_frame,
            height=8,
            bg=COLORS["entry"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["primary"],
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
            padx=12,
            pady=10,
            wrap="word",
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        tk.Label(
            outer,
            text="提示：一次只连接一个 IMU。修改后请断电重启，确认地址是否掉电保存。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(10, 0))

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )

    def _section_title(self, parent: tk.Widget, title: str, subtitle: str) -> None:
        row = tk.Frame(parent, bg=COLORS["card"])
        row.pack(fill="x", padx=20, pady=(15, 12))
        tk.Label(
            row,
            text=title,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            row,
            text=subtitle,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(10, 0))

    def _button(
        self, parent: tk.Widget, text: str, command: object, background: str
    ) -> tk.Button:
        active = COLORS["primary_active"] if background == COLORS["primary"] else COLORS["border"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=COLORS["text"],
            activebackground=active,
            activeforeground=COLORS["text"],
            disabledforeground="#53657A",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=15,
            pady=8,
        )

    def refresh_ports(self, initial: bool = False) -> None:
        previous = self.port_var.get()
        ports = sorted(list_ports.comports(), key=lambda item: item.device)
        devices = [item.device for item in ports]
        self.port_combo["values"] = devices
        if previous in devices:
            self.port_var.set(previous)
        elif devices:
            self.port_var.set(devices[0])
        else:
            self.port_var.set("")
        if not initial:
            self._log(f"发现 {len(devices)} 个串口：{', '.join(devices) if devices else '无'}")

    def toggle_connection(self) -> None:
        if self.serial_port and self.serial_port.is_open:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        port_name = self.port_var.get().strip()
        if not port_name:
            messagebox.showwarning(APP_TITLE, "请先选择串口。")
            return
        try:
            port = serial.Serial(
                port=port_name,
                baudrate=int(self.baud_var.get()),
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=1,
            )
            port.reset_input_buffer()
            port.reset_output_buffer()
        except (serial.SerialException, OSError) as exc:
            messagebox.showerror(APP_TITLE, f"无法打开 {port_name}：\n{exc}")
            return

        self.serial_port = port
        self._drain_rx_queue()
        self.stop_event.clear()
        self.parser = StreamParser()
        self.current_address = None
        self.pending_target = None
        self.pending_source = None
        self.query_address = None
        self.scan_addresses.clear()
        self.scanning = False
        self.rx_bytes = 0
        self.frame_times.clear()
        self.frame_count = 0
        self._reset_data_display()

        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

        self.connection_var.set(f"已连接 {port_name}")
        self.connection_badge.configure(bg="#123823", fg=COLORS["success"])
        self.connect_button.configure(text="断开", bg=COLORS["danger"], activebackground="#DC2626")
        self.port_combo.configure(state="disabled")
        self.baud_combo.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.read_button.configure(state="normal")
        self.scan_button.configure(state="normal")
        self.address_hint_var.set("准备发送查询命令…")
        self._log(f"已连接 {port_name}，参数 {self.baud_var.get()} 8N1；请仅连接一只 IMU")
        self.request_current_address()

    def disconnect(self) -> None:
        self.stop_event.set()
        port = self.serial_port
        self.serial_port = None
        if port:
            try:
                port.close()
            except (serial.SerialException, OSError):
                pass
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
        self._drain_rx_queue()
        self.parser.clear()
        self.current_address = None
        self.query_address = None
        self.scanning = False
        self.scan_addresses.clear()

        self.connection_var.set("未连接")
        self.connection_badge.configure(bg=COLORS["card_alt"], fg=COLORS["muted"])
        self.connect_button.configure(
            text="连接", bg=COLORS["primary"], activebackground=COLORS["primary_active"]
        )
        self.port_combo.configure(state="readonly")
        self.baud_combo.configure(state="readonly")
        self.refresh_button.configure(state="normal")
        self.read_button.configure(state="disabled")
        self.modify_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.stop_scan_button.configure(state="disabled")
        self.address_hint_var.set("连接后主动查询指定地址")
        self.pending_target = None
        self._log("串口已断开")

    def _reader_loop(self) -> None:
        while not self.stop_event.is_set():
            port = self.serial_port
            if not port or not port.is_open:
                break
            try:
                with self.serial_lock:
                    waiting = port.in_waiting
                    data = port.read(waiting) if waiting else b""
                if data:
                    self.rx_queue.put(("data", data))
                else:
                    time.sleep(0.008)
            except (serial.SerialException, OSError) as exc:
                if not self.stop_event.is_set():
                    self.rx_queue.put(("error", str(exc)))
                break

    def _process_rx_queue(self) -> None:
        try:
            while True:
                event_type, payload = self.rx_queue.get_nowait()
                if event_type == "error":
                    self._log(f"串口错误：{payload}", level="error")
                    messagebox.showerror(APP_TITLE, f"串口通信失败：\n{payload}")
                    self.disconnect()
                    break
                if event_type == "data" and isinstance(payload, bytes):
                    self.rx_bytes += len(payload)
                    if self.query_address is not None or self.pending_target is not None:
                        self._log(f"RX：{format_hex(payload)}")
                    for event in self.parser.feed(payload):
                        if isinstance(event, Hi91Frame):
                            self._handle_hi91(event)
                        elif isinstance(event, ConfigResponse):
                            self._handle_config_response(event)
        except queue.Empty:
            pass

        self._advance_operations(time.monotonic())
        now = time.monotonic()
        while self.frame_times and self.frame_times[0] < now - 1:
            self.frame_times.popleft()
        self.rate_var.set(str(len(self.frame_times)))
        self.root.after(30, self._process_rx_queue)

    def _advance_operations(self, now: float) -> None:
        if self.pending_target is not None and now > self.pending_deadline:
            target = self.pending_target
            self.pending_target = None
            self.query_address = None
            self.current_address = None
            self.address_var.set("--")
            self.modify_button.configure(state="disabled")
            self.read_button.configure(state="normal")
            self.scan_button.configure(state="normal")
            self.address_hint_var.set("修改结果未确认，请查询新/旧地址")
            self._log(
                f"修改结果未确认：查询新地址 0x{target:02X} 未收到有效 HI91。"
                "可能已修改但未验证，请扫描或查询新/旧地址，不要盲目重复改址。",
                level="warning",
            )

        elif self.pending_target is not None:
            if now >= self.next_verify_at:
                if self._send_query(self.pending_target):
                    self.verify_sent = True
                    self.next_verify_at = now + 0.6
            return

        if self.query_address is not None and now >= self.query_deadline:
            expired = self.query_address
            self.query_address = None
            if not self.scanning:
                self.address_hint_var.set("查询超时；检查地址、波特率与回复格式")
                self._log(f"查询 0x{expired:02X} 超时；本次收到 {self.rx_bytes} 字节。"
                          "只接受 CRC 正确的 82 字节 HI91；其他回复请保留日志。", "warning")
        if self.scanning and self.query_address is None:
            if self.scan_addresses:
                self._send_query(self.scan_addresses.popleft())
            else:
                self.stop_scan()
                self.address_hint_var.set("扫描结束，未找到有效 HI91 回复")
                self._log("扫描结束，未找到地址。请检查波特率、接线及新固件回复格式。", "warning")

    @staticmethod
    def _parse_address(text: str) -> int:
        text = text.strip()
        value = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        if not 0 <= value <= 0xFE:
            raise ValueError("地址范围为 0x00–0xFE")
        return value

    def _send_query(self, address: int) -> bool:
        command = build_query_command(address)
        try:
            with self.serial_lock:
                if not self._is_connected():
                    raise serial.SerialException("串口已关闭")
                if self.serial_port.write(command) != len(command):
                    raise serial.SerialException("查询命令未完整发送")
        except (serial.SerialException, OSError) as exc:
            self._log(f"查询发送失败：{exc}", "error")
            self.disconnect()
            return False
        self.query_address = address
        self.query_deadline = time.monotonic() + (0.5 if self.scanning else 1.5)
        self._log(f"TX 查询 0x{address:02X}：{format_hex(command)}")
        return True

    def start_scan(self) -> None:
        if not self._is_connected() or self.pending_target is not None:
            return
        try:
            preferred = self._parse_address(self.query_var.get())
        except ValueError:
            preferred = 1
        self.stop_scan()
        self.current_address = None
        self.address_var.set("--")
        self.modify_button.configure(state="disabled")
        order = list(dict.fromkeys([preferred, 0, 1, 2, *range(0xFF)]))
        self.scan_addresses = deque(order)
        self.scanning = True
        self.rx_bytes = 0
        self.scan_button.configure(state="disabled")
        self.read_button.configure(state="disabled")
        self.stop_scan_button.configure(state="normal")
        self.address_hint_var.set("正在逐个查询 00–FE，最多约2分钟")
        self._log("开始只读扫描：不发送改址命令、不使用广播地址；一次只连接一只 IMU。")
        self._send_query(self.scan_addresses.popleft())

    def stop_scan(self) -> None:
        self.scanning = False
        self.scan_addresses.clear()
        self.query_address = None
        self.stop_scan_button.configure(state="disabled")
        if self._is_connected() and self.pending_target is None:
            self.scan_button.configure(state="normal")
            self.read_button.configure(state="normal")

    def _handle_hi91(self, frame: Hi91Frame) -> None:
        if self.pending_target is not None:
            if frame.address != self.pending_target or not self.verify_sent:
                self._log(f"收到地址 0x{frame.address:02X}；仍等待新地址查询验证")
                return
        elif self.query_address is not None and frame.address != self.query_address:
            self._log(f"回复地址 0x{frame.address:02X} 与查询地址 0x{self.query_address:02X} 不符，忽略。", "warning")
            return
        elif self.query_address is None:
            # Never infer identity from unsolicited / stale replies.
            return
        self.stop_scan()
        self.query_var.set(f"0x{frame.address:02X}")
        now = time.monotonic()
        self.frame_times.append(now)
        while self.frame_times and self.frame_times[0] < now - 1.0:
            self.frame_times.popleft()

        self.frame_count += 1
        self.frames_var.set(str(self.frame_count))
        self.rate_var.set(str(len(self.frame_times)))
        self.temperature_var.set(f"{frame.temperature_c} ℃")
        self.status_var.set(f"0x{frame.main_status:04X}")
        self.timestamp_var.set(f"{frame.timestamp_ms} ms")

        if self.current_address != frame.address:
            old_address = self.current_address
            self.current_address = frame.address
            self.address_var.set(f"0x{frame.address:02X}")
            self.address_hint_var.set("已从有效 HI91 帧读取")
            self.address_label.configure(fg=COLORS["success"])
            self.modify_button.configure(state="normal")
            if old_address is None:
                self._log(f"读取到当前地址：0x{frame.address:02X}", level="success")
            else:
                self._log(
                    f"检测到地址变化：0x{old_address:02X} → 0x{frame.address:02X}",
                    level="success",
                )

        if self.pending_target is not None and frame.address == self.pending_target:
            target = self.pending_target
            self.pending_target = None
            self.pending_source = None
            self.modify_button.configure(state="normal")
            self.read_button.configure(state="normal")
            self.scan_button.configure(state="normal")
            self.address_hint_var.set("修改成功，请断电重启验证")
            self._log(f"地址修改成功，HI91 已返回 0x{target:02X}", level="success")

    def _handle_config_response(self, response: ConfigResponse) -> None:
        checksum_note = "校验通过" if response.checksum_valid else "校验规则待确认"
        self._log(f"RX 配置应答：{format_hex(response.raw)}（{checksum_note}）")
        if (not response.checksum_valid or self.pending_target is None or
                response.address not in (self.pending_source, self.pending_target)):
            return
        if response.result == 0x00:
            self.address_hint_var.set("设备已应答，正在主动查询新地址验证…")
        else:
            self.pending_target = None
            self.pending_source = None
            self.query_address = None
            self.current_address = None
            self.address_var.set("--")
            self.modify_button.configure(state="disabled")
            self.read_button.configure(state="normal")
            self.scan_button.configure(state="normal")
            self.address_hint_var.set("设备返回修改失败")
            self._log("设备返回修改失败（结果 FF）", level="error")

    def request_current_address(self) -> None:
        if not self._is_connected() or self.pending_target is not None:
            return
        try:
            address = self._parse_address(self.query_var.get())
        except ValueError:
            messagebox.showwarning(APP_TITLE, "查询地址请填 0x00–0xFE，例如 0x01。")
            return
        self.stop_scan()
        self.current_address = None
        self.address_var.set("--")
        self.modify_button.configure(state="disabled")
        self.rx_bytes = 0
        self.address_hint_var.set(f"正在查询 0x{address:02X}…")
        self._send_query(address)

    def modify_address(self) -> None:
        if not self._is_connected():
            messagebox.showwarning(APP_TITLE, "请先连接串口。")
            return
        if self.current_address is None:
            messagebox.showwarning(APP_TITLE, "请先查询或扫描，确认当前设备地址。")
            return
        if self.pending_target is not None or self.scanning:
            return

        try:
            text = self.new_address_var.get().strip()
            new_address = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
            if not 0 <= new_address <= 0xFE:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_TITLE, "新地址必须在 0x00 到 0xFE 之间。")
            return

        if new_address == self.current_address:
            messagebox.showinfo(APP_TITLE, "新地址与当前地址相同，无需修改。")
            return

        old_address = self.current_address
        command = build_set_address_command(old_address, new_address)
        if not messagebox.askyesno(
            APP_TITLE,
            f"确认将地址从 0x{old_address:02X} 修改为 0x{new_address:02X}？\n\n"
            f"发送：{format_hex(command)}",
        ):
            return

        self._drain_rx_queue()
        self.parser.clear()
        port = self.serial_port
        try:
            with self.serial_lock:
                if not port or not port.is_open:
                    raise serial.SerialException("串口已经关闭")
                port.reset_input_buffer()
                self.pending_target = new_address
                self.pending_source = old_address
                self.verify_sent = False
                self.query_address = None
                self.pending_deadline = time.monotonic() + 5.0
                self.next_verify_at = time.monotonic() + 0.25
                if port.write(command) != len(command):
                    raise serial.SerialException("改址命令未完整发送")
                port.flush()
        except (serial.SerialException, OSError) as exc:
            self.pending_target = None
            self.current_address = None
            self.modify_button.configure(state="disabled")
            self.address_var.set("--")
            messagebox.showerror(APP_TITLE, f"发送失败：\n{exc}")
            return

        self.modify_button.configure(state="disabled")
        self.read_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.address_hint_var.set(f"正在修改为 0x{new_address:02X}…")
        self._log(f"TX 修改地址：{format_hex(command)}")

    def _is_connected(self) -> bool:
        return bool(self.serial_port and self.serial_port.is_open)

    def _drain_rx_queue(self) -> None:
        try:
            while True:
                self.rx_queue.get_nowait()
        except queue.Empty:
            pass

    def _reset_data_display(self) -> None:
        self.address_var.set("--")
        self.address_label.configure(fg=COLORS["success"])
        self.temperature_var.set("-- ℃")
        self.status_var.set("----")
        self.rate_var.set("--")
        self.timestamp_var.set("-- ms")
        self.frames_var.set("0")
        self.modify_button.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, message: str, level: str = "normal") -> None:
        color = {
            "normal": COLORS["text"],
            "success": COLORS["success"],
            "warning": COLORS["warning"],
            "error": COLORS["danger"],
        }.get(level, COLORS["text"])
        tag = f"level_{level}"
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.tag_configure(tag, foreground=color)
        self.log_text.insert("end", f"[{stamp}] {message}\n", tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > 1500:
            self.log_text.delete("1.0", "301.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self._is_connected():
            self.disconnect()
        self.root.destroy()


def enable_windows_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    enable_windows_dpi_awareness()
    root = tk.Tk()
    if "--self-test" in sys.argv:
        root.withdraw()
        app = AddressToolApp(root)
        root.update_idletasks()
        assert build_query_command(1).hex() == "6804010308"
        assert build_query_command(2).hex() == "6804020309"
        assert app.current_address is None
        root.destroy()
        return
    AddressToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

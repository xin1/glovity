from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import esptool
from serial.tools import list_ports

from pairing import make_paired_files, normalize_mac


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


class PairFlasher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Glovity 左右手配对烧录器")
        self.geometry("860x680")
        self.minsize(760, 620)
        self.ports: dict[str, ttk.Combobox] = {}
        self.macs: dict[str, tk.StringVar] = {}
        self.firmware: dict[str, tk.StringVar] = {}
        self._build_ui()
        self.refresh_ports()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Glovity 左右手 + 接收器配对烧录", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(root, text="依次连接三块 ESP32-S3；先读取 MAC，再一键生成配对固件并烧录。正常模式不采集磁力计，校准模式输出 11×9 轴原始值。", wraplength=740).pack(anchor="w", pady=(4, 12))

        grid = ttk.Frame(root)
        grid.pack(fill="x")
        ttk.Label(grid, text="设备").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="串口").grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="Wi-Fi MAC（ESP-NOW）").grid(row=0, column=2, sticky="w")
        labels = (("receiver", "接收器"), ("left", "左手"), ("right", "右手"))
        for row, (key, label) in enumerate(labels, 1):
            ttk.Label(grid, text=label, width=10).grid(row=row, column=0, sticky="w", pady=5)
            combo = ttk.Combobox(grid, width=18, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=5)
            self.ports[key] = combo
            var = tk.StringVar()
            self.macs[key] = var
            ttk.Entry(grid, width=24, textvariable=var).grid(row=row, column=2, sticky="ew", pady=5)
            ttk.Button(grid, text="读取 MAC", command=lambda k=key: self.start(lambda: self.read_mac(k))).grid(row=row, column=3, padx=(10, 0), pady=5)
        grid.columnconfigure(2, weight=1)

        firmware_box = ttk.LabelFrame(root, text="手动编译的 merged binary", padding=8)
        firmware_box.pack(fill="x", pady=(12, 0))
        for row, (key, label) in enumerate(labels):
            ttk.Label(firmware_box, text=f"{label}固件", width=10).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            self.firmware[key] = var
            ttk.Entry(firmware_box, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=3)
            ttk.Button(firmware_box, text="选择…", command=lambda k=key: self.choose_firmware(k)).grid(row=row, column=2, pady=3)
        firmware_box.columnconfigure(1, weight=1)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="刷新串口", command=self.refresh_ports).pack(side="left")
        self.flash_button = ttk.Button(actions, text="一键配对并烧录三台设备", command=lambda: self.start(self.flash_all))
        self.flash_button.pack(side="left", padx=10)
        ttk.Button(actions, text="清空日志", command=lambda: self.log.delete("1.0", "end")).pack(side="left")

        self.log = tk.Text(root, height=20, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.write_log("准备就绪。请确认三块板使用不同串口。\n")

    def refresh_ports(self) -> None:
        values = [p.device for p in list_ports.comports()]
        for combo in self.ports.values():
            old = combo.get()
            combo["values"] = values
            if old in values:
                combo.set(old)
        self.write_log(f"发现串口: {', '.join(values) if values else '无'}\n")

    def choose_firmware(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title="选择 merged binary",
            filetypes=(("ESP32 merged binary", "*.bin"), ("所有文件", "*.*")),
        )
        if path:
            self.firmware[key].set(path)

    def write_log(self, text: str) -> None:
        self.after(0, lambda: (self.log.insert("end", text), self.log.see("end")))

    def start(self, fn) -> None:
        self.flash_button.configure(state="disabled")
        def worker() -> None:
            try:
                fn()
            except Exception as exc:
                self.write_log(f"错误: {exc}\n")
                self.after(0, lambda: messagebox.showerror("操作失败", str(exc)))
            finally:
                self.after(0, lambda: self.flash_button.configure(state="normal"))
        threading.Thread(target=worker, daemon=True).start()

    def run_esptool(self, args: list[str]) -> str:
        output = io.StringIO()
        self.write_log("esptool " + " ".join(args) + "\n")
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                esptool.main(args)
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise RuntimeError(output.getvalue().strip() or f"esptool 退出码 {exc.code}") from exc
        text = output.getvalue()
        self.write_log(text)
        return text

    def require_port(self, key: str) -> str:
        port = self.ports[key].get().strip()
        if not port:
            raise ValueError(f"请选择{ {'receiver':'接收器','left':'左手','right':'右手'}[key] }串口")
        return port

    def read_mac(self, key: str) -> None:
        port = self.require_port(key)
        text = self.run_esptool(["--port", port, "read-mac"])
        matches = re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)
        if not matches:
            raise RuntimeError("未能从芯片读取 MAC")
        mac = normalize_mac(matches[-1])
        self.after(0, lambda: self.macs[key].set(mac))
        self.write_log(f"{key} MAC = {mac}\n")

    def flash_all(self) -> None:
        ports = {key: self.require_port(key) for key in self.ports}
        if len(set(ports.values())) != 3:
            raise ValueError("三台设备必须选择三个不同串口")
        macs = {key: normalize_mac(self.macs[key].get()) for key in self.macs}
        firmware_files = {key: Path(var.get().strip()) for key, var in self.firmware.items()}
        for key, path in firmware_files.items():
            if not path.is_file():
                raise ValueError(f"请选择有效的 {key} merged.bin 固件")
        with tempfile.TemporaryDirectory(prefix="glovity_pair_") as temp:
            images = make_paired_files(firmware_files, Path(temp), macs["receiver"], macs["left"], macs["right"])
            for key in ("receiver", "left", "right"):
                self.write_log(f"\n开始烧录 {key} ({ports[key]})...\n")
                self.run_esptool(["--chip", "esp32s3", "--port", ports[key], "--baud", "921600", "write-flash", "--erase-all", "0x0", os.fspath(images[key])])
        self.write_log("\n全部烧录并配对完成。\n")
        self.after(0, lambda: messagebox.showinfo("完成", "接收器、左手和右手已烧录并完成配对。"))


if __name__ == "__main__":
    PairFlasher().mainloop()

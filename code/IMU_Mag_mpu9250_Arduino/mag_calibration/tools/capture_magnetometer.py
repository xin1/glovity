"""Capture three-column magnetometer samples from an Arduino serial port."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports


DATA_DIR = Path(__file__).resolve().parents[1] / "data"

SAMPLE_PATTERN = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,"
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,"
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)


def available_ports() -> str:
    ports = list(list_ports.comports())
    if not ports:
        return "(未发现串口)"
    return "\n".join(f"  {item.device}: {item.description}" for item in ports)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Arduino 输出的 AK8963 三轴数据保存为无表头 CSV。"
    )
    parser.add_argument("port", help="串口名，例如 Windows 的 COM5 或 Linux 的 /dev/ttyUSB0")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DATA_DIR / "magnetometer_data.csv",
        help="输出文件（默认：mag_calibration/data/magnetometer_data.csv）",
    )
    parser.add_argument("-b", "--baud", type=int, default=115200, help="波特率")
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=0,
        help="采集秒数；0 表示一直采集到 Ctrl+C",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    count = 0
    started = time.monotonic()

    print("可用串口：")
    print(available_ports())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n正在采集 {args.port} -> {args.output.resolve()}")
    print("缓慢转动整套设备，使各个朝向都被覆盖；按 Ctrl+C 结束。")

    try:
        with serial.Serial(args.port, args.baud, timeout=1) as port, args.output.open(
            "w", newline="", encoding="utf-8"
        ) as output_file:
            writer = csv.writer(output_file, lineterminator="\n")
            time.sleep(2.0)  # Opening a port often resets an Arduino Uno/Nano.
            port.reset_input_buffer()

            while args.duration <= 0 or time.monotonic() - started < args.duration:
                line = port.readline().decode("ascii", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("#"):
                    print(line)
                    continue

                match = SAMPLE_PATTERN.match(line)
                if not match:
                    print(f"忽略无法解析的串口行：{line}", file=sys.stderr)
                    continue

                writer.writerow(match.groups())
                count += 1
                if count % 100 == 0:
                    output_file.flush()
                    print(f"已保存 {count} 点", end="\r", flush=True)
    except KeyboardInterrupt:
        pass
    except serial.SerialException as exc:
        print(f"串口错误：{exc}", file=sys.stderr)
        return 1

    print(f"\n完成：共保存 {count} 点到 {args.output.resolve()}")
    if count < 500:
        print("警告：样本偏少；建议覆盖完整球面并采集至少数千点。")
    return 0 if count else 2


if __name__ == "__main__":
    raise SystemExit(main())

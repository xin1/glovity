"""Unified build, flash, pair, and verification entry point for TELEX devices."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware" / "TELEX-Firmware-20261018"
PAIR_TOOL = ROOT / "tools" / "pair_telex_espnow.py"


def run(command: list[str | Path], cwd: Path = ROOT) -> None:
    printable = " ".join(f'"{item}"' if " " in str(item) else str(item) for item in command)
    print(f"> {printable}")
    subprocess.run([str(item) for item in command], cwd=cwd, check=True)


def require(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise SystemExit(f"找不到 {name}。请在 ESP-IDF 6.0 PowerShell 中运行此命令。")
    return executable


def powershell() -> str:
    return shutil.which("pwsh") or require("powershell")


def add_port(parser: argparse.ArgumentParser, name: str = "port") -> None:
    parser.add_argument(f"--{name}", required=True, help="Windows 串口，例如 COM65")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="TELEX 设备统一构建、烧录、配对与验证工具")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("ports", help="列出当前串口")

    inspect = commands.add_parser("inspect", help="读取设备 MAC")
    add_port(inspect)
    inspect.add_argument("--chip", required=True, choices=("esp32s3", "esp32c6"))

    commands.add_parser("build-glove", help="编译修改版 ESP32-S3 手套固件")
    flash_glove = commands.add_parser("flash-glove", help="烧录修改版 ESP32-S3 手套固件")
    add_port(flash_glove)
    flash_glove.add_argument("--baud", type=int, default=460800)

    commands.add_parser("build-receiver", help="编译 ESP32-C6 接收器固件")
    flash_receiver = commands.add_parser("flash-receiver", help="烧录 ESP32-C6 接收器固件")
    add_port(flash_receiver)

    pair = commands.add_parser("pair", help="配对一个左手和一个 C6 接收器")
    add_port(pair, "glove-port")
    add_port(pair, "receiver-port")
    pair.add_argument("--suite", required=True, help="本套设备的唯一 Suite ID")

    verify = commands.add_parser("verify", help="验证 C6 是否收到左手 ESP-NOW 数据")
    add_port(verify, "receiver-port")
    return root


def main() -> int:
    args = parser().parse_args()
    python = sys.executable
    source = FIRMWARE / "source" / "firmware"

    if args.command == "ports":
        run([python, "-m", "serial.tools.list_ports"])
    elif args.command == "inspect":
        run([python, "-m", "esptool", "--chip", args.chip, "--port", args.port, "read-mac"])
    elif args.command == "build-glove":
        run([powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", FIRMWARE / "Build-S3-Glovity.ps1"], FIRMWARE)
    elif args.command == "flash-glove":
        run([
            powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            FIRMWARE / "Flash-S3-Glovity.ps1", "-Port", args.port, "-Baud", str(args.baud),
        ], FIRMWARE)
    elif args.command == "build-receiver":
        idf = require("idf.py")
        run([
            idf, "-C", source, "-B", FIRMWARE / "build" / "esp32c6",
            "-D", f"SDKCONFIG={FIRMWARE / 'build-config' / 'esp32c6' / 'sdkconfig'}",
            "-D", "IDF_TARGET=esp32c6", "build",
        ], FIRMWARE)
    elif args.command == "flash-receiver":
        idf = require("idf.py")
        run([
            idf, "-C", source, "-B", FIRMWARE / "build" / "esp32c6",
            "-p", args.port, "flash",
        ], FIRMWARE)
    elif args.command == "pair":
        run([
            python, PAIR_TOOL, "--glove-port", args.glove_port,
            "--receiver-port", args.receiver_port, "--suite", args.suite,
        ])
    elif args.command == "verify":
        run([python, PAIR_TOOL, "--receiver-port", args.receiver_port, "--verify-only"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

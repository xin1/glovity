from __future__ import annotations

import re
from pathlib import Path

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

RECEIVER_PLACEHOLDER = bytes.fromhex("DE AD BE EF 00 01")
LEFT_PLACEHOLDER = bytes.fromhex("DE AD BE EF 00 02")
RIGHT_PLACEHOLDER = bytes.fromhex("DE AD BE EF 00 03")


def parse_mac(text: str) -> bytes:
    text = text.strip()
    if not MAC_RE.fullmatch(text):
        raise ValueError(f"MAC 地址格式错误: {text!r}")
    return bytes.fromhex(text.replace(":", ""))


def normalize_mac(text: str) -> str:
    return ":".join(f"{b:02X}" for b in parse_mac(text))


def patch_exact(data: bytes, placeholder: bytes, replacement: bytes, label: str) -> bytes:
    count = data.count(placeholder)
    if count == 0:
        raise ValueError(f"固件中找不到 {label} 配对占位符")
    return data.replace(placeholder, replacement)


def make_paired_images(
    firmware_dir: Path, output_dir: Path, receiver_mac: str, left_mac: str, right_mac: str
) -> dict[str, Path]:
    return make_paired_files(
        {"receiver": firmware_dir / "receiver.merged.bin",
         "left": firmware_dir / "left.merged.bin",
         "right": firmware_dir / "right.merged.bin"},
        output_dir, receiver_mac, left_mac, right_mac,
    )


def make_paired_files(
    firmware_files: dict[str, Path], output_dir: Path,
    receiver_mac: str, left_mac: str, right_mac: str,
) -> dict[str, Path]:
    receiver = parse_mac(receiver_mac)
    left = parse_mac(left_mac)
    right = parse_mac(right_mac)
    if len({receiver, left, right}) != 3:
        raise ValueError("接收器、左手和右手 MAC 必须互不相同")

    names = {
        "receiver": "receiver.merged.bin",
        "left": "left.merged.bin",
        "right": "right.merged.bin",
    }
    blobs = {key: Path(firmware_files[key]).read_bytes() for key in names}
    blobs["receiver"] = patch_exact(blobs["receiver"], LEFT_PLACEHOLDER, left, "左手")
    blobs["receiver"] = patch_exact(blobs["receiver"], RIGHT_PLACEHOLDER, right, "右手")
    blobs["left"] = patch_exact(blobs["left"], RECEIVER_PLACEHOLDER, receiver, "接收器")
    blobs["right"] = patch_exact(blobs["right"], RECEIVER_PLACEHOLDER, receiver, "接收器")

    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for key, blob in blobs.items():
        path = output_dir / names[key]
        path.write_bytes(blob)
        result[key] = path
    return result

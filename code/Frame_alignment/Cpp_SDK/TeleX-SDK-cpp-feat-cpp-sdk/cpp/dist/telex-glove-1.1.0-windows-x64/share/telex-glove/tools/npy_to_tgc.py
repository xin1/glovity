#!/usr/bin/env python3
"""把三步校准 .npy 转换为紧凑的 .tgc 格式（后备/排查手段）。

SDK 已能直接读取 Remote_USB_ThreeStepCalibration 产出的 .npy，
本脚本仅在以下情形使用：
  - 校准文件由更新版本的 numpy 产出，SDK 内置解析器暂不支持
  - 需要一个不含 pickle、便于人工核对的中间格式

用法::

    python3 npy_to_tgc.py imu_calibration_left.npy imu_calibration_left.tgc

.tgc 布局（共 1545 字节）::

    [0:6]    magic "TGCAL1"
    [6:8]    保留，置 0
    [8]      手别: 'l' 或 'r'
    [9:1545] float64 小端 (3,16,4)，scipy (x,y,z,w) 序
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def convert(src: Path, dst: Path) -> int:
    data = np.load(src, allow_pickle=True)
    payload = data.item() if data.dtype == object else {"imu_calib_quats": data}

    quats = np.asarray(payload["imu_calib_quats"], dtype="<f8")
    if quats.shape != (3, 16, 4):
        print(f"错误: 期望 imu_calib_quats 形状 (3,16,4)，实际 {quats.shape}", file=sys.stderr)
        return 2

    side = str(payload.get("side", "left")).strip().lower()
    if side not in ("left", "right"):
        print(f"警告: side='{side}' 无法识别，按 left 处理", file=sys.stderr)
        side = "left"

    blob = b"TGCAL1\x00\x00" + (b"r" if side == "right" else b"l") + quats.tobytes(order="C")
    assert len(blob) == 9 + 3 * 16 * 4 * 8, len(blob)
    dst.write_bytes(blob)
    print(f"已写出 {dst} ({len(blob)} 字节, side={side})")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    return convert(Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

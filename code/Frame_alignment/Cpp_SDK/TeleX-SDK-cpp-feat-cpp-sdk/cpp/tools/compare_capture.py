#!/usr/bin/env python3
"""对同一份录制流，逐帧比对 C++ SDK 与 Python 实现的输出。

金标准测试（tests/golden/）覆盖的是合成姿势；本脚本补上真实数据这一侧：
C++ 侧由 dump_capture 导出 CSV，Python 侧用仓库现有实现重新解算，
逐帧比对帧号、电量与 21 个关节坐标。

用法（仓库根）::

    ./cpp/build/dump_capture capture.bin [左.npy] [右.npy] > joints_cpp.csv
    PYTHONPATH=src python3 cpp/tools/compare_capture.py capture.bin joints_cpp.csv \\
        [--calib-left 左.npy] [--calib-right 右.npy] [--tol 1e-9]

退出码 0 表示全部帧在容差内一致。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from telex.glove import fk  # noqa: E402
from telex.hub.parse import FRAME_SIZE, MAGIC, parse_glove_frame  # noqa: E402

NUM_JOINTS = 21


def _python_frames(data: bytes, calib: dict[str, str | None]):
    """与 dump_capture 相同的帧同步：找 magic，校验失败只前进 1 字节。"""
    i = 0
    while i + FRAME_SIZE <= len(data):
        if data[i:i + 2] != MAGIC:
            i += 1
            continue
        raw = data[i:i + FRAME_SIZE]
        frame = parse_glove_frame(raw, timestamp_ms=0)
        if frame is None:
            i += 1
            continue
        joints = fk.compute_skeleton_joints(frame, frame.hand, calib[frame.hand])
        yield frame.hand, frame.frame_id, raw[4], joints
        i += FRAME_SIZE


def _cpp_frames(csv_path: Path):
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            joints = np.array(
                [[float(row[f"j{j}{a}"]) for a in "xyz"] for j in range(NUM_JOINTS)]
            )
            yield row["hand"], int(row["frame_id"]), int(row["battery"]), joints


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", type=Path, help="录制流 .bin")
    ap.add_argument("cpp_csv", type=Path, help="dump_capture 的输出")
    ap.add_argument("--calib-left")
    ap.add_argument("--calib-right")
    ap.add_argument("--tol", type=float, default=1e-9,
                    help="关节坐标最大绝对误差（米），默认与 FK 三步校准金标准一致")
    args = ap.parse_args()

    calib = {"left": args.calib_left, "right": args.calib_right}
    py = list(_python_frames(args.capture.read_bytes(), calib))
    cc = list(_cpp_frames(args.cpp_csv))

    failed = 0
    if len(py) != len(cc):
        print(f"[FAIL] 帧数不一致: Python {len(py)} / C++ {len(cc)}")
        failed += 1

    worst, worst_at = 0.0, -1
    for idx, (p, c) in enumerate(zip(py, cc)):
        if p[:3] != c[:3]:
            if failed < 10:
                print(f"[FAIL] 第 {idx} 帧 (hand, frame_id, battery) 不一致: "
                      f"Python {p[:3]} / C++ {c[:3]}")
            failed += 1
            continue
        err = float(np.max(np.abs(p[3] - c[3])))
        if err > worst:
            worst, worst_at = err, idx
        if err > args.tol:
            if failed < 10:
                print(f"[FAIL] 第 {idx} 帧关节误差 {err:.3e} > {args.tol:.0e}")
            failed += 1

    hands = sorted({p[0] for p in py})
    print(f"比对 {min(len(py), len(cc))} 帧（{', '.join(hands) or '无'}），"
          f"最大关节误差 {worst:.3e} m（第 {worst_at} 帧），容差 {args.tol:.0e}")
    if failed:
        print(f"== 不一致：{failed} 项 ==")
        return 1
    print("== 一致 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())

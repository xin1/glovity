#!/usr/bin/env python3
"""TeleX CLI: 一次 read() 打印 tracker 6D + 手套 IMU/joints。"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

import numpy as np

from telex import IMU_LABELS, JOINT_LABELS, TeleXSession
from telex.tracker.constants import DEFAULT_MID_PITCH_UP_DEG
from telex.tracker.imu.math import euler_zyx_deg_from_rvec


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="TeleX 统一监视：Tracker 6D + 手指 IMU/关节",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--camera", default=0, help="相机设备索引或路径")
    p.add_argument("--imu-port", default=None, help="Hub 串口，默认 /dev/ttyACM0")
    p.add_argument("--imu-baud", type=int, default=None)
    p.add_argument(
        "--side",
        choices=("both", "left", "right"),
        default="both",
    )
    p.add_argument("--frame", choices=("mid", "left"), default="mid")
    p.add_argument(
        "--mid-pitch-up-deg",
        type=float,
        default=DEFAULT_MID_PITCH_UP_DEG,
        help="mid 坐标轴绕自身 +X 上抬的安装俯角补偿",
    )
    p.add_argument("--strict", action="store_true")
    p.add_argument("--no-imu-fusion", action="store_true")
    p.add_argument("--require-glove", action="store_true")
    p.add_argument(
        "--glove-calib-left",
        default=None,
        help="左手三步校准 .npy 路径（可选，缺省为未校准 FK）",
    )
    p.add_argument(
        "--glove-calib-right",
        default=None,
        help="右手三步校准 .npy 路径（可选）",
    )
    p.add_argument("--print-interval", type=float, default=0.2)
    p.add_argument("--hz", type=float, default=None, help="若设置则覆盖 print-interval")
    return p


def _fmt_pose(hand: str, pose, frame: str) -> str:
    if pose is None:
        return f"{hand.upper():5s} [{frame}] pose=unavailable"
    t_mm = np.asarray(pose.tvec, dtype=np.float64).reshape(3)
    abc = euler_zyx_deg_from_rvec(pose.rvec)
    return (
        f"{hand.upper():5s} [{frame}] "
        f"xyz_mm=({t_mm[0]:7.1f}, {t_mm[1]:7.1f}, {t_mm[2]:7.1f}) "
        f"rpy_zyx_deg=({abc[0]:6.1f}, {abc[1]:6.1f}, {abc[2]:6.1f})"
    )


def _fmt_glove(hand: str, state) -> str:
    if state is None:
        return f"{hand.upper():5s} glove=disabled"
    if state.imus is None and state.joints is None:
        return f"{hand.upper():5s} glove=unavailable"
    lines = [f"{hand.upper():5s} glove online"]
    if state.imus is not None:
        wrist = state.imus[0]
        lines.append(
            f"  IMU[0]={IMU_LABELS[0]} "
            f"quat=({wrist.quat_w:.3f},{wrist.quat_x:.3f},"
            f"{wrist.quat_y:.3f},{wrist.quat_z:.3f}) "
            f"count={len(state.imus)}"
        )
    if state.joints is not None:
        tip = state.joints[4]
        lines.append(
            f"  joints={state.joints.shape} "
            f"{JOINT_LABELS[4]}=({tip[0]:.4f},{tip[1]:.4f},{tip[2]:.4f})"
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    interval = (
        1.0 / max(args.hz, 0.1)
        if args.hz is not None
        else max(args.print_interval, 0.01)
    )
    print(
        f"[INFO] camera={args.camera} side={args.side} frame={args.frame} "
        f"fusion={not args.no_imu_fusion}",
        flush=True,
    )
    last = 0.0
    try:
        with TeleXSession(
            camera=args.camera,
            imu_port=args.imu_port,
            imu_baud=args.imu_baud,
            side=args.side,
            enable_imu_fusion=not args.no_imu_fusion,
            require_glove=args.require_glove,
            mid_pitch_up_deg=args.mid_pitch_up_deg,
            glove_calib_left=args.glove_calib_left,
            glove_calib_right=args.glove_calib_right,
        ) as telex:
            while True:
                try:
                    result = telex.read(frame=args.frame, strict=args.strict)
                except TimeoutError:
                    print("[WARN] 相机超时", file=sys.stderr, flush=True)
                    continue
                now = time.monotonic()
                if now - last < interval:
                    continue
                last = now
                for hand, state in (("left", result.left), ("right", result.right)):
                    if state is None:
                        continue
                    print(_fmt_pose(hand, state.pose, result.frame), flush=True)
                    print(_fmt_glove(hand, state), flush=True)
                print("-" * 72, flush=True)
    except KeyboardInterrupt:
        print("\n[INFO] interrupted", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

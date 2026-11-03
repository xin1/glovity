#!/usr/bin/env python3
"""TeleX 联合监视：OpenCV Tracker 可视化 + 终端结构化刷新。

展示：
  - 双目画面上的 Marker 边框 / 单 Marker 坐标轴 + Tracker 坐标轴
  - 终端：Tracker 6D（mid/left）+ 11 路 IMU + 21 关节（腕部局部系）

用法::

    python tests/test_telex.py --camera 2
    python tests/test_telex.py --camera 2 --side left --hz 15
    python tests/test_telex.py --camera 2 --no-viz
    python tests/test_telex.py --camera 2 --frame mid --no-imu-fusion
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import deque
from typing import Deque, List, Optional
from unicodedata import east_asian_width

import cv2
import numpy as np

from telex import IMU_LABELS, JOINT_LABELS, TeleXSession
from telex.tracker.constants import DEFAULT_MID_PITCH_UP_DEG
from telex.tracker.geometry import validate_rigid_transform
from telex.tracker.imu.math import euler_zyx_deg_from_rvec
from telex.tracker.vision.pose import (
    MarkerPose,
    estimate_marker_pose_fisheye,
    transform_pose,
)
from telex.tracker.viz.draw import (
    TRACKER_LABEL_COLORS,
    TrackerVisualizationUnavailable,
    _resize_for_display,
    color_for,
    draw_marker_outline,
    draw_tracker_axes,
)
from telex.types import FrameResult, HandState

# ---------------------------------------------------------------------------
# ANSI
# ---------------------------------------------------------------------------

ESC = "\033"
CLEAR = ESC + "[2J" + ESC + "[H"
HOME = ESC + "[H"
EOL = ESC + "[K"
BOLD = ESC + "[1m"
DIM = ESC + "[2m"
RST = ESC + "[0m"
CYAN = ESC + "[96m"
GRN = ESC + "[92m"
YEL = ESC + "[93m"
RED = ESC + "[91m"
MAG = ESC + "[95m"


import re

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_COL_W = 56  # 左右栏终端显示宽度（CJK 按 2 计）


def _disp_width(s: str) -> int:
    total = 0
    for ch in _ANSI_RE.sub("", s):
        total += 2 if east_asian_width(ch) in ("F", "W") else 1
    return total


def _pad(s: str, width: int = _COL_W) -> str:
    return s + " " * max(0, width - _disp_width(s))


def _fmt_quat(imu) -> str:
    return f"{imu.quat_w:6.3f} {imu.quat_x:6.3f} {imu.quat_y:6.3f} {imu.quat_z:6.3f}"


def _fmt_xyz_short(p: np.ndarray) -> str:
    return f"{p[0]:5.2f},{p[1]:5.2f},{p[2]:5.2f}"


def _pad_label(lab: str, width: int) -> str:
    return lab + " " * max(0, width - _disp_width(lab))


def _estimate_hz(timestamps: Deque[float]) -> float:
    if len(timestamps) < 2:
        return 0.0
    span = timestamps[-1] - timestamps[0]
    if span <= 0.0:
        return 0.0
    return (len(timestamps) - 1) / span


def _short_joint_label(lab: str) -> str:
    """压缩关节标签宽度：食指-MCP → 食MCP。"""
    for a, b in (
        ("食指-", "食"),
        ("中指-", "中"),
        ("小指-", "小"),
        ("无名-", "无"),
        ("拇指-", "拇"),
        ("指-", ""),
    ):
        lab = lab.replace(a, b)
    return lab.replace("指", "")


def _section(title: str) -> str:
    return f"  {BOLD}{title}{RST}"


def _status_line(hand: str, state: Optional[HandState], frame: str) -> str:
    color = CYAN if hand == "left" else MAG
    if state is None:
        return f"{color}{BOLD}{hand.upper():5s}{RST} {DIM}disabled{RST}"

    mark = f"{GRN}●{RST}" if state.available else f"{RED}○{RST}"
    if state.battery_percent is None:
        bat = f"{DIM}bat—{RST}"
    else:
        pct = int(state.battery_percent)
        if pct >= 50:
            bat_c = GRN
        elif pct >= 20:
            bat_c = YEL
        else:
            bat_c = RED
        bat = f"{bat_c}{pct:3d}%{RST}"
    return f"{color}{BOLD}{hand.upper():5s}{RST} {mark} {bat} [{frame}]"


def _pose_lines(state: Optional[HandState]) -> List[str]:
    lines = [_section("Tracker Pose")]
    if state is None or state.pose is None:
        lines.append(f"  {YEL}—{RST}")
        return lines
    t = np.asarray(state.pose.tvec, dtype=np.float64).reshape(3)
    r = euler_zyx_deg_from_rvec(state.pose.rvec)
    lines.append(
        f"  xyz({t[0]:6.1f},{t[1]:6.1f},{t[2]:6.1f})"
        f" rpy({r[0]:5.1f},{r[1]:5.1f},{r[2]:5.1f})"
    )
    return lines


def _imu_lines(state: Optional[HandState]) -> List[str]:
    lines = [_section("Hands IMU")]
    if state is None or state.imus is None:
        lines.append(f"  {YEL}—{RST}")
        return lines
    lines.append(f"  {DIM}#  label    qw     qx     qy     qz{RST}")
    for i, imu in enumerate(state.imus):
        lab = IMU_LABELS[i] if i < len(IMU_LABELS) else f"i{i}"
        lines.append(f"  {i:2d} {_pad_label(lab, 8)}{_fmt_quat(imu)}")
    return lines


def _joint_lines(state: Optional[HandState], *, tips_only: bool) -> List[str]:
    lines = [_section("Hands Joints")]
    if state is None or state.joints is None:
        lines.append(f"  {YEL}—{RST}")
        return lines
    if not tips_only:
        lines.append(f"  {DIM}#  joint   x,y,z (m, wrist){RST}")
        n = min(21, int(state.joints.shape[0]))
        for i in range(n):
            lab = JOINT_LABELS[i] if i < len(JOINT_LABELS) else f"j{i}"
            lab = _short_joint_label(lab)
            lines.append(
                f"  {i:2d} {_pad_label(lab, 7)}{_fmt_xyz_short(state.joints[i])}"
            )
    tips = (("食", 4), ("中", 8), ("小", 12), ("无", 16), ("拇", 20))
    lines.append(f"  {DIM}Tips{RST}")
    for k in range(0, len(tips), 2):
        chunk = tips[k : k + 2]
        tip_s = "  ".join(
            f"{n}[{_fmt_xyz_short(state.joints[idx])}]" for n, idx in chunk
        )
        lines.append("  " + tip_s)
    return lines


def _pad_block(block: List[str], height: int) -> List[str]:
    out = list(block)
    while len(out) < height:
        out.append("")
    return out


def _zip_columns(left: List[str], right: List[str]) -> List[str]:
    n = max(len(left), len(right))
    out: List[str] = []
    for i in range(n):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        out.append(f"{_pad(l)}│{_pad(r)}")
    return out


def render_terminal(
    *,
    camera,
    side: str,
    frame: str,
    result: FrameResult,
    rate_hz: float,
    first: bool,
    tips_only: bool = False,
) -> str:
    """左右并排；按区块对齐标题（Tracker Pose / Hands IMU / Hands Joints）。"""
    header = [
        f"{BOLD}=== TeleX ==={RST} cam={camera} side={side} frame={frame} "
        f"{rate_hz:5.1f}Hz t={result.timestamp_s:.3f} "
        f"{DIM}q/ESC·Ctrl+C{RST}",
        f"{DIM}{'─' * _COL_W}┼{'─' * _COL_W}{RST}",
    ]

    status = _zip_columns(
        [_status_line("left", result.left, frame)],
        [_status_line("right", result.right, frame)],
    )

    body: List[str] = []
    for build in (
        lambda st: _pose_lines(st),
        lambda st: _imu_lines(st),
        lambda st: _joint_lines(st, tips_only=tips_only),
    ):
        left_b = build(result.left)
        right_b = build(result.right)
        h = max(len(left_b), len(right_b))
        body.extend(
            _zip_columns(_pad_block(left_b, h), _pad_block(right_b, h))
        )

    lines: List[str] = [CLEAR if first else HOME]
    for row in header + status + body:
        lines.append(row + f"{EOL}\n")
    lines.append(ESC + "[J")
    return "".join(lines)


def pose_in_left_camera(
    pose: Optional[MarkerPose],
    *,
    print_frame: str,
    mid_from_left: np.ndarray,
) -> Optional[MarkerPose]:
    """可视化投影始终在左目系；若终端打印的是 mid，则变换回 left。"""
    if pose is None:
        return None
    if print_frame == "left":
        return pose
    mid_to_left = np.linalg.inv(mid_from_left)
    return transform_pose(pose, mid_to_left)


def _draw_markers_on_image(
    image: np.ndarray,
    marker_update,
    *,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    marker_size_mm: float,
    axis_len_mm: float,
) -> None:
    """绘制单目上检测到的 marker 边框与局部坐标轴。"""
    if marker_update is None:
        return
    for obs in marker_update.display_observations:
        marker = obs.marker
        held = obs.status == "held"
        draw_marker_outline(image, marker.corners, marker.marker_id, held=held)
        if held:
            continue
        try:
            pose = estimate_marker_pose_fisheye(
                marker.corners,
                marker_size_mm,
                camera_matrix,
                dist_coeffs,
            )
            draw_tracker_axes(
                image,
                camera_matrix,
                dist_coeffs,
                pose,
                axis_len_mm,
                f"M{marker.marker_id}",
                color_for(marker.marker_id),
            )
        except (TrackerVisualizationUnavailable, ValueError, cv2.error):
            pass


def draw_overlay(
    telex: TeleXSession,
    result: FrameResult,
    *,
    print_frame: str,
    display_scale: float,
) -> np.ndarray:
    tracker = telex.tracker
    if tracker is None:
        raise RuntimeError("TeleXSession.tracker 未就绪")
    calibration = tracker.calibration
    mid_from_left = tracker.mid_from_left_mm
    left_to_right = validate_rigid_transform(
        np.linalg.inv(calibration.right_to_left_mm),
        "left_to_right_mm",
    )
    left_vis = result.left_image.copy() if result.left_image is not None else None
    right_vis = result.right_image.copy() if result.right_image is not None else None
    if left_vis is None or right_vis is None:
        raise RuntimeError("无相机图像，无法可视化")

    marker_size = float(tracker.marker_size_mm)
    marker_axis_len = marker_size * 0.55
    tracker_axis_len = marker_size * 0.75

    _draw_markers_on_image(
        left_vis,
        tracker.last_left_marker_update,
        camera_matrix=calibration.left.camera_matrix,
        dist_coeffs=calibration.left.dist_coeffs,
        marker_size_mm=marker_size,
        axis_len_mm=marker_axis_len,
    )
    _draw_markers_on_image(
        right_vis,
        tracker.last_right_marker_update,
        camera_matrix=calibration.right.camera_matrix,
        dist_coeffs=calibration.right.dist_coeffs,
        marker_size_mm=marker_size,
        axis_len_mm=marker_axis_len,
    )

    for hand, state in (("left", result.left), ("right", result.right)):
        if state is None or state.pose is None:
            continue
        pose_left = pose_in_left_camera(
            state.pose,
            print_frame=print_frame,
            mid_from_left=mid_from_left,
        )
        if pose_left is None:
            continue
        color = TRACKER_LABEL_COLORS[hand]
        try:
            draw_tracker_axes(
                left_vis,
                calibration.left.camera_matrix,
                calibration.left.dist_coeffs,
                pose_left,
                tracker_axis_len,
                hand.upper(),
                color,
            )
        except TrackerVisualizationUnavailable:
            pass
        try:
            draw_tracker_axes(
                right_vis,
                calibration.right.camera_matrix,
                calibration.right.dist_coeffs,
                transform_pose(pose_left, left_to_right),
                tracker_axis_len,
                hand.upper(),
                color,
            )
        except TrackerVisualizationUnavailable:
            pass

    return np.hstack(
        [
            _resize_for_display(left_vis, display_scale),
            _resize_for_display(right_vis, display_scale),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="TeleX：Tracker 可视化 + 终端结构化刷新（位姿/IMU/关节）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--camera", default=2, help="相机设备索引或路径")
    p.add_argument("--imu-port", default=None)
    p.add_argument("--imu-baud", type=int, default=None)
    p.add_argument("--side", choices=("both", "left", "right"), default="both")
    p.add_argument("--frame", choices=("mid", "left"), default="mid")
    p.add_argument(
        "--mid-pitch-up-deg",
        type=float,
        default=DEFAULT_MID_PITCH_UP_DEG,
        help="mid 坐标轴绕自身 +X 上抬的安装俯角补偿",
    )
    p.add_argument("--hz", type=float, default=12.0, help="终端刷新频率")
    p.add_argument("--no-imu-fusion", action="store_true")
    p.add_argument("--require-glove", action="store_true")
    p.add_argument("--viz", dest="viz", action="store_true", default=True)
    p.add_argument("--no-viz", dest="viz", action="store_false")
    p.add_argument("--display-scale", type=float, default=1.0)
    p.add_argument(
        "--tips-only",
        action="store_true",
        help="Hands Joints 仅显示指尖摘要（默认打印全部 21 关节）",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.display_scale <= 0 or args.display_scale > 1.0:
        raise ValueError("--display-scale 必须在 (0, 1]")

    interval = 1.0 / max(args.hz, 0.1)
    timestamps: Deque[float] = deque(maxlen=80)
    last_draw = 0.0

    print(
        f"[INFO] camera={args.camera} side={args.side} frame={args.frame} "
        f"viz={args.viz} fusion={not args.no_imu_fusion}",
        flush=True,
    )
    print("[INFO] 可视化窗口按 q/ESC 退出；终端 Ctrl+C 结束", flush=True)

    first = True
    try:
        with TeleXSession(
            camera=args.camera,
            imu_port=args.imu_port,
            imu_baud=args.imu_baud,
            side=args.side,
            enable_imu_fusion=not args.no_imu_fusion,
            require_glove=args.require_glove,
            exposure=85,
            mid_pitch_up_deg=args.mid_pitch_up_deg,
        ) as telex:
            while True:
                t0 = time.monotonic()
                try:
                    result = telex.read(frame=args.frame, timeout=3.0)
                except TimeoutError:
                    print("[WARN] 相机超时", file=sys.stderr, flush=True)
                    continue

                timestamps.append(time.monotonic())
                sys.stdout.write(
                    render_terminal(
                        camera=args.camera,
                        side=args.side,
                        frame=args.frame,
                        result=result,
                        rate_hz=_estimate_hz(timestamps),
                        first=first,
                        tips_only=args.tips_only,
                    )
                )
                sys.stdout.flush()
                first = False

                if args.viz:
                    try:
                        canvas = draw_overlay(
                            telex,
                            result,
                            print_frame=args.frame,
                            display_scale=args.display_scale,
                        )
                        cv2.imshow("TeleX Tracker", canvas)
                        key = cv2.waitKey(1) & 0xFF
                        if key in (ord("q"), 27):
                            break
                    except Exception as exc:
                        now = time.monotonic()
                        if now - last_draw > 2.0:
                            print(f"[WARN] 可视化失败: {exc}", file=sys.stderr, flush=True)
                            last_draw = now

                remaining = interval - (time.monotonic() - t0)
                if remaining > 0:
                    time.sleep(remaining)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
    finally:
        if args.viz:
            cv2.destroyAllWindows()
        print("已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

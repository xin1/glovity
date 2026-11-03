"""Command-line entry for dual-hand stereo tracker."""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

import cv2
import numpy as np

from telex.tracker.camera.calibration import (
    StereoCalibration,
    load_fisheye_stereo_calibration,
)
from telex.tracker.camera.dual_reader import DualCameraReader
from telex.tracker.constants import (
    DEFAULT_MARKER_SIZE_MM,
    DEFAULT_MID_PITCH_UP_DEG,
    DEFAULT_TARGET_IDS,
)
from telex.tracker.filter.outlier import (
    build_tracker_outlier_filters,
    build_tracker_outputs,
    update_tracker_outlier_vision,
)
from telex.tracker.filter.smoother import (
    OneEuroParams,
    TeleopPoseSmoothConfig,
    TeleopPoseSmoother,
    apply_output_pose_smoothing,
)
from telex.tracker.geometry import validate_rigid_transform
from telex.tracker.imu.runtime import (
    _print_cleanup_warning,
    start_dual_imu_runtime,
    stop_imu_subscribers,
)
from telex.tracker.io.jsonl import write_frame_record
from telex.tracker.resources import default_calibration_path
from telex.tracker.types import (
    PnPSelectionConfig,
    TrackerOutlierState,
    TrackerVisionMemory,
)
from telex.tracker.vision.pipeline import estimate_tracker_vision, make_detector
from telex.tracker.vision.pose import transform_pose
from telex.tracker.vision.rig import build_hand_rigs
from telex.tracker.vision.tracking import (
    MultiMarkerTracker,
    fresh_corner_motion_px,
)
from telex.tracker.viz.draw import (
    TRACKER_LABEL_COLORS,
    TrackerVisualizationUnavailable,
    _resize_for_display,
    draw_marker_outline,
    draw_tracker_axes,
    format_imu_debug_line,
    format_tracker_line,
)

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="鱼眼双目实时跟踪左右手 Find-GCP 多 marker 刚体 6D 位姿",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--camera", default=2, help="相机设备索引或 /dev/video 路径，默认 2")
    parser.add_argument("--calibration", default=None, help="鱼眼双目标定 yml 路径（默认包内 data/stereo_fisheye_calib.yml）")
    parser.add_argument("--marker-size-mm", type=float, default=DEFAULT_MARKER_SIZE_MM,
                        help="marker 黑色外边框边长 (mm)")
    parser.add_argument(
        "--mid-pitch-up-deg",
        type=float,
        default=DEFAULT_MID_PITCH_UP_DEG,
        help="mid 坐标轴绕自身 +X 上抬的安装俯角补偿；内部以 Rx(-角度) 变换",
    )
    # ── 相机曝光 ────────────────────────────────────────────────────
    parser.add_argument("--auto-exposure", action="store_true", help="启用相机自动曝光（默认手动）")
    parser.add_argument("--exposure", type=float, default=85, help="手动曝光值 (V4L2 约 100μs 单位)")
    parser.add_argument("--gain", type=float, default=100, help="相机增益")
    # ── 输出 ────────────────────────────────────────────────────────
    parser.add_argument("--print-pose", action="store_true", help="在终端周期性打印位姿")
    parser.add_argument("--print-interval", type=float, default=0.5, help="终端打印间隔 (秒)")
    parser.add_argument("--jsonl", help="把位姿记录追加写入该 JSONL 文件")
    parser.add_argument("--no-display", action="store_true", help="禁用 OpenCV 窗口")
    # ── marker 时序跟踪 ─────────────────────────────────────────────
    parser.add_argument("--marker-hold-frames", type=int, default=4)
    parser.add_argument(
        "--marker-smooth-window",
        type=int,
        default=1,
        help="静止/微动时用于 PnP 的短角点历史长度；快速移动时自动旁路",
    )
    parser.add_argument(
        "--marker-smooth-static-shift-px",
        type=float,
        default=5.0,
        help="角点最大帧间位移不超过此值时才平滑；0 近似关闭",
    )
    parser.add_argument("--marker-max-shift-px", type=float, default=80.0)
    parser.add_argument("--marker-min-enter-area-px", type=float, default=5.0)
    parser.add_argument("--marker-min-keep-area-px", type=float, default=1.0)
    parser.add_argument("--marker-enter-frames", type=int, default=1)
    # hold 仅用于 HUD/调试显示；PnP 永远只使用当前帧 fresh marker。
    # ── PnP 二义性、多 marker 一致性与双目门控 ─────────────────────
    parser.add_argument("--pnp-pair-max-rotation-deg", type=float, default=35.0)
    parser.add_argument("--pnp-pair-max-translation-mm", type=float, default=45.0)
    parser.add_argument("--pnp-marker-max-reprojection-px", type=float, default=4.0)
    parser.add_argument("--pnp-prior-rotation-weight", type=float, default=0.05)
    parser.add_argument("--pnp-prior-translation-weight", type=float, default=0.02)
    parser.add_argument("--pnp-pair-rotation-weight", type=float, default=0.03)
    parser.add_argument("--pnp-pair-translation-weight", type=float, default=0.01)
    parser.add_argument("--stereo-max-rotation-deg", type=float, default=12.0)
    parser.add_argument("--stereo-max-translation-mm", type=float, default=25.0)
    parser.add_argument(
        "--marker-front-axis-sign",
        type=int,
        choices=(-1, 0, 1),
        default=0,
        help="发光正面法向对应 marker Z 轴符号；0 禁用正反面约束",
    )
    parser.add_argument(
        "--marker-front-min-cos",
        type=float,
        default=0.0,
        help="发光面法向与朝向相机方向的最小余弦",
    )
    # ── 检测增强（折面 marker 灰边/局部欠曝）────────────────────────
    parser.add_argument(
        "--detect-preset",
        choices=("default", "folded"),
        default="folded",
        help="folded 针对三折面 tracker 启用 CLAHE 与更紧局部阈值",
    )
    parser.add_argument(
        "--detect-preprocess",
        choices=("auto", "none", "clahe", "gamma", "clahe-gamma"),
        default="auto",
        help="检测前灰度预处理；auto 时 folded 用 clahe，default 用 none",
    )
    parser.add_argument(
        "--marker-corner-inset",
        type=float,
        default=0.0,
        help="额外将检测四角沿中心内缩的比例；默认边缘重定位已对齐黑色外框",
    )
    parser.add_argument(
        "--marker-border-refine",
        action="store_true",
        default=True,
        help="将已解码 marker 四角重定位到黑色外框与白色静区的内侧边缘",
    )
    parser.add_argument(
        "--no-marker-border-refine",
        action="store_false",
        dest="marker_border_refine",
        help="关闭黑色外框边缘重定位",
    )
    parser.add_argument("--detect-clahe-clip", type=float, default=6.0, help="CLAHE clipLimit")
    parser.add_argument("--detect-clahe-grid", type=int, default=8, help="CLAHE tileGridSize")
    parser.add_argument("--detect-gamma", type=float, default=1.15, help="gamma 预处理指数")
    parser.add_argument(
        "--detect-warp-pixels-per-cell",
        type=int,
        default=8,
        help="透视采样中每个 marker 单元的像素数；小 marker 建议使用 8",
    )
    parser.add_argument(
        "--detect-corner-refine",
        choices=("auto", "subpix", "apriltag", "contour"),
        default="auto",
        help="角点细化；auto 跟随 preset。apriltag 更慢，subpix 更适合实时",
    )
    parser.add_argument(
        "--detect-downscale",
        type=float,
        default=1.0,
        help="检测前缩小图像比例 (0,1]，如 0.75/0.5 可显著提速",
    )
    parser.add_argument(
        "--display-scale",
        type=float,
        default=1.0,
        help="显示窗口缩放比例 (0,1]，仅影响可视化，不影响位姿",
    )
    # ── 位姿离群过滤 ────────────────────────────────────────────────
    parser.add_argument(
        "--pose-outlier-enable",
        action="store_true",
        default=True,
        help="启用逐帧位姿离群过滤（默认开启）",
    )
    parser.add_argument(
        "--pose-outlier-disable",
        action="store_true",
        help="关闭位姿离群过滤",
    )
    parser.add_argument("--pose-outlier-max-rotation-deg", type=float, default=45.0,
                        help="帧间最大旋转（相对 30Hz）；更大视为轴翻转候选")
    parser.add_argument("--pose-outlier-max-translation-mm", type=float, default=100.0,
                        help="帧间平移门限（相对 30Hz）；纯平移超限不再 hold")
    parser.add_argument("--pose-outlier-max-hold-frames", type=int, default=1,
                        help="判定为轴翻转时最多沿用旧位姿的帧数")
    # ── 输出端平滑（跟手优先；要更稳可降低 cutoff / beta）────────────
    parser.add_argument(
        "--pose-smooth-enable",
        action="store_true",
        default=True,
        help="启用输出 SE(3) OneEuro/SLERP 平滑（默认开启）",
    )
    parser.add_argument(
        "--pose-smooth-disable",
        action="store_true",
        help="关闭输出平滑",
    )
    parser.add_argument(
        "--pose-smooth-min-cutoff",
        type=float,
        default=1.0,
        help="平移 OneEuro 最小截止频率 Hz，越低越稳、越高越跟手",
    )
    parser.add_argument(
        "--pose-smooth-beta",
        type=float,
        default=0.015,
        help="平移 OneEuro 速度增益，越大越跟手",
    )
    parser.add_argument(
        "--pose-smooth-rot-min-cutoff",
        type=float,
        default=1.8,
        help="旋转平滑最小截止频率 Hz",
    )
    parser.add_argument(
        "--pose-smooth-rot-beta",
        type=float,
        default=0.15,
        help="旋转平滑速度增益",
    )
    parser.add_argument(
        "--pose-smooth-image-motion-threshold-px",
        type=float,
        default=10.0,
        help="超过此角点帧间位移时提高平滑带宽，保证快速运动跟手",
    )
    parser.add_argument(
        "--pose-smooth-image-motion-gain-hz-per-px",
        type=float,
        default=8.0,
        help="超过图像运动阈值后的额外截止频率增益",
    )
    parser.add_argument(
        "--stereo-hysteresis-frames",
        type=int,
        default=4,
        help="左右目/立体源切换滞回帧数，抑制 vision_source 抖动",
    )
    # ── IMU 融合（默认尝试 wireless；失败回退纯视觉）──────────────
    parser.add_argument(
        "--no-imu",
        action="store_true",
        help="强制纯视觉，跳过 wireless IMU 初始化",
    )
    parser.add_argument(
        "--imu-port",
        default=None,
        help="wireless 串口路径（默认 telex.tracker.imu.receiver.DEFAULT_PORT）",
    )
    parser.add_argument(
        "--imu-baud",
        type=int,
        default=None,
        help="wireless 波特率（默认 telex.tracker.imu.receiver.DEFAULT_BAUD）",
    )
    parser.add_argument("--imu-max-age-sec", type=float, default=0.35)
    parser.add_argument("--imu-hold-sec", type=float, default=0.30)
    parser.add_argument("--imu-learn-alpha", type=float, default=0.05)
    parser.add_argument("--imu-min-learn-frames", type=int, default=5)
    parser.add_argument("--imu-near-distance-m", type=float, default=0.15)
    parser.add_argument("--imu-far-distance-m", type=float, default=0.35)
    parser.add_argument("--imu-low-error-px", type=float, default=1.0)
    parser.add_argument("--imu-high-error-px", type=float, default=4.0)
    parser.add_argument("--imu-max-weight", type=float, default=0.85)
    parser.add_argument("--imu-no-auto-learn-extrinsic", action="store_true", default=False)
    parser.add_argument(
        "--imu-translation-fusion",
        action="store_true",
        help="启用平移融合（默认关闭；需可靠 lever arm）",
    )
    parser.add_argument(
        "--imu-no-translation-fusion",
        action="store_true",
        default=False,
        help="兼容旧参数：显式关闭平移融合",
    )
    parser.add_argument("--imu-max-translation-blend-m", type=float, default=0.05)
    parser.add_argument("--imu-batch-calibration-samples", type=int, default=10)
    parser.add_argument(
        "--imu-left-lever-arm",
        type=float,
        nargs=3,
        default=[0.0, 0.0, 0.0],
        metavar=("X", "Y", "Z"),
        help="左手 IMU→对应 tracker 物体系原点杠杆臂 (mm)",
    )
    parser.add_argument(
        "--imu-right-lever-arm",
        type=float,
        nargs=3,
        default=[0.0, 0.0, 0.0],
        metavar=("X", "Y", "Z"),
        help="右手 IMU→对应 tracker 物体系原点杠杆臂 (mm)",
    )
    parser.add_argument("--imu-no-gyro-prediction", action="store_true", default=False)
    parser.add_argument("--debug-imu", action="store_true", help="在 HUD/日志中显示 IMU 诊断")
    return parser

def cleanup_runtime_resources(
    reader,
    imu_subscribers,
    jsonl_handle,
    *,
    display_enabled: bool,
) -> Optional[BaseException]:
    first_error = None

    def capture(action, label: str) -> None:
        nonlocal first_error
        try:
            action()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            _print_cleanup_warning(f"[WARN] 清理 {label} 失败: {exc}")

    if reader is not None:
        capture(reader.close, "camera reader")

    subscriber_error = stop_imu_subscribers(imu_subscribers)
    if first_error is None and subscriber_error is not None:
        first_error = subscriber_error

    if jsonl_handle is not None:
        capture(jsonl_handle.close, "JSONL")
    if display_enabled:
        capture(cv2.destroyAllWindows, "OpenCV windows")

    return first_error

def main() -> int:
    args = build_arg_parser().parse_args()
    if not args.calibration:
        args.calibration = default_calibration_path()
    if args.marker_size_mm <= 0:
        raise ValueError("marker-size-mm 必须为正数")

    rigs = build_hand_rigs()
    target_ids = set(DEFAULT_TARGET_IDS)
    outlier_filters = build_tracker_outlier_filters(
        rigs,
        max_rotation_deg=args.pose_outlier_max_rotation_deg,
        max_translation_mm=args.pose_outlier_max_translation_mm,
        max_hold_frames=args.pose_outlier_max_hold_frames,
    )
    outlier_states = {
        hand: TrackerOutlierState() for hand in rigs
    }
    calibration: StereoCalibration = load_fisheye_stereo_calibration(args.calibration)
    right_to_left_mm = validate_rigid_transform(
        calibration.right_to_left_mm,
        "right_to_left_mm",
    )
    try:
        left_to_right_mm = validate_rigid_transform(
            np.linalg.inv(right_to_left_mm),
            "left_to_right_mm",
        )
    except np.linalg.LinAlgError as exc:
        raise ValueError("双目标定外参必须是有限刚体变换") from exc
    axis_len = args.marker_size_mm * 0.75
    detector = make_detector(args)
    pnp_config = PnPSelectionConfig(
        pair_max_rotation_deg=args.pnp_pair_max_rotation_deg,
        pair_max_translation_mm=args.pnp_pair_max_translation_mm,
        marker_max_reprojection_px=args.pnp_marker_max_reprojection_px,
        prior_rotation_weight=args.pnp_prior_rotation_weight,
        prior_translation_weight=args.pnp_prior_translation_weight,
        pair_rotation_weight=args.pnp_pair_rotation_weight,
        pair_translation_weight=args.pnp_pair_translation_weight,
        stereo_max_rotation_deg=args.stereo_max_rotation_deg,
        stereo_max_translation_mm=args.stereo_max_translation_mm,
        marker_front_axis_sign=args.marker_front_axis_sign,
        marker_front_min_cos=args.marker_front_min_cos,
    )
    vision_memories = {
        hand: TrackerVisionMemory() for hand in rigs
    }
    
    pose_smoothers = {
        hand: TeleopPoseSmoother(
            TeleopPoseSmoothConfig(
                enabled=True,
                translation=OneEuroParams(
                    min_cutoff_hz=args.pose_smooth_min_cutoff,
                    beta=args.pose_smooth_beta,
                    d_cutoff_hz=1.0,
                ),
                rotation_min_cutoff_hz=args.pose_smooth_rot_min_cutoff,
                rotation_beta=args.pose_smooth_rot_beta,
                image_motion_threshold_px=(
                    args.pose_smooth_image_motion_threshold_px
                ),
                image_motion_cutoff_gain_hz_per_px=(
                    args.pose_smooth_image_motion_gain_hz_per_px
                ),
            )
        )
        for hand in rigs
    }

    left_tracker = MultiMarkerTracker(
        hold_frames=args.marker_hold_frames,
        smooth_window=args.marker_smooth_window,
        max_corner_shift_px=args.marker_max_shift_px,
        min_enter_area_px=args.marker_min_enter_area_px,
        min_keep_area_px=args.marker_min_keep_area_px,
        enter_frames=args.marker_enter_frames,
        adaptive_smooth_shift_px=args.marker_smooth_static_shift_px,
    )
    right_tracker = MultiMarkerTracker(
        hold_frames=args.marker_hold_frames,
        smooth_window=args.marker_smooth_window,
        max_corner_shift_px=args.marker_max_shift_px,
        min_enter_area_px=args.marker_min_enter_area_px,
        min_keep_area_px=args.marker_min_keep_area_px,
        enter_frames=args.marker_enter_frames,
        adaptive_smooth_shift_px=args.marker_smooth_static_shift_px,
    )

    imu_subscribers = {}
    imu_fusions = {}
    reader = None
    jsonl_handle = None
    last_frame_id = -1
    last_print_ts = 0.0
    last_fps = 0.0
    prev_ts: Optional[float] = None

    try:
        if args.no_imu:
            print("[IMU] --no-imu：使用纯视觉", flush=True)
        else:
            imu_subscribers, imu_fusions = start_dual_imu_runtime(args)
            if not imu_fusions:
                print(
                    "[WARN][IMU] 未检测到可用 IMU，回退纯视觉跟踪",
                    file=sys.stderr,
                    flush=True,
                )

        reader = DualCameraReader(
            index=args.camera,
            auto_exposure=args.auto_exposure,
            exposure=args.exposure,
            gain=args.gain,
        )

        print(
            f"[INFO] LEFT IDs={sorted(rigs['left'].object_to_marker)} "
            f"| RIGHT IDs={sorted(rigs['right'].object_to_marker)} "
            f"| marker_size={args.marker_size_mm:.1f} mm "
            f"| baseline_mm={np.linalg.norm(calibration.right_to_left_mm[:3, 3]):.1f} "
            f"| dictionary=FINDGCP_3X3 "
            f"| detect_downscale={args.detect_downscale:.2f}",
            flush=True,
        )
        print("[INFO] q/ESC=退出", flush=True)

        jsonl_handle = open(args.jsonl, "a", encoding="utf-8") if args.jsonl else None

        while True:
            try:
                left, right, last_frame_id, frame_ts = reader.wait_next_frame(
                    last_frame_id=last_frame_id, timeout=3.0
                )
            except TimeoutError:
                print("[ERROR] 超时未收到新相机帧", file=sys.stderr)
                break

            if prev_ts is not None:
                dt = frame_ts - prev_ts
                if dt > 0:
                    last_fps = 0.9 * last_fps + 0.1 * (1.0 / dt) if last_fps > 0 else 1.0 / dt
            prev_ts = frame_ts

            left_marker_update = left_tracker.update(
                detector.detect_targets(left, target_ids)
            )
            right_marker_update = right_tracker.update(
                detector.detect_targets(right, target_ids)
            )
            # Critical: held markers are display-only. PnP receives current-frame
            # fresh observations and can no longer mix stale/new rigid geometry.
            left_markers = left_marker_update.fresh_markers
            right_markers = right_marker_update.fresh_markers

            vision_by_hand = {
                hand: estimate_tracker_vision(
                    left_markers,
                    right_markers,
                    rig,
                    args.marker_size_mm,
                    calibration,
                    memory=vision_memories[hand],
                    config=pnp_config,
                    stereo_hysteresis_frames=args.stereo_hysteresis_frames,
                )
                for hand, rig in rigs.items()
            }
            if args.pose_outlier_enable and not args.pose_outlier_disable:
                for hand, vision in list(vision_by_hand.items()):
                    (
                        vision_by_hand[hand],
                        outlier_states[hand],
                    ) = update_tracker_outlier_vision(
                        vision,
                        outlier_filters[hand],
                        outlier_states[hand],
                    )
            now_s = time.monotonic()
            corner_motion_px_by_hand = {
                hand: max(
                    fresh_corner_motion_px(
                        left_marker_update, rig.object_to_marker
                    ),
                    fresh_corner_motion_px(
                        right_marker_update, rig.object_to_marker
                    ),
                )
                for hand, rig in rigs.items()
            }
            outputs = build_tracker_outputs(
                vision_by_hand,
                imu_fusions=imu_fusions,
                imu_subscribers=imu_subscribers,
                debug_imu=args.debug_imu,
                now_s=now_s,
            )
            if args.pose_smooth_enable and not args.pose_smooth_disable:
                outputs = apply_output_pose_smoothing(
                    outputs,
                    pose_smoothers,
                    now_s=now_s,
                    corner_motion_px_by_hand=corner_motion_px_by_hand,
                )

            # ── 输出到终端 / JSONL ─────────────────────────────────
            do_print = args.print_pose and (time.monotonic() - last_print_ts) >= args.print_interval
            if do_print:
                last_print_ts = time.monotonic()
                for hand in ("left", "right"):
                    print(
                        format_tracker_line(hand, outputs[hand]),
                        flush=True,
                    )
            if jsonl_handle is not None:
                write_frame_record(
                    jsonl_handle,
                    time.time(),
                    outputs["left"],
                    outputs["right"],
                )

            # ── 可视化 ─────────────────────────────────────────────
            if not args.no_display:
                left_vis = left.copy()
                right_vis = right.copy()

                for observation in left_marker_update.fresh:
                    marker = observation.marker
                    draw_marker_outline(
                        left_vis, marker.corners, marker.marker_id
                    )
                for observation in left_marker_update.held:
                    marker = observation.marker
                    draw_marker_outline(
                        left_vis, marker.corners, marker.marker_id, held=True
                    )
                for observation in right_marker_update.fresh:
                    marker = observation.marker
                    draw_marker_outline(
                        right_vis, marker.corners, marker.marker_id
                    )
                for observation in right_marker_update.held:
                    marker = observation.marker
                    draw_marker_outline(
                        right_vis, marker.corners, marker.marker_id, held=True
                    )

                for hand in ("left", "right"):
                    output = outputs[hand]
                    if output.pose is None:
                        continue
                    try:
                        draw_tracker_axes(
                            left_vis,
                            calibration.left.camera_matrix,
                            calibration.left.dist_coeffs,
                            output.pose,
                            axis_len,
                            hand.upper(),
                            TRACKER_LABEL_COLORS[hand],
                        )
                    except TrackerVisualizationUnavailable:
                        pass
                    pose_in_right = transform_pose(
                        output.pose,
                        left_to_right_mm,
                    )
                    try:
                        draw_tracker_axes(
                            right_vis,
                            calibration.right.camera_matrix,
                            calibration.right.dist_coeffs,
                            pose_in_right,
                            axis_len,
                            hand.upper(),
                            TRACKER_LABEL_COLORS[hand],
                        )
                    except TrackerVisualizationUnavailable:
                        pass

                available = sum(
                    outputs[hand].pose is not None
                    for hand in ("left", "right")
                )
                status = (
                    f"FPS {last_fps:5.1f} "
                    f"| trackers {available}/2"
                )
                cv2.putText(left_vis, status, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 255, 255), 2, cv2.LINE_AA)
                marker_status = (
                    f"L fresh={[item.marker.marker_id for item in left_marker_update.fresh]} "
                    f"held={[item.marker.marker_id for item in left_marker_update.held]} | "
                    f"R fresh={[item.marker.marker_id for item in right_marker_update.fresh]} "
                    f"held={[item.marker.marker_id for item in right_marker_update.held]}"
                )
                cv2.putText(
                    left_vis, marker_status, (10, 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                    (180, 180, 180), 1, cv2.LINE_AA,
                )
                y = 68
                for hand in ("left", "right"):
                    line = format_tracker_line(hand, outputs[hand])
                    cv2.putText(left_vis, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                TRACKER_LABEL_COLORS[hand], 1, cv2.LINE_AA)
                    y += 22
                    if args.debug_imu:
                        debug_line = format_imu_debug_line(
                            hand, outputs[hand].imu_debug
                        )
                        cv2.putText(
                            left_vis,
                            debug_line,
                            (10, y),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            TRACKER_LABEL_COLORS[hand],
                            1,
                            cv2.LINE_AA,
                        )
                        y += 22
                cv2.imshow(
                    "Dual-Hand Multi-Marker Stereo Pose",
                    np.hstack([
                        _resize_for_display(left_vis, args.display_scale),
                        _resize_for_display(right_vis, args.display_scale),
                    ]),
                )
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
    finally:
        cleanup_error = cleanup_runtime_resources(
            reader,
            imu_subscribers,
            jsonl_handle,
            display_enabled=not args.no_display,
        )
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise cleanup_error
    return 0

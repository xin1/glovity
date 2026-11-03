"""Library session API for dual-hand tracker poses."""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Dict, Literal, Optional

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
from telex.tracker.geometry import (
    mid_frame_from_left_mm,
    pose_in_frame,
    validate_rigid_transform,
)
from telex.tracker.imu.runtime import start_dual_imu_runtime, stop_imu_subscribers
from telex.tracker.resources import default_calibration_path
from telex.tracker.types import (
    PnPSelectionConfig,
    TrackerOutlierState,
    TrackerOutput,
    TrackerVisionMemory,
)
from telex.tracker.vision.pipeline import estimate_tracker_vision, make_detector
from telex.tracker.vision.pose import MarkerPose
from telex.tracker.vision.rig import build_hand_rigs
from telex.tracker.vision.tracking import (
    MultiMarkerTracker,
    MultiMarkerUpdate,
    fresh_corner_motion_px,
)

PoseFrame = Literal["left", "mid"]


@dataclass(frozen=True)
class TrackerFrameResult:
    """一帧双手 tracker 结果。

    ``left`` / ``right`` 为 tracker 物体系相对所选参考系的位姿（``rvec`` + ``tvec`` mm）。
    ``frame`` 为 ``\"left\"``（左目）或 ``\"mid\"``（基线中点对称系：X 沿基线，Z 朝上）。
    ``left_image`` / ``right_image`` 为当帧双目 BGR 图像，便于外部可视化。
    """

    timestamp_s: float
    frame: PoseFrame
    left: Optional[MarkerPose]
    right: Optional[MarkerPose]
    outputs: Dict[str, TrackerOutput]
    left_image: np.ndarray
    right_image: np.ndarray


def _default_runtime_args(
    *,
    camera,
    calibration: str,
    marker_size_mm: float,
    enable_imu: bool,
    imu_port: Optional[str],
    imu_baud: Optional[int],
    auto_exposure: bool,
    exposure: float,
    gain: float,
    mid_pitch_up_deg: float,
) -> argparse.Namespace:
    """复用 CLI 默认检测/融合参数，构造 Session 所需 Namespace。"""
    from telex.tracker.cli import build_arg_parser

    parser = build_arg_parser()
    argv = [
        "--camera",
        str(camera),
        "--calibration",
        calibration,
        "--marker-size-mm",
        str(marker_size_mm),
        "--exposure",
        str(exposure),
        "--gain",
        str(gain),
        "--mid-pitch-up-deg",
        str(mid_pitch_up_deg),
        "--no-display",
    ]
    if auto_exposure:
        argv.append("--auto-exposure")
    if not enable_imu:
        argv.append("--no-imu")
    if imu_port is not None:
        argv.extend(["--imu-port", str(imu_port)])
    if imu_baud is not None:
        argv.extend(["--imu-baud", str(imu_baud)])
    return parser.parse_args(argv)


class TrackerSession:
    """可 import 的跟踪会话：读相机 → 估计双手位姿。

    示例::

        from telex.tracker import TrackerSession

        with TrackerSession(camera=2) as session:
            result = session.read(frame=\"mid\")
            if result.left is not None:
                print(result.left.tvec.ravel())  # mm，相对中点系
    """

    def __init__(
        self,
        *,
        camera=2,
        calibration: Optional[str] = None,
        marker_size_mm: float = DEFAULT_MARKER_SIZE_MM,
        enable_imu: bool = True,
        imu_port: Optional[str] = None,
        imu_baud: Optional[int] = None,
        imu_subscribers: Optional[Dict[str, object]] = None,
        imu_fusions: Optional[Dict[str, object]] = None,
        auto_exposure: bool = False,
        exposure: float = 85.0,
        gain: float = 100.0,
        mid_pitch_up_deg: float = DEFAULT_MID_PITCH_UP_DEG,
        pose_outlier: bool = True,
        pose_smooth: bool = True,
    ) -> None:
        calib_path = calibration or default_calibration_path()
        # 外部已注入 subscribers 时不再自行开串口
        own_imu = enable_imu and imu_subscribers is None
        self._args = _default_runtime_args(
            camera=camera,
            calibration=calib_path,
            marker_size_mm=marker_size_mm,
            enable_imu=own_imu,
            imu_port=imu_port,
            imu_baud=imu_baud,
            auto_exposure=auto_exposure,
            exposure=exposure,
            gain=gain,
            mid_pitch_up_deg=mid_pitch_up_deg,
        )
        self._external_imu = imu_subscribers is not None
        self._injected_subscribers = dict(imu_subscribers or {})
        self._injected_fusions = dict(imu_fusions or {})
        self._pose_outlier = bool(pose_outlier)
        self._pose_smooth = bool(pose_smooth)

        self._rigs = build_hand_rigs()
        self._target_ids = set(DEFAULT_TARGET_IDS)
        self._calibration: StereoCalibration = load_fisheye_stereo_calibration(
            self._args.calibration
        )
        right_to_left = validate_rigid_transform(
            self._calibration.right_to_left_mm, "right_to_left_mm"
        )
        self._mid_from_left = mid_frame_from_left_mm(
            right_to_left,
            pitch_up_deg=self._args.mid_pitch_up_deg,
        )

        self._detector = make_detector(self._args)
        self._pnp_config = PnPSelectionConfig(
            pair_max_rotation_deg=self._args.pnp_pair_max_rotation_deg,
            pair_max_translation_mm=self._args.pnp_pair_max_translation_mm,
            marker_max_reprojection_px=self._args.pnp_marker_max_reprojection_px,
            prior_rotation_weight=self._args.pnp_prior_rotation_weight,
            prior_translation_weight=self._args.pnp_prior_translation_weight,
            pair_rotation_weight=self._args.pnp_pair_rotation_weight,
            pair_translation_weight=self._args.pnp_pair_translation_weight,
            stereo_max_rotation_deg=self._args.stereo_max_rotation_deg,
            stereo_max_translation_mm=self._args.stereo_max_translation_mm,
            marker_front_axis_sign=self._args.marker_front_axis_sign,
            marker_front_min_cos=self._args.marker_front_min_cos,
        )
        self._outlier_filters = build_tracker_outlier_filters(
            self._rigs,
            max_rotation_deg=self._args.pose_outlier_max_rotation_deg,
            max_translation_mm=self._args.pose_outlier_max_translation_mm,
            max_hold_frames=self._args.pose_outlier_max_hold_frames,
        )
        self._outlier_states = {
            hand: TrackerOutlierState() for hand in self._rigs
        }
        self._vision_memories = {
            hand: TrackerVisionMemory() for hand in self._rigs
        }
        self._pose_smoothers = {
            hand: TeleopPoseSmoother(
                TeleopPoseSmoothConfig(
                    enabled=True,
                    translation=OneEuroParams(
                        min_cutoff_hz=self._args.pose_smooth_min_cutoff,
                        beta=self._args.pose_smooth_beta,
                        d_cutoff_hz=1.0,
                    ),
                    rotation_min_cutoff_hz=self._args.pose_smooth_rot_min_cutoff,
                    rotation_beta=self._args.pose_smooth_rot_beta,
                    image_motion_threshold_px=(
                        self._args.pose_smooth_image_motion_threshold_px
                    ),
                    image_motion_cutoff_gain_hz_per_px=(
                        self._args.pose_smooth_image_motion_gain_hz_per_px
                    ),
                )
            )
            for hand in self._rigs
        }
        tracker_kwargs = dict(
            hold_frames=self._args.marker_hold_frames,
            smooth_window=self._args.marker_smooth_window,
            max_corner_shift_px=self._args.marker_max_shift_px,
            min_enter_area_px=self._args.marker_min_enter_area_px,
            min_keep_area_px=self._args.marker_min_keep_area_px,
            enter_frames=self._args.marker_enter_frames,
            adaptive_smooth_shift_px=self._args.marker_smooth_static_shift_px,
        )
        self._left_tracker = MultiMarkerTracker(**tracker_kwargs)
        self._right_tracker = MultiMarkerTracker(**tracker_kwargs)

        self._reader: Optional[DualCameraReader] = None
        self._imu_subscribers = {}
        self._imu_fusions = {}
        self._last_frame_id = -1
        self._started = False
        self._last_left_marker_update: Optional[MultiMarkerUpdate] = None
        self._last_right_marker_update: Optional[MultiMarkerUpdate] = None

    @property
    def calibration(self) -> StereoCalibration:
        return self._calibration

    @property
    def mid_from_left_mm(self) -> np.ndarray:
        """``T_mid←left``（mm），可把左目系位姿变到中点对称系。"""
        return self._mid_from_left.copy()

    @property
    def marker_size_mm(self) -> float:
        return float(self._args.marker_size_mm)

    @property
    def last_left_marker_update(self) -> Optional[MultiMarkerUpdate]:
        """上一帧左目 MultiMarkerUpdate（可视化用）；尚未 read 时为 None。"""
        return self._last_left_marker_update

    @property
    def last_right_marker_update(self) -> Optional[MultiMarkerUpdate]:
        """上一帧右目 MultiMarkerUpdate（可视化用）；尚未 read 时为 None。"""
        return self._last_right_marker_update

    def start(self) -> "TrackerSession":
        if self._started:
            return self
        if self._external_imu:
            self._imu_subscribers = dict(self._injected_subscribers)
            self._imu_fusions = dict(self._injected_fusions)
        elif not self._args.no_imu:
            self._imu_subscribers, self._imu_fusions = start_dual_imu_runtime(
                self._args
            )
        self._reader = DualCameraReader(
            index=self._args.camera,
            auto_exposure=self._args.auto_exposure,
            exposure=self._args.exposure,
            gain=self._args.gain,
        )
        self._started = True
        return self

    def close(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None
        # 外部注入的 hub/subscribers 由 TeleXSession 负责 stop
        if not self._external_imu:
            stop_imu_subscribers(self._imu_subscribers)
        self._imu_subscribers = {}
        self._imu_fusions = {}
        self._started = False

    def __enter__(self) -> "TrackerSession":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read(
        self,
        *,
        frame: PoseFrame = "mid",
        timeout: float = 3.0,
    ) -> TrackerFrameResult:
        """读取下一相机帧并返回左右 tracker 位姿。

        Parameters
        ----------
        frame:
            ``\"left\"`` — 左目坐标系；
            ``\"mid\"`` — 基线中点对称系（X 沿基线左→右，Z 朝上）。
        """
        if not self._started or self._reader is None:
            raise RuntimeError("TrackerSession 未 start()；请使用 with 或先调用 start()")
        if frame not in ("left", "mid"):
            raise ValueError("frame 必须是 'left' 或 'mid'")

        left_img, right_img, self._last_frame_id, frame_ts = (
            self._reader.wait_next_frame(
                last_frame_id=self._last_frame_id, timeout=timeout
            )
        )
        left_update = self._left_tracker.update(
            self._detector.detect_targets(left_img, self._target_ids)
        )
        right_update = self._right_tracker.update(
            self._detector.detect_targets(right_img, self._target_ids)
        )
        self._last_left_marker_update = left_update
        self._last_right_marker_update = right_update
        left_markers = left_update.fresh_markers
        right_markers = right_update.fresh_markers

        vision_by_hand = {
            hand: estimate_tracker_vision(
                left_markers,
                right_markers,
                rig,
                self._args.marker_size_mm,
                self._calibration,
                memory=self._vision_memories[hand],
                config=self._pnp_config,
                stereo_hysteresis_frames=self._args.stereo_hysteresis_frames,
            )
            for hand, rig in self._rigs.items()
        }
        if self._pose_outlier and not self._args.pose_outlier_disable:
            for hand, vision in list(vision_by_hand.items()):
                (
                    vision_by_hand[hand],
                    self._outlier_states[hand],
                ) = update_tracker_outlier_vision(
                    vision,
                    self._outlier_filters[hand],
                    self._outlier_states[hand],
                )

        now_s = time.monotonic()
        corner_motion_px_by_hand = {
            hand: max(
                fresh_corner_motion_px(
                    left_update, rig.object_to_marker
                ),
                fresh_corner_motion_px(
                    right_update, rig.object_to_marker
                ),
            )
            for hand, rig in self._rigs.items()
        }
        outputs = build_tracker_outputs(
            vision_by_hand,
            imu_fusions=self._imu_fusions,
            imu_subscribers=self._imu_subscribers,
            debug_imu=False,
            now_s=now_s,
        )
        if self._pose_smooth and not self._args.pose_smooth_disable:
            outputs = apply_output_pose_smoothing(
                outputs,
                self._pose_smoothers,
                now_s=now_s,
                corner_motion_px_by_hand=corner_motion_px_by_hand,
            )

        left_pose = outputs["left"].pose
        right_pose = outputs["right"].pose
        if frame == "mid":
            left_pose = pose_in_frame(left_pose, self._mid_from_left)
            right_pose = pose_in_frame(right_pose, self._mid_from_left)

        return TrackerFrameResult(
            timestamp_s=float(frame_ts),
            frame=frame,
            left=left_pose,
            right=right_pose,
            outputs=outputs,
            left_image=left_img,
            right_image=right_img,
        )

"""Unified TeleX session: tracker 6D + glove IMU/joints."""
from __future__ import annotations

import sys
from typing import Dict, List, Optional

from telex.glove.fk import compute_skeleton_joints
from telex.hub.parse import DEFAULT_BAUD, DEFAULT_PORT
from telex.hub.runtime import (
    SharedImuHub,
    start_hand_subscribers_from_hub,
    start_shared_hub,
)
from telex.tracker.imu.runtime import make_imu_fusion, stop_imu_subscribers
from telex.tracker.constants import DEFAULT_MID_PITCH_UP_DEG
from telex.tracker.resources import default_calibration_path
from telex.tracker.session import TrackerSession
from telex.types import (
    FrameResult,
    HandSide,
    HandState,
    PoseFrame,
    TeleXStrictError,
)


def _normalize_side(side: HandSide | str) -> HandSide:
    key = str(side).strip().lower()
    if key not in ("both", "left", "right"):
        raise ValueError("side 必须是 'both' | 'left' | 'right'")
    return key  # type: ignore[return-value]


def _enabled_hands(side: HandSide) -> List[str]:
    if side == "both":
        return ["left", "right"]
    return [side]


def _has_glove_data(state: HandState) -> bool:
    return state.joints is not None or state.imus is not None


def _build_hand_state(
    *,
    pose,
    glove_frame,
    hand: str,
    battery_percent: Optional[int] = None,
    glove_calib_path: Optional[str] = None,
) -> HandState:
    imus = None
    joints = None
    if glove_frame is not None:
        imus = list(glove_frame.imu_data)
        if all(imu.valid for imu in imus):
            joints = compute_skeleton_joints(
                glove_frame, side=hand, calib_path=glove_calib_path
            )
    available = pose is not None or imus is not None or joints is not None
    return HandState(
        pose=pose,
        imus=imus,
        joints=joints,
        available=available,
        battery_percent=(
            glove_frame.battery_percent if glove_frame is not None else battery_percent
        ),
        frame_seq=glove_frame.frame_seq if glove_frame is not None else None,
        glove_timestamp_us=glove_frame.timestamp_us if glove_frame is not None else None,
        glove_host_receive_s=glove_frame.host_receive_s if glove_frame is not None else None,
        recording=glove_frame.recording if glove_frame is not None else None,
        imu_fail_mask=glove_frame.imu_fail_mask if glove_frame is not None else None,
        wrist_gyro_valid=glove_frame.wrist_gyro_valid if glove_frame is not None else None,
        wrist_gyro_dps=glove_frame.wrist_gyro_dps if glove_frame is not None else None,
        protocol=glove_frame.protocol if glove_frame is not None else None,
        protocol_version=glove_frame.protocol_version if glove_frame is not None else None,
        has_device_timestamp=glove_frame.has_device_timestamp if glove_frame is not None else None,
        has_recording_flag=glove_frame.has_recording_flag if glove_frame is not None else None,
        has_fail_mask=glove_frame.has_fail_mask if glove_frame is not None else None,
    )


class TeleXSession:
    """统一运行时：一次 ``read()`` 返回 tracker 6D + 手指 IMU/joints。

    示例::

        from telex import TeleXSession

        with TeleXSession(camera=2, side="both") as telex:
            result = telex.read(frame="mid")
            if result.left and result.left.pose is not None:
                print(result.left.pose.tvec)
            if result.left and result.left.joints is not None:
                print(result.left.joints[4])
    """

    def __init__(
        self,
        *,
        camera=0,
        imu_port: Optional[str] = None,
        imu_baud: Optional[int] = None,
        side: HandSide | str = "both",
        enable_imu_fusion: bool = True,
        calibration: Optional[str] = None,
        require_glove: bool = False,
        auto_exposure: bool = False,
        exposure: float = 85.0,
        gain: float = 100.0,
        mid_pitch_up_deg: float = DEFAULT_MID_PITCH_UP_DEG,
        pose_outlier: bool = True,
        pose_smooth: bool = True,
        hub_startup_timeout_s: float = 2.0,
        glove_calib_left: Optional[str] = None,
        glove_calib_right: Optional[str] = None,
    ) -> None:
        self._side = _normalize_side(side)
        self._hands = _enabled_hands(self._side)
        self._require_glove = bool(require_glove)
        self._enable_imu_fusion = bool(enable_imu_fusion)
        self._imu_port = imu_port or DEFAULT_PORT
        self._imu_baud = int(imu_baud if imu_baud is not None else DEFAULT_BAUD)
        self._calibration = calibration or default_calibration_path()
        self._glove_calib = {
            "left": glove_calib_left,
            "right": glove_calib_right,
        }

        self._hub: Optional[SharedImuHub] = None
        self._imu_subscribers: Dict[str, object] = {}
        self._imu_fusions: Dict[str, object] = {}
        self._owns_subscribers = False
        self._tracker: Optional[TrackerSession] = None
        self._started = False

        self._camera = camera
        self._auto_exposure = auto_exposure
        self._exposure = exposure
        self._gain = gain
        self._mid_pitch_up_deg = float(mid_pitch_up_deg)
        self._pose_outlier = pose_outlier
        self._pose_smooth = pose_smooth
        self._hub_startup_timeout_s = float(hub_startup_timeout_s)

    @property
    def side(self) -> HandSide:
        return self._side

    @property
    def hub(self) -> Optional[SharedImuHub]:
        return self._hub

    @property
    def tracker(self) -> Optional[TrackerSession]:
        """底层 TrackerSession（可视化/标定用）；未 start 时为 None。"""
        return self._tracker

    def start(self) -> "TeleXSession":
        if self._started:
            return self

        hub: Optional[SharedImuHub] = None
        # 默认尝试 Hub：手套数据依赖它；失败则无手套 / 纯视觉
        hub = start_shared_hub(
            port=self._imu_port,
            baud=self._imu_baud,
            startup_timeout_s=self._hub_startup_timeout_s,
        )
        if hub is None and self._require_glove:
            raise RuntimeError(
                f"require_glove=True 但 Hub 未能在 "
                f"{self._imu_port}@{self._imu_baud} 就绪"
            )

        subscribers: Dict[str, object] = {}
        fusions: Dict[str, object] = {}
        if hub is not None and self._enable_imu_fusion:
            # 构造与 TrackerSession 默认 CLI 一致的 fusion 参数
            from telex.tracker.cli import build_arg_parser

            args = build_arg_parser().parse_args(
                [
                    "--camera",
                    str(self._camera),
                    "--calibration",
                    self._calibration,
                    "--imu-port",
                    self._imu_port,
                    "--imu-baud",
                    str(self._imu_baud),
                    "--no-display",
                ]
            )
            all_subs = start_hand_subscribers_from_hub(hub, hands=("left", "right"))
            for hand in ("left", "right"):
                if hand not in self._hands:
                    # 仍保留 subscriber 引用计数；双手同口，不 start 未启用侧也可
                    continue
                sub = all_subs[hand]
                try:
                    fusion = make_imu_fusion(
                        args,
                        getattr(args, f"imu_{hand}_lever_arm"),
                        hand=hand,
                    )
                    sub.start()
                    if not sub.wait_ready(timeout_s=self._hub_startup_timeout_s):
                        raise RuntimeError(sub.error or "subscriber not ready")
                except Exception as exc:
                    print(
                        f"[WARN][IMU:{hand}] 融合订阅失败: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    try:
                        sub.stop()
                    except Exception:
                        pass
                    continue
                subscribers[hand] = sub
                fusions[hand] = fusion
            # 未启用侧也需要 acquire/release 平衡：停掉未使用的
            for hand, sub in all_subs.items():
                if hand not in subscribers:
                    # 不 start，无需 stop
                    pass
            self._owns_subscribers = True

        self._hub = hub
        self._imu_subscribers = subscribers
        self._imu_fusions = fusions

        self._tracker = TrackerSession(
            camera=self._camera,
            calibration=self._calibration,
            enable_imu=bool(subscribers),
            imu_subscribers=subscribers if subscribers else None,
            imu_fusions=fusions if fusions else None,
            auto_exposure=self._auto_exposure,
            exposure=self._exposure,
            gain=self._gain,
            mid_pitch_up_deg=self._mid_pitch_up_deg,
            pose_outlier=self._pose_outlier,
            pose_smooth=self._pose_smooth,
        )
        self._tracker.start()
        self._started = True
        return self

    def close(self) -> None:
        if self._tracker is not None:
            try:
                self._tracker.close()
            except Exception:
                pass
            self._tracker = None
        if self._owns_subscribers:
            stop_imu_subscribers(self._imu_subscribers)
        self._imu_subscribers = {}
        self._imu_fusions = {}
        self._owns_subscribers = False
        if self._hub is not None:
            try:
                self._hub.stop()
            except Exception:
                pass
            self._hub = None
        self._started = False

    def __enter__(self) -> "TeleXSession":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read(
        self,
        *,
        frame: PoseFrame = "mid",
        strict: bool = False,
        timeout: float = 3.0,
    ) -> FrameResult:
        if not self._started or self._tracker is None:
            raise RuntimeError("TeleXSession 未 start()；请使用 with 或先调用 start()")
        if frame not in ("left", "mid"):
            raise ValueError("frame 必须是 'left' 或 'mid'")

        tracker_result = self._tracker.read(frame=frame, timeout=timeout)

        def hand_result(hand: str) -> HandState:
            pose = tracker_result.left if hand == "left" else tracker_result.right
            glove = None
            battery = None
            if self._hub is not None and self._hub.is_online(hand):
                glove = self._hub.latest_glove(hand)
                wrist = self._hub.latest_hand(hand)
                if wrist is not None:
                    battery = int(wrist.bat_percent)
            return _build_hand_state(
                pose=pose,
                glove_frame=glove,
                hand=hand,
                battery_percent=battery,
                glove_calib_path=self._glove_calib.get(hand),
            )

        left_state = hand_result("left") if "left" in self._hands else None
        right_state = hand_result("right") if "right" in self._hands else None

        if strict:
            missing: List[str] = []
            for hand, state in (("left", left_state), ("right", right_state)):
                if state is None:
                    continue
                if state.pose is None or not _has_glove_data(state):
                    missing.append(hand)
            if missing:
                raise TeleXStrictError(
                    "strict=True 缺少必需数据: " + ", ".join(missing)
                )

        return FrameResult(
            timestamp_s=float(tracker_result.timestamp_s),
            frame=frame,
            left=left_state,
            right=right_state,
            left_image=tracker_result.left_image,
            right_image=tracker_result.right_image,
        )

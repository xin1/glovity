"""遥操作向 SE(3) 输出平滑：One Euro + 自适应 SLERP。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional

import numpy as np

from telex.tracker.imu.math import orthonormalize, rotmat_to_rvec, rvec_to_rotmat
from telex.tracker.types import TrackerOutput
from telex.tracker.vision.pose import MarkerPose


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass
class OneEuroParams:
    """Casiez One Euro：fmin 越低越稳，beta 越大跟手越快。"""

    min_cutoff_hz: float = 1.0
    beta: float = 0.015
    d_cutoff_hz: float = 1.0


class _OneEuro1D:
    def __init__(self, params: OneEuroParams) -> None:
        self.params = params
        self._x_prev: Optional[float] = None
        self._dx_prev: float = 0.0

    @staticmethod
    def _alpha(cutoff_hz: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(1e-6, float(cutoff_hz)))
        return 1.0 / (1.0 + tau / max(1e-6, float(dt)))

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = 0.0

    def filter(self, x: float, dt: float, *, cutoff_boost_hz: float = 0.0) -> float:
        if self._x_prev is None or dt <= 0.0:
            self._x_prev = float(x)
            self._dx_prev = 0.0
            return float(x)
        dx = (float(x) - self._x_prev) / dt
        a_d = self._alpha(self.params.d_cutoff_hz, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev
        cutoff = (
            self.params.min_cutoff_hz
            + self.params.beta * abs(dx_hat)
            + max(0.0, float(cutoff_boost_hz))
        )
        a = self._alpha(cutoff, dt)
        x_hat = a * float(x) + (1.0 - a) * self._x_prev
        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat


def _slerp_rotmat(r0: np.ndarray, r1: np.ndarray, alpha: float) -> np.ndarray:
    alpha = _clamp(alpha, 0.0, 1.0)
    r0 = orthonormalize(r0)
    r1 = orthonormalize(r1)
    relative = orthonormalize(r1 @ r0.T)
    rvec = rotmat_to_rvec(relative).reshape(3)
    angle = float(np.linalg.norm(rvec))
    if angle < 1e-12 or alpha <= 0.0:
        return r0.copy()
    if alpha >= 1.0:
        return r1.copy()
    scaled = rvec * alpha
    return orthonormalize(rvec_to_rotmat(scaled) @ r0)


@dataclass
class TeleopPoseSmoothConfig:
    enabled: bool = True
    translation: OneEuroParams = None  # type: ignore[assignment]
    rotation_min_cutoff_hz: float = 1.8
    rotation_beta: float = 0.15
    rotation_d_cutoff_hz: float = 1.0
    image_motion_threshold_px: float = 10.0
    image_motion_cutoff_gain_hz_per_px: float = 8.0
    max_dt_s: float = 0.25
    """超过该间隔视为轨迹断裂，重置滤波器。"""

    def __post_init__(self) -> None:
        if self.translation is None:
            self.translation = OneEuroParams(
                min_cutoff_hz=1.0, beta=0.015, d_cutoff_hz=1.0
            )
        if self.image_motion_threshold_px < 0.0:
            raise ValueError("image_motion_threshold_px must be non-negative")
        if self.image_motion_cutoff_gain_hz_per_px < 0.0:
            raise ValueError(
                "image_motion_cutoff_gain_hz_per_px must be non-negative"
            )


class TeleopPoseSmoother:
    """对单手 Tracker 输出做平移 OneEuro + 旋转自适应 SLERP。"""

    def __init__(self, config: Optional[TeleopPoseSmoothConfig] = None) -> None:
        self.config = config or TeleopPoseSmoothConfig()
        self._tx = _OneEuro1D(self.config.translation)
        self._ty = _OneEuro1D(self.config.translation)
        self._tz = _OneEuro1D(self.config.translation)
        self._rot_speed = _OneEuro1D(
            OneEuroParams(
                min_cutoff_hz=self.config.rotation_d_cutoff_hz,
                beta=0.0,
                d_cutoff_hz=self.config.rotation_d_cutoff_hz,
            )
        )
        self._r_prev: Optional[np.ndarray] = None
        self._t_prev_s: Optional[float] = None
        self._last_error_px: float = 0.0

    def reset(self) -> None:
        self._tx.reset()
        self._ty.reset()
        self._tz.reset()
        self._rot_speed.reset()
        self._r_prev = None
        self._t_prev_s = None

    def update(
        self,
        pose: Optional[MarkerPose],
        *,
        now_s: float,
        image_motion_px: float = 0.0,
    ) -> Optional[MarkerPose]:
        if not self.config.enabled:
            return pose
        if pose is None:
            # A missed marker frame is common at high camera rates. Resetting
            # here makes the first recovered PnP pose bypass filtering and
            # appear as a visible jump. Keep the state across short gaps while
            # still returning no pose, so callers never receive a stale hold.
            if (
                self._t_prev_s is not None
                and float(now_s) - float(self._t_prev_s) > self.config.max_dt_s
            ):
                self.reset()
            return None

        dt = 1.0 / 30.0
        if self._t_prev_s is not None:
            dt = float(now_s) - float(self._t_prev_s)
            if dt <= 0.0 or dt > self.config.max_dt_s:
                self.reset()
                dt = 1.0 / 30.0
        self._t_prev_s = float(now_s)

        t = np.asarray(pose.tvec, dtype=np.float64).reshape(3)
        r = rvec_to_rotmat(pose.rvec)
        self._last_error_px = float(pose.reprojection_error_px)
        image_motion_px = float(image_motion_px)
        if not np.isfinite(image_motion_px):
            image_motion_px = 0.0
        motion_boost_hz = self.config.image_motion_cutoff_gain_hz_per_px * max(
            0.0, image_motion_px - self.config.image_motion_threshold_px
        )

        t_hat = np.array(
            [
                self._tx.filter(
                    float(t[0]), dt, cutoff_boost_hz=motion_boost_hz
                ),
                self._ty.filter(
                    float(t[1]), dt, cutoff_boost_hz=motion_boost_hz
                ),
                self._tz.filter(
                    float(t[2]), dt, cutoff_boost_hz=motion_boost_hz
                ),
            ],
            dtype=np.float64,
        )

        if self._r_prev is None:
            self._r_prev = r.copy()
            return MarkerPose(
                rotmat_to_rvec(r),
                t_hat.reshape(3, 1),
                self._last_error_px,
            )

        relative = orthonormalize(r @ self._r_prev.T)
        angle = float(
            math.acos(
                max(
                    -1.0,
                    min(1.0, (float(np.trace(relative)) - 1.0) * 0.5),
                )
            )
        )
        speed = angle / max(dt, 1e-6)
        speed_hat = self._rot_speed.filter(speed, dt)
        # 映射到 SLERP alpha：静止时更粘，快速转动时更跟手
        cutoff = (
            self.config.rotation_min_cutoff_hz
            + self.config.rotation_beta * abs(speed_hat)
            + motion_boost_hz
        )
        tau = 1.0 / (2.0 * math.pi * max(1e-6, cutoff))
        alpha = 1.0 / (1.0 + tau / max(1e-6, dt))
        r_hat = _slerp_rotmat(self._r_prev, r, alpha)
        self._r_prev = r_hat.copy()

        return MarkerPose(
            rotmat_to_rvec(r_hat),
            t_hat.reshape(3, 1),
            self._last_error_px,
        )

def apply_output_pose_smoothing(
    outputs: Mapping[str, TrackerOutput],
    smoothers: Mapping[str, object],
    *,
    now_s: float,
    corner_motion_px_by_hand: Optional[Mapping[str, float]] = None,
) -> Dict[str, TrackerOutput]:
    """在最终输出上做 SE(3) 平滑；不改动视觉/IMU 内部状态。"""
    smoothed: Dict[str, TrackerOutput] = {}
    motions = corner_motion_px_by_hand or {}
    for hand, output in outputs.items():
        smoother = smoothers.get(hand)
        if smoother is None:
            smoothed[hand] = output
            continue
        pose = smoother.update(
            output.pose,
            now_s=now_s,
            image_motion_px=float(motions.get(hand, 0.0)),
        )
        if pose is None and output.pose is None:
            smoothed[hand] = output
            continue
        if pose is None:
            smoothed[hand] = TrackerOutput(
                pose=None,
                source="none",
                vision_source="none",
                visible_ids=(),
                mode="none",
                imu_debug=output.imu_debug,
            )
            continue
        smoothed[hand] = TrackerOutput(
            pose=pose,
            source=output.source if output.pose is not None else "none",
            vision_source=output.vision_source,
            visible_ids=output.visible_ids,
            mode=output.mode if output.pose is not None else "none",
            imu_debug=output.imu_debug,
            outlier_held=output.outlier_held,
            outlier_hold_frames=output.outlier_hold_frames,
        )
    return smoothed

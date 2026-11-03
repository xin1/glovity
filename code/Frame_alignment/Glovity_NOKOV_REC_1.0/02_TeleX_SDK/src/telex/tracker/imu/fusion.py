"""Vision + IMU loosely-coupled fusion (current production pipeline).

Formerly split as fusion.py (V2.1 base) + fusion_v3.py (batch SVD / lever arm /
gyro prediction). Runtime only used the V3 path; this module is the single
implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from telex.tracker.imu.math import (
    orthonormalize,
    quat_wxyz_to_rotmat,
    rotation_angle_deg,
    rotmat_to_rvec,
    rvec_to_rotmat,
)
from telex.tracker.imu.messages import ImuFrame, fresh_imu_frame
from telex.tracker.vision.pose import MarkerPose


@dataclass
class ImuFusionConfig:
    max_age_s: float = 0.35
    imu_hold_max_age_s: float = 0.30
    learn_alpha: float = 0.05
    min_learn_frames: int = 5
    orientation_warn_deg: float = 25.0
    # Aggressive IMU weighting (near-field visual decay)
    near_distance_m: float = 0.15
    far_distance_m: float = 0.35
    low_error_px: float = 1.0
    high_error_px: float = 4.0
    max_imu_weight: float = 0.85
    learn_min_visual_confidence: float = 0.65
    base_to_imu_rotation: np.ndarray = field(default_factory=lambda: np.eye(3, dtype=np.float64))

    auto_learn_extrinsic: bool = True
    """True = EMA online learning of R_camera_world; False = freeze after batch."""

    camera_world_rotation: Optional[np.ndarray] = None
    """Known R_camera_world (3×3). None → calibrate from frames."""

    enable_translation_fusion: bool = True
    max_translation_blend_m: float = 0.05
    """Max per-frame IMU translation correction (metres)."""

    batch_calibration_samples: int = 10
    """Collect N high-quality frames, then SVD-average R_camera_world."""

    lever_arm_mm: np.ndarray = field(
        default_factory=lambda: np.zeros(3, dtype=np.float64),
    )
    """IMU center → marker center offset in IMU body frame (mm)."""

    enable_gyro_prediction: bool = True
    gyro_max_dt_s: float = 0.05
    """Max gyro extrapolation window (s)."""

    active_marker_count: int = 2
    single_marker_confidence_scale: float = 0.6


@dataclass(frozen=True)
class FusionResult:
    pose: Optional[MarkerPose]
    source: str
    imu: dict


class ImuOrientationFusion:
    """Visual + IMU loosely-coupled fusion (rotation + translation)."""

    def __init__(self, config: Optional[ImuFusionConfig] = None) -> None:
        self.config = config or ImuFusionConfig()
        self._camera_world: Optional[np.ndarray] = (
            orthonormalize(self.config.camera_world_rotation)
            if self.config.camera_world_rotation is not None
            else None
        )
        self._learn_count = 0
        self._last_visual_pose: Optional[MarkerPose] = None
        self._last_visual_s: Optional[float] = None
        self._last_imu_reference: Optional[np.ndarray] = None
        self._last_fused_tvec: Optional[np.ndarray] = None
        self._prev_gyro_dps: Optional[np.ndarray] = None
        self._prev_imu_time_s: Optional[float] = None
        self._calibration_samples: list[np.ndarray] = []

    def set_active_marker_count(self, count: int) -> None:
        """Update visible marker count before ``update()`` each frame."""
        self.config.active_marker_count = max(0, int(count))

    @staticmethod
    def _safe_quat_to_rotmat(imu_frame: ImuFrame) -> Optional[np.ndarray]:
        quat = np.asarray(imu_frame.quat_wxyz, dtype=np.float64).reshape(4)
        if float(np.linalg.norm(quat)) < 1e-12:
            return None
        return quat_wxyz_to_rotmat(quat)

    def update(
        self,
        visual_pose: Optional[MarkerPose],
        imu_frame: Optional[ImuFrame],
        *,
        now_s: float,
    ) -> FusionResult:
        imu_frame = fresh_imu_frame(
            imu_frame,
            now_monotonic_s=now_s,
            max_age_s=self.config.max_age_s,
        )
        if imu_frame is None or not self.config.enable_gyro_prediction:
            imu_rotation = self._safe_quat_to_rotmat(imu_frame) if imu_frame is not None else None
            if visual_pose is not None:
                return self._handle_visual_present(visual_pose, imu_rotation, imu_frame, now_s)
            return self._handle_visual_absent(imu_rotation, imu_frame, now_s)

        imu_rotation = self._safe_quat_to_rotmat(imu_frame)
        gyro_dps = np.asarray(imu_frame.gyro_dps, dtype=np.float64).reshape(3)
        gyro_time_s = float(imu_frame.received_monotonic_s)

        if imu_rotation is not None:
            dt = now_s - gyro_time_s
            if 0.0 < dt <= self.config.gyro_max_dt_s and np.linalg.norm(gyro_dps) > 0.01:
                omega_rad = gyro_dps * (np.pi / 180.0)
                angle = float(np.linalg.norm(omega_rad)) * dt
                if angle > 1e-9:
                    axis = omega_rad / np.linalg.norm(omega_rad)
                    K = np.array(
                        [
                            [0, -axis[2], axis[1]],
                            [axis[2], 0, -axis[0]],
                            [-axis[1], axis[0], 0],
                        ]
                    )
                    delta_R = np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)
                    imu_rotation = orthonormalize(delta_R @ imu_rotation)

        self._prev_gyro_dps = gyro_dps.copy()
        self._prev_imu_time_s = gyro_time_s

        if visual_pose is not None:
            return self._handle_visual_present(visual_pose, imu_rotation, imu_frame, now_s)
        return self._handle_visual_absent(imu_rotation, imu_frame, now_s)

    def _handle_visual_present(
        self,
        visual_pose: MarkerPose,
        imu_rotation: Optional[np.ndarray],
        imu_frame: Optional[ImuFrame],
        now_s: float,
    ) -> FusionResult:
        visual_rotation = rvec_to_rotmat(visual_pose.rvec)
        visual_tvec = np.asarray(visual_pose.tvec, dtype=np.float64).reshape(3, 1)
        visual_confidence = self._visual_confidence(visual_pose)

        if imu_rotation is not None and visual_confidence >= self.config.learn_min_visual_confidence:
            self._calibrate_camera_world(visual_rotation, imu_rotation)

        reference_rotation = self._reference_rotation(imu_rotation)
        orientation_error = (
            rotation_angle_deg(visual_rotation, reference_rotation)
            if reference_rotation is not None and self._reference_available()
            else None
        )

        imu_weight = self._imu_weight(visual_pose, reference_rotation)

        if reference_rotation is not None and self._reference_available() and imu_weight > 0.0:
            blended_rotation = self._blend_rotations(visual_rotation, reference_rotation, imu_weight)
        else:
            blended_rotation = visual_rotation

        if (
            self.config.enable_translation_fusion
            and reference_rotation is not None
            and self._reference_available()
            and imu_weight > 0.0
        ):
            blended_tvec = self._fuse_translation(visual_tvec, reference_rotation, imu_weight)
        else:
            blended_tvec = visual_tvec

        source = "vision" if imu_weight <= 0.0 else "vision_imu"
        output_pose = MarkerPose(
            rvec=rotmat_to_rvec(blended_rotation),
            tvec=blended_tvec,
            reprojection_error_px=visual_pose.reprojection_error_px,
        )

        self._last_visual_pose = output_pose
        self._last_visual_s = float(now_s)
        self._last_fused_tvec = blended_tvec.copy()
        if reference_rotation is not None:
            self._last_imu_reference = reference_rotation.copy()

        return FusionResult(
            pose=output_pose,
            source=source,
            imu=self._diagnostics(imu_frame, now_s, orientation_error, visual_confidence, imu_weight),
        )

    def _handle_visual_absent(
        self,
        imu_rotation: Optional[np.ndarray],
        imu_frame: Optional[ImuFrame],
        now_s: float,
    ) -> FusionResult:
        reference_rotation = self._reference_rotation(imu_rotation)
        if (
            self._last_visual_pose is not None
            and self._last_visual_s is not None
            and reference_rotation is not None
            and self._reference_available()
            and float(now_s) - self._last_visual_s <= self.config.imu_hold_max_age_s
        ):
            held_tvec = self._last_visual_pose.tvec.reshape(3, 1).copy()
            if self.config.enable_translation_fusion and self._last_imu_reference is not None:
                held_tvec = self._propagate_translation(
                    self._last_fused_tvec if self._last_fused_tvec is not None else held_tvec,
                    self._last_imu_reference,
                    reference_rotation,
                )

            held_pose = MarkerPose(
                rvec=rotmat_to_rvec(reference_rotation),
                tvec=held_tvec,
                reprojection_error_px=self._last_visual_pose.reprojection_error_px,
            )

            self._last_imu_reference = reference_rotation.copy()
            self._last_fused_tvec = held_tvec.copy()

            return FusionResult(
                pose=held_pose,
                source="imu_hold",
                imu=self._diagnostics(imu_frame, now_s, None, None, 1.0),
            )

        return FusionResult(
            pose=None,
            source="none",
            imu=self._diagnostics(imu_frame, now_s, None, None, 0.0),
        )

    def _calibrate_camera_world(self, visual_rotation: np.ndarray, imu_rotation: np.ndarray) -> None:
        if self.config.camera_world_rotation is not None:
            self._learn_count = max(self._learn_count, self.config.min_learn_frames)
            return

        world_base = self._world_base_rotation(imu_rotation)
        sample = orthonormalize(visual_rotation @ world_base.T)
        alpha = float(self.config.learn_alpha)
        batch_n = int(self.config.batch_calibration_samples)

        if self._camera_world is None and len(self._calibration_samples) < batch_n:
            self._calibration_samples.append(sample.copy())
            self._learn_count += 1
            return

        if self._camera_world is None and len(self._calibration_samples) >= batch_n:
            self._calibration_samples.append(sample.copy())
            stacked = np.stack(self._calibration_samples, axis=0)
            mean_matrix = np.mean(stacked, axis=0)
            self._camera_world = orthonormalize(mean_matrix)
            self._calibration_samples.clear()
            self._learn_count = max(batch_n + 1, self.config.min_learn_frames)
            return

        if self.config.auto_learn_extrinsic:
            self._camera_world = orthonormalize(
                (1.0 - alpha) * self._camera_world + alpha * sample,
            )
            self._learn_count += 1
        elif self._learn_count < self.config.min_learn_frames:
            self._camera_world = orthonormalize(
                (1.0 - alpha) * self._camera_world + alpha * sample,
            )
            self._learn_count += 1

    def _fuse_translation(
        self,
        visual_tvec: np.ndarray,
        reference_rotation: np.ndarray,
        imu_weight: float,
    ) -> np.ndarray:
        lever = np.asarray(self.config.lever_arm_mm, dtype=np.float64).reshape(3, 1)

        if self._last_imu_reference is None or self._last_fused_tvec is None:
            self._last_fused_tvec = visual_tvec.copy()
            self._last_imu_reference = reference_rotation.copy()
            return visual_tvec.copy()

        prev_t = self._last_fused_tvec.reshape(3, 1)
        delta_rotation = orthonormalize(reference_rotation @ self._last_imu_reference.T)

        if np.linalg.norm(lever) > 1e-6:
            t_predicted = delta_rotation @ (prev_t + lever) - lever
        else:
            t_predicted = delta_rotation @ prev_t

        t_predicted = t_predicted.reshape(3, 1)

        delta = t_predicted - visual_tvec
        delta_norm = float(np.linalg.norm(delta))
        max_blend = self.config.max_translation_blend_m * 1000.0
        if delta_norm > max_blend and delta_norm > 1e-9:
            delta = delta * (max_blend / delta_norm)
            t_predicted = visual_tvec + delta

        w = self._clamp01(imu_weight)
        blended = (1.0 - w) * visual_tvec + w * t_predicted

        self._last_imu_reference = reference_rotation.copy()
        self._last_fused_tvec = blended.copy()
        return blended

    @staticmethod
    def _propagate_translation(
        prev_tvec: np.ndarray,
        prev_reference: np.ndarray,
        curr_reference: np.ndarray,
    ) -> np.ndarray:
        delta_rotation = orthonormalize(curr_reference @ prev_reference.T)
        return (delta_rotation @ prev_tvec.reshape(3, 1)).reshape(3, 1)

    def _world_base_rotation(self, imu_rotation: np.ndarray) -> np.ndarray:
        base_to_imu = orthonormalize(self.config.base_to_imu_rotation)
        return orthonormalize(imu_rotation @ base_to_imu.T)

    def _reference_rotation(self, imu_rotation: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if imu_rotation is None or self._camera_world is None:
            return None
        return orthonormalize(self._camera_world @ self._world_base_rotation(imu_rotation))

    def _reference_available(self) -> bool:
        return self._camera_world is not None and self._learn_count >= int(self.config.min_learn_frames)

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _visual_confidence(self, pose: MarkerPose) -> float:
        distance_m = float(np.linalg.norm(np.asarray(pose.tvec, dtype=np.float64).reshape(3))) / 1000.0
        distance_score = self._range_score(distance_m, self.config.near_distance_m, self.config.far_distance_m)
        error_score = self._range_score(
            float(pose.reprojection_error_px), self.config.low_error_px, self.config.high_error_px
        )
        base_confidence = round(1.0 - max(distance_score, error_score), 4)
        if int(self.config.active_marker_count) < 2:
            return round(base_confidence * float(self.config.single_marker_confidence_scale), 4)
        return base_confidence

    def _imu_weight(self, pose: MarkerPose, reference_rotation: Optional[np.ndarray]) -> float:
        if reference_rotation is None or not self._reference_available():
            return 0.0
        visual_confidence = self._visual_confidence(pose)
        weight = float(self.config.max_imu_weight) * (1.0 - visual_confidence)
        return round(self._clamp01(weight), 4)

    def _range_score(self, value: float, low: float, high: float) -> float:
        if high <= low:
            return 1.0 if value >= high else 0.0
        return self._clamp01((float(value) - float(low)) / (float(high) - float(low)))

    def _blend_rotations(self, visual_rotation: np.ndarray, reference_rotation: np.ndarray, weight: float) -> np.ndarray:
        relative = orthonormalize(reference_rotation @ visual_rotation.T)
        delta_rvec = rotmat_to_rvec(relative).reshape(3, 1)
        step_rotation = rvec_to_rotmat(delta_rvec * self._clamp01(weight))
        return orthonormalize(step_rotation @ visual_rotation)

    def _diagnostics(
        self,
        imu_frame: Optional[ImuFrame],
        now_s: float,
        orientation_error_deg: Optional[float],
        visual_confidence: Optional[float],
        imu_weight: float,
    ) -> dict:
        return {
            "present": imu_frame is not None,
            "age_ms": round((float(now_s) - imu_frame.received_monotonic_s) * 1000.0, 3) if imu_frame else None,
            "seq": imu_frame.seq if imu_frame else None,
            "frame_id": imu_frame.frame_id if imu_frame else None,
            "learn_count": self._learn_count,
            "reference_available": self._reference_available(),
            "auto_learn_extrinsic": self.config.auto_learn_extrinsic,
            "translation_fusion_enabled": self.config.enable_translation_fusion,
            "orientation_error_deg": (
                round(float(orientation_error_deg), 4) if orientation_error_deg is not None else None
            ),
            "orientation_suspicious": (
                bool(orientation_error_deg > self.config.orientation_warn_deg)
                if orientation_error_deg is not None
                else False
            ),
            "visual_confidence": visual_confidence,
            "imu_weight": round(float(imu_weight), 4),
        }

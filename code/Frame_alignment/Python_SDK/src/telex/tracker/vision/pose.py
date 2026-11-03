"""位姿估计与融合（鱼眼相机版）。

与 V3 的 ``stereo_marker_tracking.pose`` 结构一致，区别在于：
``estimate_marker_pose_fisheye`` 先用 ``cv2.fisheye.undistortPoints`` 把角点从
鱼眼(等距)畸变去除到理想针孔坐标（投影矩阵取原始 K），再用零畸变的 ``solvePnP``
求解 6D 位姿。这样能复用针孔模型的 PnP / 融合 / 离群过滤逻辑，同时对 180° 鱼眼
镜头使用正确的畸变模型。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class MarkerPose:
    rvec: np.ndarray
    tvec: np.ndarray
    reprojection_error_px: float


@dataclass(frozen=True)
class FusedPose:
    pose: Optional[MarkerPose]
    source: str


# ─────────────────────────────────────────────────────────────────
#  Frame-to-frame pose outlier filter (axis-flip suppression)
# ─────────────────────────────────────────────────────────────────
class PoseOutlierFilter:
    """Reject sudden PnP axis-flip jumps; allow continuous fast motion.

    Thresholds are defined at a 30 Hz reference frame period and scaled by the
    measured ``dt`` so higher/lower camera rates keep similar physical limits.

    Large **rotation** jumps (typical of axis flips) may hold the previous pose
    for up to ``max_hold_frames``. Large **translation** alone is treated as
    real motion and accepted, avoiding the stick-then-jump artifact when the
    hand moves quickly.
    """

    _REF_DT_S = 1.0 / 30.0

    def __init__(
        self,
        max_rotation_deg: float = 45.0,
        max_translation_mm: float = 100.0,
        max_hold_frames: int = 1,
    ) -> None:
        self.max_rotation_deg = float(max_rotation_deg)
        self.max_translation_mm = float(max_translation_mm)
        self.max_hold_frames = int(max_hold_frames)
        self._last_pose: Optional[MarkerPose] = None
        self._hold_count: int = 0
        self._last_check_s: Optional[float] = None

    def check(self, pose: Optional[MarkerPose]) -> tuple[Optional[MarkerPose], bool]:
        """Return ``(accepted_pose, is_outlier)``."""
        now_s = time.monotonic()
        if pose is None:
            self._last_pose = None
            self._hold_count = 0
            self._last_check_s = now_s
            return None, False

        if self._last_pose is None:
            self._last_pose = pose
            self._hold_count = 0
            self._last_check_s = now_s
            return pose, False

        if self._last_check_s is None:
            dt_s = self._REF_DT_S
        else:
            dt_s = float(now_s - self._last_check_s)
        if not np.isfinite(dt_s) or dt_s <= 1e-4:
            dt_s = self._REF_DT_S
        # Cap scale so a hitch doesn't open the gate to absurd jumps.
        scale = min(max(dt_s / self._REF_DT_S, 0.5), 4.0)
        max_rotation_deg = self.max_rotation_deg * scale
        max_translation_mm = self.max_translation_mm * scale

        # ── rotation delta ──────────────────────────────────────────
        r1, _ = cv2.Rodrigues(
            np.asarray(self._last_pose.rvec, dtype=np.float64).reshape(3, 1)
        )
        r2, _ = cv2.Rodrigues(
            np.asarray(pose.rvec, dtype=np.float64).reshape(3, 1)
        )
        relative = r2 @ r1.T
        cos_theta = (float(np.trace(relative)) - 1.0) / 2.0
        theta_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_theta))))

        # ── translation delta ───────────────────────────────────────
        dt_mm = float(
            np.linalg.norm(
                np.asarray(pose.tvec, dtype=np.float64).reshape(3)
                - np.asarray(self._last_pose.tvec, dtype=np.float64).reshape(3)
            )
        )

        # Axis flips are rotation-dominated. Pure translation outliers are
        # almost always legitimate fast hand motion — accepting them avoids
        # freezing the object frame for max_hold_frames then jumping.
        rotation_outlier = theta_deg > max_rotation_deg
        # Still reject absurd translation+rotation combos (flip with jump).
        absurd_jump = (
            dt_mm > max_translation_mm and theta_deg > 0.5 * max_rotation_deg
        )

        if rotation_outlier or absurd_jump:
            if self._hold_count < self.max_hold_frames:
                self._hold_count += 1
                self._last_check_s = now_s
                return self._last_pose, True  # reject: reuse previous

        # accepted
        self._last_pose = pose
        self._hold_count = 0
        self._last_check_s = now_s
        return pose, False

    def reset(self) -> None:
        self._last_pose = None
        self._hold_count = 0
        self._last_check_s = None


def marker_object_points_mm(marker_size_mm: float) -> np.ndarray:
    half = float(marker_size_mm) / 2.0
    return np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float64,
    )


def rvec_tvec_to_matrix(rvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    rotation, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))
    matrix[:3, :3] = rotation
    matrix[:3, 3] = np.asarray(tvec, dtype=np.float64).reshape(3)
    return matrix


def matrix_to_rvec_tvec(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rvec, _ = cv2.Rodrigues(np.asarray(matrix, dtype=np.float64)[:3, :3])
    tvec = np.asarray(matrix, dtype=np.float64)[:3, 3].reshape(3, 1)
    return rvec, tvec


def transform_pose(pose: MarkerPose, transform: np.ndarray) -> MarkerPose:
    pose_matrix = rvec_tvec_to_matrix(pose.rvec, pose.tvec)
    transformed_matrix = np.asarray(transform, dtype=np.float64) @ pose_matrix
    rvec, tvec = matrix_to_rvec_tvec(transformed_matrix)
    return MarkerPose(
        rvec=rvec,
        tvec=tvec,
        reprojection_error_px=pose.reprojection_error_px,
    )


def fuse_stereo_poses(
    left_pose: Optional[MarkerPose],
    right_pose_in_left: Optional[MarkerPose],
) -> FusedPose:
    if left_pose is None and right_pose_in_left is None:
        return FusedPose(pose=None, source="none")
    if left_pose is None:
        return FusedPose(pose=right_pose_in_left, source="right")
    if right_pose_in_left is None:
        return FusedPose(pose=left_pose, source="left")

    tvec = (left_pose.tvec.reshape(3, 1) + right_pose_in_left.tvec.reshape(3, 1)) / 2.0
    return FusedPose(
        pose=MarkerPose(
            rvec=left_pose.rvec.copy(),
            tvec=tvec,
            reprojection_error_px=(left_pose.reprojection_error_px + right_pose_in_left.reprojection_error_px) / 2.0,
        ),
        source="stereo",
    )


def estimate_marker_pose_fisheye(
    corners: np.ndarray,
    marker_size_mm: float,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> MarkerPose:
    """鱼眼相机下的单 marker 6D 位姿估计。

    步骤：
      1. ``cv2.fisheye.undistortPoints(corners, K, D, P=K)`` 把角点去畸变到
         以原始 K 为投影矩阵的理想针孔像素坐标；
      2. 用去畸变后的角点 + K + 零畸变运行 ``solvePnP(IPPE_SQUARE)``；
      3. 重投影误差在去畸变坐标系内计算。
    """
    image_points = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    camera_matrix = np.asarray(camera_matrix, dtype=np.float64)
    dist_coeffs = np.asarray(dist_coeffs, dtype=np.float64).reshape(-1, 1)

    undistorted = cv2.fisheye.undistortPoints(
        image_points.reshape(-1, 1, 2),
        camera_matrix,
        dist_coeffs,
        P=camera_matrix,
    ).reshape(4, 2)

    object_points = marker_object_points_mm(marker_size_mm)
    zero_dist = np.zeros((1, 5), dtype=np.float64)
    ok, rvec, tvec = cv2.solvePnP(
        object_points,
        undistorted,
        camera_matrix,
        zero_dist,
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )
    if not ok:
        raise ValueError("cv2.solvePnP failed for marker corners")

    projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, zero_dist)
    residual = projected.reshape(4, 2) - undistorted
    reprojection_error_px = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))
    return MarkerPose(rvec=rvec, tvec=tvec, reprojection_error_px=reprojection_error_px)

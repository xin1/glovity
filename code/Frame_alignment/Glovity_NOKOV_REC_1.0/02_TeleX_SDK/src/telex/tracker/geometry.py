"""Rigid transforms and pose blending helpers."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from telex.tracker.types import RigPoseEstimate, _readonly_pose_copy
from telex.tracker.vision.pose import (
    MarkerPose,
    matrix_to_rvec_tvec,
    rvec_tvec_to_matrix,
    transform_pose,
)

def normalize_rigid_transform(transform: np.ndarray) -> np.ndarray:
    result = np.asarray(transform, dtype=np.float64).copy()
    if result.shape != (4, 4) or not np.all(np.isfinite(result)):
        raise ValueError("刚体变换必须是有限的 4x4 矩阵")
    if not np.allclose(
        result[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9, rtol=0.0
    ):
        raise ValueError("刚体变换末行必须为 [0, 0, 0, 1]")
    u, _singular, vt = np.linalg.svd(result[:3, :3])
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1.0
        rotation = u @ vt
    result[:3, :3] = rotation
    result[3] = [0.0, 0.0, 0.0, 1.0]
    return result


def validate_rigid_transform(transform: np.ndarray, name: str) -> np.ndarray:
    """校验并返回连续 float64 刚体变换，不修正无效旋转。"""
    try:
        result = np.ascontiguousarray(np.asarray(transform, dtype=np.float64))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限刚体变换") from exc
    if result.shape != (4, 4) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} 必须是有限刚体变换")
    if not np.allclose(
        result[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9, rtol=0.0
    ):
        raise ValueError(f"{name} 必须是有限刚体变换")
    rotation = result[:3, :3]
    if not np.allclose(
        rotation.T @ rotation, np.eye(3), atol=1e-9, rtol=0.0
    ) or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-9, rtol=0.0):
        raise ValueError(f"{name} 必须是有限刚体变换")
    return result.copy()


def _rotation_matrix_to_quaternion(rotation: np.ndarray) -> np.ndarray:
    matrix = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    trace = float(np.trace(matrix))
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        quaternion = np.array(
            [
                0.25 * s,
                (matrix[2, 1] - matrix[1, 2]) / s,
                (matrix[0, 2] - matrix[2, 0]) / s,
                (matrix[1, 0] - matrix[0, 1]) / s,
            ],
            dtype=np.float64,
        )
    else:
        diagonal = np.diag(matrix)
        index = int(np.argmax(diagonal))
        if index == 0:
            s = math.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2.0
            quaternion = np.array(
                [
                    (matrix[2, 1] - matrix[1, 2]) / s,
                    0.25 * s,
                    (matrix[0, 1] + matrix[1, 0]) / s,
                    (matrix[0, 2] + matrix[2, 0]) / s,
                ],
                dtype=np.float64,
            )
        elif index == 1:
            s = math.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2.0
            quaternion = np.array(
                [
                    (matrix[0, 2] - matrix[2, 0]) / s,
                    (matrix[0, 1] + matrix[1, 0]) / s,
                    0.25 * s,
                    (matrix[1, 2] + matrix[2, 1]) / s,
                ],
                dtype=np.float64,
            )
        else:
            s = math.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2.0
            quaternion = np.array(
                [
                    (matrix[1, 0] - matrix[0, 1]) / s,
                    (matrix[0, 2] + matrix[2, 0]) / s,
                    (matrix[1, 2] + matrix[2, 1]) / s,
                    0.25 * s,
                ],
                dtype=np.float64,
            )
    norm = float(np.linalg.norm(quaternion))
    if norm <= 1e-12:
        raise ValueError("旋转矩阵无法转换为四元数")
    return quaternion / norm


def _quaternion_to_rotation_matrix(quaternion: np.ndarray) -> np.ndarray:
    w, x, y, z = np.asarray(quaternion, dtype=np.float64).reshape(4)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _slerp_rotation(
    first: np.ndarray,
    second: np.ndarray,
    alpha: float,
) -> np.ndarray:
    q0 = _rotation_matrix_to_quaternion(first)
    q1 = _rotation_matrix_to_quaternion(second)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    dot = float(np.clip(dot, -1.0, 1.0))
    if dot > 0.9995:
        blended = q0 + float(alpha) * (q1 - q0)
        blended /= np.linalg.norm(blended)
    else:
        theta = math.acos(dot)
        sin_theta = math.sin(theta)
        blended = (
            math.sin((1.0 - alpha) * theta) / sin_theta * q0
            + math.sin(alpha * theta) / sin_theta * q1
        )
    return _quaternion_to_rotation_matrix(blended)


def _blend_pose_estimates(
    left: RigPoseEstimate,
    right_in_left: RigPoseEstimate,
) -> MarkerPose:
    if left.pose is None or right_in_left.pose is None:
        raise ValueError("双目融合需要左右两个 pose")
    left_quality = left.confidence / (
        0.25 + left.pose.reprojection_error_px
    )
    right_quality = right_in_left.confidence / (
        0.25 + right_in_left.pose.reprojection_error_px
    )
    total = left_quality + right_quality
    alpha = 0.5 if total <= 1e-12 else right_quality / total

    left_transform = rvec_tvec_to_matrix(left.pose.rvec, left.pose.tvec)
    right_transform = rvec_tvec_to_matrix(
        right_in_left.pose.rvec, right_in_left.pose.tvec
    )
    blended = np.eye(4, dtype=np.float64)
    blended[:3, :3] = _slerp_rotation(
        left_transform[:3, :3], right_transform[:3, :3], alpha
    )
    blended[:3, 3] = (
        (1.0 - alpha) * left_transform[:3, 3]
        + alpha * right_transform[:3, 3]
    )
    rvec, tvec = matrix_to_rvec_tvec(blended)
    error = (
        (1.0 - alpha) * left.pose.reprojection_error_px
        + alpha * right_in_left.pose.reprojection_error_px
    )
    return _readonly_pose_copy(MarkerPose(rvec, tvec, error))


def rotation_x_matrix(degrees: float) -> np.ndarray:
    radians = math.radians(float(degrees))
    c, s = math.cos(radians), math.sin(radians)
    return np.array(
        [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]],
        dtype=np.float64,
    )


def mid_frame_from_left_mm(
    right_to_left_mm: np.ndarray,
    *,
    pitch_up_deg: float = 0.0,
) -> np.ndarray:
    """构建中点对称系相对左目的 ``T_mid←left``（mm）。

    定义（在左目坐标系中）：
      1. 原点取左右相机光心连线中点；
      2. 先建 Z 朝前的中间系：X 沿基线（左→右），Z 为左右光轴平均前向，
         Y = Z × X（右手系，近似朝下）；
      3. 再绕该系 X 轴旋转 -90°，使 Z 朝上（Y 朝前）。
         （OpenCV 相机系 Y 朝下；若用 +90° 会得到 Z 朝下。）
      4. ``pitch_up_deg`` 表示将所得坐标轴物理绕自身 +X 上抬的角度。
         因此对点坐标的被动变换使用 ``Rx(-pitch_up_deg)``。

    返回 4×4：``P_mid = R @ P_left + t``。
    """
    transform = validate_rigid_transform(right_to_left_mm, "right_to_left_mm")
    pitch_up_deg = float(pitch_up_deg)
    if not np.isfinite(pitch_up_deg):
        raise ValueError("pitch_up_deg 必须是有限数值")
    # 右相机原点在左目系中的位置
    c_right = transform[:3, 3].copy()
    baseline = c_right
    baseline_norm = float(np.linalg.norm(baseline))
    if not np.isfinite(baseline_norm) or baseline_norm < 1e-6:
        raise ValueError("双目基线过短，无法构建中点坐标系")
    x_axis = baseline / baseline_norm

    # 左右相机 Z 轴（前向）在左目系中的表达
    z_left = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    z_right = transform[:3, :3] @ z_left
    z_forward = z_left + z_right
    z_forward = z_forward - float(np.dot(z_forward, x_axis)) * x_axis
    z_norm = float(np.linalg.norm(z_forward))
    if not np.isfinite(z_norm) or z_norm < 1e-6:
        raise ValueError("无法从双目光轴估计前向，无法构建中点坐标系")
    z_axis = z_forward / z_norm
    y_axis = np.cross(z_axis, x_axis)
    y_norm = float(np.linalg.norm(y_axis))
    if not np.isfinite(y_norm) or y_norm < 1e-6:
        raise ValueError("中点坐标系轴向退化")
    y_axis = y_axis / y_norm
    # 再正交化 Z，保证严格右手正交
    z_axis = np.cross(x_axis, y_axis)
    z_axis = z_axis / float(np.linalg.norm(z_axis))

    # R_left←fwd：列向量为中间系（Z 朝前）基轴在左目系中的坐标
    rotation_left_from_fwd = np.column_stack([x_axis, y_axis, z_axis])
    mid_origin_in_left = 0.5 * c_right

    left_from_fwd = np.eye(4, dtype=np.float64)
    left_from_fwd[:3, :3] = rotation_left_from_fwd
    left_from_fwd[:3, 3] = mid_origin_in_left
    fwd_from_left = np.linalg.inv(left_from_fwd)

    # 绕中间系 X 转 -90°：Z 朝前 + Y 朝下 → Z 朝上 + Y 朝前
    rx = np.eye(4, dtype=np.float64)
    rx[:3, :3] = rotation_x_matrix(-90.0)
    mid_from_left = rx @ fwd_from_left
    if pitch_up_deg != 0.0:
        level_from_mid = np.eye(4, dtype=np.float64)
        level_from_mid[:3, :3] = rotation_x_matrix(-pitch_up_deg)
        mid_from_left = level_from_mid @ mid_from_left
    return normalize_rigid_transform(mid_from_left)


def pose_in_frame(
    pose: Optional[MarkerPose],
    frame_from_left_mm: np.ndarray,
) -> Optional[MarkerPose]:
    """把左目系下的 tracker 位姿变换到 ``frame_from_left`` 坐标系。"""
    if pose is None:
        return None
    return transform_pose(
        pose,
        validate_rigid_transform(frame_from_left_mm, "frame_from_left_mm"),
    )

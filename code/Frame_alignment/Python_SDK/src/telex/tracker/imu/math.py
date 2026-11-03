from __future__ import annotations

import math

import cv2
import numpy as np


def orthonormalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    u, _, vt = np.linalg.svd(matrix)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0.0:
        u[:, -1] *= -1.0
        rotation = u @ vt
    return rotation


def quat_wxyz_to_rotmat(quat_wxyz: np.ndarray) -> np.ndarray:
    quat = np.asarray(quat_wxyz, dtype=np.float64).reshape(4)
    norm = float(np.linalg.norm(quat))
    if norm < 1e-12:
        raise ValueError("IMU quaternion norm is zero")
    w, x, y, z = quat / norm
    rotation = np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    return orthonormalize(rotation)


def rvec_to_rotmat(rvec: np.ndarray) -> np.ndarray:
    rotation, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))
    return rotation


def rotmat_to_rvec(rotation: np.ndarray) -> np.ndarray:
    rvec, _ = cv2.Rodrigues(orthonormalize(rotation))
    return rvec


def rotation_angle_deg(rotation_a: np.ndarray, rotation_b: np.ndarray) -> float:
    relative = orthonormalize(rotation_b) @ orthonormalize(rotation_a).T
    cos_theta = (float(np.trace(relative)) - 1.0) / 2.0
    theta = math.acos(max(-1.0, min(1.0, cos_theta)))
    return math.degrees(theta)


def euler_zyx_deg_from_rvec(rvec: np.ndarray) -> np.ndarray:
    rotation = rvec_to_rotmat(rvec)
    sy = math.hypot(float(rotation[0, 0]), float(rotation[1, 0]))
    if sy > 1e-9:
        roll_x = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        pitch_y = math.atan2(float(-rotation[2, 0]), sy)
        yaw_z = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    else:
        roll_x = math.atan2(float(-rotation[1, 2]), float(rotation[1, 1]))
        pitch_y = math.atan2(float(-rotation[2, 0]), sy)
        yaw_z = 0.0
    return np.degrees(np.array([roll_x, pitch_y, yaw_z], dtype=np.float64))

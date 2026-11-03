"""加载鱼眼双目标定结果（``stereo_fisheye_calib.yml``）。

该文件由 ``camera/calibrate_fisheye_stereo.py`` 用 ``cv2.fisheye.stereoCalibrate``
生成，关键字段：

  - ``K_left`` / ``D_left``  左目鱼眼内参 (3x3) + 畸变 (4x1)
  - ``K_right`` / ``D_right`` 右目鱼眼内参 + 畸变
  - ``R`` / ``T``            左→右 外参：``X_right = R · X_left + T``（T 单位为米）

位姿融合时需要把右目位姿变换到左目坐标系，因此这里由 ``[R|T]`` 取逆得到
``right_to_left``（平移米→毫米），供 ``track.pose.transform_pose`` 使用。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class CameraCalibration:
    camera_matrix: np.ndarray  # 3x3 鱼眼 K
    dist_coeffs: np.ndarray    # 4x1 鱼眼 D（配合 cv2.fisheye.* 使用）


@dataclass(frozen=True)
class StereoCalibration:
    left: CameraCalibration
    right: CameraCalibration
    right_to_left_mm: np.ndarray  # 4x4，把右目坐标变换到左目坐标（mm）
    width: int
    height: int


def _read_matrix(storage: cv2.FileStorage, key: str) -> np.ndarray:
    node = storage.getNode(key)
    if node.empty():
        raise ValueError(f"Missing calibration key: {key}")
    return np.asarray(node.mat(), dtype=np.float64)


def _read_optional_int(storage: cv2.FileStorage, key: str, default: int) -> int:
    node = storage.getNode(key)
    if node.empty():
        return default
    return int(node.real())


def load_fisheye_stereo_calibration(path: str) -> StereoCalibration:
    storage = cv2.FileStorage(path, cv2.FILE_STORAGE_READ)
    if not storage.isOpened():
        raise ValueError(f"Could not open calibration file: {path}")
    try:
        k_left = _read_matrix(storage, "K_left").reshape(3, 3)
        d_left = _read_matrix(storage, "D_left").reshape(-1, 1)
        k_right = _read_matrix(storage, "K_right").reshape(3, 3)
        d_right = _read_matrix(storage, "D_right").reshape(-1, 1)
        rotation = _read_matrix(storage, "R").reshape(3, 3)
        translation_m = _read_matrix(storage, "T").reshape(3)

        # [R|T] 为左→右 (X_right = R·X_left + T)，平移由米转毫米。
        left_to_right_mm = np.eye(4, dtype=np.float64)
        left_to_right_mm[:3, :3] = rotation
        left_to_right_mm[:3, 3] = translation_m * 1000.0
        right_to_left_mm = np.linalg.inv(left_to_right_mm)

        width = _read_optional_int(storage, "image_width", 1280)
        height = _read_optional_int(storage, "image_height", 720)

        return StereoCalibration(
            left=CameraCalibration(camera_matrix=k_left, dist_coeffs=d_left),
            right=CameraCalibration(camera_matrix=k_right, dist_coeffs=d_right),
            right_to_left_mm=right_to_left_mm,
            width=width,
            height=height,
        )
    finally:
        storage.release()

"""Visualization helpers for tracker HUD and axes."""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from telex.tracker.imu.math import euler_zyx_deg_from_rvec
from telex.tracker.types import TrackerOutput
from telex.tracker.vision.pose import MarkerPose

# 不同 marker 的坐标轴/描边颜色 (BGR)
_MARKER_COLORS: Dict[int, Tuple[int, int, int]] = {
    1: (0, 0, 255),
    5: (0, 255, 0),
    11: (255, 0, 0),
    18: (0, 255, 255),
    19: (255, 0, 255),
    27: (255, 255, 0),
}
_FALLBACK_COLOR = (200, 200, 200)
TRACKER_LABEL_COLORS: Dict[str, Tuple[int, int, int]] = {
    "left": (0, 255, 0),
    "right": (0, 255, 255),
}
_TRACKER_AXIS_COLORS = (
    (0, 0, 255),
    (0, 255, 0),
    (255, 0, 0),
)
_VISUALIZATION_MIN_DEPTH_MM = 1e-6


class TrackerVisualizationUnavailable(RuntimeError):
    """当前 tracker 几何或投影无法安全显示。"""


def color_for(marker_id: int) -> Tuple[int, int, int]:
    return _MARKER_COLORS.get(marker_id, _FALLBACK_COLOR)


def _clip_segment_to_image(
    start: np.ndarray,
    end: np.ndarray,
    width: int,
    height: int,
) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """用 Liang-Barsky 在浮点域裁剪线段，再安全转换为像素整数。"""
    x0, y0 = (np.longdouble(value) for value in start)
    x1, y1 = (np.longdouble(value) for value in end)
    x_max = np.longdouble(width - 1)
    y_max = np.longdouble(height - 1)
    dx = x1 - x0
    dy = y1 - y0
    lower = np.longdouble(0.0)
    upper = np.longdouble(1.0)

    for p, q in (
        (-dx, x0),
        (dx, x_max - x0),
        (-dy, y0),
        (dy, y_max - y0),
    ):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            if ratio > upper:
                return None
            lower = max(lower, ratio)
        else:
            if ratio < lower:
                return None
            upper = min(upper, ratio)

    clipped = (
        (x0 + lower * dx, y0 + lower * dy),
        (x0 + upper * dx, y0 + upper * dy),
    )
    safe_points = []
    for x_value, y_value in clipped:
        x_float = float(np.clip(x_value, 0.0, x_max))
        y_float = float(np.clip(y_value, 0.0, y_max))
        safe_points.append((int(round(x_float)), int(round(y_float))))
    return safe_points[0], safe_points[1]


def draw_axes_fisheye(
    image: np.ndarray,
    projected_points: np.ndarray,
) -> None:
    """裁剪并绘制已经由鱼眼模型投影的 RGB 坐标轴。"""
    pts = np.asarray(projected_points, dtype=np.float64).reshape(-1, 2)
    if pts.shape != (4, 2) or not np.all(np.isfinite(pts)):
        raise TrackerVisualizationUnavailable("坐标轴投影必须包含四个有限点")
    height, width = image.shape[:2]
    for endpoint, color in zip(pts[1:], _TRACKER_AXIS_COLORS):
        clipped = _clip_segment_to_image(
            pts[0],
            endpoint,
            width,
            height,
        )
        if clipped is None:
            continue
        cv2.line(
            image,
            clipped[0],
            clipped[1],
            color,
            2,
            cv2.LINE_AA,
        )


def draw_tracker_axes(
    image: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    pose: MarkerPose,
    length_mm: float,
    label: str,
    label_color: Tuple[int, int, int],
) -> None:
    """绘制最终 tracker 物体系 RGB 坐标轴及固定手别标签。"""
    if pose is None:
        raise TypeError("pose 不能为空")
    if not isinstance(image, np.ndarray):
        raise TypeError("image 必须是 numpy.ndarray")
    if image.ndim not in (2, 3) or any(
        dimension <= 0 for dimension in image.shape
    ):
        raise ValueError("image 必须具有非空二维像素尺寸")
    height, width = image.shape[:2]
    if width > np.iinfo(np.int32).max or height > np.iinfo(np.int32).max:
        raise ValueError("image 像素尺寸超出 OpenCV 支持范围")

    length = float(length_mm)
    reprojection_error = float(pose.reprojection_error_px)
    rvec_values = np.asarray(pose.rvec, dtype=np.float64)
    tvec_values = np.asarray(pose.tvec, dtype=np.float64)
    camera_matrix_values = np.asarray(camera_matrix, dtype=np.float64)
    dist_coeff_values = np.asarray(dist_coeffs, dtype=np.float64)
    if rvec_values.size != 3 or tvec_values.size != 3:
        raise ValueError("pose 的 rvec/tvec 必须各包含 3 个数值")
    if camera_matrix_values.shape != (3, 3):
        raise ValueError("camera_matrix 必须是 3x3")
    if dist_coeff_values.size != 4:
        raise ValueError("鱼眼 dist_coeffs 必须恰好包含 4 项")
    rvec = np.ascontiguousarray(rvec_values.reshape(3, 1))
    tvec = np.ascontiguousarray(tvec_values.reshape(3, 1))
    camera_matrix_c = np.ascontiguousarray(camera_matrix_values)
    dist_coeffs_c = np.ascontiguousarray(dist_coeff_values.reshape(4, 1))
    if not np.isfinite(length) or length <= 0.0:
        raise ValueError("length_mm 必须是有限正数")
    if (
        not np.all(np.isfinite(camera_matrix_c))
        or not np.all(np.isfinite(dist_coeffs_c))
        or not np.all(np.isfinite(rvec))
        or not np.all(np.isfinite(tvec))
        or not np.isfinite(reprojection_error)
    ):
        raise ValueError("tracker axes 输入必须全部有限")

    axis_points = np.ascontiguousarray(
        np.array(
            [
                [0.0, 0.0, 0.0],
                [length, 0.0, 0.0],
                [0.0, length, 0.0],
                [0.0, 0.0, length],
            ],
            dtype=np.float64,
        ).reshape(1, 4, 3)
    )
    try:
        rotation, _ = cv2.Rodrigues(rvec)
    except cv2.error as exc:
        raise TrackerVisualizationUnavailable("Rodrigues 旋转转换失败") from exc
    with np.errstate(over="ignore", invalid="ignore"):
        points_in_camera = (
            axis_points.reshape(4, 3) @ rotation.T
            + tvec.reshape(1, 3)
        )
    if not np.all(np.isfinite(points_in_camera)):
        raise TrackerVisualizationUnavailable("坐标轴相机系坐标不是有限数值")
    if np.any(points_in_camera[:, 2] <= _VISUALIZATION_MIN_DEPTH_MM):
        raise TrackerVisualizationUnavailable("坐标轴位于相机后方或穿越相机平面")

    try:
        projected, _ = cv2.fisheye.projectPoints(
            axis_points,
            rvec,
            tvec,
            camera_matrix_c,
            dist_coeffs_c,
        )
    except (cv2.error, FloatingPointError, OverflowError) as exc:
        raise TrackerVisualizationUnavailable("鱼眼坐标轴投影失败") from exc
    try:
        projected_points = np.asarray(projected, dtype=np.float64).reshape(-1, 2)
    except (ValueError, OverflowError) as exc:
        raise TrackerVisualizationUnavailable("鱼眼坐标轴投影格式无效") from exc
    if projected_points.shape != (4, 2) or not np.all(
        np.isfinite(projected_points)
    ):
        raise TrackerVisualizationUnavailable("鱼眼坐标轴投影必须全部有限")

    draw_axes_fisheye(image, projected_points)

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.6
    text_thickness = 2
    text_size, baseline = cv2.getTextSize(
        label,
        font,
        font_scale,
        text_thickness,
    )
    desired_x = projected_points[0, 0] + 6.0
    desired_y = projected_points[0, 1] - 6.0
    max_x = max(0, width - int(text_size[0]))
    min_y = min(height - 1, max(0, int(text_size[1])))
    max_y = max(min_y, height - 1 - max(0, int(baseline)))
    label_x = int(round(float(np.clip(desired_x, 0.0, max_x))))
    label_y = int(round(float(np.clip(desired_y, min_y, max_y))))
    cv2.putText(
        image,
        label,
        (label_x, label_y),
        font,
        font_scale,
        (0, 0, 0),
        text_thickness + 2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        label,
        (label_x, label_y),
        font,
        font_scale,
        label_color,
        text_thickness,
        cv2.LINE_AA,
    )


def draw_marker_outline(
    image: np.ndarray,
    corners: np.ndarray,
    marker_id: int,
    *,
    held: bool = False,
) -> None:
    pts = np.asarray(corners, dtype=np.int32).reshape(-1, 1, 2)
    color = (120, 120, 120) if held else color_for(marker_id)
    thickness = 1 if held else 2
    cv2.polylines(
        image, [pts], isClosed=True, color=color, thickness=thickness
    )
    anchor = tuple(
        int(v)
        for v in np.asarray(corners, dtype=np.int32).reshape(4, 2)[0]
    )
    label = f"{marker_id}H" if held else str(marker_id)
    cv2.putText(
        image, label, (anchor[0], anchor[1] - 6),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, thickness, cv2.LINE_AA,
    )


def format_tracker_line(hand: str, output: TrackerOutput) -> str:
    visible = list(output.visible_ids)
    if output.pose is None:
        return (
            f"{hand.upper():5s} unavailable | visible={visible} "
            f"| outlier_held={output.outlier_held} "
            f"outlier_hold_frames={output.outlier_hold_frames}"
        )
    pose = output.pose
    t_mm = pose.tvec.reshape(3)
    distance_m = _pose_distance_m(pose)
    abc = euler_zyx_deg_from_rvec(pose.rvec)
    if not np.all(np.isfinite(abc)):
        raise ValueError("pose 派生欧拉角必须为有限数值")
    return (
        f"{hand.upper():5s} [vision={output.vision_source} "
        f"fusion={output.source} mode={output.mode}] "
        f"| visible={visible} | outlier_held={output.outlier_held} "
        f"outlier_hold_frames={output.outlier_hold_frames} "
        f"| d={distance_m:.3f}m "
        f"| t=({t_mm[0]:.1f}, {t_mm[1]:.1f}, {t_mm[2]:.1f})mm "
        f"| abc=({abc[0]:.1f}, {abc[1]:.1f}, {abc[2]:.1f})deg "
        f"| reproj={pose.reprojection_error_px:.2f}px"
    )


def format_imu_debug_line(
    hand: str, imu_debug: Optional[Mapping[str, object]]
) -> str:
    debug = {} if imu_debug is None else imu_debug
    present = debug.get("present", "n/a")
    weight = debug.get("imu_weight", debug.get("weight", "n/a"))
    learn_count = debug.get("learn_count", "n/a")
    return (
        f"{hand.upper():5s} IMU present={present} "
        f"weight={weight} learn_count={learn_count}"
    )


def _pose_distance_m(pose: MarkerPose) -> float:
    x, y, z = (
        float(value)
        for value in np.asarray(pose.tvec, dtype=np.float64).reshape(3)
    )
    distance_m = math.hypot(x, y, z) / 1000.0
    if not math.isfinite(distance_m):
        raise ValueError("pose 派生 distance_m 必须为有限数值")
    return distance_m


def _resize_for_display(image: np.ndarray, scale: float) -> np.ndarray:
    factor = float(scale)
    if factor >= 1.0:
        return image
    if factor <= 0.0:
        raise ValueError("display-scale 必须在 (0, 1] 范围内")
    height, width = image.shape[:2]
    return cv2.resize(
        image,
        (max(1, int(round(width * factor))), max(1, int(round(height * factor)))),
        interpolation=cv2.INTER_AREA,
    )


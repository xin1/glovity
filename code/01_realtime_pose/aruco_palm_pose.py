#!/usr/bin/env python3
"""Estimate a palm pose in real time from a rigidly attached square marker.

The default detector uses Find-GCP's custom dictionary 99 (32 markers with a
3x3 payload): https://github.com/zsiki/Find-GCP.  Standard OpenCV ArUco and
AprilTag dictionaries remain available through ``--dictionary``.

PnP returns ``T_camera_marker``.  If no palm-offset file is supplied, the
marker frame is treated as the palm frame.  With a calibrated fixed offset,
the reported pose is::

    T_camera_palm = T_camera_marker @ T_marker_palm

Press ``q`` or ``Esc`` in the video window to stop.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover - runtime dependency check
    print(f"Missing dependency: {exc}", file=sys.stderr)
    print(
        "Install with: python -m pip install numpy opencv-contrib-python",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


FIND_GCP_DICTIONARY_NAME = "DICT_3X3_32_FIND_GCP"
FIND_GCP_DICTIONARY_ALIASES = {
    FIND_GCP_DICTIONARY_NAME,
    "DICT_3X3",
    "DICT_3X3_32",
    "99",
}


@dataclass(frozen=True)
class CameraCalibration:
    camera_matrix: np.ndarray
    distortion_coefficients: np.ndarray
    image_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class MarkerPose:
    rotation_vector: np.ndarray
    translation_vector: np.ndarray
    reprojection_rmse_px: float

    @property
    def transform_camera_marker(self) -> np.ndarray:
        rotation, _ = cv2.Rodrigues(self.rotation_vector)
        transform = np.eye(4, dtype=np.float64)
        transform[:3, :3] = rotation
        transform[:3, 3] = self.translation_vector
        return transform


def _finite_array(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains NaN or infinity")
    return np.ascontiguousarray(array)


def _first_json_value(data: dict[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    raise ValueError(f"calibration file is missing one of: {', '.join(names)}")


def _read_file_storage_matrix(storage: Any, names: Sequence[str]) -> np.ndarray | None:
    for name in names:
        node = storage.getNode(name)
        if not node.empty():
            value = node.mat()
            if value is not None:
                return value
    return None


def load_camera_calibration(path: Path) -> CameraCalibration:
    """Load intrinsics from JSON, NPZ, or OpenCV YAML/XML."""

    if not path.is_file():
        raise ValueError(f"calibration file does not exist: {path}")

    camera_names = ("camera_matrix", "cameraMatrix", "K")
    distortion_names = (
        "distortion_coefficients",
        "dist_coeffs",
        "distCoeffs",
        "D",
    )
    image_size: tuple[int, int] | None = None

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        camera_matrix = _first_json_value(data, camera_names)
        distortion = _first_json_value(data, distortion_names)
        if "image_width" in data and "image_height" in data:
            image_size = (int(data["image_width"]), int(data["image_height"]))
    elif path.suffix.lower() == ".npz":
        with np.load(path) as data:
            camera_matrix = _first_json_value(dict(data), camera_names)
            distortion = _first_json_value(dict(data), distortion_names)
            if "image_size" in data:
                size = np.asarray(data["image_size"]).reshape(-1)
                image_size = (int(size[0]), int(size[1]))
    else:
        storage = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
        if not storage.isOpened():
            raise ValueError(f"OpenCV could not open calibration file: {path}")
        try:
            camera_matrix = _read_file_storage_matrix(storage, camera_names)
            distortion = _read_file_storage_matrix(storage, distortion_names)
            if camera_matrix is None or distortion is None:
                raise ValueError("calibration YAML/XML is missing camera or distortion data")
            width_node = storage.getNode("image_width")
            height_node = storage.getNode("image_height")
            if not width_node.empty() and not height_node.empty():
                image_size = (int(width_node.real()), int(height_node.real()))
        finally:
            storage.release()

    camera_matrix = _finite_array(camera_matrix, "camera_matrix")
    distortion = _finite_array(distortion, "distortion_coefficients").reshape(-1, 1)
    if camera_matrix.shape != (3, 3):
        raise ValueError("camera_matrix must have shape (3, 3)")
    if camera_matrix[0, 0] <= 0.0 or camera_matrix[1, 1] <= 0.0:
        raise ValueError("camera focal lengths must be positive")
    return CameraCalibration(camera_matrix, distortion, image_size)


def load_marker_to_palm_transform(path: Path | None) -> np.ndarray:
    """Load T_marker_palm from JSON, or return identity when frames coincide."""

    if path is None:
        return np.eye(4, dtype=np.float64)
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("T_marker_palm", data)
    transform = _finite_array(value, "T_marker_palm")
    if transform.shape != (4, 4):
        raise ValueError("T_marker_palm must have shape (4, 4)")
    if not np.allclose(transform[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9):
        raise ValueError("T_marker_palm must be a homogeneous transform")
    rotation = transform[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5):
        raise ValueError("T_marker_palm rotation is not orthonormal")
    if np.linalg.det(rotation) < 0.999:
        raise ValueError("T_marker_palm rotation must have determinant +1")
    return transform


def square_object_points(marker_length: float) -> np.ndarray:
    """Return the corner order required by SOLVEPNP_IPPE_SQUARE."""

    half = marker_length / 2.0
    return np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float64,
    )


def create_find_gcp_dictionary() -> Any:
    """Build Find-GCP dictionary 99: 32 markers with 3x3 payload bits.

    Find-GCP creates this custom dictionary with OpenCV's default random seed
    (zero), so rebuilding it this way produces the same marker codebook.
    """

    if hasattr(cv2.aruco, "extendDictionary"):
        return cv2.aruco.extendDictionary(32, 3)
    if hasattr(cv2.aruco, "Dictionary_create"):  # OpenCV < 4.7
        return cv2.aruco.Dictionary_create(32, 3, 0)
    raise RuntimeError("this OpenCV build cannot create the Find-GCP dictionary")


def _set_detector_parameter(parameters: Any, name: str, value: Any) -> None:
    if hasattr(parameters, name):
        setattr(parameters, name, value)


def configure_find_gcp_detector(parameters: Any) -> None:
    """Apply the detection defaults documented by the Find-GCP project."""

    settings = {
        "adaptiveThreshConstant": 7.0,
        "adaptiveThreshWinSizeMax": 23,
        "adaptiveThreshWinSizeMin": 3,
        "adaptiveThreshWinSizeStep": 10,
        "cornerRefinementMaxIterations": 30,
        "cornerRefinementMinAccuracy": 0.1,
        "cornerRefinementWinSize": 5,
        "detectInvertedMarker": True,
        "errorCorrectionRate": 0.6,
        "markerBorderBits": 1,
        "maxErroneousBitsInBorderRate": 0.35,
        "maxMarkerPerimeterRate": 4.0,
        "minCornerDistanceRate": 0.05,
        "minDistanceToBorder": 3,
        "minMarkerDistanceRate": 0.05,
        "minMarkerPerimeterRate": 0.03,
        "minOtsuStdDev": 5.0,
        "perspectiveRemoveIgnoredMarginPerCell": 0.13,
        "perspectiveRemovePixelPerCell": 4,
        "polygonalApproxAccuracyRate": 0.03,
        "useAruco3Detection": False,
    }
    for name, value in settings.items():
        _set_detector_parameter(parameters, name, value)


def create_detector(dictionary_name: str) -> tuple[Any, Any]:
    if not hasattr(cv2, "aruco"):
        raise RuntimeError(
            "cv2.aruco is unavailable; install opencv-contrib-python, not opencv-python"
        )
    if dictionary_name.upper() in FIND_GCP_DICTIONARY_ALIASES:
        dictionary = create_find_gcp_dictionary()
        is_find_gcp_dictionary = True
    else:
        dictionary_id = getattr(cv2.aruco, dictionary_name, None)
        if dictionary_id is None or not dictionary_name.startswith("DICT_"):
            raise ValueError(f"unknown ArUco/AprilTag dictionary: {dictionary_name}")
        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        is_find_gcp_dictionary = False
    if hasattr(cv2.aruco, "DetectorParameters"):
        parameters = cv2.aruco.DetectorParameters()
    else:  # OpenCV < 4.7 compatibility
        parameters = cv2.aruco.DetectorParameters_create()
    if is_find_gcp_dictionary:
        configure_find_gcp_detector(parameters)
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = (
        cv2.aruco.ArucoDetector(dictionary, parameters)
        if hasattr(cv2.aruco, "ArucoDetector")
        else None
    )
    return (detector, (dictionary, parameters))


def available_dictionary_names() -> list[str]:
    """Return one canonical name for every predefined OpenCV dictionary."""

    dictionaries_by_id: dict[int, str] = {}
    for name in dir(cv2.aruco):
        if not name.startswith("DICT_"):
            continue
        value = getattr(cv2.aruco, name)
        if isinstance(value, int):
            dictionaries_by_id.setdefault(value, name)
    return [FIND_GCP_DICTIONARY_NAME] + [
        dictionaries_by_id[key] for key in sorted(dictionaries_by_id)
    ]


def _detect_markers_once(frame: np.ndarray, detector_bundle: tuple[Any, Any]):
    detector, legacy = detector_bundle
    if detector is not None:
        return detector.detectMarkers(frame)
    dictionary, parameters = legacy
    return cv2.aruco.detectMarkers(
        frame, dictionary, parameters=parameters
    )


def detect_markers(
    frame: np.ndarray,
    detector_bundle: tuple[Any, Any],
    polarity: str,
):
    """Detect normal or color-inverted markers and report the image polarity used."""

    if polarity in ("normal", "both"):
        corners, ids, rejected = _detect_markers_once(frame, detector_bundle)
        if ids is not None or polarity == "normal":
            return corners, ids, rejected, "normal"

    inverted_frame = cv2.bitwise_not(frame)
    corners, ids, rejected = _detect_markers_once(inverted_frame, detector_bundle)
    return corners, ids, rejected, "inverted"


def solve_square_marker_pose(
    image_corners: np.ndarray,  #     ArUco 检测得到的 Marker 四个角点的 2D 像素坐标
    object_points: np.ndarray,  #     Marker 四个角点在 Marker 自身坐标系中的 3D 坐标
    calibration: CameraCalibration, #     相机标定参数，包括相机内参矩阵 K 和畸变参数
) -> MarkerPose | None: #     MarkerPose，里面包含旋转向量、平移向量、重投影误差
    """Solve both planar candidates and keep the best positive-depth pose."""

    result = cv2.solvePnPGeneric(  # 调用 OpenCV 的 PnP 求解函数。
        objectPoints=object_points, # Marker 四个角点的 3D 坐标。
        imagePoints=np.asarray(image_corners, dtype=np.float64).reshape(4, 2),  # Marker 在摄像头图像中检测到的四个角点。
        cameraMatrix=calibration.camera_matrix, # 相机内参矩阵 K。
        distCoeffs=calibration.distortion_coefficients,  # 相机镜头畸变参数。
        flags=cv2.SOLVEPNP_IPPE_SQUARE,  # 指定 PnP 使用的具体求解算法：IPPE_SQUARE
    )
    success, rotation_vectors, translation_vectors = result[:3]# solvePnPGeneric 会返回多个数据，只取前三个。
    if not success:
        return None

    candidates: list[MarkerPose] = [] # 创建一个列表用来保存所有“有效的 PnP 候选姿态”。
    for rotation_vector, translation_vector in zip(rotation_vectors, translation_vectors):# rvec + tvec
        rotation_vector = np.asarray(rotation_vector, dtype=np.float64).reshape(3)# 将旋转向量转换为长度为 3 的数组， [rx, ry, rz]。
        translation_vector = np.asarray(translation_vector, dtype=np.float64).reshape(3) # 将平移向量整理成：[tx, ty, tz]表示 Marker 原点相对于摄像头的位置。
        rotation, _ = cv2.Rodrigues(rotation_vector)# Rodrigues 变换：把 PnP 输出的旋转向量 rvec，转换成 3×3 旋转矩阵 R。
        camera_points = (rotation @ object_points.T).T + translation_vector # Pc = R × Pw + t
        if np.any(camera_points[:, 2] <= 0.0):# 检查所有 Marker 角点的 Z 坐标是否<=0。
            continue
        projected, _ = cv2.projectPoints( # 这里进行“重投影”。
            object_points,
            rotation_vector,
            translation_vector,
            calibration.camera_matrix,
            calibration.distortion_coefficients,
        )
        residuals = projected.reshape(4, 2) - np.asarray(image_corners).reshape(4, 2)# 计算“重投影残差”。
        rmse = float(np.sqrt(np.mean(np.sum(np.square(residuals), axis=1))))  # 均方根误差
        candidates.append(MarkerPose(rotation_vector, translation_vector, rmse))# 将当前这个有效候选解保存起来。
    return min(candidates, key=lambda pose: pose.reprojection_rmse_px, default=None)# 找到 reprojection_rmse_px 最小的那个。


def rotation_matrix_to_rpy_degrees(rotation: np.ndarray) -> np.ndarray:
    """Convert R = Rz(yaw) Ry(pitch) Rx(roll) to degrees."""

    horizontal = math.hypot(rotation[0, 0], rotation[1, 0])
    if horizontal > 1e-8:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        pitch = math.atan2(-rotation[2, 0], horizontal)
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = math.atan2(-rotation[1, 2], rotation[1, 1])
        pitch = math.atan2(-rotation[2, 0], horizontal)
        yaw = 0.0
    return np.rad2deg([roll, pitch, yaw])


def draw_pose_overlay(
    frame: np.ndarray,
    pose_label: str,
    transform_camera_palm: np.ndarray,
    calibration: CameraCalibration,
    axis_length: float,
    reprojection_rmse_px: float,
    fps: float,
) -> None:
    rotation_vector, _ = cv2.Rodrigues(transform_camera_palm[:3, :3])
    translation = transform_camera_palm[:3, 3]
    cv2.drawFrameAxes(
        frame,
        calibration.camera_matrix,
        calibration.distortion_coefficients,
        rotation_vector,
        translation,
        axis_length,
        2,
    )
    roll, pitch, yaw = rotation_matrix_to_rpy_degrees(
        transform_camera_palm[:3, :3]
    )
    lines = (
        f"Palm pose: {pose_label}",
        f"t [m]  x:{translation[0]:+.3f} y:{translation[1]:+.3f} z:{translation[2]:+.3f}",
        f"RPY [deg] {roll:+.1f} {pitch:+.1f} {yaw:+.1f}",
        f"reprojection:{reprojection_rmse_px:.2f}px  FPS:{fps:.1f}",
    )
    for row, line in enumerate(lines):
        y = 30 + row * 27
        cv2.putText(
            frame,
            line,
            (15, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            line,
            (15, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )


def draw_tracking_status(
    frame: np.ndarray,
    lines: Sequence[str],
    color: tuple[int, int, int] = (0, 80, 255),
) -> None:
    """Show actionable diagnostics when a selected marker has no pose."""

    for row, line in enumerate(lines):
        position = (15, 30 + row * 27)
        cv2.putText(
            frame,
            line,
            position,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            line,
            position,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            1,
            cv2.LINE_AA,
        )


def run_camera(args: argparse.Namespace) -> int:
    calibration = load_camera_calibration(args.calibration)
    transform_marker_palm = load_marker_to_palm_transform(args.palm_offset)
    automatic_dictionary = args.dictionary.upper() == "AUTO"
    if automatic_dictionary:
        dictionary_candidates = [
            (name, create_detector(name)) for name in available_dictionary_names()
        ]
        if not dictionary_candidates:
            raise RuntimeError("this OpenCV build exposes no predefined dictionaries")
        detector_bundle = dictionary_candidates[0][1]
        active_dictionary_name: str | None = None
        scan_index = 0
    else:
        dictionary_candidates = []
        detector_bundle = create_detector(args.dictionary)
        active_dictionary_name = args.dictionary
        scan_index = 0
    object_points = square_object_points(args.marker_length)

    capture = cv2.VideoCapture(args.camera)
    if args.width:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height:
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open camera index {args.camera}")

    automatic_target = args.marker_id == -1
    active_target_id: int | None = None if automatic_target else args.marker_id
    target_description = (
        "the first detected marker ID"
        if automatic_target
        else f"marker ID {args.marker_id}"
    )
    print(
        f"Camera {args.camera} opened; looking for {target_description} "
        f"in {args.dictionary} with {args.polarity} polarity."
    )
    print("Press Q/Esc to quit; press R to reacquire the automatic target.")

    fps = 0.0
    previous_time = time.perf_counter()
    warned_size = False
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("camera stopped returning frames")
            frame_size = (frame.shape[1], frame.shape[0])
            if (
                not warned_size
                and calibration.image_size
                and frame_size != calibration.image_size
            ):
                print(
                    "Warning: live resolution "
                    f"{frame_size} differs from calibration {calibration.image_size}",
                    file=sys.stderr,
                )
                warned_size = True

            if automatic_dictionary and active_dictionary_name is None:
                candidate_name, detector_bundle = dictionary_candidates[scan_index]
                corners, ids, _rejected, detected_polarity = detect_markers(
                    frame, detector_bundle, args.polarity
                )
                scan_index = (scan_index + 1) % len(dictionary_candidates)
                if ids is not None:
                    active_dictionary_name = candidate_name
                    print(
                        f"AUTO dictionary locked: {active_dictionary_name}; "
                        f"detected IDs {ids.reshape(-1).tolist()}; "
                        f"polarity {detected_polarity}"
                    )
            else:
                corners, ids, _rejected, detected_polarity = detect_markers(
                    frame, detector_bundle, args.polarity
                )

            displayed_dictionary = active_dictionary_name or (
                candidate_name if automatic_dictionary else args.dictionary
            )
            pose_drawn = False
            if ids is not None:
                detected_ids = [int(value) for value in ids.reshape(-1)]
                # Show every detected marker for visual feedback, but estimate
                # pose only for the selected target below.
                cv2.aruco.drawDetectedMarkers(frame, corners, ids)
                if active_target_id is None:
                    active_target_id = detected_ids[0]
                    print(f"Automatic target locked: marker ID {active_target_id}")

                target_indices = [
                    index
                    for index, detected_id in enumerate(detected_ids)
                    if detected_id == active_target_id
                ]
                if target_indices:
                    target_index = target_indices[0]
                    target_corners = corners[target_index]
                    marker_pose = solve_square_marker_pose(
                        target_corners, object_points, calibration
                    )
                    if marker_pose is not None:
                        transform_camera_palm = (
                            marker_pose.transform_camera_marker @ transform_marker_palm
                        )
                        draw_pose_overlay(
                            frame,
                            f"marker ID {active_target_id}",
                            transform_camera_palm,
                            calibration,
                            args.marker_length * 0.75,
                            marker_pose.reprojection_rmse_px,
                            fps,
                        )
                        pose_drawn = True

                if active_target_id not in detected_ids:
                    draw_tracking_status(
                        frame,
                        (
                            f"Detected IDs: {detected_ids}",
                            f"Waiting for target ID {active_target_id}",
                            f"Dictionary: {displayed_dictionary}",
                            f"Polarity: {detected_polarity}",
                        ),
                    )
                elif not pose_drawn:
                    draw_tracking_status(
                        frame,
                        (
                            f"Marker ID {active_target_id} detected",
                            "PnP pose could not be solved",
                            "Keep the whole marker visible and reduce glare",
                        ),
                    )

            if ids is None:
                if automatic_dictionary and active_dictionary_name is None:
                    status_lines = (
                        "AUTO-SCANNING MARKER DICTIONARIES",
                        f"Trying: {displayed_dictionary}",
                        f"Polarity: {args.polarity}",
                        "Keep one complete marker steady in view",
                    )
                else:
                    status_lines = (
                        "NO MARKER DETECTED",
                        f"Target ID: {'FIRST (not locked)' if active_target_id is None else active_target_id}",
                        f"Dictionary: {displayed_dictionary}",
                        f"Polarity: {args.polarity}",
                    )
                draw_tracking_status(frame, status_lines)

            now = time.perf_counter()
            instantaneous_fps = 1.0 / max(now - previous_time, 1e-9)
            fps = instantaneous_fps if fps == 0.0 else 0.9 * fps + 0.1 * instantaneous_fps
            previous_time = now
            cv2.imshow("ArUco / AprilTag Palm Pose", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                return 0
            if key == ord("r"):
                if automatic_target:
                    active_target_id = None
                    print("Automatic target released; waiting for the first detected marker.")
                if automatic_dictionary:
                    active_dictionary_name = None
                    scan_index = 0
                    print("Restarted AUTO dictionary scanning.")
    finally:
        capture.release()
        cv2.destroyAllWindows()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate real-time palm pose from an ArUco/AprilTag marker."
    )
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument(
        "--marker-length",
        type=float,
        required=True,
        help="Printed marker black-square side length in metres.",
    )
    parser.add_argument(
        "--marker-id",
        type=int,
        default=-1,
        help=(
            "Marker ID to track exclusively. The default -1 locks onto the first "
            "detected marker; press R to select a new first marker."
        ),
    )
    parser.add_argument(
        "--dictionary",
        default=FIND_GCP_DICTIONARY_NAME,
        help=(
            "Marker dictionary. Defaults to Find-GCP custom dictionary 99; "
            "also accepts DICT_3X3, 99, a predefined DICT_* name, or AUTO."
        ),
    )
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument(
        "--polarity",
        choices=("normal", "inverted", "both"),
        default="both",
        help=(
            "Marker color polarity. Use inverted when the physical marker is "
            "the color inverse of the dictionary image; both tries normal first "
            "and then an inverted frame."
        ),
    )
    parser.add_argument(
        "--palm-offset",
        type=Path,
        help="Optional JSON containing the fixed 4x4 T_marker_palm matrix.",
    )
    args = parser.parse_args(argv)
    if args.marker_length <= 0.0:
        parser.error("--marker-length must be positive")
    if args.marker_id < -1:
        parser.error("--marker-id must be -1 (automatic) or a non-negative ID")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run_camera(parse_args(argv))
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError, cv2.error) as exc:
        print(f"Palm pose error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Hand tracker rigid marker layouts."""
from __future__ import annotations

from types import MappingProxyType
from typing import Dict

import numpy as np

from telex.tracker.geometry import normalize_rigid_transform
from telex.tracker.types import HandRig, _readonly_pose_copy
from telex.tracker.vision.pose import (
    MarkerPose,
    marker_object_points_mm,
    matrix_to_rvec_tvec,
    rvec_tvec_to_matrix,
)

LEFT_MARKER_IDS = {"marker1": 5, "marker2": 18, "marker3": 1}
RIGHT_MARKER_IDS = {"marker1": 11, "marker2": 27, "marker3": 19}
MARKER_INSTALL_RZ_DEG = {
    18: -90.0,
    1: 90.0,
    11: 180.0,
    19: 180.0,
}
# Object-frame axes are physically rotated about their own +X by -45 degrees:
# X remains right, Y becomes horizontal forward, and Z becomes vertical up.
OBJECT_FRAME_AXIS_RX_DEG = -45.0
# Object-frame origin offsets expressed in the post-Rx(-45°) local axes:
# +X right, +Y forward, +Z up. Units are millimetres.
OBJECT_FRAME_ORIGIN_OFFSET_MM = {
    "left": np.array([-28.10, 42.25, -11.07], dtype=np.float64),
    "right": np.array([28.10, 42.25, -11.07], dtype=np.float64),
}
# Subsequent rotations about the translated object frame's own +Z axis.
# Left: X forward / Y left; right: X back / Y right.
OBJECT_FRAME_LOCAL_Z_ROTATION_DEG = {"left": 90.0, "right": -90.0}

MARKER1_TO_MARKER = {
    "marker1": np.eye(4, dtype=np.float64),
    "marker2": np.array(
        [
            [0.314324, 0.683014, 0.659312, 42.260],
            [-0.594695, 0.683019, -0.424056, -19.410],
            [-0.739959, -0.258798, 0.620874, -20.970],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    ),
    "marker3": np.array(
        [
            [-0.802336, 0.373917, 0.465234, 25.530],
            [-0.373917, 0.292666, -0.880075, -48.310],
            [-0.465234, -0.880075, -0.095002, -60.110],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    ),
}


def rotation_z_transform(degrees: float) -> np.ndarray:
    radians = np.deg2rad(float(degrees))
    c, s = float(np.cos(radians)), float(np.sin(radians))
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    return result


def rotation_x_transform(degrees: float) -> np.ndarray:
    radians = np.deg2rad(float(degrees))
    c, s = float(np.cos(radians)), float(np.sin(radians))
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]
    return result


def translation_transform(offset_mm: np.ndarray) -> np.ndarray:
    """Return a homogeneous translation for a finite XYZ offset in mm."""
    offset = np.asarray(offset_mm, dtype=np.float64).reshape(-1)
    if offset.shape != (3,) or not np.all(np.isfinite(offset)):
        raise ValueError("offset_mm 必须是三个有限数值")
    result = np.eye(4, dtype=np.float64)
    result[:3, 3] = offset
    return result


def rebase_marker1_as_old_marker3(
    marker1_to_marker: Dict[str, np.ndarray],
) -> Dict[str, np.ndarray]:
    """Rename CAD frames so new marker1 == old marker3 (1↔3 swap)."""
    required = {"marker1", "marker2", "marker3"}
    if set(marker1_to_marker) != required:
        raise ValueError("marker1_to_marker 必须且只能含 marker1/marker2/marker3")
    m2 = normalize_rigid_transform(marker1_to_marker["marker2"])
    m3 = normalize_rigid_transform(marker1_to_marker["marker3"])
    inv_m3 = np.linalg.inv(m3)
    return {
        "marker1": np.eye(4, dtype=np.float64),
        "marker2": normalize_rigid_transform(m2 @ inv_m3),
        "marker3": normalize_rigid_transform(inv_m3),
    }


def _build_rig(
    name: str,
    ids: Dict[str, int],
    base_marker: str,
    marker1_to_marker: Dict[str, np.ndarray],
    object_origin_offset_mm: np.ndarray,
    object_local_z_rotation_deg: float,
) -> HandRig:
    required = {"marker1", "marker2", "marker3"}
    if set(ids) != required:
        raise ValueError(f"{name} 必须且只能配置 marker1/marker2/marker3")
    normalized = {
        key: normalize_rigid_transform(value)
        for key, value in marker1_to_marker.items()
    }
    if set(normalized) != required:
        raise ValueError(f"{name} marker1_to_marker 必须含 marker1/marker2/marker3")
    marker1_to_base = normalized[base_marker]
    base_to_marker_by_name = {
        key: np.linalg.inv(marker1_to_base) @ value
        for key, value in normalized.items()
    }
    # ``object_to_marker`` maps marker-local points into object coordinates.
    # Rotating the physical object axes by -45 degrees therefore uses the
    # inverse (+45 degrees) coordinate basis transform on the left. Moving
    # the object origin by a local offset requires the opposite translation
    # in this marker-to-object transform. A subsequent active rotation of
    # the object axes about local +Z likewise uses the inverse sign here.
    object_from_nominal = (
        rotation_z_transform(-float(object_local_z_rotation_deg))
        @ translation_transform(-np.asarray(object_origin_offset_mm, dtype=np.float64))
        @ rotation_x_transform(-OBJECT_FRAME_AXIS_RX_DEG)
    )
    nominal = {
        ids[key]: object_from_nominal @ transform
        for key, transform in base_to_marker_by_name.items()
    }
    actual = {
        marker_id: transform
        @ rotation_z_transform(float(MARKER_INSTALL_RZ_DEG.get(marker_id, 0.0)))
        for marker_id, transform in nominal.items()
    }
    readonly_nominal = {}
    readonly_actual = {}
    for marker_id, transform in nominal.items():
        readonly = transform.copy()
        readonly.setflags(write=False)
        readonly_nominal[marker_id] = readonly
    for marker_id, transform in actual.items():
        readonly = transform.copy()
        readonly.setflags(write=False)
        readonly_actual[marker_id] = readonly
    return HandRig(
        name,
        MappingProxyType(dict(ids)),
        MappingProxyType(readonly_nominal),
        MappingProxyType(readonly_actual),
    )


def build_hand_rigs() -> Dict[str, HandRig]:
    required = {"marker1", "marker2", "marker3"}
    if set(MARKER1_TO_MARKER) != required:
        raise ValueError(
            "MARKER1_TO_MARKER 必须且只能配置 marker1/marker2/marker3"
        )
    all_ids = list(LEFT_MARKER_IDS.values()) + list(RIGHT_MARKER_IDS.values())
    if len(set(all_ids)) != 6:
        raise ValueError("左右手六个 marker ID 必须互不重复")
    unknown = set(MARKER_INSTALL_RZ_DEG) - set(all_ids)
    if unknown:
        raise ValueError(
            f"MARKER_INSTALL_RZ_DEG 包含未配置 ID: {sorted(unknown)}"
        )
    right_table = rebase_marker1_as_old_marker3(MARKER1_TO_MARKER)
    return {
        "left": _build_rig(
            "left",
            LEFT_MARKER_IDS,
            "marker1",
            MARKER1_TO_MARKER,
            OBJECT_FRAME_ORIGIN_OFFSET_MM["left"],
            OBJECT_FRAME_LOCAL_Z_ROTATION_DEG["left"],
        ),
        "right": _build_rig(
            "right",
            RIGHT_MARKER_IDS,
            "marker1",
            right_table,
            OBJECT_FRAME_ORIGIN_OFFSET_MM["right"],
            OBJECT_FRAME_LOCAL_Z_ROTATION_DEG["right"],
        ),
    }


def marker_points_in_object(
    marker_size_mm: float, object_to_marker: np.ndarray
) -> np.ndarray:
    try:
        size = float(marker_size_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError("marker_size_mm 必须为有限正数") from exc
    if not np.isfinite(size) or size <= 0.0:
        raise ValueError("marker_size_mm 必须为有限正数")

    transform = np.asarray(object_to_marker, dtype=np.float64)
    if transform.shape != (4, 4) or not np.all(np.isfinite(transform)):
        raise ValueError("object_to_marker 必须是有限的 4x4 矩阵")
    if not np.allclose(
        transform[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9, rtol=0.0
    ):
        raise ValueError("object_to_marker 末行必须为 [0, 0, 0, 1]")
    rotation = transform[:3, :3]
    if (
        not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0.0)
        or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-6, rtol=0.0)
    ):
        raise ValueError("object_to_marker 旋转块必须是有效的 SO(3) 旋转")

    local = marker_object_points_mm(size)
    homogeneous = np.column_stack(
        [local, np.ones((local.shape[0], 1), dtype=np.float64)]
    )
    return (transform @ homogeneous.T).T[:, :3]


def object_pose_from_marker(
    marker_pose: MarkerPose, rig: HandRig, marker_id: int
) -> MarkerPose:
    if marker_id not in rig.object_to_marker:
        raise ValueError(f"marker ID {marker_id} 未配置在 {rig.name} HandRig 中")
    object_to_marker = np.asarray(rig.object_to_marker[marker_id], dtype=np.float64)
    marker_points_in_object(1.0, object_to_marker)
    camera_to_marker = rvec_tvec_to_matrix(marker_pose.rvec, marker_pose.tvec)
    camera_to_object = camera_to_marker @ np.linalg.inv(
        object_to_marker
    )
    rvec, tvec = matrix_to_rvec_tvec(camera_to_object)
    return _readonly_pose_copy(
        MarkerPose(rvec, tvec, marker_pose.reprojection_error_px)
    )

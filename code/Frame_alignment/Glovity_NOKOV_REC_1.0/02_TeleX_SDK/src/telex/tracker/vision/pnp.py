"""Fisheye multi-marker PnP and ambiguity resolution."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from telex.tracker.types import (
    HandRig,
    PnPSelectionConfig,
    RigPoseEstimate,
    _readonly_pose_copy,
)
from telex.tracker.vision.pose import (
    MarkerPose,
    marker_object_points_mm,
    matrix_to_rvec_tvec,
    rvec_tvec_to_matrix,
)
from telex.tracker.vision.rig import marker_points_in_object, object_pose_from_marker

class _PoseSolveFailure(Exception):
    """OpenCV PnP 已运行，但没有产生可用的物理位姿。"""


def _validated_marker_corners(marker) -> np.ndarray:
    try:
        corners = np.asarray(marker.corners, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"marker ID {marker.marker_id} corners 必须是 4x2 数值"
        ) from exc
    if corners.size != 8:
        raise ValueError(f"marker ID {marker.marker_id} corners 必须是 4x2 数值")
    corners = corners.reshape(4, 2)
    if not np.all(np.isfinite(corners)):
        raise ValueError(f"marker ID {marker.marker_id} corners 必须全部有限")

    scale = max(float(np.ptp(corners[:, 0])), float(np.ptp(corners[:, 1])), 1.0)
    minimum_distance = max(1e-6, scale * 1e-9)
    distances = np.linalg.norm(
        corners[:, np.newaxis, :] - corners[np.newaxis, :, :], axis=2
    )
    np.fill_diagonal(distances, np.inf)
    centered = corners - np.mean(corners, axis=0)
    singular_values = np.linalg.svd(centered, compute_uv=False)
    x, y = corners[:, 0], corners[:, 1]
    polygon_area = 0.5 * abs(
        float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    )
    if (
        float(np.min(distances)) <= minimum_distance
        or singular_values[1] <= max(1.0, singular_values[0]) * 1e-9
        or polygon_area <= max(1e-6, scale * scale * 1e-9)
    ):
        raise ValueError(f"marker ID {marker.marker_id} corners 面积退化或共线")
    return corners


def _validated_pnp_pose(
    pose: MarkerPose, object_points: np.ndarray
) -> MarkerPose:
    points = np.asarray(object_points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.all(np.isfinite(points)):
        raise ValueError("PnP object_points 必须是有限的 Nx3 数值")
    try:
        rvec = np.asarray(pose.rvec, dtype=np.float64)
        tvec = np.asarray(pose.tvec, dtype=np.float64)
        error = float(pose.reprojection_error_px)
    except (TypeError, ValueError) as exc:
        raise _PoseSolveFailure("PnP pose 数值格式无效") from exc
    if (
        rvec.size != 3
        or tvec.size != 3
        or not np.all(np.isfinite(rvec))
        or not np.all(np.isfinite(tvec))
        or not np.isfinite(error)
        or error < 0.0
    ):
        raise _PoseSolveFailure("PnP pose 包含非有限或无效数值")
    rvec = rvec.reshape(3, 1)
    tvec = tvec.reshape(3, 1)
    try:
        rotation, _ = cv2.Rodrigues(rvec)
    except cv2.error as exc:
        raise _PoseSolveFailure("PnP rvec 无法转换为旋转矩阵") from exc
    camera_points = (rotation @ points.T + tvec).T
    if (
        not np.all(np.isfinite(camera_points))
        or np.any(camera_points[:, 2] <= 0.0)
    ):
        raise _PoseSolveFailure("PnP pose 产生非有限或非正深度")
    return _readonly_pose_copy(MarkerPose(rvec, tvec, error))


def _validated_fisheye_inputs(
    marker_size_mm: float,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray]:
    try:
        size = float(marker_size_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError("marker_size_mm 必须为有限正数") from exc
    if not np.isfinite(size) or size <= 0.0:
        raise ValueError("marker_size_mm 必须为有限正数")

    try:
        camera = np.asarray(camera_matrix, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("camera_matrix 必须是有限的 3x3 数值矩阵") from exc
    if (
        camera.shape != (3, 3)
        or not np.all(np.isfinite(camera))
        or camera[0, 0] <= 0.0
        or camera[1, 1] <= 0.0
    ):
        raise ValueError("camera_matrix 必须是焦距为正的有限 3x3 数值矩阵")

    try:
        distortion = np.asarray(dist_coeffs, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("dist_coeffs 必须包含 4 个有限鱼眼畸变系数") from exc
    if distortion.size != 4 or not np.all(np.isfinite(distortion)):
        raise ValueError("dist_coeffs 必须包含 4 个有限鱼眼畸变系数")

    return size, camera, distortion.reshape(4, 1)


@dataclass(frozen=True)
class _MarkerPoseCandidate:
    marker_id: int
    object_pose: MarkerPose
    marker_pose: MarkerPose
    reprojection_error_px: float


def _pose_rotation_distance_deg(a: MarkerPose, b: MarkerPose) -> float:
    rotation_a, _ = cv2.Rodrigues(
        np.asarray(a.rvec, dtype=np.float64).reshape(3, 1)
    )
    rotation_b, _ = cv2.Rodrigues(
        np.asarray(b.rvec, dtype=np.float64).reshape(3, 1)
    )
    relative = rotation_a.T @ rotation_b
    cosine = float(np.clip((np.trace(relative) - 1.0) * 0.5, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _pose_translation_distance_mm(a: MarkerPose, b: MarkerPose) -> float:
    return float(
        np.linalg.norm(
            np.asarray(a.tvec, dtype=np.float64).reshape(3)
            - np.asarray(b.tvec, dtype=np.float64).reshape(3)
        )
    )


def _undistort_marker_corners(
    corners: np.ndarray,
    camera: np.ndarray,
    distortion: np.ndarray,
) -> np.ndarray:
    undistorted = cv2.fisheye.undistortPoints(
        np.asarray(corners, dtype=np.float64).reshape(-1, 1, 2),
        camera,
        distortion,
        P=camera,
    ).reshape(-1, 2)
    if undistorted.shape != (4, 2) or not np.all(np.isfinite(undistorted)):
        raise _PoseSolveFailure("鱼眼去畸变产生无效 marker 角点")
    return undistorted


def _marker_reprojection_error(
    local_points: np.ndarray,
    image_points: np.ndarray,
    pose: MarkerPose,
    camera: np.ndarray,
) -> float:
    projected, _ = cv2.projectPoints(
        np.asarray(local_points, dtype=np.float64),
        pose.rvec,
        pose.tvec,
        camera,
        np.zeros((5, 1), dtype=np.float64),
    )
    residual = projected.reshape(-1, 2) - np.asarray(
        image_points, dtype=np.float64
    ).reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))


def _front_facing_candidate(
    marker_pose: MarkerPose,
    config: PnPSelectionConfig,
) -> bool:
    sign = int(config.marker_front_axis_sign)
    if sign == 0:
        return True
    rotation, _ = cv2.Rodrigues(marker_pose.rvec)
    outward_normal = rotation[:, 2] * float(sign)
    marker_position = np.asarray(marker_pose.tvec, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(marker_position))
    if norm <= 1e-9:
        return False
    direction_to_camera = -marker_position / norm
    cosine = float(np.dot(outward_normal, direction_to_camera))
    return cosine >= float(config.marker_front_min_cos)


def _single_marker_candidates(
    markers,
    rig: HandRig,
    marker_size_mm: float,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    config: PnPSelectionConfig,
) -> dict[int, list[_MarkerPoseCandidate]]:
    candidates_by_id: dict[int, list[_MarkerPoseCandidate]] = {}
    local_points = np.ascontiguousarray(
        marker_object_points_mm(marker_size_mm), dtype=np.float64
    )
    zero_dist = np.zeros((5, 1), dtype=np.float64)

    for marker in markers:
        marker_id = int(marker.marker_id)
        if marker_id not in rig.object_to_marker:
            continue
        corners = _validated_marker_corners(marker)
        try:
            undistorted = _undistort_marker_corners(
                corners, camera_matrix, dist_coeffs
            )
            generic_result = cv2.solvePnPGeneric(
                local_points,
                np.ascontiguousarray(undistorted, dtype=np.float64),
                camera_matrix,
                zero_dist,
                flags=cv2.SOLVEPNP_IPPE_SQUARE,
            )
        except cv2.error:
            continue

        if len(generic_result) < 3 or not bool(generic_result[0]):
            continue
        rvecs = generic_result[1]
        tvecs = generic_result[2]
        marker_candidates: list[_MarkerPoseCandidate] = []
        for rvec, tvec in zip(rvecs, tvecs):
            try:
                raw_marker_pose = _validated_pnp_pose(
                    MarkerPose(rvec, tvec, 0.0), local_points
                )
                error = _marker_reprojection_error(
                    local_points,
                    undistorted,
                    raw_marker_pose,
                    camera_matrix,
                )
                marker_pose = _validated_pnp_pose(
                    MarkerPose(
                        raw_marker_pose.rvec,
                        raw_marker_pose.tvec,
                        error,
                    ),
                    local_points,
                )
                if not _front_facing_candidate(marker_pose, config):
                    continue
                object_pose = object_pose_from_marker(
                    marker_pose, rig, marker_id
                )
                object_points = marker_points_in_object(
                    marker_size_mm, rig.object_to_marker[marker_id]
                )
                object_pose = _validated_pnp_pose(
                    MarkerPose(
                        object_pose.rvec,
                        object_pose.tvec,
                        error,
                    ),
                    object_points,
                )
            except (cv2.error, _PoseSolveFailure, ValueError):
                continue
            marker_candidates.append(
                _MarkerPoseCandidate(
                    marker_id=marker_id,
                    object_pose=object_pose,
                    marker_pose=marker_pose,
                    reprojection_error_px=error,
                )
            )

        marker_candidates.sort(key=lambda item: item.reprojection_error_px)
        if marker_candidates:
            candidates_by_id[marker_id] = marker_candidates

    return candidates_by_id


def _candidate_prior_score(
    candidate: _MarkerPoseCandidate,
    prior_pose: Optional[MarkerPose],
    config: PnPSelectionConfig,
) -> float:
    score = float(candidate.reprojection_error_px)
    if prior_pose is not None:
        score += (
            config.prior_rotation_weight
            * _pose_rotation_distance_deg(candidate.object_pose, prior_pose)
        )
        score += (
            config.prior_translation_weight
            * _pose_translation_distance_mm(candidate.object_pose, prior_pose)
        )
    return score


def _combination_score(
    candidates: tuple[_MarkerPoseCandidate, ...],
    prior_pose: Optional[MarkerPose],
    config: PnPSelectionConfig,
) -> Optional[float]:
    score = sum(
        _candidate_prior_score(candidate, prior_pose, config)
        for candidate in candidates
    )
    for first, second in itertools.combinations(candidates, 2):
        rotation_delta = _pose_rotation_distance_deg(
            first.object_pose, second.object_pose
        )
        translation_delta = _pose_translation_distance_mm(
            first.object_pose, second.object_pose
        )
        if (
            rotation_delta > config.pair_max_rotation_deg
            or translation_delta > config.pair_max_translation_mm
        ):
            return None
        score += config.pair_rotation_weight * rotation_delta
        score += config.pair_translation_weight * translation_delta
    return float(score)


def _select_consistent_candidates(
    candidates_by_id: Mapping[int, list[_MarkerPoseCandidate]],
    prior_pose: Optional[MarkerPose],
    config: PnPSelectionConfig,
) -> tuple[_MarkerPoseCandidate, ...]:
    marker_ids = tuple(sorted(candidates_by_id))
    for subset_size in range(len(marker_ids), 0, -1):
        best: Optional[tuple[float, tuple[_MarkerPoseCandidate, ...]]] = None
        for subset_ids in itertools.combinations(marker_ids, subset_size):
            candidate_lists = [candidates_by_id[item] for item in subset_ids]
            for combination in itertools.product(*candidate_lists):
                normalized = tuple(combination)
                score = _combination_score(normalized, prior_pose, config)
                if score is None:
                    continue
                if best is None or score < best[0]:
                    best = (score, normalized)
        if best is not None:
            return best[1]
    return ()


def _joint_pose_from_selected_markers(
    selected: tuple[_MarkerPoseCandidate, ...],
    corners_by_id: Mapping[int, np.ndarray],
    object_points_by_id: Mapping[int, np.ndarray],
    camera: np.ndarray,
    distortion: np.ndarray,
) -> tuple[Optional[MarkerPose], dict[int, float]]:
    if len(selected) < 2:
        return None, {}
    selected_ids = tuple(candidate.marker_id for candidate in selected)
    object_points = np.vstack(
        [object_points_by_id[marker_id] for marker_id in selected_ids]
    ).astype(np.float64)
    image_points = np.vstack(
        [corners_by_id[marker_id] for marker_id in selected_ids]
    ).astype(np.float64)
    undistorted = cv2.fisheye.undistortPoints(
        image_points.reshape(-1, 1, 2),
        camera,
        distortion,
        P=camera,
    ).reshape(-1, 2)
    if not np.all(np.isfinite(undistorted)):
        raise _PoseSolveFailure("联合 PnP 去畸变坐标无效")

    # The best consensus candidate provides a branch-stable initial guess.
    initial = min(
        selected,
        key=lambda candidate: candidate.reprojection_error_px,
    ).object_pose
    rvec = np.asarray(initial.rvec, dtype=np.float64).reshape(3, 1).copy()
    tvec = np.asarray(initial.tvec, dtype=np.float64).reshape(3, 1).copy()
    ok, rvec, tvec = cv2.solvePnP(
        np.ascontiguousarray(object_points),
        np.ascontiguousarray(undistorted),
        camera,
        np.zeros((5, 1), dtype=np.float64),
        rvec,
        tvec,
        useExtrinsicGuess=True,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        raise _PoseSolveFailure("带初值联合 solvePnP 求解失败")
    pose = _validated_pnp_pose(MarkerPose(rvec, tvec, 0.0), object_points)

    per_marker_error: dict[int, float] = {}
    all_residuals = []
    offset = 0
    for marker_id in selected_ids:
        count = object_points_by_id[marker_id].shape[0]
        marker_object_points = object_points[offset : offset + count]
        marker_image_points = undistorted[offset : offset + count]
        projected, _ = cv2.projectPoints(
            marker_object_points,
            pose.rvec,
            pose.tvec,
            camera,
            np.zeros((5, 1), dtype=np.float64),
        )
        residual = projected.reshape(-1, 2) - marker_image_points
        per_marker_error[marker_id] = float(
            np.sqrt(np.mean(np.sum(residual * residual, axis=1)))
        )
        all_residuals.append(residual)
        offset += count
    stacked = np.vstack(all_residuals)
    total_error = float(
        np.sqrt(np.mean(np.sum(stacked * stacked, axis=1)))
    )
    pose = _validated_pnp_pose(
        MarkerPose(pose.rvec, pose.tvec, total_error), object_points
    )
    return pose, per_marker_error


def _estimate_confidence(
    used_count: int,
    reprojection_error_px: float,
) -> float:
    count_factor = min(1.0, 0.45 + 0.25 * max(1, int(used_count)))
    error_factor = 1.0 / (1.0 + max(0.0, float(reprojection_error_px)))
    return float(np.clip(count_factor * error_factor, 0.0, 1.0))


def estimate_rig_pose_fisheye(
    markers,
    rig: HandRig,
    marker_size_mm: float,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    *,
    prior_pose: Optional[MarkerPose] = None,
    config: Optional[PnPSelectionConfig] = None,
) -> RigPoseEstimate:
    size, camera, distortion = _validated_fisheye_inputs(
        marker_size_mm, camera_matrix, dist_coeffs
    )
    selection_config = config or PnPSelectionConfig()

    configured_by_id = {}
    for marker in markers:
        if marker.marker_id in rig.object_to_marker:
            marker_id = int(marker.marker_id)
            if marker_id in configured_by_id:
                raise ValueError(f"输入包含重复 marker ID: {marker_id}")
            configured_by_id[marker_id] = marker
    visible_ids = tuple(sorted(configured_by_id))
    configured = [configured_by_id[marker_id] for marker_id in visible_ids]
    if not configured:
        return RigPoseEstimate(None, (), "none")

    corners_by_id = {
        int(marker.marker_id): _validated_marker_corners(marker)
        for marker in configured
    }
    object_points_by_id = {
        int(marker.marker_id): marker_points_in_object(
            size, rig.object_to_marker[marker.marker_id]
        )
        for marker in configured
    }
    candidates_by_id = _single_marker_candidates(
        configured,
        rig,
        size,
        camera,
        distortion,
        selection_config,
    )
    selected = _select_consistent_candidates(
        candidates_by_id, prior_pose, selection_config
    )
    if not selected:
        return RigPoseEstimate(None, visible_ids, "none")

    if len(selected) == 1:
        candidate = selected[0]
        mode = "single" if len(visible_ids) == 1 else "fallback-single"
        confidence = _estimate_confidence(
            1, candidate.reprojection_error_px
        )
        return RigPoseEstimate(
            candidate.object_pose,
            visible_ids,
            mode,
            used_ids=(candidate.marker_id,),
            confidence=confidence,
        )

    try:
        joint_pose, per_marker_error = _joint_pose_from_selected_markers(
            selected,
            corners_by_id,
            object_points_by_id,
            camera,
            distortion,
        )
    except (cv2.error, _PoseSolveFailure):
        joint_pose, per_marker_error = None, {}

    if joint_pose is not None and per_marker_error:
        worst_id = max(per_marker_error, key=per_marker_error.get)
        worst_error = per_marker_error[worst_id]
        if worst_error > selection_config.marker_max_reprojection_px:
            remaining = tuple(
                candidate
                for candidate in selected
                if candidate.marker_id != worst_id
            )
            if len(remaining) >= 2:
                try:
                    joint_pose, per_marker_error = _joint_pose_from_selected_markers(
                        remaining,
                        corners_by_id,
                        object_points_by_id,
                        camera,
                        distortion,
                    )
                    selected = remaining
                except (cv2.error, _PoseSolveFailure):
                    joint_pose = None
            else:
                joint_pose = None
                selected = remaining

    if joint_pose is None:
        fallback = min(
            selected if selected else tuple(
                candidate
                for candidates in candidates_by_id.values()
                for candidate in candidates
            ),
            key=lambda candidate: _candidate_prior_score(
                candidate, prior_pose, selection_config
            ),
        )
        return RigPoseEstimate(
            fallback.object_pose,
            visible_ids,
            "fallback-single" if len(visible_ids) >= 2 else "single",
            used_ids=(fallback.marker_id,),
            confidence=_estimate_confidence(
                1, fallback.reprojection_error_px
            ),
        )

    used_ids = tuple(candidate.marker_id for candidate in selected)
    return RigPoseEstimate(
        joint_pose,
        visible_ids,
        "multi",
        used_ids=used_ids,
        confidence=_estimate_confidence(
            len(used_ids), joint_pose.reprojection_error_px
        ),
    )


def markers_for_rig(markers, rig: HandRig):
    configured_ids = set(rig.object_to_marker)
    return [
        marker for marker in markers
        if marker.marker_id in configured_ids
    ]


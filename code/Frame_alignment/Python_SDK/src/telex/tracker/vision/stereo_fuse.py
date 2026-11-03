"""Left/right stereo pose fusion for tracker estimates."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from telex.tracker.geometry import (
    _blend_pose_estimates,
    validate_rigid_transform,
)
from telex.tracker.types import (
    PnPSelectionConfig,
    RigPoseEstimate,
    TrackerVisionMemory,
)
from telex.tracker.vision.pose import (
    FusedPose,
    MarkerPose,
    transform_pose,
)
from telex.tracker.vision.pnp import (
    _pose_rotation_distance_deg,
    _pose_translation_distance_mm,
)

def _transform_rig_estimate(
    estimate: RigPoseEstimate,
    transform: np.ndarray,
) -> RigPoseEstimate:
    if estimate.pose is None:
        return estimate
    return RigPoseEstimate(
        transform_pose(estimate.pose, transform),
        estimate.visible_ids,
        estimate.mode,
        used_ids=estimate.used_ids,
        confidence=estimate.confidence,
    )


def _estimate_quality(
    estimate: RigPoseEstimate,
    prior_pose: Optional[MarkerPose],
) -> float:
    if estimate.pose is None:
        return -math.inf
    quality = estimate.confidence / (
        0.25 + estimate.pose.reprojection_error_px
    )
    if prior_pose is not None:
        quality /= 1.0 + 0.01 * _pose_rotation_distance_deg(
            estimate.pose, prior_pose
        )
        quality /= 1.0 + 0.005 * _pose_translation_distance_mm(
            estimate.pose, prior_pose
        )
    return float(quality)


def _fuse_stereo_estimates(
    left: RigPoseEstimate,
    right_in_left: RigPoseEstimate,
    prior_pose: Optional[MarkerPose],
    config: PnPSelectionConfig,
    *,
    memory: Optional[TrackerVisionMemory] = None,
    hysteresis_frames: int = 4,
) -> FusedPose:
    if left.pose is None and right_in_left.pose is None:
        if memory is not None:
            memory.preferred_vision_source = None
            memory.vision_source_hold_frames = 0
        return FusedPose(None, "none")
    if right_in_left.pose is None:
        candidate = FusedPose(left.pose, "left")
    elif left.pose is None:
        candidate = FusedPose(right_in_left.pose, "right")
    else:
        rotation_delta = _pose_rotation_distance_deg(
            left.pose, right_in_left.pose
        )
        translation_delta = _pose_translation_distance_mm(
            left.pose, right_in_left.pose
        )
        if (
            rotation_delta <= config.stereo_max_rotation_deg
            and translation_delta <= config.stereo_max_translation_mm
        ):
            candidate = FusedPose(
                _blend_pose_estimates(left, right_in_left), "stereo"
            )
        else:
            left_quality = _estimate_quality(left, prior_pose)
            right_quality = _estimate_quality(right_in_left, prior_pose)
            if left_quality >= right_quality:
                candidate = FusedPose(left.pose, "left")
            else:
                candidate = FusedPose(right_in_left.pose, "right")

    if memory is None or hysteresis_frames <= 1:
        return candidate

    preferred = memory.preferred_vision_source
    if preferred is None or preferred == "none":
        memory.preferred_vision_source = candidate.source
        memory.vision_source_hold_frames = 0
        return candidate

    if candidate.source == preferred:
        memory.vision_source_hold_frames = 0
        return candidate

    # 候选与偏好不一致：尽量沿用旧 source（本帧仍可解时），连续 N 帧才切换
    sticky: Optional[FusedPose] = None
    if preferred == "stereo" and left.pose is not None and right_in_left.pose is not None:
        rotation_delta = _pose_rotation_distance_deg(
            left.pose, right_in_left.pose
        )
        translation_delta = _pose_translation_distance_mm(
            left.pose, right_in_left.pose
        )
        if (
            rotation_delta <= config.stereo_max_rotation_deg * 1.25
            and translation_delta <= config.stereo_max_translation_mm * 1.25
        ):
            sticky = FusedPose(
                _blend_pose_estimates(left, right_in_left), "stereo"
            )
    elif preferred == "left" and left.pose is not None:
        sticky = FusedPose(left.pose, "left")
    elif preferred == "right" and right_in_left.pose is not None:
        sticky = FusedPose(right_in_left.pose, "right")

    memory.vision_source_hold_frames += 1
    if memory.vision_source_hold_frames < int(hysteresis_frames) and sticky is not None:
        return sticky

    memory.preferred_vision_source = candidate.source
    memory.vision_source_hold_frames = 0
    return candidate


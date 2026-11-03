"""Per-hand outlier filtering and tracker output assembly."""
from __future__ import annotations

import time
from typing import Dict, Mapping, Optional, Tuple

import numpy as np

from telex.tracker.types import (
    HandRig,
    TrackerOutlierState,
    TrackerOutput,
    TrackerVision,
)
from telex.tracker.vision.pose import FusedPose, MarkerPose, PoseOutlierFilter

def build_tracker_outlier_filters(
    rigs: Mapping[str, HandRig],
    *,
    max_rotation_deg: float,
    max_translation_mm: float,
    max_hold_frames: int,
) -> Dict[str, PoseOutlierFilter]:
    return {
        hand: PoseOutlierFilter(
            max_rotation_deg=max_rotation_deg,
            max_translation_mm=max_translation_mm,
            max_hold_frames=max_hold_frames,
        )
        for hand in rigs
    }


def update_tracker_outlier_vision(
    vision: TrackerVision,
    outlier_filter: PoseOutlierFilter,
    state: TrackerOutlierState,
) -> Tuple[TrackerVision, TrackerOutlierState]:
    filtered_pose, is_outlier = outlier_filter.check(vision.fused.pose)
    if is_outlier:
        if state.last_accepted is None or filtered_pose is None:
            raise RuntimeError("离群过滤器拒绝位姿时缺少上一接受 TrackerVision")
        next_hold_frames = state.hold_frames + 1
        held = TrackerVision(
            FusedPose(filtered_pose, state.last_accepted.fused.source),
            state.last_accepted.visible_ids,
            state.last_accepted.mode,
            outlier_held=True,
            outlier_hold_frames=next_hold_frames,
        )
        return held, TrackerOutlierState(
            state.last_accepted, next_hold_frames
        )

    if filtered_pose is None:
        current = TrackerVision(
            FusedPose(None, "none"),
            vision.visible_ids,
            "none",
        )
        return current, TrackerOutlierState()

    accepted = TrackerVision(
        FusedPose(filtered_pose, vision.fused.source),
        vision.visible_ids,
        vision.mode,
    )
    return accepted, TrackerOutlierState(accepted, 0)


def build_tracker_outputs(
    vision_by_hand: Mapping[str, TrackerVision],
    *,
    imu_fusions: Optional[Mapping[str, object]] = None,
    imu_subscribers: Optional[Mapping[str, object]] = None,
    debug_imu: bool = False,
    now_s: Optional[float] = None,
) -> Dict[str, TrackerOutput]:
    fusions = {} if imu_fusions is None else imu_fusions
    subscribers = {} if imu_subscribers is None else imu_subscribers
    update_time = time.monotonic() if now_s is None else float(now_s)
    outputs = {}

    for hand, vision in vision_by_hand.items():
        pose = vision.fused.pose
        source = "vision" if pose is not None else "none"
        imu_debug = None
        if hand in fusions:
            if hand not in subscribers:
                raise ValueError(f"{hand} IMU fusion 缺少对应 subscriber")
            fusion = fusions[hand]
            visual_pose_for_imu = None if vision.outlier_held else pose
            active_count = 0 if vision.outlier_held else len(vision.visible_ids)
            try:
                fusion.set_active_marker_count(active_count)
                result = fusion.update(
                    visual_pose_for_imu,
                    subscribers[hand].latest(),
                    now_s=update_time,
                )
                if vision.outlier_held and result.pose is None:
                    pose = vision.fused.pose
                    source = "vision"
                else:
                    pose = result.pose
                    source = result.source
                imu_debug = result.imu if debug_imu else None
            except (
                ValueError,
                np.linalg.LinAlgError,
                FloatingPointError,
                OverflowError,
            ) as exc:
                pose = vision.fused.pose
                source = "vision" if pose is not None else "none"
                if debug_imu:
                    imu_debug = {
                        "present": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }

        outputs[hand] = TrackerOutput(
            pose=pose,
            source=source,
            vision_source=vision.fused.source,
            visible_ids=vision.visible_ids,
            mode=vision.mode,
            imu_debug=imu_debug,
            outlier_held=vision.outlier_held,
            outlier_hold_frames=vision.outlier_hold_frames,
        )
    return outputs


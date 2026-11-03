"""Optional JSONL frame recording."""
from __future__ import annotations

import json
import math
from typing import Optional

import numpy as np

from telex.tracker.types import TrackerOutput, _normalize_debug_value
from telex.tracker.vision.pose import MarkerPose
from telex.tracker.viz.draw import _pose_distance_m

def _rounded_finite_list(values, digits: int) -> list[float]:
    result = [round(float(value), digits) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("pose 派生序列必须为有限数值")
    return result


def pose_to_record(pose: Optional[MarkerPose]) -> Optional[dict]:
    if pose is None:
        return None
    t_m = _rounded_finite_list(
        (
            float(value) / 1000.0
            for value in np.asarray(pose.tvec, dtype=np.float64).reshape(3)
        ),
        6,
    )
    rvec = _rounded_finite_list(
        np.asarray(pose.rvec, dtype=np.float64).reshape(3), 6
    )
    abc_deg = _rounded_finite_list(
        euler_zyx_deg_from_rvec(pose.rvec), 4
    )
    return {
        "t_m": t_m,
        "rvec": rvec,
        "abc_deg": abc_deg,
        "reprojection_error_px": round(float(pose.reprojection_error_px), 4),
        "distance_m": round(_pose_distance_m(pose), 6),
    }


def tracker_to_record(output: Optional[TrackerOutput]) -> Optional[dict]:
    if output is None or output.pose is None:
        return None
    record = {
        "source": output.source,
        "vision_source": output.vision_source,
        "visible_ids": list(output.visible_ids),
        "visible_count": len(output.visible_ids),
        "mode": output.mode,
        "outlier_held": output.outlier_held,
        "outlier_hold_frames": output.outlier_hold_frames,
        "pose": pose_to_record(output.pose),
    }
    if output.imu_debug is not None:
        record["imu"] = _normalize_debug_value(output.imu_debug, frozen=False)
    return record


def build_frame_record(
    timestamp: float,
    left_output: Optional[TrackerOutput],
    right_output: Optional[TrackerOutput],
) -> dict:
    try:
        normalized_timestamp = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp 必须为有限数值") from exc
    if not np.isfinite(normalized_timestamp):
        raise ValueError("timestamp 必须为有限数值")
    return {
        "type": "dual_hand_multi_marker_stereo_pose",
        "ts": normalized_timestamp,
        "left_tracker": tracker_to_record(left_output),
        "right_tracker": tracker_to_record(right_output),
    }


def write_frame_record(
    jsonl_handle,
    timestamp: float,
    left_output: Optional[TrackerOutput],
    right_output: Optional[TrackerOutput],
) -> None:
    record = build_frame_record(timestamp, left_output, right_output)
    jsonl_handle.write(
        json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n"
    )
    jsonl_handle.flush()


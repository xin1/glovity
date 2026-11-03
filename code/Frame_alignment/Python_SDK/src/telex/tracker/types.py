"""Tracker / vision output dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Literal, Mapping, Optional, Tuple

import numpy as np

from telex.tracker.vision.pose import FusedPose, MarkerPose

@dataclass(frozen=True)
class HandRig:
    name: str
    marker_ids: Mapping[str, int]
    nominal_object_to_marker: Mapping[int, np.ndarray]
    object_to_marker: Mapping[int, np.ndarray]


def _readonly_pose_copy(pose: MarkerPose) -> MarkerPose:
    try:
        rvec = np.asarray(pose.rvec, dtype=np.float64)
        tvec = np.asarray(pose.tvec, dtype=np.float64)
        reprojection_error = float(pose.reprojection_error_px)
    except (TypeError, ValueError) as exc:
        raise ValueError("pose 的 rvec/tvec/reprojection_error 必须为有限数值") from exc
    if rvec.size != 3 or tvec.size != 3:
        raise ValueError("pose 的 rvec/tvec 必须各包含 3 个数值")
    if (
        not np.all(np.isfinite(rvec))
        or not np.all(np.isfinite(tvec))
        or not np.isfinite(reprojection_error)
    ):
        raise ValueError("pose 的 rvec/tvec/reprojection_error 必须全部有限")
    rvec = rvec.reshape(3, 1).copy()
    tvec = tvec.reshape(3, 1).copy()
    rvec.setflags(write=False)
    tvec.setflags(write=False)
    return MarkerPose(rvec, tvec, reprojection_error)


_RIG_POSE_MODES = {"none", "single", "multi", "fallback-single"}
_VISION_SOURCES = {"left", "right", "stereo", "none"}
_TRACKER_SOURCES = {"vision", "vision_imu", "imu_hold", "none"}


def _normalized_visible_ids(visible_ids) -> Tuple[int, ...]:
    return tuple(sorted({int(marker_id) for marker_id in visible_ids}))


def _normalize_debug_value(value, *, frozen: bool):
    if isinstance(value, np.generic):
        if isinstance(value, np.bool_):
            value = bool(value)
        elif isinstance(value, np.integer):
            value = int(value)
        elif isinstance(value, np.floating):
            value = float(value)
        elif isinstance(value, np.str_):
            value = str(value)
        else:
            scalar = value.item()
            if isinstance(scalar, np.generic):
                raise TypeError(
                    f"imu_debug 包含不支持的 JSON 类型: {type(value).__name__}"
                )
            value = scalar
    if isinstance(value, np.ndarray):
        return _normalize_debug_value(value.tolist(), frozen=frozen)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not np.isfinite(value):
            raise ValueError("imu_debug 中的浮点数必须有限")
        return value
    if isinstance(value, Mapping):
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("imu_debug 的 JSON 对象键必须是字符串")
            normalized[key] = _normalize_debug_value(item, frozen=frozen)
        return MappingProxyType(normalized) if frozen else normalized
    if isinstance(value, (tuple, list)):
        normalized = [
            _normalize_debug_value(item, frozen=frozen) for item in value
        ]
        return tuple(normalized) if frozen else normalized
    raise TypeError(
        f"imu_debug 包含不支持的 JSON 类型: {type(value).__name__}"
    )


@dataclass(frozen=True)
class PnPSelectionConfig:
    pair_max_rotation_deg: float = 35.0
    pair_max_translation_mm: float = 45.0
    marker_max_reprojection_px: float = 4.0
    prior_rotation_weight: float = 0.05
    prior_translation_weight: float = 0.02
    pair_rotation_weight: float = 0.03
    pair_translation_weight: float = 0.01
    stereo_max_rotation_deg: float = 12.0
    stereo_max_translation_mm: float = 25.0
    marker_front_axis_sign: int = 0
    marker_front_min_cos: float = 0.0

    def __post_init__(self) -> None:
        positive = {
            "pair_max_rotation_deg": self.pair_max_rotation_deg,
            "pair_max_translation_mm": self.pair_max_translation_mm,
            "marker_max_reprojection_px": self.marker_max_reprojection_px,
            "stereo_max_rotation_deg": self.stereo_max_rotation_deg,
            "stereo_max_translation_mm": self.stereo_max_translation_mm,
        }
        for name, value in positive.items():
            if not np.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} 必须是有限正数")
        non_negative = {
            "prior_rotation_weight": self.prior_rotation_weight,
            "prior_translation_weight": self.prior_translation_weight,
            "pair_rotation_weight": self.pair_rotation_weight,
            "pair_translation_weight": self.pair_translation_weight,
        }
        for name, value in non_negative.items():
            if not np.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} 必须是有限非负数")
        if int(self.marker_front_axis_sign) not in {-1, 0, 1}:
            raise ValueError("marker_front_axis_sign 必须为 -1/0/1")
        if not -1.0 <= float(self.marker_front_min_cos) <= 1.0:
            raise ValueError("marker_front_min_cos 必须在 [-1, 1]")


@dataclass
class TrackerVisionMemory:
    left_camera_pose: Optional[MarkerPose] = None
    right_camera_pose: Optional[MarkerPose] = None
    fused_left_pose: Optional[MarkerPose] = None
    preferred_vision_source: Optional[str] = None
    vision_source_hold_frames: int = 0


@dataclass(frozen=True)
class RigPoseEstimate:
    pose: Optional[MarkerPose]
    visible_ids: Tuple[int, ...]
    mode: Literal["none", "single", "multi", "fallback-single"]
    used_ids: Tuple[int, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in _RIG_POSE_MODES:
            raise ValueError(f"未知 RigPoseEstimate mode: {self.mode}")
        visible_ids = _normalized_visible_ids(self.visible_ids)
        used_ids = _normalized_visible_ids(self.used_ids)
        object.__setattr__(self, "visible_ids", visible_ids)
        object.__setattr__(self, "used_ids", used_ids)
        if not set(used_ids).issubset(visible_ids):
            raise ValueError("used_ids 必须是 visible_ids 的子集")
        confidence = float(self.confidence)
        if not np.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("RigPoseEstimate confidence 必须在 [0, 1]")
        object.__setattr__(self, "confidence", confidence)
        pose_present = self.pose is not None
        if self.mode == "none":
            if pose_present or used_ids:
                raise ValueError("mode=none 时 pose/used_ids 必须为空")
        else:
            if not pose_present or not used_ids:
                raise ValueError("有效 RigPoseEstimate 必须有 pose 和 used_ids")
            if self.mode == "multi" and len(used_ids) < 2:
                raise ValueError("mode=multi 至少需要两个 used_ids")
            if self.mode in {"single", "fallback-single"} and len(used_ids) != 1:
                raise ValueError(f"mode={self.mode} 必须恰好使用一个 marker")
            if self.mode == "fallback-single" and len(visible_ids) < 2:
                raise ValueError("fallback-single 需要至少两个当前帧可见 marker")
        if self.pose is not None:
            object.__setattr__(self, "pose", _readonly_pose_copy(self.pose))


@dataclass(frozen=True)
class TrackerOutput:
    pose: Optional[MarkerPose]
    source: str
    vision_source: str
    visible_ids: Tuple[int, ...]
    mode: Literal["none", "single", "multi", "fallback-single"]
    imu_debug: Optional[dict]
    outlier_held: bool = False
    outlier_hold_frames: int = 0

    def __post_init__(self) -> None:
        if self.mode not in _RIG_POSE_MODES:
            raise ValueError(f"未知 TrackerOutput mode: {self.mode}")
        if self.source not in _TRACKER_SOURCES:
            raise ValueError(f"未知 TrackerOutput source: {self.source}")
        if self.vision_source not in _VISION_SOURCES:
            raise ValueError(
                f"未知 TrackerOutput vision_source: {self.vision_source}"
            )
        object.__setattr__(
            self, "visible_ids", _normalized_visible_ids(self.visible_ids)
        )
        hold_frames = int(self.outlier_hold_frames)
        if hold_frames < 0 or bool(self.outlier_held) != (hold_frames > 0):
            raise ValueError(
                "TrackerOutput outlier_held 与 outlier_hold_frames 不一致"
            )
        object.__setattr__(self, "outlier_held", bool(self.outlier_held))
        object.__setattr__(self, "outlier_hold_frames", hold_frames)
        pose_present = self.pose is not None
        if (self.source == "none") != (not pose_present):
            raise ValueError("TrackerOutput source 与 pose 状态不一致")
        if self.source == "imu_hold" and not self.outlier_held and (
            self.mode != "none" or self.vision_source != "none"
        ):
            raise ValueError(
                "source=imu_hold 时 mode 和 vision_source 必须均为 none"
            )
        if self.outlier_held and not pose_present:
            raise ValueError("outlier_held=True 时 pose 不能为空")
        # Task 5 纯视觉初始化约定：无视觉 pose 时 source="none"；
        # 只有当前帧存在视觉 pose 时才初始化为 source="vision"。
        if self.mode == "none":
            if self.vision_source != "none":
                raise ValueError("mode=none 时 vision_source 必须为 none")
        else:
            if self.vision_source == "none":
                raise ValueError("视觉 mode 非 none 时必须有 vision_source")
            if not self.visible_ids:
                raise ValueError("视觉 mode 非 none 时 visible_ids 不能为空")
            if self.mode in {"multi", "fallback-single"} and len(self.visible_ids) < 2:
                raise ValueError(f"mode={self.mode} 至少需要两个 visible_ids")
            if not pose_present:
                raise ValueError("视觉 mode 非 none 时 pose 不能为空")
        if self.source in {"vision", "vision_imu"} and self.mode == "none":
            raise ValueError(f"source={self.source} 时必须有视觉 pose")
        if self.pose is not None:
            object.__setattr__(self, "pose", _readonly_pose_copy(self.pose))
        if self.imu_debug is not None:
            if not isinstance(self.imu_debug, Mapping):
                raise TypeError("imu_debug 必须是 JSON mapping")
            object.__setattr__(
                self,
                "imu_debug",
                _normalize_debug_value(self.imu_debug, frozen=True),
            )


@dataclass(frozen=True)
class TrackerVision:
    fused: FusedPose
    visible_ids: Tuple[int, ...]
    mode: Literal["none", "single", "multi", "fallback-single"]
    outlier_held: bool = False
    outlier_hold_frames: int = 0

    def __post_init__(self) -> None:
        if self.mode not in _RIG_POSE_MODES:
            raise ValueError(f"未知 TrackerVision mode: {self.mode}")
        if self.fused.source not in _VISION_SOURCES:
            raise ValueError(f"未知 TrackerVision fused.source: {self.fused.source}")
        object.__setattr__(
            self, "visible_ids", _normalized_visible_ids(self.visible_ids)
        )
        hold_frames = int(self.outlier_hold_frames)
        if hold_frames < 0 or bool(self.outlier_held) != (hold_frames > 0):
            raise ValueError(
                "TrackerVision outlier_held 与 outlier_hold_frames 不一致"
            )
        object.__setattr__(self, "outlier_held", bool(self.outlier_held))
        object.__setattr__(self, "outlier_hold_frames", hold_frames)
        pose_present = self.fused.pose is not None
        if (self.fused.source == "none") != (not pose_present):
            raise ValueError("TrackerVision fused.source 与 pose 状态不一致")
        if self.mode == "none":
            if pose_present:
                raise ValueError("TrackerVision mode=none 时 pose 必须为空")
        else:
            if not pose_present:
                raise ValueError("TrackerVision 视觉 mode 非 none 时 pose 不能为空")
            if not self.visible_ids:
                raise ValueError(
                    "TrackerVision 视觉 mode 非 none 时 visible_ids 不能为空"
                )
            if self.mode in {"multi", "fallback-single"} and len(self.visible_ids) < 2:
                raise ValueError(
                    f"TrackerVision mode={self.mode} 至少需要两个 visible_ids"
                )
        if self.outlier_held and not pose_present:
            raise ValueError("outlier_held=True 时 pose 不能为空")
        if self.fused.pose is not None:
            object.__setattr__(
                self,
                "fused",
                FusedPose(
                    _readonly_pose_copy(self.fused.pose),
                    self.fused.source,
                ),
            )


@dataclass(frozen=True)
class TrackerOutlierState:
    last_accepted: Optional[TrackerVision] = None
    hold_frames: int = 0

    def __post_init__(self) -> None:
        hold_frames = int(self.hold_frames)
        if hold_frames < 0:
            raise ValueError("TrackerOutlierState hold_frames 不能为负数")
        if self.last_accepted is None and hold_frames != 0:
            raise ValueError(
                "TrackerOutlierState 无 last_accepted 时 hold_frames 必须为 0"
            )
        object.__setattr__(self, "hold_frames", hold_frames)


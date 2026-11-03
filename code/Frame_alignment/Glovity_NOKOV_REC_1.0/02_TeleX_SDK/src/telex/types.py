"""TeleX public result types and exceptions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np

from telex.glove.labels import IMU_LABELS, JOINT_LABELS
from telex.glove.types import ImuData
from telex.tracker.vision.pose import MarkerPose

PoseFrame = Literal["left", "mid"]
HandSide = Literal["both", "left", "right"]


class TeleXError(Exception):
    """TeleX 基础异常。"""


class TeleXStrictError(TeleXError):
    """strict=True 时启用侧缺少必需数据。"""


@dataclass
class HandState:
    """单手状态：tracker 6D + 腕部系手指数据。"""

    pose: Optional[MarkerPose]
    imus: Optional[list[ImuData]]
    joints: Optional[np.ndarray]
    available: bool
    battery_percent: Optional[int] = None  # Receiver_RX 帧内 bat，0–100；无手套时 None
    frame_seq: Optional[int] = None
    glove_timestamp_us: Optional[int] = None
    glove_host_receive_s: Optional[float] = None
    recording: Optional[bool] = None
    imu_fail_mask: Optional[int] = None
    wrist_gyro_valid: Optional[bool] = None
    wrist_gyro_dps: Optional[tuple[float, float, float]] = None
    protocol: Optional[str] = None
    protocol_version: Optional[int] = None
    has_device_timestamp: Optional[bool] = None
    has_recording_flag: Optional[bool] = None
    has_fail_mask: Optional[bool] = None


@dataclass
class FrameResult:
    """一次 ``TeleXSession.read()`` 的双手结果。"""

    timestamp_s: float
    frame: PoseFrame
    left: Optional[HandState]
    right: Optional[HandState]
    left_image: Optional[np.ndarray]
    right_image: Optional[np.ndarray]


__all__ = [
    "FrameResult",
    "HandSide",
    "HandState",
    "IMU_LABELS",
    "JOINT_LABELS",
    "PoseFrame",
    "TeleXError",
    "TeleXStrictError",
]

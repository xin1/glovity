"""Glove IMU types (Receiver_RX / 11 IMU)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ImuData:
    """单个 IMU 传感器的数据。"""

    quat_w: float
    quat_x: float
    quat_y: float
    quat_z: float
    acc_x: float = 0.0
    acc_y: float = 0.0
    acc_z: float = 0.0
    mag_x: float = 0.0
    mag_y: float = 0.0
    mag_z: float = 0.0
    calib_status: int = 0
    valid: bool = True


@dataclass
class GloveFrame:
    """一帧完整的手套数据。

    Attributes:
        timestamp: 兼容字段，手套设备时间戳（毫秒）
        timestamp_us: 手套在本轮采集开始时的本地时间戳（微秒）
        host_receive_s: 主机收到完整串口帧时的单调时间（秒）
        imu_data: 11 个 IMU（0=腕部，其后每指近端/远端各一）
        frame_id: 帧序号
        hand: "left" 或 "right"
    """

    timestamp: int
    imu_data: List[ImuData]
    frame_id: int = 0
    hand: str = "left"
    timestamp_us: int = 0
    host_receive_s: float = 0.0
    recording: bool = False
    battery_percent: int = 0
    imu_fail_mask: int = 0
    wrist_gyro_valid: bool = False
    wrist_gyro_dps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    protocol: str = "v2-180"
    protocol_version: int = 2
    has_device_timestamp: bool = True
    has_recording_flag: bool = True
    has_fail_mask: bool = True

    @property
    def frame_seq(self) -> int:
        """V2 名称；``frame_id`` 作为旧 API 兼容字段保留。"""
        return self.frame_id

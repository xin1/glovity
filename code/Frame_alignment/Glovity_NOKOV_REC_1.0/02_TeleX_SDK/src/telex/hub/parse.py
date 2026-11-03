"""Glovity 180-byte V2 frame parsing for the shared hub."""
from __future__ import annotations

import struct
import time
from typing import List, Optional, Tuple

from telex.glove.types import GloveFrame, ImuData
from telex.tracker.imu.receiver import (
    CRC_OFFSET, DEFAULT_BAUD, DEFAULT_PORT, EXTRA_OFFSET, FLAG_RECORDING,
    FLAG_WRIST_GYRO_VALID, FRAME_SIZE, GYRO_SCALE, HAND_IDS, HAND_LEFT,
    HAND_NAMES, HAND_RIGHT, HEADER_SIZE, IMU_COUNT, IMU_RAW_SIZE, MAGIC,
    LEGACY_CAPTURE_FRAME_SIZE, LEGACY_FRAME_SIZE, PROTOCOL_VERSION, QUAT_SCALE,
    WirelessImuFrame, crc16_ccitt_false,
    next_receiver_frame_candidate, normalize_hand, parse_receiver_frame,
    xor_check,
)

__all__ = [
    "DEFAULT_BAUD", "DEFAULT_PORT", "FRAME_SIZE", "HAND_IDS", "HAND_LEFT",
    "HAND_RIGHT", "build_test_frame", "crc16_ccitt_false",
    "next_receiver_frame_candidate", "normalize_hand", "parse_glove_frame",
    "parse_wrist_frame",
]


def _validated_header(data: bytes) -> Optional[tuple[int, int, int, int, int, int]]:
    if len(data) != FRAME_SIZE or data[:2] != MAGIC:
        return None
    if data[2] != PROTOCOL_VERSION or data[3] not in HAND_NAMES:
        return None
    flags = data[4]
    fail_mask = struct.unpack_from("<H", data, 16)[0]
    expected_crc = struct.unpack_from("<H", data, CRC_OFFSET)[0]
    if flags & ~0x03 or data[5] > 100 or fail_mask & ~0x07FF:
        return None
    if crc16_ccitt_false(data[:CRC_OFFSET]) != expected_crc:
        return None
    return (
        data[3], flags, data[5], struct.unpack_from("<H", data, 6)[0],
        struct.unpack_from("<Q", data, 8)[0], fail_mask,
    )


def parse_glove_frame(
    data: bytes,
    *,
    timestamp_ms: Optional[int] = None,
    timestamp_s: Optional[float] = None,
) -> Optional[GloveFrame]:
    """Parse one validated V2 frame into all 11 glove IMUs.

    ``timestamp_ms`` is retained as an input compatibility shim and denotes the
    host receive time. Returned ``timestamp`` is the device time in milliseconds.
    """
    if len(data) in (LEGACY_FRAME_SIZE, LEGACY_CAPTURE_FRAME_SIZE):
        checksum_offset = len(data) - 1
        if (data[:2] != MAGIC or data[2] not in HAND_NAMES or
                data[3] != IMU_COUNT or data[4] > 100 or
                xor_check(data[:checksum_offset]) != data[checksum_offset]):
            return None
        host_receive_s = (
            float(timestamp_s) if timestamp_s is not None
            else float(timestamp_ms) / 1000.0 if timestamp_ms is not None
            else time.monotonic()
        )
        imus: List[ImuData] = []
        for index in range(IMU_COUNT):
            raw = struct.unpack_from("<7h", data, 7 + index * IMU_RAW_SIZE)
            qw, qx, qy, qz = (value * QUAT_SCALE for value in raw[3:7])
            imus.append(ImuData(qw, qx, qy, qz, valid=True))
        gyro_raw = struct.unpack_from("<3h", data, 161)
        return GloveFrame(
            timestamp=int(host_receive_s * 1000), timestamp_us=0,
            host_receive_s=host_receive_s, imu_data=imus,
            frame_id=struct.unpack_from("<H", data, 5)[0],
            hand=HAND_NAMES[data[2]], battery_percent=data[4],
            wrist_gyro_valid=True,
            wrist_gyro_dps=tuple(float(value * GYRO_SCALE) for value in gyro_raw),
            protocol=f"legacy-{len(data)}", protocol_version=1,
            has_device_timestamp=False, has_recording_flag=False,
            has_fail_mask=False,
        )

    header = _validated_header(data)
    if header is None:
        return None
    hand_id, flags, battery, frame_id, timestamp_us, fail_mask = header
    host_receive_s = (
        float(timestamp_s) if timestamp_s is not None
        else float(timestamp_ms) / 1000.0 if timestamp_ms is not None
        else time.monotonic()
    )
    imu_data: List[ImuData] = []
    for index in range(IMU_COUNT):
        raw = struct.unpack_from("<7h", data, HEADER_SIZE + index * IMU_RAW_SIZE)
        qw, qx, qy, qz = (value * QUAT_SCALE for value in raw[3:7])
        imu_data.append(ImuData(qw, qx, qy, qz, valid=not bool(fail_mask & (1 << index))))

    gyro_raw = struct.unpack_from("<3h", data, EXTRA_OFFSET)
    gyro = tuple(float(value * GYRO_SCALE) for value in gyro_raw)
    return GloveFrame(
        timestamp=timestamp_us // 1000,
        timestamp_us=timestamp_us,
        host_receive_s=host_receive_s,
        imu_data=imu_data,
        frame_id=frame_id,
        hand=HAND_NAMES[hand_id],
        recording=bool(flags & FLAG_RECORDING),
        battery_percent=battery,
        imu_fail_mask=fail_mask,
        wrist_gyro_valid=bool(flags & FLAG_WRIST_GYRO_VALID),
        wrist_gyro_dps=gyro,
    )


def parse_wrist_frame(data: bytes, *, timestamp_s: Optional[float] = None) -> Optional[WirelessImuFrame]:
    """Parse the wrist view used by the tracker fusion pipeline."""
    return parse_receiver_frame(data, timestamp=timestamp_s)


def build_test_frame(
    hand: str = "left",
    frame_id: int = 1,
    *,
    quat_wxyz: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0),
    timestamp_us: int = 1_000_000,
    recording: bool = False,
    battery_percent: int = 100,
    fail_mask: int = 0,
    wrist_gyro_valid: bool = True,
    wrist_gyro_dps: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> bytes:
    """Build a valid 180-byte V2 test frame."""
    if not 0 <= battery_percent <= 100:
        raise ValueError("battery_percent must be in [0, 100]")
    if fail_mask & ~0x07FF:
        raise ValueError("fail_mask may only use bits 0..10")
    buf = bytearray(FRAME_SIZE)
    buf[0:2] = MAGIC
    buf[2] = PROTOCOL_VERSION
    buf[3] = HAND_IDS[hand]
    buf[4] = (FLAG_RECORDING if recording else 0) | (FLAG_WRIST_GYRO_VALID if wrist_gyro_valid else 0)
    buf[5] = battery_percent
    struct.pack_into("<H", buf, 6, frame_id & 0xFFFF)
    struct.pack_into("<Q", buf, 8, timestamp_us)
    struct.pack_into("<H", buf, 16, fail_mask)
    raw_q = tuple(int(max(-32768, min(32767, round(value * 32768.0)))) for value in quat_wxyz)
    for index in range(IMU_COUNT):
        struct.pack_into("<7h", buf, HEADER_SIZE + index * IMU_RAW_SIZE, 0, 0, 0, *raw_q)
    raw_gyro = tuple(int(max(-32768, min(32767, round(value / GYRO_SCALE)))) for value in wrist_gyro_dps)
    struct.pack_into("<3h", buf, EXTRA_OFFSET, *raw_gyro)
    struct.pack_into("<H", buf, CRC_OFFSET, crc16_ccitt_false(buf[:CRC_OFFSET]))
    return bytes(buf)

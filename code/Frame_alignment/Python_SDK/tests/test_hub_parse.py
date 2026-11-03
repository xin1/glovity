"""Unit tests for shared hub frame parsing and TeleXSession helpers."""
from __future__ import annotations

import numpy as np
import struct

from telex.glove.fk import compute_skeleton_joints
from telex.glove.labels import IMU_LABELS, JOINT_LABELS
from telex.hub.parse import (
    build_test_frame,
    next_receiver_frame_candidate,
    parse_glove_frame,
    parse_wrist_frame,
)
from telex.session import _build_hand_state, _has_glove_data
from telex.types import HandState, TeleXStrictError
from telex.session import TeleXSession


def _legacy_frame(size: int, hand_id: int = 0, frame_id: int = 12) -> bytes:
    raw = bytearray(size)
    raw[:7] = bytes((0xAA, 0x55, hand_id, 11, 87, frame_id & 0xFF, frame_id >> 8))
    for index in range(11):
        struct.pack_into("<7h", raw, 7 + index * 14, 0, 0, 0, 32767, 0, 0, 0)
    checksum = 0
    for value in raw[:-1]:
        checksum ^= value
    raw[-1] = checksum
    return bytes(raw)


def test_labels_lengths():
    assert len(IMU_LABELS) == 11
    assert len(JOINT_LABELS) == 21


def test_parse_glove_and_wrist_frame():
    raw = build_test_frame(
        "left", frame_id=7, quat_wxyz=(1.0, 0.0, 0.0, 0.0),
        timestamp_us=1_234_567, recording=True, battery_percent=88,
        wrist_gyro_dps=(10.0, -20.0, 30.0),
    )
    glove = parse_glove_frame(raw, timestamp_ms=1234)
    wrist = parse_wrist_frame(raw, timestamp_s=1.5)
    assert glove is not None
    assert wrist is not None
    assert glove.frame_id == 7
    assert glove.frame_seq == 7
    assert glove.hand == "left"
    assert glove.timestamp_us == 1_234_567
    assert glove.timestamp == 1234
    assert glove.host_receive_s == 1.234
    assert glove.recording
    assert glove.battery_percent == 88
    assert glove.imu_fail_mask == 0
    assert glove.wrist_gyro_valid
    assert len(glove.imu_data) == 11
    assert abs(glove.imu_data[0].quat_w - 1.0) < 1e-3
    assert wrist.hand_name == "left"
    assert wrist.timestamp_us == 1_234_567
    assert wrist.recording
    assert abs(float(wrist.quat[0]) - 1.0) < 1e-3


def test_fk_shape_wrist_origin():
    raw = build_test_frame("right", frame_id=1)
    glove = parse_glove_frame(raw)
    assert glove is not None
    joints = compute_skeleton_joints(glove, side="right")
    assert joints.shape == (21, 3)
    assert np.allclose(joints[0], 0.0)


def test_crc_corruption_is_rejected():
    raw = bytearray(build_test_frame("left"))
    raw[30] ^= 0x01
    assert parse_glove_frame(bytes(raw)) is None
    assert parse_wrist_frame(bytes(raw)) is None


def test_failure_mask_marks_imu_invalid_and_suppresses_fk():
    glove = parse_glove_frame(build_test_frame("right", fail_mask=1 << 3))
    assert glove is not None
    assert glove.imu_data[3].valid is False
    assert glove.imu_data[2].valid is True
    state = _build_hand_state(pose=None, glove_frame=glove, hand="right")
    assert state.imus is not None
    assert state.joints is None
    assert state.imu_fail_mask == 1 << 3


def test_stream_candidate_skips_noise_and_old_protocol():
    frame = build_test_frame("left", frame_id=9)
    buf = bytearray(b"noise\xaa\x55\x01\x00" + frame)
    candidate = next_receiver_frame_candidate(buf)
    assert candidate is not None
    assert candidate[0] == frame
    assert candidate[1] == 180


def test_legacy_168_and_170_are_identified():
    for size in (168, 170):
        raw = _legacy_frame(size, hand_id=1)
        candidate = next_receiver_frame_candidate(bytearray(b"noise" + raw))
        assert candidate is not None and candidate[1] == size
        glove = parse_glove_frame(candidate[0], timestamp_s=2.0)
        wrist = parse_wrist_frame(candidate[0], timestamp_s=2.0)
        assert glove is not None and wrist is not None
        assert glove.protocol == f"legacy-{size}"
        assert glove.protocol_version == 1
        assert not glove.has_device_timestamp
        assert not glove.has_recording_flag
        assert not glove.has_fail_mask
        assert wrist.protocol == f"legacy-{size}"


def test_hand_state_available_and_strict_helper():
    raw = build_test_frame("left")
    glove = parse_glove_frame(raw)
    state = _build_hand_state(pose=None, glove_frame=glove, hand="left")
    assert state.available
    assert _has_glove_data(state)
    assert state.joints is not None
    assert state.imus is not None

    empty = HandState(pose=None, imus=None, joints=None, available=False)
    assert not _has_glove_data(empty)


def test_normalize_side():
    s = TeleXSession(camera=0, side="LEFT")
    assert s.side == "left"
    assert TeleXSession(camera=0, side="both").side == "both"


def test_strict_error_type():
    assert issubclass(TeleXStrictError, Exception)

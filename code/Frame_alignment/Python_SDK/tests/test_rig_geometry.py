"""HandRig geometry: IDs, install Rz, right-hand 1↔3 rebase."""
from __future__ import annotations

import numpy as np
import pytest

from telex.tracker.vision.rig import (
    LEFT_MARKER_IDS,
    MARKER1_TO_MARKER,
    MARKER_INSTALL_RZ_DEG,
    OBJECT_FRAME_LOCAL_Z_ROTATION_DEG,
    OBJECT_FRAME_ORIGIN_OFFSET_MM,
    OBJECT_FRAME_AXIS_RX_DEG,
    RIGHT_MARKER_IDS,
    build_hand_rigs,
    object_pose_from_marker,
    rebase_marker1_as_old_marker3,
    rotation_x_transform,
    rotation_z_transform,
    translation_transform,
)
from telex.tracker.vision.pose import MarkerPose, rvec_tvec_to_matrix


def test_marker_ids_match_install_layout():
    assert dict(LEFT_MARKER_IDS) == {"marker1": 5, "marker2": 18, "marker3": 1}
    assert dict(RIGHT_MARKER_IDS) == {"marker1": 11, "marker2": 27, "marker3": 19}
    assert MARKER_INSTALL_RZ_DEG == {18: -90.0, 1: 90.0, 11: 180.0, 19: 180.0}


def test_install_rz_applied_as_right_multiply():
    rigs = build_hand_rigs()
    for hand, rig in rigs.items():
        for marker_id, nominal in rig.nominal_object_to_marker.items():
            theta = float(MARKER_INSTALL_RZ_DEG.get(marker_id, 0.0))
            expected = nominal @ rotation_z_transform(theta)
            actual = np.asarray(rig.object_to_marker[marker_id], dtype=np.float64)
            assert np.allclose(actual, expected, atol=1e-9), (hand, marker_id)


def test_object_frame_origin_offsets_are_applied_to_both_hands():
    rigs = build_hand_rigs()
    left_m1 = np.asarray(rigs["left"].object_to_marker[5], dtype=np.float64)
    right_m1 = np.asarray(rigs["right"].object_to_marker[11], dtype=np.float64)
    correction = rotation_x_transform(-OBJECT_FRAME_AXIS_RX_DEG)
    left_offset = translation_transform(-OBJECT_FRAME_ORIGIN_OFFSET_MM["left"])
    right_offset = translation_transform(-OBJECT_FRAME_ORIGIN_OFFSET_MM["right"])
    assert np.allclose(
        left_m1,
        rotation_z_transform(-OBJECT_FRAME_LOCAL_Z_ROTATION_DEG["left"])
        @ left_offset
        @ correction,
        atol=1e-9,
    )
    assert np.allclose(
        right_m1,
        rotation_z_transform(-OBJECT_FRAME_LOCAL_Z_ROTATION_DEG["right"])
        @ right_offset
        @ correction
        @ rotation_z_transform(180.0),
        atol=1e-9,
    )


def test_object_origin_offsets_stay_fixed_during_local_z_rotations():
    rigs = build_hand_rigs()
    pre_z_rotation_by_hand = {
        "left": rotation_x_transform(OBJECT_FRAME_AXIS_RX_DEG)[:3, :3],
        "right": (
            rotation_z_transform(180.0)
            @ rotation_x_transform(OBJECT_FRAME_AXIS_RX_DEG)
        )[:3, :3],
    }
    for hand, marker_id in (("left", 5), ("right", 11)):
        object_to_marker = np.asarray(
            rigs[hand].object_to_marker[marker_id], dtype=np.float64
        )
        camera_to_object = np.linalg.inv(object_to_marker)
        expected_translation = (
            pre_z_rotation_by_hand[hand] @ OBJECT_FRAME_ORIGIN_OFFSET_MM[hand]
        )
        assert np.allclose(
            camera_to_object[:3, 3], expected_translation, atol=1e-9
        )


def test_marker1_pose_uses_the_requested_local_z_rotation():
    rig = build_hand_rigs()["left"]
    marker_pose = MarkerPose(
        rvec=np.zeros((3, 1), dtype=np.float64),
        tvec=np.array([[0.0], [0.0], [400.0]], dtype=np.float64),
        reprojection_error_px=0.0,
    )

    object_pose = object_pose_from_marker(marker_pose, rig, 5)
    rotation = rvec_tvec_to_matrix(object_pose.rvec, object_pose.tvec)[:3, :3]

    assert np.allclose(
        rotation,
        (
            rotation_x_transform(OBJECT_FRAME_AXIS_RX_DEG)
            @ rotation_z_transform(OBJECT_FRAME_LOCAL_Z_ROTATION_DEG["left"])
        )[:3, :3],
        atol=1e-9,
    )


def test_local_z_rotations_match_the_requested_hand_axes():
    rigs = build_hand_rigs()
    pre_z_rotation_by_hand = {
        "left": rotation_x_transform(OBJECT_FRAME_AXIS_RX_DEG)[:3, :3],
        "right": (
            rotation_z_transform(180.0)
            @ rotation_x_transform(OBJECT_FRAME_AXIS_RX_DEG)
        )[:3, :3],
    }
    for hand, marker_id in (("left", 5), ("right", 11)):
        camera_to_object = np.linalg.inv(
            np.asarray(rigs[hand].object_to_marker[marker_id], dtype=np.float64)
        )
        local_rotation = pre_z_rotation_by_hand[hand].T @ camera_to_object[:3, :3]
        assert np.allclose(
            local_rotation,
            rotation_z_transform(OBJECT_FRAME_LOCAL_Z_ROTATION_DEG[hand])[:3, :3],
            atol=1e-9,
        )


def test_right_table_is_old_marker3_rebase():
    right_table = rebase_marker1_as_old_marker3(MARKER1_TO_MARKER)
    m2 = np.asarray(MARKER1_TO_MARKER["marker2"], dtype=np.float64)
    m3 = np.asarray(MARKER1_TO_MARKER["marker3"], dtype=np.float64)
    inv_m3 = np.linalg.inv(m3)
    assert np.allclose(right_table["marker1"], np.eye(4), atol=1e-9)
    assert np.allclose(right_table["marker3"], inv_m3, atol=1e-6)
    assert np.allclose(right_table["marker2"], m2 @ inv_m3, atol=1e-6)


def test_unknown_install_id_raises():
    import telex.tracker.vision.rig as rig_mod

    original = dict(rig_mod.MARKER_INSTALL_RZ_DEG)
    try:
        rig_mod.MARKER_INSTALL_RZ_DEG = {**original, 99: 90.0}
        with pytest.raises(ValueError, match="MARKER_INSTALL_RZ_DEG"):
            rig_mod.build_hand_rigs()
    finally:
        rig_mod.MARKER_INSTALL_RZ_DEG = original

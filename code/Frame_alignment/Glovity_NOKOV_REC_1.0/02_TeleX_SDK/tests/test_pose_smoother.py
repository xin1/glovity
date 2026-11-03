"""Regression tests for short tracker dropouts in output pose smoothing."""
from __future__ import annotations

import numpy as np

from telex.tracker.filter.smoother import (
    OneEuroParams,
    TeleopPoseSmoothConfig,
    TeleopPoseSmoother,
)
from telex.tracker.vision.pose import MarkerPose


def _pose(x_mm: float, rvec=(0.0, 0.0, 0.0)) -> MarkerPose:
    return MarkerPose(
        rvec=np.asarray(rvec, dtype=np.float64).reshape(3, 1),
        tvec=np.asarray([[x_mm], [0.0], [400.0]], dtype=np.float64),
        reprojection_error_px=0.5,
    )


def _smoother() -> TeleopPoseSmoother:
    return TeleopPoseSmoother(
        TeleopPoseSmoothConfig(
            translation=OneEuroParams(
                min_cutoff_hz=1.0,
                beta=0.0,
                d_cutoff_hz=1.0,
            ),
            rotation_min_cutoff_hz=1.0,
            rotation_beta=0.0,
            max_dt_s=0.25,
        )
    )


def test_short_missing_pose_keeps_filter_state_without_holding_output():
    smoother = _smoother()
    smoother.update(_pose(0.0), now_s=0.0)
    first = smoother.update(_pose(30.0), now_s=1.0 / 30.0)
    assert first is not None
    assert 0.0 < float(first.tvec[0, 0]) < 30.0

    assert smoother.update(None, now_s=2.0 / 30.0) is None

    recovered = smoother.update(_pose(30.0), now_s=3.0 / 30.0)
    assert recovered is not None
    assert float(recovered.tvec[0, 0]) < 30.0


def test_long_missing_pose_resets_filter_state():
    smoother = _smoother()
    smoother.update(_pose(0.0), now_s=0.0)
    smoother.update(_pose(30.0), now_s=1.0 / 30.0)

    assert smoother.update(None, now_s=1.0) is None
    recovered = smoother.update(_pose(60.0), now_s=1.0 + 1.0 / 30.0)
    assert recovered is not None
    assert np.isclose(float(recovered.tvec[0, 0]), 60.0)


def test_large_image_motion_raises_filter_bandwidth_for_fast_tracking():
    config = TeleopPoseSmoothConfig(
        translation=OneEuroParams(
            min_cutoff_hz=1.0,
            beta=0.0,
            d_cutoff_hz=1.0,
        ),
        rotation_min_cutoff_hz=1.0,
        rotation_beta=0.0,
        image_motion_threshold_px=1.0,
        image_motion_cutoff_gain_hz_per_px=50.0,
    )
    stationary = TeleopPoseSmoother(config)
    stationary.update(_pose(0.0), now_s=0.0)
    slow = stationary.update(_pose(30.0), now_s=1.0 / 30.0)

    moving = TeleopPoseSmoother(config)
    moving.update(_pose(0.0), now_s=0.0)
    fast = moving.update(
        _pose(30.0), now_s=1.0 / 30.0, image_motion_px=10.0
    )

    assert slow is not None and fast is not None
    assert float(slow.tvec[0, 0]) < 10.0
    assert float(fast.tvec[0, 0]) > 25.0


def test_default_image_motion_gain_keeps_a_large_step_responsive():
    smoother = TeleopPoseSmoother()
    smoother.update(_pose(0.0), now_s=0.0)
    fast = smoother.update(
        _pose(30.0), now_s=1.0 / 30.0, image_motion_px=20.0
    )

    assert fast is not None
    assert float(fast.tvec[0, 0]) > 25.0


def test_default_image_motion_gain_keeps_a_large_rotation_responsive():
    smoother = TeleopPoseSmoother()
    smoother.update(_pose(0.0), now_s=0.0)
    fast = smoother.update(
        _pose(0.0, rvec=(0.0, 0.0, np.deg2rad(30.0))),
        now_s=1.0 / 30.0, image_motion_px=20.0,
    )

    assert fast is not None
    assert float(fast.rvec[2, 0]) > np.deg2rad(25.0)

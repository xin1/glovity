"""PoseOutlierFilter: allow fast translation, hold axis-flip rotations."""
from __future__ import annotations

import time

import numpy as np

from telex.tracker.vision.pose import MarkerPose, PoseOutlierFilter


def _pose(t_mm, rvec=(0.0, 0.0, 0.0)) -> MarkerPose:
    return MarkerPose(
        rvec=np.asarray(rvec, dtype=np.float64).reshape(3, 1),
        tvec=np.asarray(t_mm, dtype=np.float64).reshape(3, 1),
        reprojection_error_px=0.5,
    )


def test_fast_translation_is_accepted_not_held():
    filt = PoseOutlierFilter(
        max_rotation_deg=45.0,
        max_translation_mm=100.0,
        max_hold_frames=2,
    )
    p0 = _pose([0.0, 0.0, 400.0])
    accepted, is_outlier = filt.check(p0)
    assert not is_outlier and accepted is p0

    # ~60 mm frame-to-frame would exceed the old 30 mm absolute gate.
    time.sleep(0.02)
    p1 = _pose([60.0, 0.0, 400.0])
    accepted, is_outlier = filt.check(p1)
    assert not is_outlier
    assert np.allclose(accepted.tvec.ravel(), p1.tvec.ravel())


def test_large_rotation_holds_then_accepts():
    filt = PoseOutlierFilter(
        max_rotation_deg=45.0,
        max_translation_mm=100.0,
        max_hold_frames=1,
    )
    p0 = _pose([0.0, 0.0, 400.0], rvec=(0.0, 0.0, 0.0))
    filt.check(p0)

    # ~180° about Z — classic PnP axis-flip signature.
    time.sleep(0.02)
    p_flip = _pose([0.0, 0.0, 400.0], rvec=(0.0, 0.0, np.pi))
    held, is_outlier = filt.check(p_flip)
    assert is_outlier
    assert np.allclose(held.tvec.ravel(), p0.tvec.ravel())
    assert np.allclose(held.rvec.ravel(), p0.rvec.ravel())

    time.sleep(0.02)
    accepted, is_outlier = filt.check(p_flip)
    assert not is_outlier
    assert np.allclose(accepted.rvec.ravel(), p_flip.rvec.ravel())

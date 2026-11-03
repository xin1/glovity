"""Adaptive temporal corner smoothing for planar marker PnP."""
from __future__ import annotations

import numpy as np

from telex.tracker.marker.detection import FINDGCP_SPEC, SelectedMarker
from telex.tracker.vision.tracking import MultiMarkerTracker


def _marker(corners: np.ndarray) -> SelectedMarker:
    return SelectedMarker(FINDGCP_SPEC, 18, corners.astype(np.float32))


def _corners(offset_x: float = 0.0) -> np.ndarray:
    return np.asarray(
        [[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0]],
        dtype=np.float32,
    ) + np.asarray([offset_x, 0.0], dtype=np.float32)


def test_nearly_static_marker_uses_short_corner_history_for_pnp():
    tracker = MultiMarkerTracker(
        smooth_window=3,
        adaptive_smooth_shift_px=2.0,
    )
    tracker.update([_marker(_corners(0.0))])
    tracker.update([_marker(_corners(0.5))])
    update = tracker.update([_marker(_corners(-0.5))])

    assert len(update.fresh_markers) == 1
    assert np.allclose(update.fresh_markers[0].corners, _corners(0.0))


def test_fast_marker_motion_bypasses_corner_history():
    tracker = MultiMarkerTracker(
        smooth_window=3,
        adaptive_smooth_shift_px=2.0,
    )
    tracker.update([_marker(_corners(0.0))])
    tracker.update([_marker(_corners(0.5))])
    fast_corners = _corners(8.0)
    update = tracker.update([_marker(fast_corners)])

    assert len(update.fresh_markers) == 1
    assert np.allclose(update.fresh_markers[0].corners, fast_corners)

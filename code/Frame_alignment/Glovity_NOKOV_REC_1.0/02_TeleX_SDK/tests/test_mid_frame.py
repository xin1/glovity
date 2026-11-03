import numpy as np
import pytest

from telex.tracker.geometry import mid_frame_from_left_mm, rotation_x_matrix


def _parallel_stereo_right_to_left() -> np.ndarray:
    transform = np.eye(4, dtype=np.float64)
    transform[:3, 3] = [100.0, 0.0, 0.0]
    return transform


def test_mid_pitch_up_rotates_coordinate_components_with_opposite_sign():
    unlevelled = mid_frame_from_left_mm(_parallel_stereo_right_to_left())
    levelled = mid_frame_from_left_mm(
        _parallel_stereo_right_to_left(), pitch_up_deg=60.0
    )

    correction = rotation_x_matrix(-60.0)
    assert np.allclose(
        levelled[:3, :3], correction @ unlevelled[:3, :3], atol=1e-9
    )
    assert np.allclose(
        levelled[:3, 3], correction @ unlevelled[:3, 3], atol=1e-9
    )


def test_mid_pitch_up_requires_finite_angle():
    with pytest.raises(ValueError, match="pitch_up_deg"):
        mid_frame_from_left_mm(
            _parallel_stereo_right_to_left(), pitch_up_deg=float("nan")
        )

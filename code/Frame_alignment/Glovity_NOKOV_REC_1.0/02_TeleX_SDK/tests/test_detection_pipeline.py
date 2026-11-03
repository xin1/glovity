import pytest
import cv2
import numpy as np

from telex.tracker.cli import build_arg_parser
from telex.tracker.vision.pipeline import make_detector
from telex.tracker.vision.pipeline import FindGcpDetectionPipeline


def test_folded_detection_defaults_prioritize_small_marker_decoding():
    args = build_arg_parser().parse_args([])

    detector = make_detector(args)

    assert detector.clahe_clip == 6.0
    assert detector.warp_pixels_per_cell == 8
    assert detector.corner_inset == 0.0
    assert detector.base.parameters.perspectiveRemovePixelPerCell == 8


def test_detection_warp_resolution_is_configurable_and_validated():
    parser = build_arg_parser()
    detector = make_detector(
        parser.parse_args(["--detect-warp-pixels-per-cell", "4"])
    )

    assert detector.base.parameters.perspectiveRemovePixelPerCell == 4
    with pytest.raises(ValueError, match="warp-pixels-per-cell"):
        make_detector(parser.parse_args(["--detect-warp-pixels-per-cell", "3"]))


def test_black_border_refinement_rejects_quiet_zone_outer_contour():
    pipeline = FindGcpDetectionPipeline(
        preprocess="none",
        corner_inset=0.0,
        detect_downscale=1.0,
        clahe_clip=2.5,
        clahe_grid=8,
        gamma=1.0,
        corner_refinement_method=cv2.aruco.CORNER_REFINE_SUBPIX,
    )
    image = np.full((140, 140), 255, dtype=np.uint8)
    image[20:120, 20:120] = 0
    quiet_zone_outer = np.array(
        [[0.0, 0.0], [139.0, 0.0], [139.0, 139.0], [0.0, 139.0]],
        dtype=np.float32,
    )

    refined = pipeline._refine_black_border_corners(image, quiet_zone_outer)
    edges = np.roll(refined, -1, axis=0) - refined
    mean_side = float(np.mean(np.linalg.norm(edges, axis=1)))

    assert mean_side == pytest.approx(100.0, abs=3.0)

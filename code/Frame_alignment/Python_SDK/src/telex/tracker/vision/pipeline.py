"""Stereo tracker vision estimation pipeline."""
from __future__ import annotations

import argparse
from typing import Optional

import cv2
import numpy as np

from telex.tracker.camera.calibration import StereoCalibration
from telex.tracker.marker.detection import (
    FINDGCP_SPEC,
    FindGcpDetector,
    SelectedMarker,
)
from telex.tracker.types import (
    HandRig,
    PnPSelectionConfig,
    TrackerVision,
    TrackerVisionMemory,
)
from telex.tracker.vision.pnp import estimate_rig_pose_fisheye, markers_for_rig
from telex.tracker.vision.stereo_fuse import (
    _fuse_stereo_estimates,
    _transform_rig_estimate,
)


def _scaled_quad_projectively(corners: np.ndarray, scale: float) -> np.ndarray:
    """Scale a quadrilateral about its projective center, preserving perspective."""
    quad = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    unit_square = np.array(
        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(unit_square, quad)
    center = np.array([0.5, 0.5], dtype=np.float32)
    scaled_square = center + (unit_square - center) * float(scale)
    return cv2.perspectiveTransform(
        scaled_square.reshape(1, 4, 2), homography
    ).reshape(4, 2)


def _gray_image(image: np.ndarray) -> np.ndarray:
    gray = (
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if image.ndim == 3
        else np.asarray(image)
    )
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)
    return gray


def _quad_border_intensity_profile(
    gray: np.ndarray,
    corners: np.ndarray,
    scales: np.ndarray,
) -> np.ndarray:
    quad = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    unit_square = np.array(
        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(unit_square, quad)
    center = np.array([0.5, 0.5], dtype=np.float32)
    scaled_squares = center + (unit_square[None, :, :] - center) * scales[
        :, None, None
    ]
    scaled_quads = cv2.perspectiveTransform(
        scaled_squares.reshape(1, -1, 2), homography
    ).reshape(-1, 4, 2)
    t = np.linspace(0.15, 0.85, 13, dtype=np.float32)
    edges = (
        scaled_quads[:, 0, None, :] * (1.0 - t[None, :, None])
        + scaled_quads[:, 1, None, :] * t[None, :, None],
        scaled_quads[:, 1, None, :] * (1.0 - t[None, :, None])
        + scaled_quads[:, 2, None, :] * t[None, :, None],
        scaled_quads[:, 2, None, :] * (1.0 - t[None, :, None])
        + scaled_quads[:, 3, None, :] * t[None, :, None],
        scaled_quads[:, 3, None, :] * (1.0 - t[None, :, None])
        + scaled_quads[:, 0, None, :] * t[None, :, None],
    )
    points = np.concatenate(edges, axis=1)
    sampled = cv2.remap(
        gray,
        points[:, :, 0].astype(np.float32),
        points[:, :, 1].astype(np.float32),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return np.median(sampled, axis=1).astype(np.float64)

def estimate_tracker_vision(
    left_markers,
    right_markers,
    rig: HandRig,
    marker_size_mm: float,
    calibration: StereoCalibration,
    *,
    memory: Optional[TrackerVisionMemory] = None,
    config: Optional[PnPSelectionConfig] = None,
    stereo_hysteresis_frames: int = 4,
) -> TrackerVision:
    selection_config = config or PnPSelectionConfig()
    pose_memory = memory or TrackerVisionMemory()
    left_group = markers_for_rig(left_markers, rig)
    right_group = markers_for_rig(right_markers, rig)
    left_estimate = estimate_rig_pose_fisheye(
        left_group,
        rig,
        marker_size_mm,
        calibration.left.camera_matrix,
        calibration.left.dist_coeffs,
        prior_pose=pose_memory.left_camera_pose,
        config=selection_config,
    )
    right_estimate = estimate_rig_pose_fisheye(
        right_group,
        rig,
        marker_size_mm,
        calibration.right.camera_matrix,
        calibration.right.dist_coeffs,
        prior_pose=pose_memory.right_camera_pose,
        config=selection_config,
    )
    if left_estimate.pose is not None:
        pose_memory.left_camera_pose = left_estimate.pose
    if right_estimate.pose is not None:
        pose_memory.right_camera_pose = right_estimate.pose

    right_in_left = _transform_rig_estimate(
        right_estimate, calibration.right_to_left_mm
    )
    fused = _fuse_stereo_estimates(
        left_estimate,
        right_in_left,
        pose_memory.fused_left_pose,
        selection_config,
        memory=pose_memory,
        hysteresis_frames=stereo_hysteresis_frames,
    )
    if fused.pose is not None:
        pose_memory.fused_left_pose = fused.pose

    visible_ids = tuple(
        sorted(set(left_estimate.visible_ids) | set(right_estimate.visible_ids))
    )
    if fused.source == "left":
        mode = left_estimate.mode
    elif fused.source == "right":
        mode = right_estimate.mode
    elif fused.source == "stereo":
        modes = {left_estimate.mode, right_estimate.mode} - {"none"}
        if "multi" in modes:
            mode = "multi"
        elif "fallback-single" in modes:
            mode = "fallback-single"
        elif "single" in modes:
            mode = "single"
        else:
            mode = "none"
    else:
        mode = "none"
    return TrackerVision(fused, visible_ids, mode)


class FindGcpDetectionPipeline:
    """Detection wrapper for the project's custom Find-GCP 3x3 dictionary.

    The underlying ``track.detection.FindGcpDetector`` remains the source of
    marker decoding. This wrapper only adds optional grayscale preprocessing,
    detection downscaling, corner-refinement selection, and the PCB-specific
    corner inset. It never substitutes a predefined OpenCV ArUco dictionary.
    """

    def __init__(
        self,
        *,
        preprocess: str,
        corner_inset: float,
        detect_downscale: float,
        clahe_clip: float,
        clahe_grid: int,
        gamma: float,
        corner_refinement_method: int,
        warp_pixels_per_cell: int = 8,
        refine_black_border: bool = True,
    ) -> None:
        self.base = FindGcpDetector()
        self.preprocess = str(preprocess)
        self.corner_inset = float(corner_inset)
        self.detect_downscale = float(detect_downscale)
        self.clahe_clip = float(clahe_clip)
        self.clahe_grid = int(clahe_grid)
        self.gamma = float(gamma)
        self.warp_pixels_per_cell = int(warp_pixels_per_cell)
        self.refine_black_border = bool(refine_black_border)

        # ``FindGcpDetector`` owns the custom 3x3 dictionary. We only update
        # its detection settings and rebuild the OpenCV detector so the
        # selected settings are actually applied on OpenCV >= 4.7.  Eight
        # pixels per cell materially improves decoding of the small markers
        # observed by the stereo camera without adding output latency.
        self.base.parameters.cornerRefinementMethod = int(
            corner_refinement_method
        )
        self.base.parameters.perspectiveRemovePixelPerCell = (
            self.warp_pixels_per_cell
        )
        if hasattr(cv2.aruco, "ArucoDetector"):
            self.base._detector = cv2.aruco.ArucoDetector(
                self.base.dictionary,
                self.base.parameters,
            )

        self._clahe = (
            cv2.createCLAHE(
                clipLimit=self.clahe_clip,
                tileGridSize=(self.clahe_grid, self.clahe_grid),
            )
            if "clahe" in self.preprocess
            else None
        )
        if "gamma" in self.preprocess:
            values = np.arange(256, dtype=np.float32) / 255.0
            corrected = np.power(values, self.gamma) * 255.0
            self._gamma_lut = np.clip(corrected, 0.0, 255.0).astype(np.uint8)
        else:
            self._gamma_lut = None

    def _preprocess_gray(self, image: np.ndarray) -> np.ndarray:
        if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
            raise ValueError("检测图像必须是二维灰度图或三通道图像")
        gray = (
            cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if image.ndim == 3
            else image
        )
        if gray.dtype != np.uint8:
            gray = np.clip(gray, 0, 255).astype(np.uint8)
        if self._clahe is not None:
            gray = self._clahe.apply(gray)
        if self._gamma_lut is not None:
            gray = cv2.LUT(gray, self._gamma_lut)
        if self.detect_downscale < 1.0:
            height, width = gray.shape[:2]
            gray = cv2.resize(
                gray,
                (
                    max(1, int(round(width * self.detect_downscale))),
                    max(1, int(round(height * self.detect_downscale))),
                ),
                interpolation=cv2.INTER_AREA,
            )
        return gray

    def _restore_corners(self, corners: np.ndarray) -> np.ndarray:
        points = np.asarray(corners, dtype=np.float32).reshape(4, 2).copy()
        if self.detect_downscale < 1.0:
            points /= self.detect_downscale
        return np.ascontiguousarray(points, dtype=np.float32)

    def _inset_corners(self, corners: np.ndarray) -> np.ndarray:
        points = np.asarray(corners, dtype=np.float32).reshape(4, 2).copy()
        if self.corner_inset > 0.0:
            center = np.mean(points, axis=0, keepdims=True)
            points = center + (1.0 - self.corner_inset) * (points - center)
        return np.ascontiguousarray(points, dtype=np.float32)

    def _restore_and_inset_corners(self, corners: np.ndarray) -> np.ndarray:
        return self._inset_corners(self._restore_corners(corners))

    def _refine_black_border_corners(
        self,
        image: np.ndarray,
        corners: np.ndarray,
    ) -> np.ndarray:
        """Anchor corners to the inner edge of the white quiet zone.

        A narrow quiet zone can make ArUco alternate between the black marker
        border and the quiet-zone outer contour.  Sampling each projectively
        scaled candidate border exposes the dark-to-bright transition at the
        actual black outer border, independent of which contour was decoded.
        """
        points = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        if not self.refine_black_border:
            return points.copy()
        try:
            gray = _gray_image(image)
            scales = np.linspace(0.62, 1.20, 73, dtype=np.float32)
            profile = _quad_border_intensity_profile(gray, points, scales)
        except cv2.error:
            return points.copy()

        smoothed = np.convolve(
            profile,
            np.ones(5, dtype=np.float64) / 5.0,
            mode="same",
        )
        low = float(np.percentile(smoothed, 20))
        high = float(np.percentile(smoothed, 90))
        contrast = high - low
        if contrast < 24.0:
            return points.copy()
        threshold = low + 0.55 * contrast
        crossing_indices = np.flatnonzero(
            (smoothed[:-1] < threshold) & (smoothed[1:] >= threshold)
        ) + 1
        crossing_indices = [
            int(index)
            for index in crossing_indices
            if 0.66 <= float(scales[index]) <= 1.10
        ]
        if not crossing_indices:
            return points.copy()
        index = max(
            crossing_indices,
            key=lambda item: float(smoothed[item] - smoothed[item - 1]),
        )
        refined = _scaled_quad_projectively(points, float(scales[index]))
        return np.ascontiguousarray(refined, dtype=np.float32)

    def detect_targets(
        self,
        image: np.ndarray,
        target_ids: set[int],
    ) -> list[SelectedMarker]:
        processed = self._preprocess_gray(image)
        corners, ids, _rejected = self.base.detect(processed)
        if ids is None or len(ids) == 0:
            return []

        selected: list[SelectedMarker] = []
        seen: set[int] = set()
        flat_ids = [int(value) for value in np.asarray(ids).reshape(-1)]
        for marker_corners, marker_id in zip(corners, flat_ids):
            if marker_id not in target_ids or marker_id in seen:
                continue
            seen.add(marker_id)
            restored_corners = self._restore_corners(marker_corners)
            refined_corners = self._refine_black_border_corners(
                image, restored_corners
            )
            selected.append(
                SelectedMarker(
                    spec=FINDGCP_SPEC,
                    marker_id=marker_id,
                    corners=self._inset_corners(refined_corners),
                )
            )
        return selected


def make_detector(args: argparse.Namespace) -> FindGcpDetectionPipeline:
    if args.marker_corner_inset < 0.0 or args.marker_corner_inset >= 1.0:
        raise ValueError("marker-corner-inset 必须在 [0, 1) 范围内")
    if args.detect_clahe_clip <= 0.0:
        raise ValueError("detect-clahe-clip 必须为正数")
    if args.detect_clahe_grid < 2:
        raise ValueError("detect-clahe-grid 必须 >= 2")
    if args.detect_gamma <= 0.0:
        raise ValueError("detect-gamma 必须为正数")
    if args.detect_warp_pixels_per_cell < 4:
        raise ValueError("detect-warp-pixels-per-cell 必须 >= 4")
    if args.detect_downscale <= 0.0 or args.detect_downscale > 1.0:
        raise ValueError("detect-downscale 必须在 (0, 1] 范围内")
    if args.display_scale <= 0.0 or args.display_scale > 1.0:
        raise ValueError("display-scale 必须在 (0, 1] 范围内")

    preprocess = args.detect_preprocess
    if preprocess == "auto":
        preprocess = "clahe" if args.detect_preset == "folded" else "none"

    refine_name = args.detect_corner_refine
    if refine_name == "auto":
        refine_name = "subpix"
    refinement_methods = {
        "subpix": cv2.aruco.CORNER_REFINE_SUBPIX,
        "apriltag": cv2.aruco.CORNER_REFINE_APRILTAG,
        "contour": cv2.aruco.CORNER_REFINE_CONTOUR,
    }

    return FindGcpDetectionPipeline(
        preprocess=preprocess,
        corner_inset=args.marker_corner_inset,
        detect_downscale=args.detect_downscale,
        clahe_clip=args.detect_clahe_clip,
        clahe_grid=args.detect_clahe_grid,
        gamma=args.detect_gamma,
        warp_pixels_per_cell=args.detect_warp_pixels_per_cell,
        refine_black_border=args.marker_border_refine,
        corner_refinement_method=refinement_methods[refine_name],
    )

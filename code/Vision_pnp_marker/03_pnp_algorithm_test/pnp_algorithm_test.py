#!/usr/bin/env python3
"""Robust PnP pose estimation and reproducible synthetic-data test.

Coordinate convention
---------------------
OpenCV's camera frame is used: +x right, +y down, +z forward.  The estimated
transform maps object-frame points into the camera frame::

    point_camera = R_camera_object @ point_object + t_camera_object

The translation unit is therefore the same as the unit used by object points.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover - depends on the runtime environment
    print(f"Missing dependency: {exc}", file=sys.stderr)
    print("Install with: python -m pip install numpy opencv-python", file=sys.stderr)
    raise SystemExit(2) from exc


@dataclass(frozen=True)
class PnPConfig:
    """Numerical settings and acceptance criteria for robust PnP."""

    iterations: int = 200
    reprojection_threshold_px: float = 3.0
    confidence: float = 0.999
    min_inliers: int = 6
    min_inlier_ratio: float = 0.50
    max_inlier_rmse_px: float = 2.0
    ransac_method: int = cv2.SOLVEPNP_EPNP

    def validate(self) -> None:
        if self.iterations <= 0:
            raise ValueError("iterations must be positive")
        if self.reprojection_threshold_px <= 0.0:
            raise ValueError("reprojection_threshold_px must be positive")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("confidence must be in the open interval (0, 1)")
        if self.min_inliers < 4:
            raise ValueError("min_inliers must be at least 4")
        if not 0.0 <= self.min_inlier_ratio <= 1.0:
            raise ValueError("min_inlier_ratio must be in [0, 1]")
        if self.max_inlier_rmse_px <= 0.0:
            raise ValueError("max_inlier_rmse_px must be positive")


@dataclass(frozen=True)
class PoseEstimate:
    """PnP result, including quality indicators needed by downstream code."""

    rotation_camera_object: np.ndarray
    translation_camera_object: np.ndarray
    rotation_vector: np.ndarray
    inlier_indices: np.ndarray
    inlier_ratio: float
    inlier_rmse_px: float
    all_points_rmse_px: float
    minimum_depth: float
    positive_depth: bool
    accepted: bool
    elapsed_ms: float

    @property
    def transform_camera_object(self) -> np.ndarray:
        transform = np.eye(4, dtype=np.float64)
        transform[:3, :3] = self.rotation_camera_object
        transform[:3, 3] = self.translation_camera_object
        return transform


@dataclass(frozen=True)
class SyntheticCase:
    object_points: np.ndarray
    image_points: np.ndarray
    camera_matrix: np.ndarray
    distortion_coefficients: np.ndarray
    true_rotation: np.ndarray
    true_translation: np.ndarray
    outlier_indices: np.ndarray


def _as_finite_array(
    value: Any,
    *,
    name: str,
    columns: int | None = None,
) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if columns is not None:
        if array.size % columns:
            raise ValueError(f"{name} cannot be reshaped to (-1, {columns})")
        array = array.reshape(-1, columns)
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains NaN or infinity")
    return np.ascontiguousarray(array)


def _validate_pnp_inputs(
    object_points: Any,
    image_points: Any,
    camera_matrix: Any,
    distortion_coefficients: Any,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    points_3d = _as_finite_array(object_points, name="object_points", columns=3)
    points_2d = _as_finite_array(image_points, name="image_points", columns=2)
    intrinsic = _as_finite_array(camera_matrix, name="camera_matrix")
    distortion = _as_finite_array(
        distortion_coefficients, name="distortion_coefficients"
    ).reshape(-1, 1)

    if points_3d.shape[0] != points_2d.shape[0]:
        raise ValueError("object_points and image_points must have equal lengths")
    if points_3d.shape[0] < 4:
        raise ValueError("PnP requires at least four 3D-2D correspondences")
    if intrinsic.shape != (3, 3):
        raise ValueError("camera_matrix must have shape (3, 3)")
    if intrinsic[0, 0] <= 0.0 or intrinsic[1, 1] <= 0.0:
        raise ValueError("camera focal lengths must be positive")
    if np.linalg.matrix_rank(points_3d - points_3d.mean(axis=0)) < 2:
        raise ValueError("object_points are collinear or geometrically degenerate")

    return points_3d, points_2d, intrinsic, distortion


def _reprojection_errors(
    object_points: np.ndarray,
    image_points: np.ndarray,
    rotation_vector: np.ndarray,
    translation_vector: np.ndarray,
    camera_matrix: np.ndarray,
    distortion_coefficients: np.ndarray,
) -> np.ndarray:
    projected, _ = cv2.projectPoints(
        object_points,
        rotation_vector,
        translation_vector,
        camera_matrix,
        distortion_coefficients,
    )
    return np.linalg.norm(image_points - projected.reshape(-1, 2), axis=1)


def estimate_pose_pnp(
    object_points: Any,
    image_points: Any,
    camera_matrix: Any,
    distortion_coefficients: Any = (),
    *,
    config: PnPConfig | None = None,
) -> PoseEstimate:
    """Estimate an object-to-camera pose with RANSAC and LM refinement.

    Raises:
        ValueError: Input data or configuration is invalid.
        RuntimeError: A valid PnP consensus could not be found.
    """

    settings = config or PnPConfig()
    settings.validate()
    points_3d, points_2d, intrinsic, distortion = _validate_pnp_inputs(
        object_points,
        image_points,
        camera_matrix,
        distortion_coefficients,
    )

    started = time.perf_counter()
    success, rotation_vector, translation_vector, inliers = cv2.solvePnPRansac(
        objectPoints=points_3d,
        imagePoints=points_2d,
        cameraMatrix=intrinsic,
        distCoeffs=distortion,
        iterationsCount=settings.iterations,
        reprojectionError=settings.reprojection_threshold_px,
        confidence=settings.confidence,
        flags=settings.ransac_method,
    )
    if not success or inliers is None:
        raise RuntimeError("solvePnPRansac failed to find a pose")

    inlier_indices = np.unique(inliers.reshape(-1)).astype(np.int32)
    required_inliers = max(4, settings.min_inliers)
    if inlier_indices.size < required_inliers:
        raise RuntimeError(
            f"PnP consensus has {inlier_indices.size} inliers; "
            f"at least {required_inliers} are required"
        )

    inlier_object_points = points_3d[inlier_indices]
    inlier_image_points = points_2d[inlier_indices]
    if hasattr(cv2, "solvePnPRefineLM"):
        rotation_vector, translation_vector = cv2.solvePnPRefineLM(
            objectPoints=inlier_object_points,
            imagePoints=inlier_image_points,
            cameraMatrix=intrinsic,
            distCoeffs=distortion,
            rvec=rotation_vector,
            tvec=translation_vector,
        )
    else:  # Compatibility with older OpenCV builds.
        refined, rotation_vector, translation_vector = cv2.solvePnP(
            objectPoints=inlier_object_points,
            imagePoints=inlier_image_points,
            cameraMatrix=intrinsic,
            distCoeffs=distortion,
            rvec=rotation_vector,
            tvec=translation_vector,
            useExtrinsicGuess=True,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not refined:
            raise RuntimeError("iterative PnP refinement failed")

    rotation, _ = cv2.Rodrigues(rotation_vector)
    translation = translation_vector.reshape(3)
    camera_points = (rotation @ points_3d.T).T + translation
    minimum_depth = float(np.min(camera_points[:, 2]))
    positive_depth = minimum_depth > 0.0

    all_errors = _reprojection_errors(
        points_3d,
        points_2d,
        rotation_vector,
        translation_vector,
        intrinsic,
        distortion,
    )
    inlier_errors = all_errors[inlier_indices]
    inlier_rmse = float(np.sqrt(np.mean(np.square(inlier_errors))))
    all_points_rmse = float(np.sqrt(np.mean(np.square(all_errors))))
    inlier_ratio = float(inlier_indices.size / points_3d.shape[0])
    accepted = (
        positive_depth
        and inlier_ratio >= settings.min_inlier_ratio
        and inlier_rmse <= settings.max_inlier_rmse_px
    )

    return PoseEstimate(
        rotation_camera_object=rotation,
        translation_camera_object=translation,
        rotation_vector=rotation_vector.reshape(3),
        inlier_indices=inlier_indices,
        inlier_ratio=inlier_ratio,
        inlier_rmse_px=inlier_rmse,
        all_points_rmse_px=all_points_rmse,
        minimum_depth=minimum_depth,
        positive_depth=positive_depth,
        accepted=accepted,
        elapsed_ms=(time.perf_counter() - started) * 1_000.0,
    )


def generate_synthetic_case(
    *,
    point_count: int = 80,
    noise_std_px: float = 0.8,
    outlier_ratio: float = 0.15,
    planar: bool = False,
    seed: int = 7,
) -> SyntheticCase:
    """Create a deterministic camera, pose, and noisy 3D-2D correspondences."""

    if point_count < 6:
        raise ValueError("point_count must be at least 6 for this robust test")
    if noise_std_px < 0.0:
        raise ValueError("noise_std_px cannot be negative")
    if not 0.0 <= outlier_ratio < 0.5:
        raise ValueError("outlier_ratio must be in [0, 0.5)")

    rng = np.random.default_rng(seed)
    object_points = rng.uniform(
        low=(-0.25, -0.18, -0.10),
        high=(0.25, 0.18, 0.10),
        size=(point_count, 3),
    )
    if planar:
        object_points[:, 2] = 0.0

    camera_matrix = np.array(
        [[920.0, 0.0, 640.0], [0.0, 915.0, 360.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    distortion = np.array([-0.08, 0.025, 0.0005, -0.0003, 0.0], dtype=np.float64)
    true_rotation_vector = np.deg2rad(np.array([12.0, -18.0, 25.0]))
    true_rotation, _ = cv2.Rodrigues(true_rotation_vector)
    true_translation = np.array([0.08, -0.04, 1.45], dtype=np.float64)

    image_points, _ = cv2.projectPoints(
        object_points,
        true_rotation_vector,
        true_translation,
        camera_matrix,
        distortion,
    )
    image_points = image_points.reshape(-1, 2)
    image_points += rng.normal(0.0, noise_std_px, image_points.shape)

    outlier_count = int(round(point_count * outlier_ratio))
    outlier_indices = np.sort(
        rng.choice(point_count, size=outlier_count, replace=False)
    ).astype(np.int32)
    if outlier_count:
        image_points[outlier_indices] = rng.uniform(
            low=(20.0, 20.0), high=(1260.0, 700.0), size=(outlier_count, 2)
        )

    return SyntheticCase(
        object_points=np.ascontiguousarray(object_points),
        image_points=np.ascontiguousarray(image_points),
        camera_matrix=camera_matrix,
        distortion_coefficients=distortion,
        true_rotation=true_rotation,
        true_translation=true_translation,
        outlier_indices=outlier_indices,
    )


def rotation_error_degrees(estimated: np.ndarray, truth: np.ndarray) -> float:
    """Return the geodesic rotation error in degrees."""

    relative = truth.T @ estimated
    cosine = float(np.clip((np.trace(relative) - 1.0) / 2.0, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _json_ready(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


def build_test_report(case: SyntheticCase, estimate: PoseEstimate) -> dict[str, Any]:
    translation_error = float(
        np.linalg.norm(estimate.translation_camera_object - case.true_translation)
    )
    rotation_error = rotation_error_degrees(
        estimate.rotation_camera_object, case.true_rotation
    )
    injected_outliers = set(case.outlier_indices.tolist())
    rejected = set(range(case.object_points.shape[0])) - set(
        estimate.inlier_indices.tolist()
    )
    rejected_true_outliers = len(injected_outliers & rejected)

    return {
        "accepted": estimate.accepted,
        "point_count": int(case.object_points.shape[0]),
        "inlier_count": int(estimate.inlier_indices.size),
        "inlier_ratio": estimate.inlier_ratio,
        "inlier_rmse_px": estimate.inlier_rmse_px,
        "all_points_rmse_px": estimate.all_points_rmse_px,
        "rotation_error_deg": rotation_error,
        "translation_error": translation_error,
        "positive_depth": estimate.positive_depth,
        "minimum_depth": estimate.minimum_depth,
        "injected_outlier_count": len(injected_outliers),
        "rejected_true_outlier_count": rejected_true_outliers,
        "elapsed_ms": estimate.elapsed_ms,
        "estimated_translation": estimate.translation_camera_object,
        "true_translation": case.true_translation,
        "transform_camera_object": estimate.transform_camera_object,
    }


def _print_report(report: dict[str, Any]) -> None:
    print("PnP synthetic test")
    print("-" * 56)
    print(f"Quality gate:          {'PASS' if report['accepted'] else 'FAIL'}")
    print(
        f"Inliers:               {report['inlier_count']}/{report['point_count']} "
        f"({report['inlier_ratio']:.1%})"
    )
    print(f"Inlier reproj. RMSE:   {report['inlier_rmse_px']:.3f} px")
    print(f"All-point RMSE:        {report['all_points_rmse_px']:.3f} px")
    print(f"Rotation error:        {report['rotation_error_deg']:.4f} deg")
    print(f"Translation error:     {report['translation_error']:.6f} model units")
    print(
        "Outliers rejected:     "
        f"{report['rejected_true_outlier_count']}/{report['injected_outlier_count']}"
    )
    print(f"Minimum depth:         {report['minimum_depth']:.4f} model units")
    print(f"Solver time:           {report['elapsed_ms']:.3f} ms")
    print("Estimated T_camera_object:")
    print(
        np.array2string(
            np.asarray(report["transform_camera_object"]),
            precision=6,
            suppress_small=True,
        )
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a robust OpenCV PnP test with reproducible synthetic data."
    )
    parser.add_argument("--points", type=int, default=80, help="Number of 3D points.")
    parser.add_argument(
        "--noise", type=float, default=0.8, help="Gaussian image noise in pixels."
    )
    parser.add_argument(
        "--outliers", type=float, default=0.15, help="Injected outlier ratio [0, 0.5)."
    )
    parser.add_argument("--seed", type=int, default=7, help="Random seed.")
    parser.add_argument(
        "--planar", action="store_true", help="Use coplanar rather than 3D points."
    )
    parser.add_argument(
        "--json", type=Path, help="Optional path for the machine-readable test report."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        case = generate_synthetic_case(
            point_count=args.points,
            noise_std_px=args.noise,
            outlier_ratio=args.outliers,
            planar=args.planar,
            seed=args.seed,
        )
        config = PnPConfig(min_inliers=max(6, int(math.ceil(args.points * 0.50))))
        estimate = estimate_pose_pnp(
            case.object_points,
            case.image_points,
            case.camera_matrix,
            case.distortion_coefficients,
            config=config,
        )
        report = build_test_report(case, estimate)
    except (ValueError, RuntimeError, cv2.error) as exc:
        print(f"PnP test failed: {exc}", file=sys.stderr)
        return 1

    _print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(_json_ready(report), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"JSON report:           {args.json.resolve()}")

    # Synthetic accuracy limits are deliberately separate from runtime quality gates.
    accuracy_ok = (
        report["rotation_error_deg"] < 1.0
        and report["translation_error"] < 0.02
        and report["rejected_true_outlier_count"] == report["injected_outlier_count"]
    )
    return 0 if report["accepted"] and accuracy_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

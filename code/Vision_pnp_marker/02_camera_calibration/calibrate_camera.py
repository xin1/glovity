#!/usr/bin/env python3
"""Interactive monocular-camera calibration using a printed checkerboard.

The output JSON is directly accepted by ``aruco_palm_pose.py``.

Controls:
    Space  Capture the currently detected checkerboard.
    D      Delete the most recently captured sample.
    C      Calibrate and save after enough samples have been captured.
    Q/Esc  Quit without saving.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover - runtime dependency check
    print(f"Missing dependency: {exc}", file=sys.stderr)
    print("Install with: python -m pip install numpy opencv-python", file=sys.stderr)
    raise SystemExit(2) from exc


def checkerboard_object_points(
    columns: int,
    rows: int,
    square_size: float,
) -> np.ndarray:
    """Create checkerboard inner-corner coordinates on the Z=0 plane."""

    points = np.zeros((rows * columns, 3), dtype=np.float32)
    points[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2)
    points[:, :2] *= square_size
    return points


def detect_checkerboard(
    gray: np.ndarray,
    pattern_size: tuple[int, int],
) -> tuple[bool, np.ndarray | None]:
    """Detect corners with the modern SB detector, with a legacy fallback."""

    if hasattr(cv2, "findChessboardCornersSB"):
        flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
        found, corners = cv2.findChessboardCornersSB(gray, pattern_size, flags)
        if found:
            return True, np.asarray(corners, dtype=np.float32)

    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    found, corners = cv2.findChessboardCorners(gray, pattern_size, flags)
    if not found:
        return False, None
    termination = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
        40,
        0.001,
    )
    refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), termination)
    return True, refined


def per_view_reprojection_errors(
    object_points: list[np.ndarray],
    image_points: list[np.ndarray],
    rotation_vectors: Sequence[np.ndarray],
    translation_vectors: Sequence[np.ndarray],
    camera_matrix: np.ndarray,
    distortion_coefficients: np.ndarray,
) -> list[float]:
    errors: list[float] = []
    for points_3d, points_2d, rotation, translation in zip(
        object_points,
        image_points,
        rotation_vectors,
        translation_vectors,
    ):
        projected, _ = cv2.projectPoints(
            points_3d,
            rotation,
            translation,
            camera_matrix,
            distortion_coefficients,
        )
        residuals = projected.reshape(-1, 2) - points_2d.reshape(-1, 2)
        errors.append(float(np.sqrt(np.mean(np.sum(residuals * residuals, axis=1)))))
    return errors


def calibrate_and_save(
    image_points: list[np.ndarray],
    image_size: tuple[int, int],
    pattern_size: tuple[int, int],
    square_size: float,
    output_path: Path,
) -> float:
    template = checkerboard_object_points(*pattern_size, square_size)
    object_points = [template.copy() for _ in image_points]
    rms, camera_matrix, distortion, rotations, translations = cv2.calibrateCamera(
        object_points,
        image_points,
        image_size,
        None,
        None,
    )
    if not np.isfinite(camera_matrix).all() or not np.isfinite(distortion).all():
        raise RuntimeError("calibration returned non-finite parameters")
    per_view_errors = per_view_reprojection_errors(
        object_points,
        image_points,
        rotations,
        translations,
        camera_matrix,
        distortion,
    )
    report = {
        "camera_matrix": camera_matrix.tolist(),
        "distortion_coefficients": distortion.reshape(-1).tolist(),
        "image_width": image_size[0],
        "image_height": image_size[1],
        "calibration_rms_px": float(rms),
        "per_view_rmse_px": per_view_errors,
        "sample_count": len(image_points),
        "checkerboard_inner_corners": {
            "columns": pattern_size[0],
            "rows": pattern_size[1],
        },
        "square_size_m": square_size,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return float(rms)


def draw_status(
    frame: np.ndarray,
    found: bool,
    captured_count: int,
    minimum_samples: int,
) -> None:
    color = (0, 220, 0) if found else (0, 80, 255)
    lines = (
        f"Checkerboard: {'DETECTED' if found else 'NOT FOUND'}",
        f"Captured: {captured_count}/{minimum_samples} minimum",
        "SPACE capture | D delete | C calibrate | Q quit",
    )
    for index, line in enumerate(lines):
        position = (15, 30 + index * 28)
        cv2.putText(
            frame,
            line,
            position,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            line,
            position,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            1,
            cv2.LINE_AA,
        )


def run(args: argparse.Namespace) -> int:
    pattern_size = (args.columns, args.rows)
    capture = cv2.VideoCapture(args.camera)
    if args.width:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height:
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open camera index {args.camera}")

    captured_image_points: list[np.ndarray] = []
    image_size: tuple[int, int] | None = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("camera stopped returning frames")
            current_size = (frame.shape[1], frame.shape[0])
            if image_size is None:
                image_size = current_size
            elif current_size != image_size:
                raise RuntimeError("camera resolution changed during calibration")

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = detect_checkerboard(gray, pattern_size)
            display = frame.copy()
            if found and corners is not None:
                cv2.drawChessboardCorners(display, pattern_size, corners, found)
            draw_status(display, found, len(captured_image_points), args.min_samples)
            cv2.imshow("Camera Calibration", display)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                print("Calibration cancelled; no file was written.")
                return 1
            if key == ord("d"):
                if captured_image_points:
                    captured_image_points.pop()
                    print(f"Deleted last sample; {len(captured_image_points)} remain.")
                continue
            if key == ord(" "):
                if not found or corners is None:
                    print("Capture skipped: checkerboard was not detected.")
                    continue
                captured_image_points.append(corners.copy())
                print(f"Captured sample {len(captured_image_points)}.")
                continue
            if key == ord("c"):
                if len(captured_image_points) < args.min_samples:
                    print(
                        f"Need at least {args.min_samples} samples; "
                        f"currently have {len(captured_image_points)}."
                    )
                    continue
                assert image_size is not None
                rms = calibrate_and_save(
                    captured_image_points,
                    image_size,
                    pattern_size,
                    args.square_size,
                    args.output,
                )
                print(f"Calibration RMS: {rms:.4f} px")
                print(f"Saved calibration: {args.output.resolve()}")
                if rms > 1.5:
                    print(
                        "Warning: RMS is high. Recapture sharp images with wider "
                        "position and angle coverage.",
                        file=sys.stderr,
                    )
                return 0
    finally:
        capture.release()
        cv2.destroyAllWindows()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate a camera from manually captured checkerboard views."
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=9,
        help="Number of inner corners horizontally (not square count).",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=6,
        help="Number of inner corners vertically (not square count).",
    )
    parser.add_argument(
        "--square-size",
        type=float,
        required=True,
        help="Physical checkerboard square side length in metres.",
    )
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--min-samples", type=int, default=15)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("camera_calibration.json"),
    )
    args = parser.parse_args(argv)
    if args.columns < 3 or args.rows < 3:
        parser.error("checkerboard must have at least 3x3 inner corners")
    if args.square_size <= 0.0:
        parser.error("--square-size must be positive")
    if args.min_samples < 8:
        parser.error("--min-samples must be at least 8")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (RuntimeError, OSError, cv2.error) as exc:
        print(f"Calibration error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Robust full 3x3 ellipsoid calibration for a three-axis magnetometer.

The column-vector convention used everywhere is:

    calibrated = soft_iron_matrix @ (raw - hard_iron_offset)

The fitted ellipsoid is constrained to be positive definite.  A robust
non-linear fit and a second inlier-only fit prevent a few disturbed samples
from dominating the result.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from paths import (
    ARDUINO_HEADER,
    ARDUINO_RAW_HEADER,
    ARDUINO_STREAM_HEADER,
    CALIBRATED_CSV,
    CALIBRATION_JSON,
    DATA_DIR,
    MAGNETOMETER_CSV,
    PROJECT_DIR,
    QUALITY_REPORT,
)


@dataclass(frozen=True)
class FitResult:
    offset: np.ndarray
    matrix: np.ndarray
    quadratic_form: np.ndarray
    axes: np.ndarray
    eigenvectors: np.ndarray
    target_radius: float
    inlier_mask: np.ndarray
    normalized_residuals: np.ndarray
    optimizer_cost: float
    optimizer_evaluations: int


def load_csv(path: Path) -> tuple[np.ndarray, int]:
    """Load the first three numeric columns, tolerating headers/comments."""
    rows: list[list[float]] = []
    ignored = 0
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        for row in csv.reader(source):
            if not row or row[0].lstrip().startswith("#"):
                ignored += 1
                continue
            try:
                values = [float(row[index].strip()) for index in range(3)]
            except (IndexError, ValueError):
                ignored += 1
                continue
            if not np.all(np.isfinite(values)):
                ignored += 1
                continue
            rows.append(values)

    data = np.asarray(rows, dtype=np.float64)
    if data.shape[0] < 100:
        raise ValueError(f"有效样本只有 {data.shape[0]} 个；至少需要 100 个")
    if np.linalg.matrix_rank(data - np.mean(data, axis=0)) < 3:
        raise ValueError("数据没有覆盖三维空间，无法拟合完整椭球")
    return data, ignored


def _nearest_spd(matrix: np.ndarray, minimum_ratio: float = 1e-4) -> np.ndarray:
    symmetric = 0.5 * (matrix + matrix.T)
    values, vectors = np.linalg.eigh(symmetric)
    floor = max(float(np.max(np.abs(values))) * minimum_ratio, 1e-8)
    return vectors @ np.diag(np.maximum(values, floor)) @ vectors.T


def _algebraic_initial_fit(normalized: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return an SPD center/quadratic-form initializer in normalized units."""
    x, y, z = normalized.T
    design = np.column_stack(
        (x * x, y * y, z * z, 2 * x * y, 2 * x * z, 2 * y * z,
         2 * x, 2 * y, 2 * z)
    )
    coefficients, *_ = np.linalg.lstsq(design, np.ones(len(normalized)), rcond=None)
    quadratic = np.array(
        [
            [coefficients[0], coefficients[3], coefficients[4]],
            [coefficients[3], coefficients[1], coefficients[5]],
            [coefficients[4], coefficients[5], coefficients[2]],
        ]
    )
    quadratic = _nearest_spd(quadratic)
    linear = coefficients[6:9]
    center = -np.linalg.solve(quadratic, linear)
    level = 1.0 + float(center @ quadratic @ center)
    if not np.isfinite(level) or level <= 1e-8:
        center = np.zeros(3)
        level = 1.0
    return center, _nearest_spd(quadratic / level)


def _pack(center: np.ndarray, quadratic: np.ndarray) -> np.ndarray:
    lower = np.linalg.cholesky(_nearest_spd(quadratic))
    return np.array(
        [center[0], center[1], center[2], math.log(lower[0, 0]),
         lower[1, 0], math.log(lower[1, 1]), lower[2, 0], lower[2, 1],
         math.log(lower[2, 2])],
        dtype=np.float64,
    )


def _unpack(parameters: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = parameters[:3]
    lower = np.array(
        [
            [math.exp(parameters[3]), 0.0, 0.0],
            [parameters[4], math.exp(parameters[5]), 0.0],
            [parameters[6], parameters[7], math.exp(parameters[8])],
        ]
    )
    return center, lower @ lower.T


def _radial_residuals(
    parameters: np.ndarray, normalized: np.ndarray
) -> np.ndarray:
    center, quadratic = _unpack(parameters)
    centered = normalized - center
    squared = np.einsum("ni,ij,nj->n", centered, quadratic, centered)
    return np.sqrt(np.maximum(squared, np.finfo(float).tiny)) - 1.0


def _mad_sigma(values: np.ndarray) -> float:
    median = float(np.median(values))
    return 1.4826 * float(np.median(np.abs(values - median)))


def fit_ellipsoid(
    data: np.ndarray,
    target_radius: float | None = None,
    loss_scale: float = 0.03,
    outlier_sigma: float = 4.5,
) -> FitResult:
    """Fit a robust SPD ellipsoid and return a symmetric full 3x3 transform."""
    location = np.median(data, axis=0)
    unit = float(np.median(np.linalg.norm(data - location, axis=1)))
    if not np.isfinite(unit) or unit <= 0:
        raise ValueError("数据尺度无效")
    normalized = (data - location) / unit

    center0, quadratic0 = _algebraic_initial_fit(normalized)
    lower_bounds = np.array([-10, -10, -10, -8, -10, -8, -10, -10, -8], dtype=float)
    upper_bounds = np.array([10, 10, 10, 8, 10, 8, 10, 10, 8], dtype=float)
    initial = np.clip(_pack(center0, quadratic0), lower_bounds, upper_bounds)
    robust = least_squares(
        _radial_residuals,
        initial,
        args=(normalized,),
        loss="cauchy",
        f_scale=loss_scale,
        x_scale="jac",
        bounds=(lower_bounds, upper_bounds),
        max_nfev=4000,
    )
    if not robust.success:
        raise RuntimeError(f"稳健椭球拟合失败：{robust.message}")

    first_residuals = _radial_residuals(robust.x, normalized)
    sigma = max(_mad_sigma(first_residuals), 0.002)
    median = float(np.median(first_residuals))
    threshold = max(outlier_sigma * sigma, 0.025)
    inliers = np.abs(first_residuals - median) <= threshold
    if np.count_nonzero(inliers) < max(100, int(0.6 * len(data))):
        raise RuntimeError("稳健筛选后有效点过少；请重新采集完整球面数据")

    refined = least_squares(
        _radial_residuals,
        robust.x,
        args=(normalized[inliers],),
        loss="soft_l1",
        f_scale=max(loss_scale / 2.0, 0.005),
        x_scale="jac",
        bounds=(lower_bounds, upper_bounds),
        max_nfev=4000,
    )
    if not refined.success:
        raise RuntimeError(f"椭球精修失败：{refined.message}")

    center_n, quadratic_n = _unpack(refined.x)
    offset = location + unit * center_n
    quadratic = quadratic_n / (unit * unit)
    eigenvalues, eigenvectors = np.linalg.eigh(quadratic)
    if np.any(eigenvalues <= 0):
        raise RuntimeError("拟合结果不是正定椭球")
    axes = 1.0 / np.sqrt(eigenvalues)
    if target_radius is None:
        target_radius = float(np.exp(np.mean(np.log(axes))))
    if not np.isfinite(target_radius) or target_radius <= 0:
        raise ValueError("目标半径必须是正数")

    sqrt_quadratic = eigenvectors @ np.diag(np.sqrt(eigenvalues)) @ eigenvectors.T
    matrix = target_radius * sqrt_quadratic
    centered = data - offset
    normalized_residuals = (
        np.linalg.norm(centered @ matrix.T, axis=1) / target_radius - 1.0
    )
    final_median = float(np.median(normalized_residuals))
    final_sigma = max(_mad_sigma(normalized_residuals), 0.002)
    final_threshold = max(outlier_sigma * final_sigma, 0.025)
    final_inliers = np.abs(normalized_residuals - final_median) <= final_threshold

    return FitResult(
        offset=offset,
        matrix=0.5 * (matrix + matrix.T),
        quadratic_form=quadratic,
        axes=axes,
        eigenvectors=eigenvectors,
        target_radius=float(target_radius),
        inlier_mask=final_inliers,
        normalized_residuals=normalized_residuals,
        optimizer_cost=float(refined.cost),
        optimizer_evaluations=int(robust.nfev + refined.nfev),
    )


def apply_calibration(data: np.ndarray, fit: FitResult) -> np.ndarray:
    return (data - fit.offset) @ fit.matrix.T


def quality_metrics(data: np.ndarray, calibrated: np.ndarray, fit: FitResult) -> dict:
    mask = fit.inlier_mask
    radii = np.linalg.norm(calibrated, axis=1)
    inlier_radii = radii[mask]
    raw_radii = np.linalg.norm(data - fit.offset, axis=1)
    directions = calibrated[mask] / inlier_radii[:, None]
    direction_covariance = directions.T @ directions / len(directions)
    coverage_values = np.linalg.eigvalsh(direction_covariance)
    octants = set(map(tuple, (directions >= 0).astype(int)))
    relative_error = inlier_radii / fit.target_radius - 1.0
    matrix_values = np.linalg.eigvalsh(fit.matrix)

    metrics = {
        "sample_count": int(len(data)),
        "inlier_count": int(np.count_nonzero(mask)),
        "rejected_count": int(np.count_nonzero(~mask)),
        "inlier_ratio": float(np.mean(mask)),
        "raw_radius_mean": float(np.mean(raw_radii)),
        "raw_radius_std": float(np.std(raw_radii)),
        "raw_radius_cv": float(np.std(raw_radii) / np.mean(raw_radii)),
        "calibrated_radius_mean": float(np.mean(inlier_radii)),
        "calibrated_radius_std": float(np.std(inlier_radii)),
        "calibrated_radius_cv": float(np.std(inlier_radii) / np.mean(inlier_radii)),
        "relative_rms_error": float(np.sqrt(np.mean(relative_error**2))),
        "relative_p95_abs_error": float(np.percentile(np.abs(relative_error), 95)),
        "axis_ratio": float(np.max(fit.axes) / np.min(fit.axes)),
        "matrix_condition_number": float(np.max(matrix_values) / np.min(matrix_values)),
        "occupied_octants": len(octants),
        "direction_coverage_condition": float(coverage_values[-1] / coverage_values[0]),
        "optimizer_cost": fit.optimizer_cost,
        "optimizer_evaluations": fit.optimizer_evaluations,
    }
    checks = {
        "enough_samples": metrics["sample_count"] >= 500,
        "enough_inliers": metrics["inlier_ratio"] >= 0.90,
        "all_octants_covered": metrics["occupied_octants"] == 8,
        "direction_coverage_reasonable": metrics["direction_coverage_condition"] <= 10.0,
        "radius_cv_below_5_percent": metrics["calibrated_radius_cv"] <= 0.05,
        "p95_error_below_10_percent": metrics["relative_p95_abs_error"] <= 0.10,
        "matrix_condition_below_5": metrics["matrix_condition_number"] <= 5.0,
    }
    metrics["checks"] = checks
    metrics["passed"] = bool(all(checks.values()))
    return metrics


def _float_rows(matrix: np.ndarray) -> list[list[float]]:
    return [[float(value) for value in row] for row in matrix]


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return str(resolved)


def build_json(
    source: Path, ignored_rows: int, fit: FitResult, quality: dict
) -> dict:
    return {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": _portable_path(source),
        "model": "robust_positive_definite_full_3x3_ellipsoid",
        "convention": {
            "vector": "column",
            "formula": "calibrated = soft_iron_matrix @ (raw - hard_iron_offset)",
            "input_unit": "magnetometer counts supplied by the capture source",
            "output_unit": "same scale as the input magnetometer counts",
        },
        "hard_iron_offset": fit.offset.tolist(),
        "soft_iron_matrix": _float_rows(fit.matrix),
        "target_radius": fit.target_radius,
        "ellipsoid": {
            "semi_axes": fit.axes.tolist(),
            "principal_axes_columns": _float_rows(fit.eigenvectors),
            "quadratic_form": _float_rows(fit.quadratic_form),
        },
        "robust_fit": {
            "ignored_input_rows": ignored_rows,
            "inlier_mask_rule": "abs(radial residual - median) <= max(4.5*MAD_sigma, 2.5%)",
        },
        "quality": quality,
        # Compatibility aliases for older local visualization scripts.
        "offset_hard_iron": fit.offset.tolist(),
        "calibration_matrix": _float_rows(fit.matrix),
        "bias": fit.offset.tolist(),
        "rotation_matrix": np.eye(3).tolist(),
        "ellipsoid_center": fit.offset.tolist(),
        "ellipsoid_radii": fit.axes.tolist(),
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_calibrated_csv(path: Path, calibrated: np.ndarray, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("mx_cal", "my_cal", "mz_cal", "fit_inlier"))
        for values, inlier in zip(calibrated, mask):
            writer.writerow((*[f"{value:.9f}" for value in values], int(inlier)))


def write_header(path: Path, fit: FitResult) -> None:
    offset = ", ".join(f"{value:.9f}f" for value in fit.offset)
    rows = ",\n".join(
        "    {" + ", ".join(f"{value:.9f}f" for value in row) + "}"
        for row in fit.matrix
    )
    text = f"""// Generated by mag_calibration/calibrate_magnetometer.py. Do not edit.
#pragma once

constexpr float MAG_HARD_IRON_OFFSET[3] = {{{offset}}};
constexpr float MAG_SOFT_IRON_MATRIX[3][3] = {{
{rows}
}};

inline void applyMagCalibration(float rawX, float rawY, float rawZ,
                                float &outX, float &outY, float &outZ) {{
  const float centered[3] = {{
      rawX - MAG_HARD_IRON_OFFSET[0],
      rawY - MAG_HARD_IRON_OFFSET[1],
      rawZ - MAG_HARD_IRON_OFFSET[2],
  }};
  outX = MAG_SOFT_IRON_MATRIX[0][0] * centered[0] +
         MAG_SOFT_IRON_MATRIX[0][1] * centered[1] +
         MAG_SOFT_IRON_MATRIX[0][2] * centered[2];
  outY = MAG_SOFT_IRON_MATRIX[1][0] * centered[0] +
         MAG_SOFT_IRON_MATRIX[1][1] * centered[1] +
         MAG_SOFT_IRON_MATRIX[1][2] * centered[2];
  outZ = MAG_SOFT_IRON_MATRIX[2][0] * centered[0] +
         MAG_SOFT_IRON_MATRIX[2][1] * centered[1] +
         MAG_SOFT_IRON_MATRIX[2][2] * centered[2];
}}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_report(path: Path, source: Path, fit: FitResult, quality: dict) -> None:
    checks = "\n".join(
        f"- {'PASS' if passed else 'FAIL'} — `{name}`"
        for name, passed in quality["checks"].items()
    )
    matrix = "\n".join(
        "  [" + ", ".join(f"{value: .9f}" for value in row) + "]"
        for row in fit.matrix
    )
    report = f"""# 三轴磁力计校准质量报告

- 输入：`{_portable_path(source)}`
- 样本：{quality['sample_count']}（拟合内点 {quality['inlier_count']}，剔除 {quality['rejected_count']}）
- 总体结论：**{'PASS' if quality['passed'] else 'FAIL'}**

## 校准参数

硬铁偏置：`[{', '.join(f'{value:.9f}' for value in fit.offset)}]`

完整 3×3 软铁矩阵：

```text
{matrix}
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | {fit.target_radius:.6f} |
| 原始半径 CV | {quality['raw_radius_cv']:.3%} |
| 校准后半径均值 | {quality['calibrated_radius_mean']:.6f} |
| 校准后半径标准差 | {quality['calibrated_radius_std']:.6f} |
| 校准后半径 CV | {quality['calibrated_radius_cv']:.3%} |
| 相对 RMS 误差 | {quality['relative_rms_error']:.3%} |
| 相对绝对误差 P95 | {quality['relative_p95_abs_error']:.3%} |
| 椭球长短轴比 | {quality['axis_ratio']:.6f} |
| 校准矩阵条件数 | {quality['matrix_condition_number']:.6f} |
| 覆盖八分体 | {quality['occupied_octants']} / 8 |
| 方向覆盖条件数 | {quality['direction_coverage_condition']:.6f} |

## 自动检查

{checks}

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="稳健完整 3x3 三轴磁力计椭球校准")
    parser.add_argument("--input", type=Path, default=MAGNETOMETER_CSV)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--target-radius", type=float, default=None,
                        help="输出球半径；默认使用拟合椭球三条半轴的几何平均")
    parser.add_argument("--loss-scale", type=float, default=0.03,
                        help="Cauchy 稳健损失尺度（相对半径，默认 0.03）")
    parser.add_argument("--outlier-sigma", type=float, default=4.5)
    parser.add_argument("--no-calibrated-csv", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    json_path = output_dir / CALIBRATION_JSON.name
    report_path = output_dir / QUALITY_REPORT.name
    header_path = output_dir / ARDUINO_HEADER.name
    calibrated_path = output_dir / CALIBRATED_CSV.name

    try:
        data, ignored = load_csv(args.input)
        fit = fit_ellipsoid(data, args.target_radius, args.loss_scale, args.outlier_sigma)
        calibrated = apply_calibration(data, fit)
        quality = quality_metrics(data, calibrated, fit)
        payload = build_json(args.input, ignored, fit, quality)
        write_json(json_path, payload)
        write_report(report_path, args.input, fit, quality)
        write_header(header_path, fit)
        write_header(ARDUINO_STREAM_HEADER, fit)
        write_header(ARDUINO_RAW_HEADER, fit)
        if not args.no_calibrated_csv:
            write_calibrated_csv(calibrated_path, calibrated, fit.inlier_mask)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"校准失败：{exc}", file=sys.stderr)
        return 2

    print(f"已加载 {len(data)} 点；内点 {np.count_nonzero(fit.inlier_mask)}，剔除 {np.count_nonzero(~fit.inlier_mask)}")
    print(f"硬铁偏置：{np.array2string(fit.offset, precision=9)}")
    print("完整 3x3 软铁矩阵：")
    print(np.array2string(fit.matrix, precision=9, suppress_small=False))
    print(f"校准后半径 CV：{quality['calibrated_radius_cv']:.3%}")
    print(f"P95 相对误差：{quality['relative_p95_abs_error']:.3%}")
    print(f"质量检查：{'PASS' if quality['passed'] else 'FAIL'}")
    print(f"JSON：{json_path}")
    print(f"质量报告：{report_path}")
    print(f"Arduino 头文件：{header_path}")
    print(f"Arduino 草图同步：{ARDUINO_STREAM_HEADER.parent}，{ARDUINO_RAW_HEADER.parent}")
    if not args.no_calibrated_csv:
        print(f"校准后数据：{calibrated_path}")
    return 0 if quality["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

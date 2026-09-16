#!/usr/bin/env python3
"""Live magnetometer coverage, capture, and non-destructive calibration."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import serial
from matplotlib.widgets import Button
from serial.tools import list_ports


CALIBRATION_DIR = Path(__file__).resolve().parent
PROJECT_DIR = CALIBRATION_DIR.parent
if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

from calibrate_magnetometer import (  # noqa: E402
    apply_calibration,
    build_json,
    fit_ellipsoid,
    quality_metrics,
    write_calibrated_csv,
    write_header,
    write_json,
    write_report,
)
from interactive_calibration_visualization import write_visualization  # noqa: E402
from paths import CALIBRATION_JSON, CALIBRATION_RUNS_DIR  # noqa: E402


SAMPLE_PATTERN = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,"
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,"
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)

RESULT_COLUMNS = (
    "run_id", "started_at_utc", "finished_at_utc", "port", "baud",
    "duration_s", "sample_rate_hz", "sample_count", "inlier_count",
    "inlier_ratio", "calibrated_radius_cv", "relative_p95_abs_error",
    "occupied_octants", "direction_coverage_condition",
    "matrix_condition_number", "passed", "run_directory", "error",
)


@dataclass
class CaptureState:
    samples: list[tuple[float, float, float]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    ready_event: threading.Event = field(default_factory=threading.Event)
    action: str = "running"
    error: str | None = None
    capture_started: float | None = None
    capture_finished: float | None = None

    def snapshot(self) -> np.ndarray:
        with self.lock:
            if not self.samples:
                return np.empty((0, 3), dtype=float)
            return np.asarray(self.samples, dtype=float).copy()


def available_ports() -> str:
    ports = list(list_ports.comports())
    if not ports:
        return "  (未发现串口)"
    return "\n".join(f"  {item.device}: {item.description}" for item in ports)


def create_run_directory(root: Path) -> tuple[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    for suffix in range(1000):
        run_id = base if suffix == 0 else f"{base}_{suffix:03d}"
        run_dir = root / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        return run_id, run_dir
    raise RuntimeError("无法创建唯一的试验目录")


def load_preview_center(path: Path) -> tuple[np.ndarray | None, str]:
    if not path.is_file():
        return None, "live range midpoint"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        center = np.asarray(payload["hard_iron_offset"], dtype=float)
        if center.shape == (3,) and np.all(np.isfinite(center)):
            return center, "saved calibration offset (preview only)"
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return None, "live range midpoint"


def preview_center(data: np.ndarray, saved_center: np.ndarray | None) -> np.ndarray:
    if saved_center is not None:
        return saved_center
    if len(data) == 0:
        return np.zeros(3)
    low = np.percentile(data, 2.0, axis=0)
    high = np.percentile(data, 98.0, axis=0)
    return 0.5 * (low + high)


def coverage_metrics(
    data: np.ndarray,
    center: np.ndarray,
    azimuth_bins: int = 12,
    vertical_bins: int = 6,
) -> dict:
    total_bins = azimuth_bins * vertical_bins
    empty_counts = np.zeros(total_bins, dtype=int)
    if len(data) == 0:
        return {
            "counts": empty_counts,
            "directions": np.empty((0, 3)),
            "covered_bins": 0,
            "total_bins": total_bins,
            "octants": 0,
            "condition": math.inf,
        }

    centered = data - center
    norms = np.linalg.norm(centered, axis=1)
    valid = np.isfinite(norms) & (norms > 1e-9)
    directions = centered[valid] / norms[valid, None]
    if len(directions) == 0:
        return {
            "counts": empty_counts,
            "directions": directions,
            "covered_bins": 0,
            "total_bins": total_bins,
            "octants": 0,
            "condition": math.inf,
        }

    azimuth = np.mod(np.arctan2(directions[:, 1], directions[:, 0]), 2 * math.pi)
    az_index = np.minimum((azimuth / (2 * math.pi) * azimuth_bins).astype(int), azimuth_bins - 1)
    z_index = np.minimum(
        (((directions[:, 2] + 1.0) * 0.5) * vertical_bins).astype(int),
        vertical_bins - 1,
    )
    z_index = np.maximum(z_index, 0)
    flat_index = z_index * azimuth_bins + az_index
    counts = np.bincount(flat_index, minlength=total_bins)
    covariance = directions.T @ directions / len(directions)
    eigenvalues = np.linalg.eigvalsh(covariance)
    condition = float(eigenvalues[-1] / max(eigenvalues[0], 1e-12))
    octants = len(set(map(tuple, (directions >= 0).astype(int))))
    return {
        "counts": counts,
        "directions": directions,
        "covered_bins": int(np.count_nonzero(counts >= 3)),
        "total_bins": total_bins,
        "octants": octants,
        "condition": condition,
    }


def coverage_bin_centers(azimuth_bins: int = 12, vertical_bins: int = 6) -> np.ndarray:
    points = []
    for z_index in range(vertical_bins):
        z = -1.0 + (z_index + 0.5) * 2.0 / vertical_bins
        radius = math.sqrt(max(0.0, 1.0 - z * z))
        for az_index in range(azimuth_bins):
            azimuth = (az_index + 0.5) * 2.0 * math.pi / azimuth_bins
            points.append((radius * math.cos(azimuth), radius * math.sin(azimuth), z))
    return np.asarray(points)


def serial_capture_worker(
    port_name: str,
    baud: int,
    raw_path: Path,
    state: CaptureState,
) -> None:
    try:
        with serial.Serial(port_name, baud, timeout=0.1) as port, raw_path.open(
            "w", newline="", encoding="utf-8"
        ) as output:
            writer = csv.writer(output, lineterminator="\n")
            time.sleep(2.0)
            port.reset_input_buffer()
            state.capture_started = time.monotonic()
            state.ready_event.set()
            since_flush = 0
            while not state.stop_event.is_set():
                line = port.readline().decode("ascii", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("#"):
                    print(line)
                    continue
                match = SAMPLE_PATTERN.match(line)
                if not match:
                    continue
                values = tuple(float(value) for value in match.groups())
                if not all(math.isfinite(value) for value in values):
                    continue
                writer.writerow((f"{values[0]:.9f}", f"{values[1]:.9f}", f"{values[2]:.9f}"))
                with state.lock:
                    state.samples.append(values)
                since_flush += 1
                if since_flush >= 100:
                    output.flush()
                    since_flush = 0
            output.flush()
    except (OSError, serial.SerialException) as exc:
        state.error = str(exc)
        state.ready_event.set()
    finally:
        state.capture_finished = time.monotonic()


def set_equal_limits(axis, data: np.ndarray) -> None:
    if len(data) == 0:
        axis.set_xlim(-1, 1)
        axis.set_ylim(-1, 1)
        axis.set_zlim(-1, 1)
        return
    low = np.min(data, axis=0)
    high = np.max(data, axis=0)
    center = 0.5 * (low + high)
    half_span = max(float(np.max(high - low)) * 0.55, 1.0)
    axis.set_xlim(center[0] - half_span, center[0] + half_span)
    axis.set_ylim(center[1] - half_span, center[1] + half_span)
    axis.set_zlim(center[2] - half_span, center[2] + half_span)


def append_result(path: Path, result: dict) -> tuple[int, int, float]:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header_row = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=RESULT_COLUMNS, lineterminator="\n")
        if write_header_row:
            writer.writeheader()
        writer.writerow({column: result.get(column, "") for column in RESULT_COLUMNS})

    completed = 0
    passed = 0
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            value = row.get("passed", "").strip()
            if value not in ("0", "1"):
                continue
            completed += 1
            passed += value == "1"
    rate = passed / completed if completed else 0.0
    return passed, completed, rate


def finalize_run(
    data: np.ndarray,
    raw_path: Path,
    run_id: str,
    run_dir: Path,
    metadata: dict,
) -> tuple[dict, Path]:
    fit = fit_ellipsoid(data)
    calibrated = apply_calibration(data, fit)
    quality = quality_metrics(data, calibrated, fit)
    payload = build_json(raw_path, 0, fit, quality)
    payload["trial"] = metadata

    json_path = run_dir / "calibration_matrix.json"
    report_path = run_dir / "calibration_quality_report.md"
    header_path = run_dir / "mag_calibration.h"
    calibrated_path = run_dir / "magnetometer_data_calibrated.csv"
    html_path = run_dir / "calibration_interactive.html"

    write_json(json_path, payload)
    write_report(report_path, raw_path, fit, quality)
    write_header(header_path, fit)
    write_calibrated_csv(calibrated_path, calibrated, fit.inlier_mask)
    write_visualization(
        data,
        fit.offset,
        fit.matrix,
        html_path,
        title=f"Calibration trial {run_id} — {'PASS' if quality['passed'] else 'FAIL'}",
    )
    return quality, html_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="实时显示磁力计方向覆盖，并将每次校准试验保存到独立目录"
    )
    parser.add_argument("port", help="串口，例如 COM6 或 /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-root", type=Path, default=CALIBRATION_RUNS_DIR)
    parser.add_argument("--refresh-hz", type=float, default=10.0,
                        help="图形刷新频率，默认10 Hz；不改变磁力计采样率")
    parser.add_argument("--max-display-points", type=int, default=5000,
                        help="实时图最多显示的点数；CSV仍保存全部数据")
    parser.add_argument("--no-open-html", action="store_true",
                        help="完成后生成但不自动打开HTML")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.refresh_hz <= 0 or args.max_display_points < 100:
        print("参数无效：refresh-hz必须大于0，max-display-points至少100。", file=sys.stderr)
        return 2

    run_id, run_dir = create_run_directory(args.output_root.resolve())
    raw_path = run_dir / "magnetometer_data.csv"
    results_path = args.output_root.resolve() / "results.csv"
    preview_saved_center, center_source = load_preview_center(CALIBRATION_JSON)
    started_at = datetime.now(timezone.utc)
    state = CaptureState()

    print("可用串口：")
    print(available_ports())
    print(f"\n试验编号：{run_id}")
    print(f"原始数据将保存到：{raw_path}")
    print("本程序不会覆盖正式 magnetometer_data.csv 或 Arduino 校准头文件。")

    worker = threading.Thread(
        target=serial_capture_worker,
        args=(args.port, args.baud, raw_path, state),
        daemon=True,
    )
    worker.start()

    figure = plt.figure(figsize=(13.5, 7.6))
    if hasattr(figure.canvas.manager, "set_window_title"):
        figure.canvas.manager.set_window_title("Live magnetometer calibration coverage")
    raw_axis = figure.add_subplot(1, 2, 1, projection="3d")
    coverage_axis = figure.add_subplot(1, 2, 2, projection="3d")
    figure.subplots_adjust(bottom=0.18, top=0.88, wspace=0.15)

    raw_axis.set_title("Raw samples")
    raw_axis.set_xlabel("X")
    raw_axis.set_ylabel("Y")
    raw_axis.set_zlabel("Z")
    raw_scatter = raw_axis.scatter([], [], [], s=4, alpha=0.45, color="#2878b5")
    set_equal_limits(raw_axis, np.empty((0, 3)))

    coverage_axis.set_title("Direction coverage (red = missing)")
    coverage_axis.set_xlabel("X")
    coverage_axis.set_ylabel("Y")
    coverage_axis.set_zlabel("Z")
    coverage_axis.set_xlim(-1.1, 1.1)
    coverage_axis.set_ylim(-1.1, 1.1)
    coverage_axis.set_zlim(-1.1, 1.1)
    coverage_axis.set_box_aspect((1, 1, 1))
    bin_points = coverage_bin_centers()
    coverage_scatter = coverage_axis.scatter(
        bin_points[:, 0], bin_points[:, 1], bin_points[:, 2],
        s=45, alpha=0.85, c=["#d62728"] * len(bin_points),
    )

    status_text = figure.text(
        0.02, 0.955, "Connecting to the serial port...", va="top",
        family="monospace", fontsize=10
    )
    hint_text = figure.text(
        0.02, 0.115,
        "Rotate slowly around all three axes. Cover the red directions on the right.\n"
        "Finish after at least 500 samples and 8/8 octants.",
        fontsize=10,
    )
    finish_button = Button(figure.add_axes((0.72, 0.035, 0.12, 0.055)), "Finish")
    cancel_button = Button(figure.add_axes((0.86, 0.035, 0.10, 0.055)), "Cancel")

    def finish(_event) -> None:
        count = len(state.snapshot())
        if count < 100:
            hint_text.set_text(
                f"Only {count} valid samples. At least 100 are required for fitting; "
                "500 or more are recommended."
            )
            figure.canvas.draw_idle()
            return
        state.action = "finish"

    def cancel(_event) -> None:
        state.action = "cancel"

    def close_window(_event) -> None:
        if state.action == "running":
            state.action = "cancel"

    finish_button.on_clicked(finish)
    cancel_button.on_clicked(cancel)
    figure.canvas.mpl_connect("close_event", close_window)

    last_draw = 0.0
    last_limit_update = 0.0
    try:
        plt.show(block=False)
        while state.action == "running" and plt.fignum_exists(figure.number):
            if state.error is not None:
                status_text.set_text(f"Serial port error: {state.error}")
                figure.canvas.draw_idle()
                state.action = "error"
                break

            now = time.monotonic()
            if now - last_draw >= 1.0 / args.refresh_hz:
                data = state.snapshot()
                display = data
                if len(display) > args.max_display_points:
                    indices = np.linspace(0, len(display) - 1, args.max_display_points).astype(int)
                    display = display[indices]
                if len(display):
                    raw_scatter._offsets3d = (display[:, 0], display[:, 1], display[:, 2])
                center = preview_center(data, preview_saved_center)
                coverage = coverage_metrics(data, center)
                colors = [
                    "#2ca02c" if count >= 3 else "#ffbf00" if count else "#d62728"
                    for count in coverage["counts"]
                ]
                coverage_scatter.set_color(colors)

                elapsed = 0.0
                if state.capture_started is not None:
                    elapsed = max(0.0, now - state.capture_started)
                rate = len(data) / elapsed if elapsed > 0 else 0.0
                condition_text = (
                    f"{coverage['condition']:.2f}" if math.isfinite(coverage["condition"]) else "--"
                )
                status_text.set_text(
                    f"Run {run_id} | samples {len(data):5d} | time {elapsed:6.1f}s | rate {rate:5.1f} Hz\n"
                    f"coverage {coverage['covered_bins']:2d}/{coverage['total_bins']} bins | "
                    f"octants {coverage['octants']}/8 | condition {condition_text} | center: {center_source}"
                )
                if now - last_limit_update >= 1.0 and len(display):
                    set_equal_limits(raw_axis, display)
                    last_limit_update = now
                figure.canvas.draw_idle()
                last_draw = now
            plt.pause(0.02)
    except KeyboardInterrupt:
        state.action = "cancel"
    finally:
        state.stop_event.set()
        worker.join(timeout=3.0)
        plt.close(figure)

    data = state.snapshot()
    if state.action != "finish":
        if state.error:
            print(f"采集失败：{state.error}", file=sys.stderr)
        print(f"试验未校准；已采集的原始数据仍保留在：{raw_path}")
        return 1

    capture_start = state.capture_started or time.monotonic()
    capture_end = state.capture_finished or time.monotonic()
    duration = max(0.0, capture_end - capture_start)
    sample_rate = len(data) / duration if duration > 0 else 0.0
    finished_at = datetime.now(timezone.utc)
    metadata = {
        "run_id": run_id,
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "port": args.port,
        "baud": args.baud,
        "duration_s": duration,
        "sample_rate_hz": sample_rate,
    }

    print(f"\n正在拟合 {len(data)} 个样本，请稍候……")
    try:
        quality, html_path = finalize_run(data, raw_path, run_id, run_dir, metadata)
        error = ""
    except (OSError, ValueError, RuntimeError) as exc:
        quality = {
            "sample_count": len(data),
            "inlier_count": 0,
            "inlier_ratio": 0.0,
            "calibrated_radius_cv": math.nan,
            "relative_p95_abs_error": math.nan,
            "occupied_octants": 0,
            "direction_coverage_condition": math.inf,
            "matrix_condition_number": math.inf,
            "passed": False,
        }
        html_path = run_dir / "calibration_interactive.html"
        error = str(exc)
        (run_dir / "calibration_error.txt").write_text(error + "\n", encoding="utf-8")

    result = {
        **metadata,
        "sample_count": int(quality["sample_count"]),
        "inlier_count": int(quality["inlier_count"]),
        "inlier_ratio": quality["inlier_ratio"],
        "calibrated_radius_cv": quality["calibrated_radius_cv"],
        "relative_p95_abs_error": quality["relative_p95_abs_error"],
        "occupied_octants": quality["occupied_octants"],
        "direction_coverage_condition": quality["direction_coverage_condition"],
        "matrix_condition_number": quality["matrix_condition_number"],
        "passed": int(bool(quality["passed"])),
        "run_directory": str(run_dir),
        "error": error,
    }
    passed_count, completed_count, success_rate = append_result(results_path, result)

    print(f"试验结果：{'PASS' if quality['passed'] else 'FAIL'}")
    if error:
        print(f"拟合错误：{error}", file=sys.stderr)
    else:
        print(f"校准后半径CV：{quality['calibrated_radius_cv']:.3%}")
        print(f"P95相对误差：{quality['relative_p95_abs_error']:.3%}")
        print(f"方向覆盖：{quality['occupied_octants']}/8八分体，条件数 {quality['direction_coverage_condition']:.3f}")
        print(f"校准后HTML：{html_path}")
    print(f"累计成功率：{passed_count}/{completed_count} = {success_rate:.1%}")
    print(f"结果总表：{results_path}")
    print("本次参数没有写入正式data目录或Arduino草图。")

    if not error and not args.no_open_html:
        webbrowser.open(html_path.resolve().as_uri())
    return 0 if quality["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Wired OV9782 + ESP32-S3/11-IMU experiment with visual verification."""

from __future__ import annotations

import argparse
from collections import deque
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time

from .protocol import AutoStreamDecoder, GloveFrame


def monotonic_us() -> int:
    return time.perf_counter_ns() // 1_000


def resolve_serial_port(requested: str) -> str:
    if requested.lower() != "auto":
        return requested
    from serial.tools import list_ports
    candidates = [port for port in list_ports.comports() if port.vid == 0x303A and port.pid == 0x1001]
    if len(candidates) == 1:
        print(f"自动选择ESP32-S3串口：{candidates[0].device} ({candidates[0].serial_number})")
        return candidates[0].device
    if not candidates:
        raise RuntimeError("未发现VID:PID=303A:1001的ESP32-S3 USB串口")
    names = ", ".join(port.device for port in candidates)
    raise RuntimeError(f"发现多个ESP32-S3串口：{names}；请用 --serial COMx 明确指定")


@dataclass(frozen=True)
class ImuRecord:
    host_us: int
    glove_us: int
    seq: int
    hand_id: int
    recording: bool
    battery: int
    fail_mask: int


class ArrivalClockModel:
    """Online local-clock to host-clock mapping based on USB arrival observations.

    This is deliberately labelled as software alignment: USB arrival time contains
    transport jitter and is not a camera exposure timestamp.
    """

    def __init__(self, max_points: int = 500) -> None:
        self.points: deque[tuple[int, int]] = deque(maxlen=max_points)
        self.scale = 1.0
        self.offset_us = 0.0
        self.rms_us = math.inf

    def add(self, glove_us: int, host_us: int) -> None:
        self.points.append((glove_us, host_us))
        if len(self.points) < 5:
            offsets = [host - glove for glove, host in self.points]
            self.offset_us = float(statistics.median(offsets))
            return
        # Use the lowest-delay observations in time buckets to reduce USB queue bias.
        offsets = sorted(host - glove for glove, host in self.points)
        baseline = offsets[max(0, len(offsets) // 10 - 1)]
        selected = [(g, h) for g, h in self.points if h - g <= baseline + 2_000]
        if len(selected) < 5:
            selected = list(self.points)
        mean_g = sum(g for g, _ in selected) / len(selected)
        mean_h = sum(h for _, h in selected) / len(selected)
        denominator = sum((g - mean_g) ** 2 for g, _ in selected)
        if denominator > 0 and max(g for g, _ in selected) - min(g for g, _ in selected) >= 500_000:
            scale = sum((g - mean_g) * (h - mean_h) for g, h in selected) / denominator
            # Reject implausible fits during startup.
            if 0.995 <= scale <= 1.005:
                self.scale = scale
        self.offset_us = statistics.median(h - self.scale * g for g, h in selected)
        errors = [h - (self.scale * g + self.offset_us) for g, h in selected]
        self.rms_us = math.sqrt(sum(error * error for error in errors) / len(errors))

    def glove_to_host(self, glove_us: int) -> float:
        return self.scale * glove_us + self.offset_us

    @property
    def ready(self) -> bool:
        return len(self.points) >= 10


class SerialCollector:
    def __init__(self, port: str, baud: int, raw_path: Path, imu_log_path: Path) -> None:
        self.port = port
        self.baud = baud
        self.raw_path = raw_path
        self.imu_log_path = imu_log_path
        self.decoder = AutoStreamDecoder(max_buffer=180 * 64)
        self.records: deque[ImuRecord] = deque(maxlen=2_000)
        self.clock = ArrivalClockModel()
        self.total_bytes = 0
        self.frames = 0
        self.seq_gaps = 0
        self.last_seq: int | None = None
        self.last_error: str | None = None
        self.protocol = "waiting"
        self.started_us = monotonic_us()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="imu-serial", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            import serial
            with serial.Serial(self.port, self.baud, timeout=0.1) as connection, self.raw_path.open("wb") as raw, self.imu_log_path.open("w", encoding="utf-8") as log:
                while not self._stop.is_set():
                    chunk = connection.read(max(connection.in_waiting, 1))
                    if not chunk:
                        continue
                    received_us = monotonic_us()
                    raw.write(chunk)
                    with self._lock:
                        self.total_bytes += len(chunk)
                    for frame in self.decoder.feed(chunk):
                        is_v2 = isinstance(frame, GloveFrame)
                        # Legacy170 has no sequence or device timestamp. Use a
                        # monotonic receive counter/time until firmware V2 is installed.
                        seq = frame.frame_seq if is_v2 else self.frames & 0xFFFF
                        glove_us = frame.timestamp_us if is_v2 else received_us
                        record = ImuRecord(
                            received_us, glove_us, seq, frame.hand_id,
                            frame.recording if is_v2 else False, frame.battery_percent,
                            frame.imu_fail_mask if is_v2 else 0,
                        )
                        with self._lock:
                            if self.last_seq is not None:
                                delta = (seq - self.last_seq) & 0xFFFF
                                if 1 < delta < 0x8000:
                                    self.seq_gaps += delta - 1
                            self.last_seq = seq
                            self.frames += 1
                            self.protocol = "v2-180" if is_v2 else "legacy-170"
                            self.records.append(record)
                            self.clock.add(glove_us, received_us)
                        value = frame.to_dict()
                        value["host_receive_us"] = received_us
                        value["host_sequence"] = seq
                        log.write(json.dumps(value, ensure_ascii=False) + "\n")
        except Exception as exc:  # keep the camera UI alive and display the cause
            self.last_error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = max((monotonic_us() - self.started_us) / 1_000_000.0, 1e-6)
            return {
                "bytes": self.total_bytes,
                "frames": self.frames,
                "fps": self.frames / elapsed,
                "gaps": self.seq_gaps,
                "bad": self.decoder.rejected_candidates,
                "last": self.records[-1] if self.records else None,
                "records": list(self.records),
                "scale": self.clock.scale,
                "offset_us": self.clock.offset_us,
                "clock_rms_us": self.clock.rms_us,
                "clock_ready": self.clock.ready,
                "error": self.last_error,
                "protocol": self.protocol,
            }


class DemoCollector:
    """Synthetic 100 Hz IMU source for UI and workflow verification."""

    def __init__(self) -> None:
        self.started_us = monotonic_us()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def snapshot(self) -> dict:
        now = monotonic_us()
        count = max(1, (now - self.started_us) // 10_000)
        records = [
            ImuRecord(self.started_us + index * 10_000 + 800, index * 10_000, index & 0xFFFF, 0, True, 88, 0)
            for index in range(max(0, count - 300), count + 1)
        ]
        return {
            "bytes": count * 180, "frames": count, "fps": 100.0, "gaps": 0, "bad": 0,
            "last": records[-1], "records": records, "scale": 1.0,
            "offset_us": float(self.started_us + 800), "clock_rms_us": 120.0,
            "clock_ready": True, "error": None,
            "protocol": "demo",
        }


class StereoCamera:
    def __init__(self, layout: str, camera: int, left_camera: int, right_camera: int, width: int, height: int, fps: float) -> None:
        import cv2
        self.cv2 = cv2
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        self.layout = layout
        indexes = [camera] if layout in ("mono", "sbs") else [left_camera, right_camera]
        self.captures = [cv2.VideoCapture(index, backend) for index in indexes]
        for capture in self.captures:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            capture.set(cv2.CAP_PROP_FPS, fps)
        if not all(capture.isOpened() for capture in self.captures):
            self.close()
            raise RuntimeError(f"无法打开相机索引 {indexes}；先运行 --scan-cameras")

    def read(self):
        if self.layout in ("mono", "sbs"):
            ok, image = self.captures[0].read()
            if not ok or image is None:
                return False, None, None
            if self.layout == "mono":
                return True, image, None
            midpoint = image.shape[1] // 2
            return True, image[:, :midpoint], image[:, midpoint:]
        ok_left, left = self.captures[0].read()
        ok_right, right = self.captures[1].read()
        return ok_left and ok_right, left, right

    def close(self) -> None:
        for capture in self.captures:
            capture.release()


class DemoCamera:
    def __init__(self, width: int = 640, height: int = 400) -> None:
        self.width, self.height = width, height
        self.index = 0

    def read(self):
        import cv2
        import numpy as np
        self.index += 1
        left = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        right = np.zeros_like(left)
        x = int((math.sin(self.index * 0.05) * 0.4 + 0.5) * self.width)
        cv2.circle(left, (x, self.height // 2), 35, (40, 210, 255), -1)
        cv2.circle(right, (max(0, x - 24), self.height // 2), 35, (40, 210, 255), -1)
        cv2.putText(left, "DEMO LEFT", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(right, "DEMO RIGHT", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        time.sleep(1 / 30)
        return True, left, right

    def close(self) -> None:
        pass


def nearest_alignment(records: list[ImuRecord], scale: float, offset_us: float, camera_host_us: int) -> tuple[ImuRecord | None, float | None]:
    if not records:
        return None, None
    record = min(records, key=lambda item: abs((scale * item.glove_us + offset_us) - camera_host_us))
    delta_ms = ((scale * record.glove_us + offset_us) - camera_host_us) / 1000.0
    return record, delta_ms


def _put_status(image, y: int, label: str, value: str, level: str) -> None:
    import cv2
    colors = {"green": (60, 220, 60), "yellow": (0, 210, 255), "red": (50, 50, 240), "white": (230, 230, 230)}
    cv2.putText(image, f"{label}: {value}", (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, colors[level], 2, cv2.LINE_AA)


def scan_cameras(limit: int = 10) -> int:
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    found = []
    for index in range(limit):
        capture = cv2.VideoCapture(index, backend)
        if capture.isOpened():
            ok, frame = capture.read()
            if ok and frame is not None:
                found.append((index, frame.shape[1], frame.shape[0]))
        capture.release()
    if not found:
        print("未发现可读取相机。检查USB连接、驱动以及相机是否被其他软件占用。")
        return 1
    for index, width, height in found:
        print(f"camera={index}: {width}x{height}")
    return 0


def run(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np
    session = Path(args.output) / time.strftime("session_%Y%m%d_%H%M%S")
    session.mkdir(parents=True, exist_ok=True)
    serial_port = "demo" if args.demo else resolve_serial_port(args.serial)
    collector = DemoCollector() if args.demo else SerialCollector(serial_port, args.baud, session / "imu_raw.bin", session / "imu_frames.jsonl")
    camera = DemoCamera() if args.demo else StereoCamera(args.layout, args.camera, args.left_camera, args.right_camera, args.width, args.height, args.fps)
    camera_log_file = (session / "camera_frames.csv").open("w", newline="", encoding="utf-8")
    camera_log = csv.writer(camera_log_file)
    camera_log.writerow(["camera_frame", "host_capture_us", "imu_seq", "imu_glove_us", "aligned_delta_ms", "status"])
    collector.start()
    started = time.monotonic()
    camera_frames = 0
    last_frame_time = started
    camera_fps = 0.0
    print(f"实验开始，结果目录：{session.resolve()}")
    print("按 Q 或 ESC 结束。绿色表示数据流及软件对齐均正常。")
    try:
        while True:
            ok, left, right = camera.read()
            captured_us = monotonic_us()
            if not ok or left is None:
                print("相机读帧失败。")
                return 2
            camera_frames += 1
            now = time.monotonic()
            instant_fps = 1.0 / max(now - last_frame_time, 1e-6)
            camera_fps = instant_fps if camera_frames == 1 else camera_fps * 0.9 + instant_fps * 0.1
            last_frame_time = now
            if right is None:
                view = left.copy()
            else:
                if left.shape[0] != right.shape[0]:
                    right = cv2.resize(right, (right.shape[1], left.shape[0]))
                view = np.hstack((left, right))
            stats = collector.snapshot()
            record, delta_ms = nearest_alignment(stats["records"], stats["scale"], stats["offset_us"], captured_us)
            camera_ok = camera_fps >= max(1.0, args.fps * 0.65) or args.demo
            imu_ok = stats["fps"] >= args.min_imu_fps and stats["frames"] >= 10
            align_ok = delta_ms is not None and abs(delta_ms) <= args.max_align_ms and stats["clock_ready"]
            overall = "green" if camera_ok and imu_ok and align_ok and not stats["error"] else "yellow"
            if stats["error"] or (time.monotonic() - started > 3 and stats["bytes"] == 0):
                overall = "red"
            overlay = view.copy()
            cv2.rectangle(overlay, (0, 0), (view.shape[1], 156), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.65, view, 0.35, 0, view)
            mode = "DEMO" if args.demo else "SOFTWARE/USB ARRIVAL"
            _put_status(view, 25, "MODE", mode, "yellow" if not args.demo else "green")
            _put_status(view, 50, "CAMERA", f"frame={camera_frames}  {camera_fps:.1f} fps", "green" if camera_ok else "red")
            imu_value = f"{stats['protocol']}  frames={stats['frames']}  {stats['fps']:.1f} fps  bad={stats['bad']}  gaps={stats['gaps']}"
            _put_status(view, 75, "IMU", imu_value, "green" if imu_ok else "red")
            align_value = "warming up" if delta_ms is None else f"nearest={delta_ms:+.2f} ms  clockRMS={stats['clock_rms_us'] / 1000:.2f} ms"
            _put_status(view, 100, "ALIGN", align_value, "green" if align_ok else "yellow")
            last_value = "none" if record is None else f"seq={record.seq} hand={record.hand_id} fail=0x{record.fail_mask:03X}"
            _put_status(view, 125, "LATEST", last_value, "green" if record and record.fail_mask == 0 else "yellow")
            result_text = "PASS: WIRED STREAMS + ALIGNMENT" if overall == "green" else "WAIT / CHECK CONNECTION"
            _put_status(view, 150, "RESULT", result_text, overall)
            if stats["error"]:
                _put_status(view, 175, "ERROR", stats["error"][:100], "red")
            camera_log.writerow([
                camera_frames, captured_us, record.seq if record else "", record.glove_us if record else "",
                f"{delta_ms:.6f}" if delta_ms is not None else "", overall,
            ])
            cv2.imshow("Glovity Wired OV9782 + 11 IMU Verification", view)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
    finally:
        collector.stop()
        camera.close()
        camera_log_file.close()
        cv2.destroyAllWindows()
    print(f"实验结束：camera={camera_frames}，IMU={collector.snapshot()['frames']}，结果保存在 {session.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OV9782双目 + ESP32-S3/11 IMU有线实验")
    parser.add_argument("--demo", action="store_true", help="无硬件演示可视化界面")
    parser.add_argument("--scan-cameras", action="store_true", help="扫描可用相机索引")
    parser.add_argument("--serial", default="auto", help="ESP32-S3串口；默认按VID:PID=303A:1001自动发现")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--layout", choices=("mono", "sbs", "dual"), default="mono", help="mono=单相机；sbs=单路左右拼接；dual=两个相机索引")
    parser.add_argument("--camera", type=int, default=1, help="mono/sbs模式的相机索引；当前实验默认右相机1")
    parser.add_argument("--left-camera", type=int, default=0)
    parser.add_argument("--right-camera", type=int, default=1)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--min-imu-fps", type=float, default=25.0)
    parser.add_argument("--max-align-ms", type=float, default=20.0)
    parser.add_argument("--output", default="experiment_output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.scan_cameras:
        return scan_cameras()
    if not args.demo and not args.serial:
        raise SystemExit("真实实验必须指定 --serial")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

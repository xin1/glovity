#!/usr/bin/env python3
"""
读取 USB 双目同步模组（左右拼接帧）并实时显示左目 / 右目画面。

摄像头配置（来自 stereo_cam.yml）:
  设备节点  : Linux 下可用 /dev/video*；Windows 下使用设备索引（0/1/...）
  合并分辨率: 2560 × 720  （左目 1280×720 | 右目 1280×720）
  帧率      : 60 fps
"""

from __future__ import annotations

import platform
import sys
import threading
import time

import cv2
import numpy as np

_IS_WINDOWS = platform.system() == "Windows"

# 手动曝光模式下 CAP_PROP_AUTO_EXPOSURE / CAP_PROP_EXPOSURE 的默认值
# - V4L2 (Linux): auto_exposure=1 表示手动，=3 表示光圈优先（自动）；
#                 exposure 单位为 100μs，78 ≈ 7.8 ms，适合 30 fps IR 跟踪。
# - DSHOW (Windows): auto_exposure=0.25 手动 / 0.75 自动；exposure 常为 log2(秒)。
# - MSMF (Windows): 与 DSHOW 不同，OpenCV 映射一般为 auto_exposure=0 手动 / 1 自动。
#   若误用 0.25/0.75，部分设备不会进入手动曝光，CAP_PROP_EXPOSURE 被忽略，画面会
#   一直偏暗（与 Linux 对比像「怎么调曝光都不变」）。实际使用的标志见
#   _cap_auto_exposure_manual_auto()。
if _IS_WINDOWS:
    _AUTO_EXPOSURE_MANUAL = 0.25
    _AUTO_EXPOSURE_AUTO = 0.75
    _DEFAULT_FIXED_EXPOSURE = -5
    _DEFAULT_GAIN = 0
else:
    _AUTO_EXPOSURE_MANUAL = 1.0
    _AUTO_EXPOSURE_AUTO = 3.0
    # _DEFAULT_FIXED_EXPOSURE = 85.0
    _DEFAULT_FIXED_EXPOSURE = 25  # 180度镜头
    _DEFAULT_GAIN = 100  # V4L2 analogue_gain，范围因设备而异，0 为最低增益


class DualCameraReader:
    """后台线程持续读取相机最新帧，主线程按自身节奏非阻塞获取。"""

    def __init__(
        self,
        index: int | str = 2,
        combined_w: int = 2560,
        combined_h: int = 720,
        fps: int = 120,
        auto_exposure: bool = False,
        exposure: float | None = None,
        gain: float | None = None,
    ):
        self.index = index
        self.combined_w = combined_w
        self.combined_h = combined_h
        self.fps = fps
        self.auto_exposure = bool(auto_exposure)
        self.exposure = (
            float(exposure) if exposure is not None
            else (None if self.auto_exposure else _DEFAULT_FIXED_EXPOSURE)
        )
        self.gain = float(gain) if gain is not None else _DEFAULT_GAIN
        self.cap: cv2.VideoCapture | None = None

        self._lock = threading.Lock()
        self._frame_ready = threading.Condition(self._lock)
        self._latest_frame: np.ndarray | None = None
        self._latest_frame_ts: float | None = None
        self._frame_id: int = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._reader_error: str | None = None

        self._open_camera()
        self._start_reader()

    # ------------------------------------------------------------------ 相机
    @staticmethod
    def _normalize_source(index: int | str) -> int | str:
        """统一输入源类型：
        - Windows: 推荐整数设备索引（字符串数字也会自动转 int）
        - Linux: 允许整数索引或 /dev/video* 路径
        """
        if isinstance(index, str):
            stripped = index.strip()
            if stripped.isdigit():
                return int(stripped)
            return stripped
        return index

    def _candidate_backends(self) -> list[int]:
        """按平台返回优先尝试的 OpenCV 采集后端。"""
        if _IS_WINDOWS:
            return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        return [cv2.CAP_V4L2, cv2.CAP_GSTREAMER, cv2.CAP_ANY]

    def _apply_capture_params(self, cap: cv2.VideoCapture) -> None:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.combined_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.combined_h)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

    def _open_camera(self) -> None:
        source = self._normalize_source(self.index)
        tried: list[str] = []
        self.cap = None

        for backend in self._candidate_backends():
            backend_name = getattr(cv2, "videoio_registry", None)
            if backend_name is not None and hasattr(cv2.videoio_registry, "getBackendName"):
                try:
                    name = cv2.videoio_registry.getBackendName(backend)
                except Exception:
                    name = str(backend)
            else:
                name = str(backend)
            tried.append(name)
            try:
                cap = cv2.VideoCapture(source, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                self._apply_capture_params(cap)
                self.cap = cap
                break
            except Exception:
                # 某些 OpenCV 构建不支持部分后端，继续尝试下一项。
                continue

        if self.cap is None or not self.cap.isOpened():
            print(
                f"[ERROR] 无法打开摄像头 (source={source})，已尝试后端: {', '.join(tried)}",
                file=sys.stderr,
            )
            sys.exit(1)

        self._apply_exposure_settings()
        # UVC + DSHOW/MSMF：首几帧常仍沿用缓存的自动曝光，丢弃后再写一次手动曝光更稳。
        if _IS_WINDOWS:
            for _ in range(5):
                if self.cap is None:
                    break
                ok, _ = self.cap.read()
                if not ok:
                    break
            self._apply_exposure_settings()

        actual_fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join(chr((actual_fourcc >> (8 * i)) & 0xFF) for i in range(4))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_gain = self.cap.get(cv2.CAP_PROP_GAIN)
        exp_mode = (
            "AUTO"
            if self.auto_exposure
            else f"MANUAL(exp={self.exposure}, gain={self.gain})"
        )
        print(
            f"[INFO] 输入源: {source}, 编码: {fourcc_str}, 分辨率: {w}x{h}, "
            f"目标帧率: {self.fps} fps, 驱动回报: {actual_fps:.0f} fps, "
            f"曝光: {exp_mode}, 驱动回读 gain={actual_gain}"
        )

    def _apply_exposure_settings(self) -> None:
        """按初始化参数设置曝光模式与曝光值，保证每次启动画面亮度一致。"""
        if self.cap is None:
            return
        try:
            if self.auto_exposure:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, _AUTO_EXPOSURE_AUTO)
                return
            # 先切到手动模式，再写曝光/增益；部分驱动要求顺序如此。
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, _AUTO_EXPOSURE_MANUAL)
            if self.exposure is not None:
                self.cap.set(cv2.CAP_PROP_EXPOSURE, float(self.exposure))
            self.cap.set(cv2.CAP_PROP_GAIN, float(self.gain))
        except Exception as exc:  # pragma: no cover - 驱动差异容错
            print(f"[WARN] 设置曝光失败: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ 后台读取
    def _start_reader(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self) -> None:
        """持续 cap.read()，始终保留最新帧供主线程取用。

        节奏完全交给驱动：cap.read() 在 V4L2 + MJPG 下会阻塞到下一帧到达，
        这是硬件能给出的最稳定时钟。任何应用层的 time.sleep 都只会引入抖动。
        """
        debug_count = 0
        debug_t0 = time.monotonic()
        debug_read_ms_sum = 0.0
        read_error_count = 0

        while self._running:
            t0 = time.monotonic()
            try:
                ret, frame = self.cap.read()
            except cv2.error as exc:
                read_error_count += 1
                if read_error_count == 1:
                    print(f"[WARN] cap.read() 抛出异常，将重试: {exc}", file=sys.stderr)
                if read_error_count >= 5:
                    # 某些 UVC 设备会在状态切换后短暂返回坏 MJPG 包，尝试原地重开。
                    print("[WARN] 连续读取异常，尝试重开相机流...", file=sys.stderr)
                    self._reopen_camera_stream()
                    read_error_count = 0
                time.sleep(0.01)
                continue
            t1 = time.monotonic()
            if not ret:
                read_error_count += 1
                if read_error_count >= 60:
                    print("[WARN] 长时间读不到帧，尝试重开相机流...", file=sys.stderr)
                    self._reopen_camera_stream()
                    read_error_count = 0
                time.sleep(0.001)
                continue
            read_error_count = 0

            debug_count += 1
            debug_read_ms_sum += (t1 - t0) * 1000.0
            elapsed = t1 - debug_t0
            if elapsed >= 1.0:
                raw_fps = debug_count / elapsed
                avg_read_ms = debug_read_ms_sum / debug_count
                # print(
                #     f"[reader] raw cap.read() FPS = {raw_fps:5.2f}  "
                #     f"avg cap.read() = {avg_read_ms:5.2f} ms",
                #     flush=True,
                # )
                debug_count = 0
                debug_read_ms_sum = 0.0
                debug_t0 = t1

            with self._frame_ready:
                self._latest_frame = frame
                self._latest_frame_ts = t1
                self._frame_id += 1
                self._frame_ready.notify_all()

    def _reopen_camera_stream(self) -> None:
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        self.cap = None
        try:
            self._open_camera()
            self._reader_error = None
        except Exception as exc:
            self._reader_error = f"重开相机失败: {exc}"
            print(f"[ERROR] {self._reader_error}", file=sys.stderr)

    def _wait_for_any_frame(self, timeout: float) -> tuple[np.ndarray, int, float]:
        deadline = time.monotonic() + timeout
        with self._frame_ready:
            while self._latest_frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("超时未收到相机帧")
                self._frame_ready.wait(timeout=remaining)
            frame = self._latest_frame
            frame_id = self._frame_id
            frame_ts = self._latest_frame_ts if self._latest_frame_ts is not None else time.monotonic()
        return frame, frame_id, frame_ts

    @staticmethod
    def _split_stereo(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mid = frame.shape[1] // 2
        return frame[:, :mid], frame[:, mid:]

    # ------------------------------------------------------------------ 公共接口
    def get_image(self, timeout: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
        """返回最新的左右目图像。首次调用时自动等待后台线程拿到第一帧。"""
        try:
            frame, _, _ = self._wait_for_any_frame(timeout=timeout)
        except TimeoutError:
            print("[ERROR] 超时未收到相机帧", file=sys.stderr)
            sys.exit(1)
        return self._split_stereo(frame)

    def wait_next_frame(
        self,
        last_frame_id: int = -1,
        timeout: float = 3.0,
    ) -> tuple[np.ndarray, np.ndarray, int, float]:
        """阻塞直到有更新帧，返回 (left, right, frame_id, frame_ts)。"""
        deadline = time.monotonic() + timeout
        with self._frame_ready:
            while self._latest_frame is None or self._frame_id <= last_frame_id:
                if self._reader_error is not None:
                    raise RuntimeError(self._reader_error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("超时未收到新相机帧")
                self._frame_ready.wait(timeout=remaining)

            frame = self._latest_frame
            frame_id = self._frame_id
            frame_ts = self._latest_frame_ts if self._latest_frame_ts is not None else time.monotonic()

        left, right = self._split_stereo(frame)
        return left, right, frame_id, frame_ts

    def get_frame_id(self) -> int:
        """返回当前帧编号，用于判断是否收到了新帧。"""
        with self._lock:
            return self._frame_id

    def close(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.cap = None


def main() -> None:
    reader = DualCameraReader()
    print("[INFO] 按 'q' 或 ESC 退出；按 's' 保存当前帧对。")

    save_idx = 0
    last_frame_id = -1

    window_start_ts: float | None = None
    window_first_ts: float | None = None
    window_prev_ts: float | None = None
    window_count = 0
    window_dt_sum = 0.0
    window_dt_sq_sum = 0.0

    while True:
        try:
            left_frame, right_frame, last_frame_id, frame_ts = reader.wait_next_frame(
                last_frame_id=last_frame_id,
                timeout=3.0,
            )
        except TimeoutError:
            print("\n[ERROR] 超时未收到新相机帧", file=sys.stderr)
            break

        if window_start_ts is None:
            window_start_ts = frame_ts
            window_first_ts = frame_ts
            window_prev_ts = frame_ts
            window_count = 1
        else:
            dt = frame_ts - window_prev_ts
            window_prev_ts = frame_ts
            window_count += 1
            window_dt_sum += dt
            window_dt_sq_sum += dt * dt

        if window_first_ts is not None and (frame_ts - window_first_ts) >= 0.5 and window_count >= 2:
            n_intervals = window_count - 1
            mean_dt = window_dt_sum / n_intervals
            var_dt = max(window_dt_sq_sum / n_intervals - mean_dt * mean_dt, 0.0)
            std_dt = var_dt ** 0.5
            fps_disp = 1.0 / mean_dt if mean_dt > 0 else 0.0
            print(
                f"\rFPS: {fps_disp:6.2f} | dt: {mean_dt * 1000:6.2f} ms "
                f"(±{std_dt * 1000:5.2f} ms)   ",
                end="",
                flush=True,
            )
            window_first_ts = frame_ts
            window_count = 1
            window_dt_sum = 0.0
            window_dt_sq_sum = 0.0

        cv2.imshow("Stereo - Left",  left_frame)
        cv2.imshow("Stereo - Right", right_frame)

        key = cv2.waitKey(1) & 0xFF

        if key in (ord('q'), 27):
            break
        elif key == ord('s'):
            cv2.imwrite(f"left_{save_idx:04d}.png",  left_frame)
            cv2.imwrite(f"right_{save_idx:04d}.png", right_frame)
            print(f"\n[INFO] 已保存: left_{save_idx:04d}.png / right_{save_idx:04d}.png")
            save_idx += 1

    reader.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

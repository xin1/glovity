"""Shared Receiver_RX hub: one serial port for glove FK + tracker wrist IMU."""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import Dict, Optional, Sequence

from telex.glove.types import GloveFrame
from telex.hub.parse import (
    DEFAULT_BAUD,
    DEFAULT_PORT,
    HAND_IDS,
    HAND_LEFT,
    HAND_NAMES,
    HAND_RIGHT,
    next_receiver_frame_candidate,
    normalize_hand,
    parse_glove_frame,
    parse_wrist_frame,
)
from telex.tracker.imu.receiver import WirelessImuFrame, _copy_frame, _load_serial_module, _open_serial
from telex.tracker.imu.wireless import WirelessHandImuSubscriber, _HubStopToken


class SharedImuHub:
    """唯一串口所有者：缓存左右手全量 GloveFrame 与腕部 WirelessImuFrame。"""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        timeout: float = 0.2,
        offline_timeout: float = 0.35,
    ) -> None:
        self._port = port
        self._baud = int(baud)
        self._timeout = float(timeout)
        self._offline_timeout = float(offline_timeout)

        self._lock = threading.Lock()
        self._latest_wrist: Dict[int, Optional[WirelessImuFrame]] = {
            HAND_LEFT: None,
            HAND_RIGHT: None,
        }
        self._latest_glove: Dict[int, Optional[GloveFrame]] = {
            HAND_LEFT: None,
            HAND_RIGHT: None,
        }
        self._rx_total = {HAND_LEFT: 0, HAND_RIGHT: 0}
        self._last_rx_ts = {HAND_LEFT: 0.0, HAND_RIGHT: 0.0}
        self._timestamps = {
            HAND_LEFT: deque(maxlen=80),
            HAND_RIGHT: deque(maxlen=80),
        }
        self._rx_rate_hz = {HAND_LEFT: 0.0, HAND_RIGHT: 0.0}

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.error: Optional[str] = None

    def start(self) -> None:
        if self._running:
            return
        self.error = None
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="telex_shared_imu_hub",
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def wait_ready(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while time.monotonic() < deadline:
            if self.error:
                return False
            if self.connected:
                return True
            if not self._running and not self.connected:
                return False
            time.sleep(0.02)
        return bool(self.connected)

    def latest_hand(self, hand: str | int) -> Optional[WirelessImuFrame]:
        """腕部帧（兼容 tracker ``WirelessHandImuSubscriber``）。"""
        hand_id, _ = normalize_hand(hand)
        with self._lock:
            return _copy_frame(self._latest_wrist[hand_id])

    def latest_glove(self, hand: str | int) -> Optional[GloveFrame]:
        hand_id, _ = normalize_hand(hand)
        with self._lock:
            frame = self._latest_glove[hand_id]
            if frame is None:
                return None
            # 浅拷贝列表，避免外部修改
            return GloveFrame(
                timestamp=frame.timestamp,
                imu_data=list(frame.imu_data),
                frame_id=frame.frame_id,
                hand=frame.hand,
                timestamp_us=frame.timestamp_us,
                host_receive_s=frame.host_receive_s,
                recording=frame.recording,
                battery_percent=frame.battery_percent,
                imu_fail_mask=frame.imu_fail_mask,
                wrist_gyro_valid=frame.wrist_gyro_valid,
                wrist_gyro_dps=frame.wrist_gyro_dps,
                protocol=frame.protocol,
                protocol_version=frame.protocol_version,
                has_device_timestamp=frame.has_device_timestamp,
                has_recording_flag=frame.has_recording_flag,
                has_fail_mask=frame.has_fail_mask,
            )

    def is_online(self, hand: str | int) -> bool:
        hand_id, _ = normalize_hand(hand)
        with self._lock:
            last = self._last_rx_ts[hand_id]
            return last > 0.0 and time.monotonic() - last <= self._offline_timeout

    def get_frame_count(self, hand: Optional[str] = None) -> int:
        with self._lock:
            if hand is None:
                return self._rx_total[HAND_LEFT] + self._rx_total[HAND_RIGHT]
            hand_id, _ = normalize_hand(hand)
            return self._rx_total[hand_id]

    def _update(self, raw: bytes, now_s: float) -> None:
        wrist = parse_wrist_frame(raw, timestamp_s=now_s)
        glove = parse_glove_frame(raw, timestamp_s=now_s)
        if wrist is None or glove is None:
            return
        hand_id = wrist.hand_id
        with self._lock:
            self._latest_wrist[hand_id] = wrist
            self._latest_glove[hand_id] = glove
            self._rx_total[hand_id] += 1
            self._last_rx_ts[hand_id] = now_s
            timestamps = self._timestamps[hand_id]
            timestamps.append(now_s)
            if len(timestamps) >= 2:
                span = timestamps[-1] - timestamps[0]
                if span > 0.0:
                    self._rx_rate_hz[hand_id] = (len(timestamps) - 1) / span

    def _run(self) -> None:
        serial_module = _load_serial_module("TeleX Shared IMU Hub")
        if serial_module is None:
            self.error = "pyserial missing"
            self._running = False
            return

        try:
            with _open_serial(serial_module, self._port, self._baud, self._timeout) as ser:
                self.connected = True
                print(
                    f"[TeleX Hub] 已连接: {self._port} @ {self._baud} (left/right)",
                    flush=True,
                )
                buf = bytearray()
                while self._running:
                    chunk = ser.read(1024)
                    if chunk:
                        buf.extend(chunk)
                    else:
                        time.sleep(0.001)
                    while True:
                        candidate_info = next_receiver_frame_candidate(buf)
                        if candidate_info is None:
                            break
                        candidate, frame_size = candidate_info
                        now_s = time.monotonic()
                        wrist = parse_wrist_frame(candidate, timestamp_s=now_s)
                        if wrist is None:
                            del buf[:1]
                            continue
                        del buf[:frame_size]
                        self._update(candidate, now_s)
        except Exception as exc:  # SerialException or open failure
            self.error = str(exc)
            print(f"[TeleX Hub] 串口错误: {exc} -> Hub 已禁用", file=sys.stderr, flush=True)
        finally:
            self.connected = False
            self._running = False


def start_hand_subscribers_from_hub(
    hub: SharedImuHub,
    hands: Sequence[str] = ("left", "right"),
) -> Dict[str, WirelessHandImuSubscriber]:
    """把已启动的 SharedImuHub 适配成 tracker 融合用的 per-hand subscribers。"""
    token = _HubStopToken(hub)
    return {hand: WirelessHandImuSubscriber(hub, hand, token) for hand in hands}


def start_shared_hub(
    *,
    port: Optional[str] = None,
    baud: Optional[int] = None,
    startup_timeout_s: float = 2.0,
) -> Optional[SharedImuHub]:
    """启动共享 Hub；失败返回 None。"""
    hub = SharedImuHub(
        port=port or DEFAULT_PORT,
        baud=int(baud if baud is not None else DEFAULT_BAUD),
    )
    try:
        hub.start()
    except Exception as exc:
        print(f"[WARN][Hub] 启动失败: {exc}", file=sys.stderr, flush=True)
        return None
    if not hub.wait_ready(timeout_s=startup_timeout_s):
        detail = hub.error or "timeout"
        print(f"[WARN][Hub] 未就绪: {detail}", file=sys.stderr, flush=True)
        hub.stop()
        return None
    return hub


__all__ = [
    "DEFAULT_BAUD",
    "DEFAULT_PORT",
    "HAND_IDS",
    "HAND_LEFT",
    "HAND_NAMES",
    "HAND_RIGHT",
    "SharedImuHub",
    "start_hand_subscribers_from_hub",
    "start_shared_hub",
]

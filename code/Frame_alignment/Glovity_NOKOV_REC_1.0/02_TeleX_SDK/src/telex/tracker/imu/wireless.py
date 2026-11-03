"""把 wireless receiver 适配成 per-hand IMU subscriber 接口。"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from telex.tracker.imu.messages import ImuFrame


def wireless_frame_to_imu_frame(hand: str, frame, *, seq: int = 0) -> ImuFrame:
    """``WirelessImuFrame`` → 融合管线使用的 ``ImuFrame``（quat 按 wxyz）。"""
    device = str(hand).strip().lower()
    return ImuFrame(
        device=device,
        seq=int(seq),
        frame_id=int(frame.frame_id),
        timestamp_s=float(frame.timestamp),
        received_monotonic_s=float(frame.timestamp),
        euler_deg=np.asarray(frame.angle, dtype=np.float64).reshape(3).copy(),
        gyro_dps=np.asarray(frame.gyro, dtype=np.float64).reshape(3).copy(),
        quat_wxyz=np.asarray(frame.quat, dtype=np.float64).reshape(4).copy(),
    )


class _HubStopToken:
    """多个 hand adapter 共享同一 hub；最后一个 stop 关闭串口。"""

    def __init__(self, hub) -> None:
        self._hub = hub
        self._lock = threading.Lock()
        self._refs = 0
        self._stopped = False

    def acquire(self) -> None:
        with self._lock:
            self._refs += 1

    def release(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._refs = max(0, self._refs - 1)
            if self._refs == 0:
                self._stopped = True
                try:
                    self._hub.stop()
                except Exception:
                    pass


class WirelessHandImuSubscriber:
    """单手视图：latest()/start()/stop()/wait_ready() 对齐 ZMQ subscriber。"""

    def __init__(self, hub, hand: str, stop_token: _HubStopToken) -> None:
        self._hub = hub
        self.hand = str(hand).strip().lower()
        if self.hand not in {"left", "right"}:
            raise ValueError(f"unsupported hand: {hand!r}")
        self._token = stop_token
        self.error: Optional[str] = None
        self._started = False
        self._seq = 0

    def start(self) -> None:
        if self._started:
            return
        self._token.acquire()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._token.release()

    @property
    def is_ready(self) -> bool:
        return bool(getattr(self._hub, "connected", False)) or self._hub.is_online(
            self.hand
        )

    def wait_ready(self, timeout_s: float = 1.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while time.monotonic() < deadline:
            if self.error:
                return False
            if getattr(self._hub, "connected", False):
                return True
            # 串口线程若因缺 pyserial 立刻退出，connected 永假
            if not getattr(self._hub, "_running", True) and not getattr(
                self._hub, "connected", False
            ):
                self.error = "wireless IMU hub failed to open serial port"
                return False
            time.sleep(0.02)
        if getattr(self._hub, "connected", False):
            return True
        self.error = self.error or (
            f"wireless IMU hub not connected within {float(timeout_s):.3f}s"
        )
        return False

    def latest(self) -> Optional[ImuFrame]:
        if not self._hub.is_online(self.hand):
            return None
        frame = self._hub.latest_hand(self.hand)
        if frame is None:
            return None
        if not frame.wrist_imu_valid or not frame.wrist_gyro_valid:
            return None
        quat = np.asarray(frame.quat, dtype=np.float64).reshape(4)
        if float(np.linalg.norm(quat)) < 1e-12:
            return None
        self._seq += 1
        return wireless_frame_to_imu_frame(self.hand, frame, seq=self._seq)


def start_wireless_hand_subscribers(
    *,
    port: str,
    baud: int,
    offline_timeout_s: float,
    hands=("left", "right"),
):
    """打开一次串口 hub，返回 {hand: WirelessHandImuSubscriber}。"""
    from telex.tracker.imu.receiver import WirelessImuHubReader

    hub = WirelessImuHubReader(
        port=port,
        baud=baud,
        offline_timeout=float(offline_timeout_s),
    )
    token = _HubStopToken(hub)
    hub.start()
    return {
        hand: WirelessHandImuSubscriber(hub, hand, token) for hand in hands
    }

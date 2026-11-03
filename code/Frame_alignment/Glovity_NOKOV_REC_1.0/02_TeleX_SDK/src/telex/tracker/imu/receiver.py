#!/usr/bin/env python3
"""
Wireless IMU receiver for ``Receiver_RX.ino``.

The firmware forwards validated left/right glove frames to the PC unchanged.
There is no Base device and no 12th C-ring IMU in this protocol.

Serial frame format (180 bytes, V2, little-endian)::

    [0:2]      magic        0xAA 0x55
    [2]        version      2
    [3]        hand_id      0=LEFT, 1=RIGHT
    [4]        flags        bit0 recording, bit1 wrist gyro valid
    [5]        bat_percent
    [6:8]      frame_seq    uint16
    [8:16]     timestamp_us uint64
    [16:18]    fail_mask    uint16, bits 0..10
    [18:172]   imu[11]      11 x (euler[3] + quat[4]) int16
    [172:178]  wrist_gyro   int16[3]
    [178:180]  crc16        CRC-16/CCITT-FALSE of bytes [0:178)

The wrist orientation exposed by :class:`WirelessImuFrame` comes from IMU slot
0 (the board-mounted wrist IMU). The separate ``wrist_gyro`` field is used for
angular velocity.

Self-test examples::

    python -m utils.imu.wireless_receiver --port /dev/ttyACM0 --hand left
    python -m utils.imu.wireless_receiver --hand right
    python -m utils.imu.wireless_receiver --hand dual
    python -m utils.imu.wireless_receiver --hand all
"""

from __future__ import annotations

import argparse
import platform
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

serial = None


MAGIC = b"\xAA\x55"
PROTOCOL_VERSION = 2
FLAG_RECORDING = 0x01
FLAG_WRIST_GYRO_VALID = 0x02
HAND_LEFT = 0
HAND_RIGHT = 1
HAND_NAMES = {HAND_LEFT: "left", HAND_RIGHT: "right"}
HAND_IDS = {"left": HAND_LEFT, "right": HAND_RIGHT}
DEVICE_NAMES = HAND_NAMES.copy()

BOARD_WRIST_IMU_INDEX = 0
WRIST_IMU_INDEX = BOARD_WRIST_IMU_INDEX
# Compatibility alias for code that imported the old v2 symbol. Receiver_RX.ino
# has no separate C-ring slot; wrist data now comes from slot 0.
RING_WRIST_IMU_INDEX = BOARD_WRIST_IMU_INDEX

IMU_COUNT = 11
IMU_RAW_SIZE = 14  # 7 * int16: euler[3], quaternion[4]
HEADER_SIZE = 18
EXTRA_OFFSET = HEADER_SIZE + IMU_COUNT * IMU_RAW_SIZE
CRC_OFFSET = EXTRA_OFFSET + 6
FRAME_SIZE = CRC_OFFSET + 2
LEGACY_FRAME_SIZE = 168
LEGACY_CAPTURE_FRAME_SIZE = 170
MAX_FRAME_SIZE = FRAME_SIZE

GYRO_SCALE = 2000.0 / 32768.0
ANG_SCALE = 180.0 / 32768.0
QUAT_SCALE = 1.0 / 32768.0

if platform.system() == "Linux":
    DEFAULT_PORT = "/dev/ttyACM0"
elif platform.system() == "Darwin":
    DEFAULT_PORT = "/dev/cu.usbmodem11101"
else:
    DEFAULT_PORT = "/dev/ttyACM0"

DEFAULT_BAUD = 2_000_000
DEFAULT_TIMEOUT = 0.2
DEFAULT_OFFLINE_TIMEOUT_SEC = 0.35

assert EXTRA_OFFSET == 172
assert FRAME_SIZE == 180


@dataclass
class WirelessImuFrame:
    """Latest wrist IMU data for one glove."""

    timestamp: float = 0.0
    hand_id: int = HAND_LEFT
    hand_name: str = "left"
    frame_id: int = 0
    bat_percent: int = 0
    imu_count: int = 0
    angle: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    gyro: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    quat: np.ndarray = field(default_factory=lambda: np.zeros(4, dtype=np.float64))
    timestamp_us: int = 0
    recording: bool = False
    imu_fail_mask: int = 0
    wrist_gyro_valid: bool = False
    wrist_imu_valid: bool = False
    protocol: str = "v2-180"
    protocol_version: int = 2
    has_device_timestamp: bool = True
    has_recording_flag: bool = True
    has_fail_mask: bool = True


@dataclass
class WirelessImuStats:
    """Connection and receive statistics for one glove."""

    hand_id: int
    hand_name: str
    connected: bool
    online: bool
    frame_id: int
    bat_percent: int
    imu_count: int
    rx_total: int
    rx_rate_hz: float
    last_rx_ts: float


@dataclass
class WirelessImuHubStats:
    """Connection and receive statistics for both gloves."""

    connected: bool
    left: WirelessImuStats
    right: WirelessImuStats


def normalize_hand(hand: str | int) -> Tuple[int, str]:
    if isinstance(hand, str):
        key = hand.strip().lower()
        if key not in HAND_IDS:
            raise ValueError(f"Unsupported hand: {hand!r}. Expected 'left' or 'right'.")
        return HAND_IDS[key], key

    hand_id = int(hand)
    if hand_id not in HAND_NAMES:
        raise ValueError(f"Unsupported hand id: {hand_id}. Expected 0 or 1.")
    return hand_id, HAND_NAMES[hand_id]


def crc16_ccitt_false(data: bytes | bytearray | memoryview) -> int:
    """CRC-16/CCITT-FALSE used by the 180-byte V2 firmware."""
    value = 0xFFFF
    for byte in data:
        value ^= int(byte) << 8
        for _ in range(8):
            value = ((value << 1) ^ 0x1021) & 0xFFFF if value & 0x8000 else (value << 1) & 0xFFFF
    return value


def xor_check(data: bytes | bytearray | memoryview) -> int:
    """Legacy frame XOR checksum."""
    value = 0
    for byte in data:
        value ^= int(byte)
    return value


def _legacy_size_if_valid(frame: bytes | bytearray | memoryview) -> Optional[int]:
    # Prefer the longer captured variant when both checks happen to match.
    if len(frame) >= LEGACY_CAPTURE_FRAME_SIZE:
        head = frame[:LEGACY_CAPTURE_FRAME_SIZE]
        if (head[:2] == MAGIC and head[2] in HAND_NAMES and head[3] == IMU_COUNT
                and head[4] <= 100 and xor_check(head[:-1]) == head[-1]):
            return LEGACY_CAPTURE_FRAME_SIZE
    if len(frame) >= LEGACY_FRAME_SIZE:
        head = frame[:LEGACY_FRAME_SIZE]
        if (head[:2] == MAGIC and head[2] in HAND_NAMES and head[3] == IMU_COUNT
                and head[4] <= 100 and xor_check(head[:-1]) == head[-1]):
            return LEGACY_FRAME_SIZE
    return None


def receiver_frame_size_for_device_id(device_id: int) -> Optional[int]:
    """Compatibility helper: return the V2 size for left/right hand IDs."""
    return FRAME_SIZE if device_id in HAND_NAMES else None


def next_receiver_frame_candidate(buf: bytearray) -> Optional[Tuple[bytes, int]]:
    """Synchronize a byte stream and return the next complete hand frame.

    Leading text/noise and magic sequences followed by unsupported device IDs
    are discarded. A returned candidate remains in ``buf`` so the caller can
    validate it and decide whether to consume the whole frame or one byte.
    """
    while True:
        idx = buf.find(MAGIC)
        if idx < 0:
            # Keep enough trailing bytes for a future partial frame and retain a
            # lone 0xAA which might be the first byte of MAGIC.
            keep = max(FRAME_SIZE, 1)
            if len(buf) > keep:
                del buf[:-keep]
            return None

        if idx > 0:
            del buf[:idx]

        if len(buf) < 4:
            return None

        if buf[2] == PROTOCOL_VERSION and buf[3] in HAND_NAMES:
            if len(buf) < FRAME_SIZE:
                return None
            return bytes(buf[:FRAME_SIZE]), FRAME_SIZE

        if buf[2] in HAND_NAMES and buf[3] == IMU_COUNT:
            if len(buf) < LEGACY_FRAME_SIZE:
                return None
            legacy_size = _legacy_size_if_valid(buf)
            if legacy_size is not None:
                return bytes(buf[:legacy_size]), legacy_size
            if len(buf) < LEGACY_CAPTURE_FRAME_SIZE:
                return None
            return bytes(buf[:LEGACY_CAPTURE_FRAME_SIZE]), LEGACY_CAPTURE_FRAME_SIZE

        else:
            del buf[:1]
            continue


def parse_receiver_frame(
    frame: bytes,
    timestamp: Optional[float] = None,
) -> Optional[WirelessImuFrame]:
    """Parse one validated 180-byte V2 frame into wrist IMU data."""
    legacy_size = _legacy_size_if_valid(frame)
    if legacy_size == len(frame):
        hand_id = frame[2]
        frame_id = struct.unpack_from("<H", frame, 5)[0]
        wrist_offset = 7 + WRIST_IMU_INDEX * IMU_RAW_SIZE
        wrist_raw = struct.unpack_from("<7h", frame, wrist_offset)
        gyro_raw = struct.unpack_from("<3h", frame, 161)
        return WirelessImuFrame(
            timestamp=time.monotonic() if timestamp is None else float(timestamp),
            hand_id=hand_id, hand_name=HAND_NAMES[hand_id], frame_id=frame_id,
            bat_percent=frame[4], imu_count=IMU_COUNT,
            angle=np.asarray(wrist_raw[0:3], dtype=np.float64) * ANG_SCALE,
            gyro=np.asarray(gyro_raw, dtype=np.float64) * GYRO_SCALE,
            quat=np.asarray(wrist_raw[3:7], dtype=np.float64) * QUAT_SCALE,
            wrist_gyro_valid=True, wrist_imu_valid=True,
            protocol=f"legacy-{legacy_size}", protocol_version=1,
            has_device_timestamp=False, has_recording_flag=False,
            has_fail_mask=False,
        )
    if len(frame) != FRAME_SIZE:
        return None
    if frame[:2] != MAGIC:
        return None
    if frame[2] != PROTOCOL_VERSION or frame[3] not in HAND_NAMES:
        return None
    flags = frame[4]
    if flags & ~0x03 or frame[5] > 100:
        return None
    fail_mask = struct.unpack_from("<H", frame, 16)[0]
    if fail_mask & ~0x07FF:
        return None
    if crc16_ccitt_false(frame[:CRC_OFFSET]) != struct.unpack_from("<H", frame, CRC_OFFSET)[0]:
        return None

    hand_id = frame[3]
    bat_percent = frame[5]
    frame_id = struct.unpack_from("<H", frame, 6)[0]
    timestamp_us = struct.unpack_from("<Q", frame, 8)[0]

    wrist_offset = HEADER_SIZE + WRIST_IMU_INDEX * IMU_RAW_SIZE
    wrist_raw = struct.unpack_from("<7h", frame, wrist_offset)
    angle = np.asarray(wrist_raw[0:3], dtype=np.float64) * ANG_SCALE
    quat = np.asarray(wrist_raw[3:7], dtype=np.float64) * QUAT_SCALE

    wrist_gyro_raw = struct.unpack_from("<3h", frame, EXTRA_OFFSET)
    gyro = np.asarray(wrist_gyro_raw, dtype=np.float64) * GYRO_SCALE

    return WirelessImuFrame(
        timestamp=time.monotonic() if timestamp is None else float(timestamp),
        hand_id=hand_id,
        hand_name=HAND_NAMES[hand_id],
        frame_id=frame_id,
        bat_percent=bat_percent,
        imu_count=IMU_COUNT,
        angle=angle,
        gyro=gyro,
        quat=quat,
        timestamp_us=timestamp_us,
        recording=bool(flags & FLAG_RECORDING),
        imu_fail_mask=fail_mask,
        wrist_gyro_valid=bool(flags & FLAG_WRIST_GYRO_VALID),
        wrist_imu_valid=not bool(fail_mask & (1 << WRIST_IMU_INDEX)),
    )


def parse_frame_snapshot(frame: bytes, timestamp: Optional[float] = None) -> Optional[dict]:
    """Parse all 11 IMUs for the terminal monitor."""
    legacy_size = _legacy_size_if_valid(frame)
    if legacy_size == len(frame):
        hand_id = frame[2]
        values = struct.unpack_from("<80h", frame, 7)
        return {
            "hand_id": hand_id, "hand_name": HAND_NAMES[hand_id].upper(),
            "frame_id": struct.unpack_from("<H", frame, 5)[0],
            "timestamp_us": None, "recording": None, "imu_fail_mask": None,
            "wrist_gyro_valid": True, "bat": int(frame[4]),
            "imu_count": IMU_COUNT,
            "euler": [tuple(v * ANG_SCALE for v in values[i * 7:i * 7 + 3]) for i in range(IMU_COUNT)],
            "quat": [tuple(v * QUAT_SCALE for v in values[i * 7 + 3:i * 7 + 7]) for i in range(IMU_COUNT)],
            "wrist_gyro": tuple(v * GYRO_SCALE for v in values[77:80]),
            "last_rx_ts": time.monotonic() if timestamp is None else float(timestamp),
            "protocol": f"legacy-{legacy_size}", "protocol_version": 1,
        }
    if len(frame) != FRAME_SIZE:
        return None
    if frame[:2] != MAGIC:
        return None
    if frame[2] != PROTOCOL_VERSION or frame[3] not in HAND_NAMES:
        return None
    flags = frame[4]
    fail_mask = struct.unpack_from("<H", frame, 16)[0]
    if flags & ~0x03 or frame[5] > 100 or fail_mask & ~0x07FF:
        return None
    if crc16_ccitt_false(frame[:CRC_OFFSET]) != struct.unpack_from("<H", frame, CRC_OFFSET)[0]:
        return None

    hand_id = frame[3]
    euler: list[tuple[float, float, float]] = []
    quat: list[tuple[float, float, float, float]] = []

    for index in range(IMU_COUNT):
        offset = HEADER_SIZE + index * IMU_RAW_SIZE
        raw = struct.unpack_from("<7h", frame, offset)
        euler.append(tuple(value * ANG_SCALE for value in raw[0:3]))
        quat.append(tuple(value * QUAT_SCALE for value in raw[3:7]))

    wrist_gyro_raw = struct.unpack_from("<3h", frame, EXTRA_OFFSET)
    wrist_gyro = tuple(value * GYRO_SCALE for value in wrist_gyro_raw)

    return {
        "hand_id": hand_id,
        "hand_name": "LEFT" if hand_id == HAND_LEFT else "RIGHT",
        "frame_id": struct.unpack_from("<H", frame, 6)[0],
        "timestamp_us": struct.unpack_from("<Q", frame, 8)[0],
        "recording": bool(flags & FLAG_RECORDING),
        "imu_fail_mask": fail_mask,
        "wrist_gyro_valid": bool(flags & FLAG_WRIST_GYRO_VALID),
        "bat": int(frame[5]),
        "imu_count": IMU_COUNT,
        "euler": euler,
        "quat": quat,
        "wrist_gyro": wrist_gyro,
        "last_rx_ts": time.monotonic() if timestamp is None else float(timestamp),
    }


def _copy_frame(frame: Optional[WirelessImuFrame]) -> Optional[WirelessImuFrame]:
    if frame is None:
        return None
    return WirelessImuFrame(
        timestamp=frame.timestamp,
        hand_id=frame.hand_id,
        hand_name=frame.hand_name,
        frame_id=frame.frame_id,
        bat_percent=frame.bat_percent,
        imu_count=frame.imu_count,
        angle=frame.angle.copy(),
        gyro=frame.gyro.copy(),
        quat=frame.quat.copy(),
        timestamp_us=frame.timestamp_us,
        recording=frame.recording,
        imu_fail_mask=frame.imu_fail_mask,
        wrist_gyro_valid=frame.wrist_gyro_valid,
        wrist_imu_valid=frame.wrist_imu_valid,
        protocol=frame.protocol,
        protocol_version=frame.protocol_version,
        has_device_timestamp=frame.has_device_timestamp,
        has_recording_flag=frame.has_recording_flag,
        has_fail_mask=frame.has_fail_mask,
    )


class WirelessImuReader:
    """Background serial reader for one selected glove."""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        hand: str | int = "left",
        timeout: float = DEFAULT_TIMEOUT,
        offline_timeout: float = DEFAULT_OFFLINE_TIMEOUT_SEC,
    ):
        self._port = port
        self._baud = int(baud)
        self._timeout = float(timeout)
        self._offline_timeout = float(offline_timeout)
        self._target_hand_id, self._target_hand_name = normalize_hand(hand)

        self._lock = threading.Lock()
        self._latest: Optional[WirelessImuFrame] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.connected = False

        self._rx_total = 0
        self._rx_rate_hz = 0.0
        self._last_rx_ts = 0.0
        self._timestamps: deque[float] = deque(maxlen=80)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="wireless_imu_reader",
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    @property
    def latest(self) -> Optional[WirelessImuFrame]:
        with self._lock:
            return _copy_frame(self._latest)

    @property
    def hand_id(self) -> int:
        return self._target_hand_id

    @property
    def hand_name(self) -> str:
        return self._target_hand_name

    @property
    def online(self) -> bool:
        with self._lock:
            return self._is_online_locked()

    def _is_online_locked(self) -> bool:
        return (
            self._last_rx_ts > 0.0
            and time.monotonic() - self._last_rx_ts <= self._offline_timeout
        )

    @property
    def stats(self) -> WirelessImuStats:
        with self._lock:
            latest = self._latest
            return WirelessImuStats(
                hand_id=self._target_hand_id,
                hand_name=self._target_hand_name,
                connected=self.connected,
                online=self._is_online_locked(),
                frame_id=0 if latest is None else latest.frame_id,
                bat_percent=0 if latest is None else latest.bat_percent,
                imu_count=0 if latest is None else latest.imu_count,
                rx_total=self._rx_total,
                rx_rate_hz=self._rx_rate_hz,
                last_rx_ts=self._last_rx_ts,
            )

    def _update_selected_hand(self, frame: WirelessImuFrame) -> None:
        with self._lock:
            self._latest = frame
            self._rx_total += 1
            self._last_rx_ts = frame.timestamp
            self._timestamps.append(frame.timestamp)
            if len(self._timestamps) >= 2:
                span = self._timestamps[-1] - self._timestamps[0]
                if span > 0.0:
                    self._rx_rate_hz = (len(self._timestamps) - 1) / span

    def _run(self) -> None:
        serial_module = _load_serial_module("Wireless IMU")
        if serial_module is None:
            self._running = False
            return

        try:
            with _open_serial(serial_module, self._port, self._baud, self._timeout) as ser:
                self.connected = True
                print(
                    f"[Wireless IMU] 已连接: {self._port} @ {self._baud} "
                    f"(hand={self._target_hand_name})"
                )
                buf = bytearray()
                while self._running:
                    _read_into_buffer(ser, buf)
                    for frame in _consume_valid_frames(buf):
                        if frame.hand_id == self._target_hand_id:
                            self._update_selected_hand(frame)
        except serial_module.SerialException as exc:
            print(f"[Wireless IMU] 串口错误: {exc} -> IMU 功能已禁用")
        finally:
            self.connected = False
            self._running = False


class WirelessImuHubReader:
    """Open the receiver serial port once and track both gloves."""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        timeout: float = DEFAULT_TIMEOUT,
        offline_timeout: float = DEFAULT_OFFLINE_TIMEOUT_SEC,
    ):
        self._port = port
        self._baud = int(baud)
        self._timeout = float(timeout)
        self._offline_timeout = float(offline_timeout)

        self._lock = threading.Lock()
        self._latest = {
            HAND_LEFT: None,
            HAND_RIGHT: None,
        }
        self._rx_total = {HAND_LEFT: 0, HAND_RIGHT: 0}
        self._rx_rate_hz = {HAND_LEFT: 0.0, HAND_RIGHT: 0.0}
        self._last_rx_ts = {HAND_LEFT: 0.0, HAND_RIGHT: 0.0}
        self._timestamps = {
            HAND_LEFT: deque(maxlen=80),
            HAND_RIGHT: deque(maxlen=80),
        }

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.connected = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="wireless_imu_hub_reader",
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    @property
    def latest_left(self) -> Optional[WirelessImuFrame]:
        return self.latest_hand(HAND_LEFT)

    @property
    def latest_right(self) -> Optional[WirelessImuFrame]:
        return self.latest_hand(HAND_RIGHT)

    def latest_hand(self, hand: str | int) -> Optional[WirelessImuFrame]:
        hand_id, _ = normalize_hand(hand)
        with self._lock:
            return _copy_frame(self._latest[hand_id])

    def is_online(self, hand: str | int) -> bool:
        hand_id, _ = normalize_hand(hand)
        with self._lock:
            last = self._last_rx_ts[hand_id]
            return last > 0.0 and time.monotonic() - last <= self._offline_timeout

    @property
    def left_online(self) -> bool:
        return self.is_online(HAND_LEFT)

    @property
    def right_online(self) -> bool:
        return self.is_online(HAND_RIGHT)

    def _make_stats_locked(self, hand_id: int) -> WirelessImuStats:
        latest = self._latest[hand_id]
        last = self._last_rx_ts[hand_id]
        return WirelessImuStats(
            hand_id=hand_id,
            hand_name=HAND_NAMES[hand_id],
            connected=self.connected,
            online=(last > 0.0 and time.monotonic() - last <= self._offline_timeout),
            frame_id=0 if latest is None else latest.frame_id,
            bat_percent=0 if latest is None else latest.bat_percent,
            imu_count=0 if latest is None else latest.imu_count,
            rx_total=self._rx_total[hand_id],
            rx_rate_hz=self._rx_rate_hz[hand_id],
            last_rx_ts=last,
        )

    @property
    def stats(self) -> WirelessImuHubStats:
        with self._lock:
            return WirelessImuHubStats(
                connected=self.connected,
                left=self._make_stats_locked(HAND_LEFT),
                right=self._make_stats_locked(HAND_RIGHT),
            )

    def _update_hand(self, frame: WirelessImuFrame) -> None:
        with self._lock:
            hand_id = frame.hand_id
            self._latest[hand_id] = frame
            self._rx_total[hand_id] += 1
            self._last_rx_ts[hand_id] = frame.timestamp
            timestamps = self._timestamps[hand_id]
            timestamps.append(frame.timestamp)
            if len(timestamps) >= 2:
                span = timestamps[-1] - timestamps[0]
                if span > 0.0:
                    self._rx_rate_hz[hand_id] = (len(timestamps) - 1) / span

    def _run(self) -> None:
        serial_module = _load_serial_module("Wireless IMU Hub")
        if serial_module is None:
            self._running = False
            return

        try:
            with _open_serial(serial_module, self._port, self._baud, self._timeout) as ser:
                self.connected = True
                print(
                    f"[Wireless IMU Hub] 已连接: {self._port} @ {self._baud} "
                    "(left/right)"
                )
                buf = bytearray()
                while self._running:
                    _read_into_buffer(ser, buf)
                    for frame in _consume_valid_frames(buf):
                        self._update_hand(frame)
        except serial_module.SerialException as exc:
            print(f"[Wireless IMU Hub] 串口错误: {exc} -> IMU Hub 功能已禁用")
        finally:
            self.connected = False
            self._running = False


def _load_serial_module(label: str):
    global serial
    if serial is None:
        try:
            import serial as serial_module
        except ImportError:  # pragma: no cover
            print(f"[{label}] 缺少 pyserial，请先安装: pip install pyserial")
            return None
        serial = serial_module
    return serial


def _open_serial(serial_module, port: str, baud: int, timeout: float):
    ser = serial_module.Serial(
        port=port,
        baudrate=baud,
        timeout=timeout,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    return ser


def _read_into_buffer(ser, buf: bytearray) -> int:
    chunk = ser.read(1024)
    if chunk:
        buf.extend(chunk)
        return len(chunk)
    return 0


def _consume_valid_frames(buf: bytearray):
    """Yield all currently complete and valid hand frames from ``buf``."""
    while True:
        candidate_info = next_receiver_frame_candidate(buf)
        if candidate_info is None:
            return

        candidate, frame_size = candidate_info
        frame = parse_receiver_frame(candidate)
        if frame is None:
            del buf[:1]
            continue

        del buf[:frame_size]
        yield frame


# Terminal self-test -------------------------------------------------------

IMU_LABELS = [
    "板载腕部",
    "拇指-1",
    "拇指-2",
    "食指-1",
    "食指-2",
    "中指-1",
    "中指-2",
    "无名指-1",
    "无名指-2",
    "小指-1",
    "小指-2",
]

ESC = "\033"
CLEAR = ESC + "[2J" + ESC + "[H"
HOME = ESC + "[H"
EOL = ESC + "[K"
BOLD = ESC + "[1m"
DIM = ESC + "[2m"
RST = ESC + "[0m"
CYAN = ESC + "[96m"
MAG = ESC + "[95m"
GRN = ESC + "[92m"
YEL = ESC + "[93m"
RED = ESC + "[91m"


def _bat_bar(percent: int) -> str:
    percent = max(0, min(100, int(percent)))
    filled = max(0, min(10, percent // 10))
    bar = "█" * filled + "░" * (10 - filled)
    color = GRN if percent > 60 else (YEL if percent > 20 else RED)
    return f"{color}[{bar}] {percent:3d}%{RST}"


def _status_text(online: bool) -> str:
    return f"{GRN}ONLINE{RST}" if online else f"{RED}OFFLINE{RST}"


def _empty_snapshot() -> dict:
    zero3 = (0.0, 0.0, 0.0)
    zero4 = (0.0, 0.0, 0.0, 0.0)
    return {
        "frame_id": 0,
        "bat": 0,
        "imu_count": 0,
        "euler": [zero3] * IMU_COUNT,
        "quat": [zero4] * IMU_COUNT,
        "wrist_gyro": zero3,
        "rx_total": 0,
        "rx_rate": 0.0,
        "online": False,
    }


class _ImuTestStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames = {HAND_LEFT: None, HAND_RIGHT: None}
        self._rx_total = {HAND_LEFT: 0, HAND_RIGHT: 0}
        self._timestamps = {
            HAND_LEFT: deque(maxlen=80),
            HAND_RIGHT: deque(maxlen=80),
        }
        self.rx_bytes = 0
        self.bad_frames = 0

    def note_bytes(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self.rx_bytes += int(count)

    def note_bad_frame(self) -> None:
        with self._lock:
            self.bad_frames += 1

    def feed(self, snapshot: dict) -> None:
        hand_id = int(snapshot["hand_id"])
        with self._lock:
            self._frames[hand_id] = snapshot
            self._rx_total[hand_id] += 1
            self._timestamps[hand_id].append(float(snapshot["last_rx_ts"]))

    def get_side(self, hand_id: int):
        with self._lock:
            return (
                self._frames[hand_id],
                self._rx_total[hand_id],
                tuple(self._timestamps[hand_id]),
            )

    def io_stats(self) -> Tuple[int, int]:
        with self._lock:
            return self.rx_bytes, self.bad_frames


def _snapshot_from_store(
    store: _ImuTestStore,
    hand_id: int,
    offline_timeout: float,
) -> dict:
    raw, rx_total, timestamps = store.get_side(hand_id)
    if raw is None:
        snapshot = _empty_snapshot()
        snapshot["rx_total"] = rx_total
        return snapshot

    rx_rate = 0.0
    if len(timestamps) >= 2:
        span = timestamps[-1] - timestamps[0]
        if span > 0.0:
            rx_rate = (len(timestamps) - 1) / span

    return {
        "frame_id": raw["frame_id"],
        "bat": raw["bat"],
        "imu_count": raw["imu_count"],
        "euler": raw["euler"],
        "quat": raw["quat"],
        "wrist_gyro": raw["wrist_gyro"],
        "rx_total": rx_total,
        "rx_rate": rx_rate,
        "online": time.monotonic() - raw["last_rx_ts"] <= offline_timeout,
    }


def _render_one_hand(output: list[str], snapshot: dict, title: str, color: str) -> None:
    separator = "─" * 88
    output.append(f"{color}{BOLD}{separator}{RST}{EOL}\n")
    output.append(
        f"{color}{BOLD}{title}{RST}  "
        f"frame:{snapshot['frame_id']:5d}  "
        f"{_bat_bar(snapshot['bat'])}  "
        f"{_status_text(snapshot['online'])}  "
        f"rx:{snapshot['rx_rate']:6.1f} Hz  "
        f"total:{snapshot['rx_total']}{EOL}\n"
    )
    output.append(f"{DIM}{separator}{RST}{EOL}\n")

    gx, gy, gz = snapshot["wrist_gyro"]
    output.append(f"  wrist gyro (dps): {gx:8.2f} {gy:8.2f} {gz:8.2f}{EOL}\n")
    output.append(f"{DIM}{separator}{RST}{EOL}\n")
    output.append(
        f"  {'#':>2}  {'标签':<8}  {'Roll':>8} {'Pitch':>8} {'Yaw':>8}"
        f"   {'q0':>7} {'q1':>7} {'q2':>7} {'q3':>7}{EOL}\n"
    )
    output.append(f"{DIM}{separator}{RST}{EOL}\n")

    for index in range(IMU_COUNT):
        roll, pitch, yaw = snapshot["euler"][index]
        q0, q1, q2, q3 = snapshot["quat"][index]
        is_zero = not any((roll, pitch, yaw, q0, q1, q2, q3))
        prefix = DIM if is_zero else ""
        suffix = RST if is_zero else ""
        output.append(
            f"  {index:2d}  {prefix}{IMU_LABELS[index]:<8}"
            f"  {roll:8.2f} {pitch:8.2f} {yaw:8.2f}"
            f"   {q0:7.3f} {q1:7.3f} {q2:7.3f} {q3:7.3f}{suffix}{EOL}\n"
        )


def _reader_thread(
    port: str,
    baud: int,
    store: _ImuTestStore,
    stop_event: threading.Event,
) -> None:
    serial_module = _load_serial_module("ERROR")
    if serial_module is None:
        stop_event.set()
        return

    try:
        ser = _open_serial(serial_module, port, baud, DEFAULT_TIMEOUT)
        print(f"[OK] Opened {port} @ {baud}")
    except Exception as exc:
        print(f"[ERROR] {exc}")
        stop_event.set()
        return

    buf = bytearray()
    try:
        while not stop_event.is_set():
            store.note_bytes(_read_into_buffer(ser, buf))
            while True:
                candidate_info = next_receiver_frame_candidate(buf)
                if candidate_info is None:
                    break
                candidate, frame_size = candidate_info
                snapshot = parse_frame_snapshot(candidate)
                if snapshot is None:
                    store.note_bad_frame()
                    del buf[:1]
                    continue
                del buf[:frame_size]
                store.feed(snapshot)
    finally:
        ser.close()


def _display_loop(
    store: _ImuTestStore,
    *,
    dual: bool,
    filter_hand_id: Optional[int],
    offline_timeout: float,
    hz: float,
    stop_event: threading.Event,
) -> None:
    interval = 1.0 / max(hz, 0.1)
    first = True

    while not stop_event.is_set():
        started = time.monotonic()
        output: list[str] = [CLEAR if first else HOME]
        first = False

        mode = "dual hand" if dual else f"hand_id={filter_hand_id}"
        output.append(
            f"{BOLD}=== Wireless IMU Receiver_RX test | {mode} | "
            f"Ctrl+C to exit ==={RST}{EOL}\n\n"
        )
        rx_bytes, bad_frames = store.io_stats()
        output.append(
            f"{DIM}serial bytes:{rx_bytes}  bad binary candidates:{bad_frames}"
            f"  frame_size:{FRAME_SIZE}  imu_count:{IMU_COUNT}{RST}{EOL}\n\n"
        )

        if dual:
            left = _snapshot_from_store(store, HAND_LEFT, offline_timeout)
            right = _snapshot_from_store(store, HAND_RIGHT, offline_timeout)
            _render_one_hand(output, left, "LEFT  HAND  ◀", CYAN)
            output.append(f"{EOL}\n")
            _render_one_hand(output, right, "RIGHT HAND  ▶", MAG)
        else:
            assert filter_hand_id is not None
            snapshot = _snapshot_from_store(store, filter_hand_id, offline_timeout)
            name = "LEFT" if filter_hand_id == HAND_LEFT else "RIGHT"
            color = CYAN if filter_hand_id == HAND_LEFT else MAG
            _render_one_hand(output, snapshot, f"{name} HAND", color)

        output.append(ESC + "[J")
        sys.stdout.write("".join(output))
        sys.stdout.flush()
        remaining = interval - (time.monotonic() - started)
        if remaining > 0.0:
            time.sleep(remaining)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Test legacy 168/170-byte and V2 180-byte left-right frames emitted by "
            "Receiver_RX.ino."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help="Serial device")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate")
    parser.add_argument(
        "--hand",
        default="all",
        choices=["left", "right", "dual", "all"],
        help="left/right: one panel; dual/all: both gloves",
    )
    parser.add_argument("--hz", type=float, default=15.0, help="Terminal refresh rate")
    parser.add_argument(
        "--offline-timeout",
        type=float,
        default=DEFAULT_OFFLINE_TIMEOUT_SEC,
        help="Seconds without a frame before marking a glove OFFLINE",
    )
    args = parser.parse_args()

    dual = args.hand in ("dual", "all")
    filter_hand_id = None if dual else normalize_hand(args.hand)[0]
    store = _ImuTestStore()
    stop_event = threading.Event()

    print(
        f"=== wireless_receiver test === port={args.port} baud={args.baud} "
        f"mode={args.hand} protocol=Receiver_RX/AUTO(168,170,180)/11IMU"
    )
    print("Waiting for binary frames... (Ctrl+C to exit)\n")
    time.sleep(0.15)

    thread = threading.Thread(
        target=_reader_thread,
        args=(args.port, int(args.baud), store, stop_event),
        daemon=True,
    )
    thread.start()

    try:
        _display_loop(
            store,
            dual=dual,
            filter_hand_id=filter_hand_id,
            offline_timeout=float(args.offline_timeout),
            hz=float(args.hz),
            stop_event=stop_event,
        )
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        thread.join(timeout=1.5)
        print("\nExited.")


__all__ = [
    "ANG_SCALE",
    "BOARD_WRIST_IMU_INDEX",
    "DEFAULT_BAUD",
    "DEFAULT_OFFLINE_TIMEOUT_SEC",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "DEVICE_NAMES",
    "EXTRA_OFFSET",
    "CRC_OFFSET",
    "FLAG_RECORDING",
    "FLAG_WRIST_GYRO_VALID",
    "FRAME_SIZE",
    "LEGACY_FRAME_SIZE",
    "LEGACY_CAPTURE_FRAME_SIZE",
    "GYRO_SCALE",
    "HAND_IDS",
    "HAND_LEFT",
    "HAND_NAMES",
    "HAND_RIGHT",
    "HEADER_SIZE",
    "IMU_COUNT",
    "IMU_RAW_SIZE",
    "MAGIC",
    "PROTOCOL_VERSION",
    "MAX_FRAME_SIZE",
    "QUAT_SCALE",
    "RING_WRIST_IMU_INDEX",
    "WRIST_IMU_INDEX",
    "WirelessImuFrame",
    "WirelessImuHubReader",
    "WirelessImuHubStats",
    "WirelessImuReader",
    "WirelessImuStats",
    "next_receiver_frame_candidate",
    "normalize_hand",
    "parse_frame_snapshot",
    "parse_receiver_frame",
    "receiver_frame_size_for_device_id",
    "crc16_ccitt_false",
    "xor_check",
]

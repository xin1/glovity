"""Decoder for the fixed 180-byte Glovity protocol version 2 frame."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable


MAGIC = b"\xAA\x55"
VERSION = 2
FRAME_SIZE = 180
LEGACY_FRAME_SIZE = 170
LEGACY_SERIAL_FRAME_SIZE = 168
_FRAME = struct.Struct("<2sBBBBHQH77h3hH")
_LEGACY_PAYLOAD = struct.Struct("<77h3h")


class FrameError(ValueError):
    """Raised when a candidate packet is not a valid Glovity frame."""


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class ImuSample:
    euler_deg: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float]
    valid: bool


@dataclass(frozen=True)
class GloveFrame:
    hand_id: int
    recording: bool
    wrist_gyro_valid: bool
    battery_percent: int
    frame_seq: int
    timestamp_us: int
    imu_fail_mask: int
    imus: tuple[ImuSample, ...]
    wrist_gyro_dps: tuple[float, float, float]
    raw: bytes

    def to_dict(self, device_id: str | None = None) -> dict:
        result = {
            "protocol": "v2-180",
            "hand_id": self.hand_id,
            "recording": self.recording,
            "wrist_gyro_valid": self.wrist_gyro_valid,
            "battery_percent": self.battery_percent,
            "frame_seq": self.frame_seq,
            "timestamp_us": self.timestamp_us,
            "imu_fail_mask": self.imu_fail_mask,
            "imus": [
                {
                    "euler_deg": list(imu.euler_deg),
                    "quaternion_wxyz": list(imu.quaternion_wxyz),
                    "valid": imu.valid,
                }
                for imu in self.imus
            ],
            "wrist_gyro_dps": list(self.wrist_gyro_dps),
        }
        if device_id is not None:
            result["device_id"] = device_id
        return result


@dataclass(frozen=True)
class LegacyGloveFrame:
    """Observed 170-byte legacy frame without device timestamp or sequence.

    The capture establishes the layout of the 77 int16 IMU values, wrist gyro,
    and final XOR checksum. Header bytes 5-6 and reserved bytes 167-168 are
    retained but not assigned semantics until the old firmware definition is
    available.
    """

    hand_id: int
    imu_count: int
    battery_percent: int
    header_5: int
    header_6: int
    imus: tuple[ImuSample, ...]
    wrist_gyro_dps: tuple[float, float, float]
    reserved: bytes
    raw: bytes

    def to_dict(self, device_id: str | None = None) -> dict:
        result = {
            "protocol": f"legacy{len(self.raw)}",
            "hand_id": self.hand_id,
            "imu_count": self.imu_count,
            "battery_percent": self.battery_percent,
            "header_5": self.header_5,
            "header_6": self.header_6,
            "imus": [
                {"euler_deg": list(imu.euler_deg), "quaternion_wxyz": list(imu.quaternion_wxyz), "valid": True}
                for imu in self.imus
            ],
            "wrist_gyro_dps": list(self.wrist_gyro_dps),
            "reserved_hex": self.reserved.hex(),
        }
        if device_id is not None:
            result["device_id"] = device_id
        return result


def frame_from_dict(value: dict) -> GloveFrame:
    """Recreate a decoded frame from :meth:`GloveFrame.to_dict` output."""
    imus = tuple(
        ImuSample(
            tuple(float(x) for x in item["euler_deg"]),
            tuple(float(x) for x in item["quaternion_wxyz"]),
            bool(item["valid"]),
        )
        for item in value["imus"]
    )
    if len(imus) != 11:
        raise FrameError(f"expected 11 IMUs, got {len(imus)}")
    return GloveFrame(
        hand_id=int(value["hand_id"]),
        recording=bool(value["recording"]),
        wrist_gyro_valid=bool(value["wrist_gyro_valid"]),
        battery_percent=int(value["battery_percent"]),
        frame_seq=int(value["frame_seq"]),
        timestamp_us=int(value["timestamp_us"]),
        imu_fail_mask=int(value["imu_fail_mask"]),
        imus=imus,
        wrist_gyro_dps=tuple(float(x) for x in value["wrist_gyro_dps"]),
        raw=b"",
    )


def decode_frame(packet: bytes) -> GloveFrame:
    if len(packet) != FRAME_SIZE:
        raise FrameError(f"expected {FRAME_SIZE} bytes, got {len(packet)}")
    fields = _FRAME.unpack(packet)
    magic, version, hand_id, flags, battery, seq, timestamp, fail_mask = fields[:8]
    if magic != MAGIC or version != VERSION:
        raise FrameError("bad magic or unsupported protocol version")
    if hand_id not in (0, 1):
        raise FrameError(f"invalid hand_id {hand_id}")
    if flags & ~0x03:
        raise FrameError("reserved flag bits are non-zero")
    if battery > 100 or fail_mask & ~0x07FF:
        raise FrameError("invalid battery or IMU failure mask")
    expected_crc = fields[-1]
    actual_crc = crc16_ccitt_false(packet[:178])
    if actual_crc != expected_crc:
        raise FrameError(f"CRC mismatch: expected 0x{expected_crc:04X}, got 0x{actual_crc:04X}")

    raw_imus = fields[8:85]
    imus = []
    for index in range(11):
        values = raw_imus[index * 7 : index * 7 + 7]
        euler = tuple(value / 32768.0 * 180.0 for value in values[:3])
        quaternion = tuple(value / 32768.0 for value in values[3:])
        imus.append(ImuSample(euler, quaternion, not bool(fail_mask & (1 << index))))
    gyro_raw = fields[85:88]
    gyro = tuple(value / 32768.0 * 2000.0 for value in gyro_raw)
    return GloveFrame(
        hand_id=hand_id,
        recording=bool(flags & 0x01),
        wrist_gyro_valid=bool(flags & 0x02),
        battery_percent=battery,
        frame_seq=seq,
        timestamp_us=timestamp,
        imu_fail_mask=fail_mask,
        imus=tuple(imus),
        wrist_gyro_dps=gyro,
        raw=packet,
    )


def decode_legacy_frame(packet: bytes) -> LegacyGloveFrame:
    if len(packet) not in (LEGACY_SERIAL_FRAME_SIZE, LEGACY_FRAME_SIZE):
        raise FrameError(f"expected 168 or 170 legacy bytes, got {len(packet)}")
    if packet[:2] != MAGIC:
        raise FrameError("bad legacy magic")
    checksum_offset = len(packet) - 1
    checksum = 0
    for byte in packet[:checksum_offset]:
        checksum ^= byte
    if checksum != packet[checksum_offset]:
        raise FrameError(f"legacy XOR mismatch: expected 0x{packet[checksum_offset]:02X}, got 0x{checksum:02X}")
    hand_id, imu_count, battery = packet[2], packet[3], packet[4]
    if hand_id not in (0, 1) or imu_count != 11 or battery > 100:
        raise FrameError("invalid legacy header")
    values = _LEGACY_PAYLOAD.unpack(packet[7:167])
    imus = []
    for index in range(11):
        sample = values[index * 7 : index * 7 + 7]
        imus.append(
            ImuSample(
                tuple(value / 32768.0 * 180.0 for value in sample[:3]),
                tuple(value / 32768.0 for value in sample[3:]),
                True,
            )
        )
    gyro = tuple(value / 32768.0 * 2000.0 for value in values[77:80])
    return LegacyGloveFrame(hand_id, imu_count, battery, packet[5], packet[6], tuple(imus), gyro, packet[167:checksum_offset], packet)


class AutoStreamDecoder:
    """Decode both observed 170-byte legacy frames and 180-byte V2 frames."""

    def __init__(self, max_buffer: int = FRAME_SIZE * 16) -> None:
        self._buffer = bytearray()
        self.max_buffer = max(max_buffer, FRAME_SIZE)
        self.rejected_candidates = 0
        self.discarded_bytes = 0
        self.legacy_frames = 0
        self.v2_frames = 0

    def feed(self, chunk: bytes) -> list[GloveFrame | LegacyGloveFrame]:
        self._buffer.extend(chunk)
        decoded: list[GloveFrame | LegacyGloveFrame] = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                keep = 1 if self._buffer.endswith(MAGIC[:1]) else 0
                self.discarded_bytes += len(self._buffer) - keep
                del self._buffer[: len(self._buffer) - keep]
                break
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < LEGACY_SERIAL_FRAME_SIZE:
                break
            is_v2 = self._buffer[2] == VERSION
            if is_v2:
                required = FRAME_SIZE
            else:
                required = LEGACY_FRAME_SIZE
                if len(self._buffer) >= LEGACY_FRAME_SIZE:
                    try:
                        decode_legacy_frame(bytes(self._buffer[:LEGACY_FRAME_SIZE]))
                    except FrameError:
                        required = LEGACY_SERIAL_FRAME_SIZE
                elif len(self._buffer) == LEGACY_SERIAL_FRAME_SIZE:
                    try:
                        decode_legacy_frame(bytes(self._buffer[:LEGACY_SERIAL_FRAME_SIZE]))
                    except FrameError:
                        break
                    required = LEGACY_SERIAL_FRAME_SIZE
            if len(self._buffer) < required:
                break
            try:
                if is_v2:
                    frame = decode_frame(bytes(self._buffer[:required]))
                    self.v2_frames += 1
                else:
                    frame = decode_legacy_frame(bytes(self._buffer[:required]))
                    self.legacy_frames += 1
                decoded.append(frame)
                del self._buffer[:required]
            except FrameError:
                self.rejected_candidates += 1
                self.discarded_bytes += 1
                del self._buffer[0]
        if len(self._buffer) > self.max_buffer:
            excess = len(self._buffer) - self.max_buffer
            self.discarded_bytes += excess
            del self._buffer[:excess]
        return decoded


class StreamDecoder:
    """Recover valid frames from arbitrary USB chunks, noise, and corrupt packets."""

    def __init__(self, max_buffer: int = FRAME_SIZE * 16) -> None:
        self._buffer = bytearray()
        self.max_buffer = max(max_buffer, FRAME_SIZE)
        self.rejected_candidates = 0
        self.discarded_bytes = 0

    def feed(self, chunk: bytes) -> list[GloveFrame]:
        self._buffer.extend(chunk)
        decoded: list[GloveFrame] = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                keep = 1 if self._buffer.endswith(MAGIC[:1]) else 0
                self.discarded_bytes += len(self._buffer) - keep
                del self._buffer[: len(self._buffer) - keep]
                break
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < FRAME_SIZE:
                break
            candidate = bytes(self._buffer[:FRAME_SIZE])
            try:
                decoded.append(decode_frame(candidate))
                del self._buffer[:FRAME_SIZE]
            except FrameError:
                self.rejected_candidates += 1
                self.discarded_bytes += 1
                del self._buffer[0]
        if len(self._buffer) > self.max_buffer:
            excess = len(self._buffer) - self.max_buffer
            self.discarded_bytes += excess
            del self._buffer[:excess]
        return decoded


def unwrap_sequence(values: Iterable[int]) -> list[int]:
    """Unwrap uint16 sequence values while preserving small out-of-order steps."""
    result: list[int] = []
    epoch = 0
    previous: int | None = None
    for value in values:
        if not 0 <= value <= 0xFFFF:
            raise ValueError("sequence number must be uint16")
        if previous is not None:
            delta = value - previous
            if delta < -0x8000:
                epoch += 0x10000
            elif delta > 0x8000:
                result.append(epoch - 0x10000 + value)
                previous = value
                continue
        result.append(epoch + value)
        previous = value
    return result

from __future__ import annotations

from dataclasses import dataclass


HI91_HEADER = b"\x5A\xA5"
HI91_TAG = 0x91
HI91_PAYLOAD_LENGTH = 76
CONFIG_HEADER = 0x68
SET_ADDRESS_COMMAND = 0x0F
SET_ADDRESS_RESPONSE = 0x8F
QUERY_COMMAND = 0x03


@dataclass(frozen=True)
class Hi91Frame:
    address: int
    main_status: int
    temperature_c: int
    timestamp_ms: int
    raw: bytes


@dataclass(frozen=True)
class ConfigResponse:
    address: int
    command: int
    result: int
    checksum_valid: bool
    raw: bytes


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT used by IMU761 HI91 frames (poly 0x1021, init 0)."""
    crc = 0
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def verify_hi91_crc(frame: bytes) -> bool:
    if len(frame) < 6:
        return False
    payload_length = int.from_bytes(frame[2:4], "little")
    if len(frame) != 6 + payload_length:
        return False
    expected = int.from_bytes(frame[4:6], "little")
    actual = crc16_ccitt(frame[:4] + frame[6:])
    return actual == expected


def decode_hi91(frame: bytes) -> Hi91Frame | None:
    if len(frame) < 18 or frame[:2] != HI91_HEADER:
        return None
    payload_length = int.from_bytes(frame[2:4], "little")
    if payload_length != HI91_PAYLOAD_LENGTH or len(frame) != 6 + payload_length:
        return None
    if not verify_hi91_crc(frame):
        return None

    payload = frame[6:]
    if payload[0] != HI91_TAG:
        return None

    temperature = payload[3] if payload[3] < 0x80 else payload[3] - 0x100
    return Hi91Frame(
        address=payload[4],
        main_status=int.from_bytes(payload[1:3], "little"),
        temperature_c=temperature,
        timestamp_ms=int.from_bytes(payload[8:12], "little"),
        raw=frame,
    )


def build_query_command(address: int) -> bytes:
    """Vendor addressed single-frame query; binary bytes, no CR/LF."""
    if not 0 <= address <= 0xFE:
        raise ValueError("address must be between 0x00 and 0xFE")
    body = bytes((0x04, address, QUERY_COMMAND))
    return bytes((CONFIG_HEADER,)) + body + bytes((sum(body) & 0xFF,))


def build_set_address_command(current_address: int, new_address: int) -> bytes:
    for name, value in (("current_address", current_address), ("new_address", new_address)):
        if not 0 <= value <= 0xFE:
            raise ValueError(f"{name} must be between 0x00 and 0xFE")

    body = bytes((0x05, current_address, SET_ADDRESS_COMMAND, new_address))
    checksum = sum(body) & 0xFF
    return bytes((CONFIG_HEADER,)) + body + bytes((checksum,))


def format_hex(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


class StreamParser:
    """Extracts HI91 frames and short address-command responses from a UART stream."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_error_count = 0
        self.discarded_byte_count = 0

    def clear(self) -> None:
        self.buffer.clear()

    def feed(self, data: bytes) -> list[Hi91Frame | ConfigResponse]:
        if data:
            self.buffer.extend(data)

        events: list[Hi91Frame | ConfigResponse] = []
        while True:
            if len(self.buffer) < 2:
                break

            hi91_index = self.buffer.find(HI91_HEADER)
            config_index = self.buffer.find(bytes((CONFIG_HEADER,)))
            candidates = [index for index in (hi91_index, config_index) if index >= 0]
            if not candidates:
                keep = 1 if self.buffer[-1] == HI91_HEADER[0] else 0
                removed = len(self.buffer) - keep
                self.discarded_byte_count += removed
                if keep:
                    self.buffer[:] = self.buffer[-1:]
                else:
                    self.buffer.clear()
                break

            start = min(candidates)
            if start:
                del self.buffer[:start]
                self.discarded_byte_count += start

            if self.buffer.startswith(HI91_HEADER):
                if len(self.buffer) < 6:
                    break
                payload_length = int.from_bytes(self.buffer[2:4], "little")
                if payload_length != HI91_PAYLOAD_LENGTH:
                    del self.buffer[0]
                    self.discarded_byte_count += 1
                    continue
                frame_length = 6 + payload_length
                if len(self.buffer) < frame_length:
                    break
                raw = bytes(self.buffer[:frame_length])
                frame = decode_hi91(raw)
                if frame is None:
                    del self.buffer[0]
                    self.crc_error_count += 1
                    continue
                del self.buffer[:frame_length]
                events.append(frame)
                continue

            if self.buffer[0] == CONFIG_HEADER:
                if len(self.buffer) < 2:
                    break
                declared_length = self.buffer[1]
                if declared_length not in (4, 5):
                    del self.buffer[0]
                    self.discarded_byte_count += 1
                    continue
                if len(self.buffer) < 4:
                    break
                if self.buffer[3] not in (QUERY_COMMAND, SET_ADDRESS_COMMAND, SET_ADDRESS_RESPONSE):
                    del self.buffer[0]
                    self.discarded_byte_count += 1
                    continue
                frame_length = 1 + declared_length
                if len(self.buffer) < frame_length:
                    break
                raw = bytes(self.buffer[:frame_length])
                command = raw[3]
                result = raw[4] if len(raw) > 5 else 0xFF
                direct_checksum = sum(raw[1:-1]) & 0xFF
                # The vendor example calculates the response checksum with 0x0F,
                # even though the response command byte is 0x8F.
                vendor_checksum = (
                    raw[1] + raw[2] + (command & 0x7F) + sum(raw[4:-1])
                ) & 0xFF
                checksum_valid = raw[-1] in (direct_checksum, vendor_checksum)
                if not checksum_valid:
                    del self.buffer[0]
                    self.discarded_byte_count += 1
                    continue
                del self.buffer[:frame_length]
                if command == SET_ADDRESS_RESPONSE:
                    events.append(
                        ConfigResponse(
                            address=raw[2],
                            command=command,
                            result=result,
                            checksum_valid=checksum_valid,
                            raw=raw,
                        )
                    )
                continue

        return events

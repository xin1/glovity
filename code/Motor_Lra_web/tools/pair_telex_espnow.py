"""Pair one TELEX S3 glove with one C6 receiver over the TELEX USB protocol."""

from __future__ import annotations

import argparse
import json
import struct
import time

import serial


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    output = bytearray(b"\x00")
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 1
    output[code_index] = code
    output.append(0)
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    output = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        index += 1
        if code == 0 or index + code - 1 > len(data):
            raise ValueError("invalid COBS frame")
        output.extend(data[index : index + code - 1])
        index += code - 1
        if code != 0xFF and index < len(data):
            output.append(0)
    return bytes(output)


def encode_packet(kind: int, sequence: int, target: int, payload: bytes = b"") -> bytes:
    header = bytearray(24)
    header[0:3] = b"TX\x01"
    header[3] = kind
    header[6] = target
    struct.pack_into("<IIIH", header, 8, 0, sequence, int(time.monotonic() * 1000) & 0xFFFFFFFF, len(payload))
    raw = header + payload
    raw += struct.pack("<H", crc16(raw))
    return cobs_encode(raw)


def decode_packet(frame: bytes) -> dict:
    raw = cobs_decode(frame)
    if len(raw) < 26 or raw[:3] != b"TX\x01":
        raise ValueError("invalid TELEX packet")
    length = struct.unpack_from("<H", raw, 20)[0]
    if length != len(raw) - 26 or crc16(raw[:-2]) != struct.unpack_from("<H", raw, len(raw) - 2)[0]:
        raise ValueError("invalid TELEX length or CRC")
    return {
        "kind": raw[3],
        "source": raw[5],
        "target": raw[6],
        "sequence": struct.unpack_from("<I", raw, 12)[0],
        "payload": bytes(raw[24:-2]),
    }


class Device:
    def __init__(self, port: str):
        self.port = port
        self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=1)
        self.sequence = 1

    def close(self) -> None:
        self.serial.close()

    def request(self, kind: int, target: int, payload: bytes = b"", timeout: float = 3.0) -> dict:
        sequence = self.sequence
        self.sequence += 1
        self.serial.reset_input_buffer()
        self.serial.write(encode_packet(kind, sequence, target, payload))
        deadline = time.monotonic() + timeout
        frame = bytearray()
        while time.monotonic() < deadline:
            value = self.serial.read(1)
            if not value:
                continue
            if value != b"\x00":
                frame.extend(value)
                if len(frame) > 251:
                    frame.clear()
                continue
            if not frame:
                continue
            encoded = bytes(frame)
            frame.clear()
            try:
                packet = decode_packet(encoded)
            except ValueError:
                continue
            if packet["sequence"] == sequence:
                return packet
        raise TimeoutError(f"{self.port}: TELEX request 0x{kind:02x} timed out")

    def identity(self) -> dict:
        packet = self.request(1, 0)
        if packet["kind"] != 1:
            raise RuntimeError(f"{self.port}: invalid identity response")
        return json.loads(packet["payload"].decode("utf-8"))

    def write_config(self, role: int, uid: bytes, peer: bytes, suite: str, haptics: bool) -> None:
        record = bytearray(96)
        record[:5] = b"TXCF\x01"
        record[12] = 1  # TELEX v1 mode
        record[13] = 0  # JY901P (ignored by the receiver)
        record[15] = int(haptics)
        struct.pack_into("<H", record, 16, 30)
        record[20:26] = uid
        suite_bytes = suite.encode("ascii")
        record[26 : 26 + len(suite_bytes)] = suite_bytes
        record[58] = 1
        record[59:65] = peer
        struct.pack_into("<H", record, 94, crc16(record[:94]))
        packet = self.request(3, role, bytes(record), timeout=4.0)
        payload = packet["payload"]
        if packet["kind"] != 0x7E or len(payload) != 99 or payload[:3] != b"\x03\x01\x00":
            raise RuntimeError(f"{self.port}: configuration was not persistently acknowledged")
        if payload[3 + 20 : 3 + 26] != uid or payload[3 + 59 : 3 + 65] != peer:
            raise RuntimeError(f"{self.port}: configuration readback mismatch")


def uid_bytes(identity: dict) -> bytes:
    value = identity.get("uid", "")
    if len(value) != 12:
        raise RuntimeError(f"invalid device UID: {value!r}")
    return bytes.fromhex(value)


def valid_suite(value: str) -> str:
    if not value or len(value) > 31 or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in value):
        raise argparse.ArgumentTypeError("suite must contain 1-31 ASCII letters, digits, '_' or '-'")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Pair a TELEX S3 glove and C6 receiver without TELEX Bench")
    parser.add_argument("--glove-port", help="S3 glove serial port, for example COM60")
    parser.add_argument("--receiver-port", required=True, help="C6 receiver serial port, for example COM54")
    parser.add_argument("--suite", type=valid_suite, default="GLOVITY_L_01", help="shared suite ID")
    parser.add_argument("--verify-only", action="store_true", help="only verify radio packets arriving at the receiver")
    args = parser.parse_args()

    if args.verify_only:
        receiver = serial.Serial(args.receiver_port, 115200, timeout=0.2)
        try:
            print(f"Listening on {args.receiver_port} for 8 seconds. Keep the left glove powered...")
            deadline = time.monotonic() + 8
            frame = bytearray()
            poses = 0
            while time.monotonic() < deadline:
                value = receiver.read(1)
                if not value:
                    continue
                if value != b"\x00":
                    frame.extend(value)
                    if len(frame) > 251:
                        frame.clear()
                    continue
                if not frame:
                    continue
                encoded = bytes(frame)
                frame.clear()
                try:
                    packet = decode_packet(encoded)
                except ValueError:
                    continue
                if packet["kind"] == 0x10 and packet["source"] == 1:
                    poses += 1
            if poses:
                print(f"PASS: C6 received {poses} left-glove pose packets over ESP-NOW.")
                return 0
            print("FAIL: no left-glove pose packet reached the C6 receiver.")
            return 2
        finally:
            receiver.close()

    if not args.glove_port:
        parser.error("--glove-port is required unless --verify-only is used")

    glove = Device(args.glove_port)
    receiver = Device(args.receiver_port)
    try:
        glove_identity = glove.identity()
        receiver_identity = receiver.identity()
        if glove_identity.get("chip") != "esp32s3" or glove_identity.get("role") != 1:
            raise RuntimeError(f"{args.glove_port} is not a left-hand S3: {glove_identity}")
        if receiver_identity.get("chip") != "esp32c6" or receiver_identity.get("role") != 3:
            raise RuntimeError(f"{args.receiver_port} is not a C6 receiver: {receiver_identity}")

        glove_uid = uid_bytes(glove_identity)
        receiver_uid = uid_bytes(receiver_identity)
        print(f"Left glove: {glove_uid.hex(':')} ({args.glove_port})")
        print(f"Receiver:   {receiver_uid.hex(':')} ({args.receiver_port})")
        print(f"Suite:      {args.suite}")

        # Configure the receiver first so it is ready to admit the glove after both reboot.
        receiver.write_config(3, receiver_uid, glove_uid, args.suite, False)
        print("Receiver configuration written and verified.")
        glove.write_config(1, glove_uid, receiver_uid, args.suite, True)
        print("Left-glove configuration written and verified.")
        print("Pairing complete. Unplug both devices; reconnect the C6 first, then power the glove.")
        return 0
    finally:
        glove.close()
        receiver.close()


if __name__ == "__main__":
    raise SystemExit(main())

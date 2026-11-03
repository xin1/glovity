"""PX-6AX GEN3 single serial-converter board, fin_master V1.0.5.

Based on the project's vendor protocol/reference. Strict response identity,
length and LRC checks; the board's ERR_LEN quirk needs explicit opt-in.
"""
import struct
import time

from .mapping import Sample


def read_request(device_id, address, size):
    if type(device_id) is not int or not 1 <= device_id <= 255:
        raise ValueError("device ID must be 1..255")
    if not 1 <= size <= 1024:
        raise ValueError("read size must be 1..1024")
    frame = struct.pack("<2sHBBBIH", b"\x55\xaa", 9, device_id, 0, 0xfb, address, size)
    return frame + bytes((-sum(frame) & 255,))


def decode_response(frame, device_id, address, size, allow_err_len=False):
    if len(frame) != 15 + size or frame[:2] != b"\xaa\x55":
        raise ValueError("PX response length/header mismatch")
    if sum(frame) & 255 or int.from_bytes(frame[2:4], "little") != len(frame) - 5:
        raise ValueError("PX LRC/frame length mismatch")
    if frame[4:7] != bytes((device_id, 0, 0xfb)):
        raise ValueError("PX response device/function mismatch")
    if int.from_bytes(frame[7:11], "little") != address or int.from_bytes(frame[11:13], "little") != size:
        raise ValueError("PX response register/data length mismatch")
    if frame[13] != 0 and not (allow_err_len and frame[13] == 1):
        raise ValueError(f"PX status error {frame[13]}")
    return frame[14:-1]


def vector(data):
    x, y, z = struct.unpack("bbB", data)
    return x * .1, y * .1, z * .1


class PaxiniSource:
    def __init__(self, port, device_id=1, sensor="finger", points=0,
                 allow_err_len=False, timeout=.075):
        if type(points) is not int or not 0 <= points <= 239:
            raise ValueError("points must be 0..239")
        read_request(device_id, 0x3f0, 3)
        self.port, self.device_id, self.sensor = port, device_id, sensor
        self.points, self.allow_err_len, self.timeout = points, allow_err_len, timeout
        self.serial = None
        self.sequence = 0

    def __enter__(self):
        import serial
        self.serial = serial.Serial(self.port, 921600, timeout=.005, write_timeout=.05)
        return self

    def __exit__(self, *args):
        if self.serial:
            self.serial.close()

    def read(self):
        # Read resultant and optional taxels in one contiguous transaction.
        # Registers 0x3f3..0x40d are skipped, NOT interpreted as force points.
        size = 30 + self.points * 3 if self.points else 3
        self.serial.reset_input_buffer()
        acquired = time.monotonic()  # conservative: request time, not reply time
        self.serial.write(read_request(self.device_id, 0x3f0, size))
        buf = bytearray()
        while time.monotonic() - acquired < self.timeout:
            buf.extend(self.serial.read(max(1, min(self.serial.in_waiting, 1024))))
            start = buf.find(b"\xaa\x55")
            if start < 0:
                buf[:] = buf[-1:]
                continue
            del buf[:start]
            if len(buf) < 4:
                continue
            total = int.from_bytes(buf[2:4], "little") + 5
            if total != size + 15:
                raise ValueError("PX unexpected declared response size")
            if len(buf) < total:
                continue
            if len(buf) != total:
                raise ValueError("PX unsolicited trailing data")
            data = decode_response(bytes(buf), self.device_id, 0x3f0, size, self.allow_err_len)
            self.sequence += 1
            return Sample(self.sensor, self.sequence, acquired, vector(data[:3]),
                          tuple(vector(data[i:i+3]) for i in range(30, size, 3)))
        raise TimeoutError("PX tactile response timeout")

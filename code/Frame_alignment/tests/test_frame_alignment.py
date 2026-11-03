from __future__ import annotations

import math
import struct
import unittest

from frame_alignment.clock import SyncPoint, fit_clock_model
from frame_alignment.protocol import AutoStreamDecoder, FRAME_SIZE, StreamDecoder, crc16_ccitt_false, decode_frame, decode_legacy_frame, unwrap_sequence
from frame_alignment.resample import slerp


def protocol_vector() -> bytes:
    prefix = struct.pack("<2sBBBBHQH", b"\xAA\x55", 2, 0, 3, 100, 1, 1_000_000, 0)
    body = prefix + bytes(160)
    return body + struct.pack("<H", crc16_ccitt_false(body))


class ProtocolTests(unittest.TestCase):
    def test_legacy_170_xor_frame(self) -> None:
        packet = bytearray(170)
        packet[:7] = bytes((0xAA, 0x55, 0, 11, 95, 0, 6))
        checksum = 0
        for byte in packet[:169]:
            checksum ^= byte
        packet[169] = checksum
        frame = decode_legacy_frame(bytes(packet))
        self.assertEqual(frame.battery_percent, 95)
        decoder = AutoStreamDecoder()
        self.assertEqual(len(decoder.feed(b"noise" + bytes(packet))), 1)
        self.assertEqual(decoder.legacy_frames, 1)

    def test_legacy_168_xor_frame(self) -> None:
        packet = bytearray(168)
        packet[:7] = bytes((0xAA, 0x55, 1, 11, 90, 8, 0))
        for byte in packet[:167]:
            packet[167] ^= byte
        frame = decode_legacy_frame(bytes(packet))
        self.assertEqual(frame.hand_id, 1)
        decoder = AutoStreamDecoder()
        self.assertEqual(len(decoder.feed(bytes(packet))), 1)

    def test_documented_crc_vector(self) -> None:
        packet = protocol_vector()
        self.assertEqual(len(packet), FRAME_SIZE)
        self.assertEqual(packet[-2:], b"\x1A\x49")
        frame = decode_frame(packet)
        self.assertEqual(frame.timestamp_us, 1_000_000)
        self.assertTrue(frame.recording)

    def test_stream_recovery(self) -> None:
        packet = protocol_vector()
        broken = bytearray(packet)
        broken[20] ^= 1
        decoder = StreamDecoder()
        frames = decoder.feed(b"noise" + bytes(broken) + packet[:37])
        self.assertEqual(frames, [])
        frames = decoder.feed(packet[37:] + packet)
        self.assertEqual(len(frames), 2)
        self.assertEqual(decoder.rejected_candidates, 1)

    def test_sequence_wrap(self) -> None:
        self.assertEqual(unwrap_sequence([65534, 65535, 0, 1]), [65534, 65535, 65536, 65537])


class ClockTests(unittest.TestCase):
    def test_fit_offset_and_drift_with_outlier(self) -> None:
        points = [SyncPoint(x, round(1.00002 * x + 5000)) for x in range(0, 10_000_000, 1_000_000)]
        points.append(SyncPoint(4_500_000, 9_000_000))
        model = fit_clock_model(points)
        self.assertAlmostEqual(model.drift_ppm, 20.0, places=2)
        self.assertAlmostEqual(model.offset_us, 5000.0, places=1)
        self.assertEqual(model.point_count, 10)

    def test_single_sync_point_is_offset_only(self) -> None:
        model = fit_clock_model([SyncPoint(100, 250)])
        self.assertEqual(model.scale, 1.0)
        self.assertEqual(model.to_nokov(200), 350)


class ResampleTests(unittest.TestCase):
    def test_slerp_sign_continuity(self) -> None:
        result = slerp((1, 0, 0, 0), (-1, 0, 0, 0), 0.5)
        self.assertIsNotNone(result)
        self.assertTrue(math.isclose(abs(result[0]), 1.0))


if __name__ == "__main__":
    unittest.main()

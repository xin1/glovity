import unittest

from protocol import (
    ConfigResponse,
    Hi91Frame,
    StreamParser,
    build_set_address_command,
    decode_hi91,
    verify_hi91_crc,
)


SAMPLE_ADDRESS_05 = bytes.fromhex(
    "5A A5 4C 00 D1 87 91 08 18 20 05 00 00 00 AB 49 00 00 "
    "B7 2B FB 3B BA 29 D0 BE 5A 5B 6A 3F A6 C1 02 C0 "
    "57 67 E6 3D 7A AD 11 BC 78 7E B7 C1 0C F9 D9 C1 "
    "CC 95 5B C2 20 AB 1F BF C1 A6 BF C1 C6 88 70 BF "
    "8F 6A 7A 3F 19 8E 54 BE FD 66 6D BB 13 CF E1 BB"
)


class ProtocolTests(unittest.TestCase):
    def test_build_set_address_command(self) -> None:
        self.assertEqual(
            build_set_address_command(0x00, 0x05),
            bytes.fromhex("68 05 00 0F 05 19"),
        )
        self.assertEqual(
            build_set_address_command(0x01, 0x05),
            bytes.fromhex("68 05 01 0F 05 1A"),
        )

    def test_decode_captured_address_05_frame(self) -> None:
        self.assertEqual(len(SAMPLE_ADDRESS_05), 82)
        self.assertTrue(verify_hi91_crc(SAMPLE_ADDRESS_05))
        frame = decode_hi91(SAMPLE_ADDRESS_05)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.address, 0x05)
        self.assertEqual(frame.main_status, 0x1808)
        self.assertEqual(frame.temperature_c, 32)
        self.assertEqual(frame.timestamp_ms, 0x49AB)

    def test_stream_parser_handles_fragmented_input(self) -> None:
        parser = StreamParser()
        events = []
        for start in range(0, len(SAMPLE_ADDRESS_05), 7):
            events.extend(parser.feed(SAMPLE_ADDRESS_05[start : start + 7]))
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], Hi91Frame)
        self.assertEqual(events[0].address, 0x05)

    def test_vendor_response_checksum_variant(self) -> None:
        parser = StreamParser()
        events = parser.feed(bytes.fromhex("68 05 00 8F 00 14"))
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ConfigResponse)
        self.assertTrue(events[0].checksum_valid)
        self.assertEqual(events[0].result, 0x00)


if __name__ == "__main__":
    unittest.main()

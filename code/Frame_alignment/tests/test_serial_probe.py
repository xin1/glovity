from __future__ import annotations

import unittest

from frame_alignment.protocol import StreamDecoder
from frame_alignment.serial_probe import ProbeStats, _diagnosis


class SerialDiagnosisTests(unittest.TestCase):
    def test_no_bytes(self) -> None:
        message = _diagnosis(ProbeStats(0), StreamDecoder(), b"", 1.0)
        self.assertIn("未收到任何字节", message)

    def test_text_stream(self) -> None:
        stats = ProbeStats(0, total_bytes=12, printable_bytes=12)
        message = _diagnosis(stats, StreamDecoder(), b"hello world\n", 1.0)
        self.assertIn("主要是文本", message)

    def test_unknown_binary(self) -> None:
        stats = ProbeStats(0, total_bytes=10, printable_bytes=0)
        message = _diagnosis(stats, StreamDecoder(), bytes(range(10)), 1.0)
        self.assertIn("没有AA 55帧头", message)


if __name__ == "__main__":
    unittest.main()

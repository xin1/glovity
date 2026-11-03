import tempfile
import unittest
from pathlib import Path

from pairing import LEFT_PLACEHOLDER, RECEIVER_PLACEHOLDER, RIGHT_PLACEHOLDER, make_paired_images, normalize_mac


class PairingTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_mac("aa:bb:cc:dd:ee:ff"), "AA:BB:CC:DD:EE:FF")

    def test_patch_images(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            fw, out = root / "fw", root / "out"
            fw.mkdir()
            (fw / "receiver.merged.bin").write_bytes(b"R" + LEFT_PLACEHOLDER + RIGHT_PLACEHOLDER)
            (fw / "left.merged.bin").write_bytes(b"L" + RECEIVER_PLACEHOLDER)
            (fw / "right.merged.bin").write_bytes(b"X" + RECEIVER_PLACEHOLDER)
            result = make_paired_images(fw, out, "10:11:12:13:14:15", "20:21:22:23:24:25", "30:31:32:33:34:35")
            self.assertIn(bytes.fromhex("202122232425"), result["receiver"].read_bytes())
            self.assertIn(bytes.fromhex("303132333435"), result["receiver"].read_bytes())
            self.assertIn(bytes.fromhex("101112131415"), result["left"].read_bytes())


if __name__ == "__main__":
    unittest.main()

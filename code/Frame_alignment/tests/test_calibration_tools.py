import unittest

import numpy as np

from calibration_tools.common import TextLineExtractor
from calibration_tools.three_step_calibrate import (
    frame_quaternions_to_slots,
    mean_quaternion_wxyz,
)


class CalibrationToolTests(unittest.TestCase):
    def test_text_extractor_ignores_binary(self):
        extractor = TextLineExtractor()
        lines = extractor.feed(b"\xaa\x55\x02junk$GLOVITY,CAL,STARTED\r\n\x00")
        self.assertEqual(lines, ["$GLOVITY,CAL,STARTED"])

    def test_quaternion_mean_handles_antipodes(self):
        result = mean_quaternion_wxyz([(1.0, 0.0, 0.0, 0.0), (-1.0, 0.0, 0.0, 0.0)])
        np.testing.assert_allclose(result, [1.0, 0.0, 0.0, 0.0], atol=1e-15)

    def test_slot_mapping_and_dip_copy(self):
        imus = np.zeros((11, 4), dtype=np.float64)
        imus[:, 0] = 1.0
        slots = frame_quaternions_to_slots(imus)
        self.assertEqual(slots.shape, (16, 4))
        for pip, dip in ((2, 3), (5, 6), (8, 9), (11, 12), (14, 15)):
            np.testing.assert_allclose(slots[pip], slots[dip], atol=1e-15)
        np.testing.assert_allclose(slots[0], [0.0, 1.0, 0.0, 0.0], atol=1e-15)


if __name__ == "__main__":
    unittest.main()

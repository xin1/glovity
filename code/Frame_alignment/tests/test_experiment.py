from __future__ import annotations

import unittest

from frame_alignment.experiment import ArrivalClockModel, ImuRecord, nearest_alignment


class ExperimentTests(unittest.TestCase):
    def test_clock_mapping(self) -> None:
        model = ArrivalClockModel()
        for index in range(100):
            glove = index * 10_000
            host = round(glove * 1.00002 + 5_000 + (index % 3) * 50)
            model.add(glove, host)
        self.assertTrue(model.ready)
        self.assertAlmostEqual(model.scale, 1.00002, places=4)
        self.assertAlmostEqual(model.glove_to_host(2_000_000), 2_005_040, delta=300)

    def test_nearest_frame(self) -> None:
        records = [ImuRecord(0, value, index, 0, True, 90, 0) for index, value in enumerate((0, 10_000, 20_000))]
        record, delta = nearest_alignment(records, 1.0, 1_000.0, 12_000)
        self.assertEqual(record.seq, 1)
        self.assertEqual(delta, -1.0)


if __name__ == "__main__":
    unittest.main()

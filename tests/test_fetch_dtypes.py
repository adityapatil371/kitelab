"""The `ts` column's dtype, which is contagious when it is wrong.

A real backfill on 2026-09-23 skipped dozens of symbols -- HAL and IRFC among
them -- with

    ValueError: You are trying to merge on datetime64[us] and object columns

The cause was `pd.DataFrame(columns=COLUMNS)`, which gives every column object
dtype. `_pull` returned that for a range Kite had no candles in, concatenated
it with a real chunk, and pandas kept the whole column as object. `rebased`
then refused to merge and the symbol was skipped with its history left stale.

The part that made it persistent rather than annoying: the same untyped blank
was `existing` when no file was on disk, so an object `ts` reached to_parquet.
The bad dtype was then IN THE FILE and came back on every later run, which is
why the fix reads as well as writes (`with_ts`).
"""
import unittest

import pandas as pd

from kitelab import fetch


class BlankFrames(unittest.TestCase):

    def test_a_blank_frame_carries_real_dtypes(self):
        blank = fetch.blank()
        self.assertTrue(blank.empty)
        self.assertEqual(list(blank.columns), fetch.COLUMNS)
        self.assertEqual(blank["ts"].dtype.kind, "M")

    def test_the_old_untyped_blank_is_what_went_wrong(self):
        """Not a test of our code -- a record of the behaviour being guarded."""
        untyped = pd.DataFrame(columns=fetch.COLUMNS)
        self.assertEqual(untyped["ts"].dtype.kind, "O")

    def test_concatenating_a_blank_leaves_the_column_a_datetime(self):
        real = pd.DataFrame({"ts": pd.to_datetime(["2026-09-01"]),
                             "open": 1.0, "high": 1.0, "low": 1.0,
                             "close": 1.0, "volume": 1})
        joined = pd.concat([fetch.blank(), real], ignore_index=True)
        self.assertEqual(joined["ts"].dtype.kind, "M")

    def test_with_ts_repairs_a_file_already_written_badly(self):
        stale = pd.DataFrame({"ts": ["2026-09-01", "2026-09-02"],
                              "close": [1.0, 2.0]})
        self.assertEqual(stale["ts"].dtype.kind, "O")
        self.assertEqual(fetch.with_ts(stale)["ts"].dtype.kind, "M")

    def test_with_ts_leaves_an_empty_frame_alone(self):
        blank = fetch.with_ts(fetch.blank())
        self.assertTrue(blank.empty)
        self.assertEqual(blank["ts"].dtype.kind, "M")


class RebasedSurvivesAMixedPair(unittest.TestCase):
    """`rebased` is where the ValueError was actually raised."""

    def frame(self, stamps, closes, as_text=False):
        return pd.DataFrame({"ts": stamps if as_text else pd.to_datetime(stamps),
                             "close": closes})

    def test_an_object_stored_column_no_longer_raises(self):
        stored = self.frame(["2026-09-01", "2026-09-02"], [100.0, 101.0],
                            as_text=True)
        served = self.frame(["2026-09-01", "2026-09-02"], [100.0, 101.0])
        self.assertIsNone(fetch.rebased(stored, served))

    def test_a_real_rebase_is_still_caught_through_the_repair(self):
        stored = self.frame(["2026-09-01", "2026-09-02"], [7488.5, 7500.0],
                            as_text=True)
        served = self.frame(["2026-09-01", "2026-09-02"], [7627.0, 7640.0])
        why = fetch.rebased(stored, served)
        self.assertIsNotNone(why)
        self.assertIn("re-based", why)

    def test_an_empty_side_is_still_none(self):
        served = self.frame(["2026-09-01"], [100.0])
        self.assertIsNone(fetch.rebased(fetch.blank(), served))
        self.assertIsNone(fetch.rebased(served, fetch.blank()))


if __name__ == "__main__":
    unittest.main()

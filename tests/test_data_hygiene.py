"""frames cleaning -- what every strategy is allowed to see.

These repairs decide the input to everything else, and each was written after a
specific bad file was found trading. Untested until 2026-09-03, which meant the
only evidence they still worked was that nobody had noticed otherwise.
"""
import unittest

import pandas as pd

from kitelab import frames
from tests.support import bars


class ExpectedBars(unittest.TestCase):
    """NSE introduced a closing auction on 2026-08-03: continuous trading ends
    at 15:15 rather than 15:30, so a full session is 24 fifteen-minute bars
    instead of 25. A single constant would flag every session on one side."""

    def test_a_session_before_the_auction_expects_more_bars(self):
        self.assertGreater(frames.expected_bars("2026-07-01"),
                           frames.expected_bars("2026-09-01"))

    def test_the_rule_switches_on_the_auction_date_itself(self):
        self.assertEqual(frames.expected_bars("2026-08-03"),
                         frames.expected_bars("2026-12-01"))
        self.assertNotEqual(frames.expected_bars("2026-08-02"),
                            frames.expected_bars("2026-08-03"))


class Containment(unittest.TestCase):
    """A bar's high and low must contain its own open and close. Kite delivers
    bars that violate this."""

    def test_a_close_above_the_high_is_repaired(self):
        frame = bars([(100, 105, 95, 100), (100, 101, 99, 120)])
        got = frames.enforce_containment(frame, "X", "day")
        row = got.iloc[1]
        self.assertGreaterEqual(row["high"], row["close"])
        self.assertLessEqual(row["low"], row["close"])

    def test_a_low_above_the_open_is_repaired(self):
        frame = bars([(100, 105, 95, 100), (90, 101, 95, 99)])
        got = frames.enforce_containment(frame, "X", "day")
        row = got.iloc[1]
        self.assertLessEqual(row["low"], row["open"])

    def test_well_formed_bars_are_left_exactly_alone(self):
        frame = bars([(100, 105, 95, 100)] * 5)
        got = frames.enforce_containment(frame.copy(), "X", "day")
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      frame.reset_index(drop=True))


class UntradedOutliers(unittest.TestCase):
    """Zero-volume bars carrying a price nothing traded at. VINEETLAB held six
    reading 7.60 amid a Rs1,500 tape and they cost a reference account a
    manufactured Rs43,200 loss."""

    def test_a_zero_volume_bar_far_off_the_tape_is_dropped(self):
        rows = [(1500, 1510, 1490, 1500)] * 30 + [(7.6, 7.6, 7.6, 7.6)]
        frame = bars(rows)
        frame.loc[frame.index[-1], "volume"] = 0
        got = frames.drop_untraded_outliers(frame, "X", "day")
        self.assertLess(len(got), len(frame))

    def test_a_zero_volume_bar_at_a_sane_price_is_kept(self):
        """Illiquidity is not corruption: a stock can simply not trade."""
        rows = [(1500, 1510, 1490, 1500)] * 30 + [(1500, 1500, 1500, 1500)]
        frame = bars(rows)
        frame.loc[frame.index[-1], "volume"] = 0
        got = frames.drop_untraded_outliers(frame, "X", "day")
        self.assertEqual(len(got), len(frame))

    def test_a_traded_bar_is_never_dropped_however_extreme(self):
        """A real move on real volume is data, not an error -- HAL's 2018 move
        travelled intraday on 53x volume and would be lost by a naive filter."""
        rows = [(100, 101, 99, 100)] * 30 + [(100, 200, 100, 195)]
        frame = bars(rows)
        got = frames.drop_untraded_outliers(frame, "X", "day")
        self.assertEqual(len(got), len(frame))


class Sanitise(unittest.TestCase):
    def test_non_positive_prices_do_not_survive(self):
        frame = bars([(100, 105, 95, 100), (0, 0, 0, 0), (100, 105, 95, 100)])
        got = frames.sanitise(frame, "X", "day")
        for col in ("open", "high", "low", "close"):
            self.assertTrue(bool((got[col] > 0).all()), f"{col} kept a non-positive")

    def test_clean_input_survives_unchanged_in_length(self):
        frame = bars([(100, 105, 95, 100)] * 20)
        self.assertEqual(len(frames.sanitise(frame, "X", "day")), 20)

    def test_the_result_always_contains_its_own_open_and_close(self):
        frame = bars([(100, 101, 99, 120), (90, 101, 95, 99), (100, 105, 95, 100)])
        got = frames.sanitise(frame, "X", "day")
        self.assertTrue(bool((got["high"] >= got[["open", "close"]].max(axis=1)).all()))
        self.assertTrue(bool((got["low"] <= got[["open", "close"]].min(axis=1)).all()))


if __name__ == "__main__":
    unittest.main()

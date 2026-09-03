"""No result may depend on a bar that had not happened yet.

This is the discipline the project is built on, and it was the one thing with no
test. Each seam below is a place where reading one bar too early would silently
turn a backtest into a forecast of its own answer.
"""
import unittest

import numpy as np
import pandas as pd

from kitelab import indicators, levels
from kitelab.holygrail import _confirmed


class ConfirmedPivots(unittest.TestCase):
    """A pivot needs `span` bars on BOTH sides, so it is invisible until span
    bars after it forms. Reading it the moment it prints is reading the future."""

    def test_a_pivot_is_invisible_until_span_bars_later(self):
        pivots = [10]
        self.assertIsNone(_confirmed(pivots, span=5, before=14))
        self.assertEqual(_confirmed(pivots, span=5, before=15), 10)

    def test_the_latest_confirmed_pivot_is_returned_not_the_latest_pivot(self):
        # 40 has happened but is not yet confirmed at bar 42; 10 is.
        self.assertEqual(_confirmed([10, 40], span=5, before=42), 10)

    def test_no_pivot_at_all_is_none_rather_than_an_error(self):
        self.assertIsNone(_confirmed([], span=5, before=100))

    def test_confirmation_never_returns_a_future_pivot_at_any_bar(self):
        pivots = [3, 11, 27, 44]
        for before in range(0, 60):
            got = _confirmed(pivots, span=5, before=before)
            if got is not None:
                self.assertLessEqual(got + 5, before)


class FormingBarEma(unittest.TestCase):
    """A higher-timeframe EMA read mid-period must use the FORMING bar, which is
    one recursive step from the last completed one:

        ema_asof(D) = alpha*close(D) + (1-alpha)*ema(last completed bar)

    Using the finished bar would hand Wednesday its Friday value."""

    def test_the_forming_step_matches_the_recursion(self):
        closes = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        length = 3
        alpha = 2 / (length + 1)
        completed = indicators.ema(closes[:4], length).iloc[-1]
        forming = alpha * closes.iloc[4] + (1 - alpha) * completed
        self.assertAlmostEqual(forming, indicators.ema(closes, length).iloc[-1], places=10)

    def test_ema_never_uses_a_later_bar(self):
        """Changing a future close must not move an earlier EMA value."""
        base = pd.Series(np.linspace(10, 20, 40))
        tampered = base.copy()
        tampered.iloc[30:] = 999.0
        a = indicators.ema(base, 10).iloc[:30]
        b = indicators.ema(tampered, 10).iloc[:30]
        pd.testing.assert_series_equal(a, b)


class LevelValidity(unittest.TestCase):
    """A hand-drawn line is not a line until price has touched it twice, so a
    signal before the second touch is hindsight."""

    def _frame(self, lows):
        n = len(lows)
        return pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="D"),
                             "open": lows, "high": [x * 1.02 for x in lows],
                             "low": lows, "close": lows,
                             "volume": [1000] * n})

    def test_one_touch_does_not_establish_a_line(self):
        frame = self._frame([100.0, 105.0, 110.0, 115.0])
        self.assertIsNone(levels.valid_from(frame, 100.0))

    def test_two_visits_too_close_together_count_as_one_event(self):
        # TOUCH_SEPARATION_BARS: price wobbling on the line for three bars is
        # one visit, not three, or every line would validate on its first day.
        frame = self._frame([100.0, 100.0, 100.0, 130.0])
        self.assertIsNone(levels.valid_from(frame, 100.0))

    def test_validity_starts_at_the_second_touch_not_the_first(self):
        away = [130.0] * levels.TOUCH_SEPARATION_BARS
        prices = [100.0] + away + [100.0] + away
        frame = self._frame(prices)
        got = levels.valid_from(frame, 100.0)
        self.assertIsNotNone(got)
        second = len(away) + 1
        self.assertEqual(got, frame.iloc[second]["ts"])


if __name__ == "__main__":
    unittest.main()

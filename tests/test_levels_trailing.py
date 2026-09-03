"""Levels, pivots, trailing stops, and position sizing.

The pivot functions are where lookahead would be easiest to introduce and
hardest to notice: a swing low is only knowable `span` bars after it forms.
"""
import unittest

import pandas as pd

from kitelab import levels, sizing, trailing


def frame(lows, highs=None):
    n = len(lows)
    highs = highs or [x * 1.05 for x in lows]
    return pd.DataFrame({
        "ts": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": lows, "high": highs, "low": lows, "close": lows,
        "volume": [1000] * n})


class Pivots(unittest.TestCase):
    def test_a_low_with_higher_lows_on_both_sides_is_a_pivot(self):
        lows = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110]
        got = levels.pivot_lows(frame(lows), span=5)
        self.assertIn(5, got)

    def test_a_low_at_the_very_edge_cannot_be_one(self):
        """There are not `span` bars on both sides, so nothing there is
        confirmable -- and a pivot at the last bar would be the future."""
        lows = [100, 102, 104, 106, 108, 110]
        for i in levels.pivot_lows(frame(lows), span=5):
            self.assertGreaterEqual(i, 5)
            self.assertLess(i, len(lows) - 5)

    def test_pivot_highs_are_the_mirror(self):
        highs = [100, 102, 104, 106, 108, 120, 108, 106, 104, 102, 100]
        got = levels.pivot_highs(frame([h * 0.9 for h in highs], highs), span=5)
        self.assertIn(5, got)

    def test_a_flat_stretch_produces_no_pivot(self):
        """Ties are not pivots: with equal lows on both sides there is no turn,
        and admitting them would scatter false swing points through any range."""
        self.assertEqual(levels.pivot_lows(frame([100.0] * 30), span=5), [])


class Snap(unittest.TestCase):
    def test_a_price_is_snapped_to_the_tick(self):
        # Compared as a multiple, not with %: 123.45 % 0.05 is 0.0499... in
        # binary floating point, so the modulo tests the arithmetic, not snap.
        got = levels.snap(123.4567)
        self.assertAlmostEqual(got / levels.NSE_TICK,
                               round(got / levels.NSE_TICK), places=6)

    def test_snapping_moves_a_price_only_slightly(self):
        self.assertLess(abs(levels.snap(123.4567) - 123.4567), 0.05)


class TrailingStop(unittest.TestCase):
    def test_a_trail_never_moves_down(self):
        """The property that makes it a trailing stop rather than just a stop.
        If it could fall, a drifting price would widen risk after entry."""
        lows = [100, 95, 105, 92, 110, 90, 120, 88, 130, 95, 140]
        f = frame(lows)
        pivots = trailing.pivot_lows(f, span=1)
        step = trailing.intraday_trail(f, pivots, span=1)()
        stop = 80.0
        for i in range(len(lows)):
            moved = step(i, stop, float(lows[i]))
            self.assertGreaterEqual(moved, stop, "the trail moved DOWN")
            stop = moved

    def test_a_raise_never_lands_at_or_above_the_bar_that_triggered_it(self):
        """Otherwise the stop would fire on the very bar that set it.

        Checked only WHERE THE STOP MOVES. Afterwards the invariant is not the
        trail's to keep: a later bar trading below the stop is a stop-out, which
        trailing.resolve handles, not a trail that misbehaved.
        """
        lows = [100, 90, 100, 80, 100, 70, 100]
        f = frame(lows)
        step = trailing.intraday_trail(f, trailing.pivot_lows(f, span=1), span=1)()
        stop, raises = 50.0, 0
        for i in range(len(lows)):
            moved = step(i, stop, float(lows[i]))
            if moved != stop:
                raises += 1
                self.assertLess(moved, float(lows[i]),
                                "the trail landed on or above its own bar")
            stop = moved
        self.assertGreater(raises, 0, "the fixture never exercised a raise")

    def test_pivot_lows_here_agree_with_the_levels_module(self):
        """Two implementations of the same idea in one repo is how they drift.
        If these ever disagree, one of them is wrong."""
        lows = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110]
        self.assertEqual(trailing.pivot_lows(frame(lows), span=5),
                         levels.pivot_lows(frame(lows), span=5))


class Sizing(unittest.TestCase):
    def test_shares_are_the_risk_budget_over_the_stop_distance(self):
        shares, risk_taken, _ = sizing.position(100.0, 90.0)
        self.assertEqual(shares, int(sizing.risk_budget() / 10.0))
        self.assertAlmostEqual(risk_taken, shares * 10.0)

    def test_a_tighter_stop_buys_more_shares(self):
        near, _, _ = sizing.position(100.0, 99.0)
        far, _, _ = sizing.position(100.0, 50.0)
        self.assertGreater(near, far)

    def test_the_capital_cap_binds_before_risk_on_a_very_tight_stop(self):
        """Sizing by risk alone lets a tight stop buy an unbounded position --
        one support-bounce trade once took Rs2,88,500 of stock to risk Rs500."""
        shares, _, capped = sizing.position(100.0, 99.999)
        self.assertTrue(capped)
        self.assertLessEqual(shares * 100.0, sizing.CAPITAL * 1.0001)

    def test_a_stop_at_or_above_entry_buys_nothing(self):
        shares, _, _ = sizing.position(100.0, 100.0)
        self.assertLessEqual(shares, 0)


if __name__ == "__main__":
    unittest.main()

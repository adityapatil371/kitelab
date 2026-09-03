"""Resampling, checked by independent aggregation.

Zerodha serves no weekly or monthly candles at all -- these are computed here,
so nothing external validates them. scripts/verify.py exists as a second
implementation, but it is a manual command over the real data store and has
never run automatically. This does the same job hermetically: build known daily
bars, group them, and check every derived bar against the bars it should come
from.

    open   == first source open        high   == max source high
    low    == min source low           close  == last source close
    volume == sum source volume
"""
import unittest

import numpy as np
import pandas as pd

from kitelab import frames


def daily(n, start="2020-01-06"):          # a Monday
    rng = np.random.default_rng(3)
    base = 100 + rng.normal(0, 2, n).cumsum()
    stamps = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame({
        "ts": stamps,
        "open": base, "high": base + 3, "low": base - 3, "close": base + 1,
        "volume": rng.integers(1_000, 9_000, n).astype(float)})


class Aggregation(unittest.TestCase):
    """Every derived bar re-derived a second way, from the source rows it covers."""

    def _check(self, source, derived, key):
        groups = source.groupby(key, sort=True)
        self.assertEqual(len(derived), groups.ngroups)
        for (_, rows), (_, got) in zip(groups, derived.iterrows()):
            self.assertAlmostEqual(got["open"], rows["open"].iloc[0], places=9)
            self.assertAlmostEqual(got["close"], rows["close"].iloc[-1], places=9)
            self.assertAlmostEqual(got["high"], rows["high"].max(), places=9)
            self.assertAlmostEqual(got["low"], rows["low"].min(), places=9)
            self.assertAlmostEqual(got["volume"], rows["volume"].sum(), places=6)

    def test_weekly_bars_aggregate_their_own_days(self):
        day = daily(120)
        iso = day["ts"].dt.isocalendar()
        key = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
        self._check(day, frames.weekly(day).reset_index(drop=True), key)

    def test_monthly_bars_aggregate_their_own_days(self):
        day = daily(400)
        self._check(day, frames.monthly(day).reset_index(drop=True),
                    day["ts"].dt.to_period("M"))

    def test_quarterly_bars_aggregate_their_own_days(self):
        day = daily(800)
        self._check(day, frames.quarterly(day).reset_index(drop=True),
                    day["ts"].dt.to_period("Q"))

    def test_a_derived_bar_never_contains_a_future_day(self):
        """The aggregation must close each bar at its own period end. A weekly
        bar that reached into the next week would hand Monday its Friday."""
        day = daily(200)
        week = frames.weekly(day).reset_index(drop=True)
        self.assertTrue(bool(week["ts"].is_monotonic_increasing))
        self.assertLessEqual(week["ts"].iloc[-1], day["ts"].iloc[-1])

    def test_coarser_timeframes_hold_fewer_bars(self):
        day = daily(800)
        self.assertGreater(len(day), len(frames.weekly(day)))
        self.assertGreater(len(frames.weekly(day)), len(frames.monthly(day)))
        self.assertGreater(len(frames.monthly(day)), len(frames.quarterly(day)))

    def test_total_volume_is_conserved(self):
        """Nothing may be dropped or double counted in the grouping."""
        day = daily(400)
        for coarse in (frames.weekly, frames.monthly, frames.quarterly):
            self.assertAlmostEqual(coarse(day)["volume"].sum(),
                                   day["volume"].sum(), places=4)


class Degenerate(unittest.TestCase):
    def test_a_single_day_makes_a_single_bar_of_every_period(self):
        day = daily(1)
        for coarse in (frames.weekly, frames.monthly, frames.quarterly):
            self.assertEqual(len(coarse(day)), 1)

    def test_an_empty_frame_produces_an_empty_frame_not_an_error(self):
        empty = daily(0)
        for coarse in (frames.weekly, frames.monthly, frames.quarterly):
            self.assertEqual(len(coarse(empty)), 0)


if __name__ == "__main__":
    unittest.main()

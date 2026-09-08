"""The all-time-high filter is a FILTER, not an entry (2026-09-07).

Until this date both producers -- backtest.ema_stack_signal and
timeframes.stack_signal -- AND-ed `near_ath` into entry_ok BEFORE the walk's
rising-edge test `entry_ok[p] and not entry_ok[p-1]`. So when the stack was
already up and price merely climbed back within 10% of its high, entry_ok
flipped false-to-true and the walk saw a "fresh" signal with no EMA cross under
it. Measured on 250 stocks from 2018, the M/W ATH rows took 59% of their trades
and 71% of their net from such cross-less entries; Q/M 83% and 73%. A filter
that manufactures entries of its own cannot be read against its unfiltered
twin, which was the whole reason the twin was on the board.

Two synthetic daily tapes, fed through the real signal generators via
tests.support.daily_bars:

  DIP     the stack turns true once and stays true; a one-day spike early on
          sets a record far above the tape, so the first (and only) cross is
          declined, price then climbs back inside the band with the stack
          never breaking. The old code entered THERE. The filtered rule must
          take nothing on this tape.
  CROSSES three genuine crosses, two of them far below the record and one
          inside the band. The filtered rule's trade list must be a subset of
          the unfiltered rule's -- same entry stamps -- and strictly smaller.

A third test drives backtest.simulate's walk directly on a hand-built signal
frame (tests.support.signals_from) so the mechanism is pinned independently of
any EMA arithmetic: near_ath switching on inside an unbroken stack is not a
signal.
"""
import unittest

import numpy as np

from kitelab import backtest, timeframes
from tests.support import account, bars, daily_bars, signal_frame, signals_from

ATH = 0.10


def _leg(rows, to, n):
    """Straight line from the last close to `to` over n bars; tight ranges so
    the entry low sits just under the close and sizing always finds shares."""
    start = rows[-1][3]
    for i in range(1, n + 1):
        c = start + (to - start) * i / n
        rows.append((c - 0.2, c + 0.3, c - 0.5, c))


def dip_tape():
    """Stack up from the first bar of the second month to bar 199, record set
    at 120 by a spike on bar 3 while the stack was still unformed. Price sits
    ~25% below the record when the stack turns true and climbs back inside
    the 10% band around bar 180 with no cross in between; then it breaks."""
    rows = [(89.8, 90.3, 89.5, 90.0)]
    _leg(rows, 90 + 0.1 * 199, 199)
    rows[3] = (90.3, 121.0, 90.0, 120.0)
    _leg(rows, rows[-1][3] - 39, 39)
    return rows


def crosses_tape():
    """Record 130 from a spike on bar 3. Crosses near bars 31 (~93, declined),
    256 (~98, declined) and 344 (~121, inside the band), each followed by a
    break so every trade closes."""
    rows = [(89.8, 90.3, 89.5, 90.0)]
    _leg(rows, 110, 200)
    rows[3] = (90.3, 131.0, 90.0, 130.0)
    _leg(rows, 85, 25)
    _leg(rows, 128, 100)
    _leg(rows, 118, 10)
    _leg(rows, 140, 60)
    _leg(rows, 100, 40)
    return rows


def stamps(trades):
    return sorted(t["entry_ts"] for t in trades)


class SignalColumns(unittest.TestCase):
    """entry_ok is the bare stack whatever the band asks; the filter is its own
    column, so nothing downstream can mistake it for an edge."""

    def test_backtest_entry_ok_is_untouched_by_the_band(self):
        with daily_bars(bars(crosses_tape())):
            plain = backtest.ema_stack_signal("X")
            near = backtest.ema_stack_signal("X", ath_band=ATH)
        np.testing.assert_array_equal(plain["entry_ok"].to_numpy(), near["entry_ok"].to_numpy())
        self.assertTrue(bool(plain["near_ath"].all()), "no band means no bar is refused")
        self.assertFalse(bool(near["near_ath"].all()))

    def test_timeframes_entry_ok_is_untouched_by_the_band(self):
        with daily_bars(bars(crosses_tape())):
            base, highers = timeframes._stack_frames("X", "WD")
            plain = timeframes.stack_signal(base, highers)
            near = timeframes.stack_signal(base, highers, ath_band=ATH)
        np.testing.assert_array_equal(plain["entry_ok"].to_numpy(), near["entry_ok"].to_numpy())
        self.assertTrue(bool(plain["near_ath"].all()))
        self.assertFalse(bool(near["near_ath"].all()))

    def test_the_dip_tape_has_exactly_one_cross_and_it_is_declined(self):
        """The fixture is only a test of the filter if the stack really does
        stay up through the dip. Pinned here so a change to the EMA maths
        that quietly added a cross would fail loudly rather than pass."""
        with daily_bars(bars(dip_tape())):
            sig = backtest.ema_stack_signal("X", ath_band=ATH)
        e = sig["entry_ok"].to_numpy()
        n = sig["near_ath"].to_numpy()
        edges = [i for i in range(1, len(e)) if e[i] and not e[i - 1]]
        self.assertEqual(len(edges), 1)
        self.assertFalse(bool(n[edges[0]]), "the one cross must be outside the band")
        back_in = next(i for i in range(edges[0], len(n)) if n[i])
        self.assertTrue(bool(e[back_in:200].all()), "the stack must hold while price re-enters the band")


class NoEntryWithoutACross(unittest.TestCase):
    """The dip tape. The unfiltered rule trades its one cross; the filtered
    rule, having declined that cross, must not buy the recovery."""

    def test_backtest_takes_nothing_on_the_dip(self):
        with account(), daily_bars(bars(dip_tape())):
            plain = backtest.simulate("X")
            near = backtest.simulate("X", ath_band=ATH)
        self.assertEqual(len(plain), 1)
        self.assertEqual(near, [])

    def test_timeframes_takes_nothing_on_the_dip(self):
        for variant in ("WD", "MWD"):
            with self.subTest(variant=variant), account(), daily_bars(bars(dip_tape())):
                plain = timeframes.simulate_variant("X", variant)
                near = timeframes.simulate_variant("X", variant, ath_band=ATH)
            self.assertEqual(len(plain), 1)
            self.assertEqual(near, [])

    def test_the_walk_itself_ignores_the_filter_switching_on(self):
        """Mechanism, not arithmetic: entry_ok true from bar 1 to the end,
        near_ath false until bar 4 and true after. The old code saw a fresh
        signal on bar 4; there is no cross there."""
        rows = [(100.0, 105.0, 95.0, 100.0)] * 8
        frame = signal_frame(rows, entries=set(range(1, 8)), exits={7})
        frame["near_ath"] = [False] * 4 + [True] * 4
        with account(), signals_from(frame):
            self.assertEqual(backtest.simulate("X", ath_band=ATH), [])
        frame["near_ath"] = [True] * 8
        with account(), signals_from(frame):
            self.assertEqual(len(backtest.simulate("X", ath_band=ATH)), 1)


class FilteredIsASubset(unittest.TestCase):
    """The crosses tape. Every ATH trade is a trade the unfiltered twin also
    takes -- same entry stamp -- and the filter declines at least one."""

    def test_backtest(self):
        with account(), daily_bars(bars(crosses_tape())):
            plain = backtest.simulate("X")
            near = backtest.simulate("X", ath_band=ATH)
        self.assertTrue(near, "the fixture must leave the filter something to accept")
        self.assertLess(len(near), len(plain), "the fixture must give the filter something to decline")
        self.assertTrue(set(stamps(near)) <= set(stamps(plain)))
        by_stamp = {t["entry_ts"]: t for t in plain}
        for t in near:
            twin = by_stamp[t["entry_ts"]]
            self.assertEqual((t["exit_ts"], t["entry_price"], t["stop"]),
                             (twin["exit_ts"], twin["entry_price"], twin["stop"]))

    def test_timeframes(self):
        for variant in ("WD", "MWD"):
            with self.subTest(variant=variant), account(), daily_bars(bars(crosses_tape())):
                plain = timeframes.simulate_variant("X", variant)
                near = timeframes.simulate_variant("X", variant, ath_band=ATH)
            self.assertTrue(near)
            self.assertLess(len(near), len(plain))
            self.assertTrue(set(stamps(near)) <= set(stamps(plain)))

    def test_a_zero_band_is_not_a_missing_band(self):
        """ath_band=0.0 admits only bars AT the record. Still a subset, and
        still not the same list as no band at all."""
        with account(), daily_bars(bars(crosses_tape())):
            plain = backtest.simulate("X")
            at_record = backtest.simulate("X", ath_band=0.0)
        self.assertTrue(set(stamps(at_record)) <= set(stamps(plain)))
        self.assertLess(len(at_record), len(plain))


if __name__ == "__main__":
    unittest.main()

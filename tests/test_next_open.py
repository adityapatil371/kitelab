"""backtest.NEXT_OPEN_FILLS, across all four producers.

WHY THIS FILE EXISTS (2026-09-09). The flag shipped in kitelab/backtest.py with
no test of any kind, and covered only the 3 rows that module builds; the other
16 on the board had no next-open path at all, so the fill-timing lookahead could
not be measured for them and they could not be paper-traded honestly. The path
now exists in timeframes.py, darvas.py and holygrail.py too, all reading the one
flag through the backtest module, and this is the test of the four of them.

The three things worth failing on, and none of them is the arithmetic:

  1. The stop LEVEL does not move -- it is the line drawn on the chart -- but a
     signal whose next open is already at or below it is DROPPED, not filled.
  2. The trade STAMP moves with the fill, which only bites on the aggregated
     bases in timeframes.py: `ts` is a bar's first session and `end_ts` its
     last, so a next-open fill stamped `end_ts` would land four sessions late.
  3. holygrail.py's ENTRY does not move at all, because it never cheated: it is
     a resting buy-stop filled intrabar on a later bar. Only its exits move.

Hermetic like the rest of tests/: hand-built bars, no price files, no network.
Every case restores the flag, because a leaked True would silently re-price
every test that runs after it.
"""
from __future__ import annotations

import contextlib
import unittest

import pandas as pd

from kitelab import backtest, darvas, holygrail, timeframes
from tests.support import account, bars, daily_bars, signal_frame, signals_from

FLAT = (100.0, 101.0, 99.0, 100.0)      # open, high, low, close

# One shared price path, used by backtest, timeframes and darvas so the three
# engines can be read against each other. Bar 1 signals; under the close
# convention it fills at 108 and stops out at bar 3's close of 85. Under
# next-open fills it fills at bar 2's open of 104 and the same stop signal at
# bar 3 fills at bar 4's open of 100.
PATH = [FLAT,
        (100.0, 110.0, 90.0, 108.0),    # 1: the signal bar. stop = its low, 90
        (104.0, 106.0, 96.0, 100.0),    # 2: opens at 104, above the stop
        (99.0, 101.0, 80.0, 85.0),      # 3: closes below the stop
        FLAT,                           # 4: opens at 100 -- the next-open exit fill
        FLAT]

# The same path except bar 2 opens at 89, BELOW the 90 stop.
PATH_GAPPED = [FLAT,
               (100.0, 110.0, 90.0, 108.0),
               (89.0, 95.0, 85.0, 90.0),
               (95.0, 96.0, 80.0, 85.0),
               FLAT]


@contextlib.contextmanager
def next_open(on: bool = True):
    """Set the flag and always put it back."""
    saved = backtest.NEXT_OPEN_FILLS
    backtest.NEXT_OPEN_FILLS = on
    try:
        yield
    finally:
        backtest.NEXT_OPEN_FILLS = saved


class TheDefault(unittest.TestCase):
    def test_the_flag_is_off_as_shipped(self):
        """Every stored number on the board assumes it. A commit that flips it
        would re-price 10,260 cells silently."""
        self.assertIs(backtest.NEXT_OPEN_FILLS, False)


# ------------------------------------------------------------- backtest.py ---

def run_backtest(rows, entries, exits, **kw):
    with account(), signals_from(signal_frame(rows, entries, exits)):
        return backtest.simulate("X", **kw)


class Backtest(unittest.TestCase):
    def test_close_convention_fills_at_the_signal_close(self):
        t = run_backtest(PATH, entries={1}, exits=set())
        self.assertEqual((t[0]["entry_price"], t[0]["exit_price"]), (108.0, 85.0))

    def test_next_open_fills_entry_and_exit_at_the_following_open(self):
        with next_open():
            t = run_backtest(PATH, entries={1}, exits=set())
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["entry_price"], t[0]["exit_price"]), (104.0, 100.0))

    def test_the_stop_level_does_not_move(self):
        with next_open():
            t = run_backtest(PATH, entries={1}, exits=set())
        self.assertEqual(t[0]["stop"], 90.0)

    def test_a_signal_whose_next_open_is_below_the_stop_is_dropped(self):
        with next_open():
            self.assertEqual(run_backtest(PATH_GAPPED, entries={1}, exits=set()), [])

    def test_scale_out_is_refused(self):
        with next_open(), self.assertRaises(ValueError):
            run_backtest(PATH, entries={1}, exits=set(), scale_out="half")

    def test_an_intrabar_stop_is_refused(self):
        with next_open(), self.assertRaises(ValueError):
            run_backtest(PATH, entries={1}, exits=set(), stop_on_close=False)


# ----------------------------------------------------------- timeframes.py ---

def tf_frame(rows, entries, exits, aggregated=False):
    """A signal frame in the shape stack_signal returns.

    aggregated=True fakes a WEEKLY base: each bar spans Monday to Friday, so
    `ts` and `end_ts` differ by four sessions and the stamping rule can be seen.
    """
    frame = bars(rows)
    n = len(rows)
    frame["entry_ok"] = [i in entries for i in range(n)]
    frame["exit_ok"] = [i in exits for i in range(n)]
    frame["near_ath"] = [True] * n
    frame["top_tf_done"] = list(range(n))
    if aggregated:
        frame["ts"] = pd.date_range("2020-01-06", periods=n, freq="7D")   # Mondays
        frame["end_ts"] = frame["ts"] + pd.Timedelta(days=4)              # Fridays
    return frame


@contextlib.contextmanager
def tf_signals(frame):
    """Run simulate_variant against a hand-built frame, as signals_from does for
    backtest.simulate. Both seams are patched: _stack_frames chooses the base
    and stack_signal derives the conditions, and neither is under test here."""
    saved_frames, saved_signal = timeframes._stack_frames, timeframes.stack_signal
    timeframes._stack_frames = lambda symbol, variant: (frame, [])
    timeframes.stack_signal = lambda base, highers, **kw: frame
    try:
        yield
    finally:
        timeframes._stack_frames = saved_frames
        timeframes.stack_signal = saved_signal


def run_tf(rows, entries, exits, aggregated=False, **kw):
    with account(), tf_signals(tf_frame(rows, entries, exits, aggregated)):
        return timeframes.simulate_variant("X", "MWD", **kw)


class Timeframes(unittest.TestCase):
    def test_close_convention_fills_at_the_signal_close(self):
        t = run_tf(PATH, entries={1}, exits=set())
        self.assertEqual((t[0]["entry_price"], t[0]["exit_price"]), (108.0, 85.0))

    def test_next_open_matches_the_backtest_engine_bar_for_bar(self):
        """The two are separate reimplementations of one walk. If they disagree
        on the same bars, one of them is wrong."""
        with next_open():
            mine = run_tf(PATH, entries={1}, exits=set())
            theirs = run_backtest(PATH, entries={1}, exits=set())
        self.assertEqual((mine[0]["entry_price"], mine[0]["exit_price"]),
                         (theirs[0]["entry_price"], theirs[0]["exit_price"]))
        self.assertEqual(mine[0]["stop"], theirs[0]["stop"])

    def test_a_signal_whose_next_open_is_below_the_stop_is_dropped(self):
        with next_open():
            self.assertEqual(run_tf(PATH_GAPPED, entries={1}, exits=set()), [])

    def test_an_aggregated_close_fill_is_stamped_on_the_bar_s_LAST_session(self):
        t = run_tf(PATH, entries={1}, exits=set(), aggregated=True)
        self.assertEqual(t[0]["entry_ts"], pd.Timestamp("2020-01-17"))   # bar 1 Friday

    def test_an_aggregated_next_open_fill_is_stamped_on_the_bar_s_FIRST_session(self):
        """The trap. Bar 2 runs Mon 20 Jan to Fri 24 Jan and the fill is its
        OPEN, so the trade is dated the Monday. Stamping it end_ts would date it
        the 24th -- four sessions after the cash actually left."""
        with next_open():
            t = run_tf(PATH, entries={1}, exits=set(), aggregated=True)
        self.assertEqual(t[0]["entry_ts"], pd.Timestamp("2020-01-20"))   # bar 2 Monday
        self.assertEqual(t[0]["exit_ts"], pd.Timestamp("2020-02-03"))    # bar 4 Monday

    def test_an_intrabar_stop_is_refused(self):
        with next_open(), self.assertRaises(ValueError):
            run_tf(PATH, entries={1}, exits=set(), stop_on_close=False)


# --------------------------------------------------------------- darvas.py ---

# Bars 0-2 set the 3-candle channel, bar 3 closes above it. From there the path
# is PATH's, so the fills land in the same places one bar later.
DARVAS_PATH = [FLAT, FLAT, FLAT] + PATH[1:]


def run_darvas(rows, **kw):
    with account(), daily_bars(bars(rows)):
        return darvas.simulate("X", 3, 2, weekly=False, **kw)


class Darvas(unittest.TestCase):
    def test_close_convention_fills_at_the_breakout_close(self):
        t = run_darvas(DARVAS_PATH)
        self.assertEqual((t[0]["entry_price"], t[0]["exit_price"]), (108.0, 85.0))

    def test_next_open_fills_entry_and_exit_at_the_following_open(self):
        with next_open():
            t = run_darvas(DARVAS_PATH)
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["entry_price"], t[0]["exit_price"]), (104.0, 100.0))
        self.assertEqual(t[0]["stop"], 90.0)

    def test_a_breakout_whose_next_open_is_below_the_stop_is_dropped(self):
        with next_open():
            self.assertEqual(run_darvas([FLAT, FLAT, FLAT] + PATH_GAPPED[1:]), [])

    def test_the_intrabar_reading_is_refused(self):
        """intrabar=True means resting orders on the exchange. There is no
        end-of-day decision left to defer."""
        with next_open(), self.assertRaises(ValueError):
            run_darvas(DARVAS_PATH, intrabar=True)


# ------------------------------------------------------------ holygrail.py ---

def hg_frame(tail):
    """40 flat bars with a confirmed pivot high, a signal, and a hand-set tail.

    Bar 10 spikes to 130 and is the only pivot high confirmed by the entry bar,
    so the target is 130. Bar 25 is the signal: trigger 105, stop 95. Bar 26
    gaps over the trigger and fills the resting buy-stop at its open of 106.
    """
    rows = [FLAT] * 40
    rows[10] = (100.0, 130.0, 99.0, 100.0)
    rows[25] = (100.0, 105.0, 95.0, 100.0)
    rows[26] = (106.0, 110.0, 100.0, 108.0)
    for offset, row in enumerate(tail):
        rows[27 + offset] = row
    frame = bars(rows)
    frame["signal"] = [i == 25 for i in range(40)]
    return frame


@contextlib.contextmanager
def hg_setups(frame):
    saved = holygrail.setups
    holygrail.setups = lambda *a, **k: frame
    try:
        yield
    finally:
        holygrail.setups = saved


def run_hg(tail):
    frame = hg_frame(tail)
    with account(), daily_bars(frame), hg_setups(frame):
        return holygrail.simulate("X")


# The trade stops out: bar 27 closes below the 95 stop, bar 28 opens at 88.
STOPPED = [(95.0, 96.0, 88.0, 90.0), (88.0, 92.0, 86.0, 91.0)]
# The trade banks half at the target and trails the rest out:
#   27 closes at 132, through the 130 target   -> half banked
#   28 opens at 128                            -> where that half fills next-open
#   29 closes at 88, below the 95 trail        -> the rest exits
#   30 opens at 86                             -> where the rest fills next-open
BANKED = [(100.0, 135.0, 99.0, 132.0), (128.0, 130.0, 120.0, 125.0),
          (90.0, 92.0, 85.0, 88.0), (86.0, 88.0, 84.0, 87.0)]


class HolyGrail(unittest.TestCase):
    def test_the_entry_does_not_move(self):
        """The whole point of this rule's exemption. The entry is a resting
        buy-stop filled intrabar on a bar AFTER the signal, so there is no
        fill-timing lookahead in it to remove -- price and date both hold."""
        plain = run_hg(STOPPED)
        with next_open():
            shifted = run_hg(STOPPED)
        self.assertEqual(len(plain), len(shifted), 1)
        self.assertEqual(plain[0]["entry_price"], shifted[0]["entry_price"])
        self.assertEqual(plain[0]["entry_ts"], shifted[0]["entry_ts"])

    def test_the_exit_moves_to_the_next_open(self):
        plain = run_hg(STOPPED)
        with next_open():
            shifted = run_hg(STOPPED)
        self.assertEqual(plain[0]["exit_price"], 90.0)          # bar 27's close
        self.assertEqual(shifted[0]["exit_price"], 88.0)        # bar 28's open
        self.assertEqual(shifted[0]["exit_ts"] - plain[0]["exit_ts"],
                         pd.Timedelta(days=1))

    def test_the_banked_half_fills_at_its_own_next_open(self):
        """Two sells, two fills, each one session after its own decision:
        close convention 0.5 x 132 + 0.5 x 88 = 110; next-open 0.5 x 128 +
        0.5 x 86 = 107. A version that moved only the final exit would say 109."""
        plain = run_hg(BANKED)
        with next_open():
            shifted = run_hg(BANKED)
        self.assertAlmostEqual(plain[0]["exit_price"], 110.0)
        self.assertAlmostEqual(shifted[0]["exit_price"], 107.0)


if __name__ == "__main__":
    unittest.main()

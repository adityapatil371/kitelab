"""Differential test: our bar-walker against an independent implementation.

Every other test here compares kitelab against MY expectations. This compares it
against someone else's engine -- backtesting.py, written by different people to
different conventions -- on identical bars. It is the only test in the project
that could catch a mistake I made consistently in both the code and its tests.

The project already uses this pattern twice: scripts/verify.py is a second
implementation of resampling, and backtest.verify_against_screener cross-checks
the fast signal path against the screener. This extends it to the exit walker,
which is the piece with no independent check at all.

WHAT IS AND IS NOT COMPARED. Trade PRICES -- which bar, at what level -- not
account equity. The two engines size positions completely differently (ours is
risk-based and cash-constrained; theirs buys with available equity), so equal
final balances would mean nothing. Where they must agree is on the mechanics:
given the same bars and the same decision points, both should enter and exit at
the same prices. That is the part with no other witness.

SKIPPED, NOT FAILED, when backtesting.py is absent -- it is a dev dependency and
the project must stay installable without it.
"""
import unittest

import numpy as np
import pandas as pd

from kitelab import backtest
from tests.support import account, signal_frame, signals_from

try:
    from backtesting import Backtest, Strategy
    HAVE_ORACLE = True
except ImportError:                                   # pragma: no cover
    HAVE_ORACLE = False


def ohlc(rows, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(rows), freq="D")
    return pd.DataFrame({"Open": [r[0] for r in rows], "High": [r[1] for r in rows],
                         "Low": [r[2] for r in rows], "Close": [r[3] for r in rows],
                         "Volume": [10_000] * len(rows)}, index=idx)


BARS = [(100, 105, 95, 100),
        (100, 112, 90, 110),
        (110, 130, 108, 128),
        (128, 140, 120, 138),
        (138, 142, 118, 121),
        (121, 125, 115, 119),
        (119, 122, 112, 116)]
ENTRY_BAR, EXIT_BAR = 1, 4


@unittest.skipUnless(HAVE_ORACLE, "backtesting.py not installed")
class AgainstBacktestingPy(unittest.TestCase):

    def _theirs(self, bars, entry_bar, exit_bar, **kw):
        class Fixed(Strategy):
            def init(self):
                pass

            def next(self):
                i = len(self.data) - 1
                if i == entry_bar:
                    self.buy()
                elif i == exit_bar and self.position:
                    self.position.close()

        bt = Backtest(ohlc(bars), Fixed, cash=1_000_000,
                      commission=0.0, trade_on_close=True, **kw)
        stats = bt.run()
        return stats["_trades"]

    def _ours(self, bars, entry_bar, exit_bar):
        with account(), signals_from(signal_frame(bars, {entry_bar}, {exit_bar})):
            return backtest.simulate("X")

    def test_both_engines_enter_at_the_same_price(self):
        theirs = self._theirs(BARS, ENTRY_BAR, EXIT_BAR)
        ours = self._ours(BARS, ENTRY_BAR, EXIT_BAR)
        self.assertEqual(len(ours), 1)
        self.assertEqual(len(theirs), 1)
        self.assertAlmostEqual(float(theirs.iloc[0]["EntryPrice"]),
                               ours[0]["entry_price"], places=6)

    def test_both_engines_exit_at_the_same_price(self):
        theirs = self._theirs(BARS, ENTRY_BAR, EXIT_BAR)
        ours = self._ours(BARS, ENTRY_BAR, EXIT_BAR)
        self.assertAlmostEqual(float(theirs.iloc[0]["ExitPrice"]),
                               ours[0]["exit_price"], places=6)

    def test_both_engines_agree_on_the_gross_percentage_move(self):
        """The bottom line of the mechanics: same bars in, same return out,
        whatever each engine then does about position size."""
        theirs = self._theirs(BARS, ENTRY_BAR, EXIT_BAR)
        ours = self._ours(BARS, ENTRY_BAR, EXIT_BAR)[0]
        mine = ours["exit_price"] / ours["entry_price"] - 1.0
        self.assertAlmostEqual(float(theirs.iloc[0]["ReturnPct"]), mine, places=6)

    def _theirs_with_stop(self, bars, entry_bar, exit_bar, stop):
        class Fixed(Strategy):
            def init(self):
                pass

            def next(self):
                i = len(self.data) - 1
                if i == entry_bar:
                    self.buy(sl=stop)
                elif i == exit_bar and self.position:
                    self.position.close()

        bt = Backtest(ohlc(bars), Fixed, cash=1_000_000,
                      commission=0.0, trade_on_close=True)
        return bt.run()["_trades"]

    def test_the_stop_itself_agrees_across_many_random_paths(self):
        """THE TEST THAT MATTERS. Both engines given the same entry, the same
        exit signal and the SAME STOP -- our intrabar convention against theirs.

        The first version of this gave a stop only to our engine, so on any path
        that breached it the two exited for different reasons and the comparison
        was meaningless. Both now carry it, which turns a coincidence check into
        a genuine cross-implementation test of the stop walker: a touch fills at
        the stop, a gap through it fills at the open.
        """
        rng = np.random.default_rng(11)
        compared = 0
        for trial in range(40):
            base = 100 + rng.normal(0, 4, 12).cumsum()
            base = np.maximum(base, 20.0)
            rows = [(float(p), float(p) + 6, float(p) - 6, float(p) + rng.normal(0, 1))
                    for p in base]
            rows = [(o, max(o, h, c), min(o, l, c), c) for o, h, l, c in rows]
            entry_bar, exit_bar = 2, 8
            stop = rows[entry_bar][2]
            if rows[entry_bar][3] <= stop:
                continue                      # our engine refuses this entry
            theirs = self._theirs_with_stop(rows, entry_bar, exit_bar, stop)
            with account(), signals_from(signal_frame(rows, {entry_bar}, {exit_bar})):
                ours = backtest.simulate("X", stop_on_close=False)
            if not len(ours) or not len(theirs):
                continue
            compared += 1
            self.assertAlmostEqual(float(theirs.iloc[0]["EntryPrice"]),
                                   ours[0]["entry_price"], places=6,
                                   msg=f"entry differed on trial {trial}")
            self.assertAlmostEqual(float(theirs.iloc[0]["ExitPrice"]),
                                   ours[0]["exit_price"], places=6,
                                   msg=f"exit differed on trial {trial}")
        self.assertGreater(compared, 10, "too few paths actually compared")


if __name__ == "__main__":
    unittest.main()

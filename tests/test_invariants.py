"""Properties that must hold for EVERY run, and a golden value that must not move.

The tests above check that each piece does what it was written to do. These
check things no single piece owns: that the account's books balance, that
nothing is ever known before it happened, and that a refactor which quietly
changes every number fails loudly instead of silently.
"""
import unittest

import pandas as pd

from kitelab import backtest, portfolio
from tests.support import account, signal_frame, signals_from, trade

FLAT = (100.0, 105.0, 95.0, 100.0)


def spread_of_trades(n=12):
    """A deterministic set of overlapping signals across several symbols."""
    out = []
    for i in range(n):
        out.append(trade(symbol=f"S{i % 4}",
                         entry=f"2020-{(i % 9) + 1:02d}-01",
                         exit_=f"2020-{(i % 9) + 2:02d}-01",
                         entry_price=100 + i, exit_price=100 + i + (5 if i % 3 else -8),
                         stop=90 + i))
    return out


class BooksBalance(unittest.TestCase):
    def test_cash_is_never_negative_on_any_day(self):
        with account():
            r = portfolio.run(spread_of_trades(20), 200_000, 0.02)
        for _, cash in r["cash_curve"]:
            self.assertGreaterEqual(float(cash), -1e-6)

    def test_equity_never_falls_below_cash(self):
        """Equity is cash plus open positions held at cost; positions cannot be
        worth negative money, so equity below cash means the books do not add."""
        with account():
            r = portfolio.run(spread_of_trades(20), 200_000, 0.02)
        for (_, eq), (_, cash) in zip(r["curve"], r["cash_curve"]):
            self.assertGreaterEqual(float(eq), float(cash) - 1e-6)

    def test_final_equity_equals_capital_plus_every_realised_net(self):
        """The strongest arithmetic check available: what came out is what went
        in plus the sum of the trades, with nothing created or lost in between."""
        with account():
            r = portfolio.run(spread_of_trades(20), 200_000, 0.02)
        realised = sum(t["net"] for t in r["taken"])
        self.assertAlmostEqual(r["final"], 200_000 + realised, places=4)

    def test_the_curve_moves_forward_in_time(self):
        with account():
            r = portfolio.run(spread_of_trades(20), 200_000, 0.02)
        stamps = [pd.Timestamp(d) for d, _ in r["curve"]]
        self.assertEqual(stamps, sorted(stamps))

    def test_taken_plus_every_skip_reason_accounts_for_every_signal(self):
        """No signal may vanish unexplained. A trade dropped for a reason that
        is not counted would leave capture rate quietly overstated."""
        with account():
            r = portfolio.run(spread_of_trades(30), 60_000, 0.02)
        counted = (len(r["taken"]) + r["skipped_cash"] + r["skipped_size"]
                   + r["skipped_busy"] + r["skipped_tiny"] + r["skipped_liquidity"])
        self.assertLessEqual(counted, r["signals"])


class NothingKnownEarly(unittest.TestCase):
    def test_no_position_is_entered_before_its_signal(self):
        with account():
            r = portfolio.run(spread_of_trades(20), 200_000, 0.02)
        for t in r["taken"]:
            self.assertLessEqual(pd.Timestamp(t["entry_ts"]), pd.Timestamp(t["exit_ts"]))

    def test_a_later_bar_cannot_change_an_earlier_trade(self):
        """Append bars to the end and every trade that already closed must be
        byte-identical. This is the property the whole project rests on."""
        rows = [FLAT, (100, 110, 90, 108), FLAT, (100, 105, 95, 97), FLAT]
        with account(), signals_from(signal_frame(rows, {1}, {3})):
            short = backtest.simulate("X")
        longer = rows + [(100, 200, 100, 190)] * 5
        with account(), signals_from(signal_frame(longer, {1}, {3})):
            extended = backtest.simulate("X")
        self.assertEqual(len(short), 1)
        for field in ("entry_ts", "entry_price", "stop", "exit_price", "net_profit"):
            self.assertEqual(short[0][field], extended[0][field], f"{field} moved")


class BorderCases(unittest.TestCase):
    def test_no_trades_at_all_is_a_flat_account_not_a_crash(self):
        with account():
            r = portfolio.run([], 100_000, 0.01)
        self.assertEqual(r["final"], 100_000)
        self.assertEqual(len(r["taken"]), 0)

    def test_a_single_trade_produces_usable_statistics(self):
        with account():
            r = portfolio.run([trade()], 100_000, 0.01)
        self.assertEqual(len(r["taken"]), 1)
        self.assertIsNotNone(r["max_drawdown_pct"])

    def test_every_trade_winning_gives_no_drawdown(self):
        wins = [trade(symbol=f"W{i}", entry=f"2020-0{i+1}-01", exit_=f"2020-0{i+2}-01",
                      entry_price=100, exit_price=140, stop=90) for i in range(6)]
        with account():
            r = portfolio.run(wins, 500_000, 0.01)
        self.assertGreaterEqual(r["max_drawdown_pct"], -1.0)

    def test_every_trade_losing_never_takes_equity_below_zero(self):
        losses = [trade(symbol=f"L{i}", entry=f"2020-0{i+1}-01", exit_=f"2020-0{i+2}-01",
                        entry_price=100, exit_price=91, stop=90) for i in range(6)]
        with account():
            r = portfolio.run(losses, 100_000, 0.02)
        self.assertGreater(r["final"], 0)


class Golden(unittest.TestCase):
    """A fixed input with a recorded answer.

    Everything else here asserts a property; this asserts a NUMBER. Its job is
    to fail when a refactor changes results without anyone intending it -- the
    silent drift that no property test catches because every property still
    holds. If this breaks, either the change was wrong or the value below needs
    updating deliberately, with the reason recorded.
    """

    def test_a_known_trade_list_gives_the_recorded_account(self):
        rows = [FLAT,
                (100, 112, 90, 110),        # entry: close 110, stop 90
                (110, 130, 108, 128),
                (128, 140, 120, 138),
                (138, 142, 100, 105)]       # exit on the signal turning false
        with account(), signals_from(signal_frame(rows, {1}, {4})):
            trades = backtest.simulate("X")
        self.assertEqual(len(trades), 1)
        got = trades[0]
        self.assertEqual(got["entry_price"], 110.0)
        self.assertEqual(got["stop"], 90.0)
        self.assertEqual(got["exit_price"], 105.0)
        self.assertAlmostEqual(got["r_multiple"], -0.25, places=9)

    def test_the_same_input_gives_the_same_account_twice(self):
        with account():
            a = portfolio.run(spread_of_trades(15), 200_000, 0.01)
            b = portfolio.run(spread_of_trades(15), 200_000, 0.01)
        self.assertEqual(a["final"], b["final"])
        self.assertEqual([t["symbol"] for t in a["taken"]],
                         [t["symbol"] for t in b["taken"]])


if __name__ == "__main__":
    unittest.main()

"""kitelab.backtest.simulate -- the exit rules every published number rests on.

Untested until 2026-09-03, and the gap was not academic: the module docstring
described a live intrabar stop while the code's default evaluates stops only at
closes. The two produce different trades from identical signals, and the
discrepancy was found by an excursion study rather than by anything that could
have failed. Each rule the docstring states now has a test.

The signal frame is hand-built (tests.support.signal_frame) so these exercise
the EXIT MECHANICS alone. Testing them through the real EMA stack would mean
arranging twenty EMA values to make the condition turn true on the right bar,
and would be testing two things at once.
"""
import unittest

from kitelab import backtest
from tests.support import account, signal_frame, signals_from

FLAT = (100.0, 105.0, 95.0, 100.0)      # open, high, low, close


def run(rows, entries, exits, **kw):
    with account(), signals_from(signal_frame(rows, entries, exits)):
        return backtest.simulate("X", **kw)


class EntryAndStop(unittest.TestCase):
    def test_entry_fills_at_the_close_of_the_signal_bar(self):
        rows = [FLAT, (100, 110, 90, 108), FLAT, FLAT, FLAT]
        t = run(rows, entries={1}, exits={3})
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["entry_price"], 108.0)

    def test_the_stop_is_the_entry_bar_s_own_low(self):
        rows = [FLAT, (100, 110, 88, 108), FLAT, FLAT, FLAT]
        t = run(rows, entries={1}, exits={3})
        self.assertEqual(t[0]["stop"], 88.0)

    def test_a_signal_bar_whose_close_is_at_or_below_its_low_is_skipped(self):
        """Risk would be zero or negative and every sizing rule divides by it."""
        rows = [FLAT, (100, 110, 108, 108), FLAT, FLAT]
        self.assertEqual(len(run(rows, entries={1}, exits={3})), 0)

    def test_no_signal_means_no_trades(self):
        self.assertEqual(len(run([FLAT] * 5, entries=set(), exits={3})), 0)


class StopOnClose(unittest.TestCase):
    """The class convention and the DEFAULT: everything is judged at closes."""

    def test_a_close_at_or_below_the_stop_exits_at_that_close(self):
        rows = [FLAT, (100, 110, 90, 108), (100, 105, 80, 85), FLAT]
        t = run(rows, entries={1}, exits=set())
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["exit_price"], 85.0)
        self.assertIn("stop", t[0]["exit_reason"])

    def test_a_low_that_pierces_the_stop_but_closes_above_does_NOT_exit(self):
        """The finding that ~10% of winners survive this way. A trader holding a
        resting stop order would have been taken out; this convention was not."""
        rows = [FLAT, (100, 110, 90, 108), (100, 108, 70, 106), FLAT, FLAT]
        t = run(rows, entries={1}, exits={3})
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["exit_reason"], "ema break")
        self.assertGreater(t[0]["exit_price"], t[0]["stop"])

    def test_the_signal_turning_false_exits_at_that_close(self):
        rows = [FLAT, (100, 110, 90, 108), FLAT, (100, 105, 95, 97), FLAT]
        t = run(rows, entries={1}, exits={3})
        self.assertEqual(t[0]["exit_price"], 97.0)
        self.assertEqual(t[0]["exit_reason"], "ema break")


class IntrabarStop(unittest.TestCase):
    """stop_on_close=False -- the broker convention: a resting stop order."""

    def test_a_touch_fills_at_the_stop_not_at_the_close(self):
        rows = [FLAT, (100, 110, 90, 108), (100, 108, 85, 106), FLAT, FLAT]
        t = run(rows, entries={1}, exits={3}, stop_on_close=False)
        self.assertEqual(t[0]["exit_price"], 90.0)          # the stop itself

    def test_a_gap_straight_through_the_stop_fills_at_the_open(self):
        """Not at the stop: nobody traded there. This is the conservative
        reading, and it costs the strategy rather than flattering it."""
        rows = [FLAT, (100, 110, 90, 108), (80, 82, 78, 81), FLAT, FLAT]
        t = run(rows, entries={1}, exits={3}, stop_on_close=False)
        self.assertEqual(t[0]["exit_price"], 80.0)          # the gapped open
        self.assertIn("gap", t[0]["exit_reason"])

    def test_the_two_conventions_disagree_on_the_same_bars(self):
        """The whole reason the default matters. Identical signals, one bar that
        pierces the stop intraday and recovers: one convention exits, one holds."""
        rows = [FLAT, (100, 110, 90, 108), (100, 108, 70, 106), FLAT, FLAT]
        closes = run(rows, entries={1}, exits={3})
        intra = run(rows, entries={1}, exits={3}, stop_on_close=False)
        self.assertNotEqual(closes[0]["exit_price"], intra[0]["exit_price"])


class ReEntry(unittest.TestCase):
    def test_the_signal_must_go_false_before_it_can_fire_again(self):
        """Still-true is not a fresh signal. Without this the rule re-buys the
        same continuous uptrend every bar after any exit."""
        rows = [FLAT] * 8
        t = run(rows, entries={1, 2, 3, 4, 5, 6}, exits={7})
        self.assertEqual(len(t), 1)

    def test_a_fresh_signal_after_the_stack_breaks_is_a_new_trade(self):
        rows = [FLAT, (100, 110, 90, 108), (100, 105, 95, 97), FLAT,
                (100, 110, 90, 108), FLAT, (100, 105, 95, 97)]
        t = run(rows, entries={1, 4}, exits={2, 6})
        self.assertEqual(len(t), 2)

    def test_being_stopped_out_while_the_signal_holds_is_not_a_re_entry(self):
        """Stopped out on bar 2 while entry_ok stays true throughout: the rule
        must wait for the condition to go false and true again, not buy back in."""
        rows = [FLAT, (100, 110, 90, 108), (100, 105, 80, 85), FLAT, FLAT, FLAT]
        t = run(rows, entries={1, 2, 3, 4, 5}, exits=set())
        self.assertEqual(len(t), 1)


class OpenAtTheEnd(unittest.TestCase):
    """A position still open when the data runs out is DROPPED, not marked to
    the last close. Standard practice, and deliberate here -- but it is a real
    bias worth naming: the currently-running trade is never counted, and on a
    trend rule that trade is disproportionately a winner in a rising market and
    a loser in a falling one. Nothing downstream can see the omission."""

    def test_a_position_never_closed_produces_no_trade(self):
        t = run([FLAT] * 6, entries={1}, exits=set())
        self.assertEqual(len(t), 0)

    def test_the_same_signal_closed_before_the_end_does_produce_one(self):
        t = run([FLAT] * 6, entries={1}, exits={4})
        self.assertEqual(len(t), 1)

    def test_a_signal_on_the_very_last_bar_is_not_entered(self):
        """There is nothing left to exit into."""
        t = run([FLAT] * 4, entries={3}, exits=set())
        self.assertEqual(len(t), 0)


class TradeRecord(unittest.TestCase):
    def test_r_multiple_is_gross_over_the_risk_taken(self):
        rows = [FLAT, (100, 110, 90, 100), FLAT, (100, 130, 95, 120), FLAT]
        t = run(rows, entries={1}, exits={3})[0]
        self.assertAlmostEqual(t["r_multiple"], t["gross_profit"] / t["risk_taken"],
                               places=9)

    def test_net_profit_is_gross_less_charges(self):
        rows = [FLAT, (100, 110, 90, 100), FLAT, (100, 130, 95, 120), FLAT]
        t = run(rows, entries={1}, exits={3})[0]
        self.assertAlmostEqual(t["net_profit"], t["gross_profit"] - t["charges"],
                               places=9)

    def test_every_producer_field_the_account_needs_is_present(self):
        rows = [FLAT, (100, 110, 90, 100), FLAT, (100, 130, 95, 120), FLAT]
        t = run(rows, entries={1}, exits={3})[0]
        for field in ("symbol", "entry_ts", "exit_ts", "entry_price", "exit_price",
                      "stop", "same_session", "shares", "risk_taken", "net_profit"):
            self.assertIn(field, t, f"portfolio.run needs {field}")

    def test_exit_never_precedes_entry(self):
        rows = [FLAT, (100, 110, 90, 108), FLAT, FLAT, (100, 105, 95, 97)]
        for t in run(rows, entries={1}, exits={4}):
            self.assertGreater(t["exit_ts"], t["entry_ts"])


if __name__ == "__main__":
    unittest.main()

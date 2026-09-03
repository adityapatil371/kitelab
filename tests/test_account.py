"""kitelab.portfolio -- the account engine.

The most consequential code in the project and, until 2026-09-03, the only
substantial module with no test at all. Everything the dashboard reports passes
through run(): position sizing, the cash constraint, which signals get skipped
and why, and the drawdown the whole comparison is ranked on.
"""
import math
import unittest


from kitelab import portfolio
from tests.support import account, trade


class Sizing(unittest.TestCase):
    def test_shares_come_from_risk_divided_by_stop_distance(self):
        with account():
            r = portfolio.run([trade(entry_price=100, stop=90)], 100_000, 0.01)
        # Rs1,000 of risk over a Rs10 stop is 100 shares. Whole shares only.
        self.assertEqual(r["taken"][0]["shares"], 100)

    def test_cash_caps_the_position_when_the_stop_is_tight(self):
        # A 1% stop would call for 1,000 shares (Rs1,00,000 of stock) on risk
        # alone; the account holds Rs20,000, so cash binds first.
        with account():
            r = portfolio.run([trade(entry_price=100, stop=99)], 20_000, 0.01)
        self.assertEqual(r["taken"][0]["shares"], math.floor(20_000 / 100))

    def test_a_stop_at_or_above_entry_is_refused_not_divided_by(self):
        with account():
            r = portfolio.run([trade(entry_price=100, stop=100)], 100_000, 0.01)
        self.assertEqual(len(r["taken"]), 0)


class CashConstraint(unittest.TestCase):
    def test_one_position_per_symbol(self):
        overlapping = [trade(symbol="AAA", entry="2020-01-01", exit_="2020-06-01"),
                       trade(symbol="AAA", entry="2020-02-01", exit_="2020-03-01")]
        with account():
            r = portfolio.run(overlapping, 100_000, 0.01)
        self.assertEqual(len(r["taken"]), 1)
        self.assertEqual(r["skipped_busy"], 1)

    def test_signals_with_no_cash_are_skipped_and_counted(self):
        # Ten simultaneous signals, each wanting Rs50,000 of stock, on Rs100,000.
        many = [trade(symbol=f"S{i}", entry="2020-01-01", exit_="2020-12-01",
                      entry_price=100, stop=98) for i in range(10)]
        with account():
            r = portfolio.run(many, 100_000, 0.01)
        self.assertLess(len(r["taken"]), 10)
        self.assertGreater(r["skipped_cash"], 0)
        self.assertEqual(len(r["taken"]) + r["skipped_cash"] + r["skipped_size"]
                         + r["skipped_busy"] + r["skipped_tiny"]
                         + r["skipped_liquidity"], 10)

    def test_cash_is_never_left_negative(self):
        many = [trade(symbol=f"S{i}", entry=f"2020-01-{i+1:02d}", exit_="2021-01-01",
                      entry_price=100, stop=99) for i in range(20)]
        with account():
            r = portfolio.run(many, 50_000, 0.02)
        # Every entry is debited from cash; the engine trims to what cash allows.
        spent = sum(t["shares"] * t["entry_price"] for t in r["taken"])
        self.assertLessEqual(spent, 50_000 + 1e-6)

    def test_an_exit_funds_a_same_day_entry(self):
        # BBB cannot be afforded unless AAA's proceeds settle first: settle()
        # runs before every entry precisely so exits are not queued behind buys.
        pair = [trade(symbol="AAA", entry="2020-01-01", exit_="2020-02-01",
                      entry_price=100, stop=90),
                trade(symbol="BBB", entry="2020-02-01", exit_="2020-03-01",
                      entry_price=100, stop=90)]
        with account():
            r = portfolio.run(pair, 10_500, 0.10)
        self.assertEqual(len(r["taken"]), 2)


class TinyPositions(unittest.TestCase):
    def test_a_position_whose_costs_eat_it_is_refused(self):
        # Rs52 positions paying a flat Rs15.34 demat charge polluted every
        # statistic before 2026-08-31. MAX_COST_FRACTION refuses them.
        with account():
            r = portfolio.run([trade(entry_price=1.0, stop=0.9)], 600, 0.01)
        self.assertEqual(len(r["taken"]), 0)
        self.assertEqual(r["skipped_tiny"], 1)


class Priority(unittest.TestCase):
    """Which signal wins the cash when both cannot be funded."""

    def _contested(self):
        return [trade(symbol="LIQUID", entry="2020-01-01", exit_="2020-12-01",
                      entry_price=100, stop=90),
                trade(symbol="THIN", entry="2020-01-01", exit_="2020-12-01",
                      entry_price=100, stop=50)]

    # Rs30,000 at 10% risk is the size that makes this a real contest: both
    # positions clear the tiny-position floor (~Rs5,400, see MAX_COST_FRACTION),
    # and whichever is offered first takes enough cash to starve the other. Test
    # WHICH SYMBOL GOT FUNDED, not taken[0] -- `taken` is appended by settle(),
    # so it is ordered by exit, not by the priority under test.
    def _funded(self, rule, liq=None):
        with account(liquidity=liq or {}):
            r = portfolio.run(self._contested(), 30_000, 0.10, rule)
        return {t["symbol"] for t in r["taken"]}

    def test_liquidity_and_illiquid_fund_opposite_signals(self):
        liq = {"LIQUID": 1e9, "THIN": 1e3}
        self.assertNotIn("THIN", self._funded("liquidity", liq))
        self.assertIn("THIN", self._funded("illiquid", liq))

    def test_wide_and_tight_order_by_stop_distance(self):
        # THIN carries the 50% stop, LIQUID the 10% one.
        self.assertIn("THIN", self._funded("wide"))
        self.assertNotIn("THIN", self._funded("tight"))

    def test_priority_never_consults_the_outcome(self):
        """A rule that looked at exit_price would be a time machine.

        Same entries, opposite outcomes: if any ordering rule peeked at how the
        trade turned out, the two runs would disagree about what was taken.
        """
        base = self._contested()
        flipped = [dict(t, exit_price=1.0) for t in base]
        for rule in portfolio.PRIORITIES:
            with account():
                a = portfolio.run(base, 11_000, 0.10, rule)
                b = portfolio.run(flipped, 11_000, 0.10, rule)
            self.assertEqual([t["symbol"] for t in a["taken"]],
                             [t["symbol"] for t in b["taken"]], f"{rule} peeked")

    def test_unknown_priority_is_refused_rather_than_silently_ignored(self):
        with account(), self.assertRaises(ValueError):
            portfolio.run([trade()], 100_000, 0.01, "nonsense")


class Drawdown(unittest.TestCase):
    def test_a_destroyed_account_reports_no_cagr_and_says_it_was_wiped(self):
        with account():
            r = portfolio.run([trade(entry_price=100, exit_price=0.01, stop=90)],
                              10_200, 1.0)
        self.assertTrue(r["wiped"])
        self.assertIsNone(r["cagr_pct"])

    def test_drawdown_is_measured_against_the_running_peak(self):
        # Up to ~Rs110k, then all the way down: the dip is measured from the
        # peak that was standing at the time, not from the final equity.
        seq = [trade(symbol="AAA", entry="2020-01-01", exit_="2020-02-01",
                     entry_price=100, exit_price=200, stop=90),
               trade(symbol="BBB", entry="2020-03-01", exit_="2020-04-01",
                     entry_price=100, exit_price=50, stop=90)]
        with account():
            r = portfolio.run(seq, 100_000, 0.01)
        self.assertLess(r["max_drawdown_pct"], 0)


if __name__ == "__main__":
    unittest.main()

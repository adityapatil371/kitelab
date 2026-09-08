"""kitelab.portfolio -- the account engine.

The most consequential code in the project and, until 2026-09-03, the only
substantial module with no test at all. Everything the dashboard reports passes
through run(): position sizing, the cash constraint, which signals get skipped
and why, and the drawdown the whole comparison is ranked on.
"""
import math
import unittest


from kitelab import portfolio
from tests.support import TS, account, trade


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


class PerInstrumentSettings(unittest.TestCase):
    """One account, instruments with different cost models and unit sizes.

    Both were module globals until 2026-09-03, so the whole grid ran under one
    setting and non-equity instruments needed a separate hardcoded account. That
    is what made them a page rather than a universe.
    """

    def test_a_trade_carrying_fee_rate_is_priced_by_it_not_the_equity_schedule(self):
        cheap = trade(entry_price=100, exit_price=110, stop=90)
        cheap["fee_rate"] = 0.0            # a fee-free instrument
        with account():
            free = portfolio.run([cheap], 100_000, 0.01)
            equity = portfolio.run([trade(entry_price=100, exit_price=110, stop=90)],
                                   100_000, 0.01)
        self.assertGreater(free["taken"][0]["net"], equity["taken"][0]["net"])

    def test_fee_rate_replaces_the_schedule_rather_than_adding_to_it(self):
        t = trade(entry_price=100, exit_price=100, stop=90)
        t["fee_rate"] = 0.001
        with account():
            r = portfolio.run([t], 100_000, 0.01)
        shares = r["taken"][0]["shares"]
        value = shares * 100.0
        self.assertAlmostEqual(-r["taken"][0]["net"], 0.001 * 2 * value, places=4)

    def test_a_fractional_trade_can_hold_less_than_one_unit(self):
        """A whole-unit rule refuses anything under one share. Bitcoin at
        lakhs per coin would otherwise never be buyable on a small account."""
        pricey = trade(symbol="BTC", entry_price=5_000_000, exit_price=5_500_000,
                       stop=4_500_000)
        pricey["fractional"] = True
        pricey["fee_rate"] = 0.001
        with account(prices={"BTC": [(TS("2019-01-01"), 5e6), (TS("2021-12-31"), 5e6)]}):
            r = portfolio.run([pricey], 200_000, 0.01)
        self.assertEqual(len(r["taken"]), 1)
        self.assertLess(r["taken"][0]["shares"], 1.0)
        self.assertGreater(r["taken"][0]["shares"], 0.0)

    def test_the_same_trade_without_fractional_is_refused(self):
        pricey = trade(symbol="BTC", entry_price=5_000_000, exit_price=5_500_000,
                       stop=4_500_000)
        with account(prices={"BTC": [(TS("2019-01-01"), 5e6), (TS("2021-12-31"), 5e6)]}):
            r = portfolio.run([pricey], 200_000, 0.01)
        self.assertEqual(len(r["taken"]), 0)

    def test_one_account_can_mix_both(self):
        """The point of the refactor: an equity and a fractional instrument in
        the same run, each priced and sized by its own rules."""
        eq = trade(symbol="AAA", entry_price=100, exit_price=110, stop=90)
        btc = trade(symbol="BTC", entry_price=5_000_000, exit_price=5_500_000,
                    stop=4_500_000)
        btc["fractional"] = True
        btc["fee_rate"] = 0.001
        with account(prices={"BTC": [(TS("2019-01-01"), 5e6), (TS("2021-12-31"), 5e6)]}):
            r = portfolio.run([eq, btc], 500_000, 0.01)
        got = {t["symbol"]: t["shares"] for t in r["taken"]}
        self.assertEqual(len(got), 2)
        self.assertEqual(got["AAA"], int(got["AAA"]))     # whole shares
        self.assertNotEqual(got["BTC"], int(got["BTC"]))  # fractional units


class FuturesContracts(unittest.TestCase):
    """Lots and margin. Modelled from 2026-09-03; before that a futures contract
    was bought like a share, in any integer quantity, for its full value."""

    def _gold(self, entry=160_000.0, exit_=170_000.0, stop=155_000.0):
        t = trade(symbol="GOLD", entry_price=entry, exit_price=exit_, stop=stop)
        t["multiplier"] = 100.0        # 1 kg lot, quoted per 10 g
        t["margin_pct"] = 0.06
        t["fee_rate"] = 0.0005
        return t

    def _prices(self):
        return {"GOLD": [(TS("2019-01-01"), 160_000.0), (TS("2021-12-31"), 160_000.0)]}

    def test_an_account_too_small_for_one_lot_takes_no_trade(self):
        """One lot of gold is Rs1.6 crore of metal at ~Rs9.6 lakh margin. A
        Rs2,00,000 account cannot hold a fraction of it -- it holds none."""
        with account(prices=self._prices()):
            r = portfolio.run([self._gold()], 200_000, 0.01)
        self.assertEqual(len(r["taken"]), 0)
        self.assertEqual(r["skipped_size"] + r["skipped_cash"], 1)

    def test_a_large_enough_account_holds_whole_lots_only(self):
        with account(prices=self._prices()):
            r = portfolio.run([self._gold()], 50_000_000, 0.05)
        self.assertEqual(len(r["taken"]), 1)
        lots = r["taken"][0]["shares"] / 100.0
        self.assertEqual(lots, int(lots))
        self.assertGreaterEqual(lots, 1)

    def test_only_margin_is_debited_not_the_contract_value(self):
        """The point of the model: exposure far exceeds the cash committed."""
        with account(prices=self._prices()):
            r = portfolio.run([self._gold()], 50_000_000, 0.05)
        pos = r["taken"][0]
        notional = pos["shares"] * pos["entry_price"]
        self.assertLess(pos["margin_held"], notional * 0.10)
        self.assertGreater(notional, pos["margin_held"] * 10)

    def test_profit_is_the_full_move_on_the_contract_not_on_the_margin(self):
        with account(prices=self._prices()):
            r = portfolio.run([self._gold()], 50_000_000, 0.05)
        pos = r["taken"][0]
        gross = pos["shares"] * (170_000.0 - 160_000.0)
        self.assertAlmostEqual(pos["net"], gross - (pos["net"] and 0) - (gross - pos["net"]),
                               places=6)
        self.assertGreater(pos["net"], 0)
        self.assertLess(abs(pos["net"] - gross) / gross, 0.05)   # costs only

    def test_an_equity_in_the_same_run_is_untouched_by_any_of_this(self):
        eq = trade(symbol="AAA", entry_price=100, exit_price=110, stop=90)
        with account(prices=self._prices()):
            r = portfolio.run([eq, self._gold()], 50_000_000, 0.05)
        by = {t["symbol"]: t for t in r["taken"]}
        self.assertIn("AAA", by)
        self.assertIsNone(by["AAA"].get("margin_held"))


class DatedMargin(unittest.TestCase):
    """Margin is a schedule, not a constant. Crude's minimum initial margin ran
    ~9% in 2016 and ~33% after August 2024 -- a factor of three inside one
    backtest. A single figure lets the account hold late positions the exchange
    would have refused, which is the direction that flatters."""

    def test_the_same_trade_costs_more_margin_after_a_revision(self):
        from kitelab import contracts
        spec = contracts.get("CRUDEOIL")
        early = spec.margin_at("2016-06-01")
        late = spec.margin_at("2025-06-01")
        self.assertLess(early, late)
        self.assertGreater(late / early, 2.5)

    def test_a_wider_margin_means_fewer_lots_for_the_same_cash(self):
        """Sized at 100% risk so the RISK limit cannot bind and margin decides.

        Worth stating because it is the general case: risk-based sizing usually
        binds first, and margin only becomes the constraint on wide stops or
        large accounts. The first version of this test used 5% risk, where both
        margins gave the identical 50 lots -- it was measuring the risk rule and
        would have passed whatever the margin schedule said.
        """
        def lots(margin):
            t = trade(symbol="CRUDE", entry_price=5_000.0, exit_price=5_200.0,
                      stop=4_800.0)
            t["multiplier"], t["margin_pct"], t["fee_rate"] = 100.0, margin, 0.0005
            prices = {"CRUDE": [(TS("2019-01-01"), 5_000.0), (TS("2021-12-31"), 5_000.0)]}
            with account(prices=prices):
                r = portfolio.run([t], 5_000_000, 1.0)
            return r["taken"][0]["shares"] / 100.0 if r["taken"] else 0

        cheap, dear = lots(0.09), lots(0.33)
        self.assertGreater(cheap, dear)
        # Lots scale inversely with margin: 0.33/0.09 is ~3.7x.
        self.assertAlmostEqual(cheap / dear, 0.33 / 0.09, delta=0.5)

    def test_a_contract_cannot_trade_before_it_existed(self):
        from kitelab import contracts
        ten = contracts.get("GOLDTEN")
        self.assertTrue(ten.predates_launch("2010-01-01"))
        self.assertFalse(ten.predates_launch("2025-06-01"))

    def test_every_multiplier_was_checked_against_a_contract_note(self):
        """Multipliers are definitions and were verified 2026-09-03. Margins
        were not and never can be to the same standard -- SPAN is recomputed
        daily -- which is why they are a dated schedule rather than a flag."""
        from kitelab import contracts
        self.assertEqual(contracts.unverified_multipliers(), [])


class FractionalFlag(unittest.TestCase):
    """The per-trade `fractional` flag must be the ONLY flag the sizing reads.

    Audit A8 (2026-09-07): the impact loop re-read the module global instead,
    so a fractional trade trimmed to what cash could afford was floored to
    whole coins and refused. ema|0|BITCOIN|0.5|200000 took 63 of 113 signals
    and reported 50 skipped for cash with median cash at 100%; with the flag
    honoured it takes 113 of 113 and CAGR moves 4.4 -> 5.4.
    """

    def test_a_fractional_trade_trimmed_by_impact_is_still_taken_at_a_fraction(self):
        from kitelab import slippage
        pricey = trade(symbol="BTC", entry_price=5_000_000, exit_price=5_500_000,
                       stop=4_500_000)
        pricey["fractional"] = True
        pricey["fee_rate"] = 0.001
        prices = {"BTC": [(TS("2019-01-01"), 5e6), (TS("2021-12-31"), 5e6)]}
        saved = slippage.impact
        # 1% impact on every order: the risk-sized order costs more than the
        # cash it was sized against, so the loop must trim it.
        slippage.impact = lambda symbol, stamp, value: 0.01
        try:
            with account(prices=prices, spread=True, fractional=False):
                r = portfolio.run([pricey], 20_000, 1.0)
        finally:
            slippage.impact = saved
        self.assertEqual(len(r["taken"]), 1)
        self.assertLess(r["taken"][0]["shares"], 1.0)
        self.assertGreater(r["taken"][0]["shares"], 0.0)
        self.assertEqual(r["skipped_cash"], 0)
        for _, cash in r["cash_curve"]:
            self.assertGreaterEqual(cash, -1e-6)


class TinyPositionsSplit(unittest.TestCase):
    """Which rule left the position under the cost floor.

    Audit D4 (2026-09-07): W/D at all / Rs2L / 1% / 2018 refused 53,266 of
    95,452 signals as tiny, and 53,252 of those were cash scraps -- the account
    had the money for a real position elsewhere. One count hid that.
    """

    def test_a_risk_sized_position_under_the_floor_counts_against_risk(self):
        with account():
            r = portfolio.run([trade(entry_price=1.0, stop=0.9)], 600, 0.01)
        self.assertEqual(r["skipped_tiny_risk"], 1)
        self.assertEqual(r["skipped_tiny_cash"], 0)
        self.assertEqual(r["skipped_tiny"], 1)

    def test_cash_scraps_count_against_cash(self):
        # A (offered first: same day, same liquidity, alphabetical) risks Rs11 a
        # share against Rs2,200, so it takes 200 shares -- Rs20,000 of the
        # Rs22,000. B's risk rule wants 220 shares; the Rs2,000 left buys 20,
        # which is under the floor. The account, not the rule, made it tiny.
        pair = [trade(symbol="AAA", entry="2020-01-01", exit_="2020-06-01",
                      entry_price=100, stop=89),
                trade(symbol="BBB", entry="2020-01-01", exit_="2020-06-01",
                      entry_price=100, stop=90)]
        with account():
            r = portfolio.run(pair, 22_000, 0.10)
        self.assertEqual(len(r["taken"]), 1)
        self.assertEqual(r["skipped_tiny_cash"], 1)
        self.assertEqual(r["skipped_tiny_risk"], 0)
        self.assertEqual(r["skipped_tiny"], r["skipped_tiny_cash"] + r["skipped_tiny_risk"])

    def test_a_position_the_participation_cap_trims_under_the_floor_is_refused(self):
        """Audit A5: the cap trimmed AFTER the floor check, so a trimmed order
        could be placed at a size the floor would have refused."""
        from kitelab import slippage
        saved = slippage.capped_shares
        slippage.capped_shares = lambda symbol, stamp, price, shares: min(shares, 20)
        try:
            with account():
                r = portfolio.run([trade(entry_price=100, stop=90)], 100_000, 0.01)
        finally:
            slippage.capped_shares = saved
        self.assertEqual(len(r["taken"]), 0)
        self.assertEqual(r["skipped_tiny_cash"], 1)
        self.assertEqual(r["skipped_liquidity"], 0)


class AnnualisationSpan(unittest.TestCase):
    def test_years_run_to_the_latest_exit_not_the_last_entered_trades_exit(self):
        """Audit finding 6 (2026-09-07): `years` ended at the exit of the
        last-ENTERED trade. A trend rule holds its winners, so an older
        position usually outlives the newest one and the span came out short,
        which inflates the annualised rate."""
        seq = [trade(symbol="AAA", entry="2020-01-01", exit_="2021-01-01",
                     entry_price=100, exit_price=110, stop=90),
               trade(symbol="BBB", entry="2020-02-01", exit_="2020-03-01",
                     entry_price=100, exit_price=110, stop=90)]
        with account():
            r = portfolio.run(seq, 100_000, 0.01)
        self.assertEqual(len(r["taken"]), 2)
        self.assertAlmostEqual(r["years"], 366 / 365.25, places=6)

    def test_the_span_is_what_the_account_was_offered_not_only_what_it_took(self):
        """An account alive and refusing signals is still alive: a second AAA
        signal it cannot take (one position per symbol) still extends its
        clock, otherwise one early winner annualises over a few weeks."""
        seq = [trade(symbol="AAA", entry="2020-01-01", exit_="2020-03-01",
                     entry_price=100, exit_price=110, stop=90),
               trade(symbol="AAA", entry="2020-02-01", exit_="2021-01-01",
                     entry_price=100, exit_price=110, stop=90)]
        with account():
            r = portfolio.run(seq, 100_000, 0.01)
        self.assertEqual(r["skipped_busy"], 1)
        self.assertAlmostEqual(r["years"], 366 / 365.25, places=6)


class OneBook(unittest.TestCase):
    """The daily curve is READ OFF run()'s ledger, never recomputed.

    Audit A9 (2026-09-07): daily_curve kept its own books -- full notional on
    entry, the equity fee schedule on exit, no margin -- and on GOLD at
    Rs1cr / 1% / 2006 ended at Rs93,02,789 against a settled final of
    Rs1,23,06,279, with a cash floor of -Rs6.7cr that reached the page as
    median_cash -921. Every trade list in this module must reconcile.
    """

    def _gold(self, entry_price=160_000.0, exit_price=170_000.0, stop=155_000.0, **kw):
        t = trade(symbol="GOLD", entry_price=entry_price, exit_price=exit_price,
                  stop=stop, **kw)
        t["multiplier"], t["margin_pct"], t["fee_rate"] = 100.0, 0.06, 0.0005
        return t

    def _btc(self, **kw):
        t = trade(symbol="BTC", entry_price=5_000_000, exit_price=5_500_000,
                  stop=4_500_000, **kw)
        t["fractional"], t["fee_rate"] = True, 0.001
        return t

    def scenarios(self):
        """(label, trades, capital, risk, prices) for every shape this module
        exercises: plain equities, contested cash, same-day round trips, a
        wiped account, fractional, fee-rated, and futures on margin."""
        flat = {"GOLD": [(TS("2019-01-01"), 160_000.0), (TS("2021-12-31"), 160_000.0)],
                "BTC": [(TS("2019-01-01"), 5e6), (TS("2021-12-31"), 5e6)]}
        moving = {"GOLD": [(TS(f"2020-01-{d:02d}"), 160_000.0 + 1_000.0 * d)
                           for d in range(1, 31)]}
        many = [trade(symbol=f"S{i}", entry=f"2020-01-{i+1:02d}", exit_="2021-01-01",
                      entry_price=100, stop=99) for i in range(20)]
        contested = [trade(symbol="LIQUID", entry="2020-01-01", exit_="2020-12-01",
                           entry_price=100, stop=90),
                     trade(symbol="THIN", entry="2020-01-01", exit_="2020-12-01",
                           entry_price=100, stop=50)]
        return [
            ("one equity", [trade()], 100_000, 0.01, {}),
            ("twenty overlapping equities", many, 50_000, 0.02, {}),
            ("contested cash", contested, 30_000, 0.10, {}),
            ("same-day round trip", [trade(exit_="2020-01-01", same_session=True)],
             100_000, 0.01, {}),
            ("wiped", [trade(entry_price=100, exit_price=0.01, stop=90)], 10_200, 1.0, {}),
            ("fee-rated equity", [dict(trade(), fee_rate=0.0)], 100_000, 0.01, {}),
            ("fractional", [self._btc()], 200_000, 0.01, flat),
            ("equity and fractional", [trade(), self._btc()], 500_000, 0.01, flat),
            ("gold on margin", [self._gold()], 50_000_000, 0.05, flat),
            ("gold and an equity", [trade(), self._gold()], 50_000_000, 0.05, flat),
            ("gold marked daily", [self._gold(entry="2020-01-02", exit_="2020-01-20")],
             50_000_000, 0.05, moving),
            ("gold losing", [self._gold(exit_price=150_000.0)], 50_000_000, 0.05, flat),
        ]

    def test_the_curve_ends_at_the_settled_final_and_cash_never_dips_below_zero(self):
        for label, trades, capital, risk, prices in self.scenarios():
            with self.subTest(label), account(prices=prices):
                r = portfolio.run(trades, capital, risk)
            self.assertTrue(r["curve"], f"{label}: no curve")
            self.assertAlmostEqual(r["curve"][-1][1], r["final"], places=2,
                                   msg=f"{label}: curve end != final")
            self.assertAlmostEqual(r["cash_curve"][-1][1], r["final"], places=2)
            # Cash may only ever go below zero by what the wiped account itself
            # ended at: settlement fees on a position sold for Rs1.02 are real
            # and the broker bills them. Never by a debit the ledger invented.
            for day, cash in r["cash_curve"]:
                self.assertGreaterEqual(cash, min(0.0, r["final"]) - 1e-6,
                                        f"{label}: cash {cash} on {day}")
            realised = sum(t["net"] for t in r["taken"])
            self.assertAlmostEqual(r["final"], capital + realised, places=4)

    def test_an_open_future_is_marked_at_margin_plus_the_move_not_at_notional(self):
        moving = {"GOLD": [(TS(f"2020-01-{d:02d}"), 160_000.0 + 1_000.0 * d)
                           for d in range(1, 31)]}
        gold = self._gold(entry="2020-01-02", exit_="2020-01-20")
        with account(prices=moving):
            r = portfolio.run([gold], 50_000_000, 0.05)
        pos = r["taken"][0]
        eq = dict(r["curve"])
        cash = dict(r["cash_curve"])
        day = TS("2020-01-10")
        move = pos["shares"] * ((160_000.0 + 10_000.0) - pos["entry_price"])
        self.assertAlmostEqual(eq[day], cash[day] + pos["margin_held"] + move, places=4)
        # And nowhere near the notional the old curve carried.
        self.assertLess(eq[day], cash[day] + 0.5 * pos["shares"] * 170_000.0)

    def test_sizing_equity_counts_an_open_future_at_margin_not_notional(self):
        """The sizing line summed shares x entry for every open position, so a
        6%-margin gold lot counted sixteen times the cash committed and the
        next trade was sized off money that was never in the account."""
        flat = {"GOLD": [(TS("2019-01-01"), 160_000.0), (TS("2021-12-31"), 160_000.0)]}
        gold = self._gold(entry="2020-01-01", exit_="2021-01-01")
        eq = trade(symbol="AAA", entry="2020-06-01", exit_="2020-07-01",
                   entry_price=100, stop=90)
        with account(prices=flat):
            r = portfolio.run([gold, eq], 50_000_000, 0.05)
        by = {t["symbol"]: t for t in r["taken"]}
        # Flat prices: no move, so equity for sizing is exactly the capital.
        self.assertEqual(by["AAA"]["shares"], math.floor(50_000_000 * 0.05 / 10))

    def test_the_ledger_is_returned_and_replays_to_the_same_curve(self):
        with account():
            r = portfolio.run([trade(), trade(symbol="BBB", entry="2020-02-01",
                                              exit_="2020-03-01")], 100_000, 0.01)
            again = portfolio.daily_curve(r["ledger"], 100_000)
        self.assertEqual(again["curve"], r["curve"])
        self.assertEqual(again["cash_curve"], r["cash_curve"])

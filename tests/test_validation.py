"""kitelab.validation: the dashboard's "is it real" numbers.

Hermetic: every price read goes through frames.daily, which these tests
replace with hand-built frames (`prices`) or with a loader that refuses
(`no_prices`), so nothing here depends on what is in /data. Where a number
can be worked out by hand -- the equal-weight benchmark, the cluster-robust
error, the luck hurdle, the permutation p -- the test states the hand
computation and checks against it, rather than against whatever the code
produced the day the test was written.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from statistics import NormalDist

import numpy as np
import pandas as pd

from kitelab import backtest, frames, validation
from tests import support

TS = pd.Timestamp


def _trade(entry, exit_, net_profit, risk_taken, symbol="AAA"):
    t = support.trade(symbol=symbol, entry=entry, exit_=exit_)
    t["net_profit"] = net_profit
    t["risk_taken"] = risk_taken
    t["gross_profit"] = net_profit
    t["charges"] = 0.0
    return t


def _frame(closes, start="2018-01-01", low_frac=0.99):
    """A daily frame from a close series; one bar per calendar day."""
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=len(closes), freq="D"),
        "open": closes, "high": closes * 1.01, "low": closes * low_frac, "close": closes,
        "volume": np.full(len(closes), 10_000),
    })


@contextlib.contextmanager
def prices(frames_by_symbol: dict):
    """Serve hand-built daily frames; any other symbol has no price file."""
    saved = frames.daily

    def fake(symbol, *a, **k):
        if symbol not in frames_by_symbol:
            raise SystemExit(f"no price file for {symbol}")
        return frames_by_symbol[symbol].reset_index(drop=True)

    frames.daily = fake
    try:
        yield
    finally:
        frames.daily = saved


def no_prices():
    return prices({})


# ------------------------------------------------------------ benchmark ----
class BuyAndHold(unittest.TestCase):
    """Equal-weight PORTFOLIO (audit A1, 2026-09-07), not the median stock."""

    def _universe(self):
        # A doubles, B is flat, C lists a year late and gains 50%, D has too
        # few bars to be held at all.
        return {
            "A": _frame(np.linspace(100, 200, 700), "2018-01-01"),
            "B": _frame(np.full(700, 100.0), "2018-01-01"),
            "C": _frame(np.linspace(100, 150, 300), "2019-01-01"),
            "D": _frame(np.linspace(100, 900, 100), "2018-01-01"),
        }

    def test_equal_weight_arithmetic_with_a_late_listed_member(self):
        with prices(self._universe()):
            got = validation.buy_and_hold(["A", "B", "C", "D"], start_year=2018)
        # Rs1 in each of A, B, C: 2.0 + 1.0 + 1.5 = 4.5 on Rs3, over the span
        # from A's first bar to A's last bar (C ends earlier, D is skipped).
        # Since 2026-09-09 the benchmark also pays one round trip, so the
        # gross 4.5 is haircut once -- see validation._hold_retention.
        years = (TS("2018-01-01") + pd.Timedelta(days=699) - TS("2018-01-01")).days / 365.25
        want = ((4.5 * validation._hold_retention() / 3) ** (1 / years) - 1) * 100
        self.assertAlmostEqual(got, want, places=6)

    def test_benchmark_pays_one_round_trip(self):
        """The rule pays the Zerodha schedule; before 2026-09-09 the benchmark
        paid nothing, which biased every margin on the board against the rule.
        The haircut is checked against backtest.charges directly, not against
        validation's own helper, so a schedule change cannot pass silently."""
        dp = backtest.DP_PER_SELL
        buy = backtest.charges(1.0, 0.0, intraday=False) - dp
        sell = backtest.charges(0.0, 1.0, intraday=False) - dp
        self.assertAlmostEqual(validation._hold_retention(),
                               (1.0 - sell) / (1.0 + buy), places=12)

        # and it must actually reach the reported CAGR: net below gross.
        with prices(self._universe()):
            net = validation.buy_and_hold(["A", "B", "C"], start_year=2018)
        years = (TS("2018-01-01") + pd.Timedelta(days=699) - TS("2018-01-01")).days / 365.25
        gross = ((4.5 / 3) ** (1 / years) - 1) * 100
        self.assertLess(net, gross)
        self.assertAlmostEqual(net, gross, delta=0.5)   # a haircut, not a rewrite

    def test_not_the_median_stock(self):
        """Jensen: a right-skewed set of stock returns has a portfolio CAGR
        well above its median stock's -- the whole reason the benchmark
        changed. Here the median stock (B) returns 0; the portfolio does not."""
        with prices(self._universe()):
            got = validation.buy_and_hold(["A", "B", "C"], start_year=2018)
        self.assertGreater(got, 10.0)

    def test_start_year_selects_the_entry_close(self):
        with prices(self._universe()):
            whole = validation.buy_and_hold(["A"], start_year=2018)
            later = validation.buy_and_hold(["A"], start_year=2019)
        # A rises linearly, so entering later buys higher and compounds less.
        self.assertLess(later, whole)

    def test_missing_files_and_thin_members_are_skipped_not_fatal(self):
        with prices(self._universe()):
            self.assertIsNone(validation.buy_and_hold(["D", "ZZZ"], start_year=2018))
            self.assertIsNotNone(validation.buy_and_hold(["A", "ZZZ"], start_year=2018))
        with no_prices():
            self.assertIsNone(validation.buy_and_hold(["A"], start_year=2018))


class HoldByWindow(unittest.TestCase):
    def test_keys_are_the_fixed_calendar_and_empty_windows_are_none(self):
        # Data from 2009 to 2013 only: the 2006-2009 window has no bars,
        # 2012-2015 has a full year's worth.
        frame = _frame(np.linspace(100, 200, 4 * 365 + 1), "2009-01-01")
        with prices({"A": frame}):
            got = validation.hold_by_window(["A"], end_ts=TS("2013-01-01"))
        self.assertEqual(list(got), ["2006-2009", "2009-2012", "2012-2015"])
        self.assertIsNone(got["2006-2009"])
        self.assertIsNotNone(got["2009-2012"])
        self.assertIsNotNone(got["2012-2015"])

    def test_window_value_matches_buy_and_hold_over_that_span(self):
        frame = _frame(np.linspace(100, 300, 6 * 365 + 2), "2006-01-01")
        with prices({"A": frame}):
            got = validation.hold_by_window(["A"], end_ts=TS("2012-01-01"))
            direct = validation.buy_and_hold(["A"], start_year=2009, end_ts=TS("2012-01-01"))
        self.assertAlmostEqual(got["2009-2012"], direct, places=9)


# --------------------------------------------------------- walk-forward ----
class WalkForwardCalendar(unittest.TestCase):
    """One fixed calendar shared by every strategy, a win only when the rule
    beat holding the stocks over the same window (audit A10)."""

    def _trades(self, n, start, days_apart=30, gain=20.0):
        out = []
        for i in range(n):
            entry = TS(start) + pd.Timedelta(days=i * days_apart)
            t = _trade(entry, entry + pd.Timedelta(days=5), 0.0, 100.0,
                       symbol="AAA" if i % 2 == 0 else "BBB")
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 100.0 + gain / 10, 10
            out.append(t)
        return out

    def test_windows_are_the_fixed_calendar_with_iso_dates(self):
        windows = validation.walk_forward_windows(TS("2026-09-05"))
        self.assertEqual([w["label"] for w in windows],
                         ["2006-2009", "2009-2012", "2012-2015", "2015-2018",
                          "2018-2021", "2021-2024", "2024-2027"])
        self.assertEqual(windows[-1]["to"], TS("2026-09-05"))       # never 2027
        self.assertFalse(windows[-1]["partial"])                     # 2.7 years of data
        short = validation.walk_forward_windows(TS("2025-06-01"))
        self.assertTrue(short[-1]["partial"])                        # 1.4 years

    def test_win_means_beating_hold_and_partial_windows_are_not_counted(self):
        # 36 trades a month apart from 2009: covers 2009-2012 fully and
        # 2012-2015 with only ~1 year of data if the data ends 2013-01-01.
        trades = self._trades(48, "2009-01-15")
        hold = {"2009-2012": 0.5, "2012-2015": 500.0}
        with support.account():
            out = validation.walk_forward(trades, hold=hold, end_ts=TS("2013-01-01"))
        by_label = {f"{w['from'][:4]}-{w['to'][:4]}": w for w in out["windows"]}
        self.assertEqual(out["windows"][0]["from"], "2006-01-01")
        self.assertIsNone(out["windows"][0]["cagr"])                 # no trades: not counted
        w1 = by_label["2009-2012"]
        self.assertTrue(w1["win"])                                   # beat a 0.5% hold
        self.assertFalse(w1["partial"])
        w2 = out["windows"][-1]
        self.assertEqual(w2["to"], "2013-01-01")
        self.assertTrue(w2["partial"])
        self.assertEqual(out["total_windows"], 1)
        self.assertEqual(out["wins"], 1)

    def test_losing_to_hold_is_not_a_win_even_when_positive(self):
        trades = self._trades(36, "2009-01-15")
        with support.account():
            out = validation.walk_forward(trades, hold={"2009-2012": 1e6}, end_ts=TS("2012-01-01"))
        w = [w for w in out["windows"] if w["from"] == "2009-01-01"][0]
        self.assertGreater(w["cagr"], 0)
        self.assertFalse(w["win"])
        self.assertEqual(out["wins"], 0)

    def test_under_twenty_trades_has_no_cagr_and_is_not_counted(self):
        trades = self._trades(10, "2009-01-15")
        with support.account():
            out = validation.walk_forward(trades, hold={"2009-2012": 0.0}, end_ts=TS("2012-01-01"))
        w = [w for w in out["windows"] if w["from"] == "2009-01-01"][0]
        self.assertIsNone(w["cagr"])
        self.assertIsNone(w["win"])
        self.assertEqual(out["total_windows"], 0)

    def test_without_a_benchmark_nothing_is_counted(self):
        trades = self._trades(36, "2009-01-15")
        with support.account():
            out = validation.walk_forward(trades, end_ts=TS("2012-01-01"))
        self.assertEqual(out["total_windows"], 0)
        self.assertEqual(out["wins"], 0)


class ScenarioKey(unittest.TestCase):
    """web/dashboard.html's scenarioKey() must build the IDENTICAL string --
    Python's %g renders Rs1 crore as "1e+07", JS never does at this scale."""

    def test_uses_plain_int_not_percent_g(self):
        self.assertEqual(validation.scenario_key("all", "liquidity", 10_000_000),
                         "all|liquidity|10000000")
        self.assertEqual(validation.scenario_key("large", "tight", 200_000),
                         "large|tight|200000")

    def test_accepts_float_capital_too(self):
        self.assertEqual(validation.scenario_key("all", "liquidity", 200_000.0),
                         "all|liquidity|200000")


class WalkForwardGrid(unittest.TestCase):
    def _trades(self, n=60, start="2010-01-01", days_apart=30):
        out = []
        for i in range(n):
            entry = TS(start) + pd.Timedelta(days=i * days_apart)
            t = _trade(entry, entry + pd.Timedelta(days=5), 0.0, 100.0,
                       symbol="AAA" if i % 2 == 0 else "BBB")
            net = 200.0 if i % 2 == 0 else -50.0      # AAA wins, BBB loses
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 100.0 + net / 10, 10
            out.append(t)
        return out

    def test_every_combination_gets_a_key_in_the_contract_shape(self):
        trades = self._trades()
        hold = {"all": {"2009-2012": 0.0, "2012-2015": 0.0}, "aaa_only": {"2009-2012": 0.0}}
        with support.account():
            out = validation.walk_forward_grid(
                trades, {"all": None, "aaa_only": {"AAA"}},
                ["liquidity", "tight"], [200_000, 10_000_000], hold)
        self.assertEqual(len(out), 2 * 2 * 2)
        for uni in ("all", "aaa_only"):
            for prio in ("liquidity", "tight"):
                for cap in (200_000, 10_000_000):
                    row = out[validation.scenario_key(uni, prio, cap)]
                    self.assertEqual(set(row), {"wins", "total_windows", "windows"})
                    for w in row["windows"]:
                        self.assertEqual(set(w), {"from", "to", "cagr", "hold", "win", "partial"})

    def test_universe_filter_actually_filters(self):
        trades = self._trades()
        hold = {"all": {"2009-2012": 0.0}, "bbb_only": {"2009-2012": 0.0}}
        with support.account():
            out = validation.walk_forward_grid(
                trades, {"all": None, "bbb_only": {"BBB"}}, ["liquidity"], [200_000], hold)
        all_key = validation.scenario_key("all", "liquidity", 200_000)
        bbb_key = validation.scenario_key("bbb_only", "liquidity", 200_000)
        self.assertNotEqual(out[all_key]["windows"], out[bbb_key]["windows"])


# ------------------------------------------------------- credibility ----
class ClusteredStandardError(unittest.TestCase):
    """Liang-Zeger with the G/(G-1) factor, against a hand computation."""

    def test_hand_case(self):
        # r = [1, 1, -1, -1, 2, 0] in three quarters of two. mean = 1/3;
        # residuals 2/3, 2/3, -4/3, -4/3, 5/3, -1/3; cluster sums 4/3, -8/3,
        # 4/3; sum of squares 96/9; var = (3/2) x (96/9) / 36 = 4/9; se = 2/3.
        r = np.array([1.0, 1.0, -1.0, -1.0, 2.0, 0.0])
        q = np.array([1, 1, 2, 2, 3, 3])
        se, g = validation._cluster_se(r, q)
        self.assertEqual(g, 3)
        self.assertAlmostEqual(se, 2 / 3, places=12)

    def test_one_cluster_falls_back_to_iid(self):
        r = np.array([1.0, 2.0, 3.0, 4.0])
        se, g = validation._cluster_se(r, np.zeros(4, dtype=int))
        self.assertEqual(g, 1)
        self.assertAlmostEqual(se, r.std(ddof=1) / 2, places=12)

    def test_clustered_t_on_trades(self):
        # Ten trades per quarter in three quarters, quarters winning 1R,
        # losing 1R, and flat: cluster sums are 10 x (1 - 0), 10 x (-1 - 0),
        # 0 -> sum of squares 200; var = 1.5 x 200 / 900 = 1/3.
        trades = []
        for q, r in ((1, 1.0), (4, -1.0), (7, 0.0)):
            for i in range(10):
                trades.append(_trade(f"2020-{q:02d}-{i + 1:02d}", f"2020-{q:02d}-{i + 2:02d}",
                                     r * 100.0, 100.0))
        got = validation.clustered_t(trades)
        self.assertEqual(got["n"], 30)
        self.assertEqual(got["n_clusters"], 3)
        self.assertAlmostEqual(got["mean_r"], 0.0)
        self.assertAlmostEqual(got["se_r"], round((1 / 3) ** 0.5, 4))
        self.assertEqual(got["t_stat"], 0.0)
        self.assertIsNone(validation.clustered_t(trades[:29]))


class DriftTerm(unittest.TestCase):
    """random_entry_r: random entries on a straight-line uptrend must earn
    positive R with the twin's stop distance and holding period."""

    def _uptrend(self, n=800):
        close = 100.0 * 1.001 ** np.arange(n)
        return _frame(close, "2015-01-01", low_frac=0.995)

    def _trades(self, n=40):
        out = []
        for i in range(n):
            entry = TS("2016-06-01") + pd.Timedelta(days=5 * i)
            t = _trade(entry, entry + pd.Timedelta(days=20), 100.0 + 50.0 * (i % 3), 300.0,
                       symbol="UP")
            t["entry_price"], t["stop"], t["shares"] = 100.0, 97.0, 100
            out.append(t)
        return out

    def test_random_entries_on_an_uptrend_earn_positive_r(self):
        with prices({"UP": self._uptrend()}):
            rand = validation.random_entry_r(self._trades(), draws=5, seed=1)
        self.assertEqual(len(rand), 40 * 5)
        # 20 sessions at +0.1%/day is +2% on a 3% stop: ~0.67R gross, less
        # Zerodha delivery charges; the stop (3% below) is never reached
        # because each low sits only 0.5% under a rising close.
        self.assertGreater(rand.mean(), 0.4)
        self.assertLess(rand.mean(), 0.67)

    def test_stopped_out_random_entries_lose_about_one_r(self):
        # Every low is 10% under its close: any hold of one session or more
        # is stopped at 3% below entry, at the OPEN (the session gaps
        # through the stop), so R is well under -1.
        close = np.full(800, 100.0)
        frame = _frame(close, "2015-01-01", low_frac=0.90)
        frame["open"] = close * 0.95
        with prices({"UP": frame}):
            rand = validation.random_entry_r(self._trades(), draws=3, seed=1)
        self.assertLess(rand.max(), -1.0)

    def test_no_price_file_means_no_drift_and_bootstrap_one_still_runs(self):
        with no_prices():
            rand = validation.random_entry_r(self._trades())
            row = validation.bootstrap_one(self._trades(), draws=100)
        self.assertEqual(len(rand), 0)
        self.assertIsNone(row["drift_r"])
        self.assertEqual(row["t_stat"], row["t_cluster"])

    def test_gate_statistic_subtracts_the_drift(self):
        with prices({"UP": self._uptrend()}):
            row = validation.bootstrap_one(self._trades(), draws=100, seed=1)
        self.assertIsNotNone(row["drift_r"])
        # the row's fields are rounded to 4 places, so recomputing from them
        # reproduces t_stat only to within that rounding
        self.assertAlmostEqual(row["t_stat"], (row["mean_r"] - row["drift_r"]) / row["se_r"],
                               delta=abs(row["t_stat"]) * 0.02)
        self.assertLess(row["t_stat"], row["t_cluster"])


class ValidationSummaryFloor(unittest.TestCase):
    """The 30-trade floor: a thin universe (scripts.preflight's 3 symbols)
    degrades to a missing cell rather than a crash."""

    def test_empty_trades_return_none(self):
        self.assertIsNone(
            validation.validation_summary(None, [], ["AAA"], np.random.default_rng(0)))

    def test_below_min_trades_returns_none(self):
        trades = [_trade(f"2020-01-{i:02d}", f"2020-01-{i + 1:02d}", 10.0, 5.0)
                  for i in range(1, validation.MIN_TRADES - 1)]
        self.assertIsNone(
            validation.validation_summary(None, trades, ["AAA"], np.random.default_rng(0)))


class BootstrapOneShape(unittest.TestCase):
    """Pure math on trade dicts -- no price files (no_prices), no account."""

    def _trades(self, n, span_days=2000, seed=0):
        # Random wins/losses with a fixed positive edge, spaced over a FIXED
        # calendar span so that "more trades" is not also "more quarters".
        rng = np.random.default_rng(seed)
        out = []
        step = max(span_days / n, 0.01)
        for i in range(n):
            entry = TS("2010-01-01") + pd.Timedelta(days=i * step)
            r = 2.0 if rng.random() < 0.45 else -1.0
            out.append(_trade(entry, entry + pd.Timedelta(hours=1), r * 100.0, 100.0))
        return out

    def test_shape_and_bounds(self):
        with no_prices():
            row = validation.bootstrap_one(self._trades(60), draws=200)
        for key in ("n", "n_clusters", "mean_r", "drift_r", "se_r", "t_iid", "t_cluster",
                    "t_stat", "p05_mean_r", "p50_mean_r", "p95_mean_r", "p_neg", "cleared_95",
                    "obs_cagr", "obs_mar", "p05", "p50", "p95", "dd05"):
            self.assertIn(key, row)
        self.assertEqual(row["n"], 60)
        self.assertGreater(row["n_clusters"], 10)
        self.assertLessEqual(row["p05_mean_r"], row["p50_mean_r"])
        self.assertLessEqual(row["p50_mean_r"], row["p95_mean_r"])
        self.assertLessEqual(row["p05"], row["p50"])
        self.assertLessEqual(row["p50"], row["p95"])
        self.assertGreaterEqual(row["dd05"], -100.0)
        self.assertLessEqual(row["dd05"], 0.0)
        self.assertIsInstance(row["cleared_95"], bool)
        json.dumps(row)

    def test_below_floor_returns_none(self):
        self.assertIsNone(validation.bootstrap_one(self._trades(10)))

    def test_a_reliable_edge_clears_the_95_bar(self):
        trades = self._trades(60)
        for i, t in enumerate(trades):
            t["net_profit"] = 200.0 if i % 2 else 50.0   # every trade wins: 2R or 0.5R
        with no_prices():
            row = validation.bootstrap_one(trades, draws=200)
        self.assertTrue(row["cleared_95"])
        self.assertGreater(row["p05_mean_r"], 0)
        self.assertGreater(row["t_stat"], 0)

    def test_t_stat_does_not_scale_up_with_trade_count_alone(self):
        """Ten times the trades, same edge, same quarters: the cluster-robust
        t grows with sqrt(n) at most, while compounded CAGR explodes."""
        with no_prices():
            short = validation.bootstrap_one(self._trades(60), draws=200)
            long_ = validation.bootstrap_one(self._trades(600), draws=200)
        self.assertLess(long_["t_cluster"] / short["t_cluster"], 5.0)
        self.assertGreater(long_["obs_cagr"], short["obs_cagr"] * 5)

    def test_clustered_error_exceeds_iid_when_quarters_move_together(self):
        """Whole quarters winning or losing together is what the old error
        could not see: every trade in a quarter carries the quarter's sign."""
        out = []
        for q in range(16):
            sign = 1.0 if q % 2 == 0 else -1.0
            for i in range(10):
                entry = TS("2010-01-01") + pd.DateOffset(months=3 * q) + pd.Timedelta(days=i)
                out.append(_trade(entry, entry + pd.Timedelta(days=1), sign * 100.0 + 10.0, 100.0))
        with no_prices():
            row = validation.bootstrap_one(out, draws=200)
        self.assertGreater(row["se_r"] * (row["t_iid"] / row["t_cluster"]), row["se_r"])
        self.assertLess(row["t_cluster"], row["t_iid"] / 2)


class QuarterBootstrap(unittest.TestCase):
    def test_single_quarter_reproduces_the_observed_path_exactly(self):
        """With one quarter every draw is the real sequence, so the vectorised
        block arithmetic must give the same CAGR and drawdown as paths()."""
        rng = np.random.default_rng(0)
        r = rng.normal(0.2, 1.5, size=200)
        q = np.zeros(200, dtype=int)
        boot = validation.quarter_bootstrap(r, q, 3.0, 0.01, 50, rng)
        cagr, dd, mar = validation.paths(r[None, :], 0.01, 3.0)
        self.assertTrue(np.allclose(boot["cagr"], cagr[0]))
        self.assertTrue(np.allclose(boot["dd"], dd[0]))
        self.assertTrue(np.allclose(boot["mean_r"], r.mean()))

    def test_drawdown_matches_brute_force_over_concatenated_quarters(self):
        rng = np.random.default_rng(1)
        r = rng.normal(0.0, 2.0, size=120)
        q = np.repeat(np.arange(6), 20)
        draws = 200
        boot = validation.quarter_bootstrap(r, q, 2.0, 0.02, draws, np.random.default_rng(7))
        pick = np.random.default_rng(7).integers(0, 6, size=(draws, 6))
        for d in range(draws):
            seq = np.concatenate([r[q == k] for k in pick[d]])
            cagr, dd, _ = validation.paths(seq[None, :], 0.02, 2.0)
            self.assertAlmostEqual(boot["dd"][d], dd[0], places=8)
            self.assertAlmostEqual(boot["cagr"][d], cagr[0], places=6)
            self.assertAlmostEqual(boot["mean_r"][d], seq.mean(), places=10)


# ----------------------------------------------------------- the hurdle ----
class LuckHurdle(unittest.TestCase):
    def test_two_point_four_one_at_six_point_two_effective_trials(self):
        self.assertAlmostEqual(validation.luck_hurdle(6.2), 2.41, places=2)
        self.assertAlmostEqual(validation.luck_hurdle(6.2),
                               NormalDist().inv_cdf(1 - 0.05 / 6.2), places=12)
        self.assertEqual(validation.ALPHA, 0.05)

    def test_one_trial_is_the_plain_one_sided_bar(self):
        self.assertAlmostEqual(validation.luck_hurdle(1.0), 1.645, places=3)


class MultipleTestingSummary(unittest.TestCase):
    def _independent_monthly(self, keys, seed=0):
        rng = np.random.default_rng(seed)
        months = [pd.Period(f"2020-{m:02d}") for m in range(1, 13)]
        return {k: {mo: float(v) for mo, v in zip(months, rng.normal(size=len(months)))}
                for k in keys}

    def test_counts_and_hurdle_are_internally_consistent(self):
        rows = [{"key": f"rule{i}", "t_stat": float(i) - 4} for i in range(12)]
        monthly = self._independent_monthly([r["key"] for r in rows])
        out = validation.multiple_testing_summary(rows, monthly)
        self.assertEqual(out["tried"], 12)
        self.assertEqual(out["alpha"], 0.05)
        self.assertEqual(out["cleared"], sum(1 for r in rows if r["t_stat"] > out["hurdle"]))
        self.assertAlmostEqual(out["expected_by_chance"], 12 * 0.05, places=1)
        self.assertAlmostEqual(out["hurdle"], validation.luck_hurdle(out["n_eff"]), delta=0.02)
        self.assertAlmostEqual(out["expected_best"],
                               validation.expected_best_of(out["n_eff"], 1.0), delta=0.02)
        self.assertGreater(out["hurdle"], out["expected_best"])
        self.assertEqual(out["best_key"], "rule11")
        self.assertGreater(out["n_eff"], 5.0)

    def test_no_rows_is_none(self):
        self.assertIsNone(validation.multiple_testing_summary([], {}))
        self.assertIsNone(validation.multiple_testing_summary(
            [{"key": "x", "t_stat": None}], {}))

    def test_missing_monthly_data_falls_back_to_treating_trials_as_independent(self):
        rows = [{"key": f"rule{i}", "t_stat": 2.0} for i in range(10)]
        out = validation.multiple_testing_summary(rows, None)
        self.assertEqual(out["n_eff"], 10.0)
        self.assertAlmostEqual(out["hurdle"], validation.luck_hurdle(10.0), places=2)


class EffectiveTrials(unittest.TestCase):
    def test_uncorrelated_variants_are_worth_themselves(self):
        months = pd.period_range("2010-01", periods=96, freq="M")
        rng = np.random.default_rng(0)
        monthly = {f"k{i}": {mo: float(v) for mo, v in zip(months, rng.normal(size=len(months)))}
                   for i in range(6)}
        self.assertAlmostEqual(validation.effective_trials(monthly), 6.0, delta=1.0)

    def test_highly_correlated_variants_are_worth_much_less_than_their_count(self):
        months = pd.period_range("2010-01", periods=96, freq="M")
        rng = np.random.default_rng(0)
        base = rng.normal(size=len(months))
        monthly = {f"k{i}": {mo: float(v) + 0.01 * rng.normal() for mo, v in zip(months, base)}
                   for i in range(6)}
        eff = validation.effective_trials(monthly)
        self.assertLess(eff, 2.0)
        self.assertGreaterEqual(eff, 1.0)

    def test_a_mixed_board_lands_between_the_extremes(self):
        months = pd.period_range("2010-01", periods=96, freq="M")
        rng = np.random.default_rng(1)
        cluster_a = rng.normal(size=len(months))
        cluster_b = rng.normal(size=len(months))
        monthly = {}
        for i in range(4):
            monthly[f"a{i}"] = {mo: float(v) + 0.05 * rng.normal()
                                for mo, v in zip(months, cluster_a)}
        for i in range(4):
            monthly[f"b{i}"] = {mo: float(v) + 0.05 * rng.normal()
                                for mo, v in zip(months, cluster_b)}
        eff = validation.effective_trials(monthly)
        self.assertGreater(eff, 1.5)
        self.assertLess(eff, 5.0)


class CorrelationSummary(unittest.TestCase):
    def test_finds_the_most_correlated_other_key(self):
        months = [pd.Period(f"2020-{m:02d}") for m in range(1, 13)]
        a = {mo: float(i) for i, mo in enumerate(months)}
        b = {mo: float(i) for i, mo in enumerate(months)}
        c = {mo: float(-i) for i, mo in enumerate(months)}
        out = validation.correlation_summary({"a": a, "b": b, "c": c})
        self.assertEqual(out["a"]["key"], "b")
        self.assertAlmostEqual(out["a"]["r"], 1.0, places=6)

    def test_too_few_shared_months_is_none(self):
        out = validation.correlation_summary(
            {"a": {pd.Period("2020-01"): 1.0}, "b": {pd.Period("2020-02"): 1.0}})
        self.assertIsNone(out["a"])


# ---------------------------------------------------------- permutation ----
class _SpikeStrategy:
    """Buys at bar 10's close and sells at bar 50's close of whatever
    frames.daily serves. The real frame has its whole gain on bar 50, so
    any permutation that moves that one return past bar 50 earns nothing."""
    cache = "spike"

    def build(self, symbol):
        day = frames.daily(symbol)
        t = support.trade(symbol=symbol, entry=day["ts"].iloc[10], exit_=day["ts"].iloc[50],
                          entry_price=float(day["close"].iloc[10]),
                          exit_price=float(day["close"].iloc[50]))
        t["shares"] = 10
        t["gross_profit"] = (t["exit_price"] - t["entry_price"]) * 10
        t["net_profit"] = t["gross_profit"]
        t["risk_taken"] = 100.0
        return [t]


def _spike_frame():
    close = np.full(300, 100.0)
    close[50:] = 130.0
    return _frame(close, "2019-01-01")


class PermutationTest(unittest.TestCase):
    def _pool(self, names):
        return {n: _spike_frame() for n in names}

    def test_p_arithmetic_and_verdict(self):
        names = ["A", "B", "C"]
        strat = _SpikeStrategy()
        with prices(self._pool(names)), support.account():
            real = [t for n in names for t in strat.build(n)]
            observed, shuffled, pool = validation.permutation_test(
                strat, names, 9, np.random.default_rng(3), trades=real)
            summary = validation._permutation_summary(
                strat, names, np.random.default_rng(3), 9, trades=real)
        self.assertEqual(pool, 3)
        self.assertEqual(len(shuffled), 9)
        worse = sum(1 for g in shuffled if g >= observed)
        self.assertEqual(summary["p"], round((worse + 1) / 10, 4))
        self.assertEqual(summary["distinguishable"], summary["p"] <= 0.05)
        self.assertEqual(summary["rounds"], 9)
        self.assertEqual(summary["pool"], 3)
        # the spike lands in the 40-bar window after bar 10 in ~13% of
        # permutations, so most rounds are worse than the real market
        self.assertLess(worse, 9)

    def test_same_seed_same_answer_and_pool_is_drawn_by_the_rng(self):
        names = [f"S{i:02d}" for i in range(40)]
        strat = _SpikeStrategy()
        with prices(self._pool(names)), support.account():
            real = [t for n in names for t in strat.build(n)]
            a = validation.permutation_test(strat, names, 3, np.random.default_rng(5),
                                            sample=5, trades=real)
            b = validation.permutation_test(strat, names, 3, np.random.default_rng(5),
                                            sample=5, trades=real)
        self.assertEqual(a, b)
        self.assertEqual(a[2], 5)
        # Not the first five alphabetically: with 40 names the chance that a
        # random 5-sample is exactly S00..S04 is one in 658,008.
        drawn = sorted(str(s) for s in np.random.default_rng(5).choice(sorted(names), 5, replace=False))
        self.assertNotEqual(drawn, sorted(names)[:5])

    def test_shuffled_side_is_built_without_the_spread_and_state_is_restored(self):
        """apply_spread runs once, on a list built with slippage.ENABLED off --
        the way every real cache is built -- and every patched global comes
        back whatever happened inside."""
        from kitelab import slippage
        seen = []
        strat = _SpikeStrategy()
        original_build = strat.build

        def spying_build(symbol):
            seen.append(slippage.ENABLED)
            return original_build(symbol)
        strat.build = spying_build
        saved_daily = frames.daily
        with prices(self._pool(["A", "B"])), support.account(spread=True):
            validation.permutation_test(strat, ["A", "B"], 2, np.random.default_rng(0),
                                        trades=[])
            self.assertTrue(slippage.ENABLED)
        self.assertTrue(seen == [] or not any(seen))
        self.assertIs(frames.daily, saved_daily)

    def test_bypasses_load_and_filters_to_the_pool(self):
        aaa = _trade("2020-01-01", "2020-01-05", 100.0, 50.0, symbol="AAA")
        zzz = _trade("2020-01-01", "2020-01-05", 100.0, 50.0, symbol="ZZZ")

        def boom(*a, **k):
            raise AssertionError("load() must not be called when trades= is given")
        saved = validation.load
        validation.load = boom
        try:
            with support.account():
                observed, shuffled, pool = validation.permutation_test(
                    None, ["AAA"], 0, np.random.default_rng(0), sample=60, trades=[aaa, zzz])
        finally:
            validation.load = saved
        self.assertEqual(shuffled, [])
        self.assertEqual(pool, 1)
        self.assertIsNotNone(observed)


class PermutedDaily(unittest.TestCase):
    def _real(self, frames_by_symbol):
        return lambda symbol, *a, **k: frames_by_symbol[symbol]

    def test_preserves_terminal_price_and_the_multiset_of_returns(self):
        rng = np.random.default_rng(0)
        close = 100 * np.cumprod(1 + rng.normal(0, 0.02, size=300))
        frame = _frame(close, "2019-01-01")
        real = self._real({"A": frame})
        calendar = validation._calendar(["A"], real)
        perm = np.random.default_rng(1).permutation(len(calendar))
        out = validation.permuted_daily("A", perm, calendar, np.random.default_rng(2), real)
        self.assertAlmostEqual(float(out["close"].iloc[-1]), float(close[-1]), places=6)
        self.assertAlmostEqual(float(out["close"].iloc[0]), float(close[0]), places=9)
        a = np.sort(np.diff(close) / close[:-1])
        b = np.sort(np.diff(out["close"].to_numpy()) / out["close"].to_numpy()[:-1])
        self.assertTrue(np.allclose(a, b))
        # OHLC keep their shape relative to the close
        self.assertTrue(np.allclose(out["low"] / out["close"], frame["low"] / frame["close"]))

    def test_joint_permutation_moves_two_stocks_together(self):
        """Same calendar, same permutation: a session's return lands on the
        same new date for both stocks, so the cross-section survives."""
        rng = np.random.default_rng(0)
        base = rng.normal(0, 0.02, size=300)
        a = _frame(100 * np.cumprod(1 + base), "2019-01-01")
        b = _frame(50 * np.cumprod(1 + base + 0.001), "2019-01-01")
        real = self._real({"A": a, "B": b})
        calendar = validation._calendar(["A", "B"], real)
        perm = np.random.default_rng(1).permutation(len(calendar))
        pa = validation.permuted_daily("A", perm, calendar, np.random.default_rng(2), real)
        pb = validation.permuted_daily("B", perm, calendar, np.random.default_rng(3), real)
        ra = np.diff(pa["close"].to_numpy()) / pa["close"].to_numpy()[:-1]
        rb = np.diff(pb["close"].to_numpy()) / pb["close"].to_numpy()[:-1]
        self.assertTrue(np.allclose(rb - ra, 0.001))

    def test_late_listed_stock_keeps_its_own_returns(self):
        rng = np.random.default_rng(0)
        a = _frame(100 * np.cumprod(1 + rng.normal(0, 0.02, size=300)), "2019-01-01")
        late = 100 * np.cumprod(1 + rng.normal(0, 0.02, size=120))
        b = _frame(late, "2019-07-01")
        real = self._real({"A": a, "B": b})
        calendar = validation._calendar(["A", "B"], real)
        perm = np.random.default_rng(1).permutation(len(calendar))
        pb = validation.permuted_daily("B", perm, calendar, np.random.default_rng(2), real)
        self.assertEqual(len(pb), 120)
        x = np.sort(np.diff(late) / late[:-1])
        y = np.sort(np.diff(pb["close"].to_numpy()) / pb["close"].to_numpy()[:-1])
        self.assertTrue(np.allclose(x, y))
        self.assertAlmostEqual(float(pb["close"].iloc[-1]), float(late[-1]), places=6)


# --------------------------------------------------------- payload shape ----
class BreakevenPayloadIsJsonSafe(unittest.TestCase):
    def _roundtrip(self, trades):
        payload = validation._breakeven_payload(trades)
        self.assertIn(payload["status"], ("negative", "robust", "finite"))
        got = json.loads(json.dumps(payload))
        self.assertEqual(got, payload)
        return payload

    def test_an_already_losing_list_is_negative_status(self):
        trades = [_trade(f"2018-01-{i:02d}", f"2018-01-{i + 1:02d}", -10.0, 100.0)
                  for i in range(1, 5)]
        for t in trades:
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 90.0, 1
        with support.account():
            payload = self._roundtrip(trades)
        self.assertEqual(payload["status"], "negative")
        self.assertIsNone(payload["bp"])

    def test_a_very_cheap_edge_survives_200bp(self):
        trades = [_trade(f"2018-{m:02d}-01", f"2018-{m:02d}-20", 500.0, 100.0)
                  for m in range(1, 5)]
        for t in trades:
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 200.0, 10
        with support.account():
            payload = self._roundtrip(trades)
        self.assertEqual(payload["status"], "robust")
        self.assertIsNone(payload["bp"])


class FixedChecksByUniverse(unittest.TestCase):
    """Credibility, distinguishable and breakeven_margin, live per Universe.
    permutation_rounds=0 throughout: these cover the Universe-filtering and
    MIN_TRADES-floor plumbing, not the shuffled-price simulation."""

    def _trades(self, n=80):
        out = []
        for i in range(n):
            entry = TS("2010-01-01") + pd.Timedelta(days=i * 7)
            symbol = "AAA" if i % 2 == 0 else "BBB"
            net = 20.0 if symbol == "AAA" else -19.0
            t = _trade(entry, entry + pd.Timedelta(days=3), net, 10.0, symbol=symbol)
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 100.0 + net / 10, 10
            out.append(t)
        return out

    def test_keyed_by_universe_only_with_all_three_checks(self):
        trades = self._trades()
        with support.account(), no_prices():
            out = validation.fixed_checks_by_universe(
                None, trades, ["AAA", "BBB"], {"all": None, "aaa_only": {"AAA"}},
                np.random.default_rng(0), permutation_rounds=0)
        self.assertEqual(set(out), {"all", "aaa_only"})
        for row in out.values():
            self.assertIn("credibility", row)
            self.assertIn("breakeven", row)
            self.assertIn("fixed_gates", row)
            self.assertIn("breakeven_margin", row["fixed_gates"])
            self.assertIn("t_stat", row["credibility"])

    def test_universe_filter_actually_filters_credibility_and_breakeven(self):
        trades = self._trades()
        with support.account(), no_prices():
            out = validation.fixed_checks_by_universe(
                None, trades, ["AAA", "BBB"], {"all": None, "aaa_only": {"AAA"}},
                np.random.default_rng(0), permutation_rounds=0)
        self.assertNotEqual(out["all"]["credibility"]["t_stat"],
                             out["aaa_only"]["credibility"]["t_stat"])
        self.assertNotEqual(out["all"]["breakeven"], out["aaa_only"]["breakeven"])

    def test_below_floor_universe_is_simply_absent(self):
        trades = self._trades(n=validation.MIN_TRADES)
        with support.account(), no_prices():
            out = validation.fixed_checks_by_universe(
                None, trades, ["AAA", "BBB"], {"all": None, "aaa_only": {"AAA"}},
                np.random.default_rng(0), permutation_rounds=0)
        self.assertIn("all", out)
        self.assertNotIn("aaa_only", out)


class ValidationSummaryShape(unittest.TestCase):
    def test_no_hold_cagr_key_and_the_ignored_kwarg_is_tolerated(self):
        trades = FixedChecksByUniverse()._trades()
        with support.account(), no_prices():
            out = validation.validation_summary(None, trades, ["AAA", "BBB"],
                                                np.random.default_rng(0),
                                                permutation_rounds=0, hold_cagr=12.0)
        self.assertNotIn("hold_cagr", out)
        self.assertEqual(set(out), {"n", "top_n", "breakeven", "bootstrap", "permutation",
                                    "fixed_gates", "monthly"})


if __name__ == "__main__":
    unittest.main()

"""kitelab.validation: the dashboard's "is it real" numbers.

Not a re-check of scripts/validate.py or scripts/bootstrap.py's own math --
that math moved here unchanged (see kitelab/validation.py's module docstring)
and those two scripts are still exercised by running them directly. These
tests cover the two things this module adds: the shape
validation_summary()/correlation_summary()/multiple_testing_summary() hand
scripts.dashboard_data, and the guards that keep a small or degenerate
universe from crashing a rebuild instead of producing an empty cell.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from kitelab import validation
from tests import support

TS = pd.Timestamp


def _trade(entry, exit_, net_profit, risk_taken):
    t = support.trade(entry=entry, exit_=exit_)
    t["net_profit"] = net_profit
    t["risk_taken"] = risk_taken
    t["gross_profit"] = net_profit
    t["charges"] = 0.0
    return t


class ValidationSummaryFloor(unittest.TestCase):
    """The 30-trade floor scripts.bootstrap already applies, reused here so
    a thin universe (scripts.preflight's 3 symbols, a new candidate stock,
    an asset with few signals) degrades to a missing cell rather than a
    crash or a resampled distribution built on noise."""

    def test_empty_trades_return_none(self):
        self.assertIsNone(
            validation.validation_summary(None, [], ["AAA"], np.random.default_rng(0)))

    def test_below_min_trades_returns_none(self):
        trades = [_trade(f"2020-01-{i:02d}", f"2020-01-{i + 1:02d}", 10.0, 5.0)
                  for i in range(1, validation.MIN_TRADES - 1)]
        self.assertIsNone(
            validation.validation_summary(None, trades, ["AAA"], np.random.default_rng(0)))


class BootstrapOneShape(unittest.TestCase):
    """Pure math on trade dicts -- no price files, no portfolio engine.

    t_stat/mean_r are the credibility figures the page actually sorts by
    (added 2026-09-04, replacing a compounded-CAGR percentile that
    correlated 0.82 with raw trade count instead of edge quality); obs_cagr/
    p05/p50/p95 are kept as Detail-view context and checked too."""

    def _trades(self, n, win_net=200.0, win_risk=100.0, loss_net=-100.0, loss_risk=100.0,
                span_days=2000):
        # Spaced to fill a FIXED calendar span regardless of n: span_years()
        # depends on entry/exit dates, not trade count, so packing more
        # trades into the same window isolates "more trades" from "more
        # elapsed time" -- the two are conflated in real trade lists (a rule
        # that fires more often also usually has a longer live history), but
        # the point of this test is to isolate the trade-count effect alone.
        out = []
        step = max(span_days / n, 0.01)
        for i in range(n):
            entry = TS("2010-01-01") + pd.Timedelta(days=i * step)
            exit_ = entry + pd.Timedelta(hours=1)
            net, risk = (win_net, win_risk) if i % 2 == 0 else (loss_net, loss_risk)
            out.append(_trade(entry, exit_, net, risk))
        return out

    def test_shape_and_bounds(self):
        row = validation.bootstrap_one(self._trades(60), draws=200)
        self.assertIsNotNone(row)
        for key in ("n", "mean_r", "se_r", "t_stat", "p05_mean_r", "p50_mean_r",
                    "p95_mean_r", "p_neg", "cleared_95",
                    "obs_cagr", "obs_mar", "p05", "p50", "p95", "dd05"):
            self.assertIn(key, row)
        self.assertEqual(row["n"], 60)
        self.assertLessEqual(row["p05_mean_r"], row["p50_mean_r"])
        self.assertLessEqual(row["p50_mean_r"], row["p95_mean_r"])
        self.assertLessEqual(row["p05"], row["p50"])
        self.assertLessEqual(row["p50"], row["p95"])
        self.assertGreaterEqual(row["dd05"], -100.0)
        self.assertLessEqual(row["dd05"], 0.0)
        self.assertIsInstance(row["cleared_95"], bool)

    def test_below_floor_returns_none(self):
        self.assertIsNone(validation.bootstrap_one(self._trades(10)))

    def test_a_reliable_edge_clears_the_95_bar(self):
        """Every trade wins 2R with no losers -- the 5th percentile of any
        resample of an all-winning list must be positive."""
        row = validation.bootstrap_one(self._trades(60, loss_net=50.0), draws=200)
        self.assertTrue(row["cleared_95"])
        self.assertGreater(row["p05_mean_r"], 0)
        self.assertGreater(row["t_stat"], 0)

    def test_t_stat_does_not_scale_up_with_trade_count_alone(self):
        """The whole reason t_stat replaced compounded CAGR: two lists with
        the SAME per-trade edge but very different lengths should score
        SIMILAR credibility, not one dwarfing the other the way compounded
        CAGR did (0.82 correlation with log(trade count), measured
        2026-09-04). t_stat should differ only mildly (more data -> a bit
        more confidence), not by orders of magnitude."""
        short = validation.bootstrap_one(self._trades(60), draws=200)
        long_ = validation.bootstrap_one(self._trades(600), draws=200)
        self.assertLess(long_["t_stat"] / short["t_stat"], 5.0)
        # Whereas the compounded CAGR (kept for Detail-view context) DOES
        # still blow up with trade count -- confirms the two are answering
        # different questions and the page must not mix them.
        self.assertGreater(long_["obs_cagr"], short["obs_cagr"] * 5)


class BreakevenPayloadIsJsonSafe(unittest.TestCase):
    """breakeven_cost() returns float('inf') for "survives 200bp+" -- a value
    json.dumps writes as the bare token `Infinity`, which is not valid JSON
    and makes the browser's JSON.parse throw on the WHOLE payload. Every
    branch of _breakeven_payload must round-trip through json.dumps/loads."""

    def _roundtrip(self, trades):
        import json
        payload = validation._breakeven_payload(trades)
        self.assertIn(payload["status"], ("negative", "robust", "finite"))
        got = json.loads(json.dumps(payload))
        self.assertEqual(got, payload)
        return payload

    def test_an_already_losing_list_is_negative_status(self):
        # breakeven_cost() reads price, not net_profit -- cagr_of() replays
        # entry/exit through portfolio.run, which does not consult the
        # R-multiple fields at all. exit below entry is what makes it a loser.
        trades = [_trade(f"2018-01-{i:02d}", f"2018-01-{i + 1:02d}", -10.0, 100.0)
                  for i in range(1, 5)]
        for t in trades:
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 90.0, 1
        with support.account():
            payload = self._roundtrip(trades)
        self.assertEqual(payload["status"], "negative")
        self.assertIsNone(payload["bp"])

    def test_a_very_cheap_edge_survives_200bp(self):
        # Entry/exit far enough apart in price that a 200bp/side charge
        # (of entry+exit value) still leaves the trade profitable.
        trades = [_trade(f"2018-{m:02d}-01", f"2018-{m:02d}-20", 500.0, 100.0)
                  for m in range(1, 5)]
        for t in trades:
            t["entry_price"], t["exit_price"], t["shares"] = 100.0, 200.0, 10
        with support.account():
            payload = self._roundtrip(trades)
        self.assertEqual(payload["status"], "robust")
        self.assertIsNone(payload["bp"])


class CorrelationSummary(unittest.TestCase):
    def test_finds_the_most_correlated_other_key(self):
        months = [pd.Period(f"2020-{m:02d}") for m in range(1, 13)]
        a = {mo: float(i) for i, mo in enumerate(months)}
        b = {mo: float(i) for i, mo in enumerate(months)}            # identical to a
        c = {mo: float(-i) for i, mo in enumerate(months)}           # inverse of a
        out = validation.correlation_summary({"a": a, "b": b, "c": c})
        self.assertEqual(out["a"]["key"], "b")
        self.assertAlmostEqual(out["a"]["r"], 1.0, places=6)

    def test_too_few_shared_months_is_none(self):
        out = validation.correlation_summary(
            {"a": {pd.Period("2020-01"): 1.0}, "b": {pd.Period("2020-02"): 1.0}})
        self.assertIsNone(out["a"])


class EffectiveTrials(unittest.TestCase):
    """The fix for the fix: a naive average-pairwise-correlation "design
    effect" collapsed to 1.8 effective trials on this project's real 24
    variants (avg correlation 0.54), which sends expected_best_of() below
    its n_trials=2 floor and makes the luck hurdle exactly 0.0 -- any
    positive t-stat then trivially "clears" it. Nyholt's eigenvalue method
    does not collapse the same way because it uses the correlation matrix's
    full structure, not just its average."""

    def test_uncorrelated_variants_are_worth_themselves(self):
        # 96 months (8 years), not 12: a correlation ESTIMATE from only 12
        # points has enough sampling noise on its own (easily |r| ~ 0.3-0.5
        # between two truly independent series) to make this test flaky --
        # this needs enough data that the estimate actually concentrates
        # near the true value of 0.
        months = pd.period_range("2010-01", periods=96, freq="M")
        rng = np.random.default_rng(0)
        monthly = {f"k{i}": {mo: float(v) for mo, v in zip(months, rng.normal(size=len(months)))}
                   for i in range(6)}
        self.assertAlmostEqual(validation.effective_trials(monthly), 6.0, delta=1.0)

    def test_highly_correlated_variants_are_worth_much_less_than_their_count(self):
        months = pd.period_range("2010-01", periods=96, freq="M")
        rng = np.random.default_rng(0)
        base = rng.normal(size=len(months))
        # six near-duplicates of the same series -- one real bet wearing six names
        monthly = {f"k{i}": {mo: float(v) + 0.01 * rng.normal() for mo, v in zip(months, base)}
                   for i in range(6)}
        eff = validation.effective_trials(monthly)
        self.assertLess(eff, 2.0)
        self.assertGreaterEqual(eff, 1.0)          # never below the floor

    def test_a_mixed_board_lands_between_the_extremes(self):
        """Two correlated clusters plus independent noise: the real shape of
        this project's 24 variants (EMA-band and Turtle families each
        correlated within themselves, weakly related to each other)."""
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
        # two clusters of four near-duplicates -> "worth" roughly two
        # independent bets, not eight and not one
        self.assertGreater(eff, 1.5)
        self.assertLess(eff, 5.0)


class MultipleTestingSummary(unittest.TestCase):
    def _months(self, n=12):
        return [pd.Period(f"2020-{m:02d}") for m in range(1, n + 1)]

    def _independent_monthly(self, keys, seed=0):
        rng = np.random.default_rng(seed)
        months = self._months()
        return {k: {mo: float(v) for mo, v in zip(months, rng.normal(size=len(months)))}
                for k in keys}

    def test_counts_and_hurdle_are_internally_consistent(self):
        rows = [{"key": f"rule{i}", "t_stat": float(i) - 4} for i in range(12)]
        monthly = self._independent_monthly([r["key"] for r in rows])
        out = validation.multiple_testing_summary(rows, monthly)
        self.assertEqual(out["tried"], 12)
        self.assertEqual(out["cleared"], sum(1 for r in rows if r["t_stat"] > out["hurdle"]))
        self.assertAlmostEqual(out["expected_by_chance"], 12 * 0.05, places=1)
        self.assertEqual(out["best_key"], "rule11")
        # 12 near-independent variants must not collapse to a near-zero hurdle
        self.assertGreater(out["n_eff"], 5.0)
        self.assertGreater(out["hurdle"], 0.5)

    def test_no_rows_is_none(self):
        self.assertIsNone(validation.multiple_testing_summary([], {}))
        self.assertIsNone(validation.multiple_testing_summary(
            [{"key": "x", "t_stat": None}], {}))

    def test_missing_monthly_data_falls_back_to_treating_trials_as_independent(self):
        """The conservative direction to be wrong in: without correlation
        data, n_eff = tried (the OLD, too-strict assumption), never the
        collapsed-to-near-zero failure mode."""
        rows = [{"key": f"rule{i}", "t_stat": 2.0} for i in range(10)]
        out = validation.multiple_testing_summary(rows, None)
        self.assertEqual(out["n_eff"], 10.0)


if __name__ == "__main__":
    unittest.main()

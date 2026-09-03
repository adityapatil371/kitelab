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
    """Pure math on trade dicts -- no price files, no portfolio engine."""

    def _trades(self, n, win_net=200.0, win_risk=100.0, loss_net=-100.0, loss_risk=100.0):
        out = []
        for i in range(n):
            day = i + 1
            entry = TS("2018-01-01") + pd.Timedelta(days=day)
            exit_ = entry + pd.Timedelta(days=3)
            net, risk = (win_net, win_risk) if i % 2 == 0 else (loss_net, loss_risk)
            out.append(_trade(entry, exit_, net, risk))
        return out

    def test_shape_and_bounds(self):
        row = validation._bootstrap_one(self._trades(60), draws=200)
        self.assertIsNotNone(row)
        for key in ("n", "obs_cagr", "p05", "p50", "p95", "dd05", "p_neg", "cleared_95"):
            self.assertIn(key, row)
        self.assertEqual(row["n"], 60)
        self.assertLessEqual(row["p05"], row["p50"])
        self.assertLessEqual(row["p50"], row["p95"])
        self.assertGreaterEqual(row["dd05"], -100.0)
        self.assertLessEqual(row["dd05"], 0.0)
        self.assertIsInstance(row["cleared_95"], bool)

    def test_below_floor_returns_none(self):
        self.assertIsNone(validation._bootstrap_one(self._trades(10)))

    def test_a_reliable_edge_clears_the_95_bar(self):
        """Every trade wins 2R with no losers -- the 5th percentile of any
        resample of an all-winning list must be positive."""
        row = validation._bootstrap_one(self._trades(60, loss_net=50.0), draws=200)
        self.assertTrue(row["cleared_95"])
        self.assertGreater(row["p05"], 0)


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


class MultipleTestingSummary(unittest.TestCase):
    def test_counts_and_hurdle_are_internally_consistent(self):
        rows = [{"key": f"rule{i}", "obs_cagr": float(i),
                "p05": 5.0 if i % 3 == 0 else -5.0, "p95": 20.0}
                for i in range(12)]
        out = validation.multiple_testing_summary(rows)
        self.assertEqual(out["tried"], 12)
        self.assertEqual(out["cleared"], sum(1 for r in rows if r["p05"] > 0))
        self.assertAlmostEqual(out["expected_by_chance"], 12 * 0.05, places=1)
        self.assertEqual(out["best_key"], "rule11")

    def test_no_rows_is_none(self):
        self.assertIsNone(validation.multiple_testing_summary([]))
        self.assertIsNone(validation.multiple_testing_summary(
            [{"key": "x", "obs_cagr": 1.0, "p05": None, "p95": None}]))


if __name__ == "__main__":
    unittest.main()

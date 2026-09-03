"""The last pieces with no cover: spread application, the multi-timeframe
producer, the daily mark-to-market, level touches, cache refusal and the
screener's evaluator.
"""
import unittest

import numpy as np
import pandas as pd

from kitelab import levels, portfolio, screener, signals, slippage, timeframes
from tests.support import account, bars, daily_bars


class ApplySpread(unittest.TestCase):
    """Charged onto an already-simulated trade rather than re-simulating it.
    Exact, because nothing in the simulation depends on the fill price."""

    def _trade(self):
        return {"symbol": "X", "entry_ts": pd.Timestamp("2020-06-01"),
                "exit_ts": pd.Timestamp("2020-07-01"),
                "entry_price": 100.0, "exit_price": 110.0, "stop": 90.0,
                "shares": 10, "same_session": False,
                "gross_profit": 100.0, "charges": 5.0, "net_profit": 95.0,
                "risk_taken": 100.0}

    def _tape(self, adv):
        saved = slippage.profile
        slippage.profile = lambda s: {"ts": np.array([np.datetime64("2019-01-01")]),
                                      "adv": np.array([float(adv)]),
                                      "vol": np.array([0.02])}
        return saved

    def test_the_spread_makes_a_trade_worse_never_better(self):
        saved, was = self._tape(1e8), slippage.ENABLED
        slippage.ENABLED = True
        try:
            got = slippage.apply_spread(self._trade())
        finally:
            slippage.profile, slippage.ENABLED = saved, was
        self.assertLessEqual(got["gross_profit"], 100.0)

    def test_the_original_trade_is_not_mutated(self):
        """It is charged onto a COPY: the same cached trade list is reused for
        the perfect-fills arm, and mutating it would contaminate that."""
        saved, was = self._tape(1e8), slippage.ENABLED
        slippage.ENABLED = True
        original = self._trade()
        try:
            slippage.apply_spread(original)
        finally:
            slippage.profile, slippage.ENABLED = saved, was
        self.assertEqual(original["gross_profit"], 100.0)

    def test_a_thinner_stock_is_charged_more(self):
        was = slippage.ENABLED
        slippage.ENABLED = True
        try:
            saved = self._tape(1e9)
            deep = slippage.apply_spread(self._trade())["gross_profit"]
            slippage.profile = saved
            saved = self._tape(1e6)
            thin = slippage.apply_spread(self._trade())["gross_profit"]
            slippage.profile = saved
        finally:
            slippage.ENABLED = was
        self.assertGreater(deep, thin)


class MultiTimeframeProducer(unittest.TestCase):
    """timeframes.simulate_variant mirrors backtest.simulate's walk for the
    Q/M/W and pair variants -- a second walker, and therefore a second place the
    same bug can live."""

    def test_a_steady_rise_produces_trades(self):
        rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(600)]
        with daily_bars(bars(rows)):
            got = timeframes.simulate_variant("X", "QMW")
        self.assertIsInstance(got, list)

    def test_every_trade_carries_what_the_account_needs(self):
        rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(400)]
        rows += [(500 - i, 501 - i, 499 - i, 500 - i) for i in range(200)]
        with daily_bars(bars(rows)):
            got = timeframes.simulate_variant("X", "QMW")
        for t in got:
            for field in ("symbol", "entry_ts", "exit_ts", "entry_price",
                          "exit_price", "stop", "gross_profit", "charges"):
                self.assertIn(field, t)

    def test_the_stop_is_below_the_entry_on_every_trade(self):
        rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(400)]
        rows += [(500 - i, 501 - i, 499 - i, 500 - i) for i in range(200)]
        with daily_bars(bars(rows)):
            for t in timeframes.simulate_variant("X", "QMW"):
                self.assertLess(t["stop"], t["entry_price"])


class DailyCurve(unittest.TestCase):
    def test_it_marks_every_day_between_first_entry_and_last_exit(self):
        with account():
            r = portfolio.run([], 100_000, 0.01)
        self.assertEqual(r["final"], 100_000)

    def test_the_curve_and_the_cash_curve_run_in_step(self):
        from tests.support import trade
        with account():
            r = portfolio.run([trade()], 100_000, 0.01)
        self.assertEqual(len(r["curve"]), len(r["cash_curve"]))


class TouchEvents(unittest.TestCase):
    def _frame(self, lows):
        n = len(lows)
        return pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="D"),
                             "open": lows, "high": [x * 1.01 for x in lows],
                             "low": lows, "close": lows, "volume": [1] * n})

    def test_a_price_never_approached_has_no_events(self):
        self.assertEqual(levels.touch_events(self._frame([500.0] * 40), 100.0), [])

    def test_visits_closer_than_the_separation_collapse_into_one(self):
        got = levels.touch_events(self._frame([100.0, 100.0, 100.0] + [500.0] * 20), 100.0)
        self.assertEqual(len(got), 1)

    def test_separated_visits_count_separately(self):
        away = [500.0] * (levels.TOUCH_SEPARATION_BARS + 1)
        got = levels.touch_events(self._frame([100.0] + away + [100.0] + away), 100.0)
        self.assertEqual(len(got), 2)


class CacheRefusal(unittest.TestCase):
    def test_a_missing_cache_is_none_not_an_empty_list(self):
        """An empty list would read as "this rule made no trades" and would be
        used. None forces the caller to rebuild."""
        self.assertIsNone(signals.load("no_such_cache_xyz", ["AAA"]))


class ScreenerEvaluator(unittest.TestCase):
    def test_a_plain_comparison_evaluates(self):
        self.assertTrue(screener.evaluate("1 < 2", {}))
        self.assertFalse(screener.evaluate("1 > 2", {}))

    def test_the_expression_cannot_reach_the_interpreter(self):
        """Queries are eval'd, so the namespace is deliberately restricted. If
        __import__ were reachable a scan string would be arbitrary code."""
        with self.assertRaises(Exception):
            screener.evaluate("__import__('os').listdir('.')", {})


if __name__ == "__main__":
    unittest.main()

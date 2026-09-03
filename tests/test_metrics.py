"""Risk metrics, checked against hand-computed values.

These were written rather than imported (see kitelab/curves.py for why), so
each is verified against arithmetic done independently here. A metric checked
only against itself is decoration.
"""
import math
import unittest

from kitelab import curves


class DailyReturns(unittest.TestCase):
    def test_returns_are_simple_and_one_shorter_than_the_curve(self):
        got = curves.daily_returns([(0, 100.0), (1, 110.0), (2, 99.0)])
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0], 0.10)
        self.assertAlmostEqual(got[1], -0.10)


class Sharpe(unittest.TestCase):
    def test_matches_a_hand_computed_value(self):
        curve = [(0, 100.0), (1, 110.0), (2, 99.0), (3, 118.8)]
        rs = [0.10, -0.10, 0.20]
        mean = sum(rs) / 3
        sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / 3)
        self.assertAlmostEqual(curves.sharpe(curve),
                               (mean / sd) * math.sqrt(252), places=9)

    def test_a_perfectly_steady_curve_has_no_sharpe_rather_than_a_huge_one(self):
        steady = [(i, 100.0 * (1.01 ** i)) for i in range(300)]
        self.assertIsNone(curves.sharpe(steady))

    def test_a_risk_free_rate_lowers_it(self):
        curve = [(0, 100.0), (1, 110.0), (2, 99.0), (3, 118.8)]
        self.assertLess(curves.sharpe(curve, risk_free=0.5),
                        curves.sharpe(curve, risk_free=0.0))


class Sortino(unittest.TestCase):
    def test_upside_volatility_is_not_penalised(self):
        """Two curves, same downside, different upside. Sortino must prefer the
        one with the bigger upside; Sharpe punishes it for the same reason."""
        calm = [(0, 100.0), (1, 90.0), (2, 99.0)]
        wild = [(0, 100.0), (1, 90.0), (2, 180.0)]
        self.assertGreater(curves.sortino(wild), curves.sortino(calm))

    def test_no_losing_day_means_no_downside_deviation(self):
        rising = [(i, 100.0 + i) for i in range(10)]
        self.assertIsNone(curves.sortino(rising))


class Calmar(unittest.TestCase):
    def test_is_cagr_over_the_worst_drawdown(self):
        self.assertAlmostEqual(curves.calmar(20.0, -10.0), 2.0)

    def test_undefined_without_a_drawdown(self):
        self.assertIsNone(curves.calmar(20.0, 0.0))
        self.assertIsNone(curves.calmar(None, -10.0))


class Exposure(unittest.TestCase):
    def test_all_cash_is_zero_exposure(self):
        curve = [(i, 100.0) for i in range(10)]
        self.assertEqual(curves.exposure_pct(curve, list(curve)), 0.0)

    def test_fully_invested_is_full_exposure(self):
        curve = [(i, 100.0) for i in range(10)]
        cash = [(i, 0.0) for i in range(10)]
        self.assertEqual(curves.exposure_pct(curve, cash), 100.0)

    def test_mismatched_curves_are_refused_rather_than_zipped_short(self):
        self.assertIsNone(curves.exposure_pct([(0, 1.0)], []))


if __name__ == "__main__":
    unittest.main()

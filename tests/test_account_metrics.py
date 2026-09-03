"""portfolio's own measures, and the dated cost schedule.

ulcer_index and mar_ratio are what the compare table is ranked on; stt_rate_on
is the mechanism that lets a tax rate change mid-backtest.
"""
import unittest

import pandas as pd

from kitelab import backtest, portfolio


def curve(values, start="2020-01-01"):
    stamps = pd.date_range(start, periods=len(values), freq="D")
    return list(zip(stamps, [float(v) for v in values]))


class UlcerIndex(unittest.TestCase):
    def test_a_curve_that_only_rises_has_no_ulcer(self):
        self.assertAlmostEqual(portfolio.ulcer_index(curve(range(100, 200))), 0.0)

    def test_a_long_shallow_drawdown_scores_below_one_deep_plunge(self):
        """Squaring is the point: it matches how the two actually feel."""
        shallow = curve([100] + [95] * 40 + [100])
        deep = curve([100] + [50] + [100] * 40)
        self.assertLess(portfolio.ulcer_index(shallow), portfolio.ulcer_index(deep))

    def test_it_charges_for_duration_not_just_depth(self):
        """Same worst dip, different time spent in it. Max drawdown cannot tell
        these apart; the Ulcer Index is here precisely because it can."""
        brief = curve([100, 80] + [100] * 30)
        long_ = curve([100] + [80] * 30 + [100])
        self.assertLess(portfolio.ulcer_index(brief), portfolio.ulcer_index(long_))

    def test_an_empty_curve_has_no_ulcer_rather_than_zero(self):
        self.assertIsNone(portfolio.ulcer_index([]))


class MarRatio(unittest.TestCase):
    def test_it_is_return_divided_by_the_worst_dip(self):
        self.assertAlmostEqual(portfolio.mar_ratio(20.0, -10.0), 2.0)

    def test_the_sign_of_the_drawdown_does_not_change_the_answer(self):
        """Drawdowns are stored negative here; a ratio that flipped sign with
        the convention would silently invert the whole ranking."""
        self.assertEqual(portfolio.mar_ratio(20.0, -10.0),
                         portfolio.mar_ratio(20.0, 10.0))

    def test_a_losing_rule_gives_a_negative_ratio(self):
        self.assertLess(portfolio.mar_ratio(-5.0, -10.0), 0)

    def test_no_drawdown_means_no_ratio_rather_than_infinity(self):
        self.assertIsNone(portfolio.mar_ratio(20.0, 0.0))

    def test_no_cagr_means_no_ratio(self):
        """A wiped account has no growth rate, so it has no MAR either -- and
        must not sort as if it had one."""
        self.assertIsNone(portfolio.mar_ratio(None, -10.0))


class SttSchedule(unittest.TestCase):
    """One entry today, but the mechanism is the point: delivery STT is believed
    to have changed around June 2013, and a single constant would apply today's
    rate to 2010 trades."""

    def test_the_rate_is_constant_where_the_schedule_has_one_entry(self):
        self.assertEqual(backtest.stt_rate_on("2010-01-01"),
                         backtest.stt_rate_on("2026-01-01"))

    def test_a_second_entry_takes_effect_on_its_date_and_not_before(self):
        saved = backtest.STT_SCHEDULE
        backtest.STT_SCHEDULE = [(pd.Timestamp("1900-01-01"), 0.001),
                                 (pd.Timestamp("2013-06-01"), 0.002)]
        try:
            self.assertEqual(backtest.stt_rate_on("2013-05-31"), 0.001)
            self.assertEqual(backtest.stt_rate_on("2013-06-01"), 0.002)
            self.assertEqual(backtest.stt_rate_on("2020-01-01"), 0.002)
        finally:
            backtest.STT_SCHEDULE = saved

    def test_a_date_before_every_entry_falls_back_to_the_first(self):
        self.assertIsInstance(backtest.stt_rate_on("1800-01-01"), float)


if __name__ == "__main__":
    unittest.main()

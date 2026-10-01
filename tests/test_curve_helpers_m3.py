"""Hand-calculated examples for the shared performance arithmetic."""

import numpy as np
import pandas as pd
import pytest

from kitelab import curves


def test_calendar_cagr_uses_elapsed_calendar_days():
    assert curves.calendar_cagr(100, 110, "2019-01-01", "2020-01-01") == pytest.approx(
        100 * (1.1 ** (365.25 / 365) - 1))
    assert curves.calendar_cagr(100, 110, "2020-01-01", "2021-01-01") == pytest.approx(
        100 * (1.1 ** (365.25 / 366) - 1))
    assert curves.calendar_cagr(100, 110, "2020-01-01", "2020-01-01") is None
    assert curves.calendar_cagr(0, 110, "2020-01-01", "2021-01-01") is None
    assert curves.calendar_cagr(100, 0, "2020-01-01", "2021-01-01") is None


def test_drawdown_uses_observed_peak_and_recovers():
    assert curves.drawdown_from_wealth(np.array([100, 90, 105])).tolist() == [0, -10, 0]
    assert curves.drawdown_from_wealth(np.array([100, 80, 90])).tolist() == [0, -20, -10]
    assert curves.drawdown_from_wealth(np.array([90, 100])).tolist() == [0, 0]
    assert curves.drawdown_from_wealth(np.array([])).size == 0
    assert curves.drawdown_from_wealth(np.array([100])).tolist() == [0]


def test_fixed_shares_late_listing_and_daily_rebalance():
    dates = pd.date_range("2020-01-01", periods=3)
    prices = pd.DataFrame({"A": [100, 200, 100], "B": [100, 100, 100],
                           "late": [np.nan, 100, 200]}, index=dates)
    fixed = curves.fixed_share_wealth(prices[["A", "B"]],
                                      listing_policy="first_close", cash_policy="zero_return")
    assert fixed.mean(axis=1).tolist() == [1.0, 1.5, 1.0]
    late = curves.fixed_share_wealth(prices[["A", "late"]],
                                     listing_policy="first_close", cash_policy="zero_return")
    assert late.mean(axis=1).tolist() == [1.0, 1.5, 1.5]
    daily = curves.ew_daily_wealth(prices[["A", "B"]].pct_change().fillna(0))
    assert daily.iloc[-1] == 1.125
    with pytest.raises(ValueError):
        curves.fixed_share_wealth(prices, listing_policy="start_only", cash_policy="zero_return")

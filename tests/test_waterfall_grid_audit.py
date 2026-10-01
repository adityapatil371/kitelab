import numpy as np
import pandas as pd

from scripts.entry_exit_grid import walk_forward_random_rate
from scripts.waterfall import account, draw_seed, turnover_cost


def test_random_draw_seeds_are_stable_and_distinct():
    def sample(base):
        return [np.random.default_rng(draw_seed("NSE|mr|own|random", i, base))
                .integers(0, 100000, size=5).tolist() for i in range(8)]

    assert sample(7) == sample(7)
    assert sample(7) != sample(8)
    assert len({draw_seed("arm", i, 7) for i in range(100)}) == 100


def test_resize_cost_is_once_per_unit_of_turnover():
    bp = 23.0
    turnover, fee = turnover_cost({"A": 0.5}, {"A": 0.25}, bp)
    assert turnover == 0.25
    assert fee == 0.25 * bp / 2 / 10000
    dates = pd.date_range("2024-01-01", periods=4)
    rets = {"A": np.zeros(4)}
    # Trade enters after index 0, then capacity contracts at index 2.
    turn = {"A": np.array([np.nan, 500.0, 250.0, 250.0])}
    paid = account({"A": [(0, 3)]}, dates, rets, turn, 1, bp, 10.0)
    free = account({"A": [(0, 3)]}, dates, rets, turn, 1, 0.0, 10.0)
    entry_fee = 0.5 * bp / 2 / 10000
    previous_weight = 0.5 / (1 - entry_fee)
    resize_fee = abs(0.25 - previous_weight) * bp / 2 / 10000
    assert np.isclose(paid[0].iloc[1], -entry_fee)
    assert np.isclose(paid[0].iloc[2], -resize_fee)
    assert paid[0].sum() < free[0].sum()


def test_walk_forward_rate_has_prefix_invariance():
    idx = pd.date_range("2024-01-01", periods=30)
    listed = pd.DataFrame(True, index=idx, columns=["A", "B"])
    signals = {"one": listed.copy(), "two": listed.copy()}
    signals["two"].iloc[:20] = False
    prefix = walk_forward_random_rate(signals, listed)
    later = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=10)
    listed_long = pd.concat([listed, pd.DataFrame(True, index=later, columns=listed.columns)])
    signals_long = {k: pd.concat([v, pd.DataFrame(False, index=later,
                                                 columns=listed.columns)])
                    for k, v in signals.items()}
    pd.testing.assert_series_equal(prefix, walk_forward_random_rate(
        signals_long, listed_long).iloc[:len(prefix)])
    assert (prefix.iloc[:20] == 0).all()
    assert prefix.iloc[20] == 0.5

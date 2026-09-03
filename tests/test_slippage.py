"""kitelab.slippage -- what a fill really costs.

The module is explicit that half-spread is ASSUMED and impact's inputs are
measured while its shape is not. That honesty is worth preserving, so these test
the SHAPE and the guarantees -- monotonic in liquidity, floored by the tick,
capped, and never free -- rather than pinning numbers that are admittedly a
model. A test asserting half_spread == 12.3bp would be asserting the assumption.
"""
import contextlib
import unittest

import numpy as np
import pandas as pd

from kitelab import slippage


@contextlib.contextmanager
def tape(adv, vol=0.02):
    """A stock with a stated trailing turnover and volatility."""
    saved = slippage.profile
    slippage.profile = lambda symbol: {
        "ts": np.array([np.datetime64("2019-01-01")]),
        "adv": np.array([float(adv)]), "vol": np.array([float(vol)])}
    try:
        yield
    finally:
        slippage.profile = saved


WHEN = pd.Timestamp("2020-06-01")


class HalfSpread(unittest.TestCase):
    def test_a_more_liquid_stock_is_never_more_expensive(self):
        with tape(1e9):
            liquid = slippage.half_spread("X", WHEN, 100.0)
        with tape(1e6):
            thin = slippage.half_spread("X", WHEN, 100.0)
        self.assertLess(liquid, thin)

    def test_it_can_never_be_finer_than_half_a_tick(self):
        """A spread narrower than the exchange's own tick is not a spread."""
        with tape(1e12):
            got = slippage.half_spread("X", WHEN, 10.0)
        self.assertGreaterEqual(got, (slippage.TICK / 2.0) / 10.0 - 1e-12)

    def test_it_is_capped_for_a_near_dead_stock(self):
        with tape(1.0):
            self.assertLessEqual(slippage.half_spread("X", WHEN, 100.0),
                                 slippage.MAX_HALF_SPREAD)

    def test_an_unknown_stock_is_charged_the_cap_not_zero(self):
        """No data is the most expensive case, not the cheapest. Defaulting to
        free is how an untested symbol becomes the best performer."""
        with tape(float("nan")):
            self.assertGreater(slippage.half_spread("X", WHEN, 100.0), 0)

    def test_a_cheaper_share_carries_a_wider_proportional_tick_floor(self):
        with tape(1e12):
            cheap = slippage.half_spread("X", WHEN, 5.0)
            dear = slippage.half_spread("X", WHEN, 5000.0)
        self.assertGreater(cheap, dear)


class Impact(unittest.TestCase):
    def test_a_bigger_order_moves_the_price_more(self):
        with tape(1e8):
            small = slippage.impact("X", WHEN, 1e4)
            large = slippage.impact("X", WHEN, 1e7)
        self.assertGreater(large, small)

    def test_it_grows_with_the_square_root_not_linearly(self):
        """Four times the order is twice the impact, which is the whole content
        of the square-root law and the reason it is not just a percentage."""
        with tape(1e8):
            one = slippage.impact("X", WHEN, 1e5)
            four = slippage.impact("X", WHEN, 4e5)
        self.assertAlmostEqual(four / one, 2.0, places=6)

    def test_the_same_order_costs_less_in_a_more_liquid_stock(self):
        with tape(1e9):
            deep = slippage.impact("X", WHEN, 1e6)
        with tape(1e7):
            thin = slippage.impact("X", WHEN, 1e6)
        self.assertLess(deep, thin)

    def test_a_zero_order_has_no_impact(self):
        with tape(1e8):
            self.assertEqual(slippage.impact("X", WHEN, 0.0), 0.0)

    def test_an_unknown_stock_has_no_modelled_impact(self):
        with tape(float("nan")):
            self.assertEqual(slippage.impact("X", WHEN, 1e6), 0.0)


class Participation(unittest.TestCase):
    def test_the_cap_is_off_unless_max_participation_is_set(self):
        saved = slippage.MAX_PARTICIPATION
        slippage.MAX_PARTICIPATION = None
        try:
            with tape(1e6):
                self.assertEqual(slippage.capped_shares("X", WHEN, 100.0, 1e9), 1e9)
        finally:
            slippage.MAX_PARTICIPATION = saved

    def test_an_order_beyond_the_stock_s_turnover_is_trimmed(self):
        saved = slippage.MAX_PARTICIPATION
        slippage.MAX_PARTICIPATION = 0.01
        try:
            with tape(1e7):                      # Rs1 crore a day
                got = slippage.capped_shares("X", WHEN, 100.0, 1e6)
            self.assertLess(got, 1e6)
        finally:
            slippage.MAX_PARTICIPATION = saved

    def test_a_small_order_passes_through_untouched(self):
        saved = slippage.MAX_PARTICIPATION
        slippage.MAX_PARTICIPATION = 0.01
        try:
            with tape(1e9):
                self.assertEqual(slippage.capped_shares("X", WHEN, 100.0, 10), 10)
        finally:
            slippage.MAX_PARTICIPATION = saved


class Fill(unittest.TestCase):
    def test_buying_pays_up_and_selling_receives_less(self):
        """The spread is a cost on both sides. A model where one side gained
        would let a round trip profit from friction."""
        saved = slippage.ENABLED
        slippage.ENABLED = True
        try:
            with tape(1e8):
                buy = slippage.fill("X", WHEN, 100.0, +1)
                sell = slippage.fill("X", WHEN, 100.0, -1)
        finally:
            slippage.ENABLED = saved
        self.assertGreater(buy, 100.0)
        self.assertLess(sell, 100.0)

    def test_disabled_is_a_perfect_fill_at_the_quoted_price(self):
        """ENABLED off is the "perfect fills" arm, and it must be exactly the
        quoted price -- a residual cost there would contaminate the baseline
        every other fill mode is measured against."""
        saved = slippage.ENABLED
        slippage.ENABLED = False
        try:
            with tape(1e8):
                self.assertEqual(slippage.fill("X", WHEN, 100.0, +1), 100.0)
                self.assertEqual(slippage.fill("X", WHEN, 100.0, -1), 100.0)
        finally:
            slippage.ENABLED = saved


if __name__ == "__main__":
    unittest.main()

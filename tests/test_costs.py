"""kitelab.backtest.charges -- the round-trip cost model.

Peers charge a flat percentage. This models the actual Zerodha/NSE schedule, so
it is arithmetic that can be wrong in ways a flat rate cannot, and it decides
which positions are too small to place.
"""
import unittest

from kitelab import backtest
from tests.support import account


class Charges(unittest.TestCase):
    def test_intraday_is_cheaper_than_delivery(self):
        # STT drops from 0.1% both sides to 0.025% sell-side, and no DP charge.
        with account():
            delivery = backtest.charges(100_000, 105_000, intraday=False)
            intraday = backtest.charges(100_000, 105_000, intraday=True)
        self.assertLess(intraday, delivery)

    def test_the_demat_charge_is_flat_and_dominates_small_positions(self):
        """Rs15.34 per sell whatever the size -- the reason a Rs52 position
        cannot profit, and the reason MAX_COST_FRACTION exists."""
        with account():
            small = backtest.charges(100, 100, intraday=False)
        self.assertGreater(small / 100, 0.10)

    def test_costs_rise_with_turnover(self):
        with account():
            a = backtest.charges(10_000, 10_000, intraday=False)
            b = backtest.charges(100_000, 100_000, intraday=False)
        self.assertGreater(b, a)

    def test_flat_fee_replaces_the_whole_equity_model(self):
        """Non-equity instruments pay no STT and no demat charge. The flat rate
        must REPLACE the schedule, not be added to it."""
        with account(flat_fee=0.001):
            got = backtest.charges(100_000, 100_000)
        self.assertAlmostEqual(got, 0.001 * 200_000, places=6)

    def test_the_flat_fee_is_restored_after_use(self):
        """It is a module global mutated per instrument; a leak would silently
        price every NSE equity as crypto. See the 2026-09-03 audit."""
        with account(flat_fee=0.001):
            pass
        self.assertIsNone(backtest.FLAT_FEE_RATE)


if __name__ == "__main__":
    unittest.main()

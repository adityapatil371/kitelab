"""MAE/MFE, checked against hand-computed excursions."""
import unittest

import pandas as pd

from kitelab import excursion


def frame(rows):
    return pd.DataFrame([{"ts": pd.Timestamp(t), "low": lo, "high": hi,
                          "open": lo, "close": hi, "volume": 1}
                         for t, lo, hi in rows])


class Excursions(unittest.TestCase):
    def _trade(self, entry=100.0, stop=90.0):
        return {"symbol": "AAA", "entry_ts": "2020-01-01", "exit_ts": "2020-01-05",
                "entry_price": entry, "stop": stop}

    def test_the_entry_bar_is_excluded(self):
        """Entries fill at the close, and for most rules here the stop IS the
        entry bar's low -- so counting that bar measures the stop against
        itself and every trade shows MAE >= 1.0R by identity."""
        f = frame([("2020-01-01", 90, 101), ("2020-01-05", 99, 105)])
        got = excursion.excursions(self._trade(), f)   # entry bar low == stop
        self.assertLess(got["mae_r"], 1.0)

    def test_mae_and_mfe_are_measured_in_R(self):
        # Risk is Rs10. Low 95 is 0.5R against; high 130 is 3R in favour.
        f = frame([("2020-01-01", 99, 101), ("2020-01-03", 95, 110),
                   ("2020-01-05", 98, 130)])
        got = excursion.excursions(self._trade(), f)
        self.assertAlmostEqual(got["mae_r"], 0.5)
        self.assertAlmostEqual(got["mfe_r"], 3.0)

    def test_bars_outside_the_holding_window_are_ignored(self):
        """A crash the day after the exit is not this trade's excursion."""
        f = frame([("2020-01-01", 99, 101), ("2020-01-05", 98, 105),
                   ("2020-01-09", 1, 2)])
        got = excursion.excursions(self._trade(), f)
        self.assertAlmostEqual(got["mae_r"], 0.2)

    def test_a_stop_at_or_above_entry_is_unmeasurable(self):
        f = frame([("2020-01-01", 99, 101)])
        self.assertIsNone(excursion.excursions(self._trade(stop=100.0), f))


class Summary(unittest.TestCase):
    def test_worst_survivable_stop_is_the_deepest_winner_dip(self):
        rows = [{"mae_r": 0.4, "mfe_r": 3.0, "won": True},
                {"mae_r": 0.9, "mfe_r": 2.0, "won": True},
                {"mae_r": 1.0, "mfe_r": 0.1, "won": False}]
        # A stop tighter than 0.9R would have killed the second winner.
        self.assertAlmostEqual(excursion.worst_survivable(rows, 100.0), 0.9)

    def test_a_percentile_ignores_a_single_outlier(self):
        rows = [{"mae_r": 0.3, "mfe_r": 2.0, "won": True} for _ in range(19)]
        rows.append({"mae_r": 155.0, "mfe_r": 2.0, "won": True})
        self.assertLess(excursion.worst_survivable(rows, 90.0), 1.0)
        self.assertAlmostEqual(excursion.worst_survivable(rows, 100.0), 155.0)

    def test_summary_splits_winners_from_losers(self):
        rows = [{"mae_r": 0.4, "mfe_r": 3.0, "won": True},
                {"mae_r": 1.0, "mfe_r": 0.1, "won": False}]
        got = excursion.summarise(rows)
        self.assertEqual(got["winners_mae"]["n"], 1)
        self.assertEqual(got["losers_mae"]["n"], 1)
        self.assertEqual(got["trades"], 2)

    def test_no_winners_means_no_survivable_stop_rather_than_zero(self):
        self.assertIsNone(excursion.worst_survivable(
            [{"mae_r": 1.0, "mfe_r": 0.1, "won": False}], 100.0))


if __name__ == "__main__":
    unittest.main()

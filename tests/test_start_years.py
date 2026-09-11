"""A start year earlier than the first trade is not a second test.

Added 2026-09-11. The `recent` universe holds the names with no positive
turnover before 2018, so a backtest of it "starting in 2006" starts at the same
trade as one starting in 2018: same window, same account, same number. The
board carried all three as separate cells, and the multiple-testing gate
counted them as three chances to be lucky when they are one. Measured on the
13-strategy board: 156 of 156 cells agreed to within 1e-9 on both pairs, while
no other universe had more than 1 of 156 agreeing by coincidence.

Two things are pinned here, because both failures are silent:

  That the years dropped really are duplicates -- the window a dropped year
  produces must be identical to the window of a year that was kept.

  That wf_attach applies the same collapse. It walks its own copy of the grid
  loop, so if the two drift a cell the grid wrote as None comes back carrying
  a daily-excess test, and the gate counts a test with no cell behind it.
"""
from __future__ import annotations

import bisect
import unittest
from pathlib import Path

import pandas as pd

from scripts import dashboard_data as dd


def stamps_from(dates):
    return [pd.Timestamp(d) for d in dates]


def window_for(stamps, year):
    return stamps[bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01")):]


class TheYearsThatAreRealTests(unittest.TestCase):
    def test_a_rule_trading_throughout_keeps_every_year(self):
        s = stamps_from(["2005-06-01", "2013-01-01", "2019-01-01",
                         "2023-01-01", "2025-01-01"])
        self.assertEqual(dd.gridded_years(s), dd.START_YEARS)

    def test_recent_collapses_to_the_year_it_could_have_started(self):
        # Nothing before 2018, so 2006 and 2012 are 2018 wearing older labels.
        s = stamps_from(["2019-03-01", "2021-07-01", "2023-02-01"])
        self.assertEqual(dd.gridded_years(s), [2018, 2022, 2024])

    def test_the_latest_dead_year_is_the_one_kept_not_the_earliest(self):
        """2018 is the year you could actually have started; 2006 is a claim
        the data cannot support."""
        s = stamps_from(["2019-03-01"])
        self.assertEqual(dd.gridded_years(s)[0], 2018)

    def test_one_dead_year_still_leaves_the_rest_distinct(self):
        # First trade in 2010: 2006 cuts nothing, but 2012 onwards all do.
        s = stamps_from(["2010-01-01", "2013-01-01", "2019-01-01",
                         "2023-01-01", "2025-01-01"])
        self.assertEqual(dd.gridded_years(s), dd.START_YEARS)

    def test_a_rule_with_no_trades_at_all_collapses_to_one_year(self):
        # Every cell is None either way; there is no reason to write five.
        self.assertEqual(dd.gridded_years([]), [dd.START_YEARS[-1]])

    def test_every_dropped_year_duplicates_a_kept_one(self):
        """The whole claim, checked directly rather than assumed."""
        for first in ["2004-01-01", "2010-01-01", "2016-01-01",
                      "2019-01-01", "2023-01-01", "2025-06-01"]:
            s = stamps_from([first, "2025-12-01"])
            kept = dd.gridded_years(s)
            for year in dd.START_YEARS:
                if year in kept:
                    continue
                self.assertTrue(
                    any(window_for(s, year) == window_for(s, k) for k in kept),
                    f"{year} was dropped but duplicates no kept year (first={first})")

    def test_the_result_is_a_suffix_in_board_order(self):
        for s in [[], stamps_from(["2019-01-01"]), stamps_from(["2004-01-01"])]:
            kept = dd.gridded_years(s)
            n = len(kept)
            self.assertEqual(kept, dd.START_YEARS[len(dd.START_YEARS) - n:])

    def test_a_caller_may_pass_its_own_year_list(self):
        s = stamps_from(["2019-01-01"])
        self.assertEqual(dd.gridded_years(s, [2012, 2018]), [2018])


class BothGridLoopsCollapseTheSameYears(unittest.TestCase):
    """wf_attach walks its own copy of the loop; it must not walk it alone."""

    def test_wf_attach_calls_the_shared_helper(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "wf_attach.py").read_text(encoding="utf-8")
        self.assertIn("dd.gridded_years(", src,
                      "wf_attach stopped using dashboard_data.gridded_years, so "
                      "its year loop can now disagree with the grid's")


if __name__ == "__main__":
    unittest.main()

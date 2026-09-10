"""The out-of-band diagnostics: the Benjamini-Hochberg bar, the summary counts,
and the one contract that silently breaks everything -- the cell key.

Nothing here touches the price data or runs a backtest. These are the pieces
that decide 9,500 verdicts from a single threshold, so they are worth pinning
independently of the hour of compute that produces the inputs.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.attach_diagnostics import ALPHA, bh_threshold, summarise


class TheBenjaminiHochbergBar(unittest.TestCase):
    """A false-discovery-rate threshold, not a nominal 0.05.

    The board tests every cell at once; at an uncorrected 5% about one cell in
    twenty clears on noise alone. BH sorts the p-values and keeps everything at
    or below the largest rank i where p(i) <= alpha * i / n.
    """

    def test_no_pvalues_admits_nothing(self):
        self.assertEqual(bh_threshold([]), 0.0)

    def test_all_large_admits_nothing(self):
        # 0.0 is a threshold no p-value can meet, so the gate fails closed.
        self.assertEqual(bh_threshold([0.4, 0.6, 0.9]), 0.0)

    def test_textbook_example(self):
        # n=5, alpha=0.05, so the ranks buy 0.01/0.02/0.03/0.04/0.05.
        # 0.009 <= 0.01 and 0.02 <= 0.02 pass; 0.04 > 0.03 stops the step-up.
        ps = [0.009, 0.02, 0.04, 0.3, 0.8]
        self.assertAlmostEqual(bh_threshold(ps), 0.02)

    def test_it_steps_UP_not_down(self):
        """The property that separates BH from a naive per-test comparison.

        With n=4 the rank bars are 0.0125, 0.025, 0.0375, 0.05. Taken rank by
        rank, 0.02 FAILS its own bar of 0.0125 -- but 0.049 clears rank 4, and
        BH keeps everything at or below the LARGEST passing rank. So all four
        are discoveries and the threshold is 0.049. A naive per-test comparison
        would have thrown out the smallest p-value on the list.
        """
        ps = [0.02, 0.026, 0.038, 0.049]
        self.assertGreater(ps[0], ALPHA * 1 / 4)     # fails its own rank bar
        self.assertAlmostEqual(bh_threshold(ps), 0.049)

    def test_never_looser_than_alpha(self):
        for ps in ([0.05] * 10, [0.049] * 3, [0.9, 0.05], [0.0] * 4):
            self.assertLessEqual(bh_threshold(ps), ALPHA)

    def test_always_at_least_as_strict_as_uncorrected(self):
        """Every BH discovery is an uncorrected discovery; the reverse is not
        true, and the gap is the whole reason the correction is applied."""
        import random
        rng = random.Random(11)
        for _ in range(50):
            ps = [rng.random() ** 3 for _ in range(200)]
            bar = bh_threshold(ps)
            n_bh = sum(1 for p in ps if bar and p <= bar)
            n_raw = sum(1 for p in ps if p <= ALPHA)
            self.assertLessEqual(n_bh, n_raw)

    def test_bonferroni_is_the_strictest_of_the_three(self):
        import random
        rng = random.Random(3)
        ps = [rng.random() ** 4 for _ in range(500)]
        bar, bonf = bh_threshold(ps), ALPHA / len(ps)
        n_bh = sum(1 for p in ps if bar and p <= bar)
        n_bonf = sum(1 for p in ps if p <= bonf)
        self.assertLessEqual(n_bonf, n_bh)


class TheSummaryTheBannerReads(unittest.TestCase):
    """Every number in the note above the Compare table comes from here."""

    def payload(self, ps):
        daily = {f"k{i}": {"days": 1000 + i, "nw_lag": 21, "t_hac": 1.0,
                           "p": p, "excess_pts": 3.0, "mde_80": 12.0}
                 for i, p in enumerate(ps)}
        fill = {f"k{i}": {"cagr": 1.0, "taken": 10, "wiped": False,
                          "beats_hold": i == 0} for i in range(len(ps))}
        return summarise(daily, fill, ["dv|55-20"], ["backtest.py"])

    def test_counts_agree_with_their_own_thresholds(self):
        import random
        rng = random.Random(5)
        ps = [rng.random() ** 3 for _ in range(300)]
        d = self.payload(ps)
        self.assertEqual(d["n_tested"], 300)
        self.assertEqual(d["n_uncorrected"], sum(1 for p in ps if p <= ALPHA))
        self.assertEqual(d["n_bonferroni"],
                         sum(1 for p in ps if p <= d["bonferroni_threshold"]))
        self.assertEqual(d["n_bh"], sum(1 for p in ps
                                        if d["bh_threshold"] and p <= d["bh_threshold"]))
        self.assertEqual(d["n_bh"], sum(1 for p in ps if p <= d["bh_threshold"]))

    def test_expected_by_chance_is_alpha_times_n(self):
        d = self.payload([0.5] * 400)
        self.assertEqual(d["expected_by_chance"], 20.0)

    def test_walk_forward_comparison_is_carried_not_computed(self):
        """The 20 pts/yr the banner contrasts against is scripts.wf_power's
        measured figure, not something recomputed here -- if it ever moves it
        must move by editing the constant, deliberately."""
        d = self.payload([0.5])
        self.assertEqual(d["walk_forward_mde_80"], 20.0)
        self.assertLess(d["median_mde_80"], d["walk_forward_mde_80"])

    def test_the_page_gets_every_key_it_reads(self):
        # Mirrors REQUIRED_DIAG in scripts/check_dashboard.js.
        d = self.payload([0.01, 0.5])
        for k in ("alpha", "n_tested", "bh_threshold", "bonferroni_threshold",
                  "n_uncorrected", "n_bh", "n_bonferroni", "expected_by_chance",
                  "median_mde_80", "median_days", "walk_forward_mde_80",
                  "n_fill_timing", "arm_b_provisional", "arm_b_verified"):
            self.assertIn(k, d)

    def test_empty_is_survivable(self):
        """A --pilot run or a build with no traded cells must not crash the
        merge; it must produce a bar nothing clears."""
        d = summarise({}, {}, [], [])
        self.assertEqual(d["n_tested"], 0)
        self.assertEqual(d["bh_threshold"], 0.0)
        self.assertEqual(d["n_bh"], 0)


class TheCellKey(unittest.TestCase):
    """THE contract. scripts.wf_attach computes its numbers under one key and
    web/dashboard.html looks them up under another; if the two ever disagree
    the page shows an em dash on every row and nothing anywhere errors."""

    def test_grid_key_matches_the_build_script_character_for_character(self):
        from scripts import dashboard_data as dd
        from scripts.wf_attach import grid_key

        class Fake:
            key, variant = "dv", "55-20"

        skey, band, ukey, risk, capital = "dv", "55-20", "recent", 1.0, 10_000_000
        fkey, year, prio = dd.GRID_FILLS[0], 2018, "liquidity"
        # Copied from scripts/dashboard_data.py's grid loop, deliberately as a
        # literal rather than an import: this test exists to notice the day
        # that line changes.
        built = (f"{skey}|{dd.tag(band)}|{ukey}|{risk:g}"
                 f"|{capital}|{fkey}|{year}|{prio}")
        self.assertEqual(grid_key(Fake(), ukey, risk, capital, year, prio), built)
        self.assertEqual(built, "dv|55-20|recent|1|10000000|1|2018|liquidity")

    def test_a_numeric_variant_tags_the_same_way_on_both_sides(self):
        """dd.tag uses %g, which renders 0 as "0" and 0.02 as "0.02". A plain
        str() would give "0" and "0.02" too -- but str(1e7) gives "10000000.0",
        which is why the capital is NOT passed through tag()."""
        from scripts import dashboard_data as dd
        from scripts.wf_attach import sid

        class Fake:
            key, variant = "ema", 0

        self.assertEqual(sid(Fake()), "ema|0")
        self.assertEqual(dd.tag(0), "0")
        self.assertEqual(dd.tag("55-20"), "55-20")


class TheMergeRefusesStaleWork(unittest.TestCase):
    """attach_diagnostics must never merge a diagnostic computed against a
    different build -- it would describe a different account under the right
    cell keys, which is worse than showing nothing."""

    def test_missing_source_names_the_command_that_makes_it(self):
        r = subprocess.run([sys.executable, "-m", "scripts.attach_diagnostics",
                            "--check"], cwd=ROOT, capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
                                "PYTHONPATH": str(ROOT)})
        out = r.stdout + r.stderr
        # Either the diagnostics are present (and it reports), or they are not
        # (and it says exactly how to make them). Never a traceback.
        self.assertNotIn("Traceback", out)
        if r.returncode != 0:
            self.assertIn("scripts.wf_attach", out)


class TheDocumentedContract(unittest.TestCase):
    """The page and the checker must agree on which keys exist."""

    def test_check_dashboard_and_the_page_read_the_same_diagnostic_fields(self):
        page = (ROOT / "web" / "dashboard.html").read_text()
        checker = (ROOT / "scripts" / "check_dashboard.js").read_text()
        for key in ("daily_excess", "fill_timing", "diagnostics", "bh_threshold"):
            self.assertIn(key, page, f"dashboard.html never reads {key}")
            self.assertIn(key, checker, f"check_dashboard.js never checks {key}")

    def test_the_page_still_falls_back_when_the_block_is_absent(self):
        """A payload built by dashboard_data alone must still render verdicts.
        The fallback is one expression; this pins that it is still there."""
        page = (ROOT / "web" / "dashboard.html").read_text()
        self.assertIn("const durability = diag ? dailyExcess : walkForwardMajority;", page)

    def test_refresh_runs_both_stages_after_the_grid(self):
        src = (ROOT / "scripts" / "refresh.py").read_text()
        i_grid = src.index('run("scripts.dashboard_data"')
        i_attach = src.index('run("scripts.wf_attach"')
        i_merge = src.index('run("scripts.attach_diagnostics"')
        self.assertLess(i_grid, i_attach)
        self.assertLess(i_attach, i_merge)


if __name__ == "__main__":
    unittest.main()

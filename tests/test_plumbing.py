"""Registry, config, caches, curves, and the overlap filter.

Small pieces, but three of them decide what the rest of the project is even
allowed to see: the registry decides which strategies exist and what the cache
stamp covers, config decides which stocks, and drop_overlaps decides what counts
as a trade.
"""
import unittest

import pandas as pd

from kitelab import config, curves, registry, signals, strategies


class Registry(unittest.TestCase):
    def test_every_strategy_has_the_fields_the_build_needs(self):
        for s in registry.REGISTRY:
            for field in ("key", "variant", "label", "cache", "module", "build"):
                self.assertTrue(getattr(s, field, None) is not None,
                                f"{s.key} is missing {field}")

    def test_cache_names_are_unique(self):
        """Two strategies sharing a cache stem would silently serve each other's
        trades -- the stamp records the universe and the code, not the rule."""
        stems = [s.cache for s in registry.REGISTRY]
        self.assertEqual(len(stems), len(set(stems)))

    def test_key_and_variant_pairs_are_unique(self):
        pairs = [(s.key, s.variant) for s in registry.REGISTRY]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_every_declared_module_exists_on_disk(self):
        """The module name feeds the cache stamp. A typo would mean the file was
        silently skipped when hashing -- exactly the 2026-09-02 bug."""
        import pathlib
        here = pathlib.Path(registry.__file__).parent
        for name in registry.modules():
            self.assertTrue((here / name).exists(), f"{name} does not exist")

    def test_registering_adds_to_every_derived_view(self):
        before = len(registry.REGISTRY)
        registry.register(key="tmp", variant="v", label="T", cache="Tmp_v",
                          module="backtest.py", build=lambda s: [])
        try:
            self.assertEqual(len(registry.REGISTRY), before + 1)
            self.assertIn("tmp", registry.families())
            self.assertEqual(registry.primary("tmp"), "v")
            self.assertIsNotNone(registry.find("tmp", "v"))
        finally:
            registry.REGISTRY.pop()

    def test_an_unknown_family_has_no_primary(self):
        self.assertIsNone(registry.primary("nope"))


class ConfigUniverse(unittest.TestCase):
    def test_merged_holds_no_duplicates(self):
        cfg = config.load()
        self.assertEqual(len(cfg.merged), len(set(cfg.merged)))

    def test_merged_contains_both_sides_of_the_retired_split(self):
        cfg = config.load()
        self.assertTrue(set(cfg.all_symbols) <= set(cfg.merged))
        self.assertTrue(set(cfg.out_of_sample) <= set(cfg.merged))

    def test_the_two_sides_of_the_old_split_never_overlap(self):
        cfg = config.load()
        self.assertEqual(set(cfg.all_symbols) & set(cfg.out_of_sample), set())

    def test_excluded_symbols_reach_no_universe(self):
        """Each exclusion carries a measurement that justifies it; a stock that
        leaked back in would carry its seam or its padding with it."""
        cfg = config.load()
        for name in config.EXCLUDED:
            self.assertNotIn(name, cfg.merged)


class Stamp(unittest.TestCase):
    def test_a_different_universe_gives_a_different_stamp(self):
        self.assertNotEqual(signals.stamp(["AAA", "BBB"]),
                            signals.stamp(["AAA", "CCC"]))

    def test_the_stamp_does_not_depend_on_the_order_given(self):
        self.assertEqual(signals.stamp(["BBB", "AAA"]), signals.stamp(["AAA", "BBB"]))

    def test_the_code_digest_covers_every_producer_and_support_module(self):
        files = signals._code_files()
        for name in registry.modules():
            self.assertIn(name, files)
        for support in ("frames.py", "sizing.py", "slippage.py", "indicators.py"):
            self.assertIn(support, files)


class DropOverlaps(unittest.TestCase):
    def _t(self, symbol, entry, exit_):
        return {"symbol": symbol, "entry_ts": pd.Timestamp(entry),
                "exit_ts": pd.Timestamp(exit_)}

    def test_a_signal_during_an_open_trade_is_dropped(self):
        got = strategies.drop_overlaps([
            self._t("A", "2020-01-01", "2020-06-01"),
            self._t("A", "2020-02-01", "2020-03-01")])
        self.assertEqual(len(got), 1)

    def test_the_same_dates_in_a_different_symbol_are_kept(self):
        got = strategies.drop_overlaps([
            self._t("A", "2020-01-01", "2020-06-01"),
            self._t("B", "2020-02-01", "2020-03-01")])
        self.assertEqual(len(got), 2)

    def test_a_signal_after_the_exit_is_kept(self):
        got = strategies.drop_overlaps([
            self._t("A", "2020-01-01", "2020-02-01"),
            self._t("A", "2020-03-01", "2020-04-01")])
        self.assertEqual(len(got), 2)

    def test_it_is_a_no_op_on_rules_that_already_walk_forward(self):
        """EMA, Q/M/W and Darvas cannot overlap by construction. Verified on
        2026-08-31: 0 dropped from 9,224 / 3,011 / 18,296 / 6,908 / 3,197."""
        walk = [self._t("A", f"2020-0{i}-01", f"2020-0{i}-15") for i in range(1, 7)]
        self.assertEqual(len(strategies.drop_overlaps(walk)), len(walk))


class Curves(unittest.TestCase):
    def _curve(self, values):
        stamps = pd.date_range("2020-01-01", periods=len(values), freq="D")
        return list(zip(stamps, [float(v) for v in values]))

    def test_episodes_are_deepest_first(self):
        got = curves.episodes(self._curve([100, 90, 100, 50, 100, 95, 100]))
        depths = [e["depth_pct"] for e in got]
        self.assertEqual(depths, sorted(depths))

    def test_a_curve_that_only_rises_has_no_episodes(self):
        self.assertEqual(curves.episodes(self._curve(range(100, 140))), [])

    def test_underwater_returns_longest_and_current(self):
        longest, current = curves.underwater_stats(self._curve([100, 90, 90, 90, 110]))
        self.assertGreater(longest, 0)
        self.assertEqual(current, 0)          # recovered by the end

    def test_a_curve_still_underwater_reports_a_running_spell(self):
        _, current = curves.underwater_stats(self._curve([100, 120, 90, 90, 90]))
        self.assertGreater(current, 0)

    def test_buy_and_hold_stats_come_from_the_closes(self):
        frame = pd.DataFrame({
            "ts": pd.date_range("2020-01-01", periods=400, freq="D"),
            "close": [100.0 * (1.001 ** i) for i in range(400)]})
        got = curves.bh_stats(frame)
        self.assertGreater(got["cagr"], 0)
        self.assertLessEqual(got["maxdd"], 0)


if __name__ == "__main__":
    unittest.main()

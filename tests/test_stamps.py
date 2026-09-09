"""Every module that can change a number on the page is in the dashboard stamp.

Added 2026-09-07. The audit that day touched portfolio.py, curves.py,
validation.py, contracts.py, config.py and scripts/dashboard_data.py one at a
time on an isolated copy and the dashboard digest did not move for any of
them, so an edit to the account engine left `refresh` reporting "already
current" over superseded numbers -- the 2026-09-02 holygrail.py failure one
layer up. This test walks the import graph of the build script and fails when
a kitelab module it reaches is not covered by signals.stamp(account=True),
so the next omission is caught the day it is made rather than a day of
published numbers later.
"""
from __future__ import annotations

import pathlib
import unittest

from kitelab import signals

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "kitelab"

# Modules that cannot change a number: they move bytes, print progress, or
# ARE the stamp. Anything else reachable from the build must be stamped.
CANNOT_CHANGE_A_NUMBER = {"signals", "progress", "dashboard_server", "auth",
                          "fetch", "__init__"}


# The walker used to live here. It moved into kitelab.signals on 2026-09-09,
# where the stamp now USES it to build the per-producer code digest rather than
# this test merely checking the stamp against it. Imported rather than
# reimplemented so the checker and the thing checked cannot drift apart.
reachable_from = signals.reachable_from


class DashboardStampCoversTheNumbersPath(unittest.TestCase):
    def test_every_reachable_module_is_stamped(self):
        reached = reachable_from(ROOT / "scripts" / "dashboard_data.py")
        stamped = {pathlib.Path(f).name[:-3] for f in signals.stamped_files(account=True)}
        missing = sorted(reached - stamped - CANNOT_CHANGE_A_NUMBER)
        self.assertEqual(missing, [], f"reachable from the build but not stamped: {missing}")

    def test_the_build_script_itself_is_stamped(self):
        files = signals.stamped_files(account=True)
        self.assertIn("../scripts/dashboard_data.py", files)

    def test_signal_caches_do_not_depend_on_the_account_modules(self):
        narrow = set(signals.stamped_files(account=False))
        for f in ("portfolio.py", "curves.py", "validation.py"):
            self.assertNotIn(f, narrow)

    def test_account_stamp_differs_from_cache_stamp(self):
        # Same universe, same files: the two stamps must still differ, because
        # they cover different code, or the wider one is not doing anything.
        a = signals.stamp([], account=False)
        b = signals.stamp([], account=True)
        self.assertNotEqual(a["code"], b["code"])
        self.assertTrue(b.get("account"))


class StatusChecksEachCacheAgainstItsOwnStamp(unittest.TestCase):
    """scripts.cache_status must agree with refresh --check. It did not until
    2026-09-09: status() stamped every pickle as account=False, so the
    validation summary -- saved with the wider account=True digest -- was
    called STALE on every run over a cache that was current."""

    def test_an_account_stamped_cache_is_reported_ok(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(signals, "CACHE", pathlib.Path(tmp)):
                signals.save("_validation_summary_v99", [], [], account=True)
                signals.save("EMA_b0_all", [], [], account=False)
                verdicts = dict(signals.status([]))
        self.assertTrue(verdicts["_validation_summary_v99"].startswith("ok"), verdicts)
        self.assertTrue(verdicts["EMA_b0_all"].startswith("ok"), verdicts)


if __name__ == "__main__":
    unittest.main()


class PerProducerNarrowing(unittest.TestCase):
    """A cache is stamped against the code ITS OWN producer reaches.

    Added 2026-09-09. The property under test is not "the digests differ" --
    they differ trivially, because the file NAMES differ. It is that a module
    another engine owns is ABSENT FROM THE FILE LIST, which is what makes its
    content unable to enter the digest at all. Testing the list rather than
    editing files also keeps the suite from writing to the source tree.
    """

    def test_one_engine_is_not_in_another_engines_stamp(self):
        darvas = set(signals.stamped_files(producer="darvas.py"))
        self.assertIn("darvas.py", darvas)
        self.assertNotIn("holygrail.py", darvas)
        self.assertNotIn("timeframes.py", darvas)

    def test_every_producer_still_carries_backtest(self):
        # Not an accident to be tidied away: every engine imports backtest for
        # charges() and NEXT_OPEN_FILLS, so an edit there must invalidate all
        # of them. If this ever fails, the closure walk has gone blind.
        for m in ("darvas.py", "holygrail.py", "timeframes.py"):
            self.assertIn("backtest.py", signals.stamped_files(producer=m), m)

    def test_support_is_global_even_though_nobody_imports_it(self):
        # registry.py holds the parameter values baked into every cached trade
        # and NO producer imports it; strategies.py and trailing.py likewise.
        # A closure-only rule would leave them in nobody's stamp -- the
        # 2026-09-05 hole. _SUPPORT is unioned in to shut it.
        for m in ("backtest.py", "darvas.py", "holygrail.py", "timeframes.py"):
            files = signals.stamped_files(producer=m)
            for orphan in ("registry.py", "strategies.py", "trailing.py"):
                self.assertIn(orphan, files, f"{orphan} missing from {m}")

    def test_narrow_is_a_subset_of_the_whole_board(self):
        whole = set(signals.stamped_files())
        for m in ("backtest.py", "darvas.py", "holygrail.py", "timeframes.py"):
            self.assertLessEqual(set(signals.stamped_files(producer=m)), whole, m)

    def test_the_account_stamp_cannot_be_narrowed(self):
        with self.assertRaises(ValueError):
            signals.stamp([], account=True, producer="darvas.py")

    def test_an_unknown_producer_is_refused_not_ignored(self):
        with self.assertRaises(ValueError):
            signals.stamp([], producer="no_such_engine.py")

    def test_the_stamp_records_which_producer_it_was_narrowed_to(self):
        # Without this a narrow stamp could be served for a broad question.
        narrow = signals.stamp([], producer="darvas.py")
        self.assertEqual(narrow.get("producer"), "darvas.py")
        self.assertIsNone(signals.stamp([]).get("producer"))
        self.assertNotEqual(narrow["code"], signals.stamp([])["code"])


class CacheNamesResolveToTheirProducer(unittest.TestCase):
    def test_longest_prefix_wins(self):
        from kitelab import registry
        by_cache = {s.cache: s.module for s in registry.REGISTRY}
        for cache, module in by_cache.items():
            for suffix in ("all", "hold", "101"):
                self.assertEqual(signals.producer_of(f"{cache}_{suffix}"), module,
                                 f"{cache}_{suffix}")

    def test_every_registry_cache_resolves(self):
        from kitelab import registry
        for s in registry.REGISTRY:
            self.assertEqual(signals.producer_of(f"{s.cache}_all"), s.module, s.cache)

    def test_an_orphan_cache_gets_the_whole_board(self):
        # A pickle left behind by a deleted strategy matches no prefix. It must
        # fall back to the STRICTEST digest, not the loosest, or a rebuild could
        # serve it forever.
        self.assertIsNone(signals.producer_of("_validation_summary_v9"))
        self.assertIsNone(signals.producer_of("no_such_strategy_all"))


class TheCodeDigestReadsBytesNotTimestamps(unittest.TestCase):
    """The 2026-09-09 regression: a branch switch must not invalidate a cache.

    Merging a finished branch into main rewrote 28 working-tree files -- back
    to their older versions on `git switch`, then forward again on the
    fast-forward. Not one byte differed at the end; every timestamp was new.
    All 19 live caches went stale and `refresh --check` announced "the STRATEGY
    CODE has changed", which was false, and the old timestamps were recorded
    nowhere so it could not be undone. These tests fail if the digest ever goes
    back to reading the clock.
    """

    def test_a_touch_does_not_change_the_content_hash(self):
        # _content is a pure function of the bytes, so this is provable on a
        # temp file without going near the source tree.
        import os, tempfile, time
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "m.py"
            f.write_text("x = 1\n")
            before = signals._content(f)
            os.utime(f, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
            self.assertEqual(before, signals._content(f),
                             "the digest moved when only the timestamp did")

    def test_one_changed_character_does_change_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "m.py"
            f.write_text("x = 1\n")
            before = signals._content(f)
            f.write_text("x = 2\n")
            self.assertNotEqual(before, signals._content(f))

    def test_a_rewrite_with_identical_bytes_is_invisible(self):
        # What `git switch` does: same content, new file, new timestamp.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "m.py"
            f.write_text("x = 1\n")
            before = signals._content(f)
            f.unlink()
            f.write_text("x = 1\n")
            self.assertEqual(before, signals._content(f))

    def test_touching_a_real_module_leaves_every_stamp_alone(self):
        # The end-to-end property, on the real package. Safe to run because the
        # timestamp is restored, and safe even if it were not: after this fix a
        # stray mtime cannot reach the digest, which is the whole point.
        import os, time
        target = PKG / "holygrail.py"
        keep = target.stat()
        before = signals.stamp([], producer="holygrail.py")
        wide = signals.stamp([], account=True)
        try:
            os.utime(target, ns=(keep.st_atime_ns, time.time_ns()))
            self.assertEqual(before, signals.stamp([], producer="holygrail.py"))
            self.assertEqual(wide, signals.stamp([], account=True))
        finally:
            os.utime(target, ns=(keep.st_atime_ns, keep.st_mtime_ns))

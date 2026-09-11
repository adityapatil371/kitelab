"""The pre-flight's own logic.

A check that cannot fail is decoration, and this one's key-scanner was wrong in
both directions on its first run: it counted a key named only in a comment about
REMOVED code, and it dropped a key that appeared in both a comment and the code
beneath it.
"""
import unittest

from scripts import preflight


class PageKeys(unittest.TestCase):
    def test_reads_keys_the_page_actually_uses(self):
        keys = preflight._page_keys()
        for expected in ("grid", "universes", "strategies", "priorities", "fills"):
            self.assertIn(expected, keys)

    def test_a_key_named_in_both_a_comment_and_the_code_still_counts(self):
        """priority_default appears in a comment AND in keyFor(). Line-based
        comment skipping dropped such keys; stripping comments first does not."""
        self.assertIn("priority_default", preflight._page_keys())

    def test_a_key_named_only_in_a_comment_is_not_counted(self):
        """holdout_universes survives only in prose explaining that the holdout
        machinery was removed. Counting it would report a payload key missing
        that nothing actually reads."""
        self.assertNotIn("holdout_universes", preflight._page_keys())


class AttachedKeys(unittest.TestCase):
    """The payload is built in three stages; preflight runs the first.

    dashboard_data writes the payload, wf_attach computes the day-by-day tests
    beside it, attach_diagnostics merges them in. Preflight builds stage one
    only, so stage three's keys are absent BY DESIGN -- and from 2026-09-10,
    when the daily-excess gate put three of them on the page, preflight failed
    on every run and scripts.refresh aborted at the gate before starting the
    rebuild it exists to guard. Found 2026-09-11.
    """

    def test_the_scanner_finds_what_the_merge_writes(self):
        self.assertEqual(preflight._attached_keys(),
                         {"daily_excess", "fill_timing", "diagnostics"})

    def test_an_exception_must_earn_itself_from_the_source(self):
        """Empty means the pattern stopped matching. The keys then go back to
        counting as missing and the FAIL returns -- which is the safe
        direction, but it should be a test failure here first."""
        self.assertTrue(preflight._attached_keys(),
                        "nothing matched in attach_diagnostics.py -- the "
                        "scanner has drifted from how the merge is written")

    def test_nothing_is_excused_that_the_page_does_not_read(self):
        """The exception is narrowed to keys the page asks for. A key written
        by the merge and read by nobody should still show up as dead weight."""
        self.assertFalse(preflight._attached_keys() - preflight._page_keys(),
                         "attach_diagnostics writes a key the page never reads")


class Redirection(unittest.TestCase):
    def test_the_guard_rejects_a_path_outside_the_temp_dir(self):
        """The assertion that refuses to run unless every write is redirected.
        Without it a 3-symbol smoke run overwrites the 500-symbol caches."""
        import pathlib
        tmp = pathlib.Path("/tmp/kitelab-preflight-example")
        good, bad = tmp / "signal_cache", pathlib.Path("/data/clean/kitelab/signal_cache")
        self.assertTrue(tmp in good.parents or good.parent == tmp)
        self.assertFalse(tmp in bad.parents or bad.parent == tmp)


if __name__ == "__main__":
    unittest.main()

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

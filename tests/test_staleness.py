"""dashboard_server.status -- whether you are told the numbers are old.

The one piece of code whose failure mode is that nothing appears to fail. If it
wrongly answers "current", the page serves superseded results with no banner and
every conclusion drawn from them is unmarked. Untested until 2026-09-03.

Everything is redirected to a temp directory: a test that read the real stamp
would pass or fail depending on when the dashboard was last built.
"""
import contextlib
import json
import pathlib
import tempfile
import unittest

from kitelab import config, dashboard_server, signals


@contextlib.contextmanager
def snapshot(symbols_built=None, inputs="match", data_exists=True, stamp_exists=True):
    """A dashboard on disk, with a stamp we control."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kitelab-status-"))
    saved = (dashboard_server.DATA_PATH, dashboard_server.STAMP_PATH, config.load)
    dashboard_server.DATA_PATH = tmp / "dashboard.json"
    dashboard_server.STAMP_PATH = tmp / "stamp.json"

    now = ["AAA", "BBB", "CCC"]
    import dataclasses
    real = config.load()
    config.load = lambda: dataclasses.replace(
        real, symbols=now, extended=[], holdout=[], unseen=[])

    if data_exists:
        dashboard_server.DATA_PATH.write_text("{}")
    if stamp_exists:
        built_over = now if symbols_built is None else symbols_built
        body = {"symbols": built_over, "built": "2026-09-03 10:00 IST"}
        if inputs == "match":
            body["inputs"] = signals.stamp(sorted(built_over))
        elif inputs == "moved":
            body["inputs"] = dict(signals.stamp(sorted(built_over)), code="deadbeef")
        # inputs == "absent" leaves the key out entirely
        dashboard_server.STAMP_PATH.write_text(json.dumps(body))
    try:
        yield
    finally:
        (dashboard_server.DATA_PATH, dashboard_server.STAMP_PATH, config.load) = saved


class Status(unittest.TestCase):
    def test_a_matching_universe_and_matching_inputs_is_current(self):
        with snapshot():
            got = dashboard_server.status()
        self.assertFalse(got["stale"])

    def test_a_different_universe_is_stale(self):
        with snapshot(symbols_built=["AAA", "BBB"]):
            got = dashboard_server.status()
        self.assertTrue(got["stale"])
        self.assertIn("message", got)

    def test_changed_inputs_are_stale_even_when_the_universe_matches(self):
        """The subtle case: the same stocks, but the price files or the strategy
        code moved underneath. Checking only the universe would call this
        current, which is exactly how a rewritten strategy served old trades."""
        with snapshot(inputs="moved"):
            got = dashboard_server.status()
        self.assertTrue(got["stale"])
        self.assertEqual(got.get("reason"), "inputs")
        self.assertIn("STRATEGY CODE", got["message"])

    def test_a_stamp_without_input_tracking_is_stale_not_assumed_good(self):
        """An unrecorded input is an unknown one, and an unknown must not be
        reported as current -- that is the direction that misleads."""
        with snapshot(inputs="absent"):
            got = dashboard_server.status()
        self.assertTrue(got["stale"])
        self.assertEqual(got.get("reason"), "unrecorded")

    def test_no_stamp_at_all_is_stale(self):
        with snapshot(stamp_exists=False):
            self.assertTrue(dashboard_server.status()["stale"])

    def test_no_dashboard_at_all_is_not_stale_but_is_not_ok(self):
        """Nothing to be stale ABOUT. The page shows "no data yet", not a
        warning that the data is old."""
        with snapshot(data_exists=False):
            got = dashboard_server.status()
        self.assertFalse(got["stale"])
        self.assertFalse(got["ok"])

    def test_every_stale_verdict_carries_a_message(self):
        for kw in ({"symbols_built": ["AAA"]}, {"inputs": "moved"},
                   {"inputs": "absent"}, {"stamp_exists": False}):
            with snapshot(**kw):
                got = dashboard_server.status()
            self.assertTrue(got["stale"])
            self.assertTrue(got.get("message"), f"{kw} gave no message")


if __name__ == "__main__":
    unittest.main()

"""The grid is rebuilt in parts, and a reused part is the part it replaced.

Added 2026-09-09. The grid was one indivisible 10,260-cell pass, ~59 minutes of
a ~103-minute rebuild: an interruption lost all of it, and a one-engine edit
repriced all 19 variants even after signals.py learned to stamp a cache against
its own producer's import closure. It is now cut into one checkpoint per (fill,
strategy, variant).

Two failures would be expensive and silent, so both are pinned here:

  A reused partition that is not identical to the computed one. That publishes
  numbers nobody computed, which is worse than the slow rebuild it replaced.

  A digest that moves when a PRODUCER module is touched. The producers' effect
  is already in the trades hash, exactly; hashing their mtimes as well would
  throw away the narrowing the checkpoints exist to exploit, and a docstring
  edit in darvas.py would reprice Holy Grail.
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest

from kitelab import registry, signals
from scripts import dashboard_data as dd

UNIS = {"all": ("1000 stocks", None), "large": ("200 large caps", ["AAA", "BBB"])}
AXES = ([0.5, 1.0], [200_000, 10_000_000], [2018, 2022], "1")
TRADES = [{"symbol": "AAA", "entry_ts": "2020-01-01", "net_profit": 10.0},
          {"symbol": "BBB", "entry_ts": "2020-02-01", "net_profit": -3.0}]
CELLS = {"ema|0|all|0.5|200000|1|2018|liquidity": {"cagr": 12.5, "curve": [1.0, 1.1]},
         "ema|0|all|1|200000|1|2018|liquidity": None}


def digest(trades=TRADES, unis=None):
    return dd._grid_digest(trades, UNIS if unis is None else unis, *AXES)


class ACheckpointRoundTrips(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.dir.name) / "part.pkl"
        self.addCleanup(self.dir.cleanup)
        # _ckpt_save mkdirs GRID_CKPT, which must not be the real one in a test.
        self._real, dd.GRID_CKPT = dd.GRID_CKPT, pathlib.Path(self.dir.name)
        self.addCleanup(lambda: setattr(dd, "GRID_CKPT", self._real))

    def test_what_comes_back_is_what_went_in(self):
        dd._ckpt_save(self.path, "abc", CELLS)
        self.assertEqual(dd._ckpt_load(self.path, "abc"), CELLS)

    def test_a_none_cell_survives_the_round_trip(self):
        # Empty windows are stored as None and the page distinguishes them from
        # a zero. A round trip that turned one into {} would be invisible here
        # and wrong on the page.
        dd._ckpt_save(self.path, "abc", CELLS)
        back = dd._ckpt_load(self.path, "abc")
        self.assertIsNone(back["ema|0|all|1|200000|1|2018|liquidity"])

    def test_a_stale_digest_is_refused(self):
        dd._ckpt_save(self.path, "abc", CELLS)
        self.assertIsNone(dd._ckpt_load(self.path, "different"))

    def test_a_missing_file_is_a_miss_not_a_crash(self):
        self.assertIsNone(dd._ckpt_load(self.path, "abc"))

    def test_a_truncated_file_is_a_miss_not_a_crash(self):
        # What a kill during a write used to leave behind. It must rebuild, not
        # raise 50 minutes into a run.
        dd._ckpt_save(self.path, "abc", CELLS)
        self.path.write_bytes(self.path.read_bytes()[:20])
        self.assertIsNone(dd._ckpt_load(self.path, "abc"))

    def test_the_write_is_atomic(self):
        dd._ckpt_save(self.path, "abc", CELLS)
        left = list(pathlib.Path(self.dir.name).glob("*.tmp"))
        self.assertEqual(left, [], f"temporary file left behind: {left}")


class TheDigestCoversWhatACellDependsOn(unittest.TestCase):
    def test_it_is_stable(self):
        self.assertEqual(digest(), digest())

    def test_the_trades_move_it(self):
        other = [dict(TRADES[0], net_profit=11.0), TRADES[1]]
        self.assertNotEqual(digest(), digest(trades=other))

    def test_a_dropped_trade_moves_it(self):
        self.assertNotEqual(digest(), digest(trades=TRADES[:1]))

    def test_the_universe_members_move_it(self):
        wider = {"all": ("1000 stocks", None),
                 "large": ("200 large caps", ["AAA", "BBB", "CCC"])}
        self.assertNotEqual(digest(), digest(unis=wider))

    def test_the_universe_label_moves_it(self):
        # The label is rendered on the page, so a partition whose cells were
        # computed under an old label must not be served under a new one.
        relabelled = {"all": ("999 stocks", None), "large": ("200 large caps", ["AAA", "BBB"])}
        self.assertNotEqual(digest(), digest(unis=relabelled))

    def test_member_order_does_not_move_it(self):
        # Membership is a set; a reordered list is the same universe and must
        # not force an hour of recomputation.
        flipped = {"all": ("1000 stocks", None), "large": ("200 large caps", ["BBB", "AAA"])}
        self.assertEqual(digest(), digest(unis=flipped))

    def test_each_axis_moves_it(self):
        base = digest()
        for i, other in ((0, [0.5]), (1, [200_000]), (2, [2018]), (3, "0")):
            axes = list(AXES)
            axes[i] = other
            self.assertNotEqual(base, dd._grid_digest(TRADES, UNIS, *axes), f"axis {i}")


class TheDigestIgnoresTheProducers(unittest.TestCase):
    """The property the whole per-part scheme rests on."""

    def test_no_producer_module_is_in_the_hashed_mtime_set(self):
        # _grid_digest hashes the mtimes of signals._ACCOUNT and nothing else,
        # so this is what makes a darvas.py edit unable to reprice Holy Grail.
        # The trades hash already carries any real effect a producer had.
        account = {pathlib.Path(f).name for f in signals._ACCOUNT}
        producers = set(registry.modules())
        self.assertEqual(account & producers, set(),
                         "a producer module is in the grid digest -- the "
                         "per-partition reuse is defeated")

    def test_this_file_is_in_the_hashed_mtime_set(self):
        # run_payload lives here, so an edit to it must invalidate every
        # partition. It is in _ACCOUNT under its scripts/ path.
        self.assertIn("../scripts/dashboard_data.py", signals._ACCOUNT)


class PartitionsDoNotCollide(unittest.TestCase):
    def test_the_two_passes_get_different_files(self):
        # Equities and the BITCOIN/GOLD pass grid the same (fkey, skey, band)
        # space over different universes. One path for both would mean each run
        # overwrote the other's checkpoint and nothing was ever reused.
        a = dd._ckpt_path("equity_1", "ema", 0)
        b = dd._ckpt_path("assets_1", "ema", 0)
        self.assertNotEqual(a, b)

    def test_every_board_strategy_gets_its_own_file(self):
        paths = {dd._ckpt_path("equity_1", s.key, s.variant) for s in registry.REGISTRY}
        self.assertEqual(len(paths), len(registry.REGISTRY),
                         "two strategies share a checkpoint path")

    def test_the_path_is_a_safe_filename(self):
        p = dd._ckpt_path("equity_1", "pair", "W/D")
        self.assertNotIn("/", p.name)
        self.assertTrue(p.name.endswith(".pkl"))


if __name__ == "__main__":
    unittest.main()

"""The entries.py board rules, and the one piece of state they share.

Five of the six entries read only the symbol's own bars, so they are covered
by the signal tests. `xrank` is different: it ranks every stock against every
other stock on the same session, so it caches a universe-wide panel. That
cache is the thing worth pinning -- see test_panel_follows_its_price_source.
"""
import contextlib
import dataclasses
import hashlib
import io
import unittest

import numpy as np
import pandas as pd

from kitelab import config, entries, frames

SYMBOLS = [f"S{i:02d}" for i in range(12)]
SESSIONS = 300          # > MOM_LOOKBACK, so the 252-day return exists


def _frame(seed: int) -> pd.DataFrame:
    """A plain synthetic daily frame; `seed` decides the price path."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.02, SESSIONS))
    return pd.DataFrame({
        "ts": pd.date_range("2018-01-01", periods=SESSIONS, freq="D"),
        "open": close, "high": close * 1.01,
        "low": close * 0.99, "close": close,
        "volume": [10_000] * SESSIONS,
    })


def _source(offset: int):
    """A frames.daily stand-in. A distinct object per call, as the real
    permutation path builds a distinct closure per shuffle round."""
    def daily(symbol, *a, **k):
        return _frame(SYMBOLS.index(symbol) + offset)
    return daily


def _digest(panel) -> str:
    return hashlib.sha256(panel.to_numpy().tobytes()).hexdigest()


@contextlib.contextmanager
def _universe():
    """config.load() and entries' panel cache, both restored afterwards."""
    real = config.load()
    saved_load, saved_daily = config.load, frames.daily
    config.load = lambda: dataclasses.replace(
        real, symbols=list(SYMBOLS), extended=[], holdout=[], unseen=[])
    entries.clear_caches()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        config.load, frames.daily = saved_load, saved_daily
        entries.clear_caches()


class RankPanelCache(unittest.TestCase):

    def test_panel_follows_its_price_source(self):
        """A new price source must produce a new panel.

        The regression this pins (2026-09-17): `_PANEL` was a plain process
        global, built once and never invalidated. validation._shuffled_cagrs
        swaps frames.daily for a closure serving SHUFFLED bars on every round,
        so the stale panel dated xrank's entries from one market while the
        trades were simulated on another -- and if the real board warmed the
        cache first, the shuffled control kept the REAL cross-sectional ranks
        and the permutation gate tested nothing at all.
        """
        with _universe():
            frames.daily = _source(0)
            real = _digest(entries._rank_panel())

            frames.daily = _source(1000)
            shuffled = _digest(entries._rank_panel())
            self.assertNotEqual(real, shuffled,
                                "a different market returned the same panel")

            frames.daily = _source(2000)
            self.assertNotEqual(shuffled, _digest(entries._rank_panel()),
                                "two different markets shared one panel")

            frames.daily = _source(0)
            self.assertEqual(real, _digest(entries._rank_panel()),
                             "the original market did not reproduce its panel")

    def test_panel_is_cached_within_one_source(self):
        """The fix must not turn the cache off: one source, one build."""
        with _universe():
            frames.daily = _source(0)
            first = entries._rank_panel()
            self.assertIs(first, entries._rank_panel())

    def test_clear_caches_forgets_the_source(self):
        with _universe():
            frames.daily = _source(0)
            first = entries._rank_panel()
            entries.clear_caches()
            self.assertIsNone(entries._PANEL)
            self.assertIsNone(entries._PANEL_SRC)
            self.assertIsNot(first, entries._rank_panel())

    def test_panel_scope_narrows_the_market(self):
        """Inside panel_scope the panel ranks only those names."""
        with _universe():
            frames.daily = _source(0)
            few = SYMBOLS[:4]
            with entries.panel_scope(few):
                self.assertEqual(list(entries._rank_panel().columns), few)
            self.assertEqual(list(entries._rank_panel().columns), SYMBOLS)

    def test_panel_scope_is_part_of_the_cache_key(self):
        """Two scopes, one source: the panel must not be shared between them.

        Pins the OOM fix (2026-09-17): the permutation path narrows the scope
        per round, so a cache keyed on the price source alone would hand the
        60-symbol round the 1,000-symbol panel it was meant to avoid building.
        """
        with _universe():
            frames.daily = _source(0)
            whole = entries._rank_panel()
            with entries.panel_scope(SYMBOLS[:4]):
                self.assertIsNot(whole, entries._rank_panel())
            self.assertEqual(_digest(whole), _digest(entries._rank_panel()))

    def test_panel_scope_restores_on_error(self):
        with _universe():
            with self.assertRaises(RuntimeError):
                with entries.panel_scope(SYMBOLS[:4]):
                    raise RuntimeError("boom")
            self.assertIsNone(entries._PANEL_SCOPE)

    def test_a_dead_source_is_a_miss(self):
        """The panel holds its source WEAKLY, so the previous round's frames
        can be freed. A collected source must read as a miss, never as a hit
        against whatever object later lands on its address."""
        with _universe():
            frames.daily = _source(0)
            entries._rank_panel()
            self.assertTrue(entries._panel_is_current())
            frames.daily = _source(0)          # same bars, new object
            self.assertFalse(entries._panel_is_current())


if __name__ == "__main__":
    unittest.main()

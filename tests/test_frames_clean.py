"""The 2026-09-07 data rules: listing breaks, demergers, configured starts,
one bar per session, intraday ex-date sessions, the fetch resume, and a
missing levels file.

Hermetic: synthetic frames, a fake Kite, a temp directory. Each rule was
written after a specific file was found trading on bad bars (ROTO through a
1,469-day suspension at -53.5R; COALINDIA's doubled 2015-12-31; DIVISLAB's
15-minute bars at twice the daily close on its split day), so each test is
that shape, reduced to a handful of bars.
"""
import contextlib
import io
import pathlib
import tempfile
import unittest
from datetime import date, timedelta

import pandas as pd

from kitelab import config, fetch, frames, levels
from tests.support import bars


def daily(n, start, close=100.0):
    """n daily bars from `start`, one per weekday."""
    stamps = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"ts": stamps, "open": close, "high": close * 1.01,
                         "low": close * 0.99, "close": close, "volume": 10_000})


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


@contextlib.contextmanager
def configured(demergers=None, history_starts=None):
    """Temporarily replace config.DEMERGERS / HISTORY_STARTS, which frames reads
    at call time through the module."""
    saved = (config.DEMERGERS, config.HISTORY_STARTS)
    config.DEMERGERS = demergers if demergers is not None else {}
    config.HISTORY_STARTS = history_starts if history_starts is not None else {}
    frames._CLEAN_WARNED.clear()
    try:
        yield
    finally:
        config.DEMERGERS, config.HISTORY_STARTS = saved
        frames._CLEAN_WARNED.clear()


class ListingBreak(unittest.TestCase):
    """A gap over LISTING_BREAK_DAYS between sessions restarts the history.
    ROTO: 88 bars to 2018-04-13, then nothing until 2022-04-21."""

    def _split(self, gap_days):
        before = daily(50, "2018-01-01", close=83.35)
        after = daily(50, pd.Timestamp("2018-01-01") + pd.Timedelta(days=gap_days),
                      close=37.55)
        return pd.concat([before, after], ignore_index=True), after["ts"].iloc[0]

    def test_bars_before_the_last_long_gap_are_dropped(self):
        frame, restart = self._split(1469)
        with configured():
            got = quiet(frames.sanitise, frame, "ROTO", "daily")
        self.assertEqual(len(got), 50)
        self.assertEqual(got["ts"].iloc[0], restart)

    def test_a_gap_under_the_limit_is_not_a_break(self):
        frame, _ = self._split(frames.LISTING_BREAK_DAYS - 10)
        with configured():
            got = quiet(frames.sanitise, frame, "X", "daily")
        self.assertEqual(len(got), len(frame))

    def test_the_last_break_wins_when_there_are_several(self):
        a = daily(20, "2010-01-01")
        b = daily(20, "2012-01-01")
        c = daily(20, "2015-01-01")
        frame = pd.concat([a, b, c], ignore_index=True)
        with configured():
            got = quiet(frames.sanitise, frame, "X", "daily")
        self.assertEqual(got["ts"].iloc[0], c["ts"].iloc[0])
        self.assertEqual(len(got), 20)

    def test_intraday_frames_are_not_cut_by_this_rule(self):
        """The 15-minute frame inherits the cut from the daily one in base_15m;
        sanitise itself must not measure session gaps on intraday bars."""
        frame, _ = self._split(1469)
        with configured():
            got = quiet(frames.sanitise, frame, "X", "15-minute")
        self.assertEqual(len(got), len(frame))

    def test_history_start_reports_the_reason(self):
        frame, restart = self._split(400)
        got = frames.history_start("X", frame["ts"], demergers={}, history_starts={})
        self.assertEqual(got[0], restart)
        self.assertIn("listing break", got[1])

    def test_a_continuous_series_has_no_start(self):
        self.assertIsNone(frames.history_start("X", daily(100, "2020-01-01")["ts"],
                                               demergers={}, history_starts={}))


class Demergers(unittest.TestCase):
    """A config.DEMERGERS ex-date is a break: the price before it belongs to a
    different company. SIEMENS 2025-04-07, -34.7% at the open."""

    def test_history_restarts_on_the_ex_date(self):
        frame = daily(60, "2025-01-01")
        with configured(demergers={"SIEMENS": ["2025-03-03"]}):
            got = quiet(frames.sanitise, frame, "SIEMENS", "daily")
        self.assertEqual(got["ts"].iloc[0], pd.Timestamp("2025-03-03"))

    def test_an_ex_date_on_a_holiday_restarts_on_the_next_session(self):
        frame = daily(60, "2025-01-01")
        with configured(demergers={"X": ["2025-03-01"]}):        # a Saturday
            got = quiet(frames.sanitise, frame, "X", "daily")
        self.assertEqual(got["ts"].iloc[0], pd.Timestamp("2025-03-03"))

    def test_only_the_named_symbol_is_cut(self):
        frame = daily(60, "2025-01-01")
        with configured(demergers={"SIEMENS": ["2025-03-03"]}):
            got = quiet(frames.sanitise, frame, "ABB", "daily")
        self.assertEqual(len(got), 60)

    def test_the_later_of_a_gap_and_an_ex_date_wins(self):
        a = daily(20, "2010-01-01")
        b = daily(60, "2015-01-01")
        frame = pd.concat([a, b], ignore_index=True)
        with configured(demergers={"X": ["2015-02-02"]}):
            got = quiet(frames.sanitise, frame, "X", "daily")
        self.assertEqual(got["ts"].iloc[0], pd.Timestamp("2015-02-02"))

    def test_an_ex_date_before_the_file_starts_changes_nothing(self):
        frame = daily(60, "2025-01-01")
        with configured(demergers={"X": ["2019-10-11"]}):
            got = quiet(frames.sanitise, frame, "X", "daily")
        self.assertEqual(len(got), 60)

    def test_every_configured_date_parses_and_is_in_the_past(self):
        for symbol, dates in config.DEMERGERS.items():
            for d in dates:
                self.assertLess(pd.Timestamp(d), pd.Timestamp.now(), f"{symbol} {d}")
        for symbol, d in config.HISTORY_STARTS.items():
            self.assertLess(pd.Timestamp(d), pd.Timestamp.now(), f"{symbol} {d}")


class HistoryStarts(unittest.TestCase):
    """HINDPETRO before 2015: continuous, correlated with BPCL at 0.78, and
    wrong by a factor of several. No gap to hang a cut on, so it is named."""

    def test_bars_before_the_configured_date_are_dropped(self):
        frame = daily(300, "2014-06-02")
        with configured(history_starts={"HINDPETRO": "2015-01-01"}):
            got = quiet(frames.sanitise, frame, "HINDPETRO", "daily")
        self.assertGreaterEqual(got["ts"].iloc[0], pd.Timestamp("2015-01-01"))
        self.assertLess(len(got), 300)

    def test_other_symbols_keep_everything(self):
        frame = daily(300, "2014-06-02")
        with configured(history_starts={"HINDPETRO": "2015-01-01"}):
            got = quiet(frames.sanitise, frame, "BPCL", "daily")
        self.assertEqual(len(got), 300)


class DedupeSessions(unittest.TestCase):
    """COALINDIA carried 2015-12-31 at 00:00 and at 09:15 with identical OHLCV
    because fetch deduplicated the raw stamp. One bar per session, last wins."""

    def _doubled(self):
        frame = daily(5, "2015-12-28")
        twin = frame.iloc[[3]].copy()
        twin["ts"] = twin["ts"] + pd.Timedelta(hours=9, minutes=15)
        twin["close"] = 307.05
        return pd.concat([frame, twin]).sort_values("ts").reset_index(drop=True)

    def test_a_session_stored_twice_keeps_one_bar(self):
        with configured():
            got = quiet(frames.sanitise, self._doubled(), "COALINDIA", "daily")
        self.assertEqual(len(got), 5)
        self.assertFalse(got["ts"].duplicated().any())

    def test_the_later_stamp_wins_and_the_stamp_is_normalised(self):
        with configured():
            got = quiet(frames.sanitise, self._doubled(), "COALINDIA", "daily")
        row = got[got["ts"] == pd.Timestamp("2015-12-31")]
        self.assertEqual(len(row), 1)
        self.assertEqual(float(row["close"].iloc[0]), 307.05)
        self.assertTrue((got["ts"] == got["ts"].dt.normalize()).all())

    def test_intraday_bars_share_a_date_and_are_never_deduped(self):
        stamps = pd.date_range("2020-01-01 09:15", periods=25, freq="15min")
        frame = pd.DataFrame({"ts": stamps, "open": 100.0, "high": 101.0,
                              "low": 99.0, "close": 100.0, "volume": 10})
        got = frames.dedupe_sessions(frame, "X", "15-minute")
        self.assertEqual(len(got), 25)


class ExDateSessions(unittest.TestCase):
    """DIVISLAB 2015-09-22: 15-minute bars at 2241/2215 against a daily close of
    1104.55 -- Kite adjusted the daily file for the 2:1 split, not the intraday
    one. The daily bar is the authority for price AND volume."""

    def _session(self, day, close, volume=100):
        stamps = pd.date_range(f"{day} 09:15", periods=25, freq="15min")
        return pd.DataFrame({"ts": stamps, "open": close, "high": close * 1.01,
                             "low": close * 0.99, "close": close, "volume": volume})

    def _pair(self, factor):
        intraday = pd.concat([self._session("2015-09-21", 1100.0),
                              self._session("2015-09-22", 1100.0 * factor, 50),
                              self._session("2015-09-23", 1100.0)], ignore_index=True)
        day = pd.DataFrame({"ts": pd.to_datetime(["2015-09-21", "2015-09-22", "2015-09-23"]),
                            "close": [1100.0, 1100.0, 1100.0],
                            "volume": [2500, 2500, 2500]})
        return intraday, day

    def test_a_2x_session_is_scaled_onto_the_daily_bar(self):
        intraday, day = self._pair(2.0)
        got = quiet(frames.rescale_ex_date_sessions, intraday, day, "DIVISLAB")
        mid = got[got["ts"].dt.normalize() == "2015-09-22"]
        self.assertAlmostEqual(float(mid["close"].iloc[-1]), 1100.0)
        self.assertEqual(int(mid["volume"].sum()), 2500)

    def test_a_5x_session_is_scaled_too(self):
        intraday, day = self._pair(5.0)
        got = quiet(frames.rescale_ex_date_sessions, intraday, day, "ALANKIT")
        mid = got[got["ts"].dt.normalize() == "2015-09-22"]
        self.assertAlmostEqual(float(mid["high"].max()), 1100.0 * 1.01, places=6)

    def test_the_closing_auction_mismatch_is_left_alone(self):
        """MUTHOOTFIN 2018-12-06 at 5.8%, IOC 2018-10-04 at 9.2%: real prices."""
        intraday, day = self._pair(1.10)
        got = quiet(frames.rescale_ex_date_sessions, intraday, day, "X")
        pd.testing.assert_frame_equal(got, intraday)

    def test_neighbouring_sessions_are_untouched(self):
        intraday, day = self._pair(2.0)
        got = quiet(frames.rescale_ex_date_sessions, intraday, day, "X")
        for d in ("2015-09-21", "2015-09-23"):
            self.assertAlmostEqual(
                float(got[got["ts"].dt.normalize() == d]["close"].iloc[-1]), 1100.0)


class MarketWideDays(unittest.TestCase):
    def _closes(self, crash_share):
        idx = pd.bdate_range("2020-01-01", periods=10)
        out = {}
        for i in range(80):
            c = pd.Series(100.0, index=idx)
            if i < 80 * crash_share:
                c.iloc[5:] = 85.0        # -15% on the 6th session
            out[f"S{i}"] = c
        return out, idx[5]

    def test_a_day_where_a_third_of_stocks_fall_is_market_wide(self):
        closes, day = self._closes(0.35)
        self.assertIn(day, frames.market_wide_days(closes))

    def test_a_day_where_a_few_stocks_fall_is_not(self):
        closes, day = self._closes(0.05)
        self.assertNotIn(day, frames.market_wide_days(closes))

    def test_too_few_symbols_never_qualify(self):
        closes, day = self._closes(1.0)
        few = {k: closes[k] for k in list(closes)[:10]}
        self.assertNotIn(day, frames.market_wide_days(few))


class FakeKite:
    """Serves a canned daily series and records every range it was asked for."""

    def __init__(self, closes: dict[date, float]):
        self.closes = closes
        self.calls: list[tuple[date, date]] = []

    def historical_data(self, token, from_date, to_date, interval, continuous=False):
        self.calls.append((from_date, to_date))
        return [{"date": pd.Timestamp(d).tz_localize("Asia/Kolkata"),
                 "open": c, "high": c, "low": c, "close": c, "volume": 1000}
                for d, c in sorted(self.closes.items()) if from_date <= d <= to_date]


class FetchResume(unittest.TestCase):
    """fetch_interval after the 2026-09-07 audit: no partial sessions, five
    sessions of overlap on resume, and a re-based history is fetched whole."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.saved = fetch.DATA
        fetch.DATA = self.tmp
        self.days = [d.date() for d in pd.bdate_range("2024-01-01", periods=30)]
        self.series = {d: 100.0 + i for i, d in enumerate(self.days)}
        self.after_close = pd.Timestamp(f"{self.days[-1]} 16:00", tz="Asia/Kolkata")

    def tearDown(self):
        fetch.DATA = self.saved

    def _store(self, n):
        stored = pd.DataFrame({"ts": pd.to_datetime(self.days[:n]),
                               "open": 1.0, "high": 1.0, "low": 1.0,
                               "close": [self.series[d] for d in self.days[:n]],
                               "volume": 1000})
        stored.to_parquet(fetch.path_for("X", "day"), index=False)
        return stored

    def _run(self, kite, now):
        return quiet(fetch.fetch_interval, kite, "X", 1, "day", str(self.days[0]),
                     fetch.Throttle(gap=0), now=now)

    def test_cutoff_is_yesterday_before_the_close_and_today_after(self):
        morning = pd.Timestamp("2026-09-02 10:19", tz="Asia/Kolkata")
        self.assertEqual(fetch.cutoff_date(morning), date(2026, 9, 1))
        evening = pd.Timestamp("2026-09-02 15:40", tz="Asia/Kolkata")
        self.assertEqual(fetch.cutoff_date(evening), date(2026, 9, 2))

    def test_resume_refetches_the_last_five_sessions(self):
        self._store(20)
        kite = FakeKite(self.series)
        got = self._run(kite, self.after_close)
        self.assertEqual(len(got), 30)
        self.assertEqual(kite.calls[0][0], self.days[15])      # 5th-last stored session

    def test_a_rebased_history_is_discarded_and_fetched_whole(self):
        self._store(20)
        halved = {d: c / 2 for d, c in self.series.items()}    # a 2:1 split went ex
        kite = FakeKite(halved)
        got = self._run(kite, self.after_close)
        self.assertEqual(len(got), 30)
        self.assertEqual(float(got["close"].iloc[0]), halved[self.days[0]])
        self.assertEqual(kite.calls[-1][0], self.days[0])      # the whole range again

    def test_a_tick_of_revision_is_not_a_rebase(self):
        self._store(20)
        nudged = dict(self.series)
        nudged[self.days[19]] += 0.05
        kite = FakeKite(nudged)
        got = self._run(kite, self.after_close)
        self.assertEqual(len(kite.calls), 1)
        self.assertEqual(len(got), 30)

    def test_a_partial_session_is_never_stored(self):
        self._store(20)
        kite = FakeKite(self.series)
        morning = pd.Timestamp(f"{self.days[25]} 10:19", tz="Asia/Kolkata")
        got = self._run(kite, morning)
        self.assertEqual(got["ts"].max().date(), self.days[24])
        self.assertLessEqual(kite.calls[-1][1], self.days[25] - timedelta(days=1))

    def test_a_partial_bar_stored_earlier_is_dropped_and_refetched(self):
        stored = self._store(26)                              # includes "today"
        kite = FakeKite(self.series)
        morning = pd.Timestamp(f"{self.days[25]} 10:19", tz="Asia/Kolkata")
        got = self._run(kite, morning)
        self.assertEqual(got["ts"].max().date(), self.days[24])
        self.assertLess(len(got), len(stored))


class LevelsFile(unittest.TestCase):
    """A wiped CLEAN used to give level strategies zero trades in silence."""

    def setUp(self):
        self.saved = levels.LEVELS_PATH
        levels.LEVELS_PATH = pathlib.Path(tempfile.mkdtemp()) / "levels.json"

    def tearDown(self):
        levels.LEVELS_PATH = self.saved

    def test_a_missing_file_raises_instead_of_returning_empty(self):
        with self.assertRaises(FileNotFoundError):
            levels.load_all()

    def test_the_first_level_can_still_be_drawn(self):
        saved_clean = levels.CLEAN
        levels.CLEAN = levels.LEVELS_PATH.parent
        try:
            levels.add("HAL", 100.0, "support")
        finally:
            levels.CLEAN = saved_clean
        self.assertEqual(len(levels.load_all()["HAL"]), 1)


class SanitiseStillDoesTheOldJobs(unittest.TestCase):
    def test_non_positive_prices_are_repaired_before_the_new_rules_run(self):
        frame = bars([(100, 105, 95, 100), (0, 0, 0, 0), (100, 105, 95, 100)] * 5)
        with configured():
            got = quiet(frames.sanitise, frame, "X", "daily")
        self.assertTrue(bool((got[["open", "high", "low", "close"]] > 0).all().all()))


if __name__ == "__main__":
    unittest.main()

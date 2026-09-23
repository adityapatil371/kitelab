"""Backfill candles from Kite into local Parquet files.

15-minute candles are the base timeframe. Optionally daily candles are fetched too,
because Kite's intraday history is much shallower than its daily history.

Storage is one Parquet file per symbol per fetched interval, in data/. For four
symbols this is a few megabytes and pandas reads it instantly -- no database needed.
"""
from __future__ import annotations

import time
from datetime import date, datetime, time as clock, timedelta

import pandas as pd

from . import config
from .config import DATA, Config

# Max days Kite will serve in a single historical request, per interval.
# Source: Kite Connect developer forum, not the official docs page -- treat as
# approximate. If a request 400s with a date-range error, lower the value.
CHUNK_DAYS = {
    "minute": 60,
    "3minute": 100,
    "5minute": 100,
    "10minute": 100,
    "15minute": 200,
    "30minute": 200,
    "60minute": 400,
    "day": 2000,
}

# Historical endpoint is documented at 3 req/s. Stay just under it.
MIN_REQUEST_GAP = 0.35

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]

# An empty frame with the RIGHT dtypes. `pd.DataFrame(columns=COLUMNS)` gives
# every column object dtype, and object `ts` is contagious: concat it with a
# real chunk and pandas 3 keeps the column as object, so the merge in `rebased`
# dies with "trying to merge on datetime64[us] and object columns" and the
# symbol is skipped with its history left stale. Worse, the same blank feeds
# `combined`, so an object `ts` reaches to_parquet and the fault is now ON DISK
# and reappears on every later run. Seen on a real backfill 2026-09-23 across
# dozens of symbols (HAL, IRFC among them).
_DTYPES = {"ts": "datetime64[ns]", "open": "float64", "high": "float64",
           "low": "float64", "close": "float64", "volume": "int64"}


def blank() -> pd.DataFrame:
    """An empty candle frame that can be concatenated or merged safely."""
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d in _DTYPES.items()})


def with_ts(frame: pd.DataFrame) -> pd.DataFrame:
    """The same frame with `ts` guaranteed to be datetimes.

    Belt and braces over `blank()`: a parquet written by an older run already
    holds an object `ts`, and nothing upstream can fix a file that exists.
    """
    if "ts" in frame.columns and not frame.empty:
        frame = frame.copy()
        frame["ts"] = pd.to_datetime(frame["ts"])
    return frame


def path_for(symbol: str, interval: str):
    return DATA / f"{symbol}_{interval}.parquet"


class Throttle:
    """Crude but sufficient: never issue two requests closer than MIN_REQUEST_GAP."""

    def __init__(self, gap: float = MIN_REQUEST_GAP):
        self.gap = gap
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.gap:
            time.sleep(self.gap - elapsed)
        self._last = time.monotonic()


def instrument_tokens(kite, symbols: list[str], exchange: str) -> dict[str, int]:
    """Resolve trading symbols to instrument tokens, caching the daily dump."""
    cache = DATA / f"instruments_{exchange}_{date.today().isoformat()}.parquet"
    if cache.exists():
        dump = pd.read_parquet(cache)
    else:
        dump = pd.DataFrame(kite.instruments(exchange))
        dump.to_parquet(cache, index=False)
        print(f"  cached {len(dump):,} {exchange} instruments -> {cache.name}")

    equities = dump[dump["instrument_type"] == "EQ"]
    tokens: dict[str, int] = {}
    for symbol in symbols:
        match = equities[equities["tradingsymbol"] == symbol]
        if match.empty:
            print(f"  !! {symbol}: not found in the {exchange} EQ instrument list -- skipped")
            continue
        tokens[symbol] = int(match.iloc[0]["instrument_token"])
    return tokens


def _to_frame(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return blank()
    frame = pd.DataFrame(candles)
    frame = frame.rename(columns={"date": "ts"})
    # Kite returns tz-aware IST. Drop the offset so stored timestamps read exactly
    # like the clock times on a TradingView chart set to IST.
    frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_localize(None)
    frame["volume"] = frame["volume"].astype("int64")
    return frame[COLUMNS]


# NSE's closing auction ends at 15:35 (post 2026-08-03). A bar fetched before
# that on a trading day is a PARTIAL session and must never be stored as if it
# were the day. Measured 2026-09-07: the 101 in-sample files end 2026-08-25
# with a last-bar volume of 0.19x their 60-day median (fetched 10:19-10:34
# IST); the 898 others end 2026-09-02 at 0.58x (fetched 12:43-13:49 IST).
# Every one of those last bars is a fraction of a day wearing a day's stamp.
SESSION_CLOSE = clock(15, 35)

# On resume, re-fetch this many of the most recent stored sessions and compare
# them with what Kite serves now. Kite adjusts its whole history at serve time
# when a split or bonus goes ex (HAL 2:1 on 2023-07-27/28, BPCL's 2024-06-21
# bonus, NESTLEIND 1:10 on 2024-01-05), so a file that was fetched before the
# ex-date and extended after it is two price scales glued together. One
# overlapping session cannot tell that apart from an ordinary revision; five
# sessions all shifted by the same ratio can.
RESUME_SESSIONS = 5
REBASE_TOLERANCE = 0.005     # 0.5%: a real revision is a tick or two, a rebase is a ratio


def cutoff_date(now=None) -> date:
    """The last date whose session can be stored whole right now.

    Before SESSION_CLOSE (IST) today's bar is still forming, so the cutoff is
    yesterday. On a weekend that means a Saturday-morning run caps at Friday,
    which costs nothing: there is no Saturday session to lose.
    """
    if now is None:
        now = config.now_local()
    if now.time() < SESSION_CLOSE:
        return (now - timedelta(days=1)).date()
    return now.date()


def _pull(kite, token: int, interval: str, ranges: list[tuple],
          throttle: Throttle, continuous: bool) -> tuple[pd.DataFrame, int]:
    """Fetch every (start, end) range in CHUNK_DAYS-sized requests."""
    span = CHUNK_DAYS.get(interval, 100)
    chunks: list[pd.DataFrame] = []
    requests = 0
    for range_start, range_end in ranges:
        cursor = range_start
        while cursor <= range_end:
            chunk_end = min(cursor + timedelta(days=span - 1), range_end)
            throttle.wait()
            candles = kite.historical_data(token, cursor, chunk_end, interval,
                                           continuous=continuous)
            requests += 1
            chunks.append(_to_frame(candles))
            cursor = chunk_end + timedelta(days=1)
    if not chunks:
        return blank(), requests
    # with_ts, not just blank(): a run that fetched some empty ranges and some
    # full ones concatenates typed blanks with real data, and one stray object
    # column anywhere in the list would carry through.
    return with_ts(pd.concat(chunks, ignore_index=True)), requests


def rebased(stored: pd.DataFrame, served: pd.DataFrame) -> str | None:
    """Why the stored history no longer matches what Kite serves, or None.

    Compares closes on the stamps both frames hold. Any close off by more than
    REBASE_TOLERANCE means the history has been re-based by a corporate action
    since the file was written, and the whole file is stale, not just its tail.
    """
    if stored.empty or served.empty:
        return None
    joined = with_ts(stored)[["ts", "close"]].merge(
        with_ts(served)[["ts", "close"]], on="ts",
        suffixes=("_stored", "_served"))
    if joined.empty:
        return None
    ratio = joined["close_served"] / joined["close_stored"]
    off = (ratio - 1).abs() > REBASE_TOLERANCE
    if not off.any():
        return None
    worst = joined.loc[(ratio - 1).abs().idxmax()]
    return (f"{int(off.sum())} of {len(joined)} re-served bars differ from the "
            f"stored close by over {REBASE_TOLERANCE:.1%} (e.g. {worst['ts']}: "
            f"stored {worst['close_stored']:.2f}, served {worst['close_served']:.2f})"
            f" -- the history was re-based by a corporate action")


def fetch_interval(kite, symbol: str, token: int, interval: str,
                   start: str, throttle: Throttle,
                   continuous: bool = False, now=None) -> pd.DataFrame:
    """Fetch one interval for one symbol, resuming from whatever is already on disk.

    Three rules, all from the 2026-09-07 audit:

      - never store a partial session: to_date is cutoff_date(now), and any
        stored bar after it (a partial captured by an earlier run) is dropped
        so the next run fetches that session whole;
      - on resume, re-fetch the last RESUME_SESSIONS stored sessions rather
        than one, and if any re-served close differs by over REBASE_TOLERANCE
        the file is discarded and fetched whole (see rebased());
      - fill in BOTH directions, as before: lowering the configured start
        deepens history instead of silently doing nothing.

    `now` is injectable for tests; it defaults to the market clock (IST).
    """
    target = path_for(symbol, interval)
    existing = with_ts(pd.read_parquet(target)) if target.exists() else blank()

    wanted_start = datetime.fromisoformat(start).date()
    to_date = cutoff_date(now)
    label = f"  {symbol:<10} {interval:<9}"

    if not existing.empty:
        existing["ts"] = pd.to_datetime(existing["ts"])
        partial = existing["ts"].dt.normalize().dt.date > to_date
        if partial.any():
            print(f"{label} dropping {int(partial.sum())} stored bar(s) after "
                  f"{to_date} -- fetched before the {SESSION_CLOSE:%H:%M} close, so "
                  f"they were a partial session; refetched whole next run")
            existing = existing.loc[~partial].reset_index(drop=True)

    ranges: list[tuple] = []
    if existing.empty:
        ranges.append((wanted_start, to_date))
    else:
        sessions = sorted(existing["ts"].dt.normalize().dt.date.unique())
        have_start = sessions[0]
        check_from = sessions[-RESUME_SESSIONS] if len(sessions) >= RESUME_SESSIONS else have_start
        if wanted_start < have_start:
            ranges.append((wanted_start, have_start))
        if check_from <= to_date:
            ranges.append((check_from, to_date))

    fetched, requests = _pull(kite, token, interval, ranges, throttle, continuous)

    why = rebased(existing, fetched)
    if why:
        print(f"{label} {why}; discarding the stored file and fetching it whole")
        existing = blank()
        fetched, more = _pull(kite, token, interval, [(wanted_start, to_date)],
                              throttle, continuous)
        requests += more

    combined = with_ts(pd.concat([existing, fetched], ignore_index=True))
    combined = (
        combined.dropna(subset=["ts"])
        .drop_duplicates(subset=["ts"], keep="last")
        .sort_values("ts")
        .reset_index(drop=True)
    )
    combined.to_parquet(target, index=False)

    label = f"{label} {requests:>3} req"
    if combined.empty:
        print(f"{label}  no data returned")
    else:
        added = len(combined) - len(existing)
        print(
            f"{label}  {len(combined):>7,} bars (+{added:,})  "
            f"{combined['ts'].min()} .. {combined['ts'].max()}"
        )
    return combined


def backfill(kite, cfg: Config, intervals: list[str] | None = None) -> None:
    """Fetch every configured interval for every symbol in cfg.symbols.

    `intervals` overrides the default pair. Passing ["day"] is the cheap screening
    pass: daily costs ~4 requests per symbol against ~22 for 15-minute, so a wide
    candidate sweep is a fifth of the price. Fetch the intraday data afterwards, for
    the symbols that survive screening.
    """
    if intervals is None:
        intervals = ["15minute"] + (["day"] if cfg.use_daily_source else [])
    print(f"\nResolving instruments on {cfg.exchange} ...")
    tokens = instrument_tokens(kite, cfg.symbols, cfg.exchange)
    if not tokens:
        raise SystemExit("No symbols resolved -- check the names in config.local.toml.")

    throttle = Throttle()
    starts = {"day": cfg.start, "15minute": cfg.intraday_start}
    print(f"\nBackfilling daily from {cfg.start}, intraday from {cfg.intraday_start}:\n")
    for symbol, token in tokens.items():
        for interval in intervals:
            try:
                fetch_interval(kite, symbol, token, interval,
                               starts.get(interval, cfg.start), throttle)
            except Exception as exc:  # keep going; one bad symbol shouldn't kill the run
                print(f"  !! {symbol} {interval}: {type(exc).__name__}: {exc}")
    print(f"\nDone. Parquet files are in {DATA}\n")

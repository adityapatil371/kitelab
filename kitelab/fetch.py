"""Backfill candles from Kite into local Parquet files.

15-minute candles are the base timeframe. Optionally daily candles are fetched too,
because Kite's intraday history is much shallower than its daily history.

Storage is one Parquet file per symbol per fetched interval, in data/. For four
symbols this is a few megabytes and pandas reads it instantly -- no database needed.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pandas as pd

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
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(candles)
    frame = frame.rename(columns={"date": "ts"})
    # Kite returns tz-aware IST. Drop the offset so stored timestamps read exactly
    # like the clock times on a TradingView chart set to IST.
    frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_localize(None)
    frame["volume"] = frame["volume"].astype("int64")
    return frame[COLUMNS]


def fetch_interval(kite, symbol: str, token: int, interval: str,
                   start: str, throttle: Throttle,
                   continuous: bool = False) -> pd.DataFrame:
    """Fetch one interval for one symbol, resuming from whatever is already on disk."""
    target = path_for(symbol, interval)
    existing = pd.read_parquet(target) if target.exists() else pd.DataFrame(columns=COLUMNS)

    wanted_start = datetime.fromisoformat(start).date()
    to_date = date.today()

    # Fill in BOTH directions. Only extending forward means lowering the configured
    # start date silently does nothing, and the only way to deepen history is to delete
    # the file -- which is exactly the trap this avoids.
    ranges: list[tuple] = []
    if existing.empty:
        ranges.append((wanted_start, to_date))
    else:
        have_start, have_end = existing["ts"].min().date(), existing["ts"].max().date()
        if wanted_start < have_start:
            ranges.append((wanted_start, have_start))
        # Re-fetch the last stored day so a partially-captured session gets completed.
        if have_end <= to_date:
            ranges.append((have_end, to_date))

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

    combined = pd.concat([existing, *chunks], ignore_index=True)
    combined = (
        combined.dropna(subset=["ts"])
        .drop_duplicates(subset=["ts"], keep="last")
        .sort_values("ts")
        .reset_index(drop=True)
    )
    combined.to_parquet(target, index=False)

    label = f"  {symbol:<10} {interval:<9} {requests:>3} req"
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

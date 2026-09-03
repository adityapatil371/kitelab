"""Load stored candles and resample them to any timeframe.

The 15-minute file is the base. 30m and 1h are built from it; 1d comes either from
Kite's own daily candles (deeper history) or from the 15-min bars; 1w and 1M are
always built from whichever daily frame you ended up with.

NSE session arithmetic, which is why the resampling needs an explicit origin:

    09:15 -> 15:30  =  375 minutes
      15m  ->  25 bars, exact
      30m  ->  12 full bars + a 15-minute stub at 15:15
      1h   ->   6 full bars + a 15-minute stub at 15:15

Pandas' default resampling anchors to midnight, which would produce a 09:00-09:30
bucket and silently misalign every intraday bar against what Kite and TradingView
show. Every intraday resample below is anchored to that day's 09:15 instead.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from .config import CLEAN

SESSION_OPEN = pd.Timedelta(hours=9, minutes=15)

AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}

# `ts` is the bar's FIRST session and `close` is its LAST session's close, so the
# stamp is up to four sessions older than the price. `end_ts` carries the session
# the bar actually closed on -- the DECISION date. Anything that asks "what was
# known when this bar closed" must key off end_ts, not ts.
NAMED_AGG = dict(
    ts=("ts", "first"),
    end_ts=("ts", "last"),
    open=("open", "first"),
    high=("high", "max"),
    low=("low", "min"),
    close=("close", "last"),
    volume=("volume", "sum"),
)

# Like NAMED_AGG but without the ts column, for groupbys whose KEY is the stamp.
NAMED_AGG_NOTS = dict(
    open=("open", "first"),
    high=("high", "max"),
    low=("low", "min"),
    close=("close", "last"),
    volume=("volume", "sum"),
)

TIMEFRAMES = ["15m", "30m", "1h", "1d", "1w", "1M"]

# NSE introduced a Closing Auction Session on 2026-08-03. For stocks covered by it,
# continuous trading ends at 15:15 and an auction from 15:15 to 15:35 sets the official
# close. So the session shrank from 375 to 360 minutes and the bar count per session
# changed. 360 divides evenly by 15/30/60, so post-CAS sessions have no stub bar --
# pre-CAS ones do, because 375 does not divide by 30 or 60.
CAS_START = pd.Timestamp("2026-08-03")
BARS_PRE_CAS = 25   # 09:15 .. 15:15, last bar covers 15:15-15:30
BARS_POST_CAS = 24  # 09:15 .. 15:00, last bar covers 15:00-15:15


def expected_bars(session_day) -> int:
    """How many 15-minute bars a full session should hold on a given date."""
    return BARS_POST_CAS if pd.Timestamp(session_day) >= CAS_START else BARS_PRE_CAS


def _path(symbol: str, interval: str):
    return CLEAN / f"{symbol}_{interval}.parquet"


_TRIM_WARNED: set[str] = set()

_CLEAN_WARNED: set[str] = set()


# A zero-volume bar this far from the traded price around it is not a price.
# 0.2 = five times out of line in either direction; the closest thing to a false
# positive in the whole universe is TVSSRICHAK's padding at 0.371 of the first
# real trade, which this deliberately leaves alone (see drop_untraded_outliers).
UNTRADED_OUTLIER_FACTOR = 0.2
UNTRADED_REF_WINDOW = 41      # centred, so ~20 sessions of context each side
UNTRADED_REF_MIN = 5


def drop_untraded_outliers(frame: pd.DataFrame, symbol: str,
                           interval: str) -> pd.DataFrame:
    """Drop zero-volume bars whose price is wildly out of line with the tape.

    VINEETLAB carried six bars reading 7.60/7.60/7.60/7.60 with volume 0 amid
    prices around Rs1,600. Nothing traded at 7.60 -- there was no volume -- but a
    close-based stop reads it as a 99.5% collapse and sells into it. One of them
    cost the Q/M/W reference account a manufactured -Rs43,200.

    The reference is a CENTRED ROLLING MEDIAN OF THE TRADED BARS ONLY. That is
    what makes this work on RUNS: five consecutive corrupt bars cannot drag their
    own reference down, because they are excluded from it by construction. An
    earlier detector compared each bar with its immediate neighbours and so found
    only the one isolated bar, missing the run of five.

    At the ends of a series there is no centred window, so the reference falls
    back to the nearest traded close. That is what catches JMFINANCIL: 191
    zero-volume bars at Rs0.14 sitting in front of the stock's first real trade at
    Rs30.99, which seeded its 20-day EMA at 0.14 and manufactured a buy signal on
    the first day it ever traded.

    Dropped, not repaired: no volume means no trade, so there is no price to
    repair towards. The bar did not happen.
    """
    if "volume" not in frame.columns or frame.empty:
        return frame
    traded = frame["volume"] > 0
    if not traded.any():
        return frame
    reference = (frame["close"].where(traded)
                 .rolling(UNTRADED_REF_WINDOW, center=True,
                          min_periods=UNTRADED_REF_MIN)
                 .median().ffill().bfill())
    if reference.isna().all():
        return frame
    out_of_line = ((frame["close"] < UNTRADED_OUTLIER_FACTOR * reference)
                   | (frame["close"] > reference / UNTRADED_OUTLIER_FACTOR))
    doomed = (~traded) & reference.notna() & out_of_line
    if not doomed.any():
        return frame
    tag = f"{symbol}:{interval}:untraded"
    if tag not in _CLEAN_WARNED:
        first, last = frame.loc[doomed, "ts"].iloc[0], frame.loc[doomed, "ts"].iloc[-1]
        print(f"[kitelab] {symbol}: dropped {int(doomed.sum()):,} {interval} bars with "
              f"ZERO VOLUME and a price far off the tape "
              f"({pd.Timestamp(first).date()} .. {pd.Timestamp(last).date()})")
        _CLEAN_WARNED.add(tag)
    return frame.loc[~doomed].reset_index(drop=True)


def enforce_containment(frame: pd.DataFrame, symbol: str,
                        interval: str) -> pd.DataFrame:
    """A bar's high and low must contain its own open and close.

    Kite delivers bars that violate this -- SURANAT&P 2013-04-08 closes at 3.20 with
    a low of 3.56; VHL 2013-04-08 closes at 524.50 with a high of 472.20. 94 bars
    across 32 files, clustered on 2013-04-08/09, which looks like one bad day at the
    vendor.

    It matters because intrabar rules read the high and the low: the ATH breakout
    checks `low <= stop` on every bar, Darvas has an intrabar option, and the Donchian
    channels are built from highs and lows. A low above the close can hide a stop that
    was really hit, or invent one that was not.

    This repair already existed -- as the last two lines of the non-positive branch of
    sanitise() -- but it was unreachable for any file WITHOUT a non-positive price, so
    the 94 bars sailed through. It is its own step now, and always runs.

    The close is authoritative: it is the price every close-based rule trades on, and
    the one Kite is least likely to have wrong. So the high and low are widened to
    admit the open and close, never the other way round.
    """
    cols = ["high", "open", "close"]
    if frame.empty or not set(cols).issubset(frame.columns):
        return frame
    high = frame[["high", "open", "close"]].max(axis=1)
    low = frame[["low", "open", "close"]].min(axis=1)
    changed = int(((high != frame["high"]) | (low != frame["low"])).sum())
    if not changed:
        return frame
    frame = frame.copy()
    frame["high"], frame["low"] = high, low
    tag = f"{symbol}:{interval}:containment"
    if tag not in _CLEAN_WARNED:
        print(f"[kitelab] {symbol}: widened {changed:,} {interval} bars whose high/low "
              "did not contain their own open/close")
        _CLEAN_WARNED.add(tag)
    return frame


def sanitise(frame: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Repair non-positive OHLC values in the stored candles, and drop bars that
    record a price nothing traded at.

    Kite's 2015-2018 intraday history contains bars for thinly traded stocks
    where `open` and `low` are recorded as 0.00 (high/close are fine). Any
    intrabar rule then reads low=0 as "the stop was gapped through" and fills
    at open=0 -- manufacturing catastrophic losses that never happened. The
    zeros are missing data, not prices, so they are rebuilt from the same
    bar's surviving fields; a bar with no usable close is dropped outright.

    A second family survives that repair because its prices are positive: bars
    with ZERO VOLUME carrying a price nowhere near the tape. See
    drop_untraded_outliers.

    A third: bars whose HIGH/LOW do not contain their own open and close. See
    enforce_containment.
    """
    cols = ["open", "high", "low", "close"]
    broken = (frame[cols] <= 0).any(axis=1)
    if not broken.any():
        return drop_untraded_outliers(
            enforce_containment(frame, symbol, interval), symbol, interval)
    count = int(broken.sum())
    dead = frame["close"] <= 0
    frame = frame.loc[~dead].copy()
    for name, fallback in (("open", "close"), ("high", None), ("low", None)):
        column = frame[name]
        if name == "open":
            frame[name] = column.where(column > 0, frame[fallback])
        elif name == "high":
            frame[name] = column.where(column > 0, frame[["open", "close"]].max(axis=1))
        else:
            frame[name] = column.where(column > 0, frame[["open", "close"]].min(axis=1))
    tag = f"{symbol}:{interval}"
    if tag not in _CLEAN_WARNED:
        print(f"[kitelab] {symbol}: repaired {count:,} {interval} bars with "
              f"non-positive prices (Kite data gaps), dropped {int(dead.sum())}")
        _CLEAN_WARNED.add(tag)
    return drop_untraded_outliers(
        enforce_containment(frame.reset_index(drop=True), symbol, interval),
        symbol, interval)



# Reading and resampling a symbol's bars is pure -- same file in, same frame out --
# but nothing cached it, so a six-band sweep re-read and re-resampled every symbol six
# times, and the eleven Breakout variants eleven times. That redundancy was most of
# the two hours a full rebuild took.
#
# THE CONTRACT: the frames handed back are SHARED. Do not write into one; copy it
# first, as strategies.breakout_trades already does. Nothing else in the repo mutates
# a frame it did not build (checked by AST over every .py).
#
# Sizes are set so a whole universe fits: the 15-minute frames are the memory, at
# roughly 3.5 MB each.
_CACHE_SYMBOLS = 600


def clear_caches() -> None:
    """Drop every cached frame. Call after changing anything on disk.

    Unreferenced by design -- cache invalidation is part of a caching module's
    surface, and the alternative when frames go stale mid-session is restarting
    the process. Kept deliberately after the 2026-09-03 audit listed it.
    """
    for fn in (base_15m, daily, load, _resample_cached):
        fn.cache_clear()
    _CLEAN_WARNED.clear()
    _TRIM_WARNED.clear()


@lru_cache(maxsize=_CACHE_SYMBOLS)
def base_15m(symbol: str, trim_orphans: bool = True) -> pd.DataFrame:
    """15-minute bars.

    Some symbols return intraday history from before the equity listed -- IRFC serves
    bars from 2018 despite listing in 2021, almost certainly from listed debt under the
    same trading symbol. Those bars are dropped by default, using the first native daily
    bar as the listing date.
    """
    path = _path(symbol, "15minute")
    if not path.exists():
        raise SystemExit(f"No 15-minute data for {symbol}. Run: python -m scripts.backfill")
    frame = pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    frame = sanitise(frame, symbol, "15-minute")

    native = _path(symbol, "day")
    if trim_orphans and native.exists():
        listed_on = pd.read_parquet(native, columns=["ts"])["ts"].min().normalize()
        orphans = frame["ts"] < listed_on
        if orphans.any():
            if symbol not in _TRIM_WARNED:
                print(
                    f"[kitelab] {symbol}: dropped {int(orphans.sum()):,} intraday bars "
                    f"before {listed_on.date()} (predate the equity listing)."
                )
                _TRIM_WARNED.add(symbol)
            frame = frame.loc[~orphans].reset_index(drop=True)
    return frame


def _resample_intraday(base: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Resample intraday bars, anchoring each session to its own 09:15 open.

    ONE groupby over the whole frame, not one pandas resample per trading day. The
    per-day loop built ~2,500 tiny DataFrames per symbol and spent its time in pandas
    bookkeeping rather than arithmetic -- profiled at 1.89s per symbol against 0.05s
    to read the file, with 3.1 million isinstance calls and ~14,400 Index
    constructions. Same output, and verify.py proves it: 137,298 of 137,298 derived
    bars reconstruct exactly from their source.

    The bucket key is computed directly: floor the minutes elapsed since that
    session's 09:15 into `minutes`-wide slots and add them back to the open. Floor
    division handles a pre-open bar the same way resample's `origin` does, by
    extending the grid backwards.
    """
    if base.empty:
        return pd.DataFrame(columns=["ts", *AGG])
    ts = base["ts"]
    day = ts.dt.normalize()
    step = pd.Timedelta(minutes=minutes)
    offset = ((ts - day - SESSION_OPEN) // step) * step
    bucket = day + SESSION_OPEN + offset
    out = (base.assign(_bucket=bucket)
               .groupby("_bucket", sort=True)
               .agg(**NAMED_AGG_NOTS))
    out.index.name = "ts"
    out = out.reset_index()
    out["volume"] = out["volume"].astype("int64")
    return out.sort_values("ts").reset_index(drop=True)


def resample_intraday(base: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Public entry point for callers that hold their own (e.g. truncated) 15m frame."""
    return _resample_intraday(base, minutes)


@lru_cache(maxsize=_CACHE_SYMBOLS * 2)
def _resample_cached(symbol: str, minutes: int) -> pd.DataFrame:
    """Resampled intraday bars for a symbol, computed once per session."""
    return _resample_intraday(base_15m(symbol), minutes)


def _daily_from_intraday(base: pd.DataFrame) -> pd.DataFrame:
    indexed = base.set_index("ts").sort_index()
    out = indexed.groupby(indexed.index.normalize()).agg(AGG)
    out.index.name = "ts"
    out = out.reset_index()
    out["volume"] = out["volume"].astype("int64")
    return out.sort_values("ts").reset_index(drop=True)


@lru_cache(maxsize=_CACHE_SYMBOLS * 2)
def daily(symbol: str, prefer_native: bool = True) -> pd.DataFrame:
    """Daily candles, from Kite's `day` interval if available, else from 15-min bars."""
    native = _path(symbol, "day")
    if prefer_native and native.exists():
        frame = pd.read_parquet(native)
        frame["ts"] = pd.to_datetime(frame["ts"]).dt.normalize()
        frame = frame.sort_values("ts").reset_index(drop=True)
        return sanitise(frame, symbol, "daily")
    return _daily_from_intraday(base_15m(symbol))


def _group_daily(day_frame: pd.DataFrame, key) -> pd.DataFrame:
    """Aggregate daily bars by an arbitrary key, dating each bar to its first session."""
    out = day_frame.groupby(key, sort=True).agg(**NAMED_AGG).reset_index(drop=True)
    out["volume"] = out["volume"].astype("int64")
    return out.sort_values("ts").reset_index(drop=True)


def weekly(day_frame: pd.DataFrame) -> pd.DataFrame:
    iso = day_frame["ts"].dt.isocalendar()
    key = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    return _group_daily(day_frame, key)


def monthly(day_frame: pd.DataFrame) -> pd.DataFrame:
    return _group_daily(day_frame, day_frame["ts"].dt.to_period("M"))


def quarterly(day_frame: pd.DataFrame) -> pd.DataFrame:
    return _group_daily(day_frame, day_frame["ts"].dt.to_period("Q"))


@lru_cache(maxsize=_CACHE_SYMBOLS * 3)
def load(symbol: str, timeframe: str = "1d", prefer_native_daily: bool = True) -> pd.DataFrame:
    """Return OHLCV for a symbol at one of TIMEFRAMES."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {TIMEFRAMES}, got {timeframe!r}")
    if timeframe == "15m":
        return base_15m(symbol)
    if timeframe == "30m":
        # Assets fetched with native 30-minute bars (Bitcoin: 24/7 market, Binance
        # serves 30m directly and NSE-session resampling makes no sense there) are
        # loaded as-is instead of being rebuilt from 15m.
        native = _path(symbol, "30minute")
        if native.exists():
            return pd.read_parquet(native).sort_values("ts").reset_index(drop=True)
        return _resample_cached(symbol, 30)
    if timeframe == "1h":
        # Assets fetched with native 30-minute bars and no 15m file (Bitcoin) are
        # paired into hours on a midnight anchor -- correct for a 24/7 UTC market,
        # where NSE session anchoring makes no sense.
        native_30 = _path(symbol, "30minute")
        if not _path(symbol, "15minute").exists() and native_30.exists():
            base = pd.read_parquet(native_30).sort_values("ts")
            out = base.set_index("ts").resample("60min").agg(AGG).dropna(subset=["open"])
            out.index.name = "ts"
            out = out.reset_index()
            out["volume"] = out["volume"].astype("int64")
            return out
        return _resample_cached(symbol, 60)

    day_frame = daily(symbol, prefer_native=prefer_native_daily)
    if timeframe == "1d":
        return day_frame
    if timeframe == "1w":
        return weekly(day_frame)
    return monthly(day_frame)

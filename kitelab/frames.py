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

import pandas as pd

from .config import DATA

SESSION_OPEN = pd.Timedelta(hours=9, minutes=15)

AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}

NAMED_AGG = dict(
    ts=("ts", "first"),
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
    return DATA / f"{symbol}_{interval}.parquet"


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
    """
    cols = ["open", "high", "low", "close"]
    broken = (frame[cols] <= 0).any(axis=1)
    if not broken.any():
        return drop_untraded_outliers(frame, symbol, interval)
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
    # a repaired bar must still contain its own open and close
    frame["high"] = frame[["high", "open", "close"]].max(axis=1)
    frame["low"] = frame[["low", "open", "close"]].min(axis=1)
    tag = f"{symbol}:{interval}"
    if tag not in _CLEAN_WARNED:
        print(f"[kitelab] {symbol}: repaired {count:,} {interval} bars with "
              f"non-positive prices (Kite data gaps), dropped {int(dead.sum())}")
        _CLEAN_WARNED.add(tag)
    return drop_untraded_outliers(frame.reset_index(drop=True), symbol, interval)



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
    """Resample intraday bars, anchoring each session to its own 09:15 open."""
    parts = []
    for day, group in base.groupby(base["ts"].dt.normalize(), sort=True):
        origin = day + SESSION_OPEN
        indexed = group.set_index("ts").sort_index()
        bars = indexed.resample(
            f"{minutes}min", origin=origin, label="left", closed="left"
        ).agg(AGG)
        parts.append(bars.dropna(subset=["open"]))
    if not parts:
        return pd.DataFrame(columns=["ts", *AGG])
    out = pd.concat(parts)
    out.index.name = "ts"
    out = out.reset_index()
    out["volume"] = out["volume"].astype("int64")
    return out.sort_values("ts").reset_index(drop=True)


def resample_intraday(base: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Public entry point for callers that hold their own (e.g. truncated) 15m frame."""
    return _resample_intraday(base, minutes)


def _daily_from_intraday(base: pd.DataFrame) -> pd.DataFrame:
    indexed = base.set_index("ts").sort_index()
    out = indexed.groupby(indexed.index.normalize()).agg(AGG)
    out.index.name = "ts"
    out = out.reset_index()
    out["volume"] = out["volume"].astype("int64")
    return out.sort_values("ts").reset_index(drop=True)


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
        return _resample_intraday(base_15m(symbol), 30)
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
        return _resample_intraday(base_15m(symbol), 60)

    day_frame = daily(symbol, prefer_native=prefer_native_daily)
    if timeframe == "1d":
        return day_frame
    if timeframe == "1w":
        return weekly(day_frame)
    return monthly(day_frame)

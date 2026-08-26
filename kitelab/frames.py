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
        return frame.sort_values("ts").reset_index(drop=True)
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
        return _resample_intraday(base_15m(symbol), 60)

    day_frame = daily(symbol, prefer_native=prefer_native_daily)
    if timeframe == "1d":
        return day_frame
    if timeframe == "1w":
        return weekly(day_frame)
    return monthly(day_frame)

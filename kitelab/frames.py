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

from . import config
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


# The interval names the daily path is called with: frames.daily says "daily",
# clean_data passes the file suffix "day", and load() speaks in TIMEFRAMES.
DAILY_INTERVALS = {"day", "daily", "1d"}


def _is_daily(interval: str) -> bool:
    return interval in DAILY_INTERVALS


def dedupe_sessions(frame: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """One bar per session: normalise the stamp to the date, keep the last.

    Added 2026-09-07. COALINDIA, ONGC and PETRONET each carried 2015-12-31
    twice -- once stamped 00:00 and once 09:15, identical OHLCV -- because
    fetch.py's drop_duplicates ran on the raw stamp, before anything
    normalised it. Across the store: 16 duplicated sessions in CLEAN, 21 in
    RAW (GOLD, SILVER and CRUDEOIL each hold three identical pairs stamped
    10:00 and 12:00; NIFTY BANK holds four with junk stamps such as 08:59:23
    and 11:54:10). A doubled session doubles that day's volume in every
    turnover measure and puts two closes on one date in every resampler.

    The daily frame's ts column comes out NORMALISED, which frames.daily did
    already; doing it here means the cleaned files on disk carry dates too.
    `keep="last"` is the later raw stamp, which for the NIFTY BANK pairs is
    the one closer to the session close.
    """
    if frame.empty or not _is_daily(interval):
        return frame
    stamps = pd.to_datetime(frame["ts"])
    normalised = stamps.dt.normalize()
    if normalised.equals(stamps) and not normalised.duplicated().any():
        return frame
    frame = frame.copy()
    frame["ts"] = normalised
    doubled = frame["ts"].duplicated(keep="last")
    if doubled.any():
        tag = f"{symbol}:{interval}:sessions"
        if tag not in _CLEAN_WARNED:
            dates = ", ".join(str(d.date()) for d in frame.loc[doubled, "ts"].head(3))
            print(f"[kitelab] {symbol}: dropped {int(doubled.sum()):,} duplicated "
                  f"{interval} session(s) after normalising the stamp ({dates}"
                  f"{', ...' if doubled.sum() > 3 else ''})")
            _CLEAN_WARNED.add(tag)
        frame = frame.loc[~doubled]
    return frame.reset_index(drop=True)


# A gap this long between consecutive session dates is a LISTING BREAK: what
# is on the far side is a different instrument, or the same one after a
# suspension long enough that nothing traded across it could have been held.
#
# Measured 2026-09-07 (audit A6/A7) on the 999-stock universe: 10 files carry
# a gap over 180 days. ROTO was suspended 2018-04-13 -> 2022-04-21 (1,469 days,
# Rs83.35 -> Rs37.55) and a cached trade held straight through it at -53.5R.
# STARHEALTH has 61 bars from 2016-01-04 that predate its 2021-12-10 listing;
# HOMEFIRST 90 bars in 2018 before listing 2021-02-03; PIXTRANS 113 bars
# before a 737-day gap. 180 days rather than 90: the longest ordinary hole in
# a listed stock's daily file is under a month, and 180 keeps clear of the
# 169-day seam in JASH, which is a symbol reuse and belongs in EXCLUDED, not a
# rule that would then have to explain itself on every 4-month suspension.
LISTING_BREAK_DAYS = 180


def history_start(symbol: str, dates: pd.Series,
                  demergers: dict[str, list[str]] | None = None,
                  history_starts: dict[str, str] | None = None):
    """Where a symbol's usable history begins, or None if all of it is usable.

    `dates` are the normalised session dates in order. Three things restart the
    history, and the LATEST of them wins:

      - a gap over LISTING_BREAK_DAYS between consecutive sessions;
      - a demerger ex-date from config.DEMERGERS: Kite adjusts splits and
        bonuses at serve time but not demergers, so the close on the far side
        of one is a different company's price;
      - an explicit start in config.HISTORY_STARTS, for a series that is
        continuous but wrong (HINDPETRO before 2015).

    Returns (start, reason); the reason is the text printed when bars are cut.
    """
    if demergers is None:
        demergers = config.DEMERGERS
    if history_starts is None:
        history_starts = config.HISTORY_STARTS
    if len(dates) == 0:
        return None
    dates = pd.to_datetime(pd.Series(dates)).dt.normalize().reset_index(drop=True)
    candidates: list[tuple[pd.Timestamp, str]] = []
    gaps = dates.diff().dt.days
    breaks = gaps[gaps > LISTING_BREAK_DAYS]
    if len(breaks):
        at = breaks.index[-1]
        candidates.append((dates[at], f"{int(breaks.iloc[-1]):,}-day listing break"))
    for ex_date in demergers.get(symbol, []):
        ex = pd.Timestamp(ex_date)
        after = dates[dates >= ex]
        if len(after):
            candidates.append((after.iloc[0], f"demerger ex-date {ex.date()}"))
    if symbol in history_starts:
        ex = pd.Timestamp(history_starts[symbol])
        after = dates[dates >= ex]
        if len(after):
            candidates.append((after.iloc[0], "config.HISTORY_STARTS"))
    if not candidates:
        return None
    start, reason = max(candidates, key=lambda c: c[0])
    if start <= dates.iloc[0]:
        return None
    return start, reason


def drop_before_history_start(frame: pd.DataFrame, symbol: str,
                              interval: str) -> pd.DataFrame:
    """Cut everything before the last listing break / demerger / configured start.

    Daily frames only; the intraday frame inherits it in base_15m, which trims
    to the first DAILY bar. Every consumer of daily bars -- frames.daily,
    frames.load, the weekly/monthly/quarterly resamplers, and clean_data's
    cleaned copies -- goes through sanitise(), so this is the one place the rule
    has to live.
    """
    if frame.empty or not _is_daily(interval):
        return frame
    found = history_start(symbol, frame["ts"])
    if found is None:
        return frame
    start, reason = found
    doomed = pd.to_datetime(frame["ts"]) < start
    if not doomed.any():
        return frame
    tag = f"{symbol}:{interval}:history"
    if tag not in _CLEAN_WARNED:
        print(f"[kitelab] {symbol}: dropped {int(doomed.sum()):,} {interval} bars "
              f"before {start.date()} ({reason}; history restarts there)")
        _CLEAN_WARNED.add(tag)
    return frame.loc[~doomed].reset_index(drop=True)


# An intraday session whose last close is this far from the daily close is
# priced in a different unit from the daily bar. Measured 2026-09-07 over every
# 15-minute file with a daily twin: 1,837 sessions differ by 2-5%, 77 by
# 5-10%, 4 by 10-15% -- the closing-auction / VWAP-vs-last-trade effect
# (MUTHOOTFIN 2018-12-06 at 5.8%, IOC 2018-10-04 at 9.2%) -- then NOTHING
# between 15% and 50%, and four sessions above it: ALANKIT 2015-09-22 at 5.0x,
# DIVISLAB 2015-09-22 at 2.005x, ALANKIT 2016-10-18 at 2.002x, MOTHERSON
# 2015-07-22 at 1.505x. Those are ex-dates where Kite adjusted the daily bar
# for the split/bonus but not the 15-minute bars. 0.25 sits in the empty band
# with a 2x margin to the smallest hit and to the largest auction mismatch.
EX_DATE_MISMATCH = 0.25


def rescale_ex_date_sessions(frame: pd.DataFrame, day: pd.DataFrame,
                             symbol: str) -> pd.DataFrame:
    """Scale an intraday session onto its daily bar when the two disagree by
    more than EX_DATE_MISMATCH.

    The daily bar is the authority: it is what Kite adjusts, and it is what
    every close-based rule trades on. Price is scaled by daily close / last
    intraday close; volume is scaled so the session's sum matches the daily
    volume, because the intraday volume on those sessions is in pre-action
    shares too (DIVISLAB's 15-minute bars sum to 0.50x the daily volume on its
    2:1 day, ALANKIT's to 0.20x on its 5x day, MOTHERSON's to 0.66x).

    Nothing under the threshold is touched, so the 4-13% closing-auction
    mismatches stay exactly as Kite served them.
    """
    if frame.empty or day.empty:
        return frame
    session = frame["ts"].dt.normalize()
    grouped = frame.groupby(session)
    mine = pd.DataFrame({"m_close": grouped["close"].last(),
                         "m_volume": grouped["volume"].sum()})
    ref = day.set_index(pd.to_datetime(day["ts"]).dt.normalize())[["close", "volume"]]
    ref = ref[~ref.index.duplicated(keep="last")]
    joined = mine.join(ref.rename(columns={"close": "d_close", "volume": "d_volume"}),
                       how="inner")
    factor = joined["d_close"] / joined["m_close"]
    hit = (factor - 1).abs() > EX_DATE_MISMATCH
    if not hit.any():
        return frame
    frame = frame.copy()
    for stamp, row in joined[hit].iterrows():
        mask = (session == stamp).to_numpy()
        scale = row["d_close"] / row["m_close"]
        for col in ("open", "high", "low", "close"):
            frame.loc[mask, col] = frame.loc[mask, col] * scale
        if row["m_volume"] > 0 and row["d_volume"] > 0:
            v_scale = row["d_volume"] / row["m_volume"]
            frame.loc[mask, "volume"] = (frame.loc[mask, "volume"] * v_scale).round()
    frame["volume"] = frame["volume"].astype("int64")
    tag = f"{symbol}:15-minute:ex-date"
    if tag not in _CLEAN_WARNED:
        shown = ", ".join(f"{d.date()} x{1 / f:.2f}" for d, f in factor[hit].items())
        print(f"[kitelab] {symbol}: rescaled {int(hit.sum())} intraday session(s) "
              f"priced in pre-corporate-action units onto the daily bar ({shown})")
        _CLEAN_WARNED.add(tag)
    return frame


def market_wide_days(closes: dict[str, pd.Series], drop: float = 0.08,
                     share: float = 0.25, min_symbols: int = 50) -> pd.DatetimeIndex:
    """Sessions on which at least `share` of the symbols with a bar fell over
    `drop` close-to-close. A single stock halving on one of these is a crash,
    not a corporate action.

    Measured 2026-09-07 on the 999-stock universe: 2008-01-21/22, the October
    2008 run, 2015-08-24, 2020-03-12/23 and 2024-06-04 are the days that
    qualify; on every demerger ex-date in config.DEMERGERS under 5% of stocks
    fell that far. `min_symbols` stops a Saturday session with three bars from
    counting as a market-wide day.

    `closes` maps symbol -> Series of closes indexed by normalised date.
    """
    if not closes:
        return pd.DatetimeIndex([])
    returns = pd.DataFrame({s: c[~c.index.duplicated(keep="last")].pct_change()
                            for s, c in closes.items()})
    counted = returns.notna().sum(axis=1)
    fell = (returns < -drop).sum(axis=1)
    hit = (counted >= min_symbols) & (fell / counted.where(counted > 0) >= share)
    return pd.DatetimeIndex(returns.index[hit])


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

    Two more, daily frames only, added 2026-09-07: one bar per session
    (dedupe_sessions) and nothing before the last listing break, demerger
    ex-date or configured start (drop_before_history_start). They run last
    so the gap arithmetic sees clean dates. scripts.clean_data mirrors this
    order stage by stage and checks its result against this function.
    """
    cols = ["open", "high", "low", "close"]
    broken = (frame[cols] <= 0).any(axis=1)
    if broken.any():
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
        frame = frame.reset_index(drop=True)
    frame = drop_untraded_outliers(
        enforce_containment(frame, symbol, interval), symbol, interval)
    frame = dedupe_sessions(frame, symbol, interval)
    return drop_before_history_start(frame, symbol, interval)



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
    same trading symbol. Those bars are dropped by default, using the first DAILY bar
    as the listing date.

    Since 2026-09-07 that first daily bar is read through daily(), i.e. AFTER the
    listing-break / demerger / HISTORY_STARTS cut, so the intraday frame restarts
    where the daily one does -- ROTO's 15-minute bars from 2018 go with its daily
    ones. The same daily frame then rescales any session Kite left in
    pre-corporate-action units (rescale_ex_date_sessions).
    """
    path = _path(symbol, "15minute")
    if not path.exists():
        raise SystemExit(f"No 15-minute data for {symbol}. Run: python -m scripts.backfill")
    frame = pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    frame = sanitise(frame, symbol, "15-minute")

    native = _path(symbol, "day")
    if native.exists():
        day = daily(symbol)
        if trim_orphans and not day.empty:
            listed_on = day["ts"].min()
            orphans = frame["ts"] < listed_on
            if orphans.any():
                if symbol not in _TRIM_WARNED:
                    print(
                        f"[kitelab] {symbol}: dropped {int(orphans.sum()):,} intraday bars "
                        f"before {listed_on.date()} (predate the first usable daily bar)."
                    )
                    _TRIM_WARNED.add(symbol)
                frame = frame.loc[~orphans].reset_index(drop=True)
        frame = rescale_ex_date_sessions(frame, day, symbol)
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


def _daily_from_intraday(base: pd.DataFrame, symbol: str) -> pd.DataFrame:
    indexed = base.set_index("ts").sort_index()
    out = indexed.groupby(indexed.index.normalize()).agg(AGG)
    out.index.name = "ts"
    out = out.reset_index()
    out["volume"] = out["volume"].astype("int64")
    out = out.sort_values("ts").reset_index(drop=True)
    # The intraday bars were sanitised as intraday; the daily rules (one bar per
    # session, nothing before the last break) still have to run on the result.
    return sanitise(out, symbol, "daily")


@lru_cache(maxsize=_CACHE_SYMBOLS * 2)
def daily(symbol: str, prefer_native: bool = True) -> pd.DataFrame:
    """Daily candles, from Kite's `day` interval if available, else from 15-min bars.

    Sorted on the RAW stamp before sanitise() normalises it, so that when a
    session is stored twice (00:00 and 09:15) "keep last" means the later
    stamp. Every daily consumer -- load("1d"/"1w"/"1M"), weekly(), monthly(),
    quarterly(), portfolio's mark-to-market, slippage's liquidity -- reads
    through here, which is what makes the rules in sanitise() universal.
    """
    native = _path(symbol, "day")
    if prefer_native and native.exists():
        frame = pd.read_parquet(native)
        frame["ts"] = pd.to_datetime(frame["ts"])
        frame = frame.sort_values("ts", kind="stable").reset_index(drop=True)
        return sanitise(frame, symbol, "daily")
    return _daily_from_intraday(base_15m(symbol), symbol)


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

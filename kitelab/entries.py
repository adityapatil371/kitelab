"""Six entry rules chosen for COVERAGE, each tradable at two stop widths.

WHY THIS EXISTS, 2026-09-17. The board carried nine labels holding about four
ideas: `dv|20-10`/`dv|55-20` overlap at phi 0.958 and `ema|0.0`/`pair|MD` at
0.843 (scripts/board_span.py). The user's objection was that the dashboard
showed "the same thing wearing different clothes", and the obvious fix --
keep whichever rules scored best -- is exactly the criterion scripts/pbo.py
measured at PBO 0.412 here, worse than a coin flip out of sample. So the
replacement slate was picked by MAXIMIN DISTANCE on firing overlap alone, with
no return read anywhere in the selection. See scripts/board_span.py for the
pre-registration and the run that produced this list.

THE SIX. Definitions are copied verbatim in behaviour from
scripts/entry_exit_grid.build_signals, so the overlap matrix measured there
describes exactly what this module trades:

    mr      today's close is the lowest of the last 20      mean reversion
    pull    above the 200-day average, below the 20-day     pullback in a trend
    vcon    today's range is the narrowest of the last 7    volatility contraction
    vol     volume above 3x its own 50-day median           volume event
    cal     the first session of a calendar month           calendar
    xrank   252-day return in the top decile OF THE MARKET  cross-sectional momentum

`xrank` is the only one that is not a fact about the symbol alone -- it ranks
every stock in cfg.merged against every other, so _rank_panel() builds the
whole close panel once per process and caches it. That is ~40s and ~41MB, paid
on the first xrank symbol and never again. The universe is already inside the
cache stamp, so the hidden input is not hidden from signals.stamp().

TWO STOP WIDTHS, and this is the point of the change. The board's uniform stop
-- the entry candle's own low -- ends 54.6% of trades on DAY ONE and leaves a
median holding period of one session, which is why the board's eight exit rules
collapse onto 1 Kaiser idea out of 8 (board_span.py, 2026-09-17): the stop fires
before any exit rule gets a turn. At 3xATR the median trade runs 17-77 sessions
and the exits separate (2 Kaiser / 6.00 Li-Ji). So the stop is the binding
constraint on the board's variety, and STOPS makes it an axis instead of an
unexamined convention.

THE EXIT IS FIXED AT 60 SESSIONS, and it is a pre-registered choice rather than
a measured one. These six entries have no natural exit of their own the way an
EMA cross or a Turtle channel does, so something had to be picked, and picking
it on performance would reintroduce the hindsight this whole slate exists to
avoid. One quarter is set by the calendar; it is identical across all six, so
the six differ ONLY in their entry; and it is long enough that the two stop
widths produce genuinely different trades (under `own` the median trade is
1-2 sessions, so the cap never binds; under atr3 the stop-only median is 77,
so the cap binds often and the wide stop still gets room to breathe). A
shorter cap would have hidden the stop axis behind itself. Sweeping it is one
edit to MAXHOLD plus a rebuild.

CONVENTIONS, all inherited rather than re-decided: entry on the RISING EDGE of
the signal only (a rule that stays true for forty bars is one trade, not forty),
filled at that bar's close; stop evaluated on closes (stop_on_close, the class
convention); one position per symbol at a time; sizing from the stop distance;
spread on both fills; intraday fee rates only for a same-session round trip.
backtest.NEXT_OPEN_FILLS is honoured so arm B of scripts/wf_attach.py can refill
these rows at the next open like every other producer.
"""
from __future__ import annotations

import contextlib
import weakref

import numpy as np
import pandas as pd

from . import backtest, config, frames, indicators, sizing, slippage
from .backtest import charges

# entry name -> what it means, for labels. Order is the maximin selection order
# from scripts/board_span.py, which is why `mr` is first and `xrank` sixth.
ENTRIES = {
    "mr": "20-day low",
    "vcon": "narrowest 7-day range",
    "vol": "volume 3x its 50-day median",
    "cal": "first session of the month",
    "xrank": "top decile 252-day return",
    "pull": "above the 200-day, below the 20-day",
}

# variant -> ATR multiple. None means the board's historical convention, the
# entry candle's own low. See the module docstring for why this is an axis.
STOPS = {"own": None, "atr3": 3.0}

MAXHOLD = 60       # sessions. One quarter. Pre-registered, NOT measured.
ATR_LEN = 14       # the standard Wilder window, same as scripts/xrank_account.py
MOM_LOOKBACK = 252  # Jegadeesh-Titman, fixed externally and not swept here
XRANK_TOP = 0.90   # top decile, matching scripts/entry_exit_grid.build_signals

_PANEL: pd.DataFrame | None = None

# The price source `_PANEL` was built from, held WEAKLY (2026-09-17).
#
# validation._shuffled_cagrs monkey-patches frames.daily with a fresh closure
# per shuffle round, so a panel cached across rounds would date the entries
# from one shuffled market and the bars from another -- and if the real board
# warmed the cache first, the shuffled control would keep the REAL market's
# cross-sectional ranks and the permutation gate would test nothing. xrank is
# the board's first rule whose signal depends on OTHER symbols, so it is the
# first to care which market it is looking at.
#
# Weak, not strong: a strong reference would keep the previous round's closure
# -- and the up-to-1,000 permuted frames cached inside it -- alive alongside
# the current round's, doubling each worker's peak memory. A dead referent
# reads as a miss, which is the safe direction.
_PANEL_SRC: "weakref.ref | None" = None


def _panel_is_current() -> bool:
    """True when the cached panel was built from today's frames.daily."""
    return _PANEL_SRC is not None and _PANEL_SRC() is frames.daily


# The symbols the panel ranks against each other. None means the whole
# universe, which is what the board uses.
#
# validation._shuffled_cagrs narrows it to the round's pool (2026-09-17).
# Everything else in that test is already pool-scoped -- the calendar comes
# from the pool, and the observed CAGR is the real trades of the pool's
# symbols -- so the null market IS the pool, and ranking against 940 stocks
# the round never simulates was both wrong in kind and ruinous in cost:
# building the panel over all 1,000 shuffled symbols made each of 4 spawned
# workers hold the whole universe twice (real frames plus permuted ones), the
# cgroup peaked at 7.47 GB of 7.9 GB and the kernel OOM-killed the rebuild.
#
# The comparison stays fair because the threshold is a PERCENTILE: the top
# decile of 60 names fires on the same share of stock-sessions as the top
# decile of 1,000, so the rule is equally selective on both sides of the test.
_PANEL_SCOPE: tuple | None = None
_PANEL_KEY: tuple | None = None       # the scope the cached panel was built for


@contextlib.contextmanager
def panel_scope(symbols):
    """Rank against `symbols` only, for the duration of the block."""
    global _PANEL_SCOPE
    saved = _PANEL_SCOPE
    _PANEL_SCOPE = tuple(sorted(str(x) for x in symbols)) if symbols else None
    try:
        yield
    finally:
        _PANEL_SCOPE = saved


def _rank_panel() -> pd.DataFrame:
    """Cross-sectional momentum-decile membership for the whole universe.

    Built once per process PER PRICE SOURCE and cached. Columns are symbols,
    rows sessions,
    values True where that stock's 252-session return sits in the market's top
    decile THAT DAY. Point-in-time by construction: the rank on session t uses
    only closes at t and t-252.
    """
    global _PANEL, _PANEL_SRC, _PANEL_KEY
    if _PANEL is not None and _panel_is_current() and _PANEL_KEY == _PANEL_SCOPE:
        return _PANEL
    members = (list(_PANEL_SCOPE) if _PANEL_SCOPE is not None
               else sorted(config.load().merged))
    closes = {}
    for n, symbol in enumerate(members, 1):
        try:
            bars = frames.daily(symbol)
        except Exception:
            continue
        if bars is None or bars.empty:
            continue
        closes[symbol] = pd.Series(bars["close"].to_numpy(float),
                                   index=pd.DatetimeIndex(bars["ts"]))
        if n % 250 == 0:
            print(f"    xrank panel: {n:,} of {len(members):,} symbols read",
                  flush=True)
    panel = pd.DataFrame(closes).sort_index()
    ret = panel / panel.shift(MOM_LOOKBACK) - 1.0
    _PANEL = ret.rank(axis=1, pct=True).gt(XRANK_TOP) & panel.notna()
    _PANEL_SRC = weakref.ref(frames.daily)
    _PANEL_KEY = _PANEL_SCOPE
    print(f"    xrank panel: {_PANEL.shape[0]:,} sessions x {_PANEL.shape[1]:,} "
          f"symbols, {int(_PANEL.to_numpy().sum()):,} firings", flush=True)
    return _PANEL


def clear_caches() -> None:
    """Drop the cross-sectional panel. Mirrors frames.clear_caches()."""
    global _PANEL, _PANEL_SRC, _PANEL_KEY
    _PANEL = None
    _PANEL_SRC = None
    _PANEL_KEY = None


def signal(symbol: str, entry: str, day: pd.DataFrame) -> np.ndarray:
    """Boolean array, one element per session of `day`, True where `entry` fires.

    `day` is passed in rather than re-loaded so the caller pays frames.daily
    once. Every window uses min_periods equal to its own length, so a rule is
    silent until it has the history it claims to need -- a stock with 19 bars
    has no 20-day low.
    """
    c = pd.Series(day["close"].to_numpy(float))
    h = pd.Series(day["high"].to_numpy(float))
    low = pd.Series(day["low"].to_numpy(float))
    v = pd.Series(day["volume"].to_numpy(float))
    if entry == "mr":
        out = c.le(c.rolling(20, min_periods=20).min())
    elif entry == "pull":
        out = (c.gt(c.rolling(200, min_periods=200).mean())
               & c.lt(c.rolling(20, min_periods=20).mean()))
    elif entry == "vcon":
        rng = h - low
        out = rng.le(rng.rolling(7, min_periods=7).min())
    elif entry == "vol":
        vmed = v.rolling(50, min_periods=50).median()
        out = v.gt(3.0 * vmed) & vmed.gt(0)
    elif entry == "cal":
        months = pd.DatetimeIndex(day["ts"]).to_period("M")
        out = pd.Series(np.concatenate([[True], months[1:] != months[:-1]]))
    elif entry == "xrank":
        panel = _rank_panel()
        if symbol not in panel.columns:
            return np.zeros(len(day), dtype=bool)
        aligned = panel[symbol].reindex(pd.DatetimeIndex(day["ts"]))
        out = aligned.fillna(False).astype(bool).reset_index(drop=True)
    else:
        raise ValueError(f"unknown entry {entry!r}; known: {sorted(ENTRIES)}")
    return out.fillna(False).to_numpy(dtype=bool)


def simulate(symbol: str, entry: str, stop_mult: float | None = None) -> list[dict]:
    """Closed trades for one symbol, oldest first. Same dict shape as darvas.

    stop_mult=None  -> the entry candle's own low, the board's convention.
    stop_mult=3.0   -> close minus 3 x ATR(14), the wide arm of the stop axis.
    """
    if entry not in ENTRIES:
        raise ValueError(f"unknown entry {entry!r}; known: {sorted(ENTRIES)}")
    day = frames.daily(symbol).reset_index(drop=True)
    if day.empty or len(day) < 2:
        return []
    fires = signal(symbol, entry, day)
    open_, high, low, close = (day[c].to_numpy(float)
                               for c in ("open", "high", "low", "close"))
    atr = indicators.atr(day["high"], day["low"], day["close"],
                         ATR_LEN).to_numpy(float)
    stamps = pd.DatetimeIndex(day["ts"]).tolist()
    total = len(day)

    trades: list[dict] = []
    position = 0
    while position < total:
        # The RISING EDGE only. `mr` and `pull` can stay true for weeks; without
        # this they would re-enter every session and the account would be paying
        # a spread a day to hold one position.
        fresh = fires[position] and position > 0 and not fires[position - 1]
        if not fresh:
            position += 1
            continue

        if stop_mult is None:
            stop = float(low[position])
        else:
            if not np.isfinite(atr[position]):
                position += 1
                continue          # inside the ATR warm-up; no stop can be drawn
            stop = float(close[position]) - stop_mult * float(atr[position])

        if backtest.NEXT_OPEN_FILLS:
            if position + 1 >= total:
                break             # signal on the last bar; nothing to fill at
            entry_index = position + 1
            entry_price = float(open_[entry_index])
        else:
            entry_index = position
            entry_price = float(close[position])
        risk = entry_price - stop
        if risk <= 0:
            # It opened at or below the line you were going to defend, or the
            # bar closed on its own low. Nothing to risk means nothing to size
            # against; the signal is skipped rather than filled. Same rule as
            # darvas.simulate.
            position += 1
            continue

        exit_at = None
        start = entry_index if backtest.NEXT_OPEN_FILLS else position + 1
        for step in range(start, total):
            # The stop is checked FIRST, so a bar that breaks both is charged
            # the worse of the two. Same order as backtest.simulate.
            if close[step] <= stop:
                exit_at = (step, float(close[step]), "stop (close)")
                break
            if step - entry_index >= MAXHOLD:
                exit_at = (step, float(close[step]), f"{MAXHOLD}-session limit")
                break
        if exit_at is None:
            break                 # still open at the end of the data

        exit_index, exit_price, reason = exit_at
        if backtest.NEXT_OPEN_FILLS:
            if exit_index + 1 >= total:
                break             # exit signalled on the last bar, unfillable
            exit_index += 1
            exit_price = float(open_[exit_index])
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue

        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, stamps[entry_index], quoted_entry, +1)
        exit_price = slippage.fill(symbol, stamps[exit_index], quoted_exit, -1)
        buy_value = entry_price * shares
        sell_value = exit_price * shares
        gross = (exit_price - entry_price) * shares
        same_session = stamps[entry_index].date() == stamps[exit_index].date()
        cost = charges(buy_value, sell_value, intraday=same_session)

        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[entry_index],
            "exit_ts": stamps[exit_index],
            "same_session": same_session,
            "entry_time": "EOD",
            "level": None,
            "level_kind": ENTRIES[entry],
            "max_risk_capital": sizing.risk_budget(),
            "risk_taken": risk_taken,
            "capital_capped": capped,
            "range": risk,
            "target": None,
            "final_stop": stop,
            "bars_held": exit_index - entry_index,
            "charges_best": cost,
            "net_profit_best": gross - cost,
            "entry_date": stamps[entry_index],
            "entry_price": entry_price,
            "quoted_entry": quoted_entry,
            "quoted_exit": quoted_exit,
            "spread_cost": ((quoted_exit - exit_price)
                            + (entry_price - quoted_entry)) * shares,
            "stop": stop,
            "risk_per_share": risk,
            "exit_date": stamps[exit_index],
            "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (stamps[exit_index] - stamps[entry_index]).days,
            "sessions_held": exit_index - entry_index,
            "shares": shares,
            "cost_of_entry": buy_value,
            "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost,
            "net_profit": gross - cost,
        })
        position = exit_index + 1

    return trades

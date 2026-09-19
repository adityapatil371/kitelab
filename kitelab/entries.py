"""Fourteen entry rules chosen for COVERAGE, each tradable at two stop widths.

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

EIGHT MORE, ADDED 2026-09-19 (NEXT_TESTS item 24), by the same criterion and
from a queue written down BEFORE it was measured. scripts/pine_span.py ran the
maximin pass again over eighteen candidates -- the six above, the nine
`entry_exit_grid` conditions, five rules pre-registered in that file's docstring
and five readings of the user's Pine -- reading firing panels only, never a
return. The user's instruction on seeing the table was "all of these".

    gapdn   open <= previous close x 0.97        gap down
    rsi30   RSI(14) closes back above 30         oscillator
    gap     open >  previous close x 1.03        gap up
    mktrel  up over 20 days while the MARKET is  relative strength
            down over 20 days
    dryup   volume below half its 50-day median  volume dry-up
    low252  today's close is the lowest of 252   one-year low
    inside  high < previous high AND low >       containment
            previous low
    pine    the user's Heikin-Ashi rule, weekly  the hand-traded rule

ONE PINE ROW, NOT FIVE, and this is the load-bearing decision of that pass. The
maximin order collapses after ten picks: once ANY Pine variant is on the slate
the other four sit at distance 0.18 and 0.17 from it, because the RSI-filtered
rule is a near-subset of the unfiltered one. Five Pine rows would have put five
labels on the board holding one idea -- exactly the fault the 2026-09-11
redundancy cut removed. `pine:W-full+1` is taken because it is the variant the
maximin pass reached first, not because it measured best; see kitelab/pine.py
for the two execution conventions it carries and why they were settled first.

`mktrel` IS THE SECOND CROSS-SECTIONAL RULE. Like `xrank` it is not a fact about
the symbol alone -- "the market is down" is a statement about every other stock
-- so it reads the same panel, under the same scope and same weak-reference
discipline, and for the same reason: a shuffled control that kept the REAL
market's direction would be testing nothing.

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

from . import backtest, config, frames, indicators, pine, sizing, slippage
from .backtest import charges

# entry name -> what it means, for labels. Order is the maximin selection order
# from scripts/board_span.py, which is why `mr` is first and `xrank` sixth.
ENTRIES = {
    # The six of 2026-09-17, in the maximin selection order of that pass.
    "mr": "20-day low",
    "vcon": "narrowest 7-day range",
    "vol": "volume 3x its 50-day median",
    "cal": "first session of the month",
    "xrank": "top decile 252-day return",
    "pull": "above the 200-day, below the 20-day",
    # The eight of 2026-09-19, likewise in their own maximin order.
    "gapdn": "opens 3% below the previous close",
    "rsi30": "RSI(14) closes back above 30",
    "gap": "opens 3% above the previous close",
    "mktrel": "up over 20 days while the market is down",
    "dryup": "volume below half its 50-day median",
    "low252": "252-day low",
    "inside": "inside the previous bar's range",
    "pine": "Heikin-Ashi no-wick + monthly EMA + RSI, weekly",
}

# variant -> ATR multiple. None means the board's historical convention, the
# entry candle's own low. See the module docstring for why this is an axis.
STOPS = {"own": None, "atr3": 3.0}

MAXHOLD = 60       # sessions. One quarter. Pre-registered, NOT measured.
ATR_LEN = 14       # the standard Wilder window, same as scripts/xrank_account.py
MOM_LOOKBACK = 252  # Jegadeesh-Titman, fixed externally and not swept here
XRANK_TOP = 0.90   # top decile, matching scripts/entry_exit_grid.build_signals

# The eight of 2026-09-19. Every one of these numbers is copied from the panel
# definition the maximin pass actually measured (scripts/pine_span.extra_signals
# and scripts/entry_exit_grid.build_signals), NOT re-chosen here -- if a
# threshold moved, the phi table that justified the rule would no longer
# describe the rule.
GAP_UP, GAP_DOWN = 1.03, 0.97    # of the previous close
RSI_LEN, RSI_FLOOR = 14, 30.0    # the oscillator leg: a close back above 30
DRYUP_MULT = 0.5                 # of the 50-day median volume
VOL_MEDIAN_LEN = 50
LOW_LOOKBACK = 252               # one year, against `mr`'s twenty sessions
REL_LOOKBACK = 20                # both legs of mktrel read the same window

# The Pine fires on WEEKLY bars; +1 puts the firing on the session after the
# one that decided it, because the rule fills at the next open. Settled on
# 2026-09-17 before any return was read -- see kitelab/pine.py.
PINE_TF = "W"
PINE_SHIFT = 1

_PANEL: pd.DataFrame | None = None

# The market's own 20-session direction, for `mktrel` (2026-09-19). A boolean
# Series over sessions, True where the market is DOWN. It is derived from the
# same close panel as _PANEL and cached beside it, so the two cross-sectional
# rules cost one panel build between them rather than two -- that build is ~40s
# and ~41MB, and doubling it was the difference that OOM-killed a rebuild once
# already (see _PANEL_SCOPE below).
_WEAK: pd.Series | None = None

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


def _build_panels() -> None:
    """Build BOTH cross-sectional artefacts from one pass over the universe.

    `_PANEL`  columns are symbols, rows sessions, values True where that stock's
              252-session return sits in the market's top decile THAT DAY.
    `_WEAK`   one value per session, True where the market itself is down over
              REL_LOOKBACK sessions.

    Both are point-in-time by construction: the value on session t uses only
    closes at t and earlier. Built once per process PER PRICE SOURCE AND SCOPE
    and cached; the close panel they are derived from is deliberately NOT kept,
    because a third ~41MB object per worker is what the 2026-09-17 OOM was made
    of.
    """
    global _PANEL, _WEAK, _PANEL_SRC, _PANEL_KEY
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

    # The market proxy: an equal-weighted index of whatever was listed each
    # session, compounded. Copied in behaviour from
    # scripts/entry_exit_grid.build_signals, which is the definition the
    # firing-overlap pass measured `mktrel` with.
    proxy = (1.0 + panel.pct_change(fill_method=None)
             .mean(axis=1, skipna=True).fillna(0.0)).cumprod()
    _WEAK = (proxy / proxy.shift(REL_LOOKBACK) - 1.0).lt(0.0)

    _PANEL_SRC = weakref.ref(frames.daily)
    _PANEL_KEY = _PANEL_SCOPE
    print(f"    xrank panel: {_PANEL.shape[0]:,} sessions x {_PANEL.shape[1]:,} "
          f"symbols, {int(_PANEL.to_numpy().sum()):,} firings", flush=True)
    print(f"    market proxy: down over {REL_LOOKBACK} sessions on "
          f"{int(_WEAK.to_numpy().sum()):,} of {len(_WEAK):,} sessions",
          flush=True)


def _panels_are_fresh() -> bool:
    return (_PANEL is not None and _panel_is_current()
            and _PANEL_KEY == _PANEL_SCOPE)


def _rank_panel() -> pd.DataFrame:
    """Cross-sectional momentum-decile membership for the whole universe."""
    if not _panels_are_fresh():
        _build_panels()
    return _PANEL


def _weak_market() -> pd.Series:
    """True on sessions where the market is down over REL_LOOKBACK sessions."""
    if not _panels_are_fresh():
        _build_panels()
    return _WEAK


def clear_caches() -> None:
    """Drop the cross-sectional panels. Mirrors frames.clear_caches()."""
    global _PANEL, _WEAK, _PANEL_SRC, _PANEL_KEY
    _PANEL = None
    _WEAK = None
    _PANEL_SRC = None
    _PANEL_KEY = None


def _pine_fires(symbol: str, day: pd.DataFrame) -> pd.Series:
    """The Pine's weekly entries, placed on this symbol's daily sessions.

    Two conventions, both settled on 2026-09-17 and documented in
    kitelab/pine.py: the firing is dated to `end_ts` (the session the weekly bar
    CLOSED, which is when the decision could first be taken -- NOT the Monday
    frames.weekly dates the bar to), and it is then shifted PINE_SHIFT sessions
    forward because the rule fills at the next open.

    A firing whose shifted session falls past the end of the symbol's history is
    dropped rather than clamped onto the last bar, which would invent a trade on
    a date the rule never reached.
    """
    fires = pd.Series(False, index=range(len(day)))
    sig = pine.signals(symbol, PINE_TF)
    if sig.empty:
        return fires
    stamp = sig["end_ts"] if "end_ts" in sig.columns else sig["ts"]
    decided = pd.DatetimeIndex(stamp[pine.entry_mask(sig, "long")]).normalize()
    sessions = pd.DatetimeIndex(day["ts"]).normalize()
    found = sessions.get_indexer(decided)      # -1 where the session is absent
    at = found[found >= 0] + PINE_SHIFT
    at = at[at < len(day)]
    fires.iloc[at] = True
    return fires


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
    # ---- the eight of 2026-09-19 ------------------------------------------
    elif entry == "gapdn":
        out = pd.Series(day["open"].to_numpy(float)).le(c.shift(1) * GAP_DOWN)
    elif entry == "gap":
        out = pd.Series(day["open"].to_numpy(float)).gt(c.shift(1) * GAP_UP)
    elif entry == "inside":
        out = h.lt(h.shift(1)) & low.gt(low.shift(1))
    elif entry == "rsi30":
        r = indicators.rsi(c, RSI_LEN)
        # The warm-up guard is NOT in scripts/pine_span.extra_signals, which is
        # where this rule's phi was measured. indicators.rsi uses
        # ewm(adjust=False) and so emits a number from the second bar, and a
        # 14-period Wilder average seeded one bar ago is not a 14-bar
        # oscillator. Guarding it here costs the first RSI_LEN sessions of each
        # symbol's history and honours this module's stated rule -- a rule is
        # silent until it has the history it claims to need. It can only REMOVE
        # firings, so the measured distinctness is not flattered by it.
        warm = pd.Series(np.arange(len(day)) >= RSI_LEN)
        out = r.gt(RSI_FLOOR) & r.shift(1).le(RSI_FLOOR) & warm
    elif entry == "low252":
        out = c.le(c.rolling(LOW_LOOKBACK, min_periods=LOW_LOOKBACK).min())
    elif entry == "dryup":
        vmed = v.rolling(VOL_MEDIAN_LEN, min_periods=VOL_MEDIAN_LEN).median()
        out = v.lt(DRYUP_MULT * vmed) & vmed.gt(0)
    elif entry == "mktrel":
        # The stock's own window is counted in ITS sessions, the market's in
        # the panel's -- which differ for a thinly traded name. That is the
        # right way round: "has this stock risen over twenty of its own bars"
        # is a fact about the stock, and "is the market down" is a fact about
        # the market, read off the calendar on the day the stock traded.
        weak = _weak_market().reindex(pd.DatetimeIndex(day["ts"]))
        weak = weak.fillna(False).astype(bool).reset_index(drop=True)
        own_up = (c / c.shift(REL_LOOKBACK) - 1.0).gt(0.0)
        out = own_up & weak
    elif entry == "pine":
        out = _pine_fires(symbol, day)
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

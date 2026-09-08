"""Is the edge real, or is it a pattern found in noise? The math, not the CLI.

Every function here was `scripts/validate.py` or `scripts/bootstrap.py` until
2026-09-04, when the dashboard needed to show the same numbers those two
scripts print. Both scripts import `positions`, `signal_lists`, `tag` and
`STRATEGY_LABELS` from `scripts.dashboard_data`, so `dashboard_data` importing
their compute functions back would be circular -- these functions have no
dependency on either script or on argparse, so they live here instead.
`scripts/validate.py` and `scripts/bootstrap.py` are thin CLI wrappers around
this module.

DIFFERENT FROM tests/. Those check that the CODE does what it was told, which
is necessary and says nothing about whether a strategy makes money. These are
the checks a trader runs on a strategy before believing it.

WHAT THE 2026-09-07 AUDIT CHANGED, in one place, because each item below
carries its own docstring but the reader deserves the list:

  A1  the benchmark was the MEDIAN stock's own whole-history CAGR (8.5 from
      2018); it is now an equal-weight PORTFOLIO from the scenario's start
      year (17.0 from 2018) -- see buy_and_hold.
  A2  the credibility t-stat treated every trade as an independent draw and
      measured the rule against ZERO; it is now cluster-robust by entry
      quarter and measured against what RANDOM entries on the same stocks
      would have earned -- see bootstrap_one, clustered_t, random_entry_r.
  A4  the permutation test shuffled each stock on its own, sampled the first
      60 stocks alphabetically, ran 10 rounds and charged the spread twice on
      the shuffled side -- see permutation_test.
  A10 walk-forward windows started at each strategy's own first trade and
      counted "positive" as a win; they are now one fixed calendar and a win
      is beating buy-and-hold in that window -- see walk_forward.
  --  the luck hurdle was the EXPECTED MAXIMUM of an edgeless board, which the
      best of an edgeless board clears about half the time; it is now a
      family-wise 5% bar -- see multiple_testing_summary.
"""
from __future__ import annotations

import math
import multiprocessing
import os
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import backtest, frames, portfolio, signals, slippage, strategies

# The account every rule-level check that has to simulate an account runs on.
# Rs2L at 1% risk, most-liquid-first -- the smallest account on the grid, at
# the default priority. Breakeven cost and the permutation test's CAGRs both
# go through portfolio.run at these settings whatever the page has on screen;
# see fixed_checks_by_universe for why that is stated rather than hidden.
CAPITAL = 200_000
RISK = 0.01
TOP_N = (1, 5, 10, 25)

DRAWS = 5_000
RISK_PCT = 1.0
SEED = 20260903

# Below this many trades a bootstrap or benchmark answer is noise about noise.
# Also the floor scripts.preflight's 3-symbol smoke build sits under, so every
# caller here must degrade to None/empty rather than raise.
MIN_TRADES = 30

# Family-wise error rate for the luck hurdle (owner's decision, 2026-09-07).
ALPHA = 0.05

# Walk-forward: one fixed calendar shared by every strategy (audit A10).
WINDOW_YEARS = 3
WALK_FORWARD_START = pd.Timestamp("2006-01-01")
WALK_FORWARD_MIN_YEARS = 2.0     # a trailing window shorter than this is shown, not counted
WALK_FORWARD_MIN_TRADES = 20     # a window with fewer trades has no CAGR and is not counted

# Permutation test (audit A4).
# 100, not 200 (coordinator, 2026-09-07): 200 rounds x 60 stocks measured
# 107 s (pair|MW) to 176 s (e1|daily) per universe, roughly 3-4 hours of every
# rebuild across 19 variants x 5 universes. 100 rounds halves that and still
# resolves p to 0.01, so a p <= 0.05 verdict rests on 5 shuffles, not 2. Below
# ~60 the verdict is back to seed noise (10 rounds gave pass/fail/fail on the
# same trades with seeds 1/2/3).
PERMUTATION_ROUNDS = 100
PERMUTATION_SAMPLE = 60
# Forked worker processes for the shuffle rounds; 1 runs them in-process.
PERMUTATION_WORKERS = max(1, min(4, (os.cpu_count() or 1) - 2))   # 4: the box has 7 GB; each spawned worker needs ~0.5 GB
PERMUTATION_TIMEOUT = 1800            # seconds a pool may take for one universe before the sequential fallback

# Benchmark: a member needs this many bars inside the span to be held at all;
# also the number of sessions a random entry must have behind it (drift).
MIN_HOLD_BARS = 250

# Random entries simulated per real trade for the drift term (audit A2).
DRIFT_DRAWS = 5

# Validation gate -- see fixed_checks_by_universe. A strategy must survive
# this much extra friction per side to pass "survives a cost margin".
BREAKEVEN_MARGIN_BP = 40.0         # top of the ~15-40bp real Zerodha+spread range


def cagr_of(trades, capital=CAPITAL, risk=RISK, priority=None):
    if not trades:
        return None
    r = portfolio.run(trades, capital, risk, priority=priority)
    return r.get("cagr_pct")


def load(strat, universe):
    return signals.load(f"{strat.cache}_all", universe)


# ------------------------------------------------------------ price access ----
_LOAD_ERRORS = (SystemExit, OSError, ValueError, KeyError, IndexError)


def _series(symbol) -> dict | None:
    """One stock's daily bars as numpy arrays, or None when there is no usable
    file. frames.daily raises SystemExit for a symbol it has never fetched
    (it is written for CLI callers); a benchmark or a drift simulation over
    999 names must skip that name, not stop the build."""
    try:
        day = frames.daily(symbol)
    except _LOAD_ERRORS:
        return None
    if day is None or len(day) == 0:
        return None
    day = day.reset_index(drop=True)
    return {
        "ts": pd.to_datetime(day["ts"]).to_numpy().astype("datetime64[ns]"),
        "open": day["open"].to_numpy(float), "high": day["high"].to_numpy(float),
        "low": day["low"].to_numpy(float), "close": day["close"].to_numpy(float),
    }


def _day64(stamp) -> np.datetime64:
    return np.datetime64(pd.Timestamp(stamp).normalize(), "ns")


# ------------------------------------------------------------ benchmark ----
def _hold(members, start=None, end=None) -> float | None:
    """Equal-weight portfolio CAGR of `members` between `start` and `end`.

    Rs1 into each member at its first close on or after `start` (its first
    close ever if it listed later -- late money, not free money: the rupee
    is counted from `start` and earns nothing until the stock exists), held
    to the last close on or before `end`. Wealth is the SUM over members,
    compounded over the calendar span from the earliest first-close to the
    latest last-close. Members with under MIN_HOLD_BARS bars in the span are
    skipped: a stock that traded for two months contributes a noise number.
    """
    start64 = None if start is None else _day64(start)
    end64 = None if end is None else _day64(end)
    wealth, count = 0.0, 0
    first_ts, last_ts = None, None
    for sym in members:
        s = _series(sym)
        if s is None:
            continue
        ts, close = s["ts"], s["close"]
        lo = 0 if start64 is None else int(np.searchsorted(ts, start64, side="left"))
        hi = len(ts) if end64 is None else int(np.searchsorted(ts, end64, side="right"))
        if hi - lo < MIN_HOLD_BARS:
            continue
        first, last = float(close[lo]), float(close[hi - 1])
        if not (first > 0 and last > 0):
            continue
        wealth += last / first
        count += 1
        first_ts = ts[lo] if first_ts is None else min(first_ts, ts[lo])
        last_ts = ts[hi - 1] if last_ts is None else max(last_ts, ts[hi - 1])
    if count == 0:
        return None
    years = (last_ts - first_ts) / np.timedelta64(1, "D") / 365.25
    if years <= 0:
        return None
    return float(((wealth / count) ** (1.0 / years) - 1.0) * 100.0)


def buy_and_hold(members, start_year=None, end_ts=None) -> float | None:
    """Equal-weight buy and hold of the same stocks over the same span, as a
    PORTFOLIO: Rs1 in each member at the first close on or after 1 Jan of
    `start_year` (or at the member's first close if it listed later), held to
    `end_ts` (default: the end of the data), reported as the compounded
    annual growth of the summed wealth. See _hold for the arithmetic.

    WHAT IT WAS. Until 2026-09-07 this returned the MEDIAN member's own
    whole-history CAGR, whatever start year was on screen. The 2026-09-07
    audit (A1) measured both on the 999 from 2018: median stock 8.5% a year,
    equal-weight portfolio 17.0%. The gap is Jensen's inequality on a
    right-skewed distribution -- a few stocks compounding at 40% lift the
    portfolio far above the typical stock, and a strategy that holds
    whatever signals is exposed to exactly those stocks. Comparing a rule's
    account (which is a portfolio) against the median stock (which is not)
    flattered every rule by roughly 8 points, and doing so from whole
    history against an account started in 2018 compared two different
    calendars as well.

    Equal weight, not cap weight: the strategies size by risk and hold
    whatever signals, so an index's concentration would be comparing two
    different things. Members listed after the start join at their first
    close because that is what a trader who "just held the universe" could
    have done -- and no earlier.

    Universe membership itself is the 2018 liquidity cut over stocks that are
    still listed, so this benchmark carries the same survivorship the rules
    do. It is the right comparison for "did the rule beat doing nothing on
    these stocks", not a market return.
    """
    start = None if start_year is None else pd.Timestamp(year=int(start_year), month=1, day=1)
    return _hold(members, start, end_ts)


def walk_forward_windows(end_ts=None) -> list[dict]:
    """The fixed calendar every strategy is scored on (audit A10).

    Three-year windows from WALK_FORWARD_START: 2006-2009, 2009-2012, ...
    Each carries `label` ("2006-2009", by nominal years -- the key
    hold_by_window and walk_forward_grid share), `from`, `to` (the nominal
    end, or `end_ts` if the data stops sooner -- never a date the data does
    not reach) and `partial` (under WALK_FORWARD_MIN_YEARS of data: shown,
    not counted). Windows starting after `end_ts` are not emitted. With no
    `end_ts` the calendar runs to today's date.
    """
    end = pd.Timestamp(end_ts).normalize() if end_ts is not None else pd.Timestamp.now().normalize()
    out = []
    left = WALK_FORWARD_START
    while left <= end:
        right = left + pd.DateOffset(years=WINDOW_YEARS)
        to = min(right, end)
        out.append({
            "label": f"{left.year}-{right.year}", "from": left, "to": to,
            "nominal_to": right,
            "partial": bool((to - left).days / 365.25 < WALK_FORWARD_MIN_YEARS),
        })
        left = right
    return out


def hold_by_window(members, windows=None, end_ts=None) -> dict:
    """Equal-weight buy-and-hold of `members` inside each walk-forward window,
    keyed by the window's label: {"2006-2009": float | None, ...}.

    The same arithmetic as buy_and_hold (Rs1 per member at its first close in
    the window, held to its last close in the window, MIN_HOLD_BARS required)
    -- the number a walk-forward window's rule CAGR is judged against, since
    2026-09-07 a window is a win only if the rule beat holding the stocks
    over that same window (see walk_forward). `windows` defaults to
    walk_forward_windows(end_ts).
    """
    if windows is None:
        windows = walk_forward_windows(end_ts)
    return {w["label"]: _hold(members, w["from"], w["to"]) for w in windows}


# --------------------------------------------------------- walk-forward ----
def walk_forward(trades, capital=CAPITAL, risk=RISK, priority=None,
                 hold: dict | None = None, end_ts=None) -> dict:
    """Rule CAGR in each DISJOINT calendar window, and how many it WON.

    WHAT IT WAS. Until 2026-09-07 the windows started at each strategy's own
    first trade, so no two strategies were scored on the same calendar (a
    rule whose first signal came in March 2007 had windows ending in March;
    one starting in 2006 had them ending in January), a trailing stub of a
    few months counted as a full window, and "win" meant CAGR > 0 -- which
    every rule achieved in a window where the market rose 30%. The audit
    (A10) found the gate passing rules that had lost to the stocks they
    traded in most windows.

    WHAT IT IS. One fixed calendar (walk_forward_windows: 3-year windows
    from 2006-01-01) shared by every strategy. A window is a WIN only if
    the rule's CAGR beat `hold` -- equal-weight buy-and-hold of the same
    universe over the same window (hold_by_window), the owner's decision on
    2026-09-07. A window with under WALK_FORWARD_MIN_TRADES trades has no
    CAGR and is not counted; the trailing window is `partial` when it spans
    under WALK_FORWARD_MIN_YEARS of data and is shown but not counted.
    A window is counted only when BOTH sides are known.

    `hold` is {label: cagr | None}; without it every window is uncounted,
    since there is nothing to beat. `end_ts` (default: the last exit in
    `trades`) clips the trailing window so the page never shows a year the
    data does not reach. Returns {"wins", "total_windows", "windows": [...]}
    with ISO dates -- the shape the CONTRACT gives the page.
    """
    if not trades:
        return {"wins": 0, "total_windows": 0, "windows": []}
    if end_ts is None:
        end_ts = max(pd.Timestamp(t["exit_ts"]) for t in trades)
    hold = hold or {}
    stamps = np.array([pd.Timestamp(t["entry_ts"]).to_datetime64() for t in trades])
    out, wins, counted = [], 0, 0
    for w in walk_forward_windows(end_ts):
        mask = (stamps >= w["from"].to_datetime64()) & (stamps < w["nominal_to"].to_datetime64())
        inside = [t for t, m in zip(trades, mask) if m]
        cagr = (cagr_of(inside, capital, risk, priority)
                if len(inside) >= WALK_FORWARD_MIN_TRADES else None)
        bench = hold.get(w["label"])
        # Decided on the ONE-DECIMAL figures the page shows, so the flag can
        # never disagree with the two numbers beside it; a displayed tie is
        # not a win (the conservative reading). Found by check_dashboard.js
        # on the 2026-09-07 rebuild: two windows at 14.0 vs 14.0 and -3.0 vs
        # -3.0 carried win=True from the unrounded comparison.
        win = ((round(cagr, 1) > round(bench, 1))
               if cagr is not None and bench is not None else None)
        if win is not None and not w["partial"]:
            counted += 1
            wins += int(win)
        out.append({"from": w["from"].strftime("%Y-%m-%d"), "to": w["to"].strftime("%Y-%m-%d"),
                    "cagr": round(cagr, 1) if cagr is not None else None,
                    "hold": round(bench, 1) if bench is not None else None,
                    "win": win, "partial": w["partial"]})
    return {"wins": wins, "total_windows": counted, "windows": out}


def scenario_key(uni_key: str, priority, capital) -> str:
    """The lookup key walk_forward_grid() stores under, and rows() in
    web/dashboard.html builds to match -- keep the two in sync.

    Plain int(), not the ":g" format the rest of this project's grid keys
    use (see tag() in scripts/dashboard_data.py): Python's %g switches to
    scientific notation above 1e6 -- f"{10_000_000:g}" is "1e+07" -- while
    JavaScript's default number-to-string never does at this scale, so the
    two sides would build different keys for the Rs1 crore capital and the
    lookup would silently miss. int() renders identically on both sides for
    every capital value this project uses.
    """
    return f"{uni_key}|{priority}|{int(capital)}"


def walk_forward_grid(trades, universes: dict, priorities: list, capitals: list,
                      hold_by_universe: dict | None = None, risk: float = RISK,
                      end_ts=None) -> dict:
    """Walk-forward, live per Universe x Priority x Capital -- added 2026-09-05
    so "Validated" answers "for the scenario on screen", not one fixed baseline.

    `trades` is the RAW cached trade list (NOT strategies.drop_overlaps-
    filtered): this mirrors what the account grid actually does --
    portfolio.run handles busy/cash skipping itself -- rather than the
    pre-filtered trades the fixed rule-level checks use. `universes` is
    {uni_key: member_set_or_None} (None = the whole universe), matching
    scripts.dashboard_data's own bucket dict. `hold_by_universe` is
    {uni_key: hold_by_window(members)} from the coordinator -- the
    per-window benchmark each window must beat (walk_forward); a universe
    missing from it has every window uncounted.

    Risk is NOT crossed here (unlike Universe/Priority/Capital): it mainly
    scales position size, which scales a window's magnitude, not usually
    its sign relative to the benchmark, so crossing it would roughly double
    the cost for little expected change in the verdict. Year is not crossed
    either: walk-forward already covers every year through its own disjoint
    windows, which is a different question from where the cumulative
    start-year control begins.

    Cost: len(universes) x len(priorities) x len(capitals) combinations,
    each a full walk_forward() pass (~7 cagr_of() calls).
    """
    hold_by_universe = hold_by_universe or {}
    out = {}
    for uni_key, members in universes.items():
        subset = trades if members is None else [t for t in trades if t["symbol"] in members]
        hold = hold_by_universe.get(uni_key)
        for priority in priorities:
            for capital in capitals:
                out[scenario_key(uni_key, priority, capital)] = walk_forward(
                    subset, capital=capital, risk=risk, priority=priority,
                    hold=hold, end_ts=end_ts)
    return out


def fixed_checks_by_universe(strat, trades, full_universe, universes: dict, rng,
                              permutation_rounds=PERMUTATION_ROUNDS) -> dict:
    """Credibility, Beats-shuffled-prices and Survives-cost, live per
    Universe. Added 2026-09-05 once they were TIMED and found affordable.

    NOT crossed with Priority or Capital, and here is exactly what account
    each one runs on, because the docstring that stood here until
    2026-09-07 claimed none of them ever called portfolio.run(), which was
    false for two of the three:

      Credibility (bootstrap_one)  -- no account. Net R multiples of the
          drop_overlaps trade list, resampled by entry quarter; the drift
          term simulates random entries on the same stocks, also without an
          account. Priority and Capital genuinely cannot move it.
      Survives-cost (breakeven)    -- portfolio.run at CAPITAL (Rs2L), RISK
          (1%), priority "liquidity", whatever the page shows: the bisection
          re-runs THAT account with extra basis points charged per side.
      Beats-shuffled-prices (permutation) -- the same fixed account, for
          both the observed CAGR and each shuffled round.

    The two account-level ones are pinned to one account rather than
    crossed because a 60-stock, 200-round permutation test per Priority x
    Capital would multiply the slowest check on the board tenfold, and the
    question it asks -- does the RULE find structure the shuffled market
    lacks -- is not one the account size changes in kind. The page's
    tooltips say "at Rs2L / 1% / most-liquid-first" so the reader knows.

    `trades` is the strategy's cached trade list as the caller has it (the
    build passes the spread-adjusted list, with slippage.ENABLED and the
    size cap set the way the Realistic-fills grid cell has them);
    drop_overlaps is applied per universe subset. `full_universe` is
    cfg.merged -- the pool the permutation test samples PERMUTATION_SAMPLE
    names from when `universes[key]` is None (the "all" entry); a subset
    samples from its own members. Keyed by universe key alone, not
    scenario_key().
    """
    out = {}
    for uni_key, members in universes.items():
        subset = trades if members is None else [t for t in trades if t["symbol"] in members]
        held = strategies.drop_overlaps(subset)
        if len(held) < MIN_TRADES:
            continue
        pool_universe = full_universe if members is None else members
        breakeven = _breakeven_payload(held)
        permutation = _permutation_summary(strat, pool_universe, rng, permutation_rounds,
                                            trades=subset)
        out[uni_key] = {
            "credibility": bootstrap_one(held), "breakeven": breakeven,
            "permutation": permutation, "fixed_gates": _fixed_gates(permutation, breakeven),
        }
    return out


def _fixed_gates(permutation, breakeven) -> dict:
    return {
        "distinguishable": bool(permutation and permutation["distinguishable"]),
        "breakeven_margin": bool(breakeven and (
            breakeven["status"] == "robust"
            or (breakeven["status"] == "finite" and breakeven["bp"] is not None
                and breakeven["bp"] > BREAKEVEN_MARGIN_BP))),
    }


# --------------------------------------------------------------- top-N ----
def without_best(trades, n):
    """The account with the n most profitable trades deleted."""
    if n >= len(trades):
        return None
    ordered = sorted(trades, key=lambda t: t.get("net_profit", 0.0), reverse=True)
    return cagr_of(ordered[n:])


# ----------------------------------------------------- breakeven friction ----
def _charged(trades, bps):
    """Every trade charged an extra `bps` per side on its turnover."""
    out = []
    for t in trades:
        cost = (bps / 10_000.0) * (t["entry_price"] + t["exit_price"]) * t["shares"]
        u = dict(t)
        u["exit_price"] = t["exit_price"] - cost / max(t["shares"], 1e-9)
        out.append(u)
    return out


def breakeven_cost(trades, hi=200.0):
    """Basis points per side at which the edge reaches zero. None if already
    negative, or if it survives even `hi`.

    Runs the fixed account (CAPITAL, RISK, priority "liquidity") through
    cagr_of at every bisection step -- an account-level number, not a
    trade-level one, at one fixed account.
    """
    if cagr_of(trades) is None or (cagr_of(trades) or 0) <= 0:
        return None
    if (cagr_of(_charged(trades, hi)) or -1) > 0:
        return float("inf")
    lo = 0.0
    for _ in range(12):                       # 12 bisections is ~0.05bp resolution
        mid = (lo + hi) / 2
        got = cagr_of(_charged(trades, mid))
        if got is not None and got > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 1)


# ---------------------------------------------------------- correlation ----
def monthly_returns(trades):
    """Realised profit by calendar month -- the series to correlate on."""
    by_month: dict = {}
    for t in trades:
        key = pd.Timestamp(t["exit_ts"]).to_period("M")
        by_month[key] = by_month.get(key, 0.0) + t.get("net_profit", 0.0)
    return by_month


def correlate(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 12:
        return None
    x = np.array([a[k] for k in keys], dtype=float)
    y = np.array([b[k] for k in keys], dtype=float)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


# ---------------------------------------------------------- permutation ----
def permutation_test(strat, universe, rounds, rng, sample=PERMUTATION_SAMPLE, trades=None):
    """Observed CAGR against the same rule run on shuffled price paths.

    Returns (observed, shuffled, pool_size); shuffled is the list of CAGRs
    the rule earned on `rounds` permuted markets. _permutation_summary turns
    that into the p-value the gate reads.

    THREE THINGS THE 2026-09-07 AUDIT (A4) CHANGED HERE:

    1. THE POOL IS A RANDOM SAMPLE, drawn by `rng`. It was
       sorted(universe)[:60] -- the first 60 names alphabetically, so a
       verdict on "small caps" was a verdict on A2ZINFRA through BAJAJHIND.
       On pair|MW's small caps the verdict flipped with the seed (pass, fail,
       fail for seeds 1, 2, 3) because 10 rounds cannot separate a rule from
       its own noise.
    2. DATES ARE PERMUTED JOINTLY. One permutation of the shared calendar is
       applied to every stock in the pool, so a session that was good for
       everything stays good for everything on its new date: the null keeps
       the market factor and destroys only the ORDER of days. Shuffling each
       stock independently (what this did before) also destroyed the
       cross-section, making a market that never existed and a null that is
       too easy to beat. See permuted_daily.
    3. THE SPREAD IS CHARGED ONCE. Every cached trade list is built with
       slippage.ENABLED = False and spread-adjusted afterwards by
       apply_spread; the shuffled side was built with whatever ENABLED state
       the caller had (True, in the build) AND then passed through
       apply_spread -- charging darvas and holygrail trades twice (dv|55-20
       on HAL: net 18,910 -> 18,756; hg|swing on HAL: 1,848 -> 1,378). The
       shuffled side is now built with ENABLED forced off and spread-adjusted
       once, exactly as the real side was.

    `rounds` defaults through the callers to PERMUTATION_ROUNDS (100; was
    10). p = (worse + 1) / (rounds + 1), so 200 rounds resolve p to 0.005 and
    a rule that beats every shuffled market scores p = 0.005, not 0.

    `trades` -- an optional pre-loaded trade list for `strat` (what
    scripts.dashboard_data holds in `slipped`). Falls back to
    load(strat, universe) when omitted, which is what scripts.validate does.
    A caller that already has the trades in memory and wants a NARROWER
    `universe` than they were cached under must pass them: load()'s cache is
    stamped to the universe it was SAVED under, so calling it with a subset
    silently misses.

    Both CAGRs come from cagr_of, i.e. portfolio.run on the fixed CAPITAL /
    RISK / "liquidity" account, with whatever slippage.ENABLED and size-cap
    state the caller has set -- the same on both sides.
    """
    names = sorted(str(s) for s in universe)
    if not names:
        return None, [], 0
    take = min(sample, len(names))
    pool = sorted(str(s) for s in rng.choice(names, size=take, replace=False))
    real = trades if trades is not None else load(strat, universe)
    if not real:
        return None, [], len(pool)
    pool_set = set(pool)
    observed = cagr_of([t for t in real if t["symbol"] in pool_set])
    if rounds <= 0:
        return observed, [], len(pool)

    calendar = _calendar(pool, frames.daily)
    # One seed per round, drawn from the caller's rng, so the result is
    # reproducible whatever the worker count and the multiset of shuffled
    # CAGRs -- hence p -- is identical whether the rounds run in one process
    # or eight. Parallel since 2026-09-07: sequential, 100 rounds x 5
    # universes x 19 variants was a multi-hour stage of every rebuild.
    seeds = [int(x) for x in rng.integers(0, 2**31 - 1, size=rounds)]
    workers = min(PERMUTATION_WORKERS, rounds) if rounds >= 20 else 1
    ident = _registry_identity(strat)
    if workers <= 1 or ident is None:
        got = _shuffled_cagrs(strat, pool, calendar, seeds)
    else:
        # SPAWNED, not forked (2026-09-07). A forked worker inherits the
        # build's whole heap -- every cached trade list, ~2.7 GB -- and
        # Python's refcounting then copies those pages on write, so eight
        # workers reached 3.4 GB each, the kernel killed some, the pool
        # replaced them and map() hung forever at a load average of zero.
        # A spawned worker imports kitelab fresh, looks the strategy up in
        # the registry by (key, variant) and reads only the pool's price
        # files. A strategy that is not on the registry (tests build their
        # own) takes the in-process path above.
        chunks = [seeds[i::workers] for i in range(workers)]
        args = [(ident, pool, calendar, c, slippage.ENABLED, slippage.MAX_PARTICIPATION)
                for c in chunks]
        ctx = multiprocessing.get_context("spawn")
        try:
            with ctx.Pool(workers) as pool_:
                # A pool whose workers die at start-up (an unimportable
                # __main__, an out-of-memory kill) replaces them silently and
                # map() waits forever -- which is how the 2026-09-07 rebuild
                # sat 15 minutes at a load average of zero. Bounded wait, and
                # the sequential path if it does not come back.
                parts = pool_.map_async(_spawn_worker, args).get(timeout=PERMUTATION_TIMEOUT)
            got = [g for part in parts for g in part]
        except Exception as exc:                     # noqa: BLE001 -- fall back, never hang
            print(f"[kitelab] permutation pool failed ({type(exc).__name__}: {exc}); "
                  f"running {rounds} rounds in-process", flush=True)
            got = _shuffled_cagrs(strat, pool, calendar, seeds)
    return observed, [g for g in got if g is not None], len(pool)


def _shuffled_cagrs(strat, pool, calendar, seeds) -> list:
    """The rule's CAGR on one shuffled market per seed. Runs in the calling
    process (tests, small round counts) or inside a forked worker."""
    saved_daily = frames.daily
    saved_enabled = slippage.ENABLED
    got = []
    try:
        for seed in seeds:
            round_rng = np.random.default_rng(seed)
            perm = round_rng.permutation(len(calendar))
            cache = {}

            def fake(sym, *a, **k):
                if sym not in cache:
                    cache[sym] = permuted_daily(sym, perm, calendar, round_rng, saved_daily)
                return cache[sym]

            slippage.ENABLED = False
            frames.daily = fake
            built = []
            for sym in pool:
                try:
                    built.extend(strat.build(sym))
                except (SystemExit, FileNotFoundError, ValueError, IndexError):
                    continue
            frames.daily = saved_daily
            slippage.ENABLED = saved_enabled
            built = [slippage.apply_spread(t) for t in built]
            got.append(cagr_of(built))
    finally:
        frames.daily = saved_daily
        slippage.ENABLED = saved_enabled
    return got


def _registry_identity(strat):
    """(key, variant) if `strat` is the registry's own object, else None."""
    from . import registry
    for s in registry.REGISTRY:
        if s is strat:
            return (s.key, s.variant)
    return None


def _spawn_worker(args):
    ident, pool, calendar, seeds, enabled, participation = args
    from . import registry
    strat = next(s for s in registry.REGISTRY if (s.key, s.variant) == tuple(ident))
    slippage.ENABLED = enabled
    slippage.MAX_PARTICIPATION = participation
    slippage.reset()
    return _shuffled_cagrs(strat, pool, calendar, seeds)


def _calendar(pool, real_daily) -> np.ndarray:
    """Every session any stock in the pool traded, sorted -- the axis the
    joint permutation acts on."""
    stamps = []
    for sym in pool:
        try:
            stamps.append(pd.to_datetime(real_daily(sym)["ts"]).to_numpy().astype("datetime64[ns]"))
        except _LOAD_ERRORS:
            continue
    if not stamps:
        return np.array([], dtype="datetime64[ns]")
    return np.unique(np.concatenate(stamps))


def permuted_daily(symbol, perm, calendar, rng, real_daily):
    """The same daily returns on different dates, under ONE shared permutation.

    `perm[c]` says which calendar session's return now sits on session c,
    for every stock at once -- so two stocks that both traded on the source
    session both receive that session's return on the same target session,
    and their co-movement survives. Preserves each stock's return
    DISTRIBUTION (drift, volatility, fat tails) and its terminal price (the
    multiset of returns is unchanged, so their product is), and destroys
    only the sequence. A trend rule has nothing left to find, so whatever
    it still earns is fitted noise -- against a market that still moves
    together, which is the market the rule actually faced.

    A stock that did not trade on every calendar session has some target
    sessions whose source it never saw, and some of its own returns that
    the permutation sent to sessions it does not have. Those leftovers are
    matched to each other in random order (`rng`), which keeps the stock's
    multiset exact; the share of its sessions that stay aligned with the
    rest of the pool is its share of the calendar.

    `real_daily` is passed in rather than read from the module, because the
    caller has patched frames.daily to serve these very frames: reading it
    from scope would call this function from inside itself.
    """
    day = real_daily(symbol).reset_index(drop=True)
    close = day["close"].to_numpy(float)
    if len(close) < 60 or len(calendar) == 0:
        return day
    rets = np.diff(close) / close[:-1]
    ts = pd.to_datetime(day["ts"]).to_numpy().astype("datetime64[ns]")[1:]
    pos = np.searchsorted(calendar, ts)                # each return's calendar slot
    ret_at = np.full(len(calendar), np.nan)
    ret_at[pos] = rets
    src = perm[pos]                                    # where each slot now draws from
    new = ret_at[src]
    missing = np.isnan(new)
    if missing.any():
        used = np.zeros(len(calendar), dtype=bool)
        used[src[~missing]] = True
        leftover = rets[~used[pos]]
        rng.shuffle(leftover)
        new[missing] = leftover
    walk = np.concatenate([[close[0]], close[0] * np.cumprod(1 + new)])
    scale = walk / close
    out = day.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = day[col].to_numpy(float) * scale
    return out


def _permutation_summary(strat, universe, rng, rounds, trades=None):
    """The payload row: p = (worse + 1) / (rounds + 1), distinguishable when
    p <= ALPHA. `worse` counts shuffled rounds that matched or beat the real
    CAGR; the +1 is the standard Monte-Carlo correction (Phipson & Smyth,
    2010) that counts the observed market as one more draw from the null.
    The old rule -- NOT distinguishable when a quarter or more of the rounds
    matched -- was p <= 0.25 on 10 rounds, a bar a coin clears often."""
    observed, shuffled, pool = permutation_test(strat, universe, rounds, rng, trades=trades)
    if observed is None or not shuffled:
        return None
    med = float(np.median(shuffled))
    worse = sum(1 for g in shuffled if g >= observed)
    p = (worse + 1) / (len(shuffled) + 1)
    return {"observed": round(observed, 1), "shuffled_median": round(med, 1),
            "beat_by": round(observed - med, 1), "rounds": len(shuffled),
            "p": round(p, 4), "distinguishable": bool(p <= ALPHA), "pool": pool}


# ------------------------------------------------------- bootstrap ----
def r_multiples(trades: list[dict]) -> np.ndarray:
    """Net R per trade: what the trade returned as a multiple of what it risked.

    net_profit, not gross, so brokerage, STT and the modelled spread are
    already inside every number here. Trades that risked nothing measurable
    (risk_taken of zero -- a stop at the entry price) carry no R and are
    dropped rather than counted as flat, which would dilute the distribution
    toward zero.
    """
    return np.array([t["net_profit"] / t["risk_taken"] for t in trades
                     if t.get("risk_taken")], dtype=float)


def _quarter_codes(trades: list[dict]) -> np.ndarray:
    """Entry calendar quarter per trade, as an integer (year * 4 + quarter),
    aligned with r_multiples: the same risk_taken filter applies."""
    out = []
    for t in trades:
        if t.get("risk_taken"):
            e = pd.Timestamp(t["entry_ts"])
            out.append(e.year * 4 + (e.month - 1) // 3)
    return np.array(out, dtype=int)


def _cluster_se(r: np.ndarray, groups: np.ndarray) -> tuple[float, int]:
    """Cluster-robust standard error of the mean of `r`, clusters = `groups`.

    Residuals are summed within each cluster; the variance of the mean is
    G/(G-1) x sum(S_g^2) / n^2 (Liang-Zeger with the small-sample factor).
    With a single cluster there is nothing to estimate from, so it falls
    back to the i.i.d. error rather than dividing by zero.
    """
    n = len(r)
    _, inv = np.unique(groups, return_inverse=True)
    g = int(inv.max()) + 1 if n else 0
    if n < 2:
        return 0.0, g
    if g < 2:
        return float(r.std(ddof=1) / math.sqrt(n)), g
    sums = np.bincount(inv, weights=r - r.mean(), minlength=g)
    return float(math.sqrt(g / (g - 1) * float((sums ** 2).sum()) / n ** 2)), g


def clustered_t(trades: list[dict]) -> dict | None:
    """Cluster-robust t of the mean net R, clusters = entry quarter. No
    drift term, no bootstrap: the helper scripts.dashboard_data calls per
    grid cell on the trades an account actually TOOK (`t_taken`), so the
    page can show credibility on the position-sized list next to the
    rule-level one. None below MIN_TRADES.

    Returns {"t_stat", "n", "n_clusters", "mean_r", "se_r"}.
    """
    r = r_multiples(trades)
    if len(r) < MIN_TRADES:
        return None
    se, g = _cluster_se(r, _quarter_codes(trades))
    mean = float(r.mean())
    return {"t_stat": round(mean / se, 2) if se > 0 else None, "n": int(len(r)),
            "n_clusters": g, "mean_r": round(mean, 4), "se_r": round(se, 4)}


def span_years(trades: list[dict]) -> float:
    """Calendar years the trade list actually covers.

    entry_ts / exit_ts, NOT entry_date -- only some producers set the _date
    pair, and the _ts fields are the ones every producer sets.
    """
    first = min(t["entry_ts"] for t in trades)
    last = max(t["exit_ts"] for t in trades)
    return max((last - first).days / 365.25, 1e-9)


def paths(r: np.ndarray, fraction: float, years: float) -> tuple:
    """(CAGR %, max drawdown %, MAR) for each row of an (n, trades) R matrix.

    Compounding is multiplicative on equity, which is what fixed-fractional
    risk means: a rupee risked is a percentage of the account at the time,
    not a constant. The floor on the per-trade factor stops a pathological
    draw from taking equity negative.

    The starting equity counts as a peak (fixed 2026-09-07): until then a
    list whose first trades lost showed no drawdown for that loss, because
    the running peak began at the first trade's own equity. quarter_bootstrap
    treats the block start as a peak of 0 in log space and must agree.
    """
    step = np.maximum(1.0 + fraction * r, 1e-6)
    curve = np.cumprod(step, axis=1)
    cagr = (curve[:, -1] ** (1.0 / years) - 1.0) * 100.0
    peak = np.maximum(np.maximum.accumulate(curve, axis=1), 1.0)
    dd = (curve / peak - 1.0).min(axis=1) * 100.0
    mar = np.divide(cagr, np.abs(dd), out=np.full_like(cagr, np.nan), where=dd < 0)
    return cagr, dd, mar


def quarter_bootstrap(r: np.ndarray, quarters: np.ndarray, years: float,
                      fraction: float, draws: int, rng) -> dict:
    """Resample CALENDAR QUARTERS with replacement and concatenate their trades.

    WHAT IT WAS. A moving-block bootstrap of BLOCK = 20 consecutive TRADES,
    with a module comment calling that "around a year of signals for the
    busier rules". Measured on 2026-09-07: 20 trades spanned 0.6 to 15 DAYS
    for 18 of the 19 variants (a rule firing across 999 stocks produces
    twenty signals in a week), so the blocks were far too short to carry a
    market regime and the resampled spread was nearly the i.i.d. one.

    WHAT IT IS. The block is the entry calendar quarter -- the unit on which
    the credibility t is clustered (see bootstrap_one), so the bootstrap and
    the analytic error agree on what "independent" means. Each draw picks G
    quarters with replacement (G = number of quarters the list spans) and
    concatenates their trades in that order, so a draw's length varies
    around n and a good quarter can appear twice or not at all.

    Returns per-draw arrays: "mean_r" (order-invariant, the credibility
    quantity), and "cagr" / "dd" / "mar" (compounded at `fraction` of equity
    per R, over `years`, for the Detail view). All of it is vectorised over
    draws through per-quarter sufficient statistics: the mean needs only
    each quarter's sum and count; the compounded terminal needs each
    quarter's total log-growth; and the max drawdown of a concatenation of
    blocks is exact from four numbers per block -- its total, its highest
    and lowest running level, and its own internal drawdown -- because the
    drawdown at any point is the smaller of "distance below the peak
    carried in from earlier blocks" and "distance below the peak inside
    this block", and both are block-level minima.
    """
    n = len(r)
    codes, inv = np.unique(quarters, return_inverse=True)
    g = len(codes)
    order = np.lexsort((np.arange(n), inv))                # by quarter, then time
    inv_s, r_s = inv[order], r[order]
    logstep = np.log(np.maximum(1.0 + fraction * r_s, 1e-6))
    starts = np.searchsorted(inv_s, np.arange(g))
    cum = np.cumsum(logstep)
    before = np.concatenate([[0.0], cum])[starts]          # level at each block's start
    v = cum - before[inv_s]                                # level inside the block
    big = 2.0 * (float(np.abs(v).max()) + 1.0)
    prefix_max = np.maximum.accumulate(v + inv_s * big) - inv_s * big
    prefix_max = np.maximum(prefix_max, 0.0)               # the block starts at a peak of 0
    total = np.bincount(inv_s, weights=logstep, minlength=g)
    maxv = np.maximum(np.maximum.reduceat(v, starts), 0.0)
    minv = np.minimum(np.minimum.reduceat(v, starts), 0.0)
    d_in = np.minimum.reduceat(v - prefix_max, starts)
    sums = np.bincount(inv_s, weights=r_s, minlength=g)
    counts = np.bincount(inv_s, minlength=g).astype(float)

    pick = rng.integers(0, g, size=(draws, g))
    mean_r = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    t_pick = total[pick]
    level = np.concatenate([np.zeros((draws, 1)), np.cumsum(t_pick, axis=1)[:, :-1]], axis=1)
    peak_prev = np.maximum.accumulate(
        np.concatenate([np.zeros((draws, 1)), (level + maxv[pick])[:, :-1]], axis=1), axis=1)
    dd_log = np.minimum(minv[pick] + level - peak_prev, d_in[pick]).min(axis=1)
    terminal = np.exp(level[:, -1] + t_pick[:, -1])
    cagr = (terminal ** (1.0 / years) - 1.0) * 100.0
    dd = (np.exp(dd_log) - 1.0) * 100.0
    mar = np.divide(cagr, np.abs(dd), out=np.full_like(cagr, np.nan), where=dd < 0)
    return {"mean_r": mean_r, "cagr": cagr, "dd": dd, "mar": mar, "n_clusters": g}


# --------------------------------------------------------- drift term ----
def _sparse_min(a: np.ndarray) -> np.ndarray:
    """Range-minimum table: row k holds min over windows of length 2^k."""
    n = len(a)
    k_max = max(int(n).bit_length(), 1)
    table = np.full((k_max, n), np.inf)
    table[0] = a
    for k in range(1, k_max):
        step = 1 << (k - 1)
        if step >= n:
            break
        table[k, :n - step] = np.minimum(table[k - 1, :n - step], table[k - 1, step:])
    return table


def _range_min(table: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """min(a[lo..hi]) inclusive, vectorised, lo <= hi."""
    length = hi - lo + 1
    k = np.floor(np.log2(length)).astype(int)
    return np.minimum(table[k, lo], table[k, hi - (1 << k) + 1])


def _half_spread_vector(symbol: str, idx: np.ndarray, price: np.ndarray) -> np.ndarray:
    """slippage.half_spread for many sessions at once -- the same ladder,
    the same tick floor, the same cap. Zero when slippage.ENABLED is off,
    mirroring slippage.fill."""
    if not slippage.ENABLED:
        return np.zeros(len(idx))
    adv = slippage.profile(symbol)["adv"][idx]
    floor = np.where(price > 0, (slippage.TICK / 2.0) / np.where(price > 0, price, 1.0), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ladder = (slippage.SPREAD_K / np.sqrt(adv / 1e7)) / 10_000.0
    hs = np.minimum(slippage.MAX_HALF_SPREAD, np.maximum(floor, ladder))
    return np.where(np.isfinite(adv) & (adv > 0), hs, slippage.MAX_HALF_SPREAD)


def _charges_vector(buy: np.ndarray, sell: np.ndarray, intraday: np.ndarray,
                    fee_rate: np.ndarray) -> np.ndarray:
    """backtest.charges over arrays. fee_rate is NaN where the equity
    schedule applies (backtest.FLAT_FEE_RATE still overrides it globally,
    as it does there)."""
    turnover = buy + sell
    flat = np.where(np.isnan(fee_rate),
                    np.nan if backtest.FLAT_FEE_RATE is None else backtest.FLAT_FEE_RATE,
                    fee_rate)
    transaction = backtest.NSE_TXN_RATE * turnover
    sebi = backtest.SEBI_RATE * turnover
    brok_intra = (np.minimum(backtest.INTRADAY_BROKERAGE_RATE * buy, backtest.INTRADAY_BROKERAGE_CAP)
                  + np.minimum(backtest.INTRADAY_BROKERAGE_RATE * sell,
                               backtest.INTRADAY_BROKERAGE_CAP))
    brokerage = np.where(intraday, brok_intra, backtest.BROKERAGE)
    stt = np.where(intraday, backtest.INTRADAY_STT_RATE * sell, backtest.STT_RATE * turnover)
    stamp = np.where(intraday, backtest.INTRADAY_STAMP_RATE * buy, backtest.STAMP_RATE * buy)
    demat = np.where(intraday, 0.0, backtest.DP_PER_SELL)
    gst = backtest.GST_RATE * (brokerage + sebi + transaction)
    equity = brokerage + stt + transaction + sebi + gst + stamp + demat
    return np.where(np.isnan(flat), equity, flat * turnover)


def random_entry_r(trades: list[dict], draws: int = DRIFT_DRAWS, seed: int = SEED) -> np.ndarray:
    """Net R multiples that RANDOM entries matched to `trades` would have earned.

    For each real trade, `draws` random entry sessions on the same stock,
    anywhere in that stock's history after its first MIN_HOLD_BARS sessions
    (so a random entry never has less history behind it than a rule that
    needs 250 bars of EMAs would). Each random trade copies its real twin:
    enter at the random session's close, stop the same FRACTION below entry
    as the twin's stop was below its entry, hold for the same number of
    SESSIONS the twin held (at least one), exit at the close of the last
    session or at the stop if a later session's low reaches it first -- at
    the open if the session gapped through it, else at the stop -- then the
    same half-spread (slippage.half_spread's ladder at that session, if
    slippage.ENABLED) and the same Zerodha charges on the twin's share
    count. R is net profit over the risk taken at quoted prices, exactly as
    r_multiples reads the real trades.

    WHY THE TWIN'S STOP FRACTION AND NOT THE RANDOM SESSION'S OWN LOW. The
    2026-09-07 brief specified "stop = that session's low", the project's
    stop convention. Tried first, and wrong by construction for every rule
    that enters on a weekly or monthly bar: pair|MW's real stop is the
    entry WEEK's low, a median 7.4% below entry, while a daily session's low
    is about 1% below its close. Random entries on that rule's stocks were
    stopped out 83% of the time and their R was measured in a unit seven
    times smaller than the real trades' -- mean -0.15 R before the spread,
    -0.95 after it (a 0.3% round-trip spread is 0.3 R when the risk is 1%).
    A control that risks a different distance is not measuring timing; it
    is measuring stop width. Copying the twin's stop fraction fixes the unit
    of R to the real trade's and leaves TIMING as the only difference.

    WHY IT EXISTS (audit A2, 2026-09-07). Mean R measured against zero
    credits a rule with everything a rising market hands to anyone who buys
    and holds a few sessions with a stop under the entry candle. Random
    entries on pair|MW's own stocks earned +0.46 R against the rule's +0.85:
    more than half of what the old t-stat called edge was the stocks going
    up. The drift term is what the rule must beat, not zero.

    Vectorised per symbol: the first session whose low reaches the stop is
    found with a sparse-table range-minimum plus binary search over the
    hold length, so the cost is O(log hold) per random trade whatever the
    holding period. Stocks with no usable price file are skipped; the
    result may therefore hold fewer than draws x len(trades) entries.
    """
    rng = np.random.default_rng(seed)
    by_symbol: dict = {}
    for t in trades:
        if t.get("risk_taken"):
            by_symbol.setdefault(t["symbol"], []).append(t)
    out = []
    for symbol, group in by_symbol.items():
        s = _series(symbol)
        if s is None or len(s["close"]) < MIN_HOLD_BARS + 2:
            continue
        ts, open_, low, close = s["ts"], s["open"], s["low"], s["close"]
        n = len(ts)
        entry_ts = np.array([_day64(t["entry_ts"]) for t in group])
        exit_ts = np.array([_day64(t["exit_ts"]) for t in group])
        e_idx = np.searchsorted(ts, entry_ts, side="left")
        x_idx = np.searchsorted(ts, exit_ts, side="left")
        hold = np.maximum(x_idx - e_idx, 1)
        shares = np.array([float(t["shares"]) for t in group])
        stop_frac = np.array([(t["entry_price"] - t["stop"]) / t["entry_price"] for t in group])
        intraday = np.array([bool(t.get("same_session", False)) for t in group])
        fee = np.array([np.nan if t.get("fee_rate") is None else float(t["fee_rate"])
                        for t in group])
        # draws per trade, laid out trade-major
        hold = np.repeat(hold, draws)
        shares = np.repeat(shares, draws)
        stop_frac = np.repeat(stop_frac, draws)
        intraday = np.repeat(intraday, draws)
        fee = np.repeat(fee, draws)
        upper = n - hold                                   # entry i must satisfy i + hold < n
        ok = upper > MIN_HOLD_BARS
        if not ok.any():
            continue
        hold, shares, stop_frac, intraday, fee, upper = (
            a[ok] for a in (hold, shares, stop_frac, intraday, fee, upper))
        i = MIN_HOLD_BARS + np.floor(rng.random(len(hold)) * (upper - MIN_HOLD_BARS)).astype(int)
        entry = close[i]
        stop = entry * (1.0 - stop_frac)
        ok = ((entry - stop) > 0) & (entry > 0)
        if not ok.any():
            continue
        i, hold, shares, intraday, fee, entry, stop = (
            a[ok] for a in (i, hold, shares, intraday, fee, entry, stop))
        # first session in (i, i+hold] whose low reaches the stop, by binary search
        table = _sparse_min(low)
        lo = np.ones(len(i), dtype=int)
        hi = hold.copy()
        hit = _range_min(table, i + 1, i + hold) <= stop
        for _ in range(int(hold.max()).bit_length() + 1):
            mid = (lo + hi) // 2
            reached = _range_min(table, i + 1, i + mid) <= stop
            hi = np.where(reached, mid, hi)
            lo = np.where(reached, lo, mid + 1)
        j = np.where(hit, i + lo, i + hold)
        exit_ = np.where(hit, np.where(open_[j] < stop, open_[j], stop), close[j])
        entry_fill = entry * (1.0 + _half_spread_vector(symbol, i, entry))
        exit_fill = exit_ * (1.0 - _half_spread_vector(symbol, j, exit_))
        buy, sell = entry_fill * shares, exit_fill * shares
        net = (sell - buy) - _charges_vector(buy, sell, intraday, fee)
        out.append(net / ((entry - stop) * shares))
    return np.concatenate(out) if out else np.array([], dtype=float)


def expected_best_of(n_trials: int, spread: float) -> float:
    """What the BEST of `n_trials` edgeless rules scores by luck alone.

    The order statistic behind the deflated Sharpe ratio (Bailey & Lopez de
    Prado): the expected maximum of n independent draws from a zero-mean
    normal of the given spread. Reported by multiple_testing_summary as
    context; it is no longer the gate (see there).
    """
    if n_trials < 2 or spread <= 0:
        return 0.0
    nd = NormalDist()
    gamma = 0.5772156649015329                       # Euler-Mascheroni
    a = nd.inv_cdf(1 - 1.0 / n_trials)
    b = nd.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return spread * ((1 - gamma) * a + gamma * b)


# --------------------------------------------- dashboard summary builder ----
#
# Everything above is scripts/validate.py and scripts/bootstrap.py's own
# math. What follows packages it into the shapes scripts/dashboard_data.py
# puts on the page.

def bootstrap_one(trades, risk_pct=RISK_PCT, draws=DRAWS, seed=SEED,
                  drift: bool = True, drift_draws: int = DRIFT_DRAWS):
    """One rule's credibility row, or None below MIN_TRADES.

    THE CREDIBILITY FIGURE IS A T-STATISTIC ON NET R MULTIPLES, not
    compounded CAGR (since 2026-09-04: compounding grows with trade count
    for ANY positive edge, measured at a 0.82 correlation between log(trade
    count) and the old ratio, so it ranked "how often did this fire" nearly
    as much as "is this real"; a t-statistic rewards more trades with a
    narrower standard error, not a bigger number -- Harvey & Liu,
    "Backtesting", JPM 2015, make the same move before correcting for
    multiple tests).

    TWO THINGS THE 2026-09-07 AUDIT (A2) CHANGED IN THAT T:

    1. THE ERROR IS CLUSTER-ROBUST, clusters = entry calendar quarter.
       The old standard error, std / sqrt(n), treats 240,000 e1|daily
       trades as 240,000 independent draws. They are not: a rule that fires
       across 999 stocks in the same week has bought the same market 999
       times, and its winners arrive together. The cluster-robust error
       (Liang-Zeger, G/(G-1) small-sample factor; _cluster_se) sums
       residuals within each quarter first, so a quarter in which every
       trade won counts as one good quarter, not five hundred independent
       wins. `t_iid` (the old number) and `t_cluster` (mean_r / se_r) are
       both reported so the reader can see how much of the old confidence
       was that double-counting.
    2. THE MEAN IS MEASURED AGAINST DRIFT, not zero. `drift_r` is the mean
       R that random entries on the same stocks, with the same stop
       distance, holding length, spread and charges, would have earned
       (random_entry_r). Random entries on pair|MW's stocks earned +0.46 R
       against the rule's +0.85, so measuring against zero credited the
       rule with the market's own rise. The gate statistic is

           t_stat = (mean_r - drift_r) / se_r

       with drift_r taken as 0 when it could not be simulated (None: no
       price files, or under MIN_TRADES random trades). `drift=False` skips
       the simulation for a caller that only wants the clustered t.

    `p05_mean_r` / `p50_mean_r` / `p95_mean_r` / `p_neg` / `cleared_95` come
    from quarter_bootstrap -- the mean R of each draw of resampled calendar
    quarters (a moving block of 20 trades until 2026-09-07; see there for
    why that block was 0.6-15 days long). They ask "would the average trade
    still look this good under a different mix of quarters", which is
    order-invariant, unlike the compounded path.

    obs_cagr / obs_mar / p05 / p50 / p95 / dd05 (compounded, in %) are kept
    for the Detail view's display of the compounded distribution --
    supporting context, captioned there as trade-level and not comparable
    to account CAGR -- but they do not decide anything. See
    multiple_testing_summary for what t_stat is compared against.
    """
    r = r_multiples(trades)
    if len(r) < MIN_TRADES:
        return None
    n = len(r)
    quarters = _quarter_codes(trades)
    mean_r = float(r.mean())
    se_iid = float(r.std(ddof=1) / math.sqrt(n))
    se_r, n_clusters = _cluster_se(r, quarters)
    t_iid = (mean_r / se_iid) if se_iid > 0 else None
    t_cluster = (mean_r / se_r) if se_r > 0 else None

    drift_r = None
    if drift:
        rand = random_entry_r(trades, draws=drift_draws, seed=seed)
        if len(rand) >= MIN_TRADES:
            drift_r = float(rand.mean())
    t_stat = ((mean_r - (drift_r or 0.0)) / se_r) if se_r > 0 else None

    years = span_years(trades)
    fraction = risk_pct / 100.0
    rng = np.random.default_rng(seed)
    obs_cagr, obs_dd, obs_mar = (v[0] for v in paths(r[None, :], fraction, years))
    boot = quarter_bootstrap(r, quarters, years, fraction, draws, rng)
    means = boot["mean_r"]

    return {
        "n": n, "n_clusters": n_clusters,
        "mean_r": round(mean_r, 4),
        "drift_r": round(drift_r, 4) if drift_r is not None else None,
        "se_r": round(se_r, 4),
        "t_iid": round(t_iid, 2) if t_iid is not None else None,
        "t_cluster": round(t_cluster, 2) if t_cluster is not None else None,
        "t_stat": round(t_stat, 2) if t_stat is not None else None,
        "p05_mean_r": round(float(np.percentile(means, 5)), 4),
        "p50_mean_r": round(float(np.percentile(means, 50)), 4),
        "p95_mean_r": round(float(np.percentile(means, 95)), 4),
        "p_neg": round(float((means <= 0).mean()), 3),
        "cleared_95": bool(np.percentile(means, 5) > 0),
        # Compounded distribution -- Detail view context only, see docstring.
        "obs_cagr": round(float(obs_cagr), 1),
        "obs_mar": round(float(obs_mar), 2) if np.isfinite(obs_mar) else None,
        "p05": round(float(np.percentile(boot["cagr"], 5)), 1),
        "p50": round(float(np.percentile(boot["cagr"], 50)), 1),
        "p95": round(float(np.percentile(boot["cagr"], 95)), 1),
        "dd05": round(float(np.percentile(boot["dd"], 5)), 1),
    }


def _breakeven_payload(trades):
    """JSON-safe wrapper around breakeven_cost().

    breakeven_cost() returns float('inf') for "survives 200bp+" and None for
    "already negative" -- opposite verdicts that both need `json.dumps` to
    survive. Python's json module writes float('inf') as the bare token
    `Infinity`, which is not valid JSON and makes the browser's JSON.parse
    throw on the whole payload. `status` carries the verdict; `bp` is a
    plain finite number only in the ordinary case.
    """
    bp = breakeven_cost(trades)
    if bp is None:
        return {"bp": None, "status": "negative"}
    if bp == float("inf"):
        return {"bp": None, "status": "robust"}
    return {"bp": bp, "status": "finite"}


def validation_summary(strat, trades, universe, rng, permutation_rounds=PERMUTATION_ROUNDS,
                       hold_cagr=None):
    """One strategy variant's full-universe validation record, or None below
    MIN_TRADES.

    `trades` is the strategy's own cached "_all" list as the caller holds it
    (scripts.dashboard_data passes the spread-adjusted `slipped` list, with
    slippage.ENABLED and the size cap set as the Realistic-fills grid cell
    has them; scripts.validate passes the cache as built, on paper). One
    position at a time per symbol is enforced via strategies.drop_overlaps
    before anything that would double-count an overlapping signal.

    WHAT EACH PART RUNS ON. Credibility (bootstrap) and top_n deletion read
    the trade list and no account. breakeven and permutation call cagr_of,
    i.e. portfolio.run at CAPITAL (Rs2L), RISK (1%) and priority
    "liquidity" -- one fixed account, whatever scenario is on screen. The
    docstring here used to say none of these ever ran an account; two of
    them do, and the page's tooltips now say at what settings.

    NOT here since 2026-09-07: the `hold_cagr` key. The benchmark depends on
    the universe AND the start year on screen (buy_and_hold), so it lives in
    validation_summary's caller as hold_cagr_by_scenario, not on a
    per-strategy row. The `hold_cagr` PARAMETER is accepted and ignored so
    the 2026-09-07 call site keeps working during the repair; drop it there
    and then here. Not here since 2026-09-05: beats_hold and the
    walk-forward majority, which are account-level and live per scenario
    (walk_forward_grid). fixed_gates here is the full-universe pair
    (distinguishable, breakeven_margin); fixed_checks_by_universe holds the
    per-universe versions the page actually gates on.

    GATES, not weights: a compensatory weighted sum lets one great axis
    paper over a failing one, which the practitioner literature this was
    researched against (Quantopian's contest, WorldQuant BRAIN, Arnott/
    Harvey/Markowitz 2019) is explicit is backwards. See
    scripts.dashboard_data.build_validation() for where these combine with
    the live checks into the final "validated" verdict.
    """
    held = strategies.drop_overlaps(trades)
    if len(held) < MIN_TRADES:
        return None

    breakeven = _breakeven_payload(held)
    permutation = _permutation_summary(strat, universe, rng, permutation_rounds, trades=trades)
    fixed_gates = _fixed_gates(permutation, breakeven)
    fixed_gates["passes"] = all(v for k, v in fixed_gates.items() if k != "passes")

    return {
        "n": len(held),
        "top_n": {str(n): (round(c, 1) if c is not None else None)
                  for n in TOP_N for c in [without_best(held, n)]},
        "breakeven": breakeven,
        "bootstrap": bootstrap_one(held),
        "permutation": permutation,
        "fixed_gates": fixed_gates,
        "monthly": monthly_returns(held),         # consumed by correlation_summary, stripped after
    }


def correlation_summary(monthly_by_key: dict) -> dict:
    """For every key, the single most-correlated other key -- not the full
    matrix. One number per row a reader will act on beats a grid nobody reads."""
    out = {}
    keys = list(monthly_by_key)
    for k in keys:
        best_other, best_r = None, None
        for other in keys:
            if other == k:
                continue
            r = correlate(monthly_by_key[k], monthly_by_key[other])
            if r is not None and (best_r is None or abs(r) > abs(best_r)):
                best_other, best_r = other, r
        out[k] = ({"key": best_other, "r": round(best_r, 2)}
                   if best_other is not None else None)
    return out


def average_pairwise_correlation(monthly_by_key: dict) -> float:
    """Mean correlation of monthly P&L across every pair of strategies.

    Reported next to effective_trials() below. Not the same number as
    correlation_summary(), which keeps only each key's SINGLE closest match
    for display -- this needs the average over the WHOLE matrix.
    """
    keys = list(monthly_by_key)
    vals = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            r = correlate(monthly_by_key[a], monthly_by_key[b])
            if r is not None:
                vals.append(r)
    return float(np.mean(vals)) if vals else 0.0


def _correlation_matrix(monthly_by_key: dict) -> np.ndarray:
    keys = list(monthly_by_key)
    m = len(keys)
    mat = np.eye(m)
    for i, a in enumerate(keys):
        for j in range(i + 1, m):
            r = correlate(monthly_by_key[a], monthly_by_key[keys[j]])
            mat[i, j] = mat[j, i] = r if r is not None else 0.0
    return mat


def effective_trials(monthly_by_key: dict) -> float:
    """How many INDEPENDENT trials the tested variants are worth.

    A first attempt used the simple "design effect" (N_eff = N / (1 + (N-1)
    x rho) on the AVERAGE pairwise correlation) and it broke: on the
    24-variant board of 2026-09-04, average correlation 0.54, that formula
    gives 1.8 effective trials -- below 2, expected_best_of() returns 0.0,
    and "clears the luck hurdle" becomes true for any positive t-stat. The
    design effect assumes one UNIFORM correlation across every pair, which
    is the wrong shape here: EMA variants are extremely correlated with
    EACH OTHER but not uniformly with the Turtle or Holy Grail families, so
    the correlation matrix has real STRUCTURE the average throws away.

    Nyholt's method (Nyholt, "A simple correction for multiple testing...",
    American Journal of Human Genetics, 2004) uses that structure instead:
    eigen-decompose the correlation matrix and discount each eigenvalue
    above 1 by how much it exceeds 1 (an eigenvalue near 1 contributes close
    to one full independent test; a large eigenvalue -- one dominant
    correlated cluster, like six EMA variants moving together -- is
    discounted toward zero). Standard in genetics for exactly this shape of
    problem (many correlated test statistics; there, SNPs in linkage
    disequilibrium). On the 19-variant board it gives ~6.2 effective trials
    -- "about half a dozen real bets," not "barely more than one," which is
    what one strongly correlated family and several weakly related ones
    should produce.
    """
    keys = list(monthly_by_key)
    m = len(keys)
    if m < 2:
        return float(m)
    eig = np.clip(np.linalg.eigvalsh(_correlation_matrix(monthly_by_key)), 0, None)
    m_eff = m - float(np.sum(np.maximum(eig - 1.0, 0.0)))
    return max(1.0, min(m_eff, float(m)))


def luck_hurdle(n_eff: float, alpha: float = ALPHA) -> float:
    """The one-sided t a rule must clear for the chance that ANY of `n_eff`
    independent edgeless rules clears it to be `alpha`: inv_cdf(1 - alpha /
    n_eff). 2.41 at n_eff 6.2, alpha 0.05. See multiple_testing_summary."""
    return NormalDist().inv_cdf(1.0 - alpha / max(float(n_eff), 1.0))


def multiple_testing_summary(bootstrap_rows: list[dict],
                              monthly_by_key: dict | None = None) -> dict | None:
    """The paragraph scripts/bootstrap.py prints last, structured.

    IN T-STATISTIC UNITS, not raw CAGR (see bootstrap_one) -- comparable
    across strategies with very different trade counts. Under a true null a
    t-statistic has spread ~1 by construction, so no empirical spread
    estimate is needed.

    THE HURDLE (changed 2026-09-07, alpha = ALPHA by the owner's decision).

        hurdle = NormalDist().inv_cdf(1 - ALPHA / n_eff)

    the one-sided bar at which the chance that ANY of n_eff independent
    edgeless rules clears it is ALPHA (Bonferroni; Sidak's
    1 - (1 - ALPHA)^(1 / n_eff) differs in the third decimal). At n_eff 6.2
    that is 2.41.

    WHAT IT WAS: expected_best_of(n_eff, 1.0), the EXPECTED MAXIMUM t of
    n_eff edgeless rules (1.32 at 6.2). An expected maximum is the middle
    of the best-of-the-board's distribution, so an entirely edgeless board's
    best rule clears it about half the time -- measured 46% by simulation
    on 2026-09-07. It is a fair description of what luck typically scores,
    which is why it stays in the payload as `expected_best`, and a useless
    gate, which is why it no longer is one.

    `expected_by_chance` = tried x ALPHA, the number of rules an edgeless
    board of this size clears at the UNCORRECTED bar -- context for
    `cleared`, which counts rows whose t_stat clears the corrected hurdle.

    `monthly_by_key` -- see effective_trials(): 19 variants tried is not 19
    INDEPENDENT trials when they are this correlated, and the hurdle is
    computed against the effective count, not the raw one. None (or a dict
    with under 2 usable entries) falls back to n_eff = tried -- the
    conservative direction to be wrong in, since it sets the hurdle too
    HIGH rather than too low.

    `bootstrap_rows` is a list of {"key": label, "t_stat": .., ...} for
    every strategy that cleared MIN_TRADES -- the reason this exists at
    all: counting how many rules "look good" means nothing without knowing
    how many an edgeless menu of the same size would produce by chance.
    """
    rows = [r for r in bootstrap_rows if r.get("t_stat") is not None]
    if not rows:
        return None
    tried = len(rows)
    avg_corr = average_pairwise_correlation(monthly_by_key) if monthly_by_key else 0.0
    n_eff = (effective_trials(monthly_by_key) if monthly_by_key and len(monthly_by_key) >= 2
             else float(tried))
    hurdle = luck_hurdle(n_eff)
    expected_best = expected_best_of(n_eff, 1.0)
    cleared = [r for r in rows if r["t_stat"] > hurdle]
    best = max(rows, key=lambda r: r["t_stat"])
    return {
        "tried": tried, "n_eff": round(n_eff, 1), "avg_correlation": round(avg_corr, 2),
        "alpha": ALPHA, "hurdle": round(hurdle, 2),
        "expected_best": round(expected_best, 2),
        "expected_by_chance": round(tried * ALPHA, 1),
        "cleared": len(cleared), "best_t": best["t_stat"],
        "best_key": best["key"], "clears_hurdle": best["t_stat"] > hurdle,
        "cleared_keys": [r["key"] for r in cleared],
    }

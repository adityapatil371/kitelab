"""Precompute everything the local dashboard can show.

    python -m scripts.dashboard_data

Writes data/dashboard.json for http://localhost:8765/dashboard (served by
python -m scripts.dashboard). The page is a pure viewer -- every control selects
among these precomputed results, nothing is simulated in the browser.

Everything is combinable with everything:
    grid        one-account simulations for every registered strategy
                (kitelab.registry) x universe (all 500 and three liquidity
                buckets, sizes counted from config, never hardcoded)
                x risk x scanning pool x signal priority x start year
    stocks      every stock in the universe: weekly closes + trades + stats
    assets      the six non-equity instruments -- Bitcoin, the two indices and
                three MCX commodities -- run through the SAME registry as the
                equities, so a newly added strategy is tested on them too

Everything here is displayed. Sections that the page did not read were removed
on 2026-09-03: scaleout, scaleout_r, tradestats, timeframes and nifty were
computed on every build and rendered nowhere, the scale-out sweep alone costing
about 64 seconds of a seven-minute build.

Conventions: EMA stacks use the class rule (stops checked at closes only);
Breakout keeps intrabar stops (its buy-stop entry is inherently intrabar).
"""
from __future__ import annotations

import bisect
import json
import os
import re
# Module level, not inside main(): the breadth sweep needs it too, and it used to
# ride in on a local import belonging to the 10-stock Monte Carlo. Removing that
# block on 2026-09-02 took the import with it and the sweep fell over 20 minutes
# into a rebuild.
import random

import numpy as np
import pandas as pd

from kitelab import (backtest, config, dashboard_server, frames,
                     portfolio, signals, sizing, slippage, strategies)
from kitelab.progress import Bar
from kitelab.curves import (bh_stats, calmar, episodes, exposure_pct,
                            sharpe, sortino, underwater_stats)
from kitelab import registry, timeframes

ASSIGNED = config.CLASS_ASSIGNED

OUT = config.CLEAN / "dashboard.json"
# 0.25% and 2% dropped 2026-09-03. Risk is the one lever already known to
# REORDER the table, so more than one level earns its place -- but 0.5% and 1%
# bracket that reordering, and the outer two only stretched the same finding
# across twice the grid.
RISKS = [0.5, 1.0]

# "What if I had started in <year>?" Every account used to begin at the first
# trade in the data -- 2006 for every strategy -- so a headline like Rs2.98 crore
# was twenty years of compounding, not a description of the rule. Starting later
# is a different account: fresh capital, different position sizes, different
# signals affordable.
#
# FIVE years, not nineteen. Every axis here multiplies every other, and the start
# year was the largest multiplier on the board -- 19 of them turned a 9-minute
# rebuild into 65. Adjacent years answer nearly the same question anyway: a 2013
# start and a 2014 start share nineteen twentieths of their trades. These five
# span the range and land on the market's own turning points.
#
# 2024 is the last offered: a 2025 or 2026 start has under two years of trades,
# and that number would be noise wearing a percent sign.
# THE 10-STOCK MONTE CARLO WAS REMOVED on 2026-09-02.
#
# It drew random 10-stock baskets and reported the spread, then promoted the
# median draw to a universe of its own. Three things were wrong with it.
#
# It was the most expensive thing in the build: 27,600 simulated accounts,
# 16m45s of a 28-minute rebuild -- more than the entire main grid -- and nothing
# in web/dashboard.html ever read the result.
#
# The promoted basket could not be neutral. It was chosen as the MEDIAN draw for
# the EMA stack and then used to rank all 23 variants, so it sat at the 48th
# percentile for the strategy that picked it and the 22nd for the Turtle. The
# compare page ranks strategies against each other; on that universe the ranking
# was tilted against the one the project's headline rests on.
#
# And a single draw of ten cannot be read as a measurement anyway: at that size
# the EMA stack ranges from 3.5% to 28.1% a year on luck alone.
#
# The question it was asking -- how many stocks does a rule need? -- is answered
# properly by the breadth sweep below, which draws many baskets at nine sizes and
# reports p10/median/p90 instead of one number.

START_YEARS = [2006, 2012, 2018, 2022, 2024]
START_DEFAULT = 2018

# The execution dimension, and it is TWO independent things, not one.
#
#   TRADING COSTS -- you cross a spread and you move the price you trade against.
#                    A cost. It can only ever make the account worse.
#   THE SIZE CAP  -- one order may not exceed 1% of what the stock trades that
#                    day. NOT a cost: it is a position-sizing rule, and on its own
#                    it BEATS perfect fills by about 1.9 CAGR points on Q/M/W,
#                    because it keeps the account out of positions many times
#                    larger than the stock's entire daily turnover (up to 37x).
#
# These were bundled into a single "Realistic fills" toggle, so realistic scored
# HIGHER than perfect on Q/M/W (19.0 vs 18.1) and in 110 of 1,536 cell-pairs --
# and the page read as though adding costs had improved the account. Now all four
# combinations exist and the slicer can decompose it.
#
# "0" and "1" keep their exact former meanings and their exact former numbers, so
# nothing already published moves; "2" and "3" are new.
FILL_MODES = [("0", "Perfect fills"),
              ("2", "Trading costs only"),
              ("3", "Size cap only"),
              ("1", "Realistic fills (costs + cap)")]
FILL_SPEC = {"0": (False, False), "2": (True, False),
             "3": (False, True), "1": (True, True)}       # key -> (costs, cap)

# ONLY REALISTIC FILLS ARE GRIDDED, from 2026-09-03. The other three modes exist
# to decompose ONE result into where its money went -- they are a diagnostic, not
# a comparison, and the cost waterfall that read them is the only thing that ever
# did. Gridding all four multiplied every cell by 4 to answer a question the
# compare table does not ask. FILL_MODES is left whole so the waterfall can be
# turned back on by widening this one list.
GRID_FILLS = ["1"]
REALISTIC_PARTICIPATION = 0.01     # one order <= 1% of the stock's daily turnover

# THE HOLDOUT RUNS NARROW, AND THAT IS THE POINT.
#
# HISTORICAL, kept for the reasoning rather than the numbers: the grid offered
# 4 risks x 3 capitals x 5 start years when this was written. It is now 2 risks,
# one capital and one gridded fill mode -- see RISKS, CAPITALS and GRID_FILLS,
# each of which carries the measurement that shrank it. The argument below is
# about the holdout, which was retired on 2026-09-03 (config.Config.merged).
# The in-sample side offers 4 risks x 3 capitals x 5 start years because
# browsing it is how the parameters were chosen. Offering the same width on the
# 399 would hand back 1,380 results per fill mode to pick a favourite from --
# which is the tuning the split exists to prevent, done by eye instead of by
# optimiser. A holdout answers ONE question: does the ranking found in-sample
# still hold on stocks nothing has ever seen?
#
# So the settings are committed in advance and there is nothing to choose after
# the fact. Two risks because 0.5% vs 1% is the one lever already known to
# change the ORDER of the table, and both get published; one capital and one
# start year because neither reorders anything. All four fill modes stay: they
# decompose a single result into where its money went, and cannot be used to
# find a better one.
#
# Widening these later does not cost a rebuild. It costs the holdout.
#
# RETIRED 2026-09-03 along with the split itself. The narrow axes were the right
# discipline for a holdout and are kept here as the record of one: the reason
# they existed -- a wide grid over an untouched set is a menu to pick a
# favourite from -- is now handled by not picking a favourite at all. See
# config.Config.merged for why the split went, and scripts.bootstrap for what
# replaced it.
# Small accounts dropped 2026-09-01: below ~Rs1 lakh the size cap decides the
# result more than the rule does, which made those columns a study of the cap.
#
# CUT TO ONE on 2026-09-03. The handover has said since 2026-09-01 that capital
# barely moves the result: position size scales with equity, so more money buys
# bigger positions rather than more of them, and the lever is the risk setting.
# Three capitals cost 3x the grid to print the same ranking three times.
CAPITALS = [200_000]

# HOW MANY STOCKS ARE BEING SCANNED -- a different question from how many are
# owned. Each cell runs the rule over a random sample of this many names from the
# selected universe. The account still holds only what it can fund, which on
# these settings is never more than about ten positions at once.
#
# WHY THIS IS AN AXIS AND NOT A FOOTNOTE. Over the full universe the class EMA
# stack skips 52% of its own signals for want of cash, and its median return
# PEAKS near 30 names then falls away; the Turtle skips 35% and climbs all the
# way to the end of the list. So "which rule is better" has no answer until the
# size of the scanning pool is fixed. A table computed over every name is not
# reporting which rule is better -- it is reporting how well each rule's signal
# frequency happens to fit one account size. This axis puts that choice in front
# of the reader instead of burying it in the default.
POOL_SIZES = [15, 30, 50, 100, 250]        # plus None, meaning the whole universe
POOL_DRAWS = {15: 12, 30: 10, 50: 8, 100: 6, 250: 4}

# WHICH SIGNAL WINS THE CASH -- see portfolio.PRIORITIES for what each means and
# why none of them may look at how a trade turned out. Swept only at the whole
# universe and START_DEFAULT: "does the ordering rule matter" is asked once, and
# crossing it with the pool sweep would multiply the slowest stage in the build.
PRIORITIES = portfolio.PRIORITIES
PRIORITY_DEFAULT = "liquidity"
# Fixed, so a rebuild reproduces the same baskets -- and, far more important, so
# EVERY STRATEGY IS DEALT THE SAME HANDS. Draw per strategy and the compare table
# would be measuring which rule drew the luckier stocks. See _baskets.
# Shared with the breadth sweep -- see BREADTH_SIZES for why one seed matters.
BASKET_SEED = 20260831


def _draw_baskets(pool: list[str], size: int, draws: int) -> list[list[str]]:
    """`draws` random baskets of `size` names, deterministic and SHARED.

    Deterministic twice over. Across strategies, so the compare table measures
    rules rather than which rule drew the better stocks. And across VIEWS: the
    generator is seeded per size, so asking for 12 baskets of 15 yields the first
    12 of the 30 that breadth asks for. Two views, one set of hands.
    """
    if size >= len(pool):
        return [sorted(pool)]
    rng = random.Random(BASKET_SEED + size)
    return [rng.sample(sorted(pool), size) for _ in range(draws)]


# ONE SAMPLER FOR BOTH VIEWS.
#
# Breadth and the compare page's pool axis both ask "what happens when only N
# stocks are scanned", and each used to draw from its own seed -- so Breadth at
# 15 names and Compare at pool 15 answered the same question over DIFFERENT
# stocks, and disagreed for a reason the page never gave. They share
# _draw_baskets and BASKET_SEED now, and POOL_SIZES is a subset of
# BREADTH_SIZES, so wherever the two name the same size they are looking at the
# same baskets: the pool axis simply reads fewer of them, its draw count being a
# PREFIX of breadth's, which the shared generator guarantees.
BREADTH_SIZES = [5, 10, 15, 20, 30, 50, 75, 100, 150, 250]
BREADTH_DRAWS = {5: 30, 10: 30, 15: 30, 20: 30, 30: 20, 50: 20, 75: 10, 100: 10,
                 150: 10, 250: 10}
ASSETS = [("BITCOIN", "30m", 0.0010), ("NIFTY 50", "30m", 0.0005),
          ("NIFTY BANK", "30m", 0.0005), ("GOLD", "1d", 0.0005),
          ("SILVER", "1d", 0.0005), ("CRUDEOIL", "1d", 0.0005)]
STEP = 10
BANDS = registry.BANDS
# W/D/H and ATH Breakout were ruled out in class (2026-09-01) and are no longer
# computed. Their code is untouched -- strategies.ath_breakout_trades and the WDH
# variant still work and still have scripts -- they are simply not on the board.
# "Turtle", not "Darvas": the 20/10 and 55/20 channels are what the Turtles
# traded, which is what the class is studying. The old label was wrong and the
# module docstring has always said so.
STRATEGY_LABELS = registry.families()

# HOW MANY HIGHER TIMEFRAMES DOES THE RULE NEED, AND WHICH?
#
# The class stacks use two (M/W/D, Q/M/W). These use one, and not always the
# adjacent one -- M/D skips the weekly, Q/W skips the monthly. Together with
# "daily only" that is a ladder from zero higher timeframes to two, which is the
# question item 2 is actually asking.
PAIR_TAGS = [k for k, _, _ in timeframes.PAIRS]
PAIR_LABEL = {k: label for k, label, _ in timeframes.PAIRS}

# Two questions asked on 2026-09-02, each answered by ONE variant at the class's
# own 2% band rather than a fresh band sweep -- the question is the filter, not
# the band, and a sweep on each would have tripled the grid to say the same thing.
#
#   e1    the daily 20 EMA with no higher timeframe consulted. The control that
#         says what monthly and weekly are actually worth.
#   eath  the full M/W/D stack, but only buying within 10% of the running
#         all-time high.
SOLO_BAND = registry.SOLO_BAND
ATH_BAND = registry.ATH_BAND

# The Holy Grail's band slot carries its STOP, because "SL will be swing low"
# (rule 6) has two defensible readings and the choice is worth more than any
# band sweep. BOTH are published, restored on 2026-09-03.
#
#   candle  the signal candle's own low. What the class's DI-crossover note
#           spells out ("STOPLOSS: signal candle low") and what its standing
#           rule says; fits the 39 stops marked in the sheet to 0.80%.
#   swing   the last multi-bar pivot low already CONFIRMED at entry. The
#           literal reading of "swing low"; fits those same 39 stops to 5.82%.
#
# Only `swing` was published between 2026-09-02 and 2026-09-03, on the grounds
# that the candle reading was fitted to a classmate's spreadsheet. That was
# wrong twice over. The candle reading has its own written support in the class
# notes, so it is not a curve fit; and holygrail.py has always called it the
# DEFAULT, so the repo was asserting one thing in the module and the opposite on
# the dashboard. Worse, the page labelled the pivot variant "stop at swing low"
# -- the very phrase the module argues means the candle.
#
# Publishing one was also read as a verdict it could not support. Measured over
# the 101 at 2,00,000 and 1% risk, all history:
#
#             stop dist   expectancy   win rate   avg win/loss    MAR
#   candle       4.63%      +0.561R      41.1%    2.95R/-1.11R    0.14
#   swing       13.41%      +0.404R      57.5%    1.31R/-0.82R    0.20
#
# Structurally different trades from identical signals, and BOTH lose at account
# level -- so showing both cannot be cherry-picking a winner. There isn't one.
HG_VARIANTS = registry.HG_VARIANTS
HG_TAGS = [tag for tag, _ in HG_VARIANTS]

# Darvas has no band. It has a pair of windows instead, and they matter at least as
# much, so they ride in the same slot of the key that the band uses for the EMA
# stacks: "dv|20-10|all|1|250000|0". 20/10 is what the class specified and is the
# page default; it is not the best of them.
# The two systems the Turtles actually traded: System 1 (20 in, 10 out) and
# System 2 (55 in, 20 out). The other window pairs tried earlier were ours, not
# theirs, and are dropped.
DARVAS_WINDOWS = registry.DARVAS_WINDOWS

# Every window is computed BOTH ways, because that is the question being asked.
# The class found that one timeframe took every breakout, including the ones
# against the larger trend, and that those were where the losses were. The weekly
# gate is the proposed fix; "1TF" is the control it has to beat. Testing the gate
# at only one window would answer half the question.
DARVAS_GATED = registry.DARVAS_GATED
DARVAS_TAGS = [f"{a}-{b}" + ("" if g else " 1TF")
               for g in DARVAS_GATED for a, b in DARVAS_WINDOWS]
DARVAS_DEFAULT = "20-10"

# The one setting each strategy shows on the per-stock page.
PRIMARY = registry.PRIMARY


def tag(band) -> str:
    """Key fragment for the band slot: a number for the EMA stacks, a window pair
    like "20-10" for Darvas."""
    return f"{band:g}" if isinstance(band, (int, float)) else str(band)


# ------------------------------------------------------------ helpers ----

# Every curve is sampled to about this many points, whatever its length. A fixed
# every-Nth-day step gave a 20-year window 500 points and a 3-year window 80, so
# the long ones cost six times as much to carry and drew no more shape. Also the
# reason a 19-year start axis produced a 319 MB payload.
CURVE_POINTS = 120

# Date lists are shared, not repeated. The same trading calendar was being written
# out once per grid cell -- 117 MB of the 319 MB was duplicate date strings. Each
# distinct list is stored once in payload["calendars"] and referenced by index;
# the page swaps the reference back in on load, so every consumer still reads
# curve.d and the arrays are shared in memory rather than copied 29,184 times.
_CALENDARS: dict[tuple, int] = {}


def curve_payload(curve, cash_curve=None):
    """Equity, drawdown and CASH IN HAND, sampled at the same points.

    cash answers "if a signal fired that day, could the account afford it?" --
    which the equity line alone cannot, because most of that equity is already
    committed to open positions.
    """
    cash_at = dict(cash_curve or ())
    step = max(1, len(curve) // CURVE_POINTS)
    days, eq, dd, cash = [], [], [], []
    peak = float("-inf")
    for i, (day, equity) in enumerate(curve):
        peak = max(peak, equity)
        if i % step and i != len(curve) - 1:
            continue
        days.append(day.strftime("%Y-%m-%d"))
        # float()/int() around round(): equity comes off a numpy array, and
        # round() on a numpy scalar returns a numpy scalar, which is where the
        # 4.3 million values to_native had to convert were coming from.
        eq.append(int(round(float(equity))))
        dd.append(round(100 * (float(equity) - float(peak)) / float(peak), 1))
        if cash_at:
            cash.append(int(round(float(max(cash_at.get(day, 0.0), 0.0)))))
    key = tuple(days)
    cal = _CALENDARS.setdefault(key, len(_CALENDARS))
    out = {"c": cal, "eq": eq, "dd": dd}
    if cash:
        out["cash"] = cash
    return out


def _round(v, places=2):
    """None stays None; numpy scalars become plain floats (see to_native)."""
    return None if v is None else round(float(v), places)


def run_payload(r):
    longest, current = underwater_stats(r["curve"])
    eps = [{"peak": str(e["peak_day"].date()), "trough": str(e["trough_day"].date()),
            "depth": round(float(e["depth_pct"]), 1),
            "recovered": str(e["recovered"].date()) if e["recovered"] is not None else None,
            "years": round(e["days"] / 365.25, 1)} for e in episodes(r["curve"])]
    # cagr is null when the account was wiped out -- the rate is undefined, and the
    # 0.0 this used to emit read as "broke even" on 157 of these 3,072 cells.
    # The page renders null as "Wiped", and a MISSING key as an em dash.
    # float()/int() throughout: these come off numpy arrays, and round() on a
    # numpy scalar stays numpy -- see to_native() for why that matters.
    keep = lambda v: None if v is None else float(v)
    return {"cagr": None if r["cagr_pct"] is None else round(float(r["cagr_pct"]), 1),
            "wiped": bool(r["wiped"]), "final": int(round(float(r["final"]))),
            "ret": int(round(float(r["return_pct"]))),
            "maxdd": round(float(r["max_drawdown_pct"]), 1),
            "uw_long": round(longest / 365.25, 1), "uw_now": round(current / 365.25, 1),
            "taken": len(r["taken"]), "signals": r["signals"],
            # return against pain -- the compare page sorts on mar by default
            "mar": keep(r.get("mar")), "ulcer": keep(r.get("ulcer")),
            # VOLATILITY, added 2026-09-03. Every metric above is a drawdown
            # measure, so a rule that made its return in one lucky quarter and
            # one that ground it out steadily scored identically. None where
            # undefined (a flat account has no Sharpe), never 0.0, which would
            # rank an undefined result alongside a merely mediocre one.
            "sharpe": _round(sharpe(r["curve"])),
            "sortino": _round(sortino(r["curve"])),
            "calmar": _round(calmar(r["cagr_pct"], r["max_drawdown_pct"])),
            "exposure": exposure_pct(r["curve"], r.get("cash_curve")),
            # how much of the account is waiting rather than working
            "skipped_cash": r["skipped_cash"],
            "median_cash": keep(r.get("median_cash_pct")),
            "full_pct": keep(r.get("fully_invested_pct")),
            "episodes": eps,
            "curve": curve_payload(r["curve"], r.get("cash_curve"))}


def cached_signals(name: str, build, over=None) -> list[dict]:
    """Trades for one signal list, rebuilt whenever the cache cannot be trusted.

    A cache used to be accepted because its FILE EXISTED, which meant a universe
    change, a refetch or a strategy fix left it silently wrong. kitelab.signals
    stamps each cache with the universe, the price files and the strategy code it was
    built from, and load() returns None when any of those has moved.
    """
    universe = list(over) if over is not None else config.load().merged
    hit = signals.load(name, universe)
    if hit is not None:
        return hit
    out = []
    bar = Bar(len(universe), name[:14])
    for symbol in universe:
        try:
            out.extend(build(symbol))
        except SystemExit:
            pass
        bar.step()
    bar.close()
    signals.save(name, out, universe)
    return out


def capture_ratio(trades: list[dict]):
    """Points the strategy took / the stock's own move, averaged per stock.

    The class sheet's metric. A stock that ENDED LOWER than it started has a
    negative denominator, which makes the ratio meaningless rather than merely
    small, so those stocks are skipped instead of averaged in.
    """
    by_symbol: dict[str, list] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    caps = []
    for lst in by_symbol.values():
        lst = sorted(lst, key=lambda t: t["entry_ts"])
        move = lst[-1]["exit_price"] - lst[0]["entry_price"]
        if move <= 0:
            continue
        caps.append(sum(t["exit_price"] - t["entry_price"] for t in lst) / move)
    # MEDIAN, not mean: a stock that moved +5 points while the strategy lost 500
    # gives a ratio of -100, and a couple of those drag a mean into nonsense.
    if not caps:
        return None
    caps.sort()
    middle = len(caps) // 2
    value = caps[middle] if len(caps) % 2 else (caps[middle - 1] + caps[middle]) / 2
    return round(value, 4)


def positions(trades: list[dict]) -> list[dict]:
    """One position at a time per symbol -- what a TRADE-LEVEL view should count.

    The ATH breakout fires again while it is already long, so 1,146 of its 2,031
    signals overlap an open trade in the same stock and the same rally was being
    counted several times in every win rate, profit factor, expectancy and total.
    The one-account grid was always honest about this (it holds one position per
    stock and reports the rest as skipped_busy); only the trade-level views were not.

    A no-op for every other strategy: EMA, Q/M/W, W/D/H and Darvas all walk forward
    from each exit, so they cannot overlap. Verified 2026-08-31 -- 0 dropped from
    9,224 / 3,011 / 18,296 / 6,908 / 3,197.

    NOT applied to the grid. The account decides for itself what it can hold, and
    de-duplicating its input would change which signals it is ever offered.
    """
    return strategies.drop_overlaps(trades)


def trade_stats(trades: list[dict]) -> dict:
    nets = [t["net_profit"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    running = peak = 0.0
    worst_run = 0.0
    for t in sorted(trades, key=lambda t: t["exit_ts"]):
        running += t["net_profit"]
        peak = max(peak, running)
        worst_run = min(worst_run, running - peak)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {"trades": len(trades), "wins": len(wins), "losses": len(losses),
            "total_win": round(sum(wins)), "total_loss": round(sum(losses)),
            "avg_win": round(avg_win), "avg_loss": round(avg_loss),
            "expectancy": round(sum(nets) / len(nets)) if nets else 0,
            "rr": round(avg_win / abs(avg_loss), 2) if avg_loss else None,
            "capture": capture_ratio(trades),
            "win_rate": round(len(wins) / len(nets), 3) if nets else 0,
            "banked": sum(1 for t in trades if "banked" in t["exit_reason"]),
            "gross": round(sum(t["gross_profit"] for t in trades)),
            "charges": round(sum(t["charges"] for t in trades)),
            "net": round(sum(nets)),
            "avg": round(sum(nets) / len(nets)) if nets else 0,
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "best": round(max(nets)) if nets else 0,
            "worst": round(min(nets)) if nets else 0,
            "worst_run": round(worst_run)}


def to_native(obj, path="payload", found=None):
    """Convert numpy scalars to Python ones, and say where they were.

    numpy values leak into the payload easily -- any comparison of two numpy
    floats yields numpy.bool_, and numpy 2 renders its class name as plain
    "bool", so json.dumps fails with the baffling "Object of type bool is not
    JSON serializable" after the whole 20-minute run has completed. Converting
    on the way out is cheap; doing it silently would hide the leak, so the paths
    are reported once.
    """
    if found is None:
        found = []
    if isinstance(obj, dict):
        return {k: to_native(v, f"{path}.{k}", found) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_native(v, f"{path}[{i}]", found) for i, v in enumerate(obj)]
    if isinstance(obj, np.generic):
        found.append((path, type(obj).__name__))
        return obj.item()
    return obj


def slim_trades(trades: list[dict]) -> list[dict]:
    return [{"e": pd.Timestamp(t["entry_ts"]).strftime("%Y-%m-%d"),
             "x": pd.Timestamp(t["exit_ts"]).strftime("%Y-%m-%d"),
             "ep": round(t["entry_price"], 2), "xp": round(t["exit_price"], 2),
             "st": round(t["stop"], 2), "sh": round(t["shares"], 4),
             "net": round(t["net_profit"]), "why": t["exit_reason"]}
            for t in sorted(trades, key=lambda t: t["entry_ts"], reverse=True)]


def close_series(daily: pd.DataFrame) -> dict:
    d, c = [], []
    for i, rec in enumerate(daily.itertuples(index=False)):
        if i % STEP and i != len(daily) - 1:
            continue
        d.append(rec.ts.strftime("%Y-%m-%d"))
        c.append(round(float(rec.close), 2))
    return {"d": d, "c": c}


# --------------------------------------------------------------- main ----

def signal_lists(over=None, suffix="all", label="") -> dict:
    """Every registered strategy, over one universe.

    A loop over kitelab.registry rather than a hand-written list of 24 calls.
    Adding a strategy is appending a Strategy there; it then appears here, in
    the compare grid, in the breadth sweep and in scripts.bootstrap, measured on
    the same stocks and the same account as everything already on the board.

    `suffix` only names the cache. The stamp inside it records the universe, so
    a cache built over one universe can never be served for a question about
    another even if the names were to collide.
    """
    out = {}
    for strat in registry.REGISTRY:
        out[(strat.key, strat.variant)] = cached_signals(
            f"{strat.cache}_{suffix}", strat.build, over)
    print(f"    {label}{len(out)} strategies ready", flush=True)
    return out


def main() -> None:
    cfg = config.load()

    print("  signal lists (cached where possible):", flush=True)
    base = signal_lists(cfg.merged)

    # Counted, not typed. Seven stocks were removed from the universe on
    # 2026-08-31 (kitelab.config.EXCLUDED) and every label that said "199" would
    # otherwise have quietly gone on saying it.
    # The 101/399 in-sample/holdout split was dropped on 2026-09-03 and every
    # universe below is cut from the merged 500 (config.Config.merged, which
    # carries the argument). cfg.in_sample / cfg.out_of_sample still exist, so
    # scripts.universe_bias can still ask what the old split measured.
    # Liquidity buckets, so a rule can be judged on the stocks it would actually
    # be run on. Median daily traded value over each stock's whole history --
    # the same measure and the same cut points scripts.screen_universe uses to
    # admit a stock, so "mid" here means what it means there.
    def buckets(symbols, asof=None):
        """(small, mid, large) by median daily traded value AS OF `asof`.

        THE asof IS THE POINT. Until 2026-09-03 this took the median over each
        stock's WHOLE history, so a company that only became liquid in 2024 was
        filed as a large cap for a backtest starting in 2006 -- the universe
        definition reading the future while every indicator in the project was
        scrupulous about not doing so. The same sin, one level up, and the
        rigour elsewhere made it easy to miss.

        Bars from `asof` onward are excluded, so membership uses only what the
        tape had shown by then. Cut points are scripts.screen_universe's, so
        "mid" means there what it means here.

        WHAT IS STILL NOT FIXED, and it needs a refetch rather than a patch: the
        ADMISSION gate in screen_universe also measures over full history, so
        the 500 are stocks that turned out to be liquid. Membership is
        point-in-time now; eligibility is not.
        """
        cut = pd.Timestamp(f"{asof}-01-01") if asof else None
        turn = {}
        for sym in symbols:
            try:
                d = frames.daily(sym)
                if cut is not None:
                    d = d[d["ts"] < cut]
                v = (d["close"] * d["volume"]).to_numpy(float)
                v = v[v > 0]
                if len(v):
                    turn[sym] = float(np.median(v))
            except SystemExit:
                pass
        return ({s for s, t in turn.items() if t < 5e7},
                {s for s, t in turn.items() if 5e7 <= t < 25e7},
                {s for s, t in turn.items() if t >= 25e7})

    # As of START_DEFAULT, the reference year every headline is quoted at. One
    # classification rather than one per start year: the universe key is part of
    # every grid key, so per-year buckets would multiply the grid and make two
    # cells labelled "large" mean different sets of stocks.
    small, mid, large = buckets(cfg.merged, asof=START_DEFAULT)
    print(f"  liquidity: {len(small)} small/micro, {len(mid)} mid, "
          f"{len(large)} large", flush=True)
    # The three liquidity buckets, and ALL of them. "large" was missing until
    # 2026-09-02: 46 small plus 18 mid is 64 of 101, so the 37 most liquid names
    # -- the ones easiest to actually trade -- were the only group with no view
    # of their own. The cut points are scripts.screen_universe's, so "mid" here
    # means what it means there.
    universes = {"all": (f"All {len(cfg.merged)} stocks", None),
                 "large": (f"{len(large)} large caps", large),
                 "mid": (f"{len(mid)} mid caps", mid),
                 "small": (f"{len(small)} small caps", small)}


    # The spread is charged onto the cached trades rather than re-simulated:
    # nothing in the simulation depends on the fill price, so this is exact and
    # it keeps the whole toggle affordable (see slippage.apply_spread).
    slippage.ENABLED = True
    slippage.reset()
    slipped = {key: [slippage.apply_spread(t) for t in trades]
               for key, trades in base.items()}
    slippage.ENABLED = False
    print("  spread applied to the cached signal lists", flush=True)
    # The spread is a COST, so it rides with the costs half of the key.
    sets = {fkey: (slipped if costs else base) for fkey, (costs, _) in FILL_SPEC.items()}

    def execution(costs: bool, cap: bool):
        """Impact and the size limit live in portfolio.run, so they are globals."""
        slippage.ENABLED = costs
        slippage.MAX_PARTICIPATION = REALISTIC_PARTICIPATION if cap else None
        slippage.reset()

    grid = {}

    # THE SAME HANDS FOR EVERY STRATEGY.
    #
    # Baskets are drawn once per (universe, size) and reused by every rule, so a
    # difference in the compare table is a difference between RULES. Draw them
    # inside the strategy loop instead and the table silently becomes a report of
    # which rule was dealt the better stocks -- the deck would stop cancelling,
    # which is the one property that kept these comparisons worth having when the
    # universe itself turned out to be biased.
    basket_cache: dict = {}

    def _baskets(ukey, members, size):
        hit = basket_cache.get((ukey, size))
        if hit is None:
            hit = _draw_baskets(list(members), size, POOL_DRAWS[size])
            basket_cache[(ukey, size)] = hit
        return hit

    def _pooled(window, ukey, members, size, capital, risk, priority):
        """One cell at a finite scanning pool: the MIDDLE draw, not the best.

        The payload comes from a single real run rather than from per-metric
        medians, so its CAGR, drawdown and MAR describe one account that actually
        existed. A median CAGR paired with a median drawdown from a different
        draw is a portfolio nobody ran.

        `pool_worst` rides along because it answers "but I do not know WHICH 15
        stocks". The median says what a typical basket did; the worst says what
        the unlucky one did, and on a small account that is the number deciding
        whether a rule is tradeable at all.
        """
        runs = []
        for basket in _baskets(ukey, members, size):
            keep = set(basket)
            sub_trades = [t for t in window if t["symbol"] in keep]
            if sub_trades:
                runs.append(portfolio.run(sub_trades, capital, risk, priority))
        if not runs:
            return None
        # A WIPED BASKET IS THE WORST OUTCOME, NOT A MISSING ONE. portfolio.run
        # reports cagr_pct as None when the account is destroyed, because the
        # rate is undefined -- and the first version of this filtered those out,
        # so the median was taken over the survivors and `pool_worst` named the
        # worst basket THAT LIVED. Both flattered, and pool_worst worst of all:
        # it is the number a reader leans on precisely because they cannot
        # choose their stocks. Wiped draws now sort below every loss, exactly as
        # the breadth sweep has always done, and their count is published.
        wiped = [r for r in runs if r.get("cagr_pct") is None]
        rated = sorted((r for r in runs if r.get("cagr_pct") is not None),
                       key=lambda r: r["cagr_pct"])
        ordered = wiped + rated
        out = run_payload(ordered[len(ordered) // 2])
        out["pool_draws"] = len(ordered)
        out["pool_wiped"] = len(wiped)
        # None means "the worst basket was destroyed", which no number says.
        out["pool_worst"] = None if wiped else round(rated[0]["cagr_pct"], 2)
        out["pool_best"] = round(rated[-1]["cagr_pct"], 2) if rated else None
        return out

    def fill_grid(bar, source, unis, risks, capitals, years, pools):
        """One pass of the grid, over every universe at every setting.

        The pool axis is swept only at START_DEFAULT. Pool size crossed with
        start year is a second-order interaction -- how a rule's cash starvation
        differed between 2012 and 2022 -- and gridding it would multiply the
        build by the number of start years to answer a question no view asks.
        """
        for fkey in GRID_FILLS:
            execution(*FILL_SPEC[fkey])
            for (skey, band), signals in source[fkey].items():
                for ukey, (ulabel, members) in unis.items():
                    pool_members = members if members is not None else cfg.merged
                    subset = (signals if members is None
                              else [t for t in signals if t["symbol"] in members])
                    # Sorted once; each start year is a suffix of the one before, so
                    # the slice is a bisect rather than a fresh filter per year.
                    subset = sorted(subset, key=lambda t: t["entry_ts"])
                    stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
                    for year in years:
                        cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
                        window = subset[cut:]
                        sizes = pools if year == START_DEFAULT else [None]
                        for size in sizes:
                            # The priority sweep is asked once, over the whole
                            # universe at the default year. Everywhere else runs
                            # the standing rule, so those cells stay comparable
                            # with every number published before today.
                            prios = (PRIORITIES
                                     if size is None and year == START_DEFAULT
                                     else [PRIORITY_DEFAULT])
                            for prio in prios:
                                for risk in risks:
                                    for capital in capitals:
                                        key = (f"{skey}|{tag(band)}|{ukey}|{risk:g}"
                                               f"|{capital}|{fkey}|{year}"
                                               f"|{size if size else 'all'}|{prio}")
                                        if not window:
                                            grid[key] = None
                                            bar.step()
                                            continue
                                        if size is None:
                                            grid[key] = run_payload(portfolio.run(
                                                window, capital, risk / 100, prio))
                                        else:
                                            grid[key] = _pooled(
                                                window, ukey, pool_members, size,
                                                capital, risk / 100, prio)
                                        bar.step()

    # Cells per (variant, universe, risk, capital): one per start year over the
    # whole universe, plus the pool sweep at START_DEFAULT.
    pool_axis = POOL_SIZES + [None]
    per_combo = len(START_YEARS) + len(POOL_SIZES) + len(PRIORITIES) - 1
    total = (len(GRID_FILLS) * len(sets[GRID_FILLS[0]]) * len(universes)
             * len(RISKS) * len(CAPITALS) * per_combo)
    bar = Bar(total, "grid")
    fill_grid(bar, sets, universes, RISKS, CAPITALS, START_YEARS, pool_axis)
    bar.close()

    execution(False, False)

    # WHERE THE RETURN WENT.
    #
    # The four fill modes already hold the answer and nobody had subtracted
    # them. Perfect fills is the rule on paper; costs-only charges brokerage,
    # STT and the rest; cap-only limits an order to a share of the stock's own
    # traded value; realistic does both. The differences say which lever costs
    # what, in CAGR points, instead of leaving "the numbers got worse" as a
    # feeling.
    #
    # Read as a waterfall: perfect -> costs -> cap -> both. The two middle steps
    # do not add up to the last one, and that is real, not an error: a size cap
    # changes WHICH signals are affordable, so it interacts with costs rather
    # than stacking on them. The gap is reported as `interaction`.
    waterfall = {}
    # The waterfall decomposes ONE result across all four fill modes, and only
    # mode "1" is gridded now (see GRID_FILLS). Rather than emit a table of
    # nulls that the page would render as zeroes -- a cost of nothing, which is
    # the most flattering possible lie -- it is skipped outright and the page
    # falls back to hiding the block. Widen GRID_FILLS to bring it back.
    if len(GRID_FILLS) < len(FILL_MODES):
        print("  cost waterfall: skipped (only realistic fills are gridded)",
              flush=True)
    else:
        for (skey, band) in sets["0"]:
            for ukey in universes:
                for risk in RISKS:
                    for capital in CAPITALS:
                        def cell(fkey):
                            k = (f"{skey}|{tag(band)}|{ukey}|{risk:g}"
                                 f"|{capital}|{fkey}|{START_DEFAULT}"
                                 f"|all|{PRIORITY_DEFAULT}")
                            got = grid.get(k)
                            return got["cagr"] if got else None
                        perfect, costs, cap, both = (cell("0"), cell("2"),
                                                     cell("3"), cell("1"))
                        if perfect is None or both is None:
                            continue
                        step_costs = None if costs is None else round(costs - perfect, 2)
                        step_cap = None if cap is None else round(cap - perfect, 2)
                        inter = (None if None in (step_costs, step_cap)
                                 else round((both - perfect) - step_costs - step_cap, 2))
                        waterfall[f"{skey}|{tag(band)}|{ukey}|{risk:g}|{capital}"] = {
                            "perfect": perfect, "costs": step_costs,
                            "cap": step_cap, "interaction": inter, "net": both}
        print(f"  cost waterfall: {len(waterfall):,} cells", flush=True)

    print("  breadth sweep:", flush=True)
    breadth = {}
    for skey in STRATEGY_LABELS:
        signals = base.get((skey, PRIMARY[skey]))
        if not signals:
            continue
        by_sym = {}
        for t in signals:
            by_sym.setdefault(t["symbol"], []).append(t)
        pool = sorted(cfg.merged)
        sizes = [n for n in BREADTH_SIZES if n < len(pool)] + [len(pool)]
        rows = []
        for size in sizes:
            draws = _draw_baskets(pool, size, BREADTH_DRAWS.get(size, 10))
            cagrs, dds, held, starved = [], [], [], []
            for basket in draws:
                subset = [t for sym in basket for t in by_sym.get(sym, [])]
                if not subset:
                    continue
                # 2,00,000: the middle of CAPITALS. It used to be 2,50,000,
                # which stopped being one of the offered account sizes when the
                # small accounts were dropped -- so this page was reporting a
                # reference account you could not select anywhere else.
                r = portfolio.run(subset, 200_000, 0.01)
                cagrs.append(r["cagr_pct"])
                dds.append(r["max_drawdown_pct"])
                held.append(r["max_concurrent"])
                starved.append(100 * r["skipped_cash"] / max(r["signals"], 1))
            if not cagrs:
                continue
            # Wiped baskets (no CAGR) sort to the BOTTOM, not to 0.0. A quantile that
            # lands on one is reported as null, and the count is published so the page
            # can say how many of the draws were destroyed rather than hiding them
            # among the flat results.
            ordered = sorted(cagrs, key=lambda c: (c is not None, c))
            n_wiped = sum(1 for c in cagrs if c is None)
            def pick(q, ordered=ordered):
                v = ordered[min(len(ordered) - 1, int(q * len(ordered)))]
                return None if v is None else round(v, 1)
            rows.append({"size": size, "draws": len(ordered), "wiped": n_wiped,
                         "median": pick(0.5), "p10": pick(0.1), "p90": pick(0.9),
                         "worst": None if ordered[0] is None else round(ordered[0], 1),
                         "best": None if ordered[-1] is None else round(ordered[-1], 1),
                         "dd": round(sorted(dds)[len(dds) // 2], 1),
                         "held": round(sorted(held)[len(held) // 2], 1),
                         "starved": round(sorted(starved)[len(starved) // 2], 1)})
        breadth[skey] = rows
        print(f"    {skey} done", flush=True)

    print("  per-stock detail:", flush=True)
    by_symbol = {k: {} for k in STRATEGY_LABELS}
    for (skey, band), signals in base.items():
        if band != PRIMARY[skey]:
            continue
        for t in signals:
            by_symbol[skey].setdefault(t["symbol"], []).append(t)
    stocks = {}
    stock_bar = Bar(len(cfg.merged), "per-stock")
    for index, symbol in enumerate(sorted(cfg.merged), 1):
        stock_bar.step()
        try:
            daily = frames.daily(symbol)
        except SystemExit:
            continue
        entry = {"closes": close_series(daily), "assigned": symbol in set(ASSIGNED)}
        for skey in STRATEGY_LABELS:
            tr = by_symbol[skey].get(symbol, [])
            tr = positions(tr)
            entry[skey] = {"stats": trade_stats(tr), "trades": slim_trades(tr)}
        stocks[symbol] = entry
    stock_bar.close()

    print("  assets detail:", flush=True)
    assets = {}
    # The middle field was the breakout entry timeframe; ATH Breakout is no
    # longer computed, so it is unused. (It used to say scripts/scaleout_test.py
    # still read it -- that file does not exist and has not for some time.)
    for symbol, _entry_tf, fee in ASSETS:
        backtest.FLAT_FEE_RATE = fee
        sizing.FRACTIONAL = True
        try:
            daily = frames.daily(symbol)
            bh = bh_stats(daily)
            entry = {"closes": close_series(daily),
                     "bh": {"cagr": round(bh["cagr"], 1), "maxdd": round(bh["maxdd"], 1),
                            "uw": round(bh["longest_uw"] / 365.25, 1),
                            "years": round(bh["years"], 1)}}
            # FROM THE REGISTRY, not a hand-written dict. This list used to name
            # its seven families literally, which meant a newly added strategy
            # was compared against the others on 500 NSE stocks and then quietly
            # never tested on Bitcoin, gold or the indices at all -- the same
            # class of omission as the hand-kept cache-stamp list, and just as
            # silent. Each family runs at its primary variant, matching the
            # breadth sweep, so these results sit beside those.
            #
            # ONE RESULT MOVES, DELIBERATELY. The old dict called
            # holygrail.simulate(symbol) bare, and that function's default stop
            # is "signal_low" -- the CANDLE reading. Every other view uses
            # PRIMARY["hg"], which is "swing". So this page was quietly showing a
            # different Holy Grail from the one beside it. It now shows the same
            # one. Neither reading is the winner (see HANDOVER section 5); being
            # consistent about which is on screen is the point.
            builders = {}
            for fam in registry.families():
                strat = registry.find(fam, registry.primary(fam))
                if strat is not None:
                    builders[fam] = strat.build
            # (per-stock pages show the gated 20/10; the 1TF control lives in the
            #  grid, where it can be compared across every setting at once)
            for skey, build in builders.items():
                try:
                    trades = build(symbol)
                except (SystemExit, FileNotFoundError):
                    entry[skey] = None
                    continue
                r = portfolio.run(trades, 100_000, 0.01) if trades else None
                trades = positions(trades)
                entry[skey] = {"stats": trade_stats(trades), "trades": slim_trades(trades),
                               "account": run_payload(r) if r else None}
            assets[symbol] = entry
        finally:
            backtest.FLAT_FEE_RATE = None
            sizing.FRACTIONAL = False
        print(f"    {symbol} done", flush=True)

    payload = {
        # IST, and labelled: the build machine may be on any clock.
        "built": config.now_local().strftime("%Y-%m-%d %H:%M IST"),
        "strategies": STRATEGY_LABELS,
        "universes": {k: v[0] for k, v in universes.items()},
        "universe_size": len(cfg.merged),
        "risks": RISKS, "capitals": CAPITALS,
        # The scanning pool: how many names the rule is run over. null is the
        # whole universe. Only computed at start_default -- the page must clamp
        # its year selector when a finite pool is chosen, because every other
        # combination is a missing cell, not a zero.
        "pools": POOL_SIZES + [None],
        "pool_year": START_DEFAULT,
        # Which signal wins the cash when the account cannot fund them all. Only
        # computed at the whole universe and pool_year, so the page clamps both
        # when a non-default priority is chosen.
        "priorities": PRIORITIES, "priority_default": PRIORITY_DEFAULT,
        "start_years": START_YEARS, "start_default": START_DEFAULT, "bands": BANDS,
        "hg_tags": HG_TAGS,
        "pair_tags": PAIR_TAGS, "pair_labels": PAIR_LABEL,
        # index -> the date list every curve carrying that index shares
        "calendars": [list(k) for k, _ in sorted(_CALENDARS.items(), key=lambda kv: kv[1])],
        "darvas_windows": DARVAS_TAGS,
        "breadth": breadth,
        "fills": FILL_MODES,
        "assigned": list(ASSIGNED),
        "grid": grid, "waterfall": waterfall,
        "stocks": stocks, "assets": assets,
    }
    # THE CURVES DO NOT TRAVEL WITH THE NUMBERS.
    #
    # 83,904 grid cells carried a curve and a drawdown-episode list: 226 MB and
    # 43 MB of a 303 MB payload, 89% of it, so that ONE of them could be drawn
    # when a row is opened. The comparison table -- the page people actually
    # land on -- needs none of it.
    #
    # They move to a JSONL sidecar with a byte-offset index. The server seeks to
    # the line it wants, so opening a detail costs one disk read rather than the
    # browser holding every curve it might one day show. The index is small
    # enough to sit in the payload.
    detail_path = OUT.with_name("dashboard.curves.jsonl")
    index: dict = {}
    offset = 0
    with open(detail_path, "w") as fh:
        for key, cell in grid.items():
            if not cell:
                continue
            blob = json.dumps({"curve": cell.pop("curve", None),
                               "episodes": cell.pop("episodes", None)})
            line = blob + "\n"
            index[key] = [offset, len(blob)]
            fh.write(line)
            offset += len(line.encode())
    payload["curve_index"] = index
    print(f"  curves -> {detail_path.name} "
          f"({detail_path.stat().st_size/1e6:.0f} MB, {len(index):,} entries)")

    leaked: list = []
    payload = to_native(payload, "payload", leaked)
    if leaked:
        kinds = {}
        for where, kind in leaked:
            # collapse [0], [1], ... so 30,000 grid cells report as one path
            key = (re.sub(r"\[\d+\]", "[]", where), kind)
            kinds[key] = kinds.get(key, 0) + 1
        print(f"  converted {len(leaked):,} numpy values on the way out:")
        for (where, kind), n in sorted(kinds.items(), key=lambda kv: -kv[1])[:6]:
            print(f"    {where}  ({kind}) x{n:,}")
    # Written aside and MOVED into place, never straight over the top. This file
    # is ~15 MB and the run that produces it is half an hour; an interrupt or a
    # full disk partway through a direct write would leave truncated JSON that
    # the page cannot parse, and the only way back is to run the half hour again.
    # os.replace is atomic within a filesystem, so a reader sees the old file or
    # the new one and never a half of either.
    spool = OUT.with_suffix(".json.partial")
    spool.write_text(json.dumps(payload))
    os.replace(spool, OUT)
    print(f"\n  written: {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")

    # Record WHICH UNIVERSE these numbers describe, in a small file beside the big
    # one. Without it the page can only say "built <date>", which is how a
    # dashboard built over 192 stocks went on being served after 91 of them were
    # excluded. dashboard_server.status() reads this and warns on the page.
    dashboard_server.write_stamp(cfg.merged, payload["built"])
    print(f"  stamped: {dashboard_server.STAMP_PATH.name} "
          f"({len(cfg.merged)} symbols)")


if __name__ == "__main__":
    main()

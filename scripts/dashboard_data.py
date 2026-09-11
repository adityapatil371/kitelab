"""Precompute everything the local dashboard can show.

    python -m scripts.dashboard_data
    python -m scripts.dashboard_data --stocks-only   # skip Bitcoin/GOLD

Writes dashboard.json into CLEAN for ./run_dashboard.sh. Nothing is simulated
in the browser: every control selects among these precomputed results. The
page does derive a few things from precomputed parts (vs Hold, the Validated
verdict, the walk-forward fraction), so it is a viewer, not a "pure" one.

Everything is combinable with everything:
    grid        one-account simulations for every registered strategy
                (kitelab.registry) x universe (the whole universe, three
                liquidity buckets and the post-cut listings, sizes counted
                from config, never hardcoded) x risk x capital x signal
                priority x start year
    assets      the non-equity instruments in ASSETS run through the SAME
                registry as the equities, so a newly added strategy is tested
                on them too. ASSETS is EMPTY since 2026-09-11 (BITCOIN and GOLD
                removed), so this section and `single_name` serialise empty --
                the same state a --stocks-only build produces. The machinery is
                left in place; re-adding a series is one line at ASSETS.

Everything here is displayed. Sections the page did not read were removed on
2026-09-03: scaleout, scaleout_r, tradestats, timeframes and nifty were computed
every build and rendered nowhere. `stocks` went the same day for a different
reason -- it WAS displayed, but it was 31.3 MB of a 32.8 MB payload, 96% of the
file, to answer a question about one stock at a time that the comparison this
workbench exists for does not ask.

Conventions: EMA stacks use the class rule (stops checked at closes only);
Breakout keeps intrabar stops (its buy-stop entry is inherently intrabar).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import pathlib
import pickle
import re

import numpy as np
import pandas as pd

from kitelab import (backtest, config, contracts, dashboard_server, frames,
                     portfolio, signals, sizing, slippage, strategies, validation)
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
# The question it was asking -- how many stocks does a rule need? -- was then
# handed to a breadth sweep, removed in turn on 2026-09-03. What both found (a
# small account cannot fund what a wide scan offers) now sits on every grid row
# as skipped_cash / skipped_tiny_* / exposure.

START_YEARS = [2006, 2012, 2018, 2022, 2024]
START_DEFAULT = 2018


def gridded_years(stamps, years=None):
    """The start years that are DISTINCT tests for one (strategy, universe).

    A start year earlier than the rule's FIRST trade in that universe slices
    nothing off: the window it produces is the whole sorted list, which is
    exactly the window the next start year up produces. Same trades, same
    account, same number -- one backtest wearing an earlier label. So among the
    years that cut nothing, only the LATEST is a real scenario, and it is the
    latest rather than the earliest because that is the year you could actually
    have started; an earlier label is a claim the data cannot support.

    MEASURED 2026-09-11 on the 13-strategy board, which is what put this here.
    `recent` holds 187 names with no positive-turnover bar before 2018, so its
    2006, 2012 and 2018 cells were byte-identical -- 156 of 156 on both pairs,
    equal to within 1e-9, while no other bucket had more than 1 of 156 agreeing
    by chance. That is 216 of 2,700 cells on the 9-strategy board, 8%.

    The waste was never the point; the multiple-testing count was. The
    Benjamini-Hochberg gate divides by how many chances you gave yourself to be
    lucky, and three copies of one test are one chance, not three. It does not
    move today's verdict (nothing clears the bar), but a `recent` cell that ever
    did clear it would have entered the FDR ranking three times over.

    Detected from the trade stamps rather than from a list of bucket names or a
    hardcoded 2018, so it stays true if the buckets are recut or a start year is
    added -- the same reasoning as the `single`-universe priority collapse in
    fill_grid. Returns a list; callers skip the years not in it.
    """
    years = list(START_YEARS if years is None else years)
    cuts = [bisect.bisect_left(stamps, pd.Timestamp(f"{y}-01-01")) for y in years]
    # `cuts` is non-decreasing in year, so the years that cut nothing form a
    # prefix of the list; keep from the last of them onward. No year cutting
    # nothing (the rule traded before the earliest start year) keeps them all.
    dead = [i for i, c in enumerate(cuts) if c == 0]
    return years[dead[-1]:] if dead else years

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
#
# RS1 CRORE ADDED 2026-09-05, on request (the class). That finding was measured
# across roughly Rs1L-10L, where the account only ever holds ~10 positions and
# nothing binds on liquidity; a full order of magnitude beyond the old range is
# a genuinely different regime -- MAX_PARTICIPATION (kitelab.slippage) caps a
# single order at 1% of a stock's own daily turnover, which a Rs1 crore account
# can hit on names the Rs2L account never gets close to. Two capitals, not
# three: doubles the grid rather than tripling it, and the open question is
# "does the size cap start to bite", which two points answer as well as three.
CAPITALS = [200_000, 10_000_000]

# WHICH SIGNAL WINS THE CASH -- see portfolio.PRIORITIES for what each means and
# why none of them may look at how a trade turned out. Swept at every universe
# and every start year (see fill_grid), so no other control pins this one down --
# "does the ordering rule matter" gets asked under every scenario, not once.
PRIORITIES = portfolio.PRIORITIES
# WAS "liquidity" until 2026-09-11. scripts/priority_control.py scored every
# ordering against 20 seeded shuffles per cell, costs on, across all 19
# strategies at both account sizes, and `liquidity` beat the shuffle mean in 13
# of 38 cells -- worse than chance, and the most concentrated ordering measured.
# `mom_hi` (strongest trailing 12-month return first) beat it in 35 of 38,
# +3.84 CAGR points, and replicated on the 12 strategies the hypothesis was not
# formed on. The axis itself was cut from five orderings to three at the same
# time; portfolio.PRIORITIES carries the full table.
PRIORITY_DEFAULT = "mom_hi"
# EXCLUDED, with the measurement that justifies it -- the same discipline
# config.EXCLUDED applies to equities. Found 2026-09-03 when these became
# universes and their buy-and-hold benchmarks were checked:
#
#   SILVER    A 1000x SEAM on 2026-08-26: close 244,127 -> 240.4 in one session.
#             The contract specification changes mid-series, so the file is two
#             different instruments end to end and its whole-history buy-and-hold
#             reads -26% a year. Not a price move and not repairable by scaling
#             one side, because which side is which is not recorded.
#   CRUDEOIL  Settled at Rs1.00 on 2020-04-20, the day WTI went negative and MCX
#             floored the price. REAL, not a data error -- and that is why it
#             cannot simply be cleaned away. Any position open through it shows
#             -99.9% and then a 1,324x bounce, which destroys every R multiple,
#             drawdown and CAGR that crosses the date.
#
# Both can come back: silver once the series is spliced on a recorded ratio,
# crude once the settlement day is handled explicitly rather than traded.
#   NIFTY 50    SPOT INDEX, not a futures contract, and no modelling fixes that.
#   NIFTY BANK  scripts.fetch_assets pulled these as Kite INDEX candles. An index
#               is a number, not an instrument: it has no lot size because there
#               is nothing to buy. Trading it means NIFTY futures, which are a
#               different series with their own basis and roll, or options, which
#               are a different problem entirely. Adding lot sizes here would put
#               a precise number on something that cannot be purchased. They come
#               back when the NFO futures series is fetched -- that needs a Kite
#               login, so it is a job for scripts.fetch_assets, not a patch here.
ASSET_EXCLUDED = {
    "SILVER": "1000x contract seam 2026-08-26 (244,127 -> 240.4 in one session)",
    "CRUDEOIL": "Rs1.00 settlement 2020-04-20 (real; -99.9% then 1,324x)",
    "NIFTY 50": "spot index, not a tradeable contract -- needs the NFO futures series",
    "NIFTY BANK": "spot index, not a tradeable contract -- needs the NFO futures series",
}
# EMPTIED 2026-09-11 (user: "remove bit coin and gold they arent necessary").
# This project is a workbench for NSE EQUITIES: the universe, the liquidity
# buckets, the 1% participation cap and every benchmark on the page are equity
# machinery, and two non-equity series carried at one priority each could never
# be compared with any of it. They were also the only rows on the board with no
# universe axis. Nothing else changes -- `assets` and `single_name` serialise
# empty, which is the state a --stocks-only build already produced and which
# check_dashboard.js already reports as ok. The fetch side is untouched
# (scripts/fetch_assets.py, ASSET_EXCLUDED below), so putting a series back is
# one line here.
ASSETS: list[tuple[str, str, float]] = []
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
ATH_BAND = registry.ATH_BAND
# The eath family stopped being one row on 2026-09-05 -- see registry.ATH_STACKS
# for why five. Published like PAIR_TAGS so the page enumerates the family from
# the payload instead of the hardcoded ["near-high"] it carried until then, which
# would have shown one row and silently hidden the other four.
ATH_TAGS = registry.ATH_STACKS
ATH_LABELS = {k: registry.ATH_STACK_LABEL[k] for k in ATH_TAGS}
# ATH_BAND is deliberately NOT published: the page would have no view for it.
# The percentage reaches the reader through the family label instead, which
# registry.FAMILY_LABELS builds from the same constant.

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


def _t_taken(taken):
    got = validation.clustered_t(taken) if taken else None
    return None if not got or got.get("t_stat") is None else round(float(got["t_stat"]), 2)


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
            # The rest of the refusals, split since 2026-09-07 (audit D4):
            # "unaffordable" alone understated cash starvation by more than
            # half -- W/D all/2L/1%/2018 had 40,898 skipped_cash and 53,252
            # more refused as cash SCRAPS under the fee floor. Both are "for
            # want of cash"; the page now says so.
            "skipped_tiny_cash": r.get("skipped_tiny_cash", 0),
            "skipped_tiny_risk": r.get("skipped_tiny_risk", 0),
            "skipped_size": r.get("skipped_size", 0),
            # Credibility on the trades THIS account took, not on every signal
            # the rule ever printed (audit A2: the paper t of the headline rows
            # was 17-20; on the ~400 trades the 2L account took it was 1.2-1.5).
            "t_taken": (_t_taken(r["taken"])),
            "median_cash": keep(r.get("median_cash_pct")),
            "full_pct": keep(r.get("fully_invested_pct")),
            "episodes": eps,
            "curve": curve_payload(r["curve"], r.get("cash_curve"))}


# ------------------------------------------------------- grid checkpoints ----
# THE GRID IS REBUILT IN PARTS (2026-09-09).
#
# The grid was one indivisible 10,260-cell pass, ~59 minutes of the ~103-minute
# rebuild. Two costs came out of that:
#
#   An interrupted rebuild lost everything. A crash or a Ctrl-C 50 minutes in
#   left nothing on disk to resume from, which is exactly what CLAUDE.md's
#   "checkpoint pipeline stages" rule exists to prevent.
#
#   A one-engine edit repriced all 19 variants. Once signals.py learned to
#   stamp a cache against its own producer's import closure, holygrail.py's
#   trades could change while the other 18 strategies' did not -- and the grid
#   still recomputed all 19, because it had no idea whose trades had moved.
#
# So the grid is now cut into PARTITIONS of one (fill mode, strategy, variant),
# each written to CLEAN/grid_ckpt as it completes. A partition is reused when
# its digest matches, and the digest is taken over the things a cell actually
# depends on:
#
#   the trades themselves    pickled and hashed, not fingerprinted by a chosen
#                            subset of fields. 0.21s for the largest list on the
#                            board (179,305 trades), and EXACT -- a field this
#                            file does not read today but starts reading
#                            tomorrow is already covered. Note this is taken
#                            AFTER apply_spread, so it also pins the spread.
#   the universes            ukey -> label + members, so a bucket that gains one
#                            stock invalidates every partition, as it must.
#   the axes                 RISKS, CAPITALS, START_YEARS, PRIORITIES and the
#                            single-name priority rule; each is a multiplier on
#                            the cell count and on the answers.
#   the account modules      signals._ACCOUNT (portfolio, curves, validation,
#                            contracts) and THIS FILE, BY CONTENT. These turn a
#                            trade list into a cell. Contents and not timestamps
#                            for the reason set out in signals._content: a
#                            branch switch rewrites these files without changing
#                            a byte, and on 2026-09-09 that alone would have
#                            discarded all 38 partitions.
#
# The PRODUCER modules are deliberately NOT in the digest. Their effect is
# already in the trades hash, and exactly, so hashing their contents as well
# would throw away the per-producer narrowing this was built to exploit -- a
# docstring edit in darvas.py would otherwise reprice Holy Grail.
#
# Cells are pickled whole. They are plain dicts of floats and lists at this
# point (to_native runs later), so nothing lossy happens on the round trip; a
# test asserts a reused partition is byte-identical to a computed one.
GRID_CKPT = config.CLEAN / "grid_ckpt"


def _grid_digest(trades, unis, risks, capitals, years, fkey) -> str:
    """Everything a partition's cells depend on, in one hex string."""
    h = hashlib.sha256()
    h.update(pickle.dumps(trades, protocol=5))
    h.update(repr([(k, lab, None if mem is None else sorted(mem))
                   for k, (lab, mem) in sorted(unis.items())]).encode())
    h.update(repr([risks, capitals, years, list(PRIORITIES), PRIORITY_DEFAULT,
                   fkey, FILL_SPEC[fkey]]).encode())
    here = pathlib.Path(signals.__file__).resolve().parent
    h.update(repr(sorted((n, signals._content(here.joinpath(n).resolve()))
                         for n in signals._ACCOUNT
                         if here.joinpath(n).exists())).encode())
    return h.hexdigest()[:32]


def _ckpt_path(fkey: str, skey: str, band) -> pathlib.Path:
    """One file per partition. The name is only a label -- the digest inside
    decides whether it may be used -- so squashing odd characters is safe."""
    raw = f"{fkey}__{skey}__{tag(band)}"
    return GRID_CKPT / (re.sub(r"[^A-Za-z0-9_.-]", "_", raw) + ".pkl")


def _ckpt_load(path: pathlib.Path, digest: str) -> dict | None:
    if not path.exists():
        return None
    try:
        blob = pickle.loads(path.read_bytes())
    except Exception:
        return None                      # truncated by an interrupted write
    return blob["cells"] if blob.get("digest") == digest else None


def _ckpt_save(path: pathlib.Path, digest: str, cells: dict) -> None:
    """Written via a temporary file and renamed, so a kill mid-write leaves the
    previous checkpoint intact rather than a half file that loads as garbage."""
    GRID_CKPT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pkl.tmp")
    tmp.write_bytes(pickle.dumps({"digest": digest, "cells": cells}, protocol=5))
    os.replace(tmp, path)


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


def trade_stats(trades: list[dict]) -> dict:
    """Trade-level quality of one rule's signal list, one position per stock.

    RUPEE FIELDS ARE ON THE PRODUCER'S PAPER BOOK, which since 2026-09-07 is
    the largest capital on the board (sizing.CAPITAL = Rs1cr, 1% risk) so the
    cache never drops a signal the biggest account could take (audit A11).
    They are therefore ~100x the pre-2026-09-07 figures and describe no
    account on the page. The R-multiple fields beside them are scale-free and
    are what to read; the page captions the rupee ones.
    """
    nets = [t["net_profit"] for t in trades]
    rs = [t["r_multiple"] for t in trades if t.get("risk_taken")]
    r_wins = [r for r in rs if r > 0]
    r_losses = [r for r in rs if r <= 0]
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
            "paper_capital": float(sizing.CAPITAL), "paper_risk_pct": 100 * sizing.RISK_PCT,
            "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
            "avg_win_r": round(sum(r_wins) / len(r_wins), 2) if r_wins else None,
            "avg_loss_r": round(sum(r_losses) / len(r_losses), 2) if r_losses else None,
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


# Bumped whenever the shape of what build_validation() writes changes, so an
# old cache from before a shape change is refused rather than served with a
# missing key -- kitelab.signals only knows the universe/data/code moved, not
# that the payload it is guarding grew a field.
_VALIDATION_CACHE = "_validation_summary_v9"


def benchmarks(cfg, uni_members: dict) -> tuple[dict, dict]:
    """Equal-weight buy-and-hold per (universe, start year), and per
    walk-forward calendar window per universe. A few seconds over the price
    files; computed once per build and cached with the validation record.

    Keys: hold_by_scenario["<uni>|<start_year>"]; hold_by_universe[uni] is the
    {"2006-2009": cagr, ...} dict kitelab.validation.walk_forward_grid expects.
    """
    by_scenario, by_universe = {}, {}
    for uni_key, members in uni_members.items():
        members = cfg.merged if members is None else sorted(members)
        for year in START_YEARS:
            got = validation.buy_and_hold(members, start_year=year)
            by_scenario[f"{uni_key}|{year}"] = (None if got is None
                                                else round(float(got), 1))
        by_universe[uni_key] = validation.hold_by_window(members)
    return by_scenario, by_universe


def build_validation(cfg, trades_by_key: dict, universes: dict) -> dict:
    """Is each rule's number real, or a curve fit? Once per strategy, cached.

    Six tests plus a bootstrap resample -- kitelab.validation, which is also
    what `scripts.validate` and `scripts.bootstrap` call, so the page and the
    CLI tools can never print two different answers to the same question
    (those two CLIs still run "on paper" -- see kitelab.validation.
    permutation_test's own docstring for why that is fine for a standalone
    printout but not for a page shown next to a "Realistic fills" CAGR
    column).

    `trades_by_key` is `slipped` (spread-adjusted, i.e. the SAME trades the
    gridded "Realistic fills" CAGR column uses), not `base` -- changed
    2026-09-05. Every check here that touches account simulation
    (walk_forward_grid, breakeven's bisection, permutation's shuffled and
    observed CAGR) also needs the SAME execution() state (costs + cap) the
    grid's own "Realistic fills" cell uses, active in portfolio.run while
    this function runs -- see the call site in main(). Before this, every
    check here ran "on paper" (no spread, no market impact, no size cap), a
    gap no control on the page could close, so it lived in the Compare
    banner as a permanent caveat instead of being fixed.

    "Validated" itself is NOT computed here. All five checks behind it are
    LIVE -- three (beats buy & hold, walk-forward majority: account-level) to
    Universe/Priority/Capital, two (distinguishable from shuffled prices,
    survives a cost margin) plus Credibility to Universe alone, since none of
    the three ever calls portfolio.run() -- see
    kitelab.validation.fixed_checks_by_universe. web/dashboard.html's rows()
    combines this function's walk_forward_by_scenario and
    fixed_checks_by_universe with the CURRENT scenario's own grid cell.
    validation_summary's own single, full-universe fixed_gates/bootstrap
    fields stay in the payload too (Detail's fixed-baseline paragraph, and
    the multiple-testing hurdle, which is deliberately ONE global number, not
    per-Universe) but the page reads the live per-Universe versions for the
    actual gate.

    CACHED like every signal list, via the same kitelab.signals stamp
    (universe + price files + code). The permutation test alone re-simulates
    every strategy 10 times over a 60-stock sample, so this is the one
    section of a rebuild genuinely worth not repeating when nothing about
    the strategies or the universe has moved. signals.save/load expect a
    list[dict] of trades; this holds none, so it is wrapped as a single-item
    list to fit that contract rather than changing kitelab/signals.py for
    one caller.
    """
    # account=True: this record depends on validation.py and portfolio.py,
    # not only on which trades exist (2026-09-07).
    cached = signals.load(_VALIDATION_CACHE, cfg.merged, account=True)
    if cached is not None:
        print("  validation: cached, reusing", flush=True)
        return cached[0]

    print("  validation (benchmark, walk-forward x every scenario, top-N, cost, "
          "correlation, permutation, bootstrap):", flush=True)
    rng = np.random.default_rng(20260903)
    uni_members = {k: v[1] for k, v in universes.items()}
    # THE BENCHMARK IS PART OF THE SCENARIO (2026-09-07, audit A1). One
    # whole-history, all-universe, median-stock number (11.9) was reused for
    # every universe and start year on the page; an equal-weight PORTFOLIO of
    # the same 999 from 2018 compounds at ~17.0. Both dimensions the page can
    # change -- universe and start year -- now have their own benchmark, and
    # every walk-forward window has its own too.
    hold_by_scenario, hold_by_universe = benchmarks(cfg, uni_members)
    per_strategy: dict = {}
    monthly_by_key: dict = {}
    bootstrap_rows: list = []
    trade_stats_out: dict = {}
    for i, strat in enumerate(registry.REGISTRY, 1):
        key = f"{strat.key}|{tag(strat.variant)}"
        trades = trades_by_key.get((strat.key, strat.variant), [])
        # Rounds passed explicitly so scripts.preflight can lower the module
        # constant for its shape-only smoke build (200 rounds x 19 variants x
        # 5 universes over 3 symbols ran for many minutes on 2026-09-07).
        summary = validation.validation_summary(
            strat, trades, cfg.merged, rng,
            permutation_rounds=validation.PERMUTATION_ROUNDS)
        print(f"    {key:<26} {i}/{len(registry.REGISTRY)}"
              + ("" if summary else f" -- skipped, under {validation.MIN_TRADES} trades"),
              flush=True)
        if summary is None:
            continue
        # RAW trades, not drop_overlaps-filtered: this mirrors what the
        # account grid actually does (portfolio.run handles busy/cash
        # skipping itself), unlike the fixed rule-level checks above.
        summary["walk_forward_by_scenario"] = validation.walk_forward_grid(
            trades, uni_members, PRIORITIES, CAPITALS, hold_by_universe)
        # Credibility, Beats-shuffled-prices and Survives-cost, live per
        # Universe -- see fixed_checks_by_universe's docstring for why
        # Priority/Capital cannot move any of them. "all" is the same trades
        # the call above already computed all three for, so reuse that
        # rather than resampling/re-simulating it a second time.
        fixed_by_universe = validation.fixed_checks_by_universe(
            strat, trades, cfg.merged,
            {k: v for k, v in uni_members.items() if k != "all"}, rng,
            permutation_rounds=validation.PERMUTATION_ROUNDS)
        if summary["bootstrap"] is not None:
            fixed_by_universe["all"] = {
                "credibility": summary["bootstrap"], "breakeven": summary["breakeven"],
                "permutation": summary["permutation"], "fixed_gates": summary["fixed_gates"],
            }
        summary["fixed_checks_by_universe"] = fixed_by_universe
        monthly_by_key[key] = summary.pop("monthly")
        per_strategy[key] = summary
        if summary["bootstrap"] is not None:
            bootstrap_rows.append({"key": key, **summary["bootstrap"]})
        trade_stats_out[key] = trade_stats(strategies.drop_overlaps(trades))

    correlation = validation.correlation_summary(monthly_by_key)
    summary = validation.multiple_testing_summary(bootstrap_rows, monthly_by_key)
    summary["hold_cagr_by_scenario"] = hold_by_scenario
    for key, row in per_strategy.items():
        row["most_correlated"] = correlation.get(key)
        # CLEARS_HURDLE is no longer resolved here: since Credibility itself
        # went live per Universe (fixed_checks_by_universe, 2026-09-05), the
        # gate has to compare THAT universe's t-stat against the hurdle, not
        # the "all"-universe one -- only web/dashboard.html's rows() knows
        # which universe is on screen. `summary["hurdle"]` (one number, fixed:
        # it is a property of all 22 variants' correlation structure, not of
        # any one scenario) is what it compares against.

    out = {"validation": per_strategy, "summary": summary,
           "trade_stats": trade_stats_out}
    signals.save(_VALIDATION_CACHE, [out], cfg.merged, account=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stocks-only", action="store_true",
                    help="skip the non-equity instruments (Bitcoin, GOLD) -- "
                         "equity grid only, no assets pass")
    ap.add_argument("--no-grid-cache", action="store_true",
                    help="recompute every grid partition even when its checkpoint "
                         "matches. The escape hatch for doubting the digest; a "
                         "normal rebuild does not need it")
    args = ap.parse_args()

    cfg = config.load()
    asset_symbols = [] if args.stocks_only else [a[0] for a in ASSETS]
    # STAMP FIRST, CERTIFY LAST (2026-09-07, audit B2/B3). The stamp records
    # the code and price files that PRODUCED these numbers, so it is taken
    # before anything is computed and compared again after; if the two differ
    # the file is written without a stamp and status() reports it stale. The
    # assets' price files are part of it -- they were not until today, so a
    # GOLD refetch left the page reporting current.
    stamp_before = signals.stamp(list(cfg.merged) + asset_symbols, account=True)

    print("  signal lists (cached where possible):", flush=True)
    base = signal_lists(cfg.merged)

    # Counted, not typed. Seven stocks were removed from the universe on
    # 2026-08-31 (kitelab.config.EXCLUDED) and every label that said "199" would
    # otherwise have quietly gone on saying it.
    # The 101/399 in-sample/holdout split was dropped on 2026-09-03 and every
    # universe below is cut from config.Config.merged, which carries the
    # argument. The split's last traces (cfg.in_sample / cfg.out_of_sample and
    # scripts.universe_bias) were deleted on 2026-09-07; what it measured is
    # recorded in CLAUDE.md.
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
        # RECENT (added 2026-09-07, audit A5): stocks with no positive-turnover
        # bar before the cut. Until then they were in "all" and in NO bucket
        # -- 170 of 999, so 73 + 116 + 640 = 829 and "small caps" silently
        # excluded every post-2017 listing, the cohort most exposed to
        # survivorship. The four sets now partition `symbols`.
        recent = {s for s in symbols if s not in turn}
        return ({s for s, t in turn.items() if t < 5e7},
                {s for s, t in turn.items() if 5e7 <= t < 25e7},
                {s for s, t in turn.items() if t >= 25e7},
                recent)

    # As of START_DEFAULT, the reference year every headline is quoted at. One
    # classification rather than one per start year: the universe key is part of
    # every grid key, so per-year buckets would multiply the grid and make two
    # cells labelled "large" mean different sets of stocks.
    small, mid, large, recent = buckets(cfg.merged, asof=START_DEFAULT)
    assert len(small) + len(mid) + len(large) + len(recent) == len(cfg.merged), \
        "liquidity buckets must partition the universe"
    print(f"  liquidity (pre-{START_DEFAULT} turnover): {len(small)} small/micro, "
          f"{len(mid)} mid, {len(large)} large, {len(recent)} listed after the cut",
          flush=True)
    # The three liquidity buckets, and ALL of them. "large" was missing until
    # 2026-09-02: 46 small plus 18 mid is 64 of 101, so the 37 most liquid names
    # -- the ones easiest to actually trade -- were the only group with no view
    # of their own. The cut points are scripts.screen_universe's, so "mid" here
    # means what it means there.
    universes = {"all": (f"All {len(cfg.merged)} stocks", None),
                 "large": (f"{len(large)} large caps", large),
                 "mid": (f"{len(mid)} mid caps", mid),
                 "small": (f"{len(small)} small caps", small),
                 "recent": (f"{len(recent)} listed after {START_DEFAULT - 1}", recent)}

    def execution(costs: bool, cap: bool):
        """Impact and the size limit live in portfolio.run, so they are globals."""
        slippage.ENABLED = costs
        slippage.MAX_PARTICIPATION = REALISTIC_PARTICIPATION if cap else None
        slippage.reset()

    # The spread is charged onto the cached trades rather than re-simulated:
    # nothing in the simulation depends on the fill price, so this is exact and
    # it keeps the whole toggle affordable (see slippage.apply_spread).
    execution(True, True)
    slipped = {key: [slippage.apply_spread(t) for t in trades]
               for key, trades in base.items()}
    print("  spread applied to the cached signal lists", flush=True)
    # The spread is a COST, so it rides with the costs half of the key.
    sets = {fkey: (slipped if costs else base) for fkey, (costs, _) in FILL_SPEC.items()}

    # REALISTIC FILLS, not "on paper" -- moved to `slipped` and given the SAME
    # costs+cap execution() state as the gridded "Realistic fills" cell,
    # 2026-09-05. build_validation used to run on `base` (pre-spread, no
    # market impact or size cap), which meant Credibility/Beats-shuffled-
    # prices/Survives-cost quietly ran a little ahead of the CAGR column even
    # on a row where every other control matched -- a gap no control on the
    # page could close, so it lived in the Compare banner as a permanent
    # caveat instead. Running on `slipped`, with the same execution() state
    # active in portfolio.run for everything here that calls it
    # (walk_forward_grid, breakeven's bisection, permutation's shuffled and
    # observed CAGR), closes that gap instead of just disclosing it. Needs
    # `universes` (for the live per-scenario walk-forward, crossed with
    # Universe x Priority x Capital -- see kitelab.validation.walk_forward_grid),
    # so this runs after the bucket definitions above, not before them.
    validation_out = build_validation(cfg, slipped, universes)
    execution(False, False)

    grid = {}

    def fill_grid(bar, source, unis, risks, capitals, years, scope="equity"):
        """One pass of the grid, over every universe at every setting.

        The scanning-pool axis was removed on 2026-09-03. It and the Breadth
        sweep were one measurement wearing two names, and what it found -- that
        a small account cannot fund what a wide scan offers -- is now on every
        row of the table as `skipped_cash` and `exposure`, which say it without
        a sweep. Signal priority is what remains, because that is the choice a
        trader actually makes when the cash runs out.

        REBUILT IN PARTS since 2026-09-09: one checkpoint per (fill, strategy,
        variant), reused when its digest matches. See GRID_CKPT above for what
        the digest covers and why the producer modules are excluded from it.

        `scope` only separates the two call sites' checkpoint files. Both grid
        the same (fkey, skey, band) space over DIFFERENT universes -- equities
        here, BITCOIN/GOLD in the assets pass -- so without it the two would
        write to one path, each miss the other's digest, and overwrite it. The
        result would not be wrong, but no partition would ever be reused.
        """
        reused = 0
        for fkey in GRID_FILLS:
            execution(*FILL_SPEC[fkey])
            # `trades`, not `signals`: the latter is the module imported above,
            # and shadowing it here made kitelab.signals unreachable for the
            # whole body of the grid loop.
            for (skey, band), trades in source[fkey].items():
                digest = _grid_digest(trades, unis, risks, capitals, years, fkey)
                path = _ckpt_path(f"{scope}_{fkey}", skey, band)
                if not args.no_grid_cache:
                    hit = _ckpt_load(path, digest)
                    if hit is not None:
                        grid.update(hit)
                        # Step the bar by what this partition would have cost,
                        # not by one, or the progress line under-reports and
                        # the final count never reaches the total.
                        for _ in range(len(hit)):
                            bar.step()
                        reused += 1
                        continue
                cells = {}
                for ukey, (ulabel, members) in unis.items():
                    subset = (trades if members is None
                              else [t for t in trades if t["symbol"] in members])
                    # Sorted once; each start year is a suffix of the one before, so
                    # the slice is a bisect rather than a fresh filter per year.
                    subset = sorted(subset, key=lambda t: t["entry_ts"])
                    stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
                    # Start years that cut nothing off are the same backtest
                    # under an earlier label -- see gridded_years. They are
                    # written as None rather than omitted, so the key space
                    # stays rectangular and the page's payload contract (every
                    # key PRESENT, null allowed) still holds.
                    live_years = gridded_years(stamps, years)
                    for year in years:
                        cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
                        window = subset[cut:] if year in live_years else []
                        # EVERY PRIORITY AT EVERY START YEAR -- except where
                        # priority cannot mean anything.
                        #
                        # Signal priority decides which signal wins the cash
                        # when the account cannot fund them all. A universe of
                        # ONE instrument never has two candidates at once (the
                        # account holds one position per symbol), so all five
                        # orderings produce byte-identical results. Sweeping
                        # them there computed four duplicate cells for every
                        # real one -- 40% of the grid -- and put a control on
                        # the page that could not change its own answer.
                        #
                        # Detected from the universe rather than a list of
                        # instrument names, so it stays true if single-name
                        # universes are ever added for equities too.
                        single = members is not None and len(members) == 1
                        for prio in ([PRIORITY_DEFAULT] if single else PRIORITIES):
                            for risk in risks:
                                for capital in capitals:
                                    key = (f"{skey}|{tag(band)}|{ukey}|{risk:g}"
                                           f"|{capital}|{fkey}|{year}|{prio}")
                                    if not window:
                                        cells[key] = None
                                        bar.step()
                                        continue
                                    cells[key] = run_payload(portfolio.run(
                                        window, capital, risk / 100, prio))
                                    bar.step()
                grid.update(cells)
                _ckpt_save(path, digest, cells)
        return reused

    # Cells per (variant, universe, risk, capital): every start year crossed
    # with every signal priority, so no control pins another. Single-instrument
    # universes get one priority only -- see fill_grid for why.
    per_combo = len(START_YEARS) * len(PRIORITIES)
    total = (len(GRID_FILLS) * len(sets[GRID_FILLS[0]]) * len(universes)
             * len(RISKS) * len(CAPITALS) * per_combo)
    bar = Bar(total, "grid")
    reused = fill_grid(bar, sets, universes, RISKS, CAPITALS, START_YEARS)
    bar.close()
    n_parts = len(GRID_FILLS) * len(sets[GRID_FILLS[0]])
    print(f"    grid: {reused}/{n_parts} partitions reused from checkpoints, "
          f"{n_parts - reused} recomputed", flush=True)

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
                                 f"|{capital}|{fkey}|{START_DEFAULT}|{PRIORITY_DEFAULT}")
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

    # ---- the non-equity instruments, as universes of their own -------------
    #
    # These were a separate page with a separate code path until 2026-09-03:
    # one hardcoded account each, at Rs1,00,000 and 1% risk over all history,
    # so none of the axes the compare table offers -- start year, risk, signal
    # priority -- reached them. Two paths computing the same thing differently
    # is what produced the two-samplers bug earlier the same day.
    #
    # They are universes now, and what made that possible is that the fee rate
    # and whole-versus-fractional units moved onto the TRADE. They were module
    # globals, so the grid could only run under one setting at a time, which is
    # precisely why they needed their own account.
    #
    # THE SPREAD MODEL IS NOT APPLIED TO THEM. slippage is calibrated on NSE
    # equity turnover; asking it about Bitcoin would return a number with no
    # meaning behind it. Their flat exchange fee already covers the round trip,
    # so they are gridded from unslipped trades under the same fill key.
    assets = {}
    if args.stocks_only:
        print("  non-equity instruments: skipped (--stocks-only)", flush=True)
    else:
        print("  non-equity instruments:", flush=True)
        # An unverified contract spec must not reach the dashboard quietly: every
        # lot size and margin in kitelab.contracts is hand-entered from a published
        # note, not derived from anything the project holds.
        held = {a[0] for a in ASSETS}
        unverified = [c for c in contracts.unverified_multipliers() if c in held]
        if unverified:
            print(f"    WARNING: multiplier UNVERIFIED for {', '.join(unverified)}",
                  flush=True)
        # A SERIES OLDER THAN ITS CONTRACT is not a long history, it is a different
        # instrument wearing the same name. GOLDTEN launched 2025-04-01 and
        # SILVER100 2026-06-01; a vendor serving either back to 2010 is synthesising
        # it, and nothing downstream could tell.
        for symbol in sorted(held):
            spec = contracts.get(symbol)
            if spec is None or spec.launched is None:
                continue
            try:
                first = frames.daily(symbol)["ts"].iloc[0]
            except (SystemExit, IndexError):
                continue
            if spec.predates_launch(first):
                print(f"    WARNING: {symbol} data starts {str(first)[:10]} but the "
                      f"contract launched {spec.launched} -- the earlier bars are "
                      f"not this instrument", flush=True)
        asset_base: dict = {}
        for symbol, _entry_tf, fee in ASSETS:
            try:
                daily = frames.daily(symbol)
            except SystemExit:
                print(f"    {symbol}: no data, skipped", flush=True)
                continue
            bh = bh_stats(daily)
            assets[symbol] = {"bh": {"cagr": round(bh["cagr"], 1),
                                     "maxdd": round(bh["maxdd"], 1),
                                     "uw": round(bh["longest_uw"] / 365.25, 1),
                                     "years": round(bh["years"], 1)},
                              "fee_pct": round(100 * fee, 3)}
            backtest.FLAT_FEE_RATE = fee
            sizing.FRACTIONAL = True
            try:
                for st in registry.REGISTRY:
                    try:
                        trades = st.build(symbol)
                    except (SystemExit, FileNotFoundError):
                        trades = []
                    # Stamped per trade so the account can price and size this
                    # instrument by its own rules even in a mixed run. A contract
                    # spec means futures: whole lots, funded by margin. Without one
                    # the instrument is bought outright, which is right for spot
                    # Bitcoin and wrong for everything on MCX.
                    spec = contracts.get(symbol)
                    for t in trades:
                        t["fee_rate"] = fee
                        if spec is None:
                            t["fractional"] = True
                        else:
                            t["multiplier"] = spec.multiplier
                            # DATED, not constant. Crude's minimum initial margin
                            # ran ~9% in 2016 and 33% after the Aug 2024 revision;
                            # one figure across the sample would let the account
                            # hold late positions the exchange would have refused.
                            t["margin_pct"] = spec.margin_at(t["entry_ts"])
                    asset_base.setdefault((st.key, st.variant), []).extend(trades)
            finally:
                backtest.FLAT_FEE_RATE = None
                sizing.FRACTIONAL = False
            print(f"    {symbol} done", flush=True)

        if assets:
            asset_universes = {sym: (sym, {sym}) for sym in assets}
            asset_sets = {fkey: asset_base for fkey in FILL_SPEC}
            # One priority each: every one of these is a single instrument.
            a_total = (len(GRID_FILLS) * len(asset_base) * len(asset_universes)
                       * len(RISKS) * len(CAPITALS) * len(START_YEARS))
            a_bar = Bar(a_total, "assets")
            fill_grid(a_bar, asset_sets, asset_universes, RISKS, CAPITALS,
                      START_YEARS, scope="assets")
            a_bar.close()
            universes.update({k: (v[0], v[1]) for k, v in asset_universes.items()})
            # The single-name universes' benchmark is the instrument itself,
            # from each start year -- so "vs Hold" on a BITCOIN row compares
            # against holding Bitcoin, not the 999-stock median it used to.
            for sym in assets:
                for year in START_YEARS:
                    got = validation.buy_and_hold([sym], start_year=year)
                    validation_out["summary"]["hold_cagr_by_scenario"][f"{sym}|{year}"] = (
                        None if got is None else round(float(got), 1))

    payload = {
        # IST, and labelled: the build machine may be on any clock.
        "built": config.now_local().strftime("%Y-%m-%d %H:%M IST"),
        "strategies": STRATEGY_LABELS,
        "universes": {k: v[0] for k, v in universes.items()},
        "risks": RISKS, "capitals": CAPITALS,
        # Which signal wins the cash when the account cannot fund them all.
        # Every priority is gridded at every start year, so neither control
        # constrains the other and the page needs no clamp.
        "priorities": PRIORITIES, "priority_default": PRIORITY_DEFAULT,
        # Universes holding a single instrument, where signal priority cannot
        # mean anything: nothing competes for the cash, so the page disables
        # the control rather than offering five settings with one answer.
        "single_name": sorted(k for k, v in universes.items()
                              if v[1] is not None and len(v[1]) == 1),
        "start_years": START_YEARS, "start_default": START_DEFAULT, "bands": BANDS,
        "hg_tags": HG_TAGS,
        "pair_tags": PAIR_TAGS, "pair_labels": PAIR_LABEL,
        "ath_tags": ATH_TAGS, "ath_labels": ATH_LABELS,
        # index -> the date list every curve carrying that index shares
        "calendars": [list(k) for k, _ in sorted(_CALENDARS.items(), key=lambda kv: kv[1])],
        "darvas_windows": DARVAS_TAGS,
        # Only what was actually gridded. Publishing all four let the page
        # offer three modes that resolve to nothing, and a missing cell renders
        # as an em dash -- indistinguishable from a rule that took no trades.
        "fills": [m for m in FILL_MODES if m[0] in GRID_FILLS],
        "grid": grid, "waterfall": waterfall,
        "assets": assets,
        # Stages this build skipped, so the page can say so instead of
        # rendering an empty tab as if it were a result (audit B3).
        "partial": ["assets"] if args.stocks_only else [],
        # Is each rule's number real, or a curve fit? Keyed "skey|tag(variant)",
        # matching the Compare row id built in web/dashboard.html. Computed once
        # per strategy over the merged universe -- see build_validation().
        "validation": validation_out["validation"],
        "validation_summary": validation_out["summary"],
        "trade_stats": validation_out["trade_stats"],
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
    # Written to a spool and renamed AFTER the JSON (audit B5): the old order
    # truncated the curves file first, so a failure before the JSON landed left
    # the old index pointing into the new file and /api/curve served a byte
    # slice of the wrong curve, which parsed and drew. Offsets and lengths are
    # both in BYTES now (the length was in characters, harmless only while the
    # file happened to be pure ASCII), and the file's digest travels in the
    # payload so the server can refuse a curves file that is not this build's.
    detail_path = OUT.with_name("dashboard.curves.jsonl")
    detail_spool = detail_path.with_suffix(".jsonl.partial")
    index: dict = {}
    offset = 0
    curves_hash = hashlib.sha256()
    with open(detail_spool, "wb") as fh:
        for key, cell in grid.items():
            if not cell:
                continue
            blob = json.dumps({"curve": cell.pop("curve", None),
                               "episodes": cell.pop("episodes", None)}).encode()
            line = blob + b"\n"
            index[key] = [offset, len(blob)]
            fh.write(line)
            curves_hash.update(line)
            offset += len(line)
    payload["curve_index"] = index
    payload["curves_sha256"] = curves_hash.hexdigest()
    print(f"  curves -> {detail_path.name} "
          f"({detail_spool.stat().st_size/1e6:.0f} MB, {len(index):,} entries)")

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
    os.replace(detail_spool, detail_path)
    os.replace(spool, OUT)
    print(f"\n  written: {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")

    # Record WHAT these numbers describe, in a small file beside the big one.
    # Without it the page can only say "built <date>", which is how a
    # dashboard built over 192 stocks went on being served after 91 of them
    # were excluded. dashboard_server.status() reads this and warns on the page.
    # The stamp is the one taken BEFORE the build; if the code or the price
    # files moved meanwhile the file is left uncertified (inputs=None) and
    # status() says so, rather than certifying stale numbers as current.
    stamp_after = signals.stamp(list(cfg.merged) + asset_symbols, account=True)
    moved = stamp_after != stamp_before
    dashboard_server.write_stamp(cfg.merged, payload["built"],
                                 inputs=None if moved else stamp_before,
                                 partial=payload["partial"], assets=asset_symbols)
    if moved:
        print("  NOT CERTIFIED: the code or the price files changed while this "
              "was building. The page will report it stale; rebuild.")
    else:
        print(f"  stamped: {dashboard_server.STAMP_PATH.name} "
              f"({len(cfg.merged)} symbols + {len(asset_symbols)} assets)")


if __name__ == "__main__":
    main()

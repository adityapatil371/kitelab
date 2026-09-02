"""Precompute everything the local dashboard can show.

    python -m scripts.dashboard_data

Writes data/dashboard.json for http://localhost:8765/dashboard (served by
python -m scripts.dashboard). The page is a pure viewer -- every control selects
among these precomputed results, nothing is simulated in the browser.

Everything is combinable with everything:
    grid        one-account simulations for FOUR strategy stacks (EMA on
                M/W/D and Q/M/W timeframes, plus the Darvas channel)
                x universe (all / in-sample / holdout -- sizes are counted from
                config, never hardcoded)
                x risk (0.25-2%) x capital (50k-5L)
    scaleout    the "sell half at +1R" scale-out idea, per strategy AND per
                universe, columns matching the old Scale-Out Test sheet --
                measured per trade (a two-part exit cannot be priced by the
                one-account simulation)
    stocks      every stock in the universe: weekly closes + trades + stats
                for all four stacks
    assets      the six class instruments: closes, per-stack trades/accounts,
                scale-out variants, buy-and-hold comparison (W/D/H exists only
                where hourly data does: BITCOIN and the two indices)
    timeframes  the classic 5-assigned-stock gross comparison tables
    nifty       NIFTY 50 overlay series

Conventions: EMA stacks use the class rule (stops checked at closes only);
Breakout keeps intrabar stops (its buy-stop entry is inherently intrabar).
"""
from __future__ import annotations

import bisect
import json
import re
import pickle

import numpy as np
import pandas as pd

from kitelab import (backtest, config, darvas, dashboard_server, frames, holygrail,
                     portfolio, signals, sizing, slippage, strategies)
from kitelab.progress import Bar
from kitelab.curves import bh_stats, episodes, underwater_stats
from kitelab import timeframes
from kitelab.timeframes import (VARIANTS as TF_VARIANTS, simulate_variant,
                                summarise as tf_summarise, window_start)

ASSIGNED = config.CLASS_ASSIGNED

OUT = config.CLEAN / "dashboard.json"
RISKS = [0.25, 0.5, 1.0, 2.0]

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
# How many random 10-stock baskets the Monte Carlo draws. Once the start-year
# axis came down to five, this became the longest part of the rebuild -- it was
# never on that axis, so it never shrank with it.
#
# 40 rather than 75. The draws feed two things: a distribution of outcomes, and
# the choice of the median basket that becomes the "10 random stocks" universe.
# 40 is ample for the percentiles actually read off it (median, 10th, 90th); the
# standard error of a median falls with the square root of the count, so going
# 75 -> 40 widens it by about a third while costing 47% less. For a figure quoted
# to one decimal that is not a trade worth refusing.
#
# It does move the chosen median basket, and with it every "b10" number -- a
# different draw can land on a different middle. That is a change of sample, not
# a change of method.
BASKET_DRAWS = 40

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
REALISTIC_PARTICIPATION = 0.01     # one order <= 1% of the stock's daily turnover
# Small accounts dropped 2026-09-01: below ~Rs1 lakh the size cap decides the
# result more than the rule does, which made those columns a study of the cap.
CAPITALS = [100_000, 200_000, 300_000]
ASSETS = [("BITCOIN", "30m", 0.0010), ("NIFTY 50", "30m", 0.0005),
          ("NIFTY BANK", "30m", 0.0005), ("GOLD", "1d", 0.0005),
          ("SILVER", "1d", 0.0005), ("CRUDEOIL", "1d", 0.0005)]
STEP = 10
BANDS = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
# W/D/H and ATH Breakout were ruled out in class (2026-09-01) and are no longer
# computed. Their code is untouched -- strategies.ath_breakout_trades and the WDH
# variant still work and still have scripts -- they are simply not on the board.
# "Turtle", not "Darvas": the 20/10 and 55/20 channels are what the Turtles
# traded, which is what the class is studying. The old label was wrong and the
# module docstring has always said so.
STRATEGY_LABELS = {"ema": "EMA · M/W/D", "qmw": "EMA · Q/M/W",
                   "pair": "EMA · one higher TF", "e1": "EMA · daily only",
                   "eath": "EMA · near the high",
                   "dv": "Turtle channel", "hg": "Holy Grail · ADX"}

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
SOLO_BAND = 0.02
ATH_BAND = 0.10

# The Holy Grail's band slot carries its STOP, because that is the choice worth
# seeing: "tight" is the signal candle's low, which is what the class marks and
# what its own spreadsheet measures out at a median 0.8% below entry; "wide" is
# the confirmed multi-bar pivot, 5.8% below, which sizes positions about five
# times smaller. Same signals, very different trade.
# Only the swing-low stop. Rule 6 of the class sheet says "SL will be swing
# low", and the signal-candle variant was dropped on 2026-09-02: it was measured
# against a CLASSMATE's spreadsheet, not the written rule, and having both on the
# board invited reading the fit as the rule.
HG_VARIANTS = [("swing", "pivot")]
HG_TAGS = [tag for tag, _ in HG_VARIANTS]

# Darvas has no band. It has a pair of windows instead, and they matter at least as
# much, so they ride in the same slot of the key that the band uses for the EMA
# stacks: "dv|20-10|all|1|250000|0". 20/10 is what the class specified and is the
# page default; it is not the best of them.
# The two systems the Turtles actually traded: System 1 (20 in, 10 out) and
# System 2 (55 in, 20 out). The other window pairs tried earlier were ours, not
# theirs, and are dropped.
DARVAS_WINDOWS = [(20, 10), (55, 20)]

# Every window is computed BOTH ways, because that is the question being asked.
# The class found that one timeframe took every breakout, including the ones
# against the larger trend, and that those were where the losses were. The weekly
# gate is the proposed fix; "1TF" is the control it has to beat. Testing the gate
# at only one window would answer half the question.
DARVAS_GATED = [True, False]
DARVAS_TAGS = [f"{a}-{b}" + ("" if g else " 1TF")
               for g in DARVAS_GATED for a, b in DARVAS_WINDOWS]
DARVAS_DEFAULT = "20-10"

# The one setting each strategy shows on the per-stock page.
PRIMARY = {"ema": 0.02, "qmw": 0.02, "pair": "WD", "e1": "daily",
           "eath": "near-high", "dv": DARVAS_DEFAULT, "hg": "swing"}


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
            # how much of the account is waiting rather than working
            "skipped_cash": r["skipped_cash"],
            "median_cash": keep(r.get("median_cash_pct")),
            "full_pct": keep(r.get("fully_invested_pct")),
            "episodes": eps,
            "curve": curve_payload(r["curve"], r.get("cash_curve"))}


def cached_signals(name: str, build) -> list[dict]:
    """Trades for one signal list, rebuilt whenever the cache cannot be trusted.

    A cache used to be accepted because its FILE EXISTED, which meant a universe
    change, a refetch or a strategy fix left it silently wrong. kitelab.signals
    stamps each cache with the universe, the price files and the strategy code it was
    built from, and load() returns None when any of those has moved.
    """
    cfg = config.load()
    hit = signals.load(name, cfg.all_symbols)
    if hit is not None:
        return hit
    out = []
    bar = Bar(len(cfg.all_symbols), name[:14])
    for symbol in cfg.all_symbols:
        try:
            out.extend(build(symbol))
        except SystemExit:
            pass
        bar.step()
    bar.close()
    signals.save(name, out, cfg.all_symbols)
    return out


def variant_builder(key: str, band: float = 0.02):
    """simulate_variant trades, padded with the fields portfolio.run needs."""
    def build(symbol):
        out = []
        for t in simulate_variant(symbol, key, band=band):
            t = dict(t)
            t["same_session"] = (pd.Timestamp(t["entry_ts"]).date()
                                 == pd.Timestamp(t["exit_ts"]).date())
            t["net_profit"] = t["gross_profit"] - t["charges"]
            out.append(t)
        return out
    return build


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


SCALE_VARIANTS = [("Keep full position (baseline)", None),
                  ("Sell half at +1R", "half"),
                  ("Sell half at +1R, stop to breakeven", "half_be")]

# Where to bank the half, as a multiple of R (the risk taken: entry minus stop).
# 1.0 is the rule as taught and reuses the original cache names.
SCALE_MULTIPLES = [0.5, 1.0, 1.5, 2.0, 3.0]

# "How many stocks does this rule need?" Random baskets at each size, one account
# each. The answer is a deployment question, not a stock-picking one: a rule whose
# stop sits far below entry buys small positions and needs many at once before the
# money is working. DRAWS is smaller for big baskets -- they are slow to simulate
# and barely vary.
# The final size is the WHOLE universe, whatever that currently is -- one draw,
# because there is only one way to pick all of them.
BREADTH_SIZES = [5, 10, 15, 20, 30, 50, 75, 100, 150]
BREADTH_DRAWS = {5: 30, 10: 30, 15: 30, 20: 30, 30: 20, 50: 20, 75: 10, 100: 10,
                 150: 10}
BREADTH_SEED = 20260831
SCALE_RULES = [("half", "Sell half"), ("half_be", "Sell half, stop to breakeven")]


# --------------------------------------------------------------- main ----

def main() -> None:
    cfg = config.load()

    print("  signal lists (cached where possible):", flush=True)
    base = {}
    for band in BANDS:
        # band 2% keeps the old cache names. Named cache_tag, not tag: tag() is the
        # module-level key formatter and a local of that name shadows it.
        cache_tag = "" if band == 0.02 else f"_b{band*100:g}"
        base[("ema", band)] = cached_signals(
            f"EMA{cache_tag}_all", lambda s, b=band: backtest.simulate(s, band=b))
        base[("qmw", band)] = cached_signals(
            f"QMW{cache_tag}_all", variant_builder("QMW", band))
        print(f"    band {band:.0%} ready", flush=True)
    # NOTE THE CACHE NAMES. Darvas gained a weekly gate on 2026-09-01, so a trade
    # list built before that date is a different strategy under the same label.
    # The old caches are UNSTAMPED, which means load() would hand them back
    # without complaint -- renaming is what forces the rebuild.
    for gated in DARVAS_GATED:
        for entry_len, exit_len in DARVAS_WINDOWS:
            window_tag = f"{entry_len}-{exit_len}" + ("" if gated else " 1TF")
            stem = f"Turtle_w{darvas.WEEKLY_LEN}" if gated else "Turtle_1tf"
            base[("dv", window_tag)] = cached_signals(
                f"{stem}_{entry_len}_{exit_len}_all",
                lambda s, a=entry_len, b=exit_len, g=gated:
                    darvas.simulate(s, a, b, weekly=g))
    for pair_key in PAIR_TAGS:
        base[("pair", pair_key)] = cached_signals(
            f"EMA_{pair_key}_all", variant_builder(pair_key, SOLO_BAND))
    base[("e1", "daily")] = cached_signals(
        "EMA_daily_only_all",
        lambda s: backtest.simulate(s, band=SOLO_BAND, stack="daily"))
    base[("eath", "near-high")] = cached_signals(
        f"EMA_ath{ATH_BAND*100:g}_all",
        lambda s: backtest.simulate(s, band=SOLO_BAND, ath_band=ATH_BAND))
    print("    ema variants ready", flush=True)
    print("    turtle windows ready", flush=True)
    for hg_tag, hg_stop in HG_VARIANTS:
        base[("hg", hg_tag)] = cached_signals(
            f"HolyGrail_{hg_tag}_all",
            lambda s, st=hg_stop: holygrail.simulate(s, stop=st))
    print("    holy grail ready", flush=True)
    scale_lists = {
        ("ema", "half"): cached_signals("EMA_half_all",
                                        lambda s: backtest.simulate(s, scale_out="half")),
        ("ema", "half_be"): cached_signals("EMA_halfbe_all",
                                           lambda s: backtest.simulate(s, scale_out="half_be")),
    }

    # Nobody at class level follows the whole universe; ~10 is realistic. Draw 75 random
    # baskets (fixed seed) and promote the MEDIAN performer -- at a fixed
    # reference setting -- to a universe of its own, so every chart can be read
    # through it. Median, not best: picking the winner would be cherry-picking.
    import random
    rng = random.Random(20260823)
    symbols_all = sorted(cfg.all_symbols)
    baskets = [rng.sample(symbols_all, 10) for _ in range(BASKET_DRAWS)]
    scored = []
    for basket in baskets:
        members = set(basket)
        subset = [t for t in base[("ema", 0.02)] if t["symbol"] in members]
        scored.append((portfolio.run(subset, 100_000, 0.01)["cagr_pct"], basket))
    # A wiped basket has no CAGR at all, so it sorts BELOW every basket that merely
    # lost money -- which is where it belongs. (None of the 75 wipe at this setting,
    # verified 2026-08-31, so the chosen basket is unchanged by this ordering.)
    scored.sort(key=lambda x: (x[0] is not None, x[0]))
    median_cagr, median_basket = scored[len(scored) // 2]
    print(f"  10-stock universe (median of 75 draws, "
          f"{'wiped out' if median_cagr is None else f'{median_cagr:.1f}% CAGR'} "
          f"at the reference setting): {', '.join(sorted(median_basket))}", flush=True)

    # Counted, not typed. Seven stocks were removed from the universe on
    # 2026-08-31 (kitelab.config.EXCLUDED) and every label that said "199" would
    # otherwise have quietly gone on saying it.
    # In-sample and holdout splits dropped 2026-09-01. What is left is the two
    # questions actually being asked: the whole universe, and the 10 stocks you
    # would really hold. cfg.in_sample / cfg.out_of_sample still exist for
    # scripts that want them.
    # Liquidity buckets, so a rule can be judged on the stocks it would actually
    # be run on. Median daily traded value over each stock's whole history --
    # the same measure and the same cut points scripts.screen_universe uses to
    # admit a stock, so "mid" here means what it means there.
    turn = {}
    for sym in cfg.all_symbols:
        try:
            d = frames.daily(sym)
            v = (d["close"] * d["volume"]).to_numpy(float)
            v = v[v > 0]
            if len(v):
                turn[sym] = float(np.median(v))
        except SystemExit:
            pass
    small = {s for s, t in turn.items() if t < 5e7}
    mid = {s for s, t in turn.items() if 5e7 <= t < 25e7}
    print(f"  liquidity: {len(small)} small/micro, {len(mid)} mid, "
          f"{len(turn) - len(small) - len(mid)} large", flush=True)
    universes = {"all": (f"All {len(cfg.all_symbols)} stocks", None),
                 "b10": ("10 random stocks", set(median_basket)),
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
    total = (len(FILL_MODES) * len(sets[FILL_MODES[0][0]]) * len(universes)
             * len(RISKS) * len(CAPITALS) * len(START_YEARS))
    bar = Bar(total, "grid")
    for fkey, _flabel in FILL_MODES:
        execution(*FILL_SPEC[fkey])
        for (skey, band), signals in sets[fkey].items():
            for ukey, (ulabel, members) in universes.items():
                subset = (signals if members is None
                          else [t for t in signals if t["symbol"] in members])
                # Sorted once; each start year is a suffix of the one before, so
                # the slice is a bisect rather than a fresh filter per year.
                subset = sorted(subset, key=lambda t: t["entry_ts"])
                stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
                for year in START_YEARS:
                    cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
                    window = subset[cut:]
                    for risk in RISKS:
                        for capital in CAPITALS:
                            key = (f"{skey}|{tag(band)}|{ukey}|{risk:g}"
                                   f"|{capital}|{fkey}|{year}")
                            if not window:
                                grid[key] = None
                                bar.step()
                                continue
                            r = portfolio.run(window, capital, risk / 100)
                            grid[key] = run_payload(r)
                            bar.step()
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
    for (skey, band) in sets["0"]:
        for ukey in universes:
            for risk in RISKS:
                for capital in CAPITALS:
                    def cell(fkey):
                        k = (f"{skey}|{tag(band)}|{ukey}|{risk:g}"
                             f"|{capital}|{fkey}|{START_DEFAULT}")
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

    tradestats = {}
    for fkey, _flabel in FILL_MODES:
        for (skey, band), signals in sets[fkey].items():
            for ukey, (_, members) in universes.items():
                subset = (signals if members is None
                          else [t for t in signals if t["symbol"] in members])
                tradestats[f"{skey}|{tag(band)}|{ukey}|{fkey}"] = \
                    trade_stats(positions(subset))
    print("  universe trade metrics done", flush=True)

    scaleout = {}
    for skey in ("ema",):
        lists = {None: base[(skey, 0.02)], "half": scale_lists[(skey, "half")],
                 "half_be": scale_lists[(skey, "half_be")]}
        scaleout[skey] = {}
        for ukey, (_, members) in universes.items():
            rows = []
            for label, variant in SCALE_VARIANTS:
                trades = lists[variant]
                subset = (trades if members is None
                          else [t for t in trades if t["symbol"] in members])
                rows.append({"variant": label, **trade_stats(positions(subset))})
            scaleout[skey][ukey] = rows
        print(f"  scale-out: {skey} done", flush=True)

    # ---- the scale-out page: every rule at every multiple of R ----------
    # Trade level only, and it cannot be otherwise: portfolio.run prices a trade as
    # shares x ONE exit price, but a scale-out has two, and the banked leg never
    # reaches the trade record. Verified 2026-08-31 -- 1,522 of 1,523 banked trades
    # disagree with the account engine's formula. So no CAGR appears on that page.
    print("  scale-out R sweep:", flush=True)
    r_lists = {}
    for skey, builder in (("ema", lambda s, v, r: backtest.simulate(s, scale_out=v,
                                                                    scale_r=r)),):
        for variant, _vlabel in SCALE_RULES:
            for multiple in SCALE_MULTIPLES:
                if multiple == 1.0:
                    r_lists[(skey, variant, multiple)] = scale_lists[(skey, variant)]
                    continue          # the 1R runs are already cached under old names
                r_tag = f"{multiple:g}".replace(".", "p")
                name = ("EMA" if skey == "ema" else "Breakout")
                name += ("_half" if variant == "half" else "_halfbe") + f"_r{r_tag}_all"
                r_lists[(skey, variant, multiple)] = cached_signals(
                    name, lambda s, v=variant, r=multiple: builder(s, v, r))

    scaleout_r = {}
    for skey in ("ema",):
        for ukey, (_, members) in universes.items():
            def cut(trades):
                return (trades if members is None
                        else [t for t in trades if t["symbol"] in members])
            rows = [{"rule": "Keep the whole position", "variant": None,
                     "multiple": None,
                     **trade_stats(positions(cut(base[(skey, 0.02)])))}]
            keep = rows[0]["net"] or 1
            for variant, vlabel in SCALE_RULES:
                for multiple in SCALE_MULTIPLES:
                    stats = trade_stats(positions(cut(r_lists[(skey, variant, multiple)])))
                    stats.update(rule=vlabel, variant=variant, multiple=multiple,
                                 vs_keep=round(100 * (stats["net"] - keep) / abs(keep), 1))
                    rows.append(stats)
            scaleout_r[f"{skey}|{ukey}"] = rows
        print(f"    {skey} done", flush=True)

    # ---- how many stocks each rule needs ---------------------------------
    print("  breadth sweep:", flush=True)
    breadth = {}
    for skey in STRATEGY_LABELS:
        signals = base.get((skey, PRIMARY[skey]))
        if not signals:
            continue
        by_sym = {}
        for t in signals:
            by_sym.setdefault(t["symbol"], []).append(t)
        pool = sorted(cfg.all_symbols)
        sizes = [n for n in BREADTH_SIZES if n < len(pool)] + [len(pool)]
        rows = []
        for size in sizes:
            rng = random.Random(BREADTH_SEED + size)
            draws = ([pool] if size >= len(pool)
                     else [rng.sample(pool, size) for _ in range(BREADTH_DRAWS[size])])
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
    stock_bar = Bar(len(cfg.all_symbols), "per-stock")
    for index, symbol in enumerate(sorted(cfg.all_symbols), 1):
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
    # longer computed, so it is unused. ASSETS keeps its shape because
    # scripts/scaleout_test.py still reads it.
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
            builders = {"ema": lambda so=None: backtest.simulate(symbol, scale_out=so),
                        "qmw": lambda so=None: variant_builder("QMW")(symbol),
                        # Both of these need only daily bars, so they run on
                        # every instrument here, at the class's own settings.
                        "dv": lambda so=None: darvas.simulate(symbol, 20, 10),
                        "hg": lambda so=None: holygrail.simulate(symbol),
                        "e1": lambda so=None: backtest.simulate(
                            symbol, band=SOLO_BAND, stack="daily"),
                        "pair": lambda so=None: variant_builder(
                            PRIMARY["pair"], SOLO_BAND)(symbol),
                        "eath": lambda so=None: backtest.simulate(
                            symbol, band=SOLO_BAND, ath_band=ATH_BAND)}
            # (per-stock pages show the gated 20/10; the 1TF control lives in the
            #  grid, where it can be compared across every setting at once)
            for skey, build in builders.items():
                try:
                    trades = build()
                except (SystemExit, FileNotFoundError):
                    entry[skey] = None
                    continue
                r = portfolio.run(trades, 100_000, 0.01) if trades else None
                trades = positions(trades)
                entry[skey] = {"stats": trade_stats(trades), "trades": slim_trades(trades),
                               "account": run_payload(r) if r else None}
            # scale-out variants for the scale-out-capable strategy
            entry["scaleout"] = {}
            for skey in ("ema",):
                rows = []
                for label, variant in SCALE_VARIANTS:
                    try:
                        trades = builders[skey](variant)
                    except (SystemExit, FileNotFoundError):
                        trades = []
                    rows.append({"variant": label, **trade_stats(positions(trades))})
                entry["scaleout"][skey] = rows
            assets[symbol] = entry
        finally:
            backtest.FLAT_FEE_RATE = None
            sizing.FRACTIONAL = False
        print(f"    {symbol} done", flush=True)

    tf = {}
    # W/D/H dropped 2026-09-01; tf_compare still defines it for its own script.
    for key, label, desc in [v for v in TF_VARIANTS if v[0] != "WDH"]:
        rows, allt = [], []
        for symbol in ASSIGNED:
            w = window_start(symbol)
            kept = [t for t in simulate_variant(symbol, key)
                    if pd.Timestamp(t["entry_ts"]).normalize() >= w]
            allt += kept
            s = tf_summarise(kept)
            rows.append({"sym": symbol, **{k: round(v, 2) if isinstance(v, float) else v
                        for k, v in s.items() if k != "charges"}})
        s = tf_summarise(allt)
        rows.append({"sym": "ALL 5", **{k: round(v, 2) if isinstance(v, float) else v
                     for k, v in s.items() if k != "charges"}})
        tf[key] = {"label": label, "desc": desc, "rows": rows}
        print(f"  timeframes: {label} done", flush=True)

    # ---- the 10-stock reality check: Monte Carlo over random baskets -------
    # Nobody at class level tracks the whole universe; ~10 is realistic. Which 10 you
    # pick dominates the outcome, so we run the SAME account simulation on
    # many random baskets and report the distribution. Fixed Rs1,00,000
    # capital (class level); risk follows the slider; baskets identical
    # across strategies/risks so comparisons are apples-to-apples.
    basket10 = {}
    basket_bar = Bar(len(FILL_MODES) * len(sets[FILL_MODES[0][0]]) * len(RISKS)
                     * len(baskets), "baskets")
    for fkey, _flabel in FILL_MODES:
        execution(*FILL_SPEC[fkey])
        for (skey, band), signals in sets[fkey].items():
            by_sym = {}
            for t in signals:
                by_sym.setdefault(t["symbol"], []).append(t)
            for risk in RISKS:
                cagrs, dds = [], []
                for basket in baskets:
                    subset = [t for s in basket for t in by_sym.get(s, [])]
                    basket_bar.step()
                    if not subset:
                        continue
                    r = portfolio.run(subset, 100_000, risk / 100)
                    cagrs.append(None if r["cagr_pct"] is None
                                 else round(r["cagr_pct"], 1))
                    dds.append(round(r["max_drawdown_pct"], 1))
                # A wiped basket has no CAGR. It used to arrive here as 0.0, which
                # sorted it ABOVE every basket that merely lost money and kept it out
                # of the "negative %" count entirely -- so a distribution where 29 of
                # 75 baskets were destroyed reported a median of -35.5 and 61%
                # negative instead of wiped-out and 100% negative.
                cagrs_sorted = sorted(cagrs, key=lambda c: (c is not None, c))
                n = len(cagrs_sorted)
                alive = [c for c in cagrs if c is not None]
                n_wiped = n - len(alive)
                basket10[f"{skey}|{tag(band)}|{risk:g}|{fkey}"] = {
                    "cagrs": cagrs,
                    "wiped": n_wiped,
                    "median": cagrs_sorted[n // 2],
                    # the mean of a set containing a destroyed account is not a number
                    "mean": round(sum(alive) / len(alive), 1) if not n_wiped else None,
                    "p10": cagrs_sorted[n // 10],
                    "p90": cagrs_sorted[9 * n // 10],
                    "best": cagrs_sorted[-1], "worst": cagrs_sorted[0],
                    "beat_fd": round(100 * sum(1 for c in alive if c >= 7) / n),
                    # wiped counts as negative: it lost more than any survivor did
                    "negative": round(100 * (n_wiped
                                             + sum(1 for c in alive if c < 0)) / n),
                    "median_dd": sorted(dds)[len(dds) // 2],
                }
    basket_bar.close()
    execution(False, False)

    nifty = frames.daily("NIFTY 50")
    payload = {
        # IST, and labelled: the build machine may be on any clock.
        "built": config.now_local().strftime("%Y-%m-%d %H:%M IST"),
        "strategies": STRATEGY_LABELS,
        "universes": {k: v[0] for k, v in universes.items()},
        "universe_size": len(cfg.all_symbols),
        "excluded": cfg.excluded,
        "risks": RISKS, "capitals": CAPITALS,
        "start_years": START_YEARS, "start_default": START_DEFAULT, "bands": BANDS,
        "hg_tags": HG_TAGS,
        "pair_tags": PAIR_TAGS, "pair_labels": PAIR_LABEL,
        # index -> the date list every curve carrying that index shares
        "calendars": [list(k) for k, _ in sorted(_CALENDARS.items(), key=lambda kv: kv[1])],
        "darvas_windows": DARVAS_TAGS, "darvas_default": DARVAS_DEFAULT,
        "breadth": breadth, "tie_break": portfolio.TIE_BREAK,
        "fills": FILL_MODES, "participation": REALISTIC_PARTICIPATION,
        "assigned": list(ASSIGNED),
        "basket_members": sorted(median_basket),
        "grid": grid, "waterfall": waterfall, "tradestats": tradestats, "scaleout": scaleout,
        "scaleout_r": scaleout_r, "scale_multiples": SCALE_MULTIPLES,
        "stocks": stocks, "assets": assets,
        "timeframes": tf, "basket10": basket10, "nifty": close_series(nifty),
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
    OUT.write_text(json.dumps(payload))
    print(f"\n  written: {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")

    # Record WHICH UNIVERSE these numbers describe, in a small file beside the big
    # one. Without it the page can only say "built <date>", which is how a
    # dashboard built over 192 stocks went on being served after 91 of them were
    # excluded. dashboard_server.status() reads this and warns on the page.
    dashboard_server.write_stamp(cfg.all_symbols, payload["built"])
    print(f"  stamped: {dashboard_server.STAMP_PATH.name} "
          f"({len(cfg.all_symbols)} symbols)")


if __name__ == "__main__":
    main()

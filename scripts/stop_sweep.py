"""What is the stop actually worth? Sweep its DISTANCE across all nine rules.

    python3 -m scripts.stop_sweep --selfcheck    # prove the three mirrors
    python3 -m scripts.stop_sweep --pilot        # time it, print an estimate
    python3 -m scripts.stop_sweep [--quick]      # the run; --quick = default scenarios only

WHY. turtle_hold (NEXT_TESTS item 10) found the stop ends 77-82% of Turtle
trades and fixes the median holding period at 12 sessions whatever else the
rule says. The stop is the most consequential rule on the board -- and it is
the one rule that has never been varied. It has been "the entry candle's own
low", one definition, uniform across every strategy, since the project began.

THE STOP DOES TWO JOBS, AND THEY MUST BE SEPARATED.

    1. EXIT   -- it says when to sell.
    2. SIZE   -- sizing.position() divides the risk budget by the stop
                 DISTANCE, so a wider stop buys FEWER shares.

Widen the stop and both move at once: you get stopped out less often AND you
own less of the stock. A sweep that moves them together cannot say which
effect it measured. So `atr2` moves both (what a trader would actually do)
and `atr2_sizefix` moves only the exit, sizing off the old bar-low distance.
The gap between those two columns IS the sizing effect, read directly.

THE VARIANTS, pre-specified (NEXT_TESTS item 9: the best of N numbers is a
lucky number, so the list is fixed before the run and all of it is reported):

    own       the entry candle's low. The board's own rule. CONTROL.
    atr1/2/3  entry price minus k x ATR(14), k = 1, 2, 3. ATR -- average true
              range -- is the stock's own typical daily travel, so this is a
              stop scaled to how jumpy THIS stock is rather than to one
              arbitrary candle. The standard alternative.
    pct5/10   entry price minus a flat 5% / 10%. Scaled to nothing at all --
              the naive version, included because it is what most people
              actually use and it deserves a measurement rather than a sneer.
    none      no stop at all. Sells only on the rule's own exit. Sized off the
              bar low, since with no stop there is no risk distance to size
              from. The UPPER BOUND on what loosening the stop can buy.
    atr2_sizefix   exits at atr2, sizes at the bar low. See above.

HOW, without a rebuild. The three engines -- backtest.py (2 rules),
darvas.py (4) and timeframes.py (3) -- are MIRRORED here rather than edited,
exactly as scripts/turtle_hold.py mirrors darvas. timeframes.simulate_variant
already says it "mirrors backtest.simulate's walk exactly", and darvas differs
only in where the entry comes from, so one walk with three signal front-ends
covers all nine. --selfcheck asserts each mirror reproduces its rule's CACHED
trades field for field on the `own` stop before anything else runs, and the
`own` cells are then checked against dashboard.json's own grid.

Reads:  the signal caches (for the self-check only), the cleaned parquet,
        /data/clean/kitelab/dashboard.json, output/wf_attach_hold_<board>.pkl
Writes: output/stop_sweep_ckpt_<board>/*.pkl, output/stop_sweep_<date>.json,
        output/measurements/stop_sweep_<date>.csv, output/stop_sweep_curve.png
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import json
import pickle
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import (backtest, config, darvas, indicators, portfolio, registry,
                     signals, sizing, slippage, timeframes)
from kitelab.backtest import charges
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
from scripts.wf_pine import hold_cagrs, load_board, universes

OUT = Path(__file__).resolve().parent.parent / "output"
ATR_LEN = 14
SELFCHECK_SYMBOLS = 40

# (name, exit stop, sizing stop). A spec is ("low",) or ("atr", k) or ("pct", p)
# or ("none",); the sizing spec defaults to the exit spec.
VARIANTS = [
    ("own",          ("low",),      None),
    ("atr1",         ("atr", 1.0),  None),
    ("atr2",         ("atr", 2.0),  None),
    ("atr3",         ("atr", 3.0),  None),
    ("pct5",         ("pct", 0.05), None),
    ("pct10",        ("pct", 0.10), None),
    ("none",         ("none",),     ("low",)),
    ("atr2_sizefix", ("atr", 2.0),  ("low",)),
]


def stop_level(spec, low_i, atr_i, entry_price):
    """The stop LINE for one trade, in rupees. -inf means 'no stop'."""
    kind = spec[0]
    if kind == "low":
        return float(low_i)
    if kind == "none":
        return float("-inf")
    if kind == "atr":
        if not np.isfinite(atr_i) or atr_i <= 0:
            return float("nan")          # no ATR yet -- the trade is skipped
        return float(entry_price - spec[1] * atr_i)
    if kind == "pct":
        return float(entry_price * (1.0 - spec[1]))
    raise ValueError(f"unknown stop spec {spec!r}")


def atr_of(frame):
    """ATR on the frame the rule actually TRADES, not always the daily one.

    A weekly-traded rule's stop must be scaled to weekly travel; using daily
    ATR there would set a stop a fifth of the width the rule needs. shift(1) so
    the entry bar's own range is not used to place the entry bar's own stop --
    that would be reading the bar to decide what to do at its close.
    """
    a = indicators.atr(frame["high"], frame["low"], frame["close"], ATR_LEN)
    return a.shift(1).to_numpy(dtype=float)




# =========================================================== the mirrors ====
# One prep + one walk per engine. The three walks ARE nearly the same loop, and
# an earlier draft folded them into one function with four flags -- which hid
# the two places they genuinely differ (where the risk guard sits, and whether
# slippage.fill is applied). Written out, the differences are readable.
#
# Only the close-filled, non-intrabar branch is mirrored: NEXT_OPEN_FILLS False
# and stop_on_close True, which is what every number on the board was built at.

class Prep:
    """One symbol's arrays for one rule, computed once and reused by all 8 stops."""
    __slots__ = ("ts", "open", "high", "low", "close", "atr", "n",
                 "upper", "lower", "gate", "entry_ok", "exit_ok", "near_ath",
                 "top_tf_done", "months_done")


def prep_darvas(symbol, entry_len, exit_len, weekly):
    frame = darvas.channels(symbol, entry_len, exit_len)
    p = Prep()
    p.ts = pd.DatetimeIndex(frame["ts"])
    for c in ("open", "high", "low", "close"):
        setattr(p, c, frame[c].to_numpy(dtype=float))
    p.upper = frame["upper"].to_numpy(dtype=float)
    p.lower = frame["lower"].to_numpy(dtype=float)
    p.gate = (darvas.weekly_gate(frame) if weekly
              else np.ones(len(frame), dtype=bool))
    p.atr = atr_of(frame)
    p.n = len(frame)
    return p


def prep_ema(symbol, *, engine, stack=None, variant=None, ath_band=None):
    """backtest.py and timeframes.py differ in HOW the stack is read, not what.

    backtest.ema_stack_signal walks daily bars and carries the monthly/weekly
    state alongside; timeframes.stack_signal resamples to the pair's base and
    stamps on end_ts. Both hand back entry_ok / exit_ok / near_ath over the
    frame the rule trades, which is all the walk needs.
    """
    if engine == "backtest":
        signal = backtest.ema_stack_signal(symbol, backtest.EMA_LENGTH,
                                           registry.SOLO_BAND, stack, ath_band)
        stamps = signal["ts"]
        p_months = signal["months_done"].to_numpy(dtype=int)
    else:
        base, highers = timeframes._stack_frames(symbol, variant)
        signal = timeframes.stack_signal(base, highers, band=registry.SOLO_BAND,
                                         ath_band=ath_band)
        # end_ts, not ts: a Mon-Fri weekly bar is DECIDED on the Friday close.
        # Stamping it Monday let the account engine commit cash days before the
        # price it used existed -- timeframes.py carries the whole story.
        stamps = signal["end_ts"] if "end_ts" in signal.columns else signal["ts"]
        p_months = None
    p = Prep()
    p.months_done = p_months
    p.ts = pd.DatetimeIndex(stamps)
    for c in ("open", "high", "low", "close"):
        setattr(p, c, signal[c].to_numpy(dtype=float))
    p.entry_ok = signal["entry_ok"].to_numpy()
    p.exit_ok = signal["exit_ok"].to_numpy()
    p.near_ath = (signal["near_ath"].to_numpy(dtype=bool) if "near_ath" in signal.columns
                  else np.ones(len(signal), dtype=bool))
    p.top_tf_done = (signal["top_tf_done"].to_numpy(dtype=int)
                     if "top_tf_done" in signal.columns else np.zeros(len(signal), int))
    p.atr = atr_of(signal)
    p.n = len(signal)
    return p


def _stops(p, i, entry_price, exit_spec, size_spec):
    """(exit stop, sizing stop) for a signal at bar i, or None to skip the trade.

    Skipped when an ATR stop is asked for before ATR(14) has formed. That drops
    the first ~15 bars of every symbol for the atr* variants and for nobody
    else, so the variants do not see quite the same signals -- reported in the
    run's 'signals seen' column rather than hidden.
    """
    ex = stop_level(exit_spec, p.low[i], p.atr[i], entry_price)
    sz = ex if size_spec is None else stop_level(size_spec, p.low[i], p.atr[i], entry_price)
    if np.isnan(ex) or np.isnan(sz):
        return None
    return ex, sz


def walk_darvas(symbol, p, entry_len, exit_len, exit_spec, size_spec):
    """Mirrors darvas.simulate (kitelab/darvas.py:125) with the stop swapped."""
    trades, position = [], 0
    while position < p.n:
        if not np.isfinite(p.upper[position]) or not np.isfinite(p.lower[position]):
            position += 1
            continue
        if not p.gate[position]:              # weekly first; daily not consulted
            position += 1
            continue
        if not p.close[position] > p.upper[position]:
            position += 1
            continue

        entry_index = position
        entry_price = float(p.close[position])
        got = _stops(p, position, entry_price, exit_spec, size_spec)
        if got is None:
            position += 1
            continue
        stop, size_stop = got
        risk = entry_price - size_stop
        if risk <= 0:                         # darvas guards BEFORE the scan
            position += 1
            continue

        exit_at = None
        for step in range(position + 1, p.n):
            if p.close[step] <= stop:         # 1. the stop, first
                exit_at = (step, float(p.close[step]), "stop (close)")
                break
            line = p.lower[step]              # 2. the channel, trailing upward
            if not np.isfinite(line):
                continue
            if p.close[step] <= line:
                # darvas.py:210 hardcodes "10-candle low" whatever exit_len is,
                # so every 55-20 channel exit on the board is labelled for a
                # channel it did not use. Mirrored, not corrected: fixing
                # darvas.py costs a 4-strategy rebuild and nothing reads the
                # string back. Recorded in NEXT_TESTS item 10.
                exit_at = (step, float(p.close[step]), "10-candle low")
                break
        if exit_at is None:
            break

        exit_index, exit_price, reason = exit_at
        shares, risk_taken, capped = sizing.position(entry_price, size_stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue
        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, p.ts[entry_index], quoted_entry, +1)
        exit_price = slippage.fill(symbol, p.ts[exit_index], quoted_exit, -1)
        buy_value, sell_value = entry_price * shares, exit_price * shares
        gross = (exit_price - entry_price) * shares
        same_session = p.ts[entry_index].date() == p.ts[exit_index].date()
        cost = charges(buy_value, sell_value, intraday=same_session)
        trades.append({
            "symbol": symbol,
            "entry_ts": p.ts[entry_index], "exit_ts": p.ts[exit_index],
            "same_session": same_session, "entry_time": "EOD",
            "level": float(p.upper[position]),
            "level_kind": f"{entry_len}-candle high",
            "max_risk_capital": sizing.risk_budget(),
            "risk_taken": risk_taken, "capital_capped": capped,
            "range": risk, "target": None, "final_stop": stop,
            "bars_held": exit_index - entry_index,
            "charges_best": cost, "net_profit_best": gross - cost,
            "entry_date": p.ts[entry_index], "entry_price": entry_price,
            "quoted_entry": quoted_entry, "quoted_exit": quoted_exit,
            "spread_cost": ((quoted_exit - exit_price)
                            + (entry_price - quoted_entry)) * shares,
            "stop": stop, "risk_per_share": risk,
            "exit_date": p.ts[exit_index], "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (p.ts[exit_index] - p.ts[entry_index]).days,
            "sessions_held": exit_index - entry_index, "shares": shares,
            "cost_of_entry": buy_value, "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost, "net_profit": gross - cost,
        })
        position = exit_index + 1
    return trades


def walk_backtest(symbol, p, exit_spec, size_spec):
    """Mirrors backtest.simulate (kitelab/backtest.py:292), scale_out off."""
    trades, position = [], 0
    while position < p.n:
        fresh = p.entry_ok[position] and position > 0 and not p.entry_ok[position - 1]
        if not fresh or not p.near_ath[position]:
            position += 1
            continue
        entry_index = position
        entry_price = float(p.close[position])
        got = _stops(p, position, entry_price, exit_spec, size_spec)
        if got is None:
            position += 1
            continue
        stop, size_stop = got

        exit_at = None
        for step in range(position + 1, p.n):
            if p.close[step] <= stop:
                exit_at = (step, float(p.close[step]), "stop (close)")
                break
            if p.exit_ok[step]:
                exit_at = (step, float(p.close[step]), "ema break")
                break
        if exit_at is None:
            break

        exit_index, exit_price, reason = exit_at
        # backtest.py has NO pre-scan risk guard: it scans first and lets
        # sizing.position refuse, which costs the signal AND everything up to
        # the exit. Different from darvas on purpose -- mirrored, not unified.
        shares, risk_taken, capped = sizing.position(entry_price, size_stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue
        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, p.ts[entry_index], quoted_entry, +1)
        exit_price = slippage.fill(symbol, p.ts[exit_index], quoted_exit, -1)
        buy_value, sell_value = entry_price * shares, exit_price * shares
        gross = (exit_price - entry_price) * shares
        same_session = p.ts[entry_index].date() == p.ts[exit_index].date()
        cost = charges(buy_value, sell_value, intraday=same_session)
        risk = entry_price - stop
        trades.append({
            "symbol": symbol,
            "entry_ts": p.ts[entry_index], "exit_ts": p.ts[exit_index],
            "same_session": same_session, "entry_time": "EOD",
            "level": None, "level_kind": "ema stack",
            "max_risk_capital": sizing.risk_budget(),
            "risk_taken": risk_taken, "capital_capped": capped,
            "range": risk, "target": None, "final_stop": stop,
            "bars_held": exit_index - entry_index,
            "charges_best": cost, "net_profit_best": gross - cost,
            "entry_date": p.ts[entry_index], "entry_price": entry_price,
            "quoted_entry": quoted_entry, "quoted_exit": quoted_exit,
            "spread_cost": ((quoted_exit - exit_price)
                            + (entry_price - quoted_entry)) * shares,
            "stop": stop, "risk_per_share": risk,
            "exit_date": p.ts[exit_index], "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (p.ts[exit_index] - p.ts[entry_index]).days,
            "sessions_held": exit_index - entry_index, "shares": shares,
            "cost_of_entry": buy_value, "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost, "net_profit": gross - cost,
            "months_done": int(p.months_done[position]),
        })
        position = exit_index + 1
    return trades


def walk_pair(symbol, p, exit_spec, size_spec):
    """Mirrors timeframes.simulate_variant (kitelab/timeframes.py:172) + _padded.

    Two things this engine does NOT do, and both are deliberate upstream:
    it never calls slippage.fill (the half-spread is charged once, afterwards,
    by wf_attach.spread_of), and its share guard is `shares <= 0` with no
    FRACTIONAL clause. Mirrored exactly, or the `own` column would not
    reproduce the board.
    """
    trades, position = [], 0
    while position < p.n:
        fresh = p.entry_ok[position] and position > 0 and not p.entry_ok[position - 1]
        if not fresh or not p.near_ath[position]:
            position += 1
            continue
        entry_index = position
        entry_price = float(p.close[position])
        got = _stops(p, position, entry_price, exit_spec, size_spec)
        if got is None:
            position += 1
            continue
        stop, size_stop = got

        exit_at = None
        for step in range(position + 1, p.n):
            if p.close[step] <= stop:
                exit_at = (step, float(p.close[step]), "stop (close)")
                break
            if p.exit_ok[step]:
                exit_at = (step, float(p.close[step]), "ema break")
                break
        if exit_at is None:
            break

        exit_index, exit_price, reason = exit_at
        entry_ts, exit_ts = p.ts[entry_index], p.ts[exit_index]
        shares, risk_taken, capped = sizing.position(entry_price, size_stop)
        if shares <= 0:
            position = exit_index + 1
            continue
        gross = (exit_price - entry_price) * shares
        buy_value, sell_value = entry_price * shares, exit_price * shares
        same_session = entry_ts.date() == exit_ts.date()
        cost = charges(buy_value, sell_value, intraday=same_session)
        trades.append({
            "symbol": symbol, "entry_ts": entry_ts, "exit_ts": exit_ts,
            "entry_price": entry_price, "stop": stop, "exit_price": exit_price,
            "exit_reason": reason, "shares": shares, "risk_taken": risk_taken,
            "capital_capped": capped, "gross_profit": gross, "charges": cost,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "bars_held": exit_index - entry_index,
            "days_held": (exit_ts - entry_ts).days,
            "stop_pct": (entry_price - stop) / entry_price * 100,
            "top_tf_done": int(p.top_tf_done[position]),
            "same_session": same_session, "net_profit": gross - cost,
        })
        position = exit_index + 1
    return trades


# ============================================================= the rules ====
# The nine board rows, each wired to its own engine. Taken from registry.py
# rather than retyped: the label and cache name come from the registry object,
# so a row that moves on the board cannot silently stop matching here.

def _rules():
    out = []
    for s in registry.REGISTRY:
        v = str(s.variant)
        if s.key == "dv":
            entry_len, exit_len = (int(x) for x in v.split()[0].split("-"))
            weekly = "1TF" not in v
            out.append(dict(
                label=s.label, cache=s.cache, engine="darvas",
                prep=(lambda sym, a=entry_len, b=exit_len, g=weekly:
                      prep_darvas(sym, a, b, g)),
                walk=(lambda sym, p, es, ss, a=entry_len, b=exit_len:
                      walk_darvas(sym, p, a, b, es, ss))))
        elif s.key in ("ema", "e1", "eath") and s.module == "backtest.py":
            stack = "daily" if s.key == "e1" else "mwd"
            ath = registry.ATH_BAND if s.key == "eath" else None
            out.append(dict(
                label=s.label, cache=s.cache, engine="backtest",
                prep=(lambda sym, k=stack, a=ath:
                      prep_ema(sym, engine="backtest", stack=k, ath_band=a)),
                walk=walk_backtest))
        elif s.module == "timeframes.py":
            ath = registry.ATH_BAND if s.key == "eath" else None
            out.append(dict(
                label=s.label, cache=s.cache, engine="pair",
                prep=(lambda sym, k=v, a=ath:
                      prep_ema(sym, engine="pair", variant=k, ath_band=a)),
                walk=walk_pair))
        else:
            raise SystemExit(f"stop_sweep does not know how to mirror {s.label!r} "
                             f"({s.module}). Add it or take it off the board.")
    return out


RULES = _rules()


def _build_rule(rule, symbols, *, quiet=False):
    """Every variant of one rule, with the signal frame computed ONCE per symbol.

    Symbol-outer, variant-inner. The eight variants of a rule share the same
    entry signal -- only the stop differs -- and for EMA . daily only that
    signal is the expensive part, so recomputing it eight times would be most
    of the run. The prep object is dropped as soon as the symbol is done, so
    the memory held is one symbol's arrays plus every variant's trades.
    """
    out = {name: [] for name, _, _ in VARIANTS}
    skipped = 0
    for i, sym in enumerate(symbols, 1):
        try:
            p = rule["prep"](sym)
        except Exception:
            skipped += 1                      # missing or too-short history
            continue
        for name, exit_spec, size_spec in VARIANTS:
            out[name].extend(rule["walk"](sym, p, exit_spec, size_spec))
        if not quiet and i % 250 == 0:
            n = sum(len(v) for v in out.values())
            print(f"    {i}/{len(symbols)} symbols, {n:,} trades over "
                  f"{len(VARIANTS)} variants", flush=True)
    if not quiet:
        print(f"    {len(symbols) - skipped} symbols usable ({skipped} skipped)",
              flush=True)
    return out


def build_rule(rule, symbols, *, quiet=False):
    """_build_rule with the spread forced OFF, whatever the caller left on.

    The board's producers simulate with slippage.ENABLED False and charge the
    half-spread once afterwards via wf_attach.spread_of -- which sets ENABLED
    True and LEAVES IT ON. Simulate again after that and slippage.fill charges
    the spread a second time INSIDE the walk, which moves entry_price, so it
    moves the risk distance, so sizing.position returns a different share
    count. Not a rounding difference: a different trade. That cost half of the
    2026-09-16 turtle_hold run to find, so the invariant lives here, in the
    function that must not be got wrong, rather than in the caller's ordering.
    """
    was_enabled, was_cap = slippage.ENABLED, slippage.MAX_PARTICIPATION
    slippage.ENABLED, slippage.MAX_PARTICIPATION = False, None
    slippage.reset()
    try:
        return _build_rule(rule, symbols, quiet=quiet)
    finally:
        slippage.ENABLED, slippage.MAX_PARTICIPATION = was_enabled, was_cap
        slippage.reset()


# ============================================================ self-check ====
def selfcheck(symbols, universe):
    """`own` must reproduce each rule's CACHED trades field for field.

    Compared field by field, not on a summary: a harness that agrees on mean
    return and disagrees on which trades happened is not a mirror. Field SETS
    are compared explicitly too -- an earlier version of this check looped over
    the fields MINE had and skipped the rest, which would have passed a mirror
    that simply forgot half the trade dict.
    """
    # signals.load stamps the cache against the universe it was BUILT over, so
    # it must be asked for all 1,000 and the subset taken afterwards. Asking for
    # 12 gets "STALE -- built over a DIFFERENT UNIVERSE" and None, which reads
    # as "the mirror produced trades the board did not" when it means the
    # opposite. Costs nothing: load is a pickle read, not a rebuild.
    members = set(symbols)
    total = bad = 0
    for rule in RULES:
        cached = signals.load(rule["cache"] + "_all", universe)
        if cached is None:
            raise SystemExit(f"no usable cache for {rule['cache']}_all -- the board "
                             "and the code have drifted apart. Nothing below can "
                             "be checked against it; stop here.")
        cached = [t for t in cached if t["symbol"] in members]
        mine = build_rule(rule, symbols, quiet=True)["own"]
        key = lambda t: (t["symbol"], pd.Timestamp(t["entry_ts"]))
        a = {key(t): t for t in cached}
        b = {key(t): t for t in mine}
        if set(a) != set(b):
            print(f"  !! {rule['label']}: {len(a)} cached vs {len(b)} mine, "
                  f"{len(set(a) ^ set(b))} unmatched entries")
            bad += len(set(a) ^ set(b))
        for k in set(a) & set(b):
            ta, tb = a[k], b[k]
            total += 1
            if set(ta) != set(tb):
                print(f"  !! {rule['label']} {k}: fields differ "
                      f"{sorted(set(ta) ^ set(tb))}")
                bad += 1
                continue
            for f in ta:
                x, y = ta[f], tb[f]
                same = (x == y if not isinstance(x, float)
                        else (np.isnan(x) and np.isnan(y)) or abs(x - y) <= 1e-9)
                if not same:
                    print(f"  !! {rule['label']} {k}: {f} {x!r} != {y!r}")
                    bad += 1
                    break
    print(f"  mirror: {total:,} trades compared field by field over "
          f"{len(symbols)} symbols x {len(RULES)} rules, {bad} mismatches")
    return bad == 0


# ========================================================= the account ====
# The board's own grid loop, with the axes as arguments. Copied from
# wf_pine.cells_for (itself copied from wf_attach.cells_for) so all three
# agree; the ONLY change is that the four axes are parameters instead of
# module constants, because the full 300-cell grid times eight variants times
# nine rules does not fit in an evening. Called with the full axis lists it
# reproduces wf_pine.cells_for exactly -- asserted in check_against_board.

def cells_for(trades, unis, hold_cagr, label, *, years, prios, risks, capitals):
    cells = {}
    for ukey, members in unis.items():
        subset = (trades if members is None
                  else [t for t in trades if t["symbol"] in members])
        if not subset:
            continue
        subset = sorted(subset, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        live_years = dd.gridded_years(stamps)
        for year in years:
            cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
            window = subset[cut:] if year in live_years else []
            if not window:
                continue
            for prio in prios:
                for risk in risks:
                    for capital in capitals:
                        r = portfolio.run(window, capital, risk / 100, prio)
                        pay = dd.run_payload(r)
                        held = hold_cagr.get(f"{ukey}|{year}")
                        cells[f"{label}|{ukey}|{risk:g}|{capital}|1|{year}|{prio}"] = {
                            "variant": label, "universe": ukey, "risk": risk,
                            "capital": capital, "start": year, "priority": prio,
                            "cagr": pay["cagr"], "taken": pay["taken"],
                            "wiped": bool(pay["wiped"]), "hold_cagr": held,
                            "beats_hold": (None if (held is None or pay["wiped"]
                                                    or pay["cagr"] is None)
                                           else bool(pay["cagr"] > held)),
                        }
    return cells


def check_against_board(cells, payload, board_label, expect):
    """`own` must reproduce dashboard.json's OWN cells for this rule.

    The mirror check proves the trades; this proves the AXES -- that the grid
    loop, the universes, the hold curves and the spread are all being applied
    the way the board applied them. A mirror that is right about every trade
    and wrong about which universe they land in reports a confident wrong
    number, and nothing downstream would catch it.
    """
    # The board keys a cell by STRATEGY, `e1|daily|...`, not by the on-screen
    # label -- so the label this script carries has to be swapped for the
    # registry key before the lookup. Getting that wrong matches nothing, and
    # a check that matches nothing PASSES unless it is told how many cells it
    # owes. `expect` is that: a vacuous pass is a failure here.
    grid, hits, bad = payload["grid"], 0, 0
    for key, mine in cells.items():
        theirs = grid.get(board_label + key[key.index("|"):])
        if theirs is None or mine["cagr"] is None or theirs.get("cagr") is None:
            continue
        hits += 1
        if abs(float(theirs["cagr"]) - float(mine["cagr"])) > 0.005:
            if bad < 5:
                print(f"  !! {key}: board {theirs['cagr']} vs mine {mine['cagr']}")
            bad += 1
    print(f"  board: {hits} of {expect} `own` cells matched in dashboard.json, "
          f"{bad} disagree")
    if hits < expect:
        print(f"  !! {expect - hits} cells the board should have were not found -- "
              "the key format or the axes have drifted. This is a FAILURE, not a "
              "quiet pass.")
    return bad == 0 and hits >= expect


# =============================================================== reports ====
def trade_row(name, trades):
    """Trade-level summary -- what the rule does before any account constrains it."""
    if not trades:
        return None
    r = np.array([t["r_multiple"] for t in trades], dtype=float)
    sess = np.array([t["bars_held"] for t in trades], dtype=float)
    reasons = collections.Counter(t["exit_reason"] for t in trades)
    dist = np.array([100.0 * (t["entry_price"] - t["stop"]) / t["entry_price"]
                     for t in trades if np.isfinite(t["stop"])], dtype=float)
    return {
        "variant": name, "trades": len(trades),
        "exp_r": float(r.mean()), "win_pct": float(100.0 * (r > 0).mean()),
        "med_sessions": float(np.median(sess)),
        "stop_pct_of_exits": float(100.0 * reasons["stop (close)"] / len(trades)),
        "med_stop_distance_pct": float(np.median(dist)) if len(dist) else float("nan"),
        "charges": float(sum(t["charges"] for t in trades)),
    }


def account_row(name, cells):
    live = [c for c in cells.values()
            if c["cagr"] is not None and c["hold_cagr"] is not None and not c["wiped"]]
    if not live:
        return None
    exc = np.array([c["cagr"] - c["hold_cagr"] for c in live], dtype=float)
    cagr = np.array([c["cagr"] for c in live], dtype=float)
    return {
        "variant": name, "cells": len(cells), "compared": len(live),
        "med_cagr": float(np.median(cagr)),
        "med_excess": float(np.median(exc)),
        "beats_hold": int((exc > 0).sum()),
        "best_excess": float(exc.max()),
    }


def report(rows):
    """One block per rule: the eight stops side by side, trade level then account."""
    print("\n" + "=" * 94)
    print("HOW FAR SHOULD THE STOP BE? Eight stops, nine rules, one grid.\n")
    print("  'stop%' is the share of trades the STOP ended (the rest ended on the")
    print("  rule's own exit). 'dist%' is the median distance from entry to stop.")
    print("  'excess' is CAGR points a year over the same cells' equal-weight")
    print("  buy-and-hold; '>hold' counts cells above it. Nothing here has been")
    print("  through the daily-excess test or the BH bar -- these are measurements,")
    print("  not verdicts, and the comparison that carries weight is variant vs")
    print("  `own` WITHIN a rule, which is the same rule with only the stop moved.")
    for label, tr, ac in rows:
        print("\n" + "-" * 94)
        print(f"  {label}")
        print(f"  {'stop':<14}{'trades':>9}{'exp R':>8}{'win%':>7}{'stop%':>7}"
              f"{'dist%':>7}{'sess':>6}{'med CAGR':>10}{'excess':>9}{'>hold':>7}{'best':>7}")
        for t, a in zip(tr, ac):
            if t is None or a is None:
                continue
            print(f"  {t['variant']:<14}{t['trades']:>9,}{t['exp_r']:>8.3f}"
                  f"{t['win_pct']:>7.1f}{t['stop_pct_of_exits']:>7.1f}"
                  f"{t['med_stop_distance_pct']:>7.2f}{t['med_sessions']:>6.0f}"
                  f"{a['med_cagr']:>10.2f}{a['med_excess']:>9.2f}"
                  f"{a['beats_hold']:>7}{a['best_excess']:>7.2f}")
    print("\n" + "=" * 94)

    # The one cross-rule reading: does moving the stop help, rule by rule?
    print("\n  MOVING THE STOP, vs that rule's own `own` row (CAGR points a year)\n")
    names = [n for n, _, _ in VARIANTS]
    print(f"  {'rule':<38}" + "".join(f"{n:>13}" for n in names[1:]))
    print("  " + "-" * (38 + 13 * (len(names) - 1)))
    deltas = {n: [] for n in names[1:]}
    for label, tr, ac in rows:
        by = {a["variant"]: a for a in ac if a}
        if "own" not in by:
            continue
        base = by["own"]["med_excess"]
        line = f"  {label:<38}"
        for n in names[1:]:
            if n in by:
                d = by[n]["med_excess"] - base
                deltas[n].append(d)
                line += f"{d:>+13.2f}"
            else:
                line += f"{'--':>13}"
        print(line)
    print("  " + "-" * (38 + 13 * (len(names) - 1)))
    print(f"  {'MEDIAN OF THE 9 RULES':<38}"
          + "".join(f"{np.median(deltas[n]):>+13.2f}" if deltas[n] else f"{'--':>13}"
                    for n in names[1:]))
    print(f"  {'rules it HELPS, of 9':<38}"
          + "".join(f"{sum(d > 0 for d in deltas[n]):>13}" if deltas[n] else f"{'--':>13}"
                    for n in names[1:]))
    print("\n  atr2 vs atr2_sizefix is the sizing effect on its own: same exits,")
    print("  one sized off the ATR distance and one off the old bar low. The gap")
    print("  between those two columns is what the POSITION SIZE did, and the rest")
    print("  of atr2's move is what the EXIT did.\n")


# ================================================================== main ====
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selfcheck", action="store_true",
                    help="prove the nine mirrors against the caches and stop")
    ap.add_argument("--pilot", type=int, default=0,
                    help="run N rules only, to time the thing before committing")
    ap.add_argument("--quick", action="store_true",
                    help="start 2018 and mom_hi only (20 cells/variant, not 100)")
    ap.add_argument("--all-priorities", action="store_true",
                    help="all three priorities -- the board's full 300 cells")
    args = ap.parse_args()

    cfg = config.load()
    members = sorted(cfg.merged)
    print(f"\n  universe: {len(members)} symbols")
    print(f"  stops: {', '.join(n for n, _, _ in VARIANTS)}")

    print(f"\n  proving the nine mirrors on {SELFCHECK_SYMBOLS} symbols first")
    if not selfcheck(members[:SELFCHECK_SYMBOLS], members):
        raise SystemExit("  mirror self-check FAILED -- nothing below would mean "
                         "anything. Stop here.")
    if args.selfcheck:
        return

    years = [2018] if args.quick else dd.START_YEARS
    prios = dd.PRIORITIES if args.all_priorities else ["mom_hi"]
    print(f"  grid per variant: 5 universes x {len(years)} starts x {len(prios)} "
          f"priorities x 2 risks x 2 capitals = {5*len(years)*len(prios)*4} cells")
    if prios == ["mom_hi"]:
        print("  (priority pinned to the board's default. It is the smallest of the"
              "\n   three axes -- median 6.7 CAGR points best-to-worst against 24.35"
              "\n   across strategies -- and pinning it is better design than"
              "\n   averaging over it: every variant then meets the same scenarios.)")

    payload = load_board()
    unis = universes(set(members), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(members), unis, [], board)
    hold_cagr = hold_cagrs(holds)
    print(f"  hold CAGR by cell: {len(hold_cagr)} entries, "
          f"all|2018 = {hold_cagr.get('all|2018')}")

    ckpt = OUT / f"stop_sweep_ckpt_{board}"
    ckpt.mkdir(parents=True, exist_ok=True)
    rules = RULES[:args.pilot] if args.pilot else RULES
    rows, flat, t_start = [], [], time.time()

    for rule in rules:
        label = rule["label"]
        cache = ckpt / (rule["cache"] + ".pkl")
        if cache.exists():
            tr, ac, cells_by = pickle.loads(cache.read_bytes())
            print(f"\n  {label}: reusing checkpoint")
        else:
            print(f"\n  {label}: building {len(VARIANTS)} variants", flush=True)
            t0 = time.time()
            built = build_rule(rule, members)
            print(f"    built in {time.time()-t0:.0f}s: "
                  + ", ".join(f"{k} {len(v):,}" for k, v in built.items()), flush=True)
            tr, ac, cells_by = [], [], {}
            for name, _, _ in VARIANTS:
                t0 = time.time()
                priced = wa.spread_of(built[name])
                cells = cells_for(priced, unis, hold_cagr, vlabel(label, name),
                                  years=years, prios=prios,
                                  risks=dd.RISKS, capitals=dd.CAPITALS)
                if name == "own":
                    st = registry.REGISTRY[[r["label"] for r in RULES].index(label)]
                    v = f"{st.variant:g}" if isinstance(st.variant, float) else st.variant
                    if not check_against_board(cells, payload, f"{st.key}|{v}", len(cells)):
                        raise SystemExit(f"  {label}: `own` does not reproduce the "
                                         "board. Everything else is unreadable. Stop.")
                tr.append(trade_row(name, built[name]))
                ac.append(account_row(name, cells))
                cells_by[name] = cells
                print(f"    {name:<14}{len(built[name]):>8,} trades -> "
                      f"{len(cells):>4} cells  {time.time()-t0:>5.0f}s", flush=True)
                built[name] = None            # let the trade list go; memory is 7 GB
            cache.write_bytes(pickle.dumps((tr, ac, cells_by)))
        rows.append((label, tr, ac))
        for name, cells in cells_by.items():
            for key, c in cells.items():
                flat.append(dict(rule=label, stop=name, key=key, **c))

    print(f"\n  {len(flat):,} cells in {(time.time()-t_start)/60:.1f} min")
    stamp = date.today().isoformat()
    (OUT / f"stop_sweep_{stamp}.json").write_text(json.dumps(
        {"built": payload["built"], "board": board, "variants": [n for n, _, _ in VARIANTS],
         "years": years, "priorities": prios, "rows": [
             {"rule": l, "trade": t, "account": a} for l, t, a in rows]}, indent=1))
    mdir = OUT / "measurements"
    mdir.mkdir(exist_ok=True)
    with open(mdir / f"stop_sweep_{stamp}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat[0]))
        w.writeheader()
        w.writerows(flat)
    print(f"  wrote stop_sweep_{stamp}.json and measurements/stop_sweep_{stamp}.csv "
          f"({len(flat)} rows x {len(flat[0])} columns)")
    report(rows)


def vlabel(label, name):
    """`EMA · M/W` + `atr2` -> `EMA · M/W atr2`; `own` keeps the board's own label."""
    return label if name == "own" else f"{label} {name}"


if __name__ == "__main__":
    main()

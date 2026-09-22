"""The two "+ weekly" Turtles held longer, WITH THE STOP KEPT, on the real board.

    python3 -m scripts.turtle_hold --selfcheck     # prove the mirror, ~1 min
    python3 -m scripts.turtle_hold --pilot 60      # time it, print an estimate
    python3 -m scripts.turtle_hold                 # the full run

WHY THIS EXISTS. scripts/hold_longer.py (2026-09-16, NEXT_TESTS item 9) took each
rule's own entries, DISCARDED its exit and sold at a fixed horizon. At the board's
own Rs 1cr book the two "+ weekly" Turtles held ~120 sessions came out near 20%/yr
against a 14.09% buy-and-hold, where their own exits managed 0 of 9 over hold.
That was a probe, not a strategy, and it was missing three things it said so:

    (a) THE STOP, deleted by construction -- per-trade loss was uncapped;
    (b) BREADTH and CASH -- the rate assumed the next trade starts the day this
        one ends, which one pot of money cannot do at a 120-session hold;
    (c) it was the best of 90 numbers.

This fixes (a) and (b). The stop is back, and every trade list goes through
portfolio.run on the board's own 300 scenarios, so cash constrains, signals get
refused and breadth is priced. (c) is handled by PRE-SPECIFYING the variants
below and reporting all of them, winners and losers alike.

WHAT IS AND IS NOT CHANGED. The entry is untouched: the same 20/55-candle
breakout behind the same weekly gate, read at the same closes. The STOP is
untouched: the entry candle's own low, the one rule every strategy here obeys.
Only the SECOND exit -- darvas's trailing channel -- is replaced, four ways:

    base    the rule exactly as it sits on the board. The control.
    t120    channel dropped; sell after 120 sessions or at the stop, whichever
            first. 120 is hold_longer's argmax for BOTH rules -- taken from that
            run, not re-searched here.
    t250    the same at 250 sessions, to show whether it degrades.
    slow    channel KEPT but widened 3x (10 -> 30, 20 -> 60). A trailing rule
            rather than a calendar, and the only variant here a person could
            plausibly trade.

WHY NOT EDIT kitelab/darvas.py. It is a producer module in the signal stamp:
a one-character change there invalidates four strategies' caches and costs a
rebuild. simulate_variant() below MIRRORS darvas.simulate line for line, and
--selfcheck asserts the mirror reproduces it TRADE FOR TRADE on the baseline
parameters before anything else runs. The `base` cells are then checked against
dashboard.json's own grid, so the harness is proved to land on the board's axes
the way wf_attach.SELF_CHECK_KEY does.

Reads:  /data/clean/kitelab/dashboard.json      (axes, universe buckets, hold key)
        the cleaned parquet candles, via kitelab.frames
        output/wf_attach_hold_<board key>.pkl   (hold curves, reused if built)
Writes: output/turtle_hold_ckpt_<board key>/*.pkl   (one per variant, resumable)
        output/measurements/turtle_hold_<date>.json             (every cell)
        output/measurements/turtle_hold_<date>.csv (the same, flat)
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import backtest, config, darvas, sizing, slippage
from kitelab.backtest import charges
import scripts.wf_attach as wa
from scripts.wf_pine import cells_for, hold_cagrs, load_board, universes

OUT = Path(__file__).resolve().parent.parent / "output"
MEAS = OUT / "measurements"        # every finished CSV or JSON result
MEAS.mkdir(parents=True, exist_ok=True)

# (board label, entry_len, exit_len). The two rules the user asked for; both are
# registry entries `dv|20-10` and `dv|55-20`, weekly gate ON.
RULES = [("dv|20-10", 20, 10), ("dv|55-20", 55, 20)]

# (suffix, max_hold, exit_len multiplier). PRE-SPECIFIED -- see the docstring.
# None max_hold = no time exit; None multiplier = no channel exit at all.
VARIANTS = [("base", None, 1), ("t120", 120, None), ("t250", 250, None),
            ("slow", None, 3)]

SELFCHECK_SYMBOLS = 40      # symbols the mirror is proved on
SELFCHECK_TOL = 1e-9        # CAGR points, on the board comparison


def simulate_variant(symbol, entry_len, exit_len, *, max_hold=None,
                     use_channel=True, channel_mult=1):
    """darvas.simulate, mirrored, with the SECOND exit under our control.

    Everything before the exit loop -- the channel construction, the weekly gate,
    the breakout test, the stop, the risk guard, the sizing, the fill, the fee
    convention, the trade dict -- is darvas.simulate's, deliberately unchanged so
    the only difference between `base` and the board is nothing at all. The two
    parameters that DO differ:

      use_channel=False   drop the trailing channel exit entirely
      max_hold=N          sell on the Nth session after entry if nothing else has

    THE STOP IS CHECKED FIRST in every variant, exactly as darvas does it, so a
    bar that breaks both the stop and the time limit is charged the stop -- the
    worse of the two, which is the honest one.

    Only the non-intrabar, close-filled reading is mirrored, because that is the
    one the board runs; intrabar and NEXT_OPEN_FILLS are refused rather than
    half-implemented.
    """
    if backtest.NEXT_OPEN_FILLS:
        raise SystemExit("turtle_hold mirrors the close-filled reading only; "
                         "NEXT_OPEN_FILLS is on -- run it through wf_attach arm B")
    frame = darvas.channels(symbol, entry_len, exit_len * channel_mult)
    gate = darvas.weekly_gate(frame)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    upper = frame["upper"].to_numpy(dtype=float)
    lower = frame["lower"].to_numpy(dtype=float)
    stamps = frame["ts"].tolist()
    total = len(frame)

    trades: list[dict] = []
    position = 0
    while position < total:
        if not np.isfinite(upper[position]) or not np.isfinite(lower[position]):
            position += 1
            continue
        if not gate[position]:
            position += 1
            continue
        if not close[position] > upper[position]:
            position += 1
            continue

        stop = float(low[position])
        entry_index = position
        entry_price = float(close[position])
        risk = entry_price - stop
        if risk <= 0:
            position += 1
            continue

        exit_at = None
        limit = total if max_hold is None else min(total, entry_index + max_hold + 1)
        for step in range(position + 1, limit):
            # 1. the stop, first, so a bar that breaks two rules pays the worse
            if close[step] <= stop:
                exit_at = (step, float(close[step]), "stop (close)")
                break
            # 2. the trailing channel, if this variant still has one
            if use_channel:
                line = lower[step]
                if np.isfinite(line) and close[step] <= line:
                    # darvas.py:210 hardcodes "10-candle low" whatever exit_len
                    # is, so every dv|55-20 channel exit on the board is labelled
                    # for a channel it did not use. Cosmetic -- nothing reads the
                    # string back -- and MIRRORED here rather than corrected,
                    # because fixing darvas.py costs a 4-strategy rebuild. It is
                    # the same shape as the three bugs in NEXT_TESTS: an
                    # identifier that names less than it needs to.
                    exit_at = (step, float(close[step]), "10-candle low"
                               if channel_mult == 1
                               else f"{exit_len * channel_mult}-candle low")
                    break
            # 3. the time exit, LAST, so it never pre-empts a risk rule
            if max_hold is not None and step - entry_index >= max_hold:
                exit_at = (step, float(close[step]), f"{max_hold}-session limit")
                break

        if exit_at is None:
            break                      # still open at the end of the data

        exit_index, exit_price, reason = exit_at
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
            "level": float(upper[position]),
            "level_kind": f"{entry_len}-candle high",
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


def vlabel(rule, suffix):
    """`dv|20-10` + `t120` -> `dv|20-10 t120`; `base` keeps the board's own key."""
    return rule if suffix == "base" else f"{rule} {suffix}"


def build_all(symbols, entry_len, exit_len, *, max_hold, use_channel, channel_mult,
              quiet=False):
    """Build a variant's trades with the spread OFF, whatever the caller left on.

    The board's producers simulate with slippage.ENABLED False and charge the
    half-spread afterwards, once, via wf_attach.spread_of -- which sets
    ENABLED True and LEAVES IT ON. Build a second variant after that and
    slippage.fill charges the spread again inside the simulation, which moves
    entry_price, so it moves `risk`, so sizing.position returns a different
    share count: not a rounding difference, a different trade. In the first
    full run dv|20-10 `base` was built before any spread_of and reproduced the
    board exactly; every variant after it was silently double-charged, and
    dv|55-20 `base` missed 275 of its 276 cells by up to 2.6 CAGR points.

    So the invariant lives HERE rather than in the caller's ordering. Global
    execution state that a loop has to remember to reset is state that will be
    forgotten on the day a variant is added in the wrong place.
    """
    was_enabled, was_cap = slippage.ENABLED, slippage.MAX_PARTICIPATION
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    try:
        return _build_all(symbols, entry_len, exit_len, max_hold=max_hold,
                          use_channel=use_channel, channel_mult=channel_mult,
                          quiet=quiet)
    finally:
        slippage.ENABLED, slippage.MAX_PARTICIPATION = was_enabled, was_cap
        slippage.reset()


def _build_all(symbols, entry_len, exit_len, *, max_hold, use_channel, channel_mult,
               quiet=False):
    out, skipped = [], 0
    for i, sym in enumerate(symbols, 1):
        try:
            t = simulate_variant(sym, entry_len, exit_len, max_hold=max_hold,
                                 use_channel=use_channel, channel_mult=channel_mult)
        except SystemExit:
            raise
        except Exception as exc:                      # noqa: BLE001
            print(f"    {sym}: {type(exc).__name__}: {exc}")
            skipped += 1
            continue
        out.extend(t)
        if not quiet and i % 250 == 0:
            print(f"    {i}/{len(symbols)} symbols, {len(out):,} trades", flush=True)
    if not quiet:
        print(f"    {len(symbols) - skipped} symbols usable ({skipped} skipped) "
              f"-> {len(out):,} trades")
    return out


# ------------------------------------------------------------- self-check ----
def selfcheck(symbols):
    """The mirror must reproduce darvas.simulate TRADE FOR TRADE on `base`.

    Compared field by field rather than on a summary statistic: a harness that
    agrees on mean return and disagrees on which trades happened is not a mirror.
    """
    slippage.ENABLED = False          # build_all guards itself; darvas.simulate
    slippage.MAX_PARTICIPATION = None  # does not, and both sides must be bare
    slippage.reset()
    checked = mismatch = 0
    for rule, entry_len, exit_len in RULES:
        for sym in symbols:
            want = darvas.simulate(sym, entry_len, exit_len, intrabar=False,
                                   weekly=True)
            got = simulate_variant(sym, entry_len, exit_len)
            if len(want) != len(got):
                print(f"    {rule} {sym}: {len(want)} trades vs {len(got)}")
                mismatch += 1
                continue
            for a, b in zip(want, got):
                checked += 1
                diff = [k for k in a
                        if not (a[k] == b[k]
                                or (isinstance(a[k], float) and isinstance(b[k], float)
                                    and np.isclose(a[k], b[k], rtol=0, atol=1e-9)))]
                if diff:
                    print(f"    {rule} {sym} {a['entry_ts']}: fields differ {diff}")
                    mismatch += 1
    print(f"  mirror: {checked:,} trades compared field by field over "
          f"{len(symbols)} symbols x {len(RULES)} rules, {mismatch} mismatches")
    if mismatch:
        raise SystemExit("  SELF-CHECK FAILED -- simulate_variant is not a mirror "
                         "of darvas.simulate; every number below would be untrusted")
    return True


def check_against_board(cells, payload):
    """`base` must reproduce dashboard.json's own cells for these two rules.

    The mirror check proves the trades; this proves the AXES -- that the grid
    loop, the universes, the priorities and the hold keys line up with the board
    the numbers will be compared against. Same idea as wf_attach.SELF_CHECK_KEY.
    """
    grid, seen, bad = payload["grid"], 0, []
    for key, cell in cells.items():
        want = grid.get(key)
        if not want:
            continue
        seen += 1
        a, b = cell.get("cagr"), want.get("cagr")
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > SELFCHECK_TOL:
            bad.append(f"{key}: {a} vs board {b}")
    print(f"  board: {seen} of {len(cells)} `base` cells found in dashboard.json, "
          f"{len(bad)} disagree")
    if seen == 0:
        raise SystemExit("  SELF-CHECK FAILED -- not one `base` cell key matched "
                         "the board; the harness is not on the board's axes")
    if bad:
        raise SystemExit("  SELF-CHECK FAILED -- `base` does not reproduce the "
                         "board:\n    " + "\n    ".join(bad[:10]))
    return seen


def pilot(symbols, n):
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    sample = symbols[:n]
    for rule, entry_len, exit_len in RULES:
        for suffix, max_hold, mult in VARIANTS:
            t0 = time.time()
            t = build_all(sample, entry_len, exit_len, max_hold=max_hold,
                          use_channel=mult is not None,
                          channel_mult=mult or 1, quiet=True)
            dt = time.time() - t0
            per = dt / len(sample)
            print(f"  {vlabel(rule, suffix):<20} {len(t):>6,} trades  "
                  f"{dt:>6.1f}s on {len(sample)} symbols  -> "
                  f"{per * len(symbols) / 60:>5.1f} min on {len(symbols)}")
    print("\n  Trade building only. The account layer (300 portfolio.run calls per "
          "variant)\n  is the other half -- time it with one real variant before "
          "quoting a total.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--pilot", type=int, metavar="N")
    ap.add_argument("--symbols", type=int, help="cap the universe (testing only)")
    args = ap.parse_args()

    cfg = config.load()
    members = sorted(cfg.merged)
    if args.symbols:
        members = members[:args.symbols]
    print(f"\nuniverse: {len(members)} symbols")

    if args.selfcheck:
        selfcheck(members[:SELFCHECK_SYMBOLS])
        return
    if args.pilot:
        pilot(members, args.pilot)
        return

    print(f"\n  proving the mirror on {SELFCHECK_SYMBOLS} symbols first")
    selfcheck(members[:SELFCHECK_SYMBOLS])

    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()

    payload = load_board()
    unis = universes(set(members), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(members), unis, [], board)
    hold_cagr = hold_cagrs(holds)
    print(f"  hold CAGR by cell: {len(hold_cagr)} entries, "
          f"all|2018 = {hold_cagr.get('all|2018')}")

    ck = OUT / f"turtle_hold_ckpt_{board}"
    ck.mkdir(parents=True, exist_ok=True)
    all_cells, summary = {}, {}
    t0 = time.time()
    for rule, entry_len, exit_len in RULES:
        for suffix, max_hold, mult in VARIANTS:
            label = vlabel(rule, suffix)
            path = ck / f"{label.replace('|', '__').replace(' ', '_')}.pkl"
            if path.exists():
                cells, stats = pickle.loads(path.read_bytes())
                print(f"  {label:<20} reused {len(cells)} cells")
            else:
                s0 = time.time()
                print(f"  {label}: building trades", flush=True)
                raw = build_all(members, entry_len, exit_len, max_hold=max_hold,
                                use_channel=mult is not None, channel_mult=mult or 1)
                priced = wa.spread_of(raw)
                stats = trade_summary(priced)
                cells = cells_for(priced, unis, holds, hold_cagr, label)
                if suffix == "base":
                    check_against_board(cells, payload)
                path.write_bytes(pickle.dumps((cells, stats)))
                print(f"  {label:<20} {stats['n']:>7,} trades -> {len(cells):>4} "
                      f"cells  {time.time() - s0:>6.0f}s", flush=True)
            all_cells.update(cells)
            summary[label] = stats
    print(f"\n  {len(all_cells)} cells in {(time.time() - t0) / 60:.1f} min")

    stamp = date.today().isoformat()
    blob = {"built": payload["built"], "board": board, "generated": stamp,
            "rules": [r for r, _, _ in RULES],
            "variants": [vlabel(r, s) for r, _, _ in RULES for s, _, _ in VARIANTS],
            "summary": summary, "cells": all_cells}
    jpath = MEAS / f"turtle_hold_{stamp}.json"
    jpath.write_text(json.dumps(blob, indent=1, default=str))
    flat = pd.DataFrame([
        {k: v for k, v in c.items() if k != "daily"}
        | {f"daily_{k}": v for k, v in (c["daily"] or {}).items()}
        for c in all_cells.values()])
    cpath = OUT / "measurements" / f"turtle_hold_{stamp}.csv"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    flat.to_csv(cpath, index=False)
    print(f"  wrote {jpath.name} and measurements/{cpath.name} "
          f"({len(flat)} rows x {len(flat.columns)} columns)")
    report(flat, summary)


def trade_summary(trades):
    """Trade-level shape, before any account constrains it."""
    if not trades:
        return {"n": 0}
    r = np.array([t["r_multiple"] for t in trades], dtype=float)
    s = np.array([t["sessions_held"] for t in trades], dtype=float)
    reasons = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return {"n": len(trades), "expectancy_r": float(np.mean(r)),
            "win_pct": float(100.0 * (r > 0).mean()),
            "median_sessions": float(np.median(s)),
            "mean_sessions": float(np.mean(s)),
            "charges": float(sum(t["charges"] for t in trades)),
            "exit_reasons": reasons}


def report(flat, summary):
    print("\n" + "=" * 86)
    print("TRADE LEVEL -- before any account constrains it\n")
    print(f"  {'variant':<20}{'trades':>9}{'exp R':>8}{'win%':>7}{'med sess':>10}"
          f"{'charges Rs':>14}")
    print("  " + "-" * 84)
    for label, s in summary.items():
        if not s.get("n"):
            continue
        print(f"  {label:<20}{s['n']:>9,}{s['expectancy_r']:>8.3f}"
              f"{s['win_pct']:>7.1f}{s['median_sessions']:>10.0f}"
              f"{s['charges']:>14,.0f}")

    print("\n" + "=" * 86)
    print("ACCOUNT LEVEL -- the board's own 300 scenarios per variant\n")
    print(f"  {'variant':<20}{'cells':>7}{'med CAGR':>10}{'med hold':>10}"
          f"{'med excess':>12}{'>hold':>8}{'best':>8}")
    print("  " + "-" * 84)
    for label in summary:
        sub = flat[flat["variant"] == label]
        if sub.empty:
            continue
        ok = sub.dropna(subset=["cagr", "hold_cagr"])
        exc = ok["cagr"] - ok["hold_cagr"]
        print(f"  {label:<20}{len(sub):>7}{ok['cagr'].median():>10.2f}"
              f"{ok['hold_cagr'].median():>10.2f}{exc.median():>12.2f}"
              f"{int((exc > 0).sum()):>8}{exc.max():>8.2f}")
    print("\n  'med excess' is CAGR points a year over the on-screen equal-weight")
    print("  buy-and-hold, median over the variant's cells. '>hold' counts cells")
    print("  ABOVE it -- out of the cells with a hold to compare against.")
    print("\n  A count is not a verdict. These cells are not independent (300")
    print("  scenarios over 2 rules), nothing here has been through the daily-excess")
    print("  test or the BH bar, and `slow`/`t120`/`t250` were chosen from")
    print("  hold_longer's table -- so the comparison that matters is against")
    print("  `base`, which is the same rule with only the exit changed.\n")


if __name__ == "__main__":
    main()

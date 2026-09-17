"""Does `xrank` survive the account layer? Price item 20's best cell properly.

    python3 -m scripts.xrank_account --selfcheck   # prove the mirror, stop
    python3 -m scripts.xrank_account [--quick]     # the run

WHY. Item 20 found `xrank x stop` the best of 216 trade-level cells: +4.52
bp/session over buy-and-hold at a 3xATR stop, with cells beating hold going
2 -> 13 of 72 as the stop widened. I read that as "the rules were never bad,
they were strangled" -- and item 11's SAVED account-level sweep says the
opposite for the board's nine rules: widening the stop takes cells beating
hold from 139 to 100 of 828, because it lifts the middle of the distribution
while collapsing the right tail.

That contradiction is not yet resolved for THESE rules. entry_exit_grid.py has
no account layer at all: no cash constraint, no position sizing, no limit on
how many positions can be open at once. It scores every trade as if the money
were unlimited. This script takes the identical trade list and runs it through
`portfolio.run`, the same account simulator the board uses, on the board's own
scenarios, so the two numbers differ ONLY by the account layer.

WHAT xrank IS. On each session, rank every listed stock by its trailing
252-session return and fire on the top 10%. "Cross-sectional momentum" -- it
buys what has gone up most relative to everything else, rather than against
any threshold of its own. It is the only rule in item 19's nine that looks at
other stocks, and it was the most distinct from the board (max phi 0.157).

THE EXIT is `stop`: hold until the stop is hit, up to MAXHOLD sessions. Three
stop lines, exactly item 20's: the entry candle's low (`own`, the board's
incumbent) and entry close minus 2 or 3 x ATR(14).

NO SIGNAL IS RE-DERIVED HERE. panels, build_signals, exits_for_symbol and walk
are IMPORTED from scripts.entry_exit_grid, so the trade list cannot drift from
the one that produced +4.52. --selfcheck asserts the trade count per stop
against the saved grid CSV before anything is priced.

TWO TRAPS THIS SCRIPT OWNS.
  1. wf_attach.spread_of sets slippage.ENABLED True and leaves it on. Nothing
     here simulates after pricing, but build_all guards it anyway in a finally
     -- the invariant lives with the builder (item 10's bug, hit 3x).
  2. portfolio.py:564 computes per-share risk as entry_price - trade["stop"],
     so `stop` is a SIZING input to the account, not just a record of the exit
     line. A wider stop therefore buys FEWER shares here, automatically. That
     is the real trade-off and it is exactly what item 20 could not see.

Reads:  the cleaned daily parquet (via kitelab.frames), /data/clean/kitelab/
        dashboard.json, output/wf_attach_hold_<board>.pkl,
        output/measurements/entry_exit_grid_2026-09-17.csv (self-check only)
Writes: output/xrank_account_<date>.json,
        output/measurements/xrank_account_<date>.csv,
        output/xrank_account_<date>.png, output/logs/ (by the caller)
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config, sizing, slippage
from kitelab.backtest import charges
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
import scripts.entry_exit_grid as G
from scripts.stop_sweep import cells_for
from scripts.wf_pine import hold_cagrs, load_board, universes

OUT = Path(__file__).resolve().parent.parent / "output"
GRID_CSV = OUT / "measurements" / "entry_exit_grid_2026-09-17.csv"
ENTRY = "xrank"
EXIT = "stop"
STOPS = ["own", "atr2", "atr3"]


@contextlib.contextmanager
def spread_off():
    """The board's producers simulate with the spread OFF.

    wf_attach.spread_of charges the half-spread once afterwards and LEAVES
    slippage.ENABLED True. Simulate again after that and the spread is charged
    a second time inside slippage.fill, which moves entry_price, which moves
    the risk distance, which makes sizing.position return a different share
    count -- a different trade, not a rounding difference. Item 10's bug, hit
    three times. The invariant lives with the builder, not the caller.
    """
    was_enabled, was_cap = slippage.ENABLED, slippage.MAX_PARTICIPATION
    slippage.ENABLED, slippage.MAX_PARTICIPATION = False, None
    slippage.reset()
    try:
        yield
    finally:
        slippage.ENABLED, slippage.MAX_PARTICIPATION = was_enabled, was_cap
        slippage.reset()


def build_all(members, *, quiet=False):
    """Every stop variant of xrank x stop, as board-shaped trade dicts."""
    with spread_off():
        p = G.panels(members)
        sig = G.build_signals(p)[ENTRY]
        print(f"  signal panel {sig.shape[0]:,} sessions x {sig.shape[1]:,} "
              f"symbols, {int(sig.to_numpy().sum()):,} firings", flush=True)
        return trades_for(p, sig, quiet=quiet)


def trades_for(p, sig, *, stops=STOPS, quiet=False):
    """Board-shaped trade dicts for ONE boolean signal panel, per stop line.

    Split out so scripts/xrank_breadth.py drives the identical walk with a
    different signal. Caller owns the spread guard (see spread_off).
    """
    O, H, L, C = (p[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    S = sig.to_numpy(bool)
    cols = list(p["close"].columns)
    ts = p["close"].index
    rng = np.random.default_rng(G.SEED + 1)

    out = {s: [] for s in stops}
    raw = {s: 0 for s in stops}          # before sizing drops anything
    t0 = time.time()
    for ci in range(len(cols)):
        c = C[:, ci]
        ok = np.isfinite(c)
        if ok.sum() < 260:
            continue
        s, e = int(np.argmax(ok)), int(len(ok) - np.argmax(ok[::-1]))
        o, h, l, c = O[s:e, ci], H[s:e, ci], L[s:e, ci], C[s:e, ci]
        n = len(c)
        if n < 260 or not np.isfinite(c).all():
            c = np.nan_to_num(c, nan=0.0)
        pc = np.concatenate([[np.nan], c[:-1]])
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
        stop_lines = {"own": l, "atr2": c - 2.0 * atr, "atr3": c - 3.0 * atr}
        sym_ts = ts[s:e]
        for sname in stops:
            stop_l = stop_lines[sname]
            risk = c - stop_l
            good = np.isfinite(risk) & (risk > 0)
            ex = G.exits_for_symbol(o, h, l, c, stop_l, rng)
            k_out, px_out, by_stop = ex[EXIT]
            fires = np.flatnonzero(S[s:e, ci] & good)
            if not len(fires):
                continue
            idxs = G.walk(fires, k_out, n)
            if not len(idxs):
                continue
            jdx = idxs + k_out[idxs]
            entry_px, exit_px = c[idxs], px_out[idxs]
            fin = np.isfinite(entry_px) & np.isfinite(exit_px) & (entry_px > 0)
            idxs, jdx = idxs[fin], jdx[fin]
            raw[sname] += int(fin.sum())
            for i, j in zip(idxs, jdx):
                ep, xp, st = float(c[i]), float(px_out[i]), float(stop_l[i])
                shares, risk_taken, capped = sizing.position(ep, st)
                if shares <= 0:
                    continue
                e_ts, x_ts = sym_ts[i], sym_ts[min(j, n - 1)]
                gross = (xp - ep) * shares
                same = e_ts.date() == x_ts.date()
                cost = charges(ep * shares, xp * shares, intraday=same)
                out[sname].append({
                    "symbol": cols[ci], "entry_ts": e_ts, "exit_ts": x_ts,
                    "entry_price": ep, "stop": st, "exit_price": xp,
                    "exit_reason": "stop" if bool(by_stop[i]) else "time limit",
                    "shares": shares, "risk_taken": risk_taken,
                    "capital_capped": capped, "gross_profit": gross,
                    "charges": cost,
                    "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
                    "bars_held": int(j - i), "days_held": (x_ts - e_ts).days,
                    "stop_pct": (ep - st) / ep * 100,
                    "same_session": same, "net_profit": gross - cost,
                })
        if not quiet and (ci + 1) % 250 == 0:
            print(f"    ... {ci + 1:,} of {len(cols):,} symbols "
                  f"({time.time() - t0:.0f}s)", flush=True)
    for sname in stops:
        print(f"    {sname:<5} {raw[sname]:>7,} trades at trade level -> "
              f"{len(out[sname]):>7,} after sizing "
              f"({raw[sname] - len(out[sname]):,} dropped, shares <= 0)")
    return out, raw


def selfcheck(raw):
    """The trade count per stop must match the grid CSV that produced +4.52.

    Counted BEFORE sizing: entry_exit_grid has no account layer, so its
    `trades` column is the pre-sizing number. If these disagree the trade list
    is not the one item 20 measured and nothing below is about that result.
    """
    if not GRID_CSV.exists():
        raise SystemExit(f"  {GRID_CSV} missing -- cannot prove the mirror. Stop.")
    g = pd.read_csv(GRID_CSV)
    g = g[(g.entry == ENTRY) & (g["exit"] == EXIT)].set_index("stop_rule")
    bad = 0
    for sname in STOPS:
        want = int(g.loc[sname, "trades"])
        got = raw[sname]
        flag = "ok" if want == got else "!! MISMATCH"
        if want != got:
            bad += 1
        print(f"  mirror {sname:<5} grid {want:>7,}  here {got:>7,}  {flag}")
    return bad == 0


def summarise(name, cells):
    live = [c for c in cells.values()
            if c["cagr"] is not None and c["hold_cagr"] is not None
            and not c["wiped"]]
    if not live:
        return {"variant": name, "cells": 0}
    exc = np.array([c["cagr"] - c["hold_cagr"] for c in live], dtype=float)
    return {
        "variant": name, "cells": len(live),
        "median_excess": float(np.median(exc)),
        "p90_excess": float(np.percentile(exc, 90)),
        "max_excess": float(exc.max()),
        "beats_hold": int(sum(1 for c in live if c["beats_hold"])),
        "median_cagr": float(np.median([c["cagr"] for c in live])),
        "median_hold_cagr": float(np.median([c["hold_cagr"] for c in live])),
        "median_taken": float(np.median([c["taken"] for c in live])),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--quick", action="store_true",
                    help="start 2018 only (20 cells/variant, not 100)")
    ap.add_argument("--all-priorities", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    members = sorted(cfg.merged)
    print(f"\n  universe: {len(members)} symbols")
    print(f"  variants: {ENTRY} x {EXIT} at stops {', '.join(STOPS)}")

    print("\n1. building the trade lists (signal imported from entry_exit_grid)")
    built, raw = build_all(members)

    print("\n2. mirror check against the grid that produced +4.52 bp/session")
    if not selfcheck(raw):
        raise SystemExit("  mirror FAILED -- this is not item 20's trade list. Stop.")
    if args.selfcheck:
        return

    print("\n3. the board's scenarios")
    years = [2018] if args.quick else dd.START_YEARS
    prios = dd.PRIORITIES if args.all_priorities else ["mom_hi"]
    n_cells = 5 * len(years) * len(prios) * 4
    print(f"  5 universes x {len(years)} starts x {len(prios)} priorities x 2 "
          f"risks x 2 capitals = {n_cells} cells per variant")
    payload = load_board()
    unis = universes(set(members), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(members), unis, [], board)
    hold_cagr = hold_cagrs(holds)
    print(f"  hold CAGR by cell: {len(hold_cagr)} entries, "
          f"all|2018 = {hold_cagr.get('all|2018')}")

    print("\n4. pricing the spread once, then the account layer")
    rows, flat = [], []
    for sname in STOPS:
        t0 = time.time()
        priced = wa.spread_of(built[sname])
        label = f"{ENTRY}|{EXIT}|{sname}"
        cells = cells_for(priced, unis, hold_cagr, label, years=years,
                          prios=prios, risks=dd.RISKS, capitals=dd.CAPITALS)
        rows.append(summarise(sname, cells))
        for key, c in cells.items():
            flat.append({"stop": sname, "key": key, **c})
        r = rows[-1]
        print(f"  {sname:<5} {r['cells']:>3} live cells  median excess "
              f"{r.get('median_excess', float('nan')):+6.2f}  beats hold "
              f"{r.get('beats_hold', 0):>3}/{r['cells']}  ({time.time()-t0:.0f}s)",
              flush=True)

    print("\n5. verdict — CAGR points vs buy-and-hold, account layer ON, costs ON")
    t = pd.DataFrame(rows).set_index("variant")
    print(t.round(2).to_string())
    best = t["max_excess"].max()
    print(f"\n  best single cell anywhere: {best:+.2f} points vs buy-and-hold")
    print(f"  total cells beating hold: {int(t['beats_hold'].sum())} of "
          f"{int(t['cells'].sum())}")
    print("\n  Compare with item 20's TRADE-LEVEL reading of the same trades:")
    g = pd.read_csv(GRID_CSV)
    g = g[(g.entry == ENTRY) & (g["exit"] == EXIT)].set_index("stop_rule")
    for sname in STOPS:
        print(f"    {sname:<5} trade level {g.loc[sname,'vs_hold_bp']:+6.2f} "
              f"bp/session   account level "
              f"{t.loc[sname,'median_excess']:+6.2f} CAGR pts/yr")

    stamp = date.today().isoformat()
    OUT.joinpath("measurements").mkdir(parents=True, exist_ok=True)
    cpath = OUT / "measurements" / f"xrank_account_{stamp}.csv"
    with cpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat[0].keys()))
        w.writeheader()
        w.writerows(flat)
    (OUT / f"xrank_account_{stamp}.json").write_text(json.dumps(
        {"built": stamp, "board": board, "summary": rows,
         "raw_trades": raw}, indent=2, default=str))
    print(f"\nwrote {cpath} ({len(flat):,} rows)")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    f = pd.DataFrame(flat)
    f["excess"] = f.cagr - f.hold_cagr
    fig, ax = plt.subplots(figsize=(8, 4.5))
    data = [f[f.stop == s].excess.dropna().to_numpy() for s in STOPS]
    ax.boxplot(data, tick_labels=STOPS, showfliers=True)
    ax.axhline(0, color="crimson", lw=1.2, ls="--")
    ax.set_ylabel("CAGR points vs buy-and-hold")
    ax.set_xlabel("stop line")
    ax.set_title(f"{ENTRY} x {EXIT} through the account layer — "
                 f"0 = ties buy-and-hold")
    fig.tight_layout()
    ppath = OUT / f"xrank_account_{stamp}.png"
    fig.savefig(ppath, dpi=130)
    print(f"wrote {ppath}")


if __name__ == "__main__":
    main()

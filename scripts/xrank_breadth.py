"""How many stocks should a rule pick? Sweep `xrank`'s breadth, 5 to 100.

    python3 -m scripts.xrank_breadth [--quick]

WHY. Item 21 killed `xrank` at the account layer -- +4.52 bp/session at trade
level became -4.84 CAGR points below buy-and-hold -- and the mechanism was
BREADTH, not entry quality. `xrank` fires on the top DECILE of ~1,000 stocks,
about 100 names a session. An account risking 0.5-1% per trade holds maybe ten
positions, so it could afford only 2.6-6.6% of the signals. The other 93-97%
of the edge measured in item 20 was unreachable.

That is a shape problem with an obvious test: make the rule pick FEWER names.
If the account was already taking the best few by priority, cutting breadth
changes nothing. If breadth itself was the tax -- if the rule was being graded
on a portfolio nobody can own -- it should improve as N falls.

WHAT CHANGES, and it is one line. Item 19's `xrank` thresholds a PERCENTILE
(`ret252.rank(pct=True) > 0.90`). "Pick 5" is not a percentile, so this ranks
by the same trailing 252-session return and takes the top N by COUNT. N = 100
is the closest thing to the decile version and is here as a BRIDGE to item 21,
not as a finding: at ~1,000 listed names the decile is ~100, so its row should
land near item 21's -4.84. It will not match exactly -- the decile gives fewer
names in the early years when fewer stocks were listed, top-N gives 100
whenever 100 exist -- so read it as a consistency check, not an identity.

PRE-SPECIFIED, before the run: N in 5, 10, 20, 50, 100. The stop is pinned at
3xATR and the exit at `stop`, which is item 20's best cell and item 21's best
row; nothing else is searched. One axis moves.

PREDICTION, recorded before the numbers (this project's rule -- item 18 logged
two predictions and both were wrong, which is the point of writing them down):
N=5 improves on N=100 by more than 2 CAGR points, and still does not beat
buy-and-hold. If N=5 beats hold, that is a bigger result than anything on the
board and must be checked for luck before it is believed.

NO WALK IS RE-DERIVED. `trades_for` and `spread_off` are imported from
scripts.xrank_account, whose mirror check pins them to item 20's trade list;
`panels` comes from scripts.entry_exit_grid. Only the signal differs.

Reads:  the cleaned daily parquet (via kitelab.frames), /data/clean/kitelab/
        dashboard.json, output/wf_attach_hold_<board>.pkl
Writes: output/xrank_breadth_<date>.json,
        output/measurements/xrank_breadth_<date>.csv,
        output/xrank_breadth_<date>.png
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
import scripts.entry_exit_grid as G
from scripts.stop_sweep import cells_for
from scripts.wf_pine import hold_cagrs, load_board, universes
from scripts.xrank_account import spread_off, trades_for, summarise

OUT = Path(__file__).resolve().parent.parent / "output"
BREADTHS = [5, 10, 20, 50, 100]
STOP = "atr3"
EXIT = "stop"


def topn_signal(p, n):
    """Fire on the N stocks with the highest trailing 252-session return.

    `method="first"` breaks ties by column order so EXACTLY n names fire on a
    session where several returns are equal -- with the default "average" a tie
    at the boundary silently fires none of the tied names or all of them.
    Sessions with fewer than n priced names fire all of them, which is correct
    and only touches the earliest years.
    """
    c = p["close"]
    ret252 = c / c.shift(252) - 1.0
    rk = ret252.rank(axis=1, ascending=False, method="first")
    return rk.le(n) & c.notna()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="start 2018 only (20 cells/variant, not 100)")
    args = ap.parse_args()

    cfg = config.load()
    members = sorted(cfg.merged)
    print(f"\n  universe: {len(members)} symbols")
    print(f"  breadths: {BREADTHS} names per session")
    print(f"  stop pinned {STOP}, exit pinned {EXIT} (item 20/21's best cell)")
    print("  PREDICTION, recorded first: N=5 beats N=100 by >2 CAGR points and"
          "\n  still loses to buy-and-hold.")

    print("\n1. panel (read once, shared by all five breadths)")
    with spread_off():
        p = G.panels(members)
        listed = p["close"].notna().sum(axis=1)
        print(f"  listed names per session: median {listed.median():.0f}, "
              f"first {listed.iloc[0]:.0f}, last {listed.iloc[-1]:.0f}")
        print("  (N=100 is ~the old decile only in the later years -- see the "
              "docstring)")
        built = {}
        for n in BREADTHS:
            sig = topn_signal(p, n)
            fires = int(sig.to_numpy().sum())
            print(f"\n  N={n:<4} {fires:>9,} firings "
                  f"({100.0*fires/int(p['close'].notna().to_numpy().sum()):.2f}% "
                  f"of listed stock-sessions)", flush=True)
            out, raw = trades_for(p, sig, stops=[STOP], quiet=True)
            built[n] = out[STOP]

    print("\n2. the board's scenarios")
    years = [2018] if args.quick else dd.START_YEARS
    payload = load_board()
    unis = universes(set(members), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(members), unis, [], board)
    hold_cagr = hold_cagrs(holds)
    print(f"  hold CAGR by cell: {len(hold_cagr)} entries, "
          f"all|2018 = {hold_cagr.get('all|2018')}")

    print("\n3. the account layer")
    rows, flat = [], []
    for n in BREADTHS:
        t0 = time.time()
        priced = wa.spread_of(built[n])
        cells = cells_for(priced, unis, hold_cagr, f"xrank{n}", years=years,
                          prios=["mom_hi"], risks=dd.RISKS, capitals=dd.CAPITALS)
        r = summarise(f"N={n}", cells)
        r["available"] = len(built[n])
        r["pct_taken"] = 100.0 * r.get("median_taken", 0) / max(len(built[n]), 1)
        rows.append(r)
        for key, c in cells.items():
            flat.append({"N": n, "key": key, **c})
        print(f"  N={n:<4} {len(built[n]):>7,} trades available  "
              f"median excess {r.get('median_excess', float('nan')):+6.2f}  "
              f"beats hold {r.get('beats_hold', 0):>3}/{r['cells']}  "
              f"({time.time()-t0:.0f}s)", flush=True)

    print("\n4. verdict — CAGR points vs buy-and-hold, account layer ON, costs ON")
    t = pd.DataFrame(rows).set_index("variant")
    print(t[["available", "cells", "median_excess", "p90_excess", "max_excess",
             "beats_hold", "median_taken", "pct_taken"]].round(2).to_string())

    lo, hi = t.loc["N=5", "median_excess"], t.loc["N=100", "median_excess"]
    print(f"\n  N=5 vs N=100: {lo:+.2f} vs {hi:+.2f}  "
          f"= {lo - hi:+.2f} CAGR points")
    print(f"  prediction was '>2 points better AND still losing'. Better by "
          f"{lo - hi:+.2f}; " + ("still loses." if lo < 0 else
          "IT WINS -- this needs a luck check before anyone believes it."))
    print("  item 21's decile version at the same stop: -4.84")
    best = t["max_excess"].max()
    print(f"  best single cell anywhere: {best:+.2f} points vs buy-and-hold")

    stamp = date.today().isoformat()
    OUT.joinpath("measurements").mkdir(parents=True, exist_ok=True)
    cpath = OUT / "measurements" / f"xrank_breadth_{stamp}.csv"
    with cpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat[0].keys()))
        w.writeheader()
        w.writerows(flat)
    (OUT / f"xrank_breadth_{stamp}.json").write_text(
        json.dumps({"built": stamp, "board": board, "stop": STOP,
                    "summary": rows}, indent=2, default=str))
    print(f"\nwrote {cpath} ({len(flat):,} rows)")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    f = pd.DataFrame(flat)
    f["excess"] = f.cagr - f.hold_cagr
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
    a1.boxplot([f[f.N == n].excess.dropna().to_numpy() for n in BREADTHS],
               tick_labels=[str(n) for n in BREADTHS], showfliers=True)
    a1.axhline(0, color="crimson", lw=1.2, ls="--")
    a1.set_xlabel("names picked per session")
    a1.set_ylabel("CAGR points vs buy-and-hold")
    a1.set_title("0 = ties buy-and-hold")
    a2.plot(BREADTHS, [r.get("median_excess", np.nan) for r in rows],
            "o-", label="median")
    a2.plot(BREADTHS, [r.get("p90_excess", np.nan) for r in rows],
            "s--", label="90th percentile")
    a2.axhline(0, color="crimson", lw=1.2, ls="--")
    a2.set_xscale("log")
    a2.set_xticks(BREADTHS)
    a2.set_xticklabels([str(n) for n in BREADTHS])
    a2.set_xlabel("names picked per session")
    a2.legend()
    a2.set_title("does concentrating help?")
    fig.suptitle("xrank breadth sweep — stop 3xATR, exit hold-till-stopped")
    fig.tight_layout()
    ppath = OUT / f"xrank_breadth_{stamp}.png"
    fig.savefig(ppath, dpi=130)
    print(f"wrote {ppath}")


if __name__ == "__main__":
    main()

"""If a trade falls X of the way to its stop, is it worth cutting?

    python3 -m scripts.stop_sweep [--pilot N]

THE QUESTION THIS ANSWERS, AND THE TRAP IT AVOIDS. scripts/stops.py (board-wide
2026-09-10) found that a trade which ends up WINNING typically dips only 0.38R
against you, while a loser dips 1.00R. The tempting inference -- "cut anything
past 0.38R" -- is backwards reasoning: 0.38R is a fact about a group that could
only be identified AFTERWARDS. At the moment a position is down 0.4R you do not
know which group it is in. The answerable question is the conditional one:

    of every trade that has ALREADY fallen X, what fraction still ends up
    profitable, and what would cutting them all at X have earned instead?

MEASURED ON CLOSING PRICES, NOT LOWS, AND THAT IS THE WHOLE POINT.
`excursion.py` defines MAE off the session's LOW, which presumes someone
watching the tape intraday. The method actually being modelled here looks once,
near the close, and decides -- so a threshold read off a low is not a rule this
trader could execute. Every crossing below is a CLOSE at or under the cut price.

THE COUNTERFACTUAL IS LEDGER ARITHMETIC ON A FIXED TRADE LIST, NOT A
RE-SIMULATION -- the same caveat scripts/stop_rescue.py carries. Cutting a
position in March frees its capital and would have changed which later trades
the account could afford. What this bounds is the size of the question, and the
sign of the answer.

Costs: the cut trade is charged the same rupee charges as the real one. Same
shares, same round trip, so the entry leg is exact and the exit leg moves only
with the exit price -- second-order against a 0.222% statutory toll.

Reads:  the 19 *_all.pkl signal caches and the daily parquet candles.
Writes: output/measurements/stop_sweep_2026-09-10.csv
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np
import pandas as pd

from kitelab import config, registry, signals
from scripts.entry_edge import close_matrix

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output", "measurements", "stop_sweep_2026-09-10.csv")

# 0.375 is in the grid on purpose: it is the winners' median MAE, i.e. exactly
# the threshold the backwards inference above would have picked.
CUTS = (0.25, 0.375, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
REQUIRED = ("symbol", "entry_price", "stop", "entry_ts", "exit_ts",
            "r_multiple", "gross_profit", "net_profit", "risk_taken")


def check(trades: list[dict], label: str) -> None:
    """Stop with a clear error if the cache is not shaped the way we assume."""
    missing = [k for k in REQUIRED if k not in trades[0]]
    if missing:
        raise SystemExit(f"{label}: cached trades lack {missing} -- "
                         "the cache format moved, do not trust this run")


def rule_rows(trades, sessions, col_of, closes):
    """Per trade: actual net R, and net R under each cut threshold.

    Returns (kept, dropped, actual, cut, crossed). `cut` and `crossed` are
    (n_trades, n_cuts) arrays. A trade whose closes never reach the cut price
    keeps its real result. `crossed` is tracked separately rather than inferred
    from `cut != actual`, because a trade that already exited on a stop close at
    exactly -1.00R is unchanged by a 1.00R cut and would otherwise be counted as
    never having reached it -- which is precisely the population of interest.
    """
    actual, per_cut, per_hit, dropped = [], [], [], 0
    for t in trades:
        j = col_of.get(t["symbol"])
        i = sessions.get(pd.Timestamp(t["entry_ts"]).normalize())
        e = sessions.get(pd.Timestamp(t["exit_ts"]).normalize())
        entry = float(t["entry_price"])
        risk_ps = entry - float(t["stop"])
        risk_rs = float(t["risk_taken"])
        if j is None or i is None or e is None or risk_ps <= 0 or risk_rs <= 0:
            dropped += 1
            continue
        path = closes[i + 1:e + 1, j]          # entry bar excluded, exit included
        if path.size == 0:
            dropped += 1
            continue
        r_now = float(t["r_multiple"])
        # charges in R, held fixed: same shares, same round trip
        charge_r = (float(t["gross_profit"]) - float(t["net_profit"])) / risk_rs
        actual.append(r_now)
        row = np.empty(len(CUTS))
        hitrow = np.zeros(len(CUTS), dtype=bool)
        for k, x in enumerate(CUTS):
            price = entry - x * risk_ps
            hit = np.flatnonzero(path <= price)
            if hit.size == 0:
                row[k] = r_now
            else:
                row[k] = (path[hit[0]] - entry) / risk_ps - charge_r
                hitrow[k] = True
        per_cut.append(row); per_hit.append(hitrow)
    if not actual:
        return 0, dropped, None, None, None
    return (len(actual), dropped, np.array(actual),
            np.vstack(per_cut), np.vstack(per_hit))


def main() -> None:
    pilot = None
    if "--pilot" in sys.argv:
        pilot = int(sys.argv[sys.argv.index("--pilot") + 1])

    cfg = config.load()
    universe = cfg.merged
    board = registry.REGISTRY[:pilot] if pilot else registry.REGISTRY
    print(f"\n  {len(universe)} stocks, {len(board)} rules"
          f"{f' (PILOT of {len(registry.REGISTRY)})' if pilot else ''}.\n")

    index, symbols, filled, _traded, _last = close_matrix(universe)
    sessions = {ts: i for i, ts in enumerate(index)}
    col_of = {s: j for j, s in enumerate(symbols)}

    print()
    hdr = ("  {:<34}{:>9}".format("rule", "trades")
           + "".join(f"{f'cut {x:g}R':>10}" for x in CUTS) + f"{'actual':>10}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))

    out_rows = []
    all_actual, all_cut, all_hit = [], [], []
    total_in = total_kept = 0
    for strat in board:
        trades = signals.load(f"{strat.cache}_all", universe)
        if not trades:
            print(f"  {strat.label}: no cache -- run scripts.refresh first")
            continue
        check(trades, strat.label)
        kept, dropped, actual, cut, hit = rule_rows(
            trades, sessions, col_of, filled)
        total_in += len(trades); total_kept += kept
        if actual is None:
            print(f"  {strat.label:<34}{0:>9}   nothing measurable")
            continue
        print(f"  {strat.label:<34}{kept:>9,}"
              + "".join(f"{cut[:, k].mean():>10.3f}" for k in range(len(CUTS)))
              + f"{actual.mean():>10.3f}")
        all_actual.append(actual); all_cut.append(cut); all_hit.append(hit)
        row = {"rule": strat.label, "trades": kept, "dropped": dropped,
               "actual_mean_r": round(float(actual.mean()), 4)}
        for k, x in enumerate(CUTS):
            row[f"cut_{x:g}R_mean_r"] = round(float(cut[:, k].mean()), 4)
        out_rows.append(row)

    print(f"\n  {total_in:,} cached trades -> {total_kept:,} measurable "
          f"({100.0 * total_kept / total_in:.2f}%). A trade is dropped only when "
          "its entry or exit stamp is not a session, or its risk is non-positive.")

    actual = np.concatenate(all_actual)
    cut = np.vstack(all_cut)
    hit = np.vstack(all_hit)
    print("\n  POOLED -- mean net R per trade, all rules together\n")
    print(f"  {'no cut (what the board did)':<34}{actual.mean():>10.3f} R")
    for k, x in enumerate(CUTS):
        d = cut[:, k].mean() - actual.mean()
        print(f"  {f'cut at {x:g}R':<34}{cut[:, k].mean():>10.3f} R   "
              f"{d:+.3f} R per trade")

    # The conditional question, stated directly.
    print("\n  RECOVERY -- of trades whose CLOSE reached each depth, how many won?\n")
    h2 = f"  {'depth reached':<20}{'trades':>12}{'% of all':>10}{'% that won':>13}{'mean R':>10}"
    print(h2); print("  " + "-" * (len(h2) - 2))
    for k, x in enumerate(CUTS):
        reached = hit[:, k]
        n = int(reached.sum())
        if n == 0:
            continue
        won = float((actual[reached] > 0).mean())
        print(f"  {f'fell to {x:g}R':<20}{n:>12,}{100.0 * n / len(actual):>10.1f}"
              f"{100.0 * won:>13.1f}{actual[reached].mean():>10.3f}")

    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
    print(f"\n  wrote {OUT}\n")


if __name__ == "__main__":
    main()

"""Reconcile the two things this project has called "exposure", and test whether
the breadth finding survives being measured against the real account.

Two numbers wear the name, and a 2026-09-16 reading of the breadth table
treated them as one:

  curves.exposure_pct   share of DAYS the account held anything at all -- a
                        binary per-day flag, ~99% on every cell. It was read as
                        "99% of capital deployed"; it says no such thing.
  entry_edge.exposure_pct  held stock-sessions over all stock-sessions the
                        universe offers -- BREADTH, 5.9-49.5%. No account, no
                        cash limit: it counts every signal the rule ever made.

entry_edge then builds `effective_pct_yr = expm1(net_in_market * breadth)`,
whose comment reads "idle cash earns 0". That is a real portfolio -- one that
owns every signal at market weight and sits in cash otherwise -- but it is NOT
the board's account, which is cash-constrained and ~100% deployed into a
handful of names. So the -4.7 to -13.2 "vs hold" column describes a fund
nobody here simulated.

That matters because the breadth/vs_hold correlation of +0.986 is then close to
arithmetic: vs_hold is a monotone function of breadth by construction. This
script re-runs the correlation against the BOARD's own median excess, which is
free of that circularity, and reports both.

Reads:  /data/clean/kitelab/dashboard.json (grid + daily_excess)
        output/measurements/exposure_*.csv (newest)
Writes: output/measurements/exposure_reconcile_<N>strat_<date>.csv
        output/measurements/exposure_reconcile_<N>strat_<date>.png
"""
from __future__ import annotations

import csv
import datetime
import glob
import json
import os

import numpy as np

from kitelab import registry

REPO = "/work/kitelab"
OUT = os.path.join(REPO, "output")
M = os.path.join(OUT, "measurements")
PAYLOAD = "/data/clean/kitelab/dashboard.json"

N_RULES = len(registry.REGISTRY)
STAMP = f"{N_RULES}strat_{datetime.date.today():%Y-%m-%d}"
CSV_PATH = os.path.join(M, f"exposure_reconcile_{STAMP}.csv")
PNG_PATH = os.path.join(M, f"exposure_reconcile_{STAMP}.png")


def newest(prefix):
    hits = sorted(glob.glob(os.path.join(M, prefix + "*.csv")))
    if not hits:
        raise SystemExit(f"no {prefix}*.csv in {M}")
    print(f"  using {os.path.basename(hits[-1])}")
    return hits[-1]


def med(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else float("nan")


# ----------------------------------------------------------------- load ----
d = json.load(open(PAYLOAD))
grid, de = d["grid"], d.get("daily_excess") or {}
print(f"\n  {PAYLOAD}")
print(f"  built {d['built']}: {len(grid):,} grid cells, {len(de):,} with the "
      f"daily-excess test")
if not de:
    raise SystemExit("  daily_excess missing -- run the diagnostics leg first.")

tag = {}
for s in registry.REGISTRY:
    v = f"{s.variant:g}" if isinstance(s.variant, float) else str(s.variant)
    tag[f"{s.key}|{v}"] = s.label
print(f"  {len(tag)} registered strategies")

breadth = {}
with open(newest("exposure_")) as fh:
    for r in csv.DictReader(fh):
        if r["rule"].startswith("ALL "):
            continue
        breadth[r["rule"]] = {k: float(r[k]) for k in
                              ("exposure_pct", "net_pct_yr_in_market",
                               "effective_pct_yr", "vs_hold_pts")}
print(f"  breadth table: {len(breadth)} rules\n")

# ------------------------------------------------- gather, per strategy ----
rows = []
for st, label in tag.items():
    # 216 of the 2,700 cells are null by design (all in `recent`, whose
    # members have no pre-2018 turnover to be classified on) and serialise
    # as None rather than being absent.
    cells = [v for k, v in grid.items()
             if "|".join(k.split("|")[:2]) == st and v]
    exc = [v["excess_pts"] for k, v in de.items()
           if "|".join(k.split("|")[:2]) == st]
    if not cells or not exc:
        print(f"  !! {label}: no cells"); continue
    taken = sum(c.get("taken") or 0 for c in cells)
    signals = sum(c.get("signals") or 0 for c in cells)
    b = breadth.get(label, {})
    rows.append({
        "rule": label, "cells": len(cells), "tested": len(exc),
        "days_with_a_position_pct": med([c.get("exposure") for c in cells]),
        "median_cash_pct": med([c.get("median_cash") for c in cells]),
        "days_under_5pct_cash": med([c.get("full_pct") for c in cells]),
        "take_rate_pct": 100.0 * taken / signals if signals else float("nan"),
        "breadth_pct": b.get("exposure_pct", float("nan")),
        "net_pct_yr_in_market": b.get("net_pct_yr_in_market", float("nan")),
        "modelled_effective_pct_yr": b.get("effective_pct_yr", float("nan")),
        "modelled_vs_hold_pts": b.get("vs_hold_pts", float("nan")),
        "board_median_cagr": med([c.get("cagr") for c in cells]),
        "board_median_excess_pts": float(np.median(exc)),
    })
rows.sort(key=lambda r: -r["breadth_pct"])

# ------------------------------------------------------- 1. the two names --
print("  1. THE TWO THINGS CALLED EXPOSURE -- median over each rule's cells\n")
h = (f"  {'rule':<34}{'days held':>11}{'med cash%':>11}{'<5% cash':>10}"
     f"{'take%':>8}{'BREADTH%':>10}")
print(h); print("  " + "-" * (len(h) - 2))
for r in rows:
    print(f"  {r['rule']:<34}{r['days_with_a_position_pct']:>11.2f}"
          f"{r['median_cash_pct']:>11.1f}{r['days_under_5pct_cash']:>10.1f}"
          f"{r['take_rate_pct']:>8.1f}{r['breadth_pct']:>10.2f}")
print("  " + "-" * (len(h) - 2))
print("  'days held' = curves.exposure_pct: days holding ANYTHING. 'med cash%'")
print("  and '<5% cash' are the real capital measures. 'take%' = signals the")
print("  account could afford. BREADTH = stock-sessions owned / offered.\n")
print("  Read across one row: capital is spent, signals are not taken, and")
print("  breadth is small. All three at once = CONCENTRATION, not cash drag.\n")

# ------------------------------------------- 2. does the model hold up? ----
print("  2. THE MODEL vs THE ACCOUNT -- both in CAGR %/yr\n")
h2 = (f"  {'rule':<34}{'in-mkt rate':>12}{'x breadth =':>13}{'BOARD cagr':>12}"
      f"{'model err':>11}")
print(h2); print("  " + "-" * (len(h2) - 2))
for r in rows:
    r["model_error_pts"] = r["modelled_effective_pct_yr"] - r["board_median_cagr"]
    print(f"  {r['rule']:<34}{r['net_pct_yr_in_market']:>12.1f}"
          f"{r['modelled_effective_pct_yr']:>13.1f}{r['board_median_cagr']:>12.2f}"
          f"{r['model_error_pts']:>11.2f}")
print("  " + "-" * (len(h2) - 2))
err = np.array([r["model_error_pts"] for r in rows])
rate = np.array([r["net_pct_yr_in_market"] for r in rows])
board = np.array([r["board_median_cagr"] for r in rows])
print(f"  model error: median {np.median(err):+.2f}, range "
      f"{err.min():+.2f} to {err.max():+.2f} points")
print(f"  the un-scaled in-market rate against the board: median error "
      f"{np.median(rate - board):+.2f} points\n")
print("  The in-market rate is GROSS of slippage and the 1% fill cap (its own")
print("  script says so) while the board is NET of both -- so neither column")
print("  is the account, and the gap is not evidence about breadth.\n")

# ------------------------------------------------ 3. the correlation test --
print("  3. DOES BREADTH PREDICT ANYTHING THE BOARD MEASURES?\n")
b = np.array([r["breadth_pct"] for r in rows])
vs = np.array([r["modelled_vs_hold_pts"] for r in rows])
be = np.array([r["board_median_excess_pts"] for r in rows])


def rank(a):
    o = np.argsort(np.argsort(a))
    return o.astype(float)


pairs = [("breadth vs MODELLED vs-hold  (circular)", b, vs),
         ("breadth vs BOARD median excess", b, be),
         ("breadth vs BOARD median cagr", b, board),
         ("in-market rate vs BOARD median excess", rate, be)]
print(f"  {'comparison':<42}{'pearson':>9}{'spearman':>10}")
print("  " + "-" * 61)
for name, x, y in pairs:
    print(f"  {name:<42}{np.corrcoef(x, y)[0, 1]:>9.3f}"
          f"{np.corrcoef(rank(x), rank(y))[0, 1]:>10.3f}")
print("  " + "-" * 61)
print(f"  n = {len(rows)} rules. With 9 points, |r| must exceed ~0.67 to clear")
print("  p=0.05 two-sided, and these 9 are not independent (n_eff 4.0), so the")
print("  real bar is higher still.\n")

# ----------------------------------------------------------------- write ---
os.makedirs(M, exist_ok=True)
cols = list(rows[0].keys())
with open(CSV_PATH, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    for a, y, ttl in ((ax[0], vs, "modelled 'vs hold' (breadth-scaled)"),
                      (ax[1], be, "board median excess vs hold")):
        a.scatter(b, y, s=60)
        a.axhline(0, lw=0.8, color="grey")
        a.set_xlabel("breadth: % of stock-sessions owned")
        a.set_ylabel("CAGR points vs hold")
        a.set_title(f"{ttl}\nr = {np.corrcoef(b, y)[0, 1]:+.3f}")
        a.grid(alpha=0.3)
    fig.suptitle(f"Breadth against a model of itself, and against the board "
                 f"({N_RULES} rules)")
    fig.tight_layout(); fig.savefig(PNG_PATH, dpi=120); plt.close(fig)
    print(f"  wrote {CSV_PATH}\n  wrote {PNG_PATH}\n")
except ImportError:
    print(f"  wrote {CSV_PATH}\n  (no matplotlib -- skipped the figure)\n")

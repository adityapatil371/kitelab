"""Rank the board's strategies on independent measurements, to find the ones
that are consistently worst rather than worst on any single view.

MOVED 2026-09-16 (user's call) from `output/measurements/rank19_2026-09-11.py`,
which was gitignored and therefore one `rm` away from being lost -- the same
exposure that destroyed the original NEXT_TESTS.md. Renamed because a file
with dots and hyphens in its name cannot be run as `python3 -m scripts.<name>`.
Content is otherwise unchanged; it still reads the newest CSVs by glob and
takes a board path as its one optional argument, so it is not tied to the
19-strategy board its old name claimed.

    python3 -m scripts.rank_board [/path/to/dashboard.json]

Reads:  /data/clean/kitelab/dashboard.json (the 2026-09-10 board: 10,260 cells
        and the per-cell daily-excess-vs-hold test), plus three standalone
        measurement CSVs in output/measurements/ (entry_edge, exit_sweep,
        exposure). Writes nothing -- prints a table.
"""
from __future__ import annotations
import csv, json, os
import numpy as np
from kitelab import registry

REPO = "/work/kitelab"
M = os.path.join(REPO, "output", "measurements")

import sys
PATH = sys.argv[1] if len(sys.argv) > 1 else "/data/clean/kitelab/dashboard.json"
d = json.load(open(PATH))
grid = d["grid"]
de = d.get("daily_excess") or {}
print(f"\n  {PATH}")
print(f"  built {d['built']}: {len(grid):,} grid cells, "
      f"{len(de):,} carrying the daily-excess test")
print(f"  priorities on this board: {d['priorities']}, default {d['priority_default']}")
if not de:
    # The diagnostics leg has not run yet. Fall back to the grid's own CAGR
    # minus the equal-weight hold for that (universe, start) pair, which the
    # payload carries per cell -- a coarser view, same direction.
    raise SystemExit("  daily_excess missing -- the diagnostics leg has not "
                     "finished. Re-run this once refresh completes.")

tag = {}                      # "ema|0" -> label
for s in registry.REGISTRY:
    v = s.variant
    v = f"{v:g}" if isinstance(v, float) else str(v)
    tag[f"{s.key}|{v}"] = s.label
print(f"  {len(tag)} registered strategies; first 3 keys: {list(tag)[:3]}")

# --- board: excess vs hold, per cell, grouped by strategy ------------------
rows: dict[str, list] = {k: [] for k in tag}
unmatched = 0
for key, v in de.items():
    st = "|".join(key.split("|")[:2])
    if st in rows:
        rows[st].append((v["excess_pts"], v["t_hac"]))
    else:
        unmatched += 1
print(f"  cells matched to a strategy: {len(de) - unmatched:,}; unmatched "
      f"{unmatched:,} (assets, which are off the board now)")

def col(path, keyname, field, cast=float):
    out = {}
    with open(os.path.join(M, path)) as fh:
        for r in csv.DictReader(fh):
            out.setdefault(r[keyname], []).append(cast(r[field]))
    return out


# These CSVs were hardcoded to the 2026-09-10 filenames, which were written from
# the 19-rule board and kept their name through both cuts -- so this harness went
# on reading 19 rules after the board was 9. Pick the NEWEST matching file and
# say which one, rather than naming a date.
def newest(prefix):
    # Anchored on "<N>strat_", not the bare prefix: on 2026-09-16 a plain
    # "exposure_*" sort put the NEW exposure_reconcile_9strat_*.csv last and
    # this harness read the wrong table. Pinning the rule count also refuses a
    # CSV written for a board that has since been cut.
    import glob
    n = len(registry.REGISTRY)
    pat = os.path.join(M, f"{prefix}{n}strat_*.csv")
    hits = sorted(glob.glob(pat))
    if not hits:
        raise SystemExit(f"no {os.path.basename(pat)} in {M}")
    print(f"  using {os.path.basename(hits[-1])}")
    return hits[-1]

# entry quality: excess over a drift-matched random entry at a 20-session
# horizon -- says nothing about the account, only about the entry stamp.
ee, n_ee = {}, 0
with open(newest("entry_edge_")) as fh:
    for r in csv.DictReader(fh):
        n_ee += 1
        if r["horizon"] == "20":
            ee[r["rule"]] = float(r["excess_vs_drift_pct"])
print(f"  entry_edge: {n_ee} rows, {len(ee)} at the 20-session horizon")

ex = col(os.path.basename(newest("exit_sweep_")), "rule", "own_exit_pct_yr")
with open(newest("exposure_")) as fh:
    hdr = csv.DictReader(fh); eff = {}
    for r in hdr:
        eff[r["rule"]] = float(r.get("effective_pct_yr") or r.get("vs_hold_pts"))
print(f"  exit_sweep: {len(ex)} rules; exposure: {len(eff)} rules\n")

out = []
for st, label in tag.items():
    a = np.array(rows[st], dtype=float)
    if not len(a):
        print(f"  !! {label}: no cells"); continue
    out.append({
        "st": st, "label": label, "cells": len(a),
        "med_excess": float(np.median(a[:, 0])),
        "best_excess": float(a[:, 0].max()),
        "pct_beating_hold": 100.0 * float((a[:, 0] > 0).mean()),
        "med_t": float(np.median(a[:, 1])),
        "entry_20": ee.get(label, float("nan")),
        "own_exit": float(np.median(ex.get(label, [np.nan]))),
        "effective": eff.get(label, float("nan")),
    })

# rank each column (1 = best) and average, so no single view decides
for f, hi_is_good in [("med_excess", True), ("best_excess", True),
                      ("pct_beating_hold", True), ("entry_20", True),
                      ("own_exit", True), ("effective", True)]:
    vals = np.array([r[f] for r in out], dtype=float)
    order = np.argsort(-vals if hi_is_good else vals)
    for rank, i in enumerate(order, 1):
        out[i][f"r_{f}"] = rank
for r in out:
    r["avg_rank"] = float(np.mean([r[k] for k in r if k.startswith("r_")]))

out.sort(key=lambda r: r["avg_rank"])
h = (f"  {'strategy':<38}{'cells':>6}{'med exc':>9}{'best':>8}{'>hold%':>8}"
     f"{'med t':>8}{'entry20':>9}{'ownexit':>9}{'effect':>8}{'AVG RANK':>10}")
print(h); print("  " + "-" * (len(h) - 2))
for r in out:
    print(f"  {r['label']:<38}{r['cells']:>6}{r['med_excess']:>9.2f}"
          f"{r['best_excess']:>8.1f}{r['pct_beating_hold']:>8.1f}"
          f"{r['med_t']:>8.2f}{r['entry_20']:>9.3f}{r['own_exit']:>9.1f}"
          f"{r['effective']:>8.1f}{r['avg_rank']:>10.2f}")
print("  " + "-" * (len(h) - 2))
print("  med exc = median CAGR points vs the on-screen buy-and-hold, over the")
print("  strategy's own cells. entry20 = excess over a drift-matched random")
print("  entry 20 sessions out. effect = CAGR/yr after idle cash, vs 14.09 hold.\n")

# --------------------------------------------------------- redundancy ----
# Two rules that score the same on the same cells are one rule wearing two
# labels: the board pays for both and learns from one. Correlate each pair
# across the cells they SHARE (same universe/risk/capital/start/priority), so
# the comparison is like for like and not a comparison of cell mixes.
print("\n  REDUNDANCY -- correlation of excess-vs-hold across shared cells\n")
by_cell: dict[str, dict[str, float]] = {}
for key, v in de.items():
    parts = key.split("|")
    st, scen = "|".join(parts[:2]), "|".join(parts[2:])
    by_cell.setdefault(scen, {})[st] = v["excess_pts"]
print(f"  {len(by_cell):,} distinct scenarios (universe|risk|capital|fill|start|priority)")

names = [r["st"] for r in out]
pairs = []
for i, a in enumerate(names):
    for b in names[i + 1:]:
        xs = [(c[a], c[b]) for c in by_cell.values() if a in c and b in c]
        if len(xs) < 50:
            continue
        m = np.array(xs, dtype=float)
        r = float(np.corrcoef(m[:, 0], m[:, 1])[0, 1])
        gap = float(np.median(m[:, 0] - m[:, 1]))
        pairs.append((r, a, b, len(xs), gap))
pairs.sort(reverse=True)
print(f"  {len(pairs)} comparable pairs. The ten most alike:\n")
print(f"  {'pair':<34}{'cells':>7}{'corr':>8}{'median gap':>12}")
print("  " + "-" * 59)
lab = {r["st"]: r["label"] for r in out}
for r, a, b, n, gap in pairs[:10]:
    print(f"  {a + ' / ' + b:<34}{n:>7}{r:>8.3f}{gap:>12.2f}")
print("\n  A pair above ~0.95 is two labels on one rule: dropping the weaker of")
print("  the two costs the board almost no information. 'median gap' is the")
print("  first minus the second, in CAGR points -- which one is the weaker.\n")

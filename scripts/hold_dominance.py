"""Are the nine rules merely UNPROVEN against buy-and-hold, or reliably WORSE?

THE QUESTION, noticed 2026-09-16 while ranking the Pine candidate. The board's
headline is "0 of 2,484 cells clear the gate", which reads as an absence of
evidence. But absence of evidence would look different from this. If the nine
rules had no edge either way, each cell's one-sided p would be uniform on
(0,1) and about 5% of 2,484 -- roughly 124 cells -- would clear 0.05 by luck
alone. **Zero do.** A coin that never lands heads in 2,484 tosses is not an
unproven coin.

So this script turns the test around and asks the LEFT tail: how many cells are
significantly worse than holding? Same statistic, same HAC standard error, same
Benjamini-Hochberg machinery -- only the direction of the alternative changes.
`wf_attach.excess_stats` stores t_hac, so the left-tail p is norm_sf(-t) and
nothing has to be recomputed from curves.

WHY BH IS STILL THE RIGHT BAR. The cells are heavily dependent (9 rules over
overlapping universes and start years, and the rules correlate with each other
too). Benjamini-Hochberg controls the false discovery rate under positive
dependence, which is the same argument the board's own gate rests on; it works
in this direction for the same reason.

WHAT THIS CANNOT SAY. 2,484 cells are not 2,484 independent facts. A count of
significant cells is a description of the board, not a sample size, so the
honest summary statistic is the per-RULE one (9 numbers), and even those are
not independent -- the 2026-09-11 redundancy pass put the board at 3-9
independent ideas. Both readings are printed rather than one.

Reads:  /data/clean/kitelab/dashboard.json (the daily_excess block only; the
        diagnostics leg must have run).
Writes: output/measurements/hold_dominance_<date>.csv, one row per cell with
        both tails. Rebuilds nothing, edits no stamped module.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from kitelab.config import CLEAN
from scripts.attach_diagnostics import bh_threshold
from scripts.wf_daily import norm_sf

OUT = Path(__file__).resolve().parent.parent / "output"
ALPHA = 0.05


def load():
    payload = json.loads((CLEAN / "dashboard.json").read_text())
    de = payload.get("daily_excess") or {}
    if not de:
        raise SystemExit("dashboard.json carries no daily_excess block -- run "
                         "scripts.wf_attach then scripts.attach_diagnostics")
    rows = []
    for key, v in de.items():
        if v.get("t_hac") is None:
            continue
        parts = key.split("|")
        t = float(v["t_hac"])
        rows.append({
            "cell": key,
            "rule": "|".join(parts[:2]),
            "universe": parts[2], "risk": parts[3], "capital": parts[4],
            "start": parts[6], "priority": parts[7],
            "t_hac": t,
            # the stored p is the RIGHT tail (H1: rule beats hold); the left is
            # the same statistic asking whether hold beats the rule
            "p_beats_hold": float(v["p"]),
            "p_worse_than_hold": float(norm_sf(-t)),
            "excess_pts": v.get("excess_pts"),
            "mde_80": v.get("mde_80"),
        })
    d = pd.DataFrame(rows)
    print(f"board built {payload['built']}")
    print(f"cells frame: {len(d)} rows x {len(d.columns)} columns")
    print(d.head(3).to_string())
    for col in ("t_hac", "p_beats_hold", "p_worse_than_hold"):
        if d[col].isna().any():
            raise SystemExit(f"{col} has missing values; refusing to test")
    return d


def tails(d):
    """The same three bars, pointed each way."""
    n = len(d)
    print(f"\n{'=' * 78}\nBOTH TAILS OF THE SAME TEST -- {n:,} cells, alpha {ALPHA}")
    print(f"  {'direction':<26}{'p<=0.05':>9}{'expected by chance':>20}"
          f"{'BH':>6}{'Bonferroni':>12}")
    out = {}
    for name, col in (("rule BEATS hold", "p_beats_hold"),
                      ("rule is WORSE than hold", "p_worse_than_hold")):
        ps = sorted(d[col].tolist())
        bh = bh_threshold(ps, ALPHA)
        bonf = ALPHA / n
        rec = {"n_uncorrected": sum(1 for p in ps if p <= ALPHA),
               "bh_threshold": bh,
               "n_bh": sum(1 for p in ps if bh and p <= bh),
               "n_bonferroni": sum(1 for p in ps if p <= bonf)}
        out[col] = rec
        print(f"  {name:<26}{rec['n_uncorrected']:>9,}{ALPHA * n:>20.0f}"
              f"{rec['n_bh']:>6,}{rec['n_bonferroni']:>12,}")
    print("\n  Read the two rows against the 'expected by chance' column, not")
    print("  against each other. A board with no edge in either direction would")
    print("  show roughly that number on BOTH rows.")
    print("\n  The Bonferroni column is the one correlation cannot explain away:")
    print("  it is valid under ARBITRARY dependence, so however tangled these")
    print("  cells are, a cell clearing it is worse than hold at a 5% bar taken")
    print("  across all of them at once. BH assumes positive dependence; the")
    print("  uncorrected count assumes nothing but is not a discovery count.")
    return out


def per_rule(d):
    """Nine numbers, which is the largest honest sample size here."""
    print(f"\nPER RULE -- each rule's own {len(d) // d['rule'].nunique()} cells")
    print(f"  {'rule':<16}{'med t':>8}{'med excess':>12}{'t<0':>8}"
          f"{'worse p<=.05':>14}{'beats p<=.05':>14}")
    g = []
    for rule, sub in d.groupby("rule"):
        rec = {"rule": rule, "n": len(sub),
               "median_t": sub["t_hac"].median(),
               "median_excess": sub["excess_pts"].median(),
               "frac_t_neg": (sub["t_hac"] < 0).mean(),
               "n_worse": int((sub["p_worse_than_hold"] <= ALPHA).sum()),
               "n_beats": int((sub["p_beats_hold"] <= ALPHA).sum())}
        g.append(rec)
    g.sort(key=lambda r: r["median_t"], reverse=True)
    for r in g:
        print(f"  {r['rule']:<16}{r['median_t']:>8.2f}{r['median_excess']:>12.2f}"
              f"{100 * r['frac_t_neg']:>7.0f}%{r['n_worse']:>14,}{r['n_beats']:>14,}")

    neg = sum(1 for r in g if r["median_t"] < 0)
    k = len(g)
    print(f"\n  {neg} of {k} rules have a NEGATIVE median t.")
    print(f"    sign test treating the {k} rules as independent : "
          f"p = {0.5 ** k:.4f} (one-sided)")
    print(f"    the same test at the measured n_eff of 4.0      : "
          f"p = {0.5 ** 4:.4f}")
    print("  The second is the one to quote. scripts/redundancy.py put this")
    print("  board at 3-9 independent ideas in 9 labels, so 9 rules all leaning")
    print("  the same way is nearer four coin flips than nine.")
    return g


def by_axis(d, axis):
    """Is the underperformance everywhere, or concentrated in one corner?"""
    print(f"\nBY {axis.upper()}")
    print(f"  {axis:<12}{'cells':>7}{'med t':>8}{'med excess':>12}"
          f"{'worse p<=.05':>14}{'beats p<=.05':>14}")
    for val, sub in d.groupby(axis):
        print(f"  {str(val):<12}{len(sub):>7,}{sub['t_hac'].median():>8.2f}"
              f"{sub['excess_pts'].median():>12.2f}"
              f"{int((sub['p_worse_than_hold'] <= ALPHA).sum()):>14,}"
              f"{int((sub['p_beats_hold'] <= ALPHA).sum()):>14,}")


def power(d):
    """The claim's own limits, stated rather than left to the reader."""
    mde = d["mde_80"].dropna()
    print("\nPOWER, so the claim is not read wider than it is")
    print(f"  median detectable edge (80% power): {mde.median():.2f} CAGR pts/yr")
    print(f"  median measured excess            : {d['excess_pts'].median():.2f}")
    print(f"  cells whose shortfall EXCEEDS their own detectable edge: "
          f"{int((-d['excess_pts'] >= d['mde_80']).sum()):,} of {len(d):,}")
    print("  A shortfall smaller than the detectable edge is consistent with")
    print("  'no difference' as well as with 'worse'; the counts above are the")
    print("  cells where the data could actually tell the two apart.")


def main():
    d = load()
    t = tails(d)
    rules = per_rule(d)
    for axis in ("universe", "start", "priority"):
        by_axis(d, axis)
    power(d)

    print(f"\n{'=' * 78}\nVERDICT")
    worse, beats = t["p_worse_than_hold"], t["p_beats_hold"]
    print(f"  cells significantly WORSE than hold : {worse['n_uncorrected']:,} "
          f"uncorrected, {worse['n_bh']:,} after BH, {worse['n_bonferroni']:,} "
          f"after Bonferroni")
    print(f"  cells significantly BETTER          : {beats['n_uncorrected']:,} / "
          f"{beats['n_bh']:,} / {beats['n_bonferroni']:,}")
    print(f"  rules with a negative median t      : "
          f"{sum(1 for r in rules if r['median_t'] < 0)} of {len(rules)}")

    out = OUT / "measurements" / f"hold_dominance_{date.today()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False)
    print(f"\n  wrote {out.relative_to(OUT.parent)} "
          f"({len(d)} rows x {len(d.columns)} cols)")


if __name__ == "__main__":
    main()

"""Merge the out-of-band diagnostics into dashboard.json, and set their bars.

    python3 -m scripts.attach_diagnostics            # merge the current run
    python3 -m scripts.attach_diagnostics --check    # report, change nothing
    python3 -m scripts.attach_diagnostics --strip    # remove them again

WHAT IT ADDS to the payload, all keyed by the grid's own cell key so the page
joins on it with no translation:

    daily_excess[cell]   {days, nw_lag, t_hac, p, excess_pts, mde_80}
    fill_timing[cell]    {cagr, taken, wiped, beats_hold}
    diagnostics          the bars, the counts, and what is provisional

WHY THE BARS LIVE HERE and not in the page. 9,500 cells tested at once is 9,500
chances to be lucky; at an uncorrected 5% about 475 would clear on noise alone.
The board already refuses that argument on the trade side (validation.luck_hurdle
is a Bonferroni bar over n_eff correlated variants), so the daily test gets the
same treatment rather than a nominal p. Three bars are computed and all three
travel to the page:

    uncorrected    p <= 0.05                  -- shown, never gated on
    Benjamini-Hochberg  FDR 5%                -- THE GATE
    Bonferroni     p <= 0.05 / n              -- the strictest, shown

BH rather than Bonferroni for the gate because these cells are heavily
correlated -- the same 19 rules re-run over overlapping universes and start
years -- and Bonferroni on correlated tests is far more conservative than its
own nominal level. BH controls the false DISCOVERY rate, which is the question
a reader of this board is actually asking: of the rows it marks validated, what
share are noise?

Reads:  output/wf_attach_<built date>.json, /data/clean/kitelab/dashboard.json
Writes: /data/clean/kitelab/dashboard.json (in place, atomically)
Cost:   seconds. Safe to re-run; it replaces its own keys rather than appending.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kitelab.config import CLEAN

OUT = Path(__file__).resolve().parent.parent / "output"
ALPHA = 0.05
KEYS = ("daily_excess", "fill_timing", "diagnostics")


def bh_threshold(ps, alpha=ALPHA):
    """Largest p that survives Benjamini-Hochberg at `alpha`, or 0.0 if none.

    Sort ascending; the discovery set is every p at or below the largest rank i
    where p(i) <= alpha * i / n. Returning the threshold rather than the set
    lets the page decide one cell at a time with a single comparison.
    """
    if not ps:
        return 0.0
    ordered = sorted(ps)
    n = len(ordered)
    keep = 0.0
    for i, p in enumerate(ordered, 1):
        if p <= alpha * i / n:
            keep = p
    return keep


def summarise(daily, fill, provisional, verified):
    ps = [c["p"] for c in daily.values() if c.get("p") is not None]
    n = len(ps)
    bh = bh_threshold(ps)
    bonf = ALPHA / n if n else 0.0
    mdes = sorted(c["mde_80"] for c in daily.values() if c.get("mde_80") is not None)
    days = sorted(c["days"] for c in daily.values() if c.get("days") is not None)
    return {
        "median_days": (days[len(days) // 2] if days else None),
        "alpha": ALPHA,
        "n_tested": n,
        "bh_threshold": round(bh, 8),
        "bonferroni_threshold": round(bonf, 8),
        "n_uncorrected": sum(1 for p in ps if p <= ALPHA),
        "n_bh": sum(1 for p in ps if bh and p <= bh),
        "n_bonferroni": sum(1 for p in ps if p <= bonf),
        "expected_by_chance": round(ALPHA * n, 1),
        "median_mde_80": (mdes[len(mdes) // 2] if mdes else None),
        # scripts.wf_power, 2026-09-08: the seven-window gate's own figures.
        "walk_forward_mde_80": 20.0,
        "walk_forward_false_positive_rate": 0.498,
        "n_fill_timing": len(fill),
        "arm_b_provisional": provisional,
        "arm_b_verified": verified,
    }


def report(diag, daily, fill):
    print(f"\n  DAILY EXCESS TEST -- {diag['n_tested']:,} cells")
    print(f"    median detectable edge (80% power) : "
          f"{diag['median_mde_80']} CAGR pts/yr")
    print(f"      the seven-window gate needed       : "
          f"{diag['walk_forward_mde_80']} pts/yr (scripts.wf_power)")
    print(f"    beats hold, one-sided HAC p <= 0.05: {diag['n_uncorrected']:,} "
          f"(~{diag['expected_by_chance']:.0f} expected by chance alone)")
    print(f"    Benjamini-Hochberg FDR 5% [THE GATE]: {diag['n_bh']:,} "
          f"(p <= {diag['bh_threshold']})")
    print(f"    Bonferroni across {diag['n_tested']:,} cells      : "
          f"{diag['n_bonferroni']:,} (p <= {diag['bonferroni_threshold']:.2e})")
    beats = sum(1 for c in fill.values() if c.get("beats_hold") is True)
    print(f"\n  NEXT-OPEN FILLS -- {len(fill):,} cells")
    print(f"    still beat buy-and-hold when filled at the next open: {beats:,}")
    print(f"    verified engines   : {', '.join(diag['arm_b_verified']) or 'none'}")
    print(f"    PROVISIONAL engines: {len(diag['arm_b_provisional'])} rules "
          f"-- their next-open branch has never been independently checked")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report what would be merged and change nothing")
    ap.add_argument("--strip", action="store_true",
                    help="remove the diagnostics from the payload again")
    args = ap.parse_args()

    dash = CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"No dashboard.json at {dash}")
    payload = json.loads(dash.read_bytes())
    print(f"dashboard.json: built {payload['built']}, "
          f"{len(payload['grid']):,} grid cells")

    if args.strip:
        removed = [k for k in KEYS if k in payload]
        for k in removed:
            payload.pop(k)
        write(dash, payload)
        print(f"  removed {removed or 'nothing'}")
        return

    stamp = payload["built"].split()[0]
    src = OUT / f"wf_attach_{stamp}.json"
    if not src.exists():
        raise SystemExit(
            f"\n  No diagnostics for this build at {src}.\n"
            f"  The payload was built {payload['built']}; compute them first:\n"
            f"      python3 -m scripts.wf_attach\n")
    blob = json.loads(src.read_text())

    # THE STALENESS GUARD. A diagnostic computed against a different build
    # describes a different account. The file name already carries the date;
    # this checks the full stamp, so two builds on the same day cannot be
    # crossed over.
    if blob["built"] != payload["built"]:
        raise SystemExit(
            f"\n  STALE: {src.name} was computed against a payload built "
            f"{blob['built']},\n  but dashboard.json says {payload['built']}. "
            f"Re-run: python3 -m scripts.wf_attach\n")

    daily, fill = blob["daily_excess"], blob["fill_timing"]
    unknown = [k for k in daily if k not in payload["grid"]]
    if unknown:
        raise SystemExit(f"\n  {len(unknown)} diagnostic cells are not in the grid, "
                         f"e.g. {unknown[:3]}. The axes moved; recompute.\n")
    print(f"  {len(daily):,} daily-excess cells, {len(fill):,} fill-timing cells, "
          f"all present in the grid")

    diag = summarise(daily, fill, blob["arm_b_provisional"], blob["arm_b_verified"])
    report(diag, daily, fill)

    if args.check:
        print("\n  --check: nothing was changed.\n")
        return

    payload["daily_excess"] = daily
    payload["fill_timing"] = fill
    payload["diagnostics"] = diag
    write(dash, payload)
    size = dash.stat().st_size / 1e6
    print(f"\n  merged into {dash} ({size:.1f} MB)\n")


def write(path, payload):
    """Atomic: a kill mid-write must not leave a truncated dashboard."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)


if __name__ == "__main__":
    main()

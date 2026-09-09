"""How far does each rule miss, not just how often? (off-board, 2026-09-08)

The walk-forward gate reduces every window to one bit: did the rule out-return
equal-weight hold, yes or no. A rule that trailed by 0.1 points and one that
trailed by 30 score identically. That throws away the effect size, which is
the thing you need to judge whether "loses 84% of windows" means "narrowly"
or "catastrophically".

This reads the built dashboard.json and, for every scenario-cell, keeps the
MARGIN instead: excess = rule CAGR - hold CAGR, in percentage points of CAGR,
for each comparable window. It then reports

  1. the distribution of all per-window margins, per universe;
  2. per cell, the mean margin and an EXACT sign-flip permutation p-value
     (one-sided, H1: mean margin > 0), enumerating all 2^n sign patterns --
     n is 7 or fewer, so 128 patterns is the whole null distribution;
  3. how many cells have a positive mean margin at all, and how many clear
     0.05 -- noting that with n = 7 the smallest attainable p is 1/128.

A window counts only when both sides are known and it is not partial, which
is walk_forward()'s own rule. `recent` has 3 comparable windows, not 7.

Reads:  /data/clean/kitelab/dashboard.json
Writes: output/wf_excess_<built date>.csv
Runs in seconds. No prices, no rebuild, no network.
"""
import csv
import itertools
import json
import statistics
from pathlib import Path

from kitelab.config import CLEAN

OUT = Path(__file__).resolve().parent.parent / "output"
UNIVERSES = ["all", "large", "mid", "small", "recent"]


def load_dashboard() -> dict:
    path = CLEAN / "dashboard.json"
    if not path.exists():
        raise SystemExit(f"No dashboard.json at {path}. Build it first: python3 -m scripts.refresh")
    d = json.loads(path.read_bytes())
    for key in ("validation", "built"):
        if key not in d:
            raise SystemExit(f"dashboard.json is missing required key {key!r}")
    print(f"dashboard.json: built {d['built']}, {len(d['validation'])} strategies")
    return d


def comparable(windows: list[dict]) -> list[dict]:
    """walk_forward()'s own rule for a window that counts."""
    return [w for w in windows
            if w.get("cagr") is not None and w.get("hold") is not None
            and not w.get("partial")]


def cells_for(d: dict, universe: str) -> list[dict]:
    """Every (strategy, priority, capital) record in `universe`, with margins."""
    out, dropped = [], 0
    for strat, rec in d["validation"].items():
        for scen, wf in (rec.get("walk_forward_by_scenario") or {}).items():
            if scen.split("|")[0] != universe:
                continue
            keep = comparable(wf.get("windows") or [])
            dropped += len(wf.get("windows") or []) - len(keep)
            if not keep:
                continue
            out.append({
                "strategy": strat, "scenario": scen,
                "wins": wf.get("wins"), "total": wf.get("total_windows"),
                "labels": [w["from"] for w in keep],
                "excess": [round(w["cagr"] - w["hold"], 4) for w in keep],
            })
    print(f"  {len(out)} cells, {dropped} windows dropped (partial or no benchmark)")
    return out


def sign_flip_p(excess: list[float]) -> float:
    """Exact one-sided randomization test, H1: mean margin > 0.

    Under the null the margin is symmetric about zero, so every assignment of
    signs to the observed magnitudes is equally likely. With n <= 7 the whole
    null distribution is 2^n <= 128 points, so this is exact, not sampled.
    The smallest p it can return is 1/2^n -- 0.0078 at n = 7, 0.125 at n = 3.
    """
    mags = [abs(x) for x in excess]
    obs = sum(excess)
    atleast = sum(1 for signs in itertools.product((-1, 1), repeat=len(mags))
                  if sum(s * m for s, m in zip(signs, mags)) >= obs - 1e-12)
    return atleast / 2 ** len(mags)


def spread(values: list[float]) -> dict:
    v = sorted(values)
    n = len(v)
    q = lambda f: v[min(n - 1, int(f * n))]
    return {"n": n, "min": v[0], "p10": q(0.10), "q1": q(0.25), "median": q(0.50),
            "q3": q(0.75), "p90": q(0.90), "max": v[-1],
            "mean": statistics.fmean(v)}


def report(d: dict, universe: str, writer) -> list[float]:
    print(f"\n{'=' * 74}\nUNIVERSE {universe!r}")
    cells = cells_for(d, universe)
    if not cells:
        print("  no walk-forward records -- skipped")
        return []

    every = [x for c in cells for x in c["excess"]]
    s = spread(every)
    print(f"\n  per-window margin (rule CAGR - hold CAGR, percentage points), "
          f"{s['n']} comparisons")
    print(f"    min {s['min']:>7.1f}   p10 {s['p10']:>6.1f}   q1 {s['q1']:>6.1f}   "
          f"median {s['median']:>6.1f}")
    print(f"    q3  {s['q3']:>7.1f}   p90 {s['p90']:>6.1f}   max {s['max']:>6.1f}   "
          f"mean   {s['mean']:>6.1f}")
    won = sum(1 for x in every if x > 0)
    near = sum(1 for x in every if -2 <= x <= 0)
    bad = sum(1 for x in every if x < -10)
    print(f"    ahead            {won:>5} ({100*won/len(every):>5.1f}%)")
    print(f"    behind by <= 2   {near:>5} ({100*near/len(every):>5.1f}%)  <- near misses")
    print(f"    behind by > 10   {bad:>5} ({100*bad/len(every):>5.1f}%)  <- blowouts")

    rows = []
    for c in cells:
        m = statistics.fmean(c["excess"])
        rows.append({
            "universe": universe, "strategy": c["strategy"], "scenario": c["scenario"],
            "windows": len(c["excess"]), "wins": c["wins"],
            "mean_excess": round(m, 2),
            "median_excess": round(statistics.median(c["excess"]), 2),
            "sd_excess": round(statistics.stdev(c["excess"]), 2) if len(c["excess"]) > 1 else None,
            "worst_window": round(min(c["excess"]), 2),
            "best_window": round(max(c["excess"]), 2),
            "p_sign_flip": round(sign_flip_p(c["excess"]), 4),
        })
    rows.sort(key=lambda r: r["mean_excess"], reverse=True)
    for r in rows:
        writer.writerow(r)

    pos = [r for r in rows if r["mean_excess"] > 0]
    sig = [r for r in rows if r["p_sign_flip"] <= 0.05]
    floor = 1 / 2 ** rows[0]["windows"]
    print(f"\n  cells with a POSITIVE mean margin: {len(pos)}/{len(rows)}")
    print(f"  cells with sign-flip p <= 0.05    : {len(sig)}/{len(rows)}"
          f"   (smallest attainable p = {floor:.4f})")
    print("\n  best 3 cells by mean margin:")
    for r in rows[:3]:
        print(f"    {r['mean_excess']:>7.1f} pts/yr  p={r['p_sign_flip']:.4f}  "
              f"wins {r['wins']}/{r['windows']}  {r['strategy']} {r['scenario']}")
    print(f"  worst cell: {rows[-1]['mean_excess']:.1f} pts/yr  "
          f"{rows[-1]['strategy']} {rows[-1]['scenario']}")
    return every


def main() -> None:
    d = load_dashboard()
    OUT.mkdir(exist_ok=True)
    stamp = d["built"].split()[0]
    path = OUT / f"wf_excess_{stamp}.csv"
    fields = ["universe", "strategy", "scenario", "windows", "wins", "mean_excess",
              "median_excess", "sd_excess", "worst_window", "best_window", "p_sign_flip"]
    pooled = []
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for uni in UNIVERSES:
            pooled += report(d, uni, w)

    print(f"\n{'=' * 74}\nALL UNIVERSES POOLED: {len(pooled)} window comparisons")
    s = spread(pooled)
    print(f"  median margin {s['median']:.1f} pts/yr, mean {s['mean']:.1f}, "
          f"q1 {s['q1']:.1f}, q3 {s['q3']:.1f}")
    print(f"  ahead in {sum(1 for x in pooled if x > 0)} of {len(pooled)} "
          f"({100*sum(1 for x in pooled if x > 0)/len(pooled):.1f}%)")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

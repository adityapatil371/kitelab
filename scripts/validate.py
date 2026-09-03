"""Is the edge real, or is it a pattern found in noise?

    python -m scripts.validate                    # every test, primary variants
    python -m scripts.validate --only walkforward top-n
    python -m scripts.validate --all-variants     # all 24, not one per family
    python -m scripts.validate --permutations 20

DIFFERENT FROM tests/. Those check that the CODE does what it was told, which is
necessary and says nothing about whether a strategy makes money. These are the
checks a trader runs on a strategy before believing it. A rule can pass every
unit test in the project and still be a curve fit.

Six tests, and each answers a question the compare table cannot:

  BENCHMARK      Did the rule beat simply owning the same stocks? The only
                 comparison that matters, and the one missing from the headline
                 table -- a rule can rank first among 24 and still lose to doing
                 nothing.
  WALK-FORWARD   Does it hold in DISJOINT periods? The dashboard's start-year
                 axis is cumulative -- every window ends today, so one strong
                 stretch flatters them all. These windows do not overlap.
  TOP-N          Delete the best few trades. If the edge dies, it was a lottery
                 ticket, not a rule. Aimed squarely at the Turtle, whose return
                 is already known to live in a thin tail.
  BREAKEVEN COST How much friction does the edge survive? A rule that dies at
                 0.3% a side is untradeable whatever its backtest says.
  CORRELATION    Are 24 variants 24 bets, or three? If they take the same
                 trades, the multiple-testing count is overstated and
                 "diversifying across rules" is an illusion.
  PERMUTATION    Run the rule on SHUFFLED returns -- same distribution, no
                 structure. Anything it earns there is fitted noise. The
                 sharpest test here, and the slowest.

Reads the cached trades the dashboard uses, so it describes the rules as
published rather than a re-simulation that might differ.

THE MATH LIVES IN kitelab/validation.py, not here -- scripts/dashboard_data.py
puts the same numbers on the dashboard and needed to call the same functions
rather than a re-derived copy, on pain of repeating the "two samplers, two
answers" bug from 2026-09-03. This file is the CLI presentation only.
"""
from __future__ import annotations

import argparse
import statistics

import numpy as np

from kitelab import config, registry
from kitelab.validation import (
    CAPITAL, RISK, TOP_N, WINDOW_YEARS,
    breakeven_cost, buy_and_hold, cagr_of, correlate, load, monthly_returns,
    permutation_test, walk_forward, without_best,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None,
                    choices=["benchmark", "walkforward", "top-n", "cost",
                             "correlation", "permutation"])
    ap.add_argument("--all-variants", action="store_true")
    ap.add_argument("--permutations", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20260903)
    args = ap.parse_args()
    want = set(args.only) if args.only else {"benchmark", "walkforward", "top-n",
                                             "cost", "correlation", "permutation"}

    cfg = config.load()
    universe = cfg.merged
    rng = np.random.default_rng(args.seed)

    picked = (registry.REGISTRY if args.all_variants else
              [registry.find(f, registry.primary(f)) for f in registry.families()])
    picked = [s for s in picked if s is not None]

    loaded = {}
    for s in picked:
        t = load(s, universe)
        if t:
            loaded[s.label] = (s, t)
    if not loaded:
        raise SystemExit("\n  No cached trades. Run: python -m scripts.refresh\n")

    print(f"\n  {len(universe)} stocks, Rs{CAPITAL:,} at {RISK:.0%} risk. "
          f"{len(loaded)} rules.\n")

    # ---- benchmark -------------------------------------------------------
    if "benchmark" in want:
        bh = buy_and_hold(universe)
        print("  DID IT BEAT SIMPLY OWNING THE STOCKS?")
        print(f"    equal-weight buy and hold, median stock: {bh:.1f}% a year\n")
        print(f"    {'rule':<34}{'CAGR':>9}{'vs hold':>10}")
        print("    " + "-" * 51)
        beat = 0
        for label, (s, t) in loaded.items():
            got = cagr_of(t)
            if got is None:
                continue
            beat += got > bh
            print(f"    {label:<34}{got:>8.1f}%{got - bh:>9.1f}")
        print(f"\n    {beat} of {len(loaded)} beat doing nothing.\n")

    # ---- walk-forward ----------------------------------------------------
    if "walkforward" in want:
        print(f"  DOES IT HOLD IN DISJOINT {WINDOW_YEARS}-YEAR WINDOWS?")
        print("    (the dashboard's start-year axis is cumulative; these do not overlap)\n")
        for label, (s, t) in loaded.items():
            rows = walk_forward(t)
            if not rows:
                continue
            vals = [c for _, _, c in rows if c is not None]
            pos = sum(1 for c in vals if c > 0)
            spans = "  ".join(f"{a}-{b}:{c:>6.1f}" if c is not None else f"{a}-{b}:    --"
                              for a, b, c in rows)
            print(f"    {label}")
            print(f"      {spans}")
            print(f"      positive in {pos}/{len(vals)} windows\n")

    # ---- top-N -----------------------------------------------------------
    if "top-n" in want:
        print("  HOW MUCH RIDES ON THE BEST FEW TRADES?")
        head = f"    {'rule':<34}{'all':>8}" + "".join(f"{'-' + str(n):>8}" for n in TOP_N)
        print(head); print("    " + "-" * (len(head) - 4))
        for label, (s, t) in loaded.items():
            base = cagr_of(t)
            cells = "".join(
                f"{(without_best(t, n) or float('nan')):>8.1f}" for n in TOP_N)
            print(f"    {label:<34}{(base or float('nan')):>8.1f}{cells}")
        print("    columns: CAGR with the best 1, 5, 10, 25 trades deleted\n")

    # ---- breakeven cost --------------------------------------------------
    if "cost" in want:
        print("  HOW MUCH FRICTION DOES THE EDGE SURVIVE?")
        print(f"    {'rule':<34}{'breakeven':>12}")
        print("    " + "-" * 46)
        for label, (s, t) in loaded.items():
            got = breakeven_cost(t)
            text = ("already negative" if got is None else
                    "survives 200bp+" if got == float("inf") else f"{got:.1f} bp/side")
            print(f"    {label:<34}{text:>12}")
        print("    Zerodha delivery plus a modelled spread is roughly 15-40 bp a side.\n")

    # ---- correlation -----------------------------------------------------
    if "correlation" in want and len(loaded) > 1:
        print("  ARE THESE DIFFERENT BETS, OR THE SAME ONE?")
        labels = list(loaded)
        months = {k: monthly_returns(v[1]) for k, v in loaded.items()}
        print(f"    {'':<20}" + "".join(f"{l.split(chr(183))[0][:7]:>8}" for l in labels))
        for a in labels:
            cells = ""
            for b in labels:
                c = 1.0 if a == b else correlate(months[a], months[b])
                cells += f"{c:>8.2f}" if c is not None else f"{'--':>8}"
            print(f"    {a[:20]:<20}{cells}")
        pairs = [correlate(months[a], months[b])
                 for i, a in enumerate(labels) for b in labels[i + 1:]]
        pairs = [p for p in pairs if p is not None]
        if pairs:
            print(f"\n    median pairwise correlation {statistics.median(pairs):.2f} "
                  f"-- above ~0.7 these are one bet wearing several names.\n")

    # ---- permutation -----------------------------------------------------
    if "permutation" in want:
        print("  DOES IT STILL WORK WHEN THE STRUCTURE IS REMOVED?")
        print("    Each stock's daily returns shuffled -- same distribution, no")
        print(f"    sequence. {args.permutations} rounds on a 60-stock sample.\n")
        print(f"    {'rule':<34}{'real':>8}{'shuffled median':>17}{'beat by':>9}")
        print("    " + "-" * 68)
        for label, (s, t) in loaded.items():
            observed, got = permutation_test(s, universe, args.permutations, rng)
            if observed is None or not got:
                print(f"    {label:<34}{'--':>8}")
                continue
            med = statistics.median(got)
            worse = sum(1 for g in got if g >= observed)
            flag = "" if worse * 4 <= len(got) else "   <-- NOT DISTINGUISHABLE"
            print(f"    {label:<34}{observed:>7.1f}%{med:>16.1f}%"
                  f"{observed - med:>8.1f}{flag}")
        print("\n    A rule that earns as much on shuffled prices as on real ones")
        print("    has found no structure -- it is fitting noise.\n")


if __name__ == "__main__":
    main()

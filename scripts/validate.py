"""Is the edge real, or is it a pattern found in noise?

    python -m scripts.validate                    # every test, primary variants
    python -m scripts.validate --only walkforward top-n
    python -m scripts.validate --all-variants     # every variant on the board, not one per family
    python -m scripts.validate --permutations 50
    python -m scripts.validate --realistic        # the build's execution state, not on paper

DIFFERENT FROM tests/. Those check that the CODE does what it was told, which is
necessary and says nothing about whether a strategy makes money. These are the
checks a trader runs on a strategy before believing it. A rule can pass every
unit test in the project and still be a curve fit.

Six tests, and each answers a question the compare table cannot:

  BENCHMARK      Did the rule beat simply owning the same stocks? An
                 equal-weight PORTFOLIO of the universe from the start year
                 (not the median stock -- see kitelab.validation.buy_and_hold
                 for the 8.5-vs-17.0 gap that changed on 2026-09-07).
  WALK-FORWARD   Does it hold in DISJOINT periods? One fixed calendar of
                 3-year windows from 2006 for every rule, and a window is a
                 win only if the rule beat holding the stocks over it.
  TOP-N          Delete the best few trades. If the edge dies, it was a lottery
                 ticket, not a rule. Aimed squarely at the Turtle, whose return
                 is already known to live in a thin tail.
  BREAKEVEN COST How much friction does the edge survive? A rule that dies at
                 0.3% a side is untradeable whatever its backtest says.
  CORRELATION    Are the variants that many bets, or three? If they take the
                 same trades, the multiple-testing count is overstated and
                 "diversifying across rules" is an illusion.
  PERMUTATION    Run the rule on a market whose DAYS are shuffled -- same
                 distribution, same cross-section, no sequence. Anything it
                 earns there is fitted noise. The sharpest test here, and the
                 slowest: 200 rounds over a random 60-stock sample per rule.

WHAT ACCOUNT THIS RUNS. By default, ON PAPER: the cached trade lists as
built -- no spread, no market impact, no size cap -- with every account-level
number (benchmark comparison, walk-forward, top-N, breakeven, the permutation
CAGRs) from portfolio.run at Rs2L, 1% risk, most-liquid-first. The dashboard
computes the same checks on the SPREAD-ADJUSTED lists under the build's
Realistic-fills state (costs on, one order <= 1% of daily turnover), so its
numbers are lower; `--realistic` applies that state here so the two agree.

THE MATH LIVES IN kitelab/validation.py, not here -- scripts/dashboard_data.py
puts the same numbers on the dashboard and needed to call the same functions
rather than a re-derived copy, on pain of repeating the "two samplers, two
answers" bug from 2026-09-03. This file is the CLI presentation only.
"""
from __future__ import annotations

import argparse
import statistics

import numpy as np

from kitelab import config, registry, slippage
from kitelab.validation import (
    ALPHA, CAPITAL, PERMUTATION_ROUNDS, PERMUTATION_SAMPLE, RISK, TOP_N, WINDOW_YEARS,
    breakeven_cost, buy_and_hold, cagr_of, correlate, hold_by_window, load,
    monthly_returns, permutation_test, walk_forward, without_best,
)

START_YEAR = 2018      # the reference year the page quotes (scripts.dashboard_data.START_DEFAULT)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None,
                    choices=["benchmark", "walkforward", "top-n", "cost",
                             "correlation", "permutation"])
    ap.add_argument("--all-variants", action="store_true")
    ap.add_argument("--permutations", type=int, default=PERMUTATION_ROUNDS)
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--start-year", type=int, default=START_YEAR,
                    help="benchmark start year (default: the page's reference year)")
    ap.add_argument("--realistic", action="store_true",
                    help="spread-adjust the cached trades and run the build's "
                         "costs-and-cap execution state instead of on paper")
    args = ap.parse_args()
    want = set(args.only) if args.only else {"benchmark", "walkforward", "top-n",
                                             "cost", "correlation", "permutation"}

    cfg = config.load()
    universe = cfg.merged
    rng = np.random.default_rng(args.seed)

    picked = (registry.REGISTRY if args.all_variants else
              [registry.find(f, registry.primary(f)) for f in registry.families()])
    picked = [s for s in picked if s is not None]

    if args.realistic:
        from .dashboard_data import REALISTIC_PARTICIPATION
        slippage.ENABLED = True
        slippage.MAX_PARTICIPATION = REALISTIC_PARTICIPATION
        slippage.reset()

    loaded = {}
    for s in picked:
        t = load(s, universe)
        if t:
            loaded[s.label] = (s, [slippage.apply_spread(x) for x in t] if args.realistic else t)
    if not loaded:
        raise SystemExit("\n  No cached trades. Run: python -m scripts.refresh\n")

    state = ("realistic fills: spread, impact and the size cap on" if args.realistic
             else "on paper: no spread, no impact, no size cap")
    print(f"\n  {len(universe)} stocks, Rs{CAPITAL:,} at {RISK:.0%} risk, most-liquid-first. "
          f"{len(loaded)} rules. {state}.\n")

    # ---- benchmark -------------------------------------------------------
    if "benchmark" in want:
        bh = buy_and_hold(universe, start_year=args.start_year)
        print("  DID IT BEAT SIMPLY OWNING THE STOCKS?")
        print(f"    equal-weight portfolio of all {len(universe)} from 1 Jan {args.start_year}, "
              f"late listings joining at their first close: {bh:.1f}% a year")
        print("    (the rules' CAGR below is over their whole history; the page compares\n"
              "     each start year against its own benchmark)\n")
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
        print("    (one fixed calendar from 2006; a window is a WIN only if the rule beat\n"
              "     equal-weight buy-and-hold of the universe over that same window)\n")
        hold = hold_by_window(universe)
        for label, (s, t) in loaded.items():
            wf = walk_forward(t, hold=hold)
            if not wf["windows"]:
                continue
            print(f"    {label}")
            for w in wf["windows"]:
                cagr = f"{w['cagr']:>6.1f}" if w["cagr"] is not None else "    --"
                bench = f"{w['hold']:>6.1f}" if w["hold"] is not None else "    --"
                verdict = ("partial, not counted" if w["partial"] else
                           "under 20 trades" if w["cagr"] is None else
                           "WIN" if w["win"] else "lost to hold")
                print(f"      {w['from']} to {w['to']}  rule {cagr}  hold {bench}  {verdict}")
            print(f"      won {wf['wins']} of {wf['total_windows']} counted windows"
                  f"{'  (majority)' if wf['wins'] * 2 > wf['total_windows'] else ''}\n")

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
        print("    The calendar's days shuffled, the same way for every stock -- same")
        print(f"    distribution, same cross-section, no sequence. {args.permutations} rounds "
              f"on a random {PERMUTATION_SAMPLE}-stock sample.\n")
        print(f"    {'rule':<34}{'real':>8}{'shuffled median':>17}{'beat by':>9}{'p':>8}")
        print("    " + "-" * 76)
        for label, (s, t) in loaded.items():
            observed, got, pool = permutation_test(s, universe, args.permutations, rng, trades=t)
            if observed is None or not got:
                print(f"    {label:<34}{'--':>8}")
                continue
            med = statistics.median(got)
            worse = sum(1 for g in got if g >= observed)
            p = (worse + 1) / (len(got) + 1)
            flag = "" if p <= ALPHA else "   <-- NOT DISTINGUISHABLE"
            print(f"    {label:<34}{observed:>7.1f}%{med:>16.1f}%"
                  f"{observed - med:>8.1f}{p:>8.3f}{flag}")
        print(f"\n    p = (rounds at least as good + 1) / (rounds + 1); distinguishable at p <= {ALPHA}.")
        print("    A rule that earns as much on shuffled prices as on real ones")
        print("    has found no structure -- it is fitting noise.\n")


if __name__ == "__main__":
    main()

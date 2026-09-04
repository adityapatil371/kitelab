"""How much of each rule's record is edge, and how much is luck?

    python -m scripts.bootstrap                    # every variant, 5,000 draws
    python -m scripts.bootstrap --draws 20000
    python -m scripts.bootstrap --risk 1.0         # fixed fraction per trade, %
    python -m scripts.bootstrap --strategy ema qmw

WHY THIS EXISTS. It is the control that replaced the 101/399 in-sample/holdout
split on 2026-09-03 (see config.Config.merged for the argument). A holdout is a
tool for catching a FITTED model that memorised its training rows; these rules
were specified in class before this repo read a candle, so there was nothing to
memorise and the split was paying 80% of the data for a guarantee it could not
give. What the project is actually exposed to is MULTIPLE TESTING -- 24 variants
ranked and the winner reported -- and that is measurable on all 500 stocks at
once, which is what this does.

THE TEST, which is the one traders actually run on a trade list:

    1. Take a rule's realised trades and reduce each to its NET R multiple --
       net_profit / risk_taken, so costs are already inside it.
    2. Resample that list WITH REPLACEMENT into thousands of alternative
       histories of the same length, in contiguous BLOCKS (trades cluster --
       a rule that is working is working on a market that is rising, and its
       winners arrive together; drawing single trades destroys that and
       reports a narrower spread than the rule really has).
    3. Report a one-sample T-STATISTIC on the net R multiples -- mean divided
       by standard error -- not compounded CAGR.

WHY A T-STATISTIC, NOT COMPOUNDED CAGR (changed 2026-09-04). Compounding is
order-dependent and multiplicative, so a rule's compounded return grows
roughly exponentially with its trade count for ANY positive average edge --
measured at a 0.82 correlation between log(trade count) and the old
CAGR-based ratio across this project's 24 variants. It was ranking "how many
trades did this fire" nearly as much as "is this a real edge", and a rule
with enough trades cleared "distinguishable from luck" at a rate that
saturated near 100% regardless of quality. A t-statistic does not have that
problem: more trades correctly narrows the standard error, not the point
estimate, so it rewards more data with more CONFIDENCE rather than a bigger
number -- the standard practitioner move for exactly this failure mode
(Harvey & Liu, "Backtesting", Journal of Portfolio Management 2015, convert
Sharpe to a t-ratio before applying a multiple-testing correction for the
same reason). Compounded CAGR/MAR are still printed alongside it as context,
because they are still the number that maps onto the account-level grid --
just no longer what the luck-hurdle verdict is computed from.

AND THEN THE PART THAT MATTERS MOST. Twenty-four variants were tried. At a 95%
bar, chance alone clears about 24 x 0.05 of them, so ONE winner is exactly
what an edgeless menu produces and is evidence of nothing. The closing summary
counts how many clear the bar against how many chance predicts, which is the
question the holdout was a clumsy way of asking -- and it corrects for the
fact that 24 EMA-band variants on the same universe are not 24 INDEPENDENT
trials: see kitelab.validation.effective_trials, driven by the average
pairwise correlation of every variant's monthly P&L.

WHAT THIS DOES NOT DO, so it is not read as more than it is:

  * It resamples TRADES, not the account. Cash constraints, the size cap and
    skipped signals are real and already measured by the one-account grid; they
    are deliberately outside this. A rule can pass here and still fail an
    account, and the Holy Grail does exactly that.
  * It cannot see survivorship. Every stock here is one still listed today, and
    that is still the largest known bias in the project (~4.9pp/yr).
  * It does not jointly resample across variants. The 24 bootstrap
    distributions are each built from that ONE rule's own trades independently,
    so the SPREAD used for the hurdle does not itself carry the cross-variant
    correlation structure -- effective_trials() corrects the TRIAL COUNT for
    that correlation, which is the more tractable of two ways to fix this and
    the one implemented here; a fully joint block-bootstrap sharing the same
    resampled date-blocks across all 24 variants would be the more complete
    fix and is not implemented.

THE MATH LIVES IN kitelab/validation.py, not here -- scripts/dashboard_data.py
puts the same figures on the dashboard's "Credibility" column and needed to
call the same functions rather than a re-derived copy. This file is the CLI
presentation only.
"""
from __future__ import annotations

import argparse

from kitelab import config
from kitelab.validation import bootstrap_one, monthly_returns, multiple_testing_summary
from .dashboard_data import STRATEGY_LABELS, positions, signal_lists, tag

DRAWS = 5_000
RISK_PCT = 1.0
BLOCK = 20


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--risk", type=float, default=RISK_PCT,
                    help="fixed fraction of equity risked per trade, in percent")
    ap.add_argument("--strategy", nargs="*", default=None,
                    help="only these strategy keys (default: all)")
    ap.add_argument("--block", type=int, default=BLOCK,
                    help="trades per resampled block; 0 or 1 = naive i.i.d.")
    ap.add_argument("--seed", type=int, default=20260903)
    args = ap.parse_args()

    cfg = config.load()

    print(f"\n  universe   {len(cfg.merged)} stocks (merged; no in-sample/holdout split)")
    print(f"  test       {args.draws:,} resamples at {args.risk:g}% fixed-fractional risk")
    print(f"  blocks     {args.block} trades per block"
          if args.block > 1 else "  blocks     i.i.d. (trade clustering ignored)")
    print("  signal lists (cached where possible):", flush=True)
    lists = signal_lists(cfg.merged)

    rows, monthly_by_key = [], {}
    for (skey, band), trades in sorted(lists.items(), key=lambda kv: str(kv[0])):
        if args.strategy and skey not in args.strategy:
            continue
        held = positions(trades)
        boot = bootstrap_one(held, risk_pct=args.risk, draws=args.draws,
                             seed=args.seed, block=args.block)
        if boot is None:
            print(f"    skipped {skey}/{tag(band)}: fewer than 30 trades")
            continue
        key = f"{STRATEGY_LABELS.get(skey, skey)} · {tag(band)}"
        rows.append({"key": key, **boot})
        monthly_by_key[key] = monthly_returns(held)
        print(f"    {key:<34} {boot['n']:>6} trades", flush=True)

    if not rows:
        print("\n  nothing to report.\n")
        return

    rows.sort(key=lambda x: -x["t_stat"] if x["t_stat"] is not None else 1e9)
    print("\n  Each rule against its own trades. CAGR percentiles are the spread of")
    print("  histories the SAME trades produce in a different order or mix; t-stat")
    print("  is the mean/SE test that decides the luck-hurdle verdict below.\n")
    head = (f"  {'rule':<34}{'n':>6}{'meanR':>7}{'t-stat':>8}"
            f"{'CAGR':>10}{'5th':>10}{'50th':>10}{'95th':>10}{'P(<=0)':>8}")
    print(head)
    print("  " + "-" * (len(head) - 2))
    for x in rows:
        t = f"{x['t_stat']:.2f}" if x["t_stat"] is not None else "  -"
        print(f"  {x['key']:<34}{x['n']:>6}{x['mean_r']:>7.3f}{t:>8}"
              f"{x['obs_cagr']:>10.1f}{x['p05']:>10.1f}{x['p50']:>10.1f}{x['p95']:>10.1f}"
              f"{x['p_neg']:>8.1%}")

    # ---- the multiple-testing summary: the reason this script exists --------
    mt = multiple_testing_summary(rows, monthly_by_key)
    print(f"\n  {mt['tried']} variants tested, average pairwise correlation "
          f"{mt['avg_correlation']:.2f} -- worth {mt['n_eff']:.1f} INDEPENDENT trials.")
    print(f"  {mt['cleared']} cleared a 95% bar (t-stat above the luck hurdle), "
          f"against about {mt['expected_by_chance']:.1f} an edgeless menu of "
          f"{mt['tried']} clears by chance alone at the raw (uncorrected) count.")
    print(f"\n  The luckiest of {mt['n_eff']:.1f} effective edgeless rules would score "
          f"a t-stat of about {mt['hurdle']:.2f}.")
    print(f"  The best rule here scored t={mt['best_t']:.2f} ({mt['best_key']}).")
    if not mt["clears_hurdle"]:
        print("  IT DOES NOT CLEAR THE LUCK HURDLE. Nothing on this board is")
        print("  distinguishable from the best of a menu of coin flips.")
    else:
        print(f"  It clears the hurdle by {mt['best_t'] - mt['hurdle']:.2f}. That is a")
        print("  reason to keep testing it, not a reason to trade it.")
    if mt["cleared"] <= mt["expected_by_chance"]:
        print("  THAT IS NOT EVIDENCE OF AN EDGE. This is what noise looks like when")
        print("  it is ranked: something always comes first.")
    else:
        print(f"  {mt['cleared']} against {mt['expected_by_chance']:.1f} expected. "
              "The rules that cleared:")
        for x in rows:
            if x["key"] in mt["cleared_keys"]:
                print(f"      {x['key']:<34} t-stat {x['t_stat']:>6.2f}")
        print("  Survivors are candidates, not conclusions: they were still chosen")
        print("  after seeing the table, and no further test here is independent of it.")
    print()


if __name__ == "__main__":
    main()

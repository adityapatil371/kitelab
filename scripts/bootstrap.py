"""How much of each rule's record is edge, and how much is luck?

    python -m scripts.bootstrap                    # every variant, 5,000 draws
    python -m scripts.bootstrap --draws 20000
    python -m scripts.bootstrap --risk 1.0         # fixed fraction per trade, %
    python -m scripts.bootstrap --strategy ema qmw
    python -m scripts.bootstrap --no-drift         # skip the random-entry control

WHY THIS EXISTS. It is the control that replaced the 101/399 in-sample/holdout
split on 2026-09-03 (see config.Config.merged for the argument). A holdout is a
tool for catching a FITTED model that memorised its training rows; these rules
were specified in class before this repo read a candle, so there was nothing to
memorise and the split was paying 80% of the data for a guarantee it could not
give. What the project is actually exposed to is MULTIPLE TESTING -- every
variant on the board ranked and the winner reported -- and that is measurable
on the whole universe at once, which is what this does.

THE TEST, which is the one traders actually run on a trade list:

    1. Take a rule's realised trades and reduce each to its NET R multiple --
       net_profit / risk_taken, so costs are already inside it.
    2. Measure the mean against DRIFT, not zero: the mean R that random
       entries on the same stocks, with the same stop distance, holding
       period, spread and charges, would have earned. A rising market hands
       that much to anyone; only the excess is timing.
    3. Report a T-STATISTIC on that excess, with a standard error that is
       CLUSTER-ROBUST by entry quarter -- trades that fire across 999 stocks
       in the same week are one bet on one market, not hundreds of
       independent draws.
    4. Resample calendar QUARTERS with replacement (not blocks of 20 trades,
       which spanned 0.6-15 days for 18 of 19 variants) for the spread of
       mean R the same history could have produced.

The old i.i.d. t (t_iid) and the clustered t before drift (t_cluster) are
printed beside the gate statistic so the reader can see how much of the old
confidence was double-counting and how much was the market. See
kitelab.validation.bootstrap_one for the 2026-09-07 measurements behind each.

WHY A T-STATISTIC, NOT COMPOUNDED CAGR (changed 2026-09-04). Compounding is
order-dependent and multiplicative, so a rule's compounded return grows
roughly exponentially with its trade count for ANY positive average edge --
measured at a 0.82 correlation between log(trade count) and the old
CAGR-based ratio across the board of that day. A t-statistic rewards more
data with more CONFIDENCE rather than a bigger number (Harvey & Liu,
"Backtesting", JPM 2015, convert Sharpe to a t-ratio before correcting for
multiple tests for the same reason). Compounded CAGR percentiles are still
printed as context; they decide nothing.

AND THEN THE PART THAT MATTERS MOST. Many variants were tried. The closing
summary computes how many INDEPENDENT bets they amount to
(kitelab.validation.effective_trials, from the correlation of monthly P&L),
sets the hurdle so that the chance ANY edgeless rule among that many clears
it is ALPHA (kitelab.validation.luck_hurdle -- the expected best-of-luck score
is printed too, and it is lower), and counts how many cleared against how many
chance predicts at the raw count.

WHAT THIS DOES NOT DO, so it is not read as more than it is:

  * It resamples TRADES, not the account. Cash constraints, the size cap and
    skipped signals are real and already measured by the one-account grid; they
    are deliberately outside this. A rule can pass here and still fail an
    account, and the Holy Grail does exactly that.
  * It cannot see survivorship. Every stock here is one still listed today, and
    that is still the largest known bias in the project (~4.9pp/yr).
  * It does not jointly resample across variants. Each distribution is built
    from that ONE rule's own quarters; effective_trials() corrects the TRIAL
    COUNT for cross-variant correlation, which is the tractable half of the
    fix. A joint bootstrap sharing the same resampled quarters across every
    variant would be the complete one and is not implemented.
  * It runs ON PAPER -- the cached lists as built, before the spread. The
    page's Credibility column runs the same function on the spread-adjusted
    list, so its numbers are a little lower.

THE MATH LIVES IN kitelab/validation.py, not here -- scripts/dashboard_data.py
puts the same figures on the dashboard's "Credibility" column and needed to
call the same functions rather than a re-derived copy. This file is the CLI
presentation only.
"""
from __future__ import annotations

import argparse

from kitelab import config, strategies
from kitelab.validation import DRAWS, RISK_PCT, bootstrap_one, monthly_returns, multiple_testing_summary
from .dashboard_data import STRATEGY_LABELS, signal_lists, tag


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--risk", type=float, default=RISK_PCT,
                    help="fixed fraction of equity risked per trade, in percent")
    ap.add_argument("--strategy", nargs="*", default=None,
                    help="only these strategy keys (default: all)")
    ap.add_argument("--no-drift", action="store_true",
                    help="measure mean R against zero instead of random entries")
    ap.add_argument("--seed", type=int, default=20260903)
    args = ap.parse_args()

    cfg = config.load()

    print(f"\n  universe   {len(cfg.merged)} stocks (merged; no in-sample/holdout split)")
    print(f"  test       {args.draws:,} resamples of calendar quarters at {args.risk:g}% "
          f"fixed-fractional risk; standard errors clustered by entry quarter")
    print("  drift      " + ("off: mean R against zero" if args.no_drift else
                             "random entries on the same stocks, same stop distance and hold"))
    print("  signal lists (cached where possible):", flush=True)
    lists = signal_lists(cfg.merged)

    rows, monthly_by_key = [], {}
    for (skey, band), trades in sorted(lists.items(), key=lambda kv: str(kv[0])):
        if args.strategy and skey not in args.strategy:
            continue
        held = strategies.drop_overlaps(trades)      # one position per symbol, as the page
        boot = bootstrap_one(held, risk_pct=args.risk, draws=args.draws,
                             seed=args.seed, drift=not args.no_drift)
        if boot is None:
            print(f"    skipped {skey}/{tag(band)}: fewer than 30 trades")
            continue
        key = f"{STRATEGY_LABELS.get(skey, skey)} · {tag(band)}"
        rows.append({"key": key, **boot})
        monthly_by_key[key] = monthly_returns(held)
        print(f"    {key:<34} {boot['n']:>6} trades in {boot['n_clusters']} quarters", flush=True)

    if not rows:
        print("\n  nothing to report.\n")
        return

    rows.sort(key=lambda x: -x["t_stat"] if x["t_stat"] is not None else 1e9)
    print("\n  Each rule against its own trades. meanR is the average net R; drift is what")
    print("  random entries earned; t-stat = (meanR - drift) / clustered SE decides the")
    print("  luck-hurdle verdict below. t-iid is the old, unclustered figure; t-clus is")
    print("  clustered but against zero. CAGR columns compound the trades at the risk")
    print("  fraction and are context only.\n")
    head = (f"  {'rule':<34}{'n':>7}{'meanR':>7}{'drift':>7}{'t-iid':>7}{'t-clus':>7}"
            f"{'t-stat':>7}{'CAGR':>9}{'5th':>9}{'95th':>9}{'P(<=0)':>8}")
    print(head)
    print("  " + "-" * (len(head) - 2))

    def num(v, fmt):
        return format(v, fmt) if v is not None else "-"
    for x in rows:
        print(f"  {x['key']:<34}{x['n']:>7}{x['mean_r']:>7.3f}{num(x['drift_r'], '.3f'):>7}"
              f"{num(x['t_iid'], '.2f'):>7}{num(x['t_cluster'], '.2f'):>7}"
              f"{num(x['t_stat'], '.2f'):>7}"
              f"{x['obs_cagr']:>9.1f}{x['p05']:>9.1f}{x['p95']:>9.1f}{x['p_neg']:>8.1%}")

    # ---- the multiple-testing summary: the reason this script exists --------
    mt = multiple_testing_summary(rows, monthly_by_key)
    print(f"\n  {mt['tried']} variants tested, average pairwise correlation "
          f"{mt['avg_correlation']:.2f} -- worth {mt['n_eff']:.1f} INDEPENDENT trials.")
    print(f"  Hurdle: t > {mt['hurdle']:.2f}, so that the chance ANY of {mt['n_eff']:.1f} "
          f"edgeless rules clears it is {mt['alpha']:.0%}. (The luckiest of them would")
    print(f"  typically score {mt['expected_best']:.2f} -- the old hurdle, which an edgeless "
          f"board's best clears about half the time.)")
    print(f"  {mt['cleared']} cleared it, against about {mt['expected_by_chance']:.1f} an "
          f"edgeless menu of {mt['tried']} clears an UNCORRECTED {mt['alpha']:.0%} bar.")
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
        print("  The rules that cleared:")
        for x in rows:
            if x["key"] in mt["cleared_keys"]:
                print(f"      {x['key']:<34} t-stat {x['t_stat']:>6.2f}")
        print("  Survivors are candidates, not conclusions: they were still chosen")
        print("  after seeing the table, and no further test here is independent of it.")
    print()


if __name__ == "__main__":
    main()

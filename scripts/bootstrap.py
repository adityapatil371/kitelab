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
       histories of the same length, compounding at a fixed fractional risk.
    3. Read off the distribution of CAGR, max drawdown and MAR.

The observed record is one draw from that distribution. If its 5th percentile
CAGR is still above zero the edge is distinguishable from luck on the rule's own
trades; if it is not, the headline number is a sequence that happened.

AND THEN THE PART THAT MATTERS MOST. Twenty-four variants were tried. At a 95%
bar, chance alone clears about 24 x 0.05 = 1.2 of them, so ONE winner is exactly
what an edgeless menu produces and is evidence of nothing. The closing summary
counts how many clear the bar against how many chance predicts, which is the
question the holdout was a clumsy way of asking.

WHAT THIS DOES NOT DO, so it is not read as more than it is:

  * It resamples TRADES, not the account. Cash constraints, the size cap and
    skipped signals are real and already measured by the one-account grid; they
    are deliberately outside this. A rule can pass here and still fail an
    account, and the Holy Grail does exactly that.
  * Resampling in BLOCKS, not one trade at a time. Trades are not independent
    draws: they cluster, because a rule that is working is working on a market
    that is rising, and its winners arrive together. Drawing single trades
    destroys that structure and reports a narrower spread than the rule really
    has -- flattering it. Contiguous blocks keep neighbouring trades together
    (--block 0 restores the naive i.i.d. version for comparison).
  * It cannot see survivorship. Every stock here is one still listed today, and
    that is still the largest known bias in the project (~4.9pp/yr).
"""
from __future__ import annotations

import argparse
import math
from statistics import NormalDist

import numpy as np

from kitelab import config
from .dashboard_data import STRATEGY_LABELS, positions, signal_lists, tag

DRAWS = 5_000
RISK_PCT = 1.0
CHUNK = 250          # iterations per block, so a 20k-trade rule stays in memory
# Trades per contiguous block. Around a year of signals for the busier rules,
# which is the scale on which market regimes -- and therefore runs of winners --
# actually persist. Not tuned: tuning it on the answer would be the very thing
# this script exists to detect.
BLOCK = 20


def r_multiples(trades: list[dict]) -> np.ndarray:
    """Net R per trade: what the trade returned as a multiple of what it risked.

    net_profit, not gross, so brokerage, STT and the modelled spread are already
    inside every number here. Trades that risked nothing measurable (risk_taken
    of zero -- a stop at the entry price) carry no R and are dropped rather than
    counted as flat, which would dilute the distribution toward zero.
    """
    return np.array([t["net_profit"] / t["risk_taken"] for t in trades
                     if t.get("risk_taken")], dtype=float)


def span_years(trades: list[dict]) -> float:
    """Calendar years the trade list actually covers.

    entry_ts / exit_ts, NOT entry_date. Only some producers set the _date pair:
    kitelab.timeframes (the qmw and pair variants) sets the _ts pair alone, so
    reading _date here raised KeyError on 10 of the 24 variants -- after the
    other 14 had already printed, which is the shape of bug this project keeps
    finding by running things rather than by importing them. The _ts fields are
    the ones every producer sets, which is why dashboard_data.fill_grid sorts on
    them too.
    """
    first = min(t["entry_ts"] for t in trades)
    last = max(t["exit_ts"] for t in trades)
    return max((last - first).days / 365.25, 1e-9)


def paths(r: np.ndarray, fraction: float, years: float) -> tuple:
    """(CAGR %, max drawdown %, MAR) for each row of an (n, trades) R matrix.

    Compounding is multiplicative on equity, which is what fixed-fractional
    risk means: a rupee risked is a percentage of the account at the time, not a
    constant. The floor on the per-trade factor stops a pathological draw from
    taking equity negative -- at 1% risk it never binds (it would need R = -100)
    and it is here so the arithmetic cannot produce a nonsense curve if someone
    passes --risk 20.
    """
    step = np.maximum(1.0 + fraction * r, 1e-6)
    curve = np.cumprod(step, axis=1)
    cagr = (curve[:, -1] ** (1.0 / years) - 1.0) * 100.0
    peak = np.maximum.accumulate(curve, axis=1)
    dd = (curve / peak - 1.0).min(axis=1) * 100.0
    # np.where would evaluate BOTH branches and so divide by zero before
    # selecting -- right answer, a RuntimeWarning per call, and 24 variants x
    # 5,000 draws of noise over the report. `where=` skips the division instead.
    mar = np.divide(cagr, np.abs(dd), out=np.full_like(cagr, np.nan), where=dd < 0)
    return cagr, dd, mar


def _indices(rng, n_rows: int, n_trades: int, block: int) -> np.ndarray:
    """Row indices for one chunk of resamples.

    block <= 1 is the naive i.i.d. draw. Otherwise this is a MOVING BLOCK
    bootstrap: pick random start points and take `block` consecutive trades from
    each, wrapping at the end so every trade is equally likely to appear (a
    non-wrapping version under-samples the tail, which on a trade list ordered
    by time means under-sampling the most recent regime).
    """
    if block <= 1:
        return rng.integers(0, n_trades, size=(n_rows, n_trades))
    n_blocks = -(-n_trades // block)                  # ceiling division
    starts = rng.integers(0, n_trades, size=(n_rows, n_blocks, 1))
    offsets = np.arange(block).reshape(1, 1, block)
    idx = (starts + offsets) % n_trades
    return idx.reshape(n_rows, -1)[:, :n_trades]


def bootstrap(r: np.ndarray, years: float, fraction: float, draws: int, rng,
              block: int = BLOCK):
    """Resample in blocks, in chunks, keeping only the statistics."""
    cagrs, dds, mars = [], [], []
    done = 0
    while done < draws:
        n = min(CHUNK, draws - done)
        idx = _indices(rng, n, len(r), block)
        c, d, m = paths(r[idx], fraction, years)
        cagrs.append(c); dds.append(d); mars.append(m)
        done += n
    return (np.concatenate(cagrs), np.concatenate(dds), np.concatenate(mars))


def expected_best_of(n_trials: int, spread: float) -> float:
    """What the BEST of `n_trials` edgeless rules scores by luck alone.

    The order statistic behind the deflated Sharpe ratio (Bailey & Lopez de
    Prado): the expected maximum of n independent draws from a zero-mean normal
    of the given spread. Run twenty-four coin-flippers and the luckiest looks
    skilled; this says how skilled, so an observed winner can be measured
    against it instead of against zero.

    Uses statistics.NormalDist rather than scipy -- stdlib, and the whole
    formula is two quantiles.
    """
    if n_trials < 2 or spread <= 0:
        return 0.0
    nd = NormalDist()
    gamma = 0.5772156649015329                       # Euler-Mascheroni
    a = nd.inv_cdf(1 - 1.0 / n_trials)
    b = nd.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return spread * ((1 - gamma) * a + gamma * b)


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
    fraction = args.risk / 100.0
    rng = np.random.default_rng(args.seed)

    print(f"\n  universe   {len(cfg.merged)} stocks (merged; no in-sample/holdout split)")
    print(f"  test       {args.draws:,} resamples at {args.risk:g}% fixed-fractional risk")
    print(f"  blocks     {args.block} trades per block"
          if args.block > 1 else "  blocks     i.i.d. (trade clustering ignored)")
    print("  signal lists (cached where possible):", flush=True)
    lists = signal_lists(cfg.merged)

    rows = []
    for (skey, band), trades in sorted(lists.items(), key=lambda kv: str(kv[0])):
        if args.strategy and skey not in args.strategy:
            continue
        held = positions(trades)
        r = r_multiples(held)
        if len(r) < 30:
            print(f"    skipped {skey}/{tag(band)}: {len(r)} trades is too few")
            continue
        years = span_years(held)
        obs_cagr, obs_dd, obs_mar = (v[0] for v in paths(r[None, :], fraction, years))
        cagr, dd, mar = bootstrap(r, years, fraction, args.draws, rng, args.block)
        rows.append({
            "key": f"{STRATEGY_LABELS.get(skey, skey)} · {tag(band)}",
            "n": len(r), "exp": r.mean(),
            "obs_cagr": obs_cagr, "obs_mar": obs_mar,
            "p05": np.percentile(cagr, 5), "p50": np.percentile(cagr, 50),
            "p95": np.percentile(cagr, 95),
            "mar05": np.nanpercentile(mar, 5), "mar50": np.nanpercentile(mar, 50),
            "dd05": np.percentile(dd, 5),
            "p_neg": float((cagr <= 0).mean()),
        })
        print(f"    {rows[-1]['key']:<34} {len(r):>6} trades", flush=True)

    if not rows:
        print("\n  nothing to report.\n")
        return

    rows.sort(key=lambda x: -x["obs_mar"] if np.isfinite(x["obs_mar"]) else 1e9)
    print("\n  Each rule against its own trades. CAGR percentiles are the spread of")
    print("  histories the SAME trades produce in a different order or mix.\n")
    head = (f"  {'rule':<34}{'n':>6}{'expR':>7}{'CAGR':>8}"
            f"{'5th':>8}{'50th':>8}{'95th':>8}{'MAR':>7}{'worst DD':>10}{'P(<=0)':>8}")
    print(head)
    print("  " + "-" * (len(head) - 2))
    for x in rows:
        mar = f"{x['obs_mar']:.2f}" if np.isfinite(x["obs_mar"]) else "  -"
        print(f"  {x['key']:<34}{x['n']:>6}{x['exp']:>7.2f}{x['obs_cagr']:>8.1f}"
              f"{x['p05']:>8.1f}{x['p50']:>8.1f}{x['p95']:>8.1f}{mar:>7}"
              f"{x['dd05']:>10.1f}{x['p_neg']:>8.1%}")

    # ---- the multiple-testing summary: the reason this script exists --------
    #
    # A 5th percentile above zero is a 95% one-sided bar. Testing N rules at that
    # bar and reporting the winner is not a 95% claim about the winner: chance
    # alone clears about N x 0.05 of them. So the count is only interesting
    # against what an edgeless menu of the same size would have produced.
    tried = len(rows)
    cleared = [x for x in rows if x["p05"] > 0]
    expected = tried * 0.05
    print(f"\n  {tried} variants tested. {len(cleared)} cleared a 95% bar "
          f"(5th percentile CAGR above zero).")
    print(f"  An edgeless menu of {tried} clears about {expected:.1f} by chance alone.")

    # THE DEFLATED COMPARISON. Counting clearances says how many rules look good;
    # it does not say how good the BEST of them should look if none had an edge.
    # The expected maximum of `tried` draws does, and it is the number a winner
    # has to beat -- beating zero is not the test when zero was never the bar.
    spread = float(np.median([x["p95"] - x["p05"] for x in rows])) / 3.29
    hurdle = expected_best_of(tried, spread)
    best = max(x["obs_cagr"] for x in rows)
    winner = max(rows, key=lambda x: x["obs_cagr"])
    print(f"\n  The luckiest of {tried} edgeless rules would score about "
          f"{hurdle:.1f}% CAGR.")
    print(f"  The best rule here scored {best:.1f}% ({winner['key']}).")
    if best <= hurdle:
        print("  IT DOES NOT CLEAR THE LUCK HURDLE. Nothing on this board is")
        print("  distinguishable from the best of a menu of coin flips.")
    else:
        print(f"  It clears the hurdle by {best - hurdle:.1f} points. That is a")
        print("  reason to keep testing it, not a reason to trade it: the hurdle")
        print("  assumes independent rules, and these 24 overlap heavily.")
    if len(cleared) <= expected:
        print("  THAT IS NOT EVIDENCE OF AN EDGE. This is what noise looks like when")
        print("  it is ranked: something always comes first.")
    else:
        print(f"  {len(cleared)} against {expected:.1f} expected. The rules that cleared:")
        for x in cleared:
            print(f"      {x['key']:<34} 5th pct CAGR {x['p05']:>6.1f}%")
        print("  Survivors are candidates, not conclusions: they were still chosen")
        print("  after seeing the table, and no further test here is independent of it.")
    print()


if __name__ == "__main__":
    main()

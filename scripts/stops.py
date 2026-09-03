"""Was the stop too tight, or was the entry wrong?

    python -m scripts.stops                 # every strategy
    python -m scripts.stops --strategy hg   # just the Holy Grail

Outcomes alone cannot answer that. A rule that loses money because its stop sits
inside the noise looks identical, in a CAGR column, to a rule whose entries are
simply bad -- and this project has argued about stops more than anything else:
the Holy Grail's two readings of "SL will be swing low", the Turtle stopping at
the channel low rather than the entry candle's, and the wide-vs-tight signal
priority. All of it on outcomes.

MAE and MFE separate the two. For each trade, how far did price go AGAINST the
entry before the trade resolved, in multiples of the risk taken? Then:

  * WINNERS' worst MAE is the tightest stop that would not have killed a single
    winner. Tighten past it and the rule starts converting its own winners into
    losers.
  * The GAP between winners' and losers' median MAE says whether a stop can
    tell them apart at all. If losers go against you no further than winners
    do, no stop distance separates them and tightening only costs money.

Reads the same cached trades the dashboard uses, so it describes the rules as
published rather than a re-simulation that might differ.
"""
from __future__ import annotations

import argparse

from kitelab import config, excursion, frames, registry, signals


def rows_for(trades: list[dict]) -> list[dict]:
    out = []
    by_symbol: dict[str, list[dict]] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    for symbol, group in by_symbol.items():
        try:
            daily = frames.daily(symbol)
        except SystemExit:
            continue
        for t in group:
            got = excursion.excursions(t, daily)
            if got is None:
                continue
            got["won"] = t["net_profit"] > 0
            out.append(got)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", nargs="*", default=None)
    args = ap.parse_args()

    cfg = config.load()
    universe = cfg.merged
    print(f"\n  {len(universe)} stocks. Excursions are in R -- multiples of each "
          f"trade's own risk.\n")
    head = (f"  {'rule':<34}{'trades':>7}{'win MAE':>9}{'lose MAE':>9}"
            f"{'win MFE':>9}{'stop 90% survive':>18}")
    print(head); print("  " + "-" * (len(head) - 2))

    for strat in registry.REGISTRY:
        if args.strategy and strat.key not in args.strategy:
            continue
        trades = signals.load(f"{strat.cache}_all", universe)
        if not trades:
            print(f"  {strat.label:<34}  no cache -- run scripts.refresh first")
            continue
        rows = rows_for(trades)
        if not rows:
            continue
        s = excursion.summarise(rows)
        wm = s["winners_mae"]; lm = s["losers_mae"]; wf = s["winners_mfe"]
        safe = s["stop_holding_90pct_r"]
        print(f"  {strat.label:<34}{s['trades']:>7}"
              f"{(wm['median'] if wm else 0):>9.2f}{(lm['median'] if lm else 0):>9.2f}"
              f"{(wf['median'] if wf else 0):>9.2f}"
              f"{(f'{safe:.2f}R' if safe else '—'):>18}")

    print("\n  How to read it:")
    print("  * 'stop 90% survive' is the dip 90% of WINNERS stayed above. A")
    print("    percentile, not the maximum: R is the entry bar's own range, so a")
    print("    trade entered on a quiet day has a tiny R and one wobble becomes")
    print("    a 50R outlier that would otherwise set the answer for 95,000 trades.")
    print("  * When 'lose MAE' is no larger than 'win MAE', no stop distance can")
    print("    separate the two, and the entry is the problem rather than the stop.")
    print("  * Above 1.00R means winners traded BELOW their own stop and lived.")
    print("    That is possible only because the class convention checks stops at")
    print("    CLOSES (backtest.simulate, stop_on_close=True). With a resting stop")
    print("    order those trades would have been closed for a loss instead.\n")


if __name__ == "__main__":
    main()

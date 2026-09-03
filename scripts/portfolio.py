"""Simulate a real account taking these signals.

    python -m scripts.portfolio
    python -m scripts.portfolio --capital 10000 --risk 2
"""
from __future__ import annotations

import argparse

from kitelab import backtest, config, portfolio, signals, strategies


def signals_for(name: str, build, members: list[str], refresh: bool = False) -> list[dict]:
    """Signal generation is the slow part and does not depend on account size, so it
    is computed once and cached.

    The cache key carries the universe size, and the stamp kitelab.signals writes
    rejects it once the universe, the price files or the strategy code has moved --
    which a bare `path.exists()` never noticed.
    """
    key = f"{name}_{len(members)}"
    if not refresh:
        hit = signals.load(key, members)
        if hit is not None:
            return hit
    out = []
    for index, symbol in enumerate(members, 1):
        try:
            out.extend(build(symbol))
        except SystemExit:
            pass
        if index % 25 == 0:
            print(f"    {name}: {index}/{len(members)} stocks scanned", flush=True)
    signals.save(key, out, members)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=float, nargs="+", default=[10_000, 100_000])
    parser.add_argument("--risk", type=float, default=1.0, help="percent of equity per trade")
    parser.add_argument("--refresh", action="store_true", help="rebuild the signal cache")
    args = parser.parse_args()

    cfg = config.load()
    # cfg.in_sample / cfg.out_of_sample are for scripts.universe_bias only -- see
    # CLAUDE.md. This script quotes the merged universe like everything else.
    members = cfg.merged
    builders = {"Breakout": lambda s: strategies.ath_breakout_trades(s, trailing_stops=True),
                "EMA": backtest.simulate}

    print(f"\n  universe: {len(members)} stocks   risk: {args.risk}% of equity per trade\n")
    for name, build in builders.items():
        trades = signals_for(name, build, members, args.refresh)
        print(f"  {name}  ({len(trades):,} signals available)")
        print(f"    {'capital':>10} {'final':>12} {'return':>10} {'CAGR':>8} {'maxDD':>10} "
              f"{'taken':>7} {'skipped':>8} {'concur':>7}")
        print("    " + "-" * 78)
        for capital in args.capital:
            r = portfolio.run(trades, capital, args.risk / 100)
            skipped = r["skipped_cash"] + r["skipped_size"] + r["skipped_busy"]
            print(f"    {capital:>10,.0f} {r['final']:>12,.0f} {r['return_pct']:>9,.0f}% "
                  f"{r['cagr_pct']:>7.1f}% {r['max_drawdown_pct']:>9.1f}% "
                  f"{len(r['taken']):>7,} {skipped:>8,} {r['max_concurrent']:>7}")
            dd_when = (f"{r['dd_peak_date'].date()} -> {r['dd_trough_date'].date()}"
                       if r["dd_trough_date"] is not None else "n/a")
            print(f"    {'':>10} maxDD is daily mark-to-market ({dd_when}); "
                  f"old at-cost method said {r['legacy_max_drawdown_pct']:.1f}%")
            print(f"    {'':>10} skipped: {r['skipped_size']:,} too small to size, "
                  f"{r['skipped_cash']:,} no cash, {r['skipped_busy']:,} already holding it"
                  f"   over {r['years']:.1f} years")
        print()


if __name__ == "__main__":
    main()

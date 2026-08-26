"""Simulate a real account taking these signals.

    python -m scripts.portfolio
    python -m scripts.portfolio --capital 10000 --universe out
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

from kitelab import backtest, config, portfolio, strategies

CACHE = Path(__file__).resolve().parent.parent / "data" / "signal_cache"


def signals_for(name: str, build, members: list[str], refresh: bool = False) -> list[dict]:
    """Signal generation is the slow part and does not depend on account size, so it is
    computed once and cached. Delete data/signal_cache to force a rebuild."""
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{name}_{len(members)}.pkl"
    if path.exists() and not refresh:
        return pickle.loads(path.read_bytes())
    out = []
    for index, symbol in enumerate(members, 1):
        try:
            out.extend(build(symbol))
        except SystemExit:
            pass
        if index % 25 == 0:
            print(f"    {name}: {index}/{len(members)} stocks scanned", flush=True)
    path.write_bytes(pickle.dumps(out))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=float, nargs="+", default=[10_000, 100_000])
    parser.add_argument("--risk", type=float, default=1.0, help="percent of equity per trade")
    parser.add_argument("--universe", default="all", choices=["in", "out", "all"])
    parser.add_argument("--refresh", action="store_true", help="rebuild the signal cache")
    args = parser.parse_args()

    cfg = config.load()
    members = {"in": cfg.in_sample, "out": cfg.out_of_sample, "all": cfg.all_symbols}[args.universe]
    builders = {"Breakout": lambda s: strategies.ath_breakout_trades(s, trailing_stops=True),
                "EMA": backtest.simulate}

    print(f"\n  universe: {len(members)} stocks   risk: {args.risk}% of equity per trade\n")
    for name, build in builders.items():
        signals = signals_for(name, build, members, args.refresh)
        print(f"  {name}  ({len(signals):,} signals available)")
        print(f"    {'capital':>10} {'final':>12} {'return':>10} {'CAGR':>8} {'maxDD':>10} "
              f"{'taken':>7} {'skipped':>8} {'concur':>7}")
        print("    " + "-" * 78)
        for capital in args.capital:
            r = portfolio.run(signals, capital, args.risk / 100)
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

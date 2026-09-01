"""Rebuild the signal caches that scripts.dashboard_data no longer builds.

    python -m scripts.rebuild_orphans
    python -m scripts.rebuild_orphans --check   # list them, build nothing

W/D/H and ATH Breakout left the dashboard on 2026-09-01, so dashboard_data stops
producing their trade lists. Four scripts still read them:

    drawdown_report, scaleout_test   Breakout_all, Breakout_half_all,
                                     Breakout_halfbe_all
    scripts/portfolio                EMA_101, Breakout_101 (its cache key is
                                     name + universe size)

Left alone, those caches would sit on disk unstamped forever -- readable, but
unable to say which universe, price files or code produced them. That is exactly
how "Darvas_20_10_all" went on serving pre-gate trades under a current label.
This rebuilds them through kitelab.signals so they carry the same stamp
everything else does.

If those four scripts are ever retired, delete this file with them; nothing else
needs it.
"""
from __future__ import annotations

import argparse

from kitelab import backtest, config, signals, strategies


def builders(symbols: list[str]) -> dict:
    """cache name -> the function that produces one symbol's trades.

    The names and the builders both have to match what the reading scripts
    expect; they are copied from the dashboard_data code that used to own them.
    """
    n = len(symbols)
    return {
        "Breakout_all": lambda s: strategies.ath_breakout_trades(s, trailing_stops=True),
        "Breakout_half_all": lambda s: strategies.ath_breakout_trades(
            s, trailing_stops=True, scale_out="half"),
        "Breakout_halfbe_all": lambda s: strategies.ath_breakout_trades(
            s, trailing_stops=True, scale_out="half_be"),
        # scripts/portfolio keys its caches by universe SIZE, not by name alone
        f"Breakout_{n}": lambda s: strategies.ath_breakout_trades(s, trailing_stops=True),
        f"EMA_{n}": backtest.simulate,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report which are missing or unstamped, build nothing")
    args = ap.parse_args()

    cfg = config.load()
    symbols = cfg.all_symbols
    todo = builders(symbols)

    if args.check:
        print(f"\n  universe: {len(symbols)} symbols\n")
        for name in todo:
            hit = signals.load(name, symbols, allow_legacy=False)
            print(f"    {name:<24} {'stamped and current' if hit else 'MISSING or STALE'}")
        print()
        return

    from kitelab.progress import Bar
    print(f"\n  rebuilding {len(todo)} caches over {len(symbols)} symbols\n")
    for name, build in todo.items():
        if signals.load(name, symbols, allow_legacy=False) is not None:
            print(f"    {name:<24} already stamped and current, skipped")
            continue
        out = []
        bar = Bar(len(symbols), name[:14])
        for symbol in symbols:
            try:
                out.extend(build(symbol))
            except SystemExit:
                pass                       # a symbol with no data for this rule
            bar.step()
        bar.close()
        signals.save(name, out, symbols)
        print(f"    {name:<24} {len(out):,} trades, stamped")
    print()


if __name__ == "__main__":
    main()

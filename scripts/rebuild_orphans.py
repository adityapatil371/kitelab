"""Rebuild the signal caches that scripts.dashboard_data no longer builds.

    python -m scripts.rebuild_orphans
    python -m scripts.rebuild_orphans --check   # list them, build nothing

scripts/portfolio keys its caches by name PLUS universe size -- "EMA_101", not
"EMA_all" -- so dashboard_data never builds them even though the trades are
identical. That is the whole reason this file exists.

Left alone those two would sit on disk unstamped forever: readable, but unable
to say which universe, price files or code produced them. That is exactly how
"Darvas_20_10_all" went on serving pre-gate trades under a current label. This
rebuilds them through kitelab.signals so they carry the same stamp as
everything else.

It used to cover the ATH Breakout lists too, for drawdown_report and
scaleout_test. Those scripts were retired on 2026-09-01 when the dashboard
replaced the workbooks, and the Breakout caches went with them.

If scripts/portfolio is ever retired, or taught to use the dashboard's cache
names, delete this file with it; nothing else needs it.
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
    # The names scripts/portfolio composes: strategy + universe size.
    return {
        f"EMA_{n}": backtest.simulate,
        f"Breakout_{n}": lambda s: strategies.ath_breakout_trades(s, trailing_stops=True),
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

"""Run a screener query, live or as of any past date.

    python -m scripts.scan --list
    python -m scripts.scan --preset ema-stack
    python -m scripts.scan --preset ema-stack --asof 2025-09-10
    python -m scripts.scan --preset ema-stack --history 250
    python -m scripts.scan "daily.rsi(14) < 30 and daily.close > daily.sma(200)"
"""
from __future__ import annotations

import argparse

import pandas as pd

from kitelab import config, screener

PRESETS = {
    # Your EMA strategy: long while price sits above the EMA on all three timeframes.
    "ema-stack": (
        "monthly.close > monthly.ema(20) and weekly.close > weekly.ema(20) "
        "and daily.close > daily.ema(20)"
    ),
    "ema-stack-exit": (
        "not (monthly.close > monthly.ema(20) and weekly.close > weekly.ema(20) "
        "and daily.close > daily.ema(20))"
    ),
    "golden-cross": (
        "daily.sma(50) > daily.sma(200) and daily.sma(50, ago=1) <= daily.sma(200, ago=1)"
    ),
    "death-cross": (
        "daily.sma(50) < daily.sma(200) and daily.sma(50, ago=1) >= daily.sma(200, ago=1)"
    ),
    "rsi-oversold": "daily.rsi(14) < 30",
    "rsi-overbought": "daily.rsi(14) > 70",
    "above-200dma": "daily.close > daily.sma(200)",
    "breakout-20d": "daily.close > daily.highest(20, ago=1)",
    "breakdown-20d": "daily.close < daily.lowest(20, ago=1)",
    "volume-surge": "daily.volume > 2 * daily.sma_volume(20)",
    "macd-cross-up": (
        "daily.macd() > daily.macd_signal() and daily.macd(ago=1) <= daily.macd_signal(ago=1)"
    ),
    "bollinger-break": "daily.close > daily.bb_upper(20)",
    "inside-bar": "daily.high < daily.ago('high', 1) and daily.low > daily.ago('low', 1)",
    # Rough proxy for the breakout setup in your spreadsheet: a 20-day high on
    # expanded volume. It finds candidates; it does not judge them.
    "breakout-on-volume": (
        "daily.close > daily.highest(20, ago=1) and daily.volume > 1.5 * daily.sma_volume(20)"
    ),
}


def _print(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("  no matches\n")
        return
    frame = pd.DataFrame(rows)[columns]
    print(frame.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="a boolean expression, or use --preset")
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.add_argument("--asof", help="evaluate as of this date/time, e.g. 2025-09-10")
    parser.add_argument("--history", type=int, metavar="N",
                        help="replay the query over the last N sessions instead")
    parser.add_argument("--list", action="store_true", help="show the presets and exit")
    args = parser.parse_args()

    if args.list:
        print()
        for name in sorted(PRESETS):
            print(f"  {name:<20} {PRESETS[name]}")
        print()
        return

    expression = PRESETS[args.preset] if args.preset else args.query
    if not expression:
        parser.error("give a query expression, or --preset NAME, or --list")

    cfg = config.load()
    print(f"\nquery: {expression}\n")

    if args.history:
        rows = screener.scan_history(cfg.symbols, expression, args.history)
        print(f"fired on {len(rows)} symbol-days in the last {args.history} sessions:\n")
        _print(rows, ["symbol", "date", "close", "change_pct"])
    else:
        rows = screener.scan(cfg.symbols, expression, args.asof)
        label = args.asof or "latest bar"
        print(f"matches as of {label}:\n")
        _print(rows, ["symbol", "as_of", "close", "change_pct"])


if __name__ == "__main__":
    main()

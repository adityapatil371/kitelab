"""Inspect and sanity-check what is on disk.

    python -m scripts.show                  # coverage + health check for everything
    python -m scripts.show HAL 1w -n 12     # last 12 weekly candles for HAL

The second form is what you use to verify the resampled timeframes. 30m, 1h, 1w and
1M are computed locally, not served by Zerodha, so check a handful of bars against
TradingView before trusting them in a backtest.
"""
from __future__ import annotations

import argparse

import pandas as pd

from kitelab import config, frames

# A daily move larger than this is far more likely to be an unadjusted split or
# bonus than a real price move, and it will wreck any P&L computed across it.
SPLIT_SUSPECT_PCT = 20.0


def _summary(cfg) -> None:
    print(f"\n{'symbol':<10} {'tf':<5} {'bars':>8}  {'first':<19} {'last':<19}")
    print("-" * 66)
    for symbol in cfg.symbols:
        for timeframe in frames.TIMEFRAMES:
            try:
                frame = frames.load(symbol, timeframe, cfg.use_daily_source)
            except SystemExit as exc:
                print(f"{symbol:<10} {timeframe:<5} {exc}")
                break
            if frame.empty:
                print(f"{symbol:<10} {timeframe:<5} {'0':>8}  (no data)")
                continue
            print(
                f"{symbol:<10} {timeframe:<5} {len(frame):>8,}  "
                f"{frame['ts'].min()!s:<19} {frame['ts'].max()!s:<19}"
            )
        print()
    _health(cfg)


def _health(cfg) -> None:
    print("health check")
    print("-" * 66)
    for symbol in cfg.symbols:
        try:
            base = frames.base_15m(symbol)
            day = frames.load(symbol, "1d", cfg.use_daily_source)
        except SystemExit:
            continue

        # A full session holds 25 fifteen-minute bars before NSE's closing auction
        # session started on 2026-08-03, and 24 after it.
        per_day = base.groupby(base["ts"].dt.normalize()).size()
        expected = per_day.index.map(frames.expected_bars)
        odd = per_day[per_day.values != expected.values]
        if odd.empty:
            print(f"{symbol:<10} 15m sessions: all {len(per_day):,} days have the expected bar count")
        else:
            print(
                f"{symbol:<10} 15m sessions: {len(odd)} of {len(per_day):,} days differ "
                f"from the expected count (muhurat/halts are normal; a long run is not)"
            )
            print(f"{'':<10}   e.g. {odd.head(3).to_dict()}")

        # Unadjusted corporate actions show up as impossible overnight gaps.
        if len(day) > 1:
            change = day["close"].pct_change() * 100
            spikes = day.loc[change.abs() > SPLIT_SUSPECT_PCT]
            if spikes.empty:
                print(f"{'':<10} adjustment:   no daily move over {SPLIT_SUSPECT_PCT:.0f}% -- looks adjusted")
            else:
                print(
                    f"{'':<10} adjustment:   {len(spikes)} daily move(s) over "
                    f"{SPLIT_SUSPECT_PCT:.0f}% -- check these against a corporate-action list"
                )
                for idx, row in spikes.head(5).iterrows():
                    print(
                        f"{'':<10}   {row['ts'].date()}  "
                        f"{change.loc[idx]:+.1f}%  close {row['close']:.2f}"
                    )
        print()


def _candles(cfg, symbol: str, timeframe: str, count: int) -> None:
    frame = frames.load(symbol, timeframe, cfg.use_daily_source)
    if frame.empty:
        print(f"No {timeframe} data for {symbol}.")
        return
    source = "Kite native" if timeframe == "15m" or (
        timeframe == "1d" and cfg.use_daily_source
    ) else "resampled locally"
    print(f"\n{symbol}  {timeframe}  ({source})  last {min(count, len(frame))} of {len(frame):,} bars\n")
    with pd.option_context("display.max_rows", None, "display.width", 120):
        print(frame.tail(count).to_string(index=False))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", help="e.g. HAL")
    parser.add_argument("timeframe", nargs="?", default="1d", choices=frames.TIMEFRAMES)
    parser.add_argument("-n", "--count", type=int, default=20, help="bars to print")
    args = parser.parse_args()

    cfg = config.load()
    if args.symbol is None:
        _summary(cfg)
    else:
        _candles(cfg, args.symbol.upper(), args.timeframe, args.count)


if __name__ == "__main__":
    main()

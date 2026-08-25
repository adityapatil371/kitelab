"""Prove that a resampled timeframe correctly aggregates its source timeframe.

    python -m scripts.verify                    # every symbol, every derived timeframe
    python -m scripts.verify HINDZINC 1w        # one symbol, one timeframe, with detail

Each derived bar is checked against the source bars it should be built from:

    open   == first source open
    high   == max source high
    low    == min source low
    close  == last source close
    volume == sum source volume

This is written as a second, independent implementation. It slices the source by bar
boundaries instead of by ISO week / calendar month, so a grouping bug in frames.py --
a shifted week, a mishandled ISO year boundary -- shows up as a disagreement rather
than being reproduced identically by the same logic checking itself.

What this does NOT prove: that Kite's underlying data is right. It proves the
aggregation on top of it is right.
"""
from __future__ import annotations

import argparse

import pandas as pd

from kitelab import config, frames

# derived timeframe -> the timeframe it must aggregate from
SOURCE_OF = {"30m": "15m", "1h": "15m", "1w": "1d", "1M": "1d"}

TOLERANCE = 1e-6


def _constituents(source: pd.DataFrame, start, end) -> pd.DataFrame:
    """Source bars belonging to one derived bar: [start, end), or [start, inf) if open."""
    mask = source["ts"] >= start
    if end is not None:
        mask &= source["ts"] < end
    return source.loc[mask]


def _check_bar(bar, parts: pd.DataFrame, timeframe: str) -> list[str]:
    if parts.empty:
        return ["no source bars fall inside this bar's window"]
    problems = []
    checks = [
        ("open", bar["open"], parts.iloc[0]["open"]),
        ("high", bar["high"], parts["high"].max()),
        ("low", bar["low"], parts["low"].min()),
        ("close", bar["close"], parts.iloc[-1]["close"]),
        ("volume", bar["volume"], parts["volume"].sum()),
    ]
    for field, got, want in checks:
        if abs(float(got) - float(want)) > TOLERANCE:
            problems.append(f"{field}: stored {got} but source gives {want}")
    first_source = parts.iloc[0]["ts"]
    if timeframe in ("1w", "1M"):
        # Weekly and monthly bars are labelled with their first trading session, so the
        # label must match exactly.
        if first_source != bar["ts"]:
            problems.append(
                f"label: bar is dated {bar['ts']} but its first source bar is {first_source}"
            )
    elif first_source < bar["ts"]:
        # Intraday bars are labelled by their position on the 30m/1h grid anchored to
        # 09:15, which can legitimately precede the first trade of an unusual session --
        # the old evening muhurat sessions did not always start on a grid boundary.
        # A source bar *before* the label is still a real error.
        problems.append(
            f"label: bar starts {bar['ts']} but a source bar exists at {first_source}"
        )
    return problems


def _span_sanity(timeframe: str, bar, parts: pd.DataFrame) -> list[str]:
    """Catch grouping errors that per-bar arithmetic alone would not."""
    problems = []
    first, last = parts.iloc[0]["ts"], parts.iloc[-1]["ts"]
    if timeframe == "1w":
        if (last - first).days > 6:
            problems.append(f"week spans {(last - first).days + 1} calendar days")
        if first.weekday() > last.weekday():
            problems.append("week wraps across a weekend boundary")
    elif timeframe == "1M":
        if (first.year, first.month) != (last.year, last.month):
            problems.append(f"month spans {first:%Y-%m} to {last:%Y-%m}")
    return problems


def verify(symbol: str, timeframe: str, detail: bool = False) -> tuple[int, int]:
    source_tf = SOURCE_OF[timeframe]
    derived = frames.load(symbol, timeframe)
    source = frames.load(symbol, source_tf)
    if derived.empty or source.empty:
        print(f"  {symbol:<10} {timeframe:<3} no data")
        return 0, 0

    failures = 0
    first_failure = None
    for position in range(len(derived)):
        bar = derived.iloc[position]
        end = derived.iloc[position + 1]["ts"] if position + 1 < len(derived) else None
        parts = _constituents(source, bar["ts"], end)
        problems = _check_bar(bar, parts, timeframe) + (
            _span_sanity(timeframe, bar, parts) if not parts.empty else []
        )
        if problems:
            failures += 1
            if first_failure is None:
                first_failure = (bar, parts, problems)

    total = len(derived)
    status = "OK  " if failures == 0 else "FAIL"
    print(f"  {status} {symbol:<10} {timeframe:<3} {total - failures:,}/{total:,} bars "
          f"reconstruct exactly from {source_tf}")

    if first_failure is not None:
        bar, parts, problems = first_failure
        print(f"       first mismatch at {bar['ts']}:")
        for problem in problems:
            print(f"         - {problem}")
        print(parts.to_string(index=False))
    elif detail:
        _show_worked_example(derived, source, timeframe)

    return total - failures, total


def _show_worked_example(derived, source, timeframe: str) -> None:
    """Print the most recent bar next to the source bars it was built from."""
    bar = derived.iloc[-1]
    parts = _constituents(source, bar["ts"], None)
    print(f"\n       worked example -- the {timeframe} bar dated {bar['ts']}:\n")
    print("       source bars:")
    for _, row in parts.iterrows():
        print(f"         {row['ts']}  O {row['open']:>9.2f}  H {row['high']:>9.2f}  "
              f"L {row['low']:>9.2f}  C {row['close']:>9.2f}  V {int(row['volume']):>12,}")
    print(f"\n       first open  {parts.iloc[0]['open']:>9.2f}   ->  stored open  {bar['open']:>9.2f}")
    print(f"       max high    {parts['high'].max():>9.2f}   ->  stored high  {bar['high']:>9.2f}")
    print(f"       min low     {parts['low'].min():>9.2f}   ->  stored low   {bar['low']:>9.2f}")
    print(f"       last close  {parts.iloc[-1]['close']:>9.2f}   ->  stored close {bar['close']:>9.2f}")
    print(f"       sum volume  {int(parts['volume'].sum()):>9,}   ->  stored vol   {int(bar['volume']):>9,}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?")
    parser.add_argument("timeframe", nargs="?", choices=list(SOURCE_OF))
    args = parser.parse_args()

    cfg = config.load()
    symbols = [args.symbol.upper()] if args.symbol else cfg.symbols
    timeframes = [args.timeframe] if args.timeframe else list(SOURCE_OF)
    detail = bool(args.symbol and args.timeframe)

    print()
    passed = checked = 0
    for symbol in symbols:
        for timeframe in timeframes:
            try:
                good, total = verify(symbol, timeframe, detail)
            except SystemExit as exc:
                print(f"  {symbol:<10} {timeframe:<3} {exc}")
                continue
            passed += good
            checked += total
        if not args.symbol:
            print()

    if checked:
        print(f"{passed:,} of {checked:,} derived bars reconstruct exactly "
              f"({100 * passed / checked:.4f}%)\n")


if __name__ == "__main__":
    main()

"""Dig into the anomalies that `scripts.show` only hints at.

    python -m scripts.diagnose

Answers four questions:
  1. Do sessions actually end at 15:15, and if not, since when?
  2. Does any symbol have intraday bars from before its equity listing?
  3. Are the malformed sessions spread evenly, or clustered in one era?
  4. What does a flagged >20% daily move look like in context?
"""
from __future__ import annotations

import pandas as pd

from kitelab import config, frames

SPLIT_SUSPECT_PCT = 20.0


def _sessions(base: pd.DataFrame) -> pd.DataFrame:
    return base.groupby(base["ts"].dt.normalize()).agg(
        bars=("ts", "size"), first_bar=("ts", "min"), last_bar=("ts", "max")
    )


def _closing_bar_distribution(sessions: pd.DataFrame) -> None:
    """If the 15:15 bar is being dropped, it shows up here immediately."""
    closes = sessions["last_bar"].dt.time.value_counts().sort_values(ascending=False)
    print("  session close times (top 5):")
    for close_time, count in closes.head(5).items():
        share = 100 * count / len(sessions)
        print(f"    {close_time}  {count:>6,} sessions  ({share:5.1f}%)")

    # Post-CAS the session legitimately ends at 15:00, so only judge each date
    # against the close that applied on that date.
    cutoff = sessions.index.map(
        lambda d: pd.Timestamp("15:00").time() if d >= frames.CAS_START
        else pd.Timestamp("15:15").time()
    )
    short = sessions[sessions["last_bar"].dt.time.values < cutoff.values]
    if not short.empty:
        print(f"  {len(short):,} sessions end early for their era. Most recent:")
        for day, row in short.tail(5).iterrows():
            print(f"    {day.date()}  {row['bars']:>2} bars, last {row['last_bar'].time()}")
    else:
        print("  every session closes at the expected time for its era")


def _listing_consistency(symbol: str, base: pd.DataFrame, day: pd.DataFrame) -> None:
    intraday_start = base["ts"].min().date()
    daily_start = day["ts"].min().date()
    print(f"  first intraday bar: {intraday_start}    first daily bar: {daily_start}")
    if intraday_start < daily_start:
        orphan = base[base["ts"].dt.date < daily_start]
        print(
            f"  !! {len(orphan):,} intraday bars predate the first daily bar by "
            f"{(daily_start - intraday_start).days} days."
        )
        print(f"  !! Do not backtest {symbol} intraday before {daily_start}.")


def _odd_sessions_by_year(sessions: pd.DataFrame) -> None:
    expected = sessions.index.map(frames.expected_bars)
    odd = sessions[sessions["bars"].values != expected.values]
    if odd.empty:
        print("  every session has the bar count expected for its era (25 pre-CAS, 24 post)")
        return
    by_year = odd.groupby(odd.index.year).size()
    total_by_year = sessions.groupby(sessions.index.year).size()
    print(f"  {len(odd):,} of {len(sessions):,} sessions have an unexpected bar count, by year:")
    for year, count in by_year.items():
        share = 100 * count / total_by_year[year]
        flag = "  <-- clustered" if share > 25 else ""
        print(f"    {year}  {count:>4} of {total_by_year[year]:>4}  ({share:5.1f}%){flag}")


def _spike_context(day: pd.DataFrame) -> None:
    change = day["close"].pct_change() * 100
    spikes = day.loc[change.abs() > SPLIT_SUSPECT_PCT]
    if spikes.empty:
        print(f"  no daily move over {SPLIT_SUSPECT_PCT:.0f}%")
        return
    for idx in spikes.index:
        window = day.loc[max(idx - 3, day.index[0]) : idx + 3]
        pct = change.loc[idx]
        print(f"  {day.loc[idx, 'ts'].date()}  {pct:+.1f}%  -- surrounding sessions:")
        for _, row in window.iterrows():
            print(
                f"    {row['ts'].date()}  O {row['open']:>9.2f}  H {row['high']:>9.2f}  "
                f"L {row['low']:>9.2f}  C {row['close']:>9.2f}  V {int(row['volume']):>12,}"
            )
        # Distinguish a real move from an unadjusted corporate action. A split or
        # bonus arrives as a gapped OPEN on ordinary volume -- the whole move happens
        # overnight. A genuine move opens near the prior close and travels intraday on
        # expanded volume.
        prev_close = day.loc[idx - 1, "close"] if idx - 1 in day.index else None
        if prev_close:
            gap = 100 * (day.loc[idx, "open"] - prev_close) / prev_close
            baseline = day.loc[max(idx - 21, day.index[0]) : idx - 1, "volume"].median()
            ratio = day.loc[idx, "volume"] / baseline if baseline else float("nan")
            print(f"    overnight gap {gap:+.1f}% of a {pct:+.1f}% move, "
                  f"volume {ratio:.0f}x the 20-session median")
            if abs(gap) > 0.75 * abs(pct) and ratio < 3:
                print("    -> gapped open on ordinary volume: check for a corporate action")
            else:
                print("    -> moved intraday on expanded volume: reads as a genuine move")


def main() -> None:
    cfg = config.load()
    for symbol in cfg.symbols:
        print(f"\n{'=' * 70}\n{symbol}\n{'=' * 70}")
        try:
            base = frames.base_15m(symbol)
            day = frames.load(symbol, "1d", cfg.use_daily_source)
        except SystemExit as exc:
            print(f"  {exc}")
            continue

        sessions = _sessions(base)
        _listing_consistency(symbol, base, day)
        print()
        _closing_bar_distribution(sessions)
        print()
        _odd_sessions_by_year(sessions)
        print()
        _spike_context(day)
    print()


if __name__ == "__main__":
    main()

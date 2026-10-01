"""Dig into the anomalies that `scripts.show` only hints at.

    python -m scripts.diagnose

Answers what flagged >20% daily moves look like in context.
"""
from __future__ import annotations

import pandas as pd

from kitelab import config, frames

SPLIT_SUSPECT_PCT = 20.0


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
            day = frames.daily(symbol)
        except SystemExit as exc:
            print(f"  {exc}")
            continue
        _spike_context(day)
    print()


if __name__ == "__main__":
    main()

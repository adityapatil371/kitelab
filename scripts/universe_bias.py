"""Is the in-sample universe a fair sample, or a list of winners?

    python -m scripts.universe_bias

Asked on 2026-09-03, because the holdout result looked too extreme to be
ordinary selection bias. The Turtle scored MAR 0.95 on the 101 and -0.13 on the
399, over the SAME calendar years and through the same quality gate, and the
usual explanation -- 24 variants tried, the luckiest reported -- did not fit:
the comparison arms nobody chose fell just as far.

Two tests, and they separate the two explanations cleanly.

  1. SAME PROCEDURE, FRESH SAMPLE. Run the identical best-of-24 sweep on random
     101-stock draws from the 399. Selection can only inflate the WINNER, so if
     selection is the whole story the MEDIAN variant should be unchanged. It is
     not: 0.33 on the real 101 against about 0.09 on fresh draws. Every rule does
     better on the 101.

  2. BUY AND HOLD, BUCKET BY BUCKET. No strategy at all, so nothing here can be
     blamed on a rule. Within every liquidity bucket the 101 returned more and
     lost money less often. Of its 37 large caps, one lost money in eight years;
     of the 399's 99, sixteen did.

The conclusion is that the in-sample numbers are inflated before any strategy
runs, and that comparisons BETWEEN rules on the same stocks are still valid
because the deck cancels. See HANDOVER.md section 4.

Reads the signal caches and the cleaned daily files. Writes nothing.
"""
from __future__ import annotations

import pickle
import random
import statistics as st
import sys

import numpy as np
import pandas as pd

from kitelab import config, frames, portfolio, registry, slippage

CACHE = config.CLEAN / "signal_cache"
RISK, CAPITAL, YEAR, DRAWS = 0.01, 200_000, 2018, 25
REALISTIC_PARTICIPATION = 0.02          # matches dashboard_data.py
CUT = pd.Timestamp(f"{YEAR}-01-01")

# The published variants, by cache stem -- DERIVED FROM THE REGISTRY, never
# globbed and no longer hand-kept. Globbing is wrong because the cache directory
# also holds retired lists (Breakout_101, the scale-out sweeps, every band this
# board used to sweep), and a comparison whose width changes silently is not a
# comparison. A hand-kept list was wrong for the older reason: it was written out
# here as 22 stems and went stale the same day the board changed, on 2026-09-05,
# when the EMA band was removed and 22 variants became 13.
#
# STILL TRUE, and the reason to run this only after a full rebuild: it reads
# cache files directly by path, bypassing kitelab.signals' staleness check. The
# stems EMA_WD, EMA_daily_only and EMA_ath10 did not change when the band went to
# zero, so a stale pickle under one of those names scores the OLD 2% rule under
# the new rule's label and nothing here would say so.
NAMES = [s.cache for s in registry.REGISTRY]


def load(name: str, suffix: str) -> list[dict]:
    path = CACHE / f"{name}_{suffix}.pkl"
    if not path.exists():
        sys.exit(f"\nSTOPPED: missing signal cache {path}\n"
                 f"  Build it first:  python -m scripts.refresh\n")
    return pickle.loads(path.read_bytes())["trades"]


def score(trades, members) -> float | None:
    """MAR for one variant on one basket, at the dashboard's reference cell."""
    window = [t for t in trades
              if t["symbol"] in members and pd.Timestamp(t["entry_ts"]) >= CUT]
    if not window:
        return None
    return portfolio.run(window, CAPITAL, RISK).get("mar")


def sweep(sets, suffix, members):
    """(best MAR, which variant won, median MAR across all 24)."""
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = REALISTIC_PARTICIPATION
    slippage.reset()
    got = [(m, n) for n in NAMES
           if (m := score(sets[(n, suffix)], members)) is not None]
    if not got:
        return None
    got.sort(reverse=True)
    return got[0][0], got[0][1], st.median([m for m, _ in got])


def buy_and_hold(symbol: str):
    """(CAGR since YEAR, median daily turnover) or None if too short to judge."""
    daily = frames.daily(symbol)
    turn = (daily["close"] * daily["volume"]).to_numpy(float)
    turn = turn[turn > 0]
    daily = daily[pd.to_datetime(daily["ts"]) >= CUT]
    if len(daily) < 250 or not len(turn):
        return None
    close = daily["close"].to_numpy(float)
    years = len(close) / 252
    if close[0] <= 0 or years < 1:
        return None
    return 100 * ((close[-1] / close[0]) ** (1 / years) - 1), float(np.median(turn))


def main() -> None:
    cfg = config.load()
    in_sample, unseen = sorted(cfg.all_symbols), sorted(cfg.out_of_sample)
    print(f"in-sample {len(in_sample)} stocks   holdout {len(unseen)} stocks   "
          f"{len(NAMES)} variants   from {YEAR}")
    if not in_sample or not unseen:
        sys.exit("\nSTOPPED: one side of the split is empty. Check config.local.toml.\n")

    # ---- test 1: the same best-of-24 procedure on fresh samples -----------
    print("\nloading signal caches", flush=True)
    sets = {}
    for suffix in ("all", "hold"):
        for name in NAMES:
            slippage.ENABLED = True
            slippage.reset()
            # The spread is charged onto the cached trades exactly as
            # dashboard_data does it -- nothing in the simulation depends on the
            # fill price, so this is exact and costs nothing to redo.
            sets[(name, suffix)] = [slippage.apply_spread(t)
                                    for t in load(name, suffix)]

    print("\n=== 1. the same best-of-24 procedure, run on fresh samples ===")
    real_best, winner, real_med = sweep(sets, "all", set(in_sample))
    print(f"  the real {len(in_sample)}:   best {real_best:+.2f} ({winner})"
          f"   median variant {real_med:+.2f}")

    rng = random.Random(20260903)
    bests, meds, winners = [], [], {}
    for _ in range(DRAWS):
        out = sweep(sets, "hold", set(rng.sample(unseen, len(in_sample))))
        if out is None:
            continue
        best, who, med = out
        bests.append(best); meds.append(med)
        winners[who] = winners.get(who, 0) + 1

    def band(values):
        v = sorted(values)
        q = lambda p: v[min(len(v) - 1, int(p * len(v)))]
        return f"min {v[0]:+.2f}  median {q(.5):+.2f}  max {v[-1]:+.2f}"

    print(f"  {len(bests)} fresh draws of {len(in_sample)} from the {len(unseen)}:")
    print(f"    best of {len(NAMES)}      {band(bests)}")
    print(f"    MEDIAN of {len(NAMES)}    {band(meds)}")
    print(f"  {sum(1 for b in bests if b >= real_best)} of {len(bests)} fresh draws "
          f"reached {real_best:+.2f} or better")
    print(f"  {len(winners)} different variants won across {len(bests)} draws")
    print("\n  Selection inflates the WINNER only. The median moved too, so the\n"
          "  populations differ -- selection is not the explanation.")

    # ---- test 2: buy and hold, so no rule can be blamed -------------------
    print("\n=== 2. buy and hold, within each liquidity bucket ===")
    rows: dict[tuple[str, str], list[float]] = {}
    for label, symbols in (("101", in_sample), ("399", unseen)):
        kept = 0
        for symbol in symbols:
            try:
                got = buy_and_hold(symbol)
            except Exception:                                  # noqa: BLE001
                continue
            if got is None:
                continue
            cagr, turn = got
            bucket = ("small" if turn < 5e7 else "mid" if turn < 25e7 else "large")
            rows.setdefault((label, bucket), []).append(cagr)
            kept += 1
        print(f"  {label}: {kept} of {len(symbols)} stocks have a full year since {YEAR}")

    print(f"\n  {'bucket':<8}{'set':<6}{'n':>5}{'median B&H':>12}{'% losing':>10}")
    for bucket in ("large", "mid", "small"):
        for label in ("101", "399"):
            v = rows.get((label, bucket), [])
            if not v:
                continue
            print(f"  {bucket if label == '101' else '':<8}{label:<6}{len(v):>5}"
                  f"{st.median(v):>11.1f}%"
                  f"{100 * sum(1 for x in v if x < 0) / len(v):>9.0f}%")
    print("\n  No strategy runs here. If the 101 win every bucket, the in-sample\n"
          "  numbers were never a strategy result.")


if __name__ == "__main__":
    main()

"""How much of the account is actually holding stock on a typical day?

    python3 -m scripts.deployment

WHY THIS EXISTS. scripts/entry_edge.py reports an "exposure" of 5.9% to 49.9%
per rule, and that number is easy to misread -- I misread it to the user on
2026-09-10. It is a count of STOCK-SESSIONS held over stock-sessions the
universe offers, which is the right denominator against an equal-weight hold
that owns all 1,000 names every day. It says the rule owns a THIN SLICE OF THE
MARKET, not that the account is in cash. Those are different claims and only the
first one was measured. This script measures the second.

    breadth   = names held / names available          (entry_edge.py)
    deployment = rupees at work / rupees in the account   (here)

WHAT THE NUMBER BELOW IS, PRECISELY: NOTIONAL DEMAND, NOT A FILLED ACCOUNT.
The signal caches are generated against the producer's Rs1cr paper book with one
position per symbol but NO cash constraint, deliberately, so that no signal the
largest account could take is ever missing from the cache (see the CLAUDE.md
note on sizing.CAPITAL). So the sum below is what the rule ASKED FOR. Where it
exceeds 100% the real cash-constrained account could not have taken every
signal, and portfolio.run's priority ordering decided which ones it skipped.

THE FIRST DRAFT OF THIS LINE SAID "the axis the project calls noise, worth up
to 20 CAGR points". Both halves were wrong and I checked only after the user
asked how I knew (2026-09-11). (a) Nothing in the repo calls priority noise; the
nearest real statement is portfolio.py's "that something is not part of the
strategy, and on these settings it is deciding half the trades". (b) The 20
points came from wf_daily.py's illiquid-first 47.4%/yr vs liquid-first 28.0% --
a cell that same docstring then INVALIDATES, because those fills put Rs5cr into
names trading Rs0.4cr/day with slippage off; --capped drops 47.4 to 19.6, below
liquid-first. Measured instead on the live board (dashboard.json 2026-09-10,
1,900 cells carrying all five priorities), the best-minus-worst CAGR spread
across priority is median 6.7 pts, p75 12.1, max 61.7 -- against median 24.35
across the 19 STRATEGIES and 14.75 across start year. Priority is the SMALLEST
of the three axes, not the largest. What does survive: the default `liquidity`
wins only 14.3% of its own cells (tight 26.8, time 26.5, illiquid 22.4), so the
ordering shipped as default is the second-worst of the five on the board.
Reading a demand figure over 100% as "the account was 300% invested" would be
wrong. Reading it as "the rule wanted 3x the money that existed" is right.

Position size is not a free choice here either: risk is fixed at 1% of capital
and the stop is the entry candle's own low, so position value = risk / stop
distance. A daily candle's low sits ~2% under the entry and a quarterly one
~10.5%, which is why the fast rules demand ~5x the rupees per trade.

Reads:  the 19 *_all.pkl signal caches.
Writes: output/measurements/deployment_2026-09-10.csv
        output/deployment_curve.png
"""
from __future__ import annotations

import csv
import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")            # no display in the container; PNG only
import matplotlib.pyplot as plt  # noqa: E402

from kitelab import config, registry, signals, sizing  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
CSV_PATH = os.path.join(OUT, "measurements", "deployment_2026-09-10.csv")
CURVE = os.path.join(OUT, "deployment_curve.png")

# Only two engines' worth of keys are universal: `cost_of_entry` is emitted by
# backtest.py's family but NOT by the `pair` engine, so position value is taken
# as shares x entry_price, which every engine carries and which reproduces
# cost_of_entry exactly where both exist (asserted below).
REQUIRED = ("entry_ts", "exit_ts", "shares", "entry_price", "symbol")


def position_value(t: dict) -> float:
    """Rupees committed at entry. Cross-checked against cost_of_entry when present."""
    v = float(t["shares"]) * float(t["entry_price"])
    c = t.get("cost_of_entry")
    if c is not None and v > 0 and abs(float(c) - v) / v > 1e-6:
        raise SystemExit(f"shares x entry_price = {v:,.0f} but cost_of_entry = "
                         f"{float(c):,.0f} -- the two disagree, stop here")
    return v


def session_index(trades_by_rule):
    """Every session any trade touches, as a sorted DatetimeIndex."""
    stamps = set()
    for trades in trades_by_rule.values():
        for t in trades:
            stamps.add(pd.Timestamp(t["entry_ts"]).normalize())
            stamps.add(pd.Timestamp(t["exit_ts"]).normalize())
    return pd.DatetimeIndex(sorted(stamps))


def occupancy(trades, pos):
    """(rupees demanded, positions open) per session, via a difference array.

    A trade contributes from its entry session up to but NOT including its exit
    session: it is sold at the exit close, so that day's capital is free again
    by the time the next decision is made.
    """
    n = len(pos)
    d_money = np.zeros(n + 1)
    d_count = np.zeros(n + 1)
    skipped = 0
    for t in trades:
        i = pos.get(pd.Timestamp(t["entry_ts"]).normalize())
        e = pos.get(pd.Timestamp(t["exit_ts"]).normalize())
        cost = position_value(t)
        if i is None or e is None or e <= i or cost <= 0:
            skipped += 1
            continue
        d_money[i] += cost; d_money[e] -= cost
        d_count[i] += 1.0;  d_count[e] -= 1.0
    return np.cumsum(d_money)[:n], np.cumsum(d_count)[:n], skipped


def main() -> None:
    cfg = config.load()
    universe = cfg.merged
    capital = float(sizing.CAPITAL)
    print(f"\n  {len(universe)} stocks, {len(registry.REGISTRY)} rules, "
          f"paper book Rs{capital:,.0f}.\n")

    loaded = {}
    for strat in registry.REGISTRY:
        trades = signals.load(f"{strat.cache}_all", universe)
        if not trades:
            print(f"  {strat.label}: no cache -- run scripts.refresh first")
            continue
        missing = [k for k in REQUIRED if k not in trades[0]]
        if missing:
            raise SystemExit(f"{strat.label}: cached trades lack {missing} -- "
                             "the cache format moved, do not trust this run")
        loaded[strat] = trades
    if not loaded:
        raise SystemExit("no signal caches loaded")

    index = session_index(loaded)
    pos = {ts: i for i, ts in enumerate(index)}
    print(f"  session grid: {len(index):,} sessions, "
          f"{index[0].date()} .. {index[-1].date()}")
    print("  first 3 sessions:", ", ".join(str(d.date()) for d in index[:3]))
    print()

    hdr = ("  {:<34}{:>9}{:>10}{:>10}{:>10}{:>11}{:>10}".format(
        "rule", "trades", "median %", "mean %", "p90 %", "max %", "flat %"))
    print(hdr); print("  " + "-" * (len(hdr) - 2))

    rows, curves = [], {}
    for strat, trades in loaded.items():
        money, count, skipped = occupancy(trades, pos)
        pct = 100.0 * money / capital
        live = count > 0
        flat = 100.0 * float((~live).mean())
        print(f"  {strat.label:<34}{len(trades) - skipped:>9,}"
              f"{np.median(pct):>10.1f}{pct.mean():>10.1f}"
              f"{np.percentile(pct, 90):>10.1f}{pct.max():>11.1f}{flat:>10.1f}")
        rows.append({"rule": strat.label, "trades": len(trades) - skipped,
                     "skipped": skipped,
                     "median_demand_pct": round(float(np.median(pct)), 2),
                     "mean_demand_pct": round(float(pct.mean()), 2),
                     "p90_demand_pct": round(float(np.percentile(pct, 90)), 2),
                     "max_demand_pct": round(float(pct.max()), 2),
                     "sessions_flat_pct": round(flat, 2),
                     "mean_positions_open": round(float(count.mean()), 2),
                     "median_positions_open": round(float(np.median(count)), 2)})
        curves[strat.label] = pct

    print("\n  'flat %' is the share of sessions with NO position open at all --")
    print("  the only column that means literal idle cash. 'demand' is what the")
    print("  rule asked for at 1% risk per trade; over 100% it could not all be")
    print("  taken, and portfolio.run's priority ordering chose what to skip.\n")

    counts = np.array([r["median_positions_open"] for r in rows])
    print(f"  median rule holds {np.median(counts):.0f} positions on a median "
          f"session, out of {len(universe)} names available.")
    print(f"  median rule is completely flat on "
          f"{np.median([r['sessions_flat_pct'] for r in rows]):.1f}% of sessions.")
    print(f"  median rule's median demand: "
          f"{np.median([r['median_demand_pct'] for r in rows]):.0f}% of the book.\n")

    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    order = sorted(rows, key=lambda r: r["median_demand_pct"])
    ax.barh([r["rule"] for r in order], [r["median_demand_pct"] for r in order],
            color="#1f77b4")
    ax.axvline(100, color="#d62728", ls="--", lw=1.5,
               label="the whole account (Rs1cr)")
    ax.set_xlabel("median demand, % of the paper book")
    ax.set_title("What each rule ASKS FOR on a median session\n"
                 "(no cash constraint in the signal cache -- demand, not fills)")
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CURVE, dpi=140)
    print(f"  wrote {CSV_PATH}, {CURVE}\n")


if __name__ == "__main__":
    main()

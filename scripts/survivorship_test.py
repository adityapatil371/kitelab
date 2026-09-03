"""What was survivorship bias costing us?

    python -m scripts.survivorship_test

Our stocks are the ones still listed today, so nothing in the backtest
ever went to zero. NSE's own delisting list (data/nse_delisted.csv) says how
often that really happens: 261 harmful Main-Board delistings 2006-2026, but
196 of them were the 2016-2018 clean-out of companies already suspended for
years. Excluding that clean-out leaves ~3.2 per year against roughly 1,800
listed companies -- about 0.2% a year. Spreading every event evenly instead
gives ~0.7% a year, which is the pessimistic bound.

So: inject deaths at those rates and re-run the account. A stock that dies
on date D has any open position written to ZERO on that day, and produces no
further trades -- exactly what a suspension-then-delisting does to a holder.
Repeat many times to get a distribution rather than one anecdote.
"""
from __future__ import annotations

import random

import numpy as np
import pandas as pd

from kitelab import config, portfolio, signals

CAPITAL = 250_000.0
RISK = 0.01
RATES = [0.002, 0.007]      # per stock per year: measured, and pessimistic bound
RUNS = 150
SEED = 20260829


def apply_deaths(trades: list[dict], deaths: dict[str, pd.Timestamp]) -> list[dict]:
    """Rewrite the trade list as if those stocks were delisted on those dates."""
    out = []
    for t in trades:
        died = deaths.get(t["symbol"])
        if died is None:
            out.append(t)
            continue
        entry = pd.Timestamp(t["entry_ts"])
        exit_ = pd.Timestamp(t["exit_ts"])
        if entry >= died:
            continue                      # stock no longer trades: signal never happens
        if exit_ >= died:                 # holding it when trading stopped
            t = dict(t)
            t["exit_ts"] = died
            t["exit_price"] = 0.0
            t["same_session"] = False
        out.append(t)
    return out


def main() -> None:
    trades = signals.require("EMA_all", config.load().merged)
    symbols = sorted({t["symbol"] for t in trades})
    start = min(pd.Timestamp(t["entry_ts"]) for t in trades)
    end = max(pd.Timestamp(t["exit_ts"]) for t in trades)
    span_days = (end - start).days
    years = span_days / 365.25
    base = portfolio.run(trades, CAPITAL, RISK)
    if base["cagr_pct"] is None:
        raise SystemExit("  baseline account was WIPED OUT -- nothing to compare against")
    print(f"\n  baseline (nothing dies): CAGR {base['cagr_pct']:.2f}%  "
          f"maxDD {base['max_drawdown_pct']:.1f}%  final {base['final']:,.0f}")
    print(f"  {len(symbols)} stocks, {years:.1f} years, {len(trades):,} signals\n", flush=True)

    rng = random.Random(SEED)
    for rate in RATES:
        cagrs, dds, killed = [], [], []
        p_death = min(1.0, rate * years)          # chance a given stock dies at some point
        for run in range(RUNS):
            deaths = {}
            for s in symbols:
                if rng.random() < p_death:
                    deaths[s] = start + pd.Timedelta(days=rng.random() * span_days)
            r = portfolio.run(apply_deaths(trades, deaths), CAPITAL, RISK)
            cagrs.append(r["cagr_pct"])          # None if that run wiped the account
            dds.append(r["max_drawdown_pct"])
            killed.append(len(deaths))
            if (run + 1) % 25 == 0:
                print(f"    rate {rate:.1%}/yr: {run+1}/{RUNS} runs", flush=True)
        # A wiped run has no CAGR, so it cannot enter a median or a mean. It is
        # reported as its own count rather than folded in as a 0.
        n_wiped = sum(1 for x in cagrs if x is None)
        c = np.array([x for x in cagrs if x is not None])
        d = np.array(dds)
        print(f"\n  DEATH RATE {rate:.1%} per stock per year "
              f"({p_death:.0%} chance over {years:.0f} years, {np.mean(killed):.0f} stocks die per run)")
        if n_wiped:
            print(f"    WIPED OUT in {n_wiped} of {len(cagrs)} runs -- excluded from the "
                  "CAGR statistics below, which therefore FLATTER the outcome")
        if not len(c):
            print("    every run wiped the account; no CAGR statistics possible")
            continue
        print(f"    CAGR   median {np.median(c):.2f}%   mean {c.mean():.2f}%   "
              f"p10 {np.percentile(c,10):.2f}%   p90 {np.percentile(c,90):.2f}%   worst {c.min():.2f}%")
        print(f"    cost vs baseline: median {np.median(c)-base['cagr_pct']:+.2f} points   "
              f"worst case {c.min()-base['cagr_pct']:+.2f} points")
        print(f"    maxDD  median {np.median(d):.1f}%  (baseline {base['max_drawdown_pct']:.1f}%)  "
              f"worst {d.min():.1f}%\n", flush=True)


if __name__ == "__main__":
    main()

"""Survivorship, applied to BOTH sides of the comparison. (2026-09-08)

WHY A SECOND SURVIVORSHIP SCRIPT. scripts.survivorship_test already injects
delistings, but only into the RULE's account. That answers "what did the rule
lose to companies that died?" It cannot answer the question that matters here,
which is comparative: survivorship inflates the BENCHMARK too, and probably by
more. A buy-and-hold investor holds a dying company all the way to zero. A
rule with a stop loss is out of it long before. So the correction is NOT a
uniform haircut applied to both sides -- it should differentially favour the
rule, and the only way to find out by how much is to kill the same companies
on the same dates in both accounts and re-measure the GAP.

HOW A DEATH IS MODELLED, on each side:
  rule -- any position open on the death date is written to ZERO that day, and
          the symbol produces no further signals. (scripts.survivorship_test's
          apply_deaths, reproduced here so that module is not imported and its
          constants not inherited.)
  hold -- that member's wealth goes to ZERO from the death date onward and
          stays there. It is still one of the N names the Rs1-each is divided
          between, so the loss is a real drag on the average, not a survivor
          reweighting.

WHERE THE RATES COME FROM. /data/raw/kitelab/nse_delisted.csv, NSE's own list,
counted in this script rather than copied from a docstring. Only HARMFUL types
count: Compulsory Delisting, Delisting - Liquidation, Operation of Law, and the
2020 gazette notification. Voluntary Delisting is EXCLUDED -- it is normally a
buyback at a premium, so a holder is paid, not wiped. The 2016-2018 spike is
a one-off administrative clean-out of companies suspended for years already,
so two rates are run: the post-clean-out regime (fair) and every harmful
event over the whole recorded window (pessimistic bound).

Each rate is divided by the window ITS OWN events were observed in. The file's
first harmful record is 2016-08-31 though Voluntary ones run from 2006, so
pre-2016 compulsory delistings are missing from the source, not absent from
history. Dividing 2016-2026 events by the 21-year backtest span would assume a
decade of zero corporate deaths and halve the correction.

WHAT IT CANNOT DO. Dead companies have no price history here, so they cannot
be added back to the universe -- only killed off among the survivors we have.
That understates the bias on both sides, because a real 2006 portfolio also
held names that fell 90% without ever formally delisting. Read the output as
a floor on the correction, not the whole of it.

Friction is ON (order <= 1% of daily traded value, impact charged): with it
off, illiquid fills dominate and the survivorship signal is unreadable.
kitelab/*.py is untouched -- both switches are run-time globals.

Reads:  /data/raw/kitelab/nse_delisted.csv, signal_cache/*_all.pkl, price frames
Writes: output/wf_survivor_<today>.csv
        output/wf_survivor_closes.pkl   (checkpoint: the price matrix)
Cost:   measured before the full run and printed; see --runs.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import pickle
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

import kitelab.config as C
from kitelab import portfolio, slippage
from kitelab.config import CLEAN

import scripts.wf_daily as wd

OUT = Path(__file__).resolve().parent.parent / "output"
CACHE = CLEAN / "signal_cache"
DELISTED = Path("/data/raw/kitelab/nse_delisted.csv")

CAPITAL = 200_000.0
RISK = 0.01
PRIORITY = "liquidity"
SEED = 20260908
LISTED_COMPANIES = 1800      # rough NSE Main Board count, the denominator
CLEANOUT = (2016, 2018)      # the administrative spike, excluded for the fair rate
HARMFUL = {"Compulsory Delisting", "Delisting - Liquidation", "Operation of Law"}


def death_rates() -> dict[str, float]:
    """Harmful delistings per listed company per year, two ways."""
    d = pd.read_csv(DELISTED)
    n0 = len(d)
    d["Delisted Date"] = pd.to_datetime(d["Delisted Date"])
    d = d[d["Board"] == "Main Board"]
    print(f"delisting file: {n0} rows -> {len(d)} after Main Board only")
    d = d[d["Delisted Date"] >= "2006-01-01"]
    print(f"  -> {len(d)} after dropping pre-2006")
    harmful = d[d["Type"].isin(HARMFUL) | d["Type"].str.startswith("Pursuant to gazette")]
    print(f"  -> {len(harmful)} after keeping harmful types only "
          f"(dropped {len(d) - len(harmful)}, almost all Voluntary Delisting, "
          f"which normally pays the holder a premium)")
    # THE DENOMINATOR IS NOT 2006-2026. The file records its FIRST harmful
    # delisting on 2016-08-31, while Voluntary ones appear steadily from 2006
    # (3 in 2006, 7 in 2007, ...). Compulsory delistings certainly happened in
    # India before 2016; NSE's published list simply does not carry them. So
    # each rate is divided by the window its own events were observed in, not
    # by the backtest span -- dividing 2016-2026 events by 21 years would
    # assume a decade of zero corporate deaths and halve the correction.
    yr = harmful["Delisted Date"].dt.year
    first = harmful["Delisted Date"].min()
    last = harmful["Delisted Date"].max()
    clean = harmful[(yr >= CLEANOUT[0]) & (yr <= CLEANOUT[1])]
    after = harmful[yr > CLEANOUT[1]]
    span_all = (last - first).days / 365.25
    span_after = (last - pd.Timestamp(f"{CLEANOUT[1]+1}-01-01")).days / 365.25
    print(f"  first harmful record {first.date()}, last {last.date()} "
          f"-- nothing before 2016, which is a RECORDING GAP, not a decade "
          f"without corporate deaths")
    print(f"  {len(clean)} fall in the {CLEANOUT[0]}-{CLEANOUT[1]} clean-out of "
          f"long-suspended shells; {len(after)} came after it")
    # fair: the post-clean-out regime, over the years it was observed in.
    # pessimistic: every harmful event over the whole recorded window, which
    # lets the one-off administrative purge stand in for a normal year.
    rates = {"fair": len(after) / span_after / LISTED_COMPANIES,
             "pessimistic": len(harmful) / span_all / LISTED_COMPANIES}
    print(f"  fair rate from {len(after)} events over {span_after:.1f} years; "
          f"pessimistic from {len(harmful)} over {span_all:.1f} years")
    for k, v in rates.items():
        print(f"  rate '{k}': {v*100:.2f}% of companies per year "
              f"({v*LISTED_COMPANIES:.1f} deaths a year out of {LISTED_COMPANIES})")
    return rates


def apply_deaths_rule(trades: list[dict], deaths: dict) -> list[dict]:
    """A death zeroes any open position and stops all later signals."""
    out = []
    for t in trades:
        died = deaths.get(t["symbol"])
        if died is None:
            out.append(t)
            continue
        if pd.Timestamp(t["entry_ts"]) >= died:
            continue                          # stock no longer trades
        if pd.Timestamp(t["exit_ts"]) >= died:
            t = dict(t)
            t["exit_ts"] = died
            t["exit_price"] = 0.0
            t["same_session"] = False
        out.append(t)
    return out


def hold_with_deaths(wealth: pd.DataFrame, deaths: dict) -> pd.Series:
    """Equal-weight hold where a dead member's wealth is zero from its death.

    `wealth` is already (close / first close), Rs1 in cash before listing.
    """
    if not deaths:
        return wealth.mean(axis=1)
    w = wealth.copy()
    idx = w.index
    cols = [s for s in deaths if s in w.columns]
    for s in cols:
        w.loc[idx >= deaths[s], s] = 0.0
    return w.mean(axis=1)


def wealth_matrix(closes: pd.DataFrame) -> pd.DataFrame:
    first = closes.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
    return (closes / first).fillna(1.0)


def cagr(s: pd.Series) -> float:
    if len(s) < 2 or s.iloc[0] <= 0 or s.iloc[-1] <= 0:
        return float("nan")
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100


def curve_series(curve) -> pd.Series:
    return pd.Series({ts: v for ts, v in curve}).sort_index()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="Turtle_1tf_55_20,EMA_b0")
    ap.add_argument("--runs", type=int, default=40,
                    help="Monte Carlo draws per rate (cost is printed first)")
    args = ap.parse_args()

    slippage.MAX_PARTICIPATION = 0.01
    slippage.ENABLED = True
    print("friction ON: order <= 1% of daily traded value, impact charged\n")

    rates = death_rates()
    cfg = C.load()
    members = set(cfg.merged)
    print(f"\nuniverse: {len(members)} symbols")

    ck = OUT / "wf_survivor_closes.pkl"
    if ck.exists():
        closes = pickle.loads(ck.read_bytes())
        print(f"price matrix: loaded checkpoint {ck.name} {closes.shape}")
    else:
        closes = wd.closes_matrix(members)
        ck.write_bytes(pickle.dumps(closes))
        print(f"price matrix: built and checkpointed to {ck.name}")
    wealth = wealth_matrix(closes)
    symbols = sorted(closes.columns)
    start, end = closes.index[0], closes.index[-1]
    years = (end - start).days / 365.25
    print(f"span {start.date()} .. {end.date()} = {years:.1f} years, "
          f"{len(symbols)} priced symbols")

    base_hold = wealth.mean(axis=1)
    print(f"baseline hold (nothing dies): {cagr(base_hold):.2f}%/yr")

    pools = {}
    for name in args.strategies.split(","):
        name = name.strip()
        blob = pickle.loads((CACHE / f"{name}_all.pkl").read_bytes())
        pools[name] = [t for t in blob["trades"] if t["symbol"] in members]
        r = portfolio.run(pools[name], CAPITAL, RISK, priority=PRIORITY)
        print(f"baseline {name}: {cagr(curve_series(r['curve'])):.2f}%/yr "
              f"({len(pools[name]):,} candidate trades)")

    # ---- cost, measured on one real draw before committing to the rest ----
    rng = random.Random(SEED)
    probe = {s: start + pd.Timedelta(days=rng.random() * (end - start).days)
             for s in symbols if rng.random() < 0.14}
    t0 = time.time()
    hold_with_deaths(wealth, probe)
    for name in pools:
        portfolio.run(apply_deaths_rule(pools[name], probe), CAPITAL, RISK,
                      priority=PRIORITY)
    per_draw = time.time() - t0
    total = per_draw * args.runs * len(rates)
    print(f"\none draw costs {per_draw:.2f}s; {args.runs} runs x {len(rates)} rates "
          f"= {total/60:.1f} minutes")

    stamp = dt.date.today().isoformat()
    path = OUT / f"wf_survivor_{stamp}.csv"
    fields = ["rate", "run", "deaths", "strategy", "rule_cagr", "hold_cagr", "gap_pts"]
    rng = random.Random(SEED)
    t0 = time.time()

    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        results = {}
        for rate_name, rate in rates.items():
            p_death = min(1.0, rate * years)
            print(f"\n{'='*70}\nRATE '{rate_name}': {rate*100:.2f}%/yr -> "
                  f"{p_death*100:.0f}% chance a given company dies over {years:.0f} years")
            for run in range(args.runs):
                deaths = {s: start + pd.Timedelta(days=rng.random() * (end - start).days)
                          for s in symbols if rng.random() < p_death}
                hc = cagr(hold_with_deaths(wealth, deaths))
                for name, pool in pools.items():
                    r = portfolio.run(apply_deaths_rule(pool, deaths), CAPITAL,
                                      RISK, priority=PRIORITY)
                    rc = cagr(curve_series(r["curve"]))
                    row = {"rate": rate_name, "run": run, "deaths": len(deaths),
                           "strategy": name, "rule_cagr": round(rc, 3),
                           "hold_cagr": round(hc, 3), "gap_pts": round(rc - hc, 3)}
                    w.writerow(row)
                    results.setdefault((rate_name, name), []).append(row)
                if (run + 1) % 10 == 0:
                    print(f"    {run+1}/{args.runs} draws, {time.time()-t0:.0f}s")

        for (rate_name, name), rs in results.items():
            g = np.array([r["gap_pts"] for r in rs])
            rc = np.array([r["rule_cagr"] for r in rs])
            hc = np.array([r["hold_cagr"] for r in rs])
            print(f"\n  {rate_name:<12}{name}")
            print(f"    deaths per draw   : {np.mean([r['deaths'] for r in rs]):.0f}")
            print(f"    hold CAGR         : median {np.median(hc):6.2f}%  "
                  f"(baseline {cagr(base_hold):.2f}%, "
                  f"so survivorship was worth {cagr(base_hold)-np.median(hc):+.2f} pts to hold)")
            print(f"    rule CAGR         : median {np.median(rc):6.2f}%")
            print(f"    GAP (rule - hold) : median {np.median(g):6.2f}  "
                  f"p10 {np.percentile(g,10):6.2f}  p90 {np.percentile(g,90):6.2f}")
            print(f"    draws where the rule beats hold: "
                  f"{int((g > 0).sum())} of {len(g)}")

    print(f"\nwrote {path}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()

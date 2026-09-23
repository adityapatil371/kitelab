"""What the Nifty 500 actually is, and what its members actually did.

    python3 -m scripts.n500_profile                  # ~1 min; writes a dated CSV
    python3 -m scripts.n500_profile --start 2012

WHY THIS EXISTS. scripts/n500_hold.py and scripts/n500_grid.py measure what
today's membership list does to a BACKTEST. Useful, and entirely negative: a
reader finishes them knowing which numbers not to trust and nothing about the
index itself. This measures the index as an object -- what it holds, how
concentrated it is, how differently its members behaved, and what holding one
of them actually felt like.

THE FOUR THINGS IT MEASURES, none of which the bias scripts can see:

  COMPOSITION   how the 496 study names split across NSE's own industry
                labels, by count and by share of trading.
  CONCENTRATION what share of the index's daily traded value sits in the
                biggest handful of names. NOT index weight -- see the warning
                on share_of_trading below.
  DISPERSION    the spread of per-stock CAGR. validation.buy_and_hold's
                docstring already records why this matters: on the 999 from
                2018 the median stock returned 8.5%/yr and the equal-weight
                portfolio 17.0%, because a right-skewed distribution lifts the
                portfolio far above its typical member. An average that no
                member earned is not a description of the members.
  PAIN          per-stock maximum drawdown -- the deepest fall from a running
                peak, on closes. The index line is smooth; the companies in it
                are not, and that difference is invisible in any CAGR.

WHAT share_of_trading IS NOT. The real Nifty 500 is weighted by MARKET VALUE.
No market-cap series is on disk, so index weight is not measured anywhere here
and is never claimed. What is measured is median daily traded value -- money
changing hands -- which answers "where is the trading" and not "what is the
index made of". The two are correlated and are not the same number.

SELF-CHECK BEFORE WRITING. The per-stock multipliers here must rebuild
validation.buy_and_hold on the same names to the second decimal, using
_hold's own arithmetic (Rs1 per member, skip under MIN_HOLD_BARS bars in the
span, one global calendar, one retention haircut). If they do not, this script
is reading different prices from the report it feeds and says so instead of
printing.

Reads:  data/keep/nifty500.json                    the study list + industries
        /data/clean/kitelab/*_day.parquet          via kitelab.frames
        /data/raw/mf/cache/tri_nifty_500.parquet   READ-ONLY, the real index
Writes: output/measurements/n500_profile_<year>_<date>.csv       per stock
        output/measurements/n500_profile_<year>_<date>.json      the aggregates
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import frames, validation
from scripts.wf_daily import START_DEFAULT

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "measurements"
TRI = Path("/data/raw/mf/cache/tri_nifty_500.parquet")

# "Recent" trading, for the concentration figure: the last year of bars. The
# question is where the money is NOW, not where it was averaged since 2018.
RECENT_BARS = 250
# Report a sector only when it has enough names for a median to mean anything.
MIN_SECTOR_NAMES = 8


def max_drawdown(close):
    """Deepest close-to-close fall from a running peak, as a negative percent.

    Closes only. Intraday lows would make every number worse, so this is the
    kind end of the estimate and is described that way in the report.
    """
    peak = np.maximum.accumulate(close)
    return float((100.0 * (close - peak) / peak).min())


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=START_DEFAULT,
                    help=f"start year (default {START_DEFAULT}, the board's)")
    args = ap.parse_args()
    t0 = time.time()
    year = args.start
    cut = pd.Timestamp(f"{year}-01-01")

    blob = json.loads((ROOT / "data" / "keep" / "nifty500.json").read_text())
    study, meta = blob["study"], blob["meta"]
    print(f"read data/keep/nifty500.json: {blob['n_constituents']} constituents, "
          f"{len(study)} in the study")
    unlabelled = [s for s in study if not meta.get(s, {}).get("industry")]
    if unlabelled:
        raise SystemExit(
            f"{len(unlabelled)} study names carry no industry label "
            f"({', '.join(unlabelled[:8])}...). The composition section cannot "
            f"be built from a partial labelling. Nothing was written.")
    print(f"  every one of the {len(study)} carries an NSE industry label")

    print(f"\nreading daily candles from {year}-01-01")
    rows, no_file, too_thin, bad_price = [], [], [], []
    for sym in study:
        try:
            d = frames.daily(sym)
        except SystemExit:
            no_file.append(sym)
            continue
        d = d[d["ts"] >= cut]
        # _hold's own gate, so the self-check below can be exact.
        if len(d) < validation.MIN_HOLD_BARS:
            too_thin.append(sym)
            continue
        close = d["close"].to_numpy(float)
        if not (close[0] > 0 and close[-1] > 0):
            bad_price.append(sym)
            continue
        tv = (d["close"] * d["volume"]).to_numpy(float)
        span_days = (d["ts"].iloc[-1] - d["ts"].iloc[0]).days / 365.25
        rows.append({
            "symbol": sym,
            "industry": meta[sym]["industry"],
            "bars": len(d),
            "first_ts": d["ts"].iloc[0].date().isoformat(),
            "last_ts": d["ts"].iloc[-1].date().isoformat(),
            "multiple": close[-1] / close[0],
            "own_cagr": 100.0 * ((close[-1] / close[0]) ** (1 / span_days) - 1),
            "max_drawdown": max_drawdown(close),
            "traded_value": float(np.median(tv[-RECENT_BARS:])),
        })
    print(f"  {len(study)} study names")
    print(f"  {-len(no_file):5d} no price file          -> {', '.join(no_file) or 'none'}")
    print(f"  {-len(too_thin):5d} under {validation.MIN_HOLD_BARS} bars since {year} "
          f"-> {', '.join(too_thin) or 'none'}")
    print(f"  {-len(bad_price):5d} non-positive first or last close "
          f"-> {', '.join(bad_price) or 'none'}")
    print(f"  {len(rows):5d} MEASURED")

    df = pd.DataFrame(rows)
    print(f"\ndataframe: {len(df)} rows x {len(df.columns)} columns: "
          f"{list(df.columns)}")
    for _, r in df.head(3).iterrows():
        print(f"  {r['symbol']:<12} {r['industry']:<28} "
              f"{r['own_cagr']:7.2f}%/yr  dd {r['max_drawdown']:6.1f}%")

    # ---- self-check: rebuild validation.buy_and_hold from these multipliers --
    # _hold compounds the SUMMED wealth over ONE global calendar span, from the
    # earliest first close to the latest last close, then applies one
    # retention haircut. Reproduce it exactly or refuse to write.
    first = pd.to_datetime(df["first_ts"]).min()
    last = pd.to_datetime(df["last_ts"]).max()
    years = (last - first).days / 365.25
    wealth = float(df["multiple"].sum()) * validation._hold_retention()
    rebuilt = ((wealth / len(df)) ** (1 / years) - 1) * 100.0
    want = validation.buy_and_hold(study, start_year=year)
    print(f"\n  SELF-CHECK  rebuilt equal-weight hold {rebuilt:.4f}%/yr "
          f"vs validation.buy_and_hold {want:.4f}%/yr")
    if abs(rebuilt - want) > 0.01:
        raise SystemExit(
            f"\n  SELF-CHECK FAILED by {abs(rebuilt-want):.4f} points. These "
            f"per-stock numbers do not rebuild the portfolio the rest of the "
            f"report quotes, so they are reading different prices.\n  "
            f"Nothing was written.\n")
    print(f"  SELF-CHECK PASSED (held {len(df)} names over {years:.2f} years)")

    # ---- dispersion: the average nobody earned -------------------------------
    pcts = [0, 10, 25, 50, 75, 90, 100]
    disp = {str(p): round(float(np.percentile(df["own_cagr"], p)), 2)
            for p in pcts}
    print(f"\n{'='*70}\nDISPERSION: per-stock CAGR since {year}\n{'='*70}")
    for p in pcts:
        print(f"  {p:3d}th percentile {disp[str(p)]:8.2f}%/yr")
    print(f"  equal-weight PORTFOLIO of the same names  {want:8.2f}%/yr")
    print(f"  -> the portfolio sits at the "
          f"{float((df['own_cagr'] < want).mean())*100:.0f}th percentile of "
          f"its own members")
    n_neg = int((df["own_cagr"] < 0).sum())
    print(f"  lost money outright: {n_neg} of {len(df)} ({n_neg/len(df):.0%})")

    # ---- pain ---------------------------------------------------------------
    dd = df["max_drawdown"]
    n_half = int((dd < -50).sum())
    print(f"\n{'='*70}\nPAIN: deepest fall from a running peak, on closes\n{'='*70}")
    print(f"  median stock {dd.median():.1f}%   shallowest {dd.max():.1f}%   "
          f"deepest {dd.min():.1f}%")
    print(f"  fell more than half: {n_half} of {len(df)} ({n_half/len(df):.0%})")

    # ---- the real index, for contrast ---------------------------------------
    tri_cagr = tri_dd = tri_from = tri_to = None
    if TRI.exists():
        t = pd.read_parquet(TRI)
        t["date"] = pd.to_datetime(t["date"])
        t = t[(t["date"] >= cut) & (t["date"] <= last)]
        if len(t) > 1:
            tv_ = t["tri"].to_numpy(float)
            yrs = (t["date"].iloc[-1] - t["date"].iloc[0]).days / 365.25
            tri_cagr = 100 * ((tv_[-1] / tv_[0]) ** (1 / yrs) - 1)
            tri_dd = max_drawdown(tv_)
            tri_from = t["date"].iloc[0].date().isoformat()
            tri_to = t["date"].iloc[-1].date().isoformat()
            print("\n  the real Nifty 500 index (cap-weighted, dividends in):")
            print(f"    {tri_cagr:.2f}%/yr, deepest fall {tri_dd:.1f}%")
            print(f"    against a MEDIAN MEMBER falling {dd.median():.1f}% "
                  f"-- the index is far smoother than anything inside it")
            beat = int((df["own_cagr"] > tri_cagr).sum())
            print(f"    {beat} of {len(df)} members beat the index itself "
                  f"({beat/len(df):.0%})")

    # ---- concentration -------------------------------------------------------
    top = df.sort_values("traded_value", ascending=False)
    total_tv = float(top["traded_value"].sum())
    conc = {str(n): round(100 * float(top["traded_value"].head(n).sum()) / total_tv, 2)
            for n in (10, 25, 50, 100, 200)}
    shares = top["traded_value"].to_numpy(float) / total_tv
    n_eff = float(1.0 / np.sum(shares ** 2))
    print(f"\n{'='*70}\nCONCENTRATION of TRADING (not of index weight)\n{'='*70}")
    for n in (10, 25, 50, 100, 200):
        print(f"  top {n:3d} names carry {conc[str(n)]:5.1f}% of daily traded value")
    print(f"  effective number of names (1/sum of squared shares): {n_eff:.0f} "
          f"of {len(df)}")

    # ---- composition ---------------------------------------------------------
    g = df.groupby("industry").agg(
        n=("own_cagr", "size"), median_cagr=("own_cagr", "median"),
        median_dd=("max_drawdown", "median"), tv=("traded_value", "sum"))
    g["share_of_trading"] = 100 * g["tv"] / total_tv
    g = g.sort_values("n", ascending=False)
    print(f"\n{'='*70}\nCOMPOSITION: {len(g)} NSE industry labels\n{'='*70}")
    print(f"  {'names':>5} {'%trade':>7} {'med CAGR':>9} {'med fall':>9}  industry")
    for name, r in g.iterrows():
        print(f"  {r.n:5.0f} {r.share_of_trading:6.1f}% {r.median_cagr:8.1f}% "
              f"{r.median_dd:8.1f}%  {name}")
    big = g[g.n >= MIN_SECTOR_NAMES].sort_values("median_cagr", ascending=False)
    print(f"\n  of the {len(big)} sectors with at least {MIN_SECTOR_NAMES} names, "
          f"best to worst median: {big['median_cagr'].iloc[0]:.1f}%/yr "
          f"({big.index[0]}) to {big['median_cagr'].iloc[-1]:.1f}%/yr "
          f"({big.index[-1]})")

    # ---- write ---------------------------------------------------------------
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    df["start_year"] = year
    csv_path = OUT / f"n500_profile_{year}_{stamp}.csv"
    df.to_csv(csv_path, index=False)
    print(f"\n  wrote {csv_path} ({len(df)} rows x {len(df.columns)} cols)")

    summary = {
        "start_year": year, "written": stamp,
        "n_constituents": blob["n_constituents"], "n_study": len(study),
        "n_measured": len(df), "n_no_file": len(no_file),
        "n_too_thin": len(too_thin), "too_thin_names": sorted(too_thin),
        "min_hold_bars": validation.MIN_HOLD_BARS,
        "recent_bars": RECENT_BARS, "min_sector_names": MIN_SECTOR_NAMES,
        "span_years": round(years, 3),
        "first_ts": first.date().isoformat(), "last_ts": last.date().isoformat(),
        "portfolio_cagr": round(float(want), 4),
        "selfcheck_rebuilt_cagr": round(float(rebuilt), 4),
        "cagr_percentiles": disp,
        "portfolio_percentile_of_members":
            round(float((df["own_cagr"] < want).mean()) * 100, 1),
        "n_negative": n_neg,
        "median_drawdown": round(float(dd.median()), 2),
        "worst_drawdown": round(float(dd.min()), 2),
        "shallowest_drawdown": round(float(dd.max()), 2),
        "n_halved": n_half,
        "tri_cagr": None if tri_cagr is None else round(float(tri_cagr), 4),
        "tri_drawdown": None if tri_dd is None else round(float(tri_dd), 2),
        "tri_from": tri_from, "tri_to": tri_to,
        "n_beat_index": None if tri_cagr is None
                        else int((df["own_cagr"] > tri_cagr).sum()),
        "concentration": conc,
        "effective_names": round(n_eff, 1),
        "sectors": [
            {"industry": name, "n": int(r.n),
             "share_of_trading": round(float(r.share_of_trading), 2),
             "median_cagr": round(float(r.median_cagr), 2),
             "median_drawdown": round(float(r.median_dd), 2)}
            for name, r in g.iterrows()],
    }
    json_path = OUT / f"n500_profile_{year}_{stamp}.json"
    json_path.write_text(json.dumps(summary, indent=1))
    print(f"  wrote {json_path}")
    print(f"  took {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()

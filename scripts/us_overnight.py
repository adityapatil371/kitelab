"""The overnight/intraday split, run on US history instead of NSE.

Same decomposition as `scripts/overnight_split.py`, same log arithmetic, on a
market with roughly five times the calendar. That is the point: the binding
constraint on every result in this project is INDEPENDENT CALENDAR TIME, not
symbol count -- clustering standard errors on symbol moves them 1.00x while
clustering on entry year moves them 2.4-3.6x (measured 2026-09-24). ~100 years
of US history is the only sample anyone has that materially loosens it.

What it can settle: whether "the equity premium accrues overnight" is a fact
about markets or a fact about NSE 2006-2026.
What it cannot settle: anything about trading NSE today. US costs, tick sizes
and session rules are not India's, and were not even their own before
decimalisation (2001) and deregulated commissions (1975).

Source: Yahoo, through the `yfinance` package -- no key, no account. Two
earlier sources failed on 2026-09-24 and are recorded so nobody retries them:
stooq answered with a no-JavaScript bot-challenge page instead of CSV, and
Yahoo's bare chart URL answered HTTP 429 on the FIRST request (it now wants a
cookie-and-crumb handshake, which is what yfinance exists to carry).

    ./.venv/bin/pip install yfinance

SPLIT AND DIVIDEND ADJUSTMENT MATTERS HERE and is easy to get wrong. The
overnight leg spans the boundary a split or dividend sits on, so raw prices
invent a huge fake gap; the intraday leg never spans one. The fix is to scale
BOTH of a day's prices by that day's adjclose/close factor. The intraday leg is
then algebraically unchanged and the overnight leg is correctly adjusted.

Reads  : query1.finance.yahoo.com over HTTPS
Writes : CLEAN/us_<symbol>.csv                     (the raw download, cached)
         output/measurements/us_overnight_<date>.csv
         output/figures/us_overnight_<date>.png

**Run this on the Mac, not in the dev container** -- the container firewall
allows PyPI only, so every fetch there fails with ECONNREFUSED. From inside the
kitelab folder:  ./.venv/bin/python -m scripts.us_overnight
It is UNTESTED as written (2026-09-24): it could not be executed where it was
written. Read its first output carefully rather than trusting it.

`--symbols` overrides the default list. Downloads are cached in CLEAN, so a
second run is free and the network is touched once per symbol.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

from kitelab import config as cfg

# ^GSPC is the S&P 500 -- an average, so bid-ask bounce diversifies away, which
# is exactly why the NSE version trusts NIFTY 50 for magnitude. The rest are
# large caps with long, continuous listings.
DEFAULT = ["^GSPC", "^DJI", "^IXIC", "AAPL", "GE", "KO", "JNJ",
           "XOM", "PG", "MMM", "IBM", "CAT"]
URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
       "?period1=-2208988800&period2=9999999999&interval=1d&events=div%2Csplit")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

GAP_DAYS = 7
LEG_CAP = 0.40
MIN_DAYS = 250
SESSIONS_PER_YEAR = 252

MEAS = os.path.join("output", "measurements")
FIG = os.path.join("output", "figures")


def _download(sym: str) -> pd.DataFrame:
    """Daily split/dividend-adjusted OHLC, cached in CLEAN so the net is hit once.

    Prefers `yfinance`, which carries Yahoo's cookie-and-crumb handshake. Without
    it the bare chart URL answers HTTP 429 on the very first request (observed
    2026-09-24), and stooq answers with a bot-challenge page, so the direct path
    below is a fallback that is expected to fail more often than not.

    yfinance's auto_adjust scales open, high, low and close by the SAME daily
    factor, which is exactly what this measurement needs: the intraday leg is
    algebraically unchanged and the overnight leg stops inventing a fake gap on
    every split. AdjClose is set equal to Close so the caller's factor is 1.0.
    """
    cache = os.path.join(str(cfg.CLEAN), f"us_{sym.replace('^', 'idx_')}.csv")
    if os.path.exists(cache) and os.path.getsize(cache) > 200:
        return pd.read_csv(cache)

    try:
        import yfinance as yf
    except ImportError:
        yf = None

    if yf is not None:
        h = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=True)
        if h.empty:
            raise ValueError(f"{sym}: yfinance returned an empty frame")
        h = h.reset_index()
        datecol = "Date" if "Date" in h.columns else h.columns[0]
        frame = pd.DataFrame({
            "Date": pd.to_datetime(h[datecol], utc=True).dt.tz_localize(None).dt.normalize(),
            "Open": h["Open"].astype(float), "Close": h["Close"].astype(float),
        })
        frame["AdjClose"] = frame["Close"]
    else:
        print("  (yfinance not installed -- trying the bare URL, which usually 429s;"
              " install it with  ./.venv/bin/pip install yfinance )")
        req = urllib.request.Request(URL.format(sym=urllib.parse.quote(sym)),
                                     headers={"User-Agent": UA,
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
        if not body.lstrip().startswith("{"):
            raise ValueError(f"{sym}: expected JSON, got {body[:120]!r}")
        payload = json.loads(body)
        if payload.get("chart", {}).get("error"):
            raise ValueError(f"{sym}: yahoo says {payload['chart']['error']}")
        res = payload["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
        frame = pd.DataFrame({
            "Date": pd.to_datetime(res["timestamp"], unit="s", utc=True)
                      .tz_convert(None).normalize(),
            "Open": q["open"], "Close": q["close"],
            "AdjClose": adj if adj is not None else q["close"],
        })

    frame = frame.dropna().sort_values("Date")
    frame.to_csv(cache, index=False)
    print(f"  downloaded {sym}: {len(frame):,} rows "
          f"({frame['Date'].min().date()} to {frame['Date'].max().date()}) -> {cache}")
    time.sleep(1.0)          # be a polite client
    return frame


def _ann(log_sum: float, n_days: int) -> float:
    return 100.0 * (np.exp(SESSIONS_PER_YEAR * log_sum / n_days) - 1.0) if n_days else np.nan


def _nw_t(x: np.ndarray, lag: int = 21) -> float:
    """Newey-West t: an ordinary t-stat widened for the fact that neighbouring
    days are not independent. lag=21 matches the NSE run."""
    n = len(x)
    e = x - x.mean()
    s = (e @ e) / n
    for L in range(1, lag + 1):
        s += 2.0 * (1.0 - L / (lag + 1)) * ((e[L:] @ e[:-L]) / n)
    return float(x.mean() / np.sqrt(s / n))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=DEFAULT)
    ap.add_argument("--since", type=int, default=None,
                    help="keep sessions from this year on (data is cached, "
                         "so re-cutting an era costs nothing)")
    args = ap.parse_args()

    rows, curves = [], {}
    for sym in args.symbols:
        try:
            d = _download(sym)
        except Exception as exc:          # yfinance raises its own types too
            # noqa: BLE001 -- this is a fetch loop; name the failure, keep going
            print(f"  FAILED {sym}: {type(exc).__name__}: {exc}")
            continue

        need = {"Date", "Open", "Close", "AdjClose"}
        if not need.issubset(d.columns):
            print(f"  {sym}: missing {sorted(need - set(d.columns))}, skipped")
            continue
        d["Date"] = pd.to_datetime(d["Date"])
        d = d.sort_values("Date")
        if args.since:
            d = d[d["Date"].dt.year >= args.since]
        raw_n = len(d)
        d = d.dropna(subset=["Open", "Close", "AdjClose"])
        d = d[(d["Open"] > 0) & (d["Close"] > 0) & (d["AdjClose"] > 0)]

        # Scale both of a day's prices by that day's factor. The intraday leg is
        # unchanged by construction; the overnight leg becomes split/dividend safe.
        factor = d["AdjClose"] / d["Close"]
        op, cl = d["Open"] * factor, d["AdjClose"]

        lo = np.log(op / cl.shift(1))
        li = np.log(cl / op)
        keep = (lo.notna() & li.notna() & d["Date"].diff().dt.days.le(GAP_DAYS)
                & lo.abs().le(LEG_CAP) & li.abs().le(LEG_CAP))
        print(f"  {sym:<7} {raw_n:>7,} rows -> {int(keep.sum()):>7,} clean sessions "
              f"({int((~keep).sum()):,} dropped)")
        if int(keep.sum()) < MIN_DAYS:
            print(f"  {sym}: under {MIN_DAYS} clean sessions, skipped")
            continue

        lo, li, ts = lo[keep], li[keep], d.loc[keep, "Date"]
        # A "synthetic open" -- a feed that reports open == previous close
        # because it never had a real opening print -- forces the entire day
        # into the intraday leg and zeroes the overnight one. On long US
        # histories this is a REAL hazard before the 1990s. A high share here
        # means the split is measuring the data vendor, not the market.
        synthetic = float((lo.abs() < 1e-9).mean())
        rows.append({
            "symbol": sym, "days": len(lo),
            "first": ts.iloc[0].date(), "last": ts.iloc[-1].date(),
            "synthetic_open_pct": 100.0 * synthetic,
            "overnight_pct_yr": _ann(lo.sum(), len(lo)),
            "intraday_pct_yr": _ann(li.sum(), len(li)),
            "total_pct_yr": _ann(lo.sum() + li.sum(), len(lo)),
            "overnight_t": _nw_t(lo.values), "intraday_t": _nw_t(li.values),
            "overnight_sharpe": float(lo.mean() / lo.std(ddof=1) * np.sqrt(SESSIONS_PER_YEAR)),
            "intraday_sharpe": float(li.mean() / li.std(ddof=1) * np.sqrt(SESSIONS_PER_YEAR)),
        })
        if sym.startswith("^"):
            curves[sym] = pd.DataFrame({"on": lo.cumsum().values,
                                        "id": li.cumsum().values}, index=ts.values)

    if not rows:
        raise SystemExit("nothing downloaded -- are you on the Mac, not the container?")
    out = pd.DataFrame(rows)
    print(f"\nresult: {out.shape[0]} rows x {out.shape[1]} columns")
    with pd.option_context("display.width", 200, "display.float_format", "{:.2f}".format):
        print(out[["symbol", "days", "first", "last", "synthetic_open_pct",
                   "overnight_pct_yr", "intraday_pct_yr", "total_pct_yr",
                   "overnight_t", "intraday_t"]].to_string(index=False))
    bad = out[out.synthetic_open_pct > 5.0]
    if len(bad):
        print(f"\nWARNING -- {len(bad)} symbol(s) report open == previous close on "
              f">5% of sessions; their split measures the FEED, not the market:")
        print(bad[["symbol", "synthetic_open_pct"]].to_string(index=False))
    print(f"\novernight leg bigger than intraday leg in "
          f"{int((out.overnight_pct_yr > out.intraday_pct_yr).sum())} of {len(out)} symbols")
    print("NSE 2006-2026 for comparison "
          "(output/measurements/overnight_split_2026-09-24.csv):")
    print("  NIFTY 50   overnight +23.41 %/yr (t +9.42)   intraday -10.00 %/yr (t -2.41)")
    print("  NIFTY BANK overnight +27.40 %/yr (t +7.94)   intraday -10.28 %/yr (t -2.05)")

    os.makedirs(MEAS, exist_ok=True)
    os.makedirs(FIG, exist_ok=True)
    stamp = dt.date.today().isoformat()
    suffix = f"_since{args.since}" if args.since else ""
    path = os.path.join(MEAS, f"us_overnight{suffix}_{stamp}.csv")
    out.to_csv(path, index=False)
    print(f"wrote {path}")

    if not curves:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib not installed -- CSV written, figure skipped. "
              "./.venv/bin/pip install matplotlib if you want it)")
        return
    fig, axes = plt.subplots(1, len(curves), figsize=(6 * len(curves), 5), squeeze=False)
    for ax, (name, c) in zip(axes[0], curves.items()):
        ax.plot(c.index, np.exp(c["on"]), label="overnight only")
        ax.plot(c.index, np.exp(c["id"]), label="intraday only")
        ax.plot(c.index, np.exp(c["on"] + c["id"]), label="both = buy and hold",
                lw=2, color="k")
        ax.set_yscale("log")
        ax.set_title(f"{name} -- gross of all costs")
        ax.set_ylabel("growth of 1 (log scale)")
        ax.legend()
        ax.grid(alpha=0.3)
    fig.tight_layout()
    figpath = os.path.join(FIG, f"us_overnight_{stamp}.png")
    fig.savefig(figpath, dpi=120)
    print(f"wrote {figpath}")


if __name__ == "__main__":
    main()

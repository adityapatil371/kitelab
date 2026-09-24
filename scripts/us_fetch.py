"""Fetch US daily OHLCV into CLEAN in kitelab's own parquet schema.

WHY THIS EXISTS. `scripts/us_overnight.py` only needed open and close, so it
cached thin CSVs. Running the BOARD'S RULES on US data needs the full bar:
high and low (every ATR stop, `low252`, `inside`), and volume (`dryup`, `vol`,
and the slippage/participation model). This script writes
`CLEAN/US_<SYM>_day.parquet` with columns ts, open, high, low, close, volume --
byte-identical in shape to an NSE `<SYM>_day.parquet`, so `frames.daily()`
loads it with no engine change.

READS:  Yahoo, via the `yfinance` package (it carries the cookie/crumb
        handshake that the bare chart URL now rejects with HTTP 429).
WRITES: CLEAN/US_<SYM>_day.parquet, one per symbol, and a manifest CSV at
        output/measurements/us_fetch_<date>.csv.

MUST RUN ON THE MAC. The container's firewall blocks query*.finance.yahoo.com.

ADJUSTMENT. auto_adjust=True, so every bar is split- and dividend-adjusted on
one consistent basis. Unadjusted prices would put a split boundary between two
adjacent bars and manufacture a fake overnight gap.

SURVIVORSHIP. This universe is companies that are LISTED TODAY. Firms that
went to zero are absent, so every number computed on it is flattered. That is
the same bias the NSE universe carries, which is the point -- the comparison
is like for like -- but neither is an unbiased estimate of what a trader
would have earned.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitelab.config import CLEAN  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output"

# Large, long-listed US names. Deliberately includes chronic laggards (GE, IBM,
# INTC, F, T, XRX, WBA, KHC) so the sample is not only compounders.
UNIVERSE = """
AAPL MSFT AMZN GOOGL META NVDA TSLA JPM V JNJ WMT PG MA HD DIS BAC ADBE CRM
NFLX XOM CVX PFE KO PEP CSCO ABT ACN AVGO COST NKE MRK TMO MCD WFC LLY DHR
TXN NEE UNP ORCL PM HON QCOM UPS LOW MS SBUX BMY RTX AMGN CAT GS BLK DE INTC
IBM GE MMM AXP BKNG ISRG SPGI ADP GILD CVS MDLZ TJX MO SYK VRTX ZTS CI CB SO
DUK PLD LMT ELV MU AMT BDX CL NSC ITW EMR FDX ETN APD PSA AON MET AIG TRV ALL
PRU AFL STZ KMB GIS K HSY SJM CAG CPB CLX ADM DOW DD PPG SHW ECL NEM FCX
HAL SLB OXY PSX VLO MPC KMI WMB EOG COP F GM T VZ CMCSA DISH XRX WBA KHC
HPQ DELL WDC STX NTAP JNPR CSX NOC GD BA LHX TDG ROK PH CMI PCAR
""".split()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="override the universe")
    ap.add_argument("--sleep", type=float, default=0.8)
    ap.add_argument("--min-days", type=int, default=1000,
                    help="skip a symbol with fewer usable sessions")
    args = ap.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        sys.exit("need yfinance:  ./.venv/bin/pip install yfinance")

    syms = args.symbols or UNIVERSE
    syms = sorted(dict.fromkeys(syms))
    print(f"universe: {len(syms)} symbols -> {CLEAN}")

    need = ["Open", "High", "Low", "Close", "Volume"]
    rows, failed = [], []
    for i, sym in enumerate(syms, 1):
        path = CLEAN / f"US_{sym}_day.parquet"
        if path.exists():
            d = pd.read_parquet(path)
            rows.append({"symbol": sym, "rows": len(d), "first": d.ts.min().date(),
                         "last": d.ts.max().date(), "source": "cached"})
            print(f"  [{i:3d}/{len(syms)}] {sym:<6} cached  {len(d):>6,} rows")
            continue
        try:
            h = yf.Ticker(sym).history(period="max", interval="1d",
                                       auto_adjust=True)
        except Exception as exc:                      # noqa: BLE001 fetch loop
            failed.append((sym, repr(exc)[:80]))
            print(f"  [{i:3d}/{len(syms)}] {sym:<6} FAILED  {exc!r:.60}")
            time.sleep(args.sleep)
            continue

        if h.empty:
            failed.append((sym, "empty frame"))
            print(f"  [{i:3d}/{len(syms)}] {sym:<6} FAILED  empty frame")
            time.sleep(args.sleep)
            continue

        missing = [c for c in need if c not in h.columns]
        if missing:
            sys.exit(f"{sym}: yfinance frame is missing {missing} -- "
                     f"got {list(h.columns)}")

        h = h.reset_index()
        datecol = "Date" if "Date" in h.columns else h.columns[0]
        d = pd.DataFrame({
            "ts": pd.to_datetime(h[datecol], utc=True).dt.tz_localize(None)
                    .dt.normalize(),
            "open": h["Open"].astype("float64"),
            "high": h["High"].astype("float64"),
            "low": h["Low"].astype("float64"),
            "close": h["Close"].astype("float64"),
            "volume": h["Volume"].fillna(0).astype("int64"),
        })
        before = len(d)
        d = d[d[["open", "high", "low", "close"]].notna().all(axis=1)]
        d = d[(d[["open", "high", "low", "close"]] > 0).all(axis=1)]
        d = d.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        print(f"  [{i:3d}/{len(syms)}] {sym:<6} {before:>6,} -> {len(d):>6,} rows"
              f"  {d.ts.min().date()} .. {d.ts.max().date()}")

        if len(d) < args.min_days:
            failed.append((sym, f"only {len(d)} sessions"))
            time.sleep(args.sleep)
            continue

        d.to_parquet(path, index=False)
        rows.append({"symbol": sym, "rows": len(d), "first": d.ts.min().date(),
                     "last": d.ts.max().date(), "source": "fetched"})
        time.sleep(args.sleep)

    man = pd.DataFrame(rows)
    print(f"\nwrote {len(man)} of {len(syms)} symbols")
    if failed:
        print(f"  {len(failed)} skipped or failed:")
        for s, why in failed:
            print(f"    {s:<6} {why}")
    if man.empty:
        sys.exit("nothing fetched")

    print(f"  manifest {man.shape[0]} rows x {man.shape[1]} columns")
    print(man.head(3).to_string(index=False))
    csv = OUT / "measurements" / f"us_fetch_{date.today().isoformat()}.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    man.to_csv(csv, index=False)
    print(f"  -> {csv}")
    print(f"  span {man['first'].min()} .. {man['last'].max()}, "
          f"median {man['rows'].median():,.0f} sessions/symbol")


if __name__ == "__main__":
    main()

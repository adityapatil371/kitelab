"""Fetch the class-assignment instruments into the same Parquet store.

    python -m scripts.fetch_assets            # everything
    python -m scripts.fetch_assets --btc      # Bitcoin only (no Kite login needed)
    python -m scripts.fetch_assets --kite     # indices + commodities (needs login)

Three data paths, one storage format:

    BITCOIN            Binance public API, no account or key needed. BTCUSDT daily
                       and native 30-minute candles from 2017. Timestamps are UTC
                       (Bitcoin trades 24/7; the "daily" candle is a UTC-midnight
                       convention). Prices are US dollars.
    NIFTY 50 /
    NIFTY BANK         Kite index candles, same API as the stocks. Daily from 2006,
                       15-minute from 2015. Indices have no volume.
    GOLD / SILVER /
    CRUDEOIL           Kite MCX futures with continuous=1, which stitches expired
                       monthly contracts into one series. Continuous data is only
                       reliable for DAILY candles, so these three get no intraday.

Everything lands as data/{SYMBOL}_day.parquet (+ _30minute / _15minute where it
exists), which is all frames/strategies/reports need.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime

import pandas as pd
import requests

from kitelab import auth, config, fetch
from kitelab.config import DATA

BINANCE = "https://api.binance.com/api/v3/klines"
BTC_SYMBOL = "BITCOIN"
INDICES = ["NIFTY 50", "NIFTY BANK"]
COMMODITIES = ["GOLD", "SILVER", "CRUDEOIL"]


# ---------------------------------------------------------------------------
# Bitcoin via Binance
# ---------------------------------------------------------------------------

def _binance_page(interval: str, start_ms: int) -> list:
    response = requests.get(BINANCE, params={
        "symbol": "BTCUSDT", "interval": interval,
        "startTime": start_ms, "limit": 1000,
    }, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_btc(interval_binance: str, interval_file: str) -> None:
    target = DATA / f"{BTC_SYMBOL}_{interval_file}.parquet"
    existing = pd.read_parquet(target) if target.exists() else pd.DataFrame(
        columns=fetch.COLUMNS)
    if existing.empty:
        start_ms = int(datetime(2017, 8, 1).timestamp() * 1000)
    else:
        start_ms = int(existing["ts"].max().timestamp() * 1000) + 1

    rows, requests_made = [], 0
    while True:
        page = _binance_page(interval_binance, start_ms)
        requests_made += 1
        if not page:
            break
        for k in page:
            rows.append((pd.to_datetime(k[0], unit="ms"), float(k[1]), float(k[2]),
                         float(k[3]), float(k[4]), int(float(k[5]))))
        start_ms = page[-1][0] + 1
        if len(page) < 1000:
            break
        time.sleep(0.15)  # well inside Binance's public rate limit
        if requests_made % 25 == 0:
            print(f"    BITCOIN {interval_file}: {len(rows):,} candles so far...",
                  flush=True)

    fresh = pd.DataFrame(rows, columns=fetch.COLUMNS)
    combined = (pd.concat([existing, fresh], ignore_index=True)
                .dropna(subset=["ts"])
                .drop_duplicates(subset=["ts"], keep="last")
                .sort_values("ts").reset_index(drop=True))
    combined.to_parquet(target, index=False)
    print(f"  BITCOIN    {interval_file:<9} {requests_made:>3} req  "
          f"{len(combined):>7,} bars (+{len(combined) - len(existing):,})  "
          f"{combined['ts'].min()} .. {combined['ts'].max()}  (UTC, USD)")


# ---------------------------------------------------------------------------
# Indices and MCX continuous futures via Kite
# ---------------------------------------------------------------------------

def fetch_indices(kite, throttle) -> None:
    dump = pd.read_parquet(sorted(DATA.glob("instruments_NSE_*.parquet"))[-1])
    for symbol in INDICES:
        match = dump[dump["tradingsymbol"] == symbol]
        if match.empty:
            print(f"  !! {symbol}: not in the NSE instrument dump -- skipped")
            continue
        token = int(match.iloc[0]["instrument_token"])
        fetch.fetch_interval(kite, symbol, token, "day", "2006-01-01", throttle)
        fetch.fetch_interval(kite, symbol, token, "15minute", "2015-01-01", throttle)


def fetch_commodities(kite, throttle) -> None:
    cache = DATA / f"instruments_MCX_{date.today().isoformat()}.parquet"
    if cache.exists():
        dump = pd.read_parquet(cache)
    else:
        dump = pd.DataFrame(kite.instruments("MCX"))
        dump.to_parquet(cache, index=False)
        print(f"  cached {len(dump):,} MCX instruments -> {cache.name}")

    futures = dump[dump["instrument_type"] == "FUT"].copy()
    futures["expiry"] = pd.to_datetime(futures["expiry"]).dt.date
    for name in COMMODITIES:
        live = futures[(futures["name"] == name) & (futures["expiry"] >= date.today())]
        if live.empty:
            print(f"  !! {name}: no live MCX future found -- skipped")
            continue
        # Any live contract works as the anchor; continuous=1 stitches the history.
        token = int(live.sort_values("expiry").iloc[0]["instrument_token"])
        try:
            fetch.fetch_interval(kite, name, token, "day", "2006-01-01", throttle,
                                 continuous=True)
        except Exception as exc:
            print(f"  !! {name} day: {type(exc).__name__}: {exc}")
        # Continuous intraday is not reliably served; try once, fail quietly.
        try:
            frame = fetch.fetch_interval(kite, name, token, "30minute", "2020-01-01",
                                         throttle, continuous=True)
            if frame.empty:
                fetch.path_for(name, "30minute").unlink(missing_ok=True)
                print(f"     {name}: no continuous intraday (expected) -- daily only")
        except Exception:
            print(f"     {name}: no continuous intraday (expected) -- daily only")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--btc", action="store_true", help="Bitcoin only")
    parser.add_argument("--kite", action="store_true", help="indices + commodities only")
    args = parser.parse_args()
    do_btc = args.btc or not args.kite
    do_kite = args.kite or not args.btc

    print()
    if do_btc:
        print("Bitcoin (Binance, no login needed):")
        fetch_btc("1d", "day")
        fetch_btc("30m", "30minute")
    if do_kite:
        print("\nIndices and commodities (Kite):")
        cfg = config.load()
        kite = auth.client(cfg)
        throttle = fetch.Throttle()
        fetch_indices(kite, throttle)
        fetch_commodities(kite, throttle)
    print("\nDone.\n")


if __name__ == "__main__":
    main()

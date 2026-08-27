"""Precompute everything the local dashboard's sliders can ask for.

    python -m scripts.dashboard_data

Writes data/dashboard.json: a full grid of portfolio simulations --
strategy (EMA class rule / EMA broker-style intrabar stops / Breakout)
x universe (all 199 / 49 in-sample / 150 holdout)
x risk (0.25 / 0.5 / 1 / 2 %) x capital (50k / 1L / 2.5L / 5L)
-- plus the timeframe-triplet tables, the six class assets vs buy-and-hold,
and a NIFTY 50 overlay series. The dashboard (python -m scripts.chart, then
http://localhost:8765/dashboard) is a pure viewer: sliders select among these
precomputed runs, nothing is simulated in the browser.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd

from kitelab import backtest, config, frames, portfolio, sizing, strategies
from scripts.drawdown_report import bh_stats, episodes, underwater_stats
from scripts.tf_compare import (ASSIGNED, VARIANTS as TF_VARIANTS, simulate_variant,
                                summarise as tf_summarise, window_start)

CACHE = Path(__file__).resolve().parent.parent / "data" / "signal_cache"
OUT = Path(__file__).resolve().parent.parent / "data" / "dashboard.json"
RISKS = [0.25, 0.5, 1.0, 2.0]
CAPITALS = [50_000, 100_000, 250_000, 500_000]
ASSETS = [("BITCOIN", "30m", 0.0010), ("NIFTY 50", "30m", 0.0005),
          ("NIFTY BANK", "30m", 0.0005), ("GOLD", "1d", 0.0005),
          ("SILVER", "1d", 0.0005), ("CRUDEOIL", "1d", 0.0005)]
STEP = 5   # keep every 5th trading day of each equity curve (~weekly)


def curve_payload(curve):
    days, eq, dd = [], [], []
    peak = float("-inf")
    for i, (day, equity) in enumerate(curve):
        peak = max(peak, equity)
        if i % STEP and i != len(curve) - 1:
            continue
        days.append(day.strftime("%Y-%m-%d"))
        eq.append(round(equity))
        dd.append(round(100 * (equity - peak) / peak, 1))
    return {"d": days, "eq": eq, "dd": dd}


def run_payload(r):
    longest, current = underwater_stats(r["curve"])
    eps = [{"peak": str(e["peak_day"].date()), "trough": str(e["trough_day"].date()),
            "depth": round(e["depth_pct"], 1),
            "recovered": str(e["recovered"].date()) if e["recovered"] is not None else None,
            "years": round(e["days"] / 365.25, 1)} for e in episodes(r["curve"])]
    return {"cagr": round(r["cagr_pct"], 1), "final": round(r["final"]),
            "ret": round(r["return_pct"]), "maxdd": round(r["max_drawdown_pct"], 1),
            "uw_long": round(longest / 365.25, 1), "uw_now": round(current / 365.25, 1),
            "taken": len(r["taken"]), "signals": r["signals"],
            "episodes": eps, "curve": curve_payload(r["curve"])}


def build_intrabar_ema() -> list[dict]:
    path = CACHE / "EMA_intrabar_199.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    cfg = config.load()
    out = []
    for index, symbol in enumerate(cfg.all_symbols, 1):
        try:
            out.extend(backtest.simulate(symbol, stop_on_close=False))
        except SystemExit:
            pass
        if index % 25 == 0:
            print(f"    intrabar EMA: {index}/{len(cfg.all_symbols)}", flush=True)
    path.write_bytes(pickle.dumps(out))
    return out


def main() -> None:
    cfg = config.load()
    strategies_data = {
        "ema": ("EMA -- class rule (stops at closes)",
                pickle.loads((CACHE / "EMA_199.pkl").read_bytes())),
        "ema_intrabar": ("EMA -- broker stops (intrabar)", build_intrabar_ema()),
        "brk": ("Breakout (intrabar by nature)",
                pickle.loads((CACHE / "Breakout_199.pkl").read_bytes())),
    }
    universes = {"all": ("All 199 stocks", None),
                 "in": ("49 in-sample", set(cfg.in_sample)),
                 "out": ("150 holdout", set(cfg.out_of_sample))}

    grid = {}
    for skey, (slabel, signals) in strategies_data.items():
        for ukey, (ulabel, members) in universes.items():
            subset = (signals if members is None
                      else [t for t in signals if t["symbol"] in members])
            for risk in RISKS:
                for capital in CAPITALS:
                    r = portfolio.run(subset, capital, risk / 100)
                    grid[f"{skey}|{ukey}|{risk:g}|{capital}"] = run_payload(r)
            print(f"  grid: {slabel} / {ulabel} done", flush=True)

    tf = {}
    for key, label, desc in TF_VARIANTS:
        rows, allt = [], []
        for symbol in ASSIGNED:
            w = window_start(symbol)
            kept = [t for t in simulate_variant(symbol, key)
                    if pd.Timestamp(t["entry_ts"]).normalize() >= w]
            allt += kept
            s = tf_summarise(kept)
            rows.append({"sym": symbol, **{k: round(v, 2) if isinstance(v, float) else v
                        for k, v in s.items() if k != "charges"}})
        s = tf_summarise(allt)
        rows.append({"sym": "ALL 5", **{k: round(v, 2) if isinstance(v, float) else v
                     for k, v in s.items() if k != "charges"}})
        tf[key] = {"label": label, "desc": desc, "rows": rows}
        print(f"  timeframes: {label} done", flush=True)

    assets = []
    for symbol, brk_tf, fee in ASSETS:
        backtest.FLAT_FEE_RATE = fee
        sizing.FRACTIONAL = True
        try:
            ema = backtest.simulate(symbol)
            brk = strategies.ath_breakout_trades(symbol, True, timeframe=brk_tf)
            bh = bh_stats(frames.daily(symbol))
            for strat, trades in (("EMA", ema), ("Breakout", brk)):
                if not trades:
                    continue
                r = portfolio.run(trades, 100_000, 0.01)
                longest, _ = underwater_stats(r["curve"])
                assets.append({"sym": symbol, "strat": strat,
                               "cagr": round(r["cagr_pct"], 1),
                               "maxdd": round(r["max_drawdown_pct"], 1),
                               "uw": round(longest / 365.25, 1),
                               "trades": len(r["taken"]),
                               "bh_cagr": round(bh["cagr"], 1),
                               "bh_dd": round(bh["maxdd"], 1),
                               "bh_uw": round(bh["longest_uw"] / 365.25, 1)})
        finally:
            backtest.FLAT_FEE_RATE = None
            sizing.FRACTIONAL = False
        print(f"  assets: {symbol} done", flush=True)

    nifty = frames.daily("NIFTY 50")
    bench = {"d": [], "close": []}
    for i, rec in enumerate(nifty.itertuples(index=False)):
        if i % STEP and i != len(nifty) - 1:
            continue
        bench["d"].append(rec.ts.strftime("%Y-%m-%d"))
        bench["close"].append(round(float(rec.close), 1))

    payload = {
        "built": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "strategies": {k: v[0] for k, v in strategies_data.items()},
        "universes": {k: v[0] for k, v in universes.items()},
        "risks": RISKS, "capitals": CAPITALS,
        "grid": grid, "timeframes": tf, "assets": assets, "nifty": bench,
    }
    OUT.write_text(json.dumps(payload))
    print(f"\n  written: {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()

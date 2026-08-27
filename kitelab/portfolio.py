"""Simulate an actual account rather than summing independent trades.

Every result so far added up trades as if each had its own funding. A real account has
one pot of money: if it is tied up in three positions, the fourth signal is missed, and
which signals you happen to catch changes everything.

    * risk is 1% of CURRENT equity, so the account compounds
    * a position is capped by cash on hand
    * signals arriving with no free cash are skipped, not queued
    * one open position per stock

Drawdown is measured on a DAILY MARK-TO-MARKET equity curve: every day, equity =
cash + shares x that day's close for every open position, drawdown = distance below
the running peak, in percent OF THAT PEAK. The old method (equity sampled only when
a trade settled, open positions held at cost, and the rupee dip divided by the FINAL
peak instead of the concurrent one) understated drawdown three separate ways; it is
kept under legacy_* keys so old reports can be reconciled.
"""
from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd

from . import frames, sizing
from .backtest import charges


@lru_cache(maxsize=None)
def _daily_closes(symbol: str):
    day = frames.daily(symbol)
    return (day["ts"].to_numpy().astype("datetime64[ns]"),
            day["close"].to_numpy().astype(float))


def daily_curve(taken: list[dict], capital: float) -> dict:
    """Daily mark-to-market equity curve for an already-simulated set of positions.

    Re-prices the account every trading day (union of the involved symbols'
    sessions): cash moves on entry/exit days exactly as in the simulation, and
    open positions are valued at that day's close (last known close on a day the
    stock did not trade). Drawdown is quoted against the peak standing at the
    time of the dip, never the final peak.
    """
    if not taken:
        return {"curve": [], "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
                "peak_date": None, "trough_date": None}
    symbols = sorted({t["symbol"] for t in taken})
    start = np.datetime64(pd.Timestamp(min(t["entry_ts"] for t in taken)).normalize(), "ns")
    end = np.datetime64(pd.Timestamp(max(t["exit_ts"] for t in taken)).normalize(), "ns")
    calendar = np.unique(np.concatenate([_daily_closes(s)[0] for s in symbols]))
    calendar = calendar[(calendar >= start) & (calendar <= end)]

    aligned = {}
    for s in symbols:
        ts, close = _daily_closes(s)
        idx = np.searchsorted(ts, calendar, side="right") - 1
        aligned[s] = np.where(idx >= 0, close[np.maximum(idx, 0)], np.nan)

    def day_pos(stamp) -> int:
        return int(np.searchsorted(
            calendar, np.datetime64(pd.Timestamp(stamp).normalize(), "ns")))

    # A trade that opens and closes within the SAME session never holds an
    # overnight position: it only moves cash on that day. Keeping it out of
    # the open-positions map matters for intraday strategies, where a symbol
    # can round-trip several times in one day (the map holds one entry per
    # symbol, so same-day trades would collide with each other and with a
    # later overnight entry).
    entries_by_day: dict[int, list] = defaultdict(list)
    exits_by_day: dict[int, list] = defaultdict(list)
    sameday_by_day: dict[int, list] = defaultdict(list)
    for t in taken:
        t = dict(t)
        t["_entry_day"] = day_pos(t["entry_ts"])
        exit_day = day_pos(t["exit_ts"])
        if exit_day == t["_entry_day"]:
            sameday_by_day[exit_day].append(t)
        else:
            entries_by_day[t["_entry_day"]].append(t)
            exits_by_day[exit_day].append(t)

    cash = capital
    open_by_symbol: dict[str, dict] = {}
    curve: list[tuple] = []
    peak = capital
    running_peak_day = pd.Timestamp(calendar[0]) if len(calendar) else None
    max_dd = 0.0
    max_dd_pct = 0.0
    peak_date = trough_date = None

    def close_out(t) -> None:
        nonlocal cash
        proceeds = t["shares"] * t["exit_price"]
        cash += proceeds - charges(t["shares"] * t["entry_price"], proceeds,
                                   t["same_session"])
        del open_by_symbol[t["symbol"]]

    for i in range(len(calendar)):
        # settle yesterday's positions first, then open today's overnight
        # positions, then net the same-day round trips through cash.
        for t in exits_by_day.get(i, ()):
            close_out(t)
        for t in entries_by_day.get(i, ()):
            cash -= t["shares"] * t["entry_price"]
            open_by_symbol[t["symbol"]] = t
        for t in sameday_by_day.get(i, ()):
            proceeds = t["shares"] * t["exit_price"]
            cash += proceeds - t["shares"] * t["entry_price"] \
                - charges(t["shares"] * t["entry_price"], proceeds, t["same_session"])
        equity = cash + sum(t["shares"] * aligned[s][i]
                            for s, t in open_by_symbol.items())
        day = pd.Timestamp(calendar[i])
        curve.append((day, equity))
        if equity > peak:
            peak, running_peak_day = equity, day
        dip_pct = (equity - peak) / peak
        if dip_pct < max_dd_pct:
            max_dd_pct, peak_date, trough_date = dip_pct, running_peak_day, day
        max_dd = min(max_dd, equity - peak)

    return {"curve": curve, "max_drawdown": max_dd,
            "max_drawdown_pct": 100 * max_dd_pct,
            "peak_date": peak_date, "trough_date": trough_date}


def run(trades: list[dict], capital: float = 10_000.0, risk_pct: float = 0.01) -> dict:
    entries = sorted(trades, key=lambda t: t["entry_ts"])
    cash = capital
    peak = capital
    open_by_symbol: dict[str, dict] = {}
    curve: list[tuple] = [(entries[0]["entry_ts"], capital)] if entries else []
    taken: list[dict] = []
    skipped_cash = skipped_size = skipped_busy = 0
    max_drawdown = 0.0
    max_concurrent = 0

    def settle(upto) -> None:
        nonlocal cash, peak, max_drawdown
        for symbol, pos in list(open_by_symbol.items()):
            if pos["exit_ts"] > upto:
                continue
            proceeds = pos["shares"] * pos["exit_price"]
            cost = charges(pos["shares"] * pos["entry_price"], proceeds, pos["same_session"])
            cash += proceeds - cost
            pos["net"] = proceeds - pos["shares"] * pos["entry_price"] - cost
            taken.append(pos)
            del open_by_symbol[symbol]
            equity = cash + sum(p["shares"] * p["entry_price"] for p in open_by_symbol.values())
            curve.append((pos["exit_ts"], equity))
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)

    for trade in entries:
        settle(trade["entry_ts"])
        if trade["symbol"] in open_by_symbol:
            skipped_busy += 1
            continue

        equity = cash + sum(p["shares"] * p["entry_price"] for p in open_by_symbol.values())
        per_share_risk = trade["entry_price"] - trade["stop"]
        if per_share_risk <= 0:
            continue
        if sizing.FRACTIONAL:
            by_risk = equity * risk_pct / per_share_risk
            by_cash = cash / trade["entry_price"]
            shares = round(min(by_risk, by_cash), 6)
        else:
            by_risk = math.floor(equity * risk_pct / per_share_risk)
            by_cash = math.floor(cash / trade["entry_price"])
            shares = min(by_risk, by_cash)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            if by_risk < 1:
                skipped_size += 1      # 1% of equity cannot cover even one share's risk
            else:
                skipped_cash += 1      # the risk rule allows it, the bank balance does not
            continue

        cash -= shares * trade["entry_price"]
        open_by_symbol[trade["symbol"]] = {**trade, "shares": shares}
        max_concurrent = max(max_concurrent, len(open_by_symbol))

    settle(max(t["exit_ts"] for t in entries)) if entries else None

    final = cash
    years = ((entries[-1]["exit_ts"] - entries[0]["entry_ts"]).days / 365.25) if entries else 0
    growth = final / capital
    marked = daily_curve(taken, capital)
    return {
        "capital": capital, "final": final, "profit": final - capital,
        "return_pct": 100 * (growth - 1),
        "cagr_pct": 100 * (growth ** (1 / years) - 1) if years > 0 and growth > 0 else 0.0,
        "years": years,
        "signals": len(entries),
        "skipped_cash": skipped_cash, "skipped_size": skipped_size,
        "skipped_busy": skipped_busy,
        # True drawdown: daily mark-to-market, percent of the concurrent peak.
        "max_drawdown": marked["max_drawdown"],
        "max_drawdown_pct": marked["max_drawdown_pct"],
        "dd_peak_date": marked["peak_date"],
        "dd_trough_date": marked["trough_date"],
        # The numbers every report before 2026-08-27 printed: settlement-sampled,
        # positions at cost, rupee dip divided by the FINAL peak. Kept for
        # reconciliation only -- do not quote these.
        "legacy_max_drawdown": max_drawdown,
        "legacy_max_drawdown_pct": 100 * max_drawdown / peak if peak else 0.0,
        "max_concurrent": max_concurrent,
        "wins": sum(1 for t in taken if t["net"] > 0),
        "curve": marked["curve"],
        "legacy_curve": curve,
        "taken": taken,
    }

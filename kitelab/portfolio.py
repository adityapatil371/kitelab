"""Simulate an actual account rather than summing independent trades.

Every result so far added up trades as if each had its own funding. A real account has
one pot of money: if it is tied up in three positions, the fourth signal is missed, and
which signals you happen to catch changes everything.

    * risk is 1% of CURRENT equity, so the account compounds
    * a position is capped by cash on hand
    * signals arriving with no free cash are skipped, not queued
    * one open position per stock

The equity curve is recorded at settlement events, so drawdown is measured on realised
equity. Open positions are held at cost, which means intra-trade drawdown is understated
-- the real dips were deeper than the ones reported here.
"""
from __future__ import annotations

import math

from .backtest import charges


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
        by_risk = math.floor(equity * risk_pct / per_share_risk)
        by_cash = math.floor(cash / trade["entry_price"])
        shares = min(by_risk, by_cash)
        if shares < 1:
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
    return {
        "capital": capital, "final": final, "profit": final - capital,
        "return_pct": 100 * (growth - 1),
        "cagr_pct": 100 * (growth ** (1 / years) - 1) if years > 0 and growth > 0 else 0.0,
        "years": years,
        "signals": len(entries), "taken": len(taken),
        "skipped_cash": skipped_cash, "skipped_size": skipped_size,
        "skipped_busy": skipped_busy,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": 100 * max_drawdown / peak if peak else 0.0,
        "max_concurrent": max_concurrent,
        "wins": sum(1 for t in taken if t["net"] > 0),
        "curve": curve,
        "taken": taken,
    }

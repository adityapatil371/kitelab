"""Darvas: in on a 20-candle high, out on a 10-candle low.

    entry   the close finishes ABOVE the highest high of the previous 20 candles
    stop    the lowest low of the previous 10 candles, as it stands at entry
    exit    the close finishes AT OR BELOW the lowest low of the previous 10 candles,
            recomputed every day -- so the exit line ratchets up behind a rising
            stock and never moves down. It is a trailing stop made of price alone.

The two windows never include today's candle. Both are shifted one bar, so the
line a trade is judged against was fully formed before the session it acts on.
Without that shift a bar could break a high it had itself just set.

Convention. Everything is read at closes, matching the class rule for the EMA
stacks (2026-08-28): a close above the line buys at that close, a close below the
exit line sells at that close. Pass intrabar=True for the Turtle reading instead,
where the channel edges are live orders and a touch fills at the line -- a gap
through it fills at the open, which is worse and honest.

Naming, so nobody is misled later: Nicolas Darvas drew BOXES around consolidation
and bought the break of the box top. The 20-in/10-out channel implemented here is
the Donchian rule the Turtles traded, which is what trading classes usually mean
when they say Darvas. It is what was asked for; it is not what Darvas wrote.

Trades come out in the same shape backtest.simulate produces, so portfolio.run,
the report writers and the dashboard all take them unchanged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import frames, sizing, slippage
from .backtest import charges

ENTRY_LEN = 20
EXIT_LEN = 10


def channels(symbol: str, entry_len: int = ENTRY_LEN,
             exit_len: int = EXIT_LEN) -> pd.DataFrame:
    """Daily bars plus the two channel edges, both strictly point-in-time."""
    day = frames.daily(symbol).reset_index(drop=True)
    out = day.copy()
    # shift(1): the window ends on the PREVIOUS candle, never this one.
    out["upper"] = day["high"].rolling(entry_len).max().shift(1)
    out["lower"] = day["low"].rolling(exit_len).min().shift(1)
    return out


def simulate(symbol: str, entry_len: int = ENTRY_LEN, exit_len: int = EXIT_LEN,
             intrabar: bool = False) -> list[dict]:
    """Walk the channels and produce closed trades, oldest first."""
    frame = channels(symbol, entry_len, exit_len)
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    upper = frame["upper"].to_numpy(dtype=float)
    lower = frame["lower"].to_numpy(dtype=float)
    stamps = frame["ts"].tolist()
    total = len(frame)

    trades: list[dict] = []
    position = 0
    while position < total:
        if not np.isfinite(upper[position]) or not np.isfinite(lower[position]):
            position += 1
            continue
        broke = (high[position] > upper[position] if intrabar
                 else close[position] > upper[position])
        if not broke:
            position += 1
            continue

        # A gap straight through the line fills at the open, not at the line.
        entry_price = (max(float(upper[position]), float(open_[position])) if intrabar
                       else float(close[position]))
        stop = float(lower[position])
        risk = entry_price - stop
        if risk <= 0:
            position += 1
            continue

        exit_at = None
        for step in range(position + 1, total):
            line = lower[step]
            if not np.isfinite(line):
                continue
            if intrabar:
                if low[step] <= line:
                    gapped = open_[step] < line
                    exit_at = (step, float(open_[step]) if gapped else float(line),
                               "gap through channel" if gapped else "10-candle low")
                    break
            elif close[step] <= line:
                exit_at = (step, float(close[step]), "10-candle low")
                break

        if exit_at is None:
            break                      # still open at the end of the data

        exit_index, exit_price, reason = exit_at
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue

        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, stamps[position], quoted_entry, +1)
        exit_price = slippage.fill(symbol, stamps[exit_index], quoted_exit, -1)
        buy_value = entry_price * shares
        sell_value = exit_price * shares
        gross = (exit_price - entry_price) * shares
        same_session = stamps[position].date() == stamps[exit_index].date()
        # ONE fee convention, everywhere: a trade that opens and closes in the same
        # session is billed at intraday rates, which is what Zerodha actually
        # charges and what portfolio.run has always done. It used to be billed
        # delivery here and intraday only into the *_best fields, so the W/D/H
        # trade lists (the only ones with same-session trades) disagreed with the
        # account engine, and with themselves across the fills toggle.
        cost = cost_best = charges(buy_value, sell_value, intraday=same_session)

        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[position],
            "exit_ts": stamps[exit_index],
            "same_session": same_session,
            "entry_time": "EOD",
            "level": float(upper[position]),
            "level_kind": f"{entry_len}-candle high",
            "max_risk_capital": sizing.risk_budget(),
            "risk_taken": risk_taken,
            "capital_capped": capped,
            "range": risk,
            "target": None,
            "final_stop": float(lower[exit_index]),
            "bars_held": exit_index - position,
            "charges_best": cost_best,
            "net_profit_best": gross - cost_best,
            "entry_date": stamps[position],
            "entry_price": entry_price,
            "quoted_entry": quoted_entry,
            "quoted_exit": quoted_exit,
            "spread_cost": ((quoted_exit - exit_price) + (entry_price - quoted_entry)) * shares,
            "stop": stop,
            "risk_per_share": risk,
            "exit_date": stamps[exit_index],
            "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (stamps[exit_index] - stamps[position]).days,
            "sessions_held": exit_index - position,
            "shares": shares,
            "cost_of_entry": buy_value,
            "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost,
            "net_profit": gross - cost,
        })
        position = exit_index + 1

    return trades

"""Darvas: a WEEKLY breakout gates a daily 20-candle-high entry.

    gate    THE SAME RULE, one timeframe up, checked FIRST. While it is shut the
            daily rule is not consulted at all.
              opens   a weekly close above the previous 20 weekly candles' high
              shuts   a weekly close at or below the previous 10 weekly candles' low
            It is a state with memory, not a per-bar test. That matters: a
            20-candle high is a record, and a record stops being one the moment
            it is set, so testing "is this week above the 20-week high" on its
            own would shut the gate a week after it opened. What keeps you in is
            the 10-week low not being broken.

            WHY (2026-09-01): on one timeframe the Turtle rule takes every
            breakout, including the ones against the larger trend, and those are
            where the losses came from. The weekly is the trend filter; the
            daily is the timing.
    entry   the close finishes ABOVE the highest high of the previous 20 candles
    stop    the lowest low of the previous 10 candles, as it stands at entry
    exit    the close finishes AT OR BELOW the lowest low of the previous 10 candles,
            recomputed every day -- so the exit line ratchets up behind a rising
            stock and never moves down. It is a trailing stop made of price alone.

The two windows never include today's candle. Both are shifted one bar, so the
line a trade is judged against was fully formed before the session it acts on.
Without that shift a bar could break a high it had itself just set.

The weekly gate is shifted the same way, and then some. Two separate guards
against reading the future:
  - its own channel is shift(1), so the week is not compared to itself;
  - the week CONSULTED is the last one that has actually finished. On a Wednesday
    the current week's bar is still forming and its close is not known, so using
    it would let Thursday's decision depend on Friday's price.
The gate is tested at ENTRY only. Once in, the exit is the daily channel alone --
the position is not closed just because the weekly condition later lapses.

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
WEEKLY_ENTRY_LEN = 20    # the weekly gate opens above this many weeks' high
WEEKLY_EXIT_LEN = 10     # and shuts below this many weeks' low


def channels(symbol: str, entry_len: int = ENTRY_LEN,
             exit_len: int = EXIT_LEN) -> pd.DataFrame:
    """Daily bars plus the two channel edges, both strictly point-in-time."""
    day = frames.daily(symbol).reset_index(drop=True)
    out = day.copy()
    # shift(1): the window ends on the PREVIOUS candle, never this one.
    out["upper"] = day["high"].rolling(entry_len).max().shift(1)
    out["lower"] = day["low"].rolling(exit_len).min().shift(1)
    return out


def weekly_state(day: pd.DataFrame, entry_len: int = WEEKLY_ENTRY_LEN,
                 exit_len: int = WEEKLY_EXIT_LEN) -> np.ndarray:
    """Per WEEKLY bar: is the gate open at that week's close?

    A state machine, not a formula. The gate has memory, so whether it is open
    this week depends on a breakout that may have happened months ago and has
    not been given back since.
    """
    week = frames.weekly(day)
    close = week["close"].to_numpy(dtype=float)
    upper = week["high"].rolling(entry_len).max().shift(1).to_numpy(dtype=float)
    floor = week["low"].rolling(exit_len).min().shift(1).to_numpy(dtype=float)

    on = np.zeros(len(week), dtype=bool)
    state = False
    for i in range(len(week)):
        if state and np.isfinite(floor[i]) and close[i] <= floor[i]:
            state = False
        elif not state and np.isfinite(upper[i]) and close[i] > upper[i]:
            state = True
        on[i] = state
    return on


def weekly_gate(day: pd.DataFrame, entry_len: int = WEEKLY_ENTRY_LEN,
                exit_len: int = WEEKLY_EXIT_LEN) -> np.ndarray:
    """Per DAILY bar: was the gate open at the last COMPLETED week's close?

    Two guards against reading the future. The weekly channel is shift(1), so a
    week is never compared with itself; and the week CONSULTED is the previous
    one, because on a Wednesday the current week has not closed and using it
    would let Thursday's decision depend on Friday's price.
    """
    week = frames.weekly(day)
    on = weekly_state(day, entry_len, exit_len)
    pos = np.searchsorted(week["ts"].to_numpy(), day["ts"].to_numpy(), side="right") - 1
    prev = pos - 1
    out = np.zeros(len(day), dtype=bool)
    usable = prev >= 0
    out[usable] = on[prev[usable]]
    return out


def simulate(symbol: str, entry_len: int = ENTRY_LEN, exit_len: int = EXIT_LEN,
             intrabar: bool = False, weekly: bool = True) -> list[dict]:
    """Walk the channels and produce closed trades, oldest first.

    weekly=False drops the gate, giving the single-timeframe Turtle rule. That
    is the control this whole change is measured against, not a fallback.
    """
    frame = channels(symbol, entry_len, exit_len)
    gate = (weekly_gate(frame) if weekly else np.ones(len(frame), dtype=bool))
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
        # The weekly condition is checked FIRST. While it is false the daily
        # channel is not consulted at all.
        if not gate[position]:
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

"""Swing-low trailing stops for the two level strategies.

The rule, as taught: whenever the stock makes a new higher swing low, move the stop up
to it. The stop NEVER moves down -- that is what makes it a trailing stop rather than
just a stop. There is no profit target; the trade ends when the trail is hit.

Two honesty constraints, both of which cost the strategy rather than flatter it:

    * A swing low is only usable once CONFIRMED. A 5-bar pivot needs 5 bars on each
      side, so it is invisible until 5 bars after it forms. Moving a stop to a pivot
      the moment it prints would be using information that did not exist yet.
    * The stop is only raised to a pivot that sits below the current bar's low.
      Otherwise it would trigger on the very bar that set it.

The trail is read from the same timeframe as each strategy's initial stop: 30-minute
swing lows for the support bounce, daily swing lows for the breakout. Using daily
pivots on a trade that lasts three days would mean the stop never moves at all.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import levels

PIVOT_SPAN = 5


def intraday_trail(bars: pd.DataFrame, pivots: list[int], span: int = PIVOT_SPAN):
    """Trailing rule reading swing lows off the same bars the trade runs on."""
    lows = bars["low"].to_numpy()

    def make():
        cursor = 0

        def step(position: int, stop: float, current_low: float) -> float:
            nonlocal cursor
            while cursor < len(pivots) and pivots[cursor] + span <= position:
                candidate = float(lows[pivots[cursor]])
                if stop < candidate < current_low:
                    stop = candidate
                cursor += 1
            return stop
        return step
    return make


def daily_trail(daily: pd.DataFrame, pivots: list[int], stamps, span: int = PIVOT_SPAN):
    """Trailing rule reading swing lows off daily bars, for trades held for months."""
    session_starts = daily["ts"].dt.normalize().to_numpy()
    lows = daily["low"].to_numpy()
    bar_sessions = pd.DatetimeIndex(stamps).normalize().to_numpy()

    def make():
        cursor = 0

        def step(position: int, stop: float, current_low: float) -> float:
            nonlocal cursor
            today = int(np.searchsorted(session_starts, bar_sessions[position], "right")) - 1
            # Confirmed by the close of the PREVIOUS session, never the current one.
            while cursor < len(pivots) and pivots[cursor] + span <= today - 1:
                candidate = float(lows[pivots[cursor]])
                if stop < candidate < current_low:
                    stop = candidate
                cursor += 1
            return stop
        return step
    return make


def resolve(bars: pd.DataFrame, entry_pos: int, initial_stop: float, step,
            entry_price: float | None = None, scale_out: str | None = None,
            exit_signal=None, exit_reason: str = "exit signal"):
    """Walk forward, ratcheting the stop.

    step may be None, meaning the stop never moves -- a plain initial stop. That is
    only useful alongside exit_signal, which is a per-bar boolean array: the first
    True at or after entry closes the trade at that bar's close. The stop is checked
    FIRST, so a bar that both breaks the stop and raises the exit flag is recorded as
    a stop, the conservative reading used everywhere in this project.

    scale_out (needs entry_price):
        "half"    -- sell half the position at entry + 1R, rest runs unchanged
        "half_be" -- same, and the stop on the remainder jumps to breakeven
    Same-bar ambiguity resolves to the STOP, the conservative reading throughout
    this project.

    Returns (position, exit_price, reason, final_stop, banked_fraction, banked_price)
    where banked_fraction is 0.0 (no scale-out happened) or 0.5.
    """
    open_ = bars["open"].to_numpy()
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    close = bars["close"].to_numpy()

    stop = initial_stop
    trigger = (entry_price + (entry_price - initial_stop)
               if scale_out and entry_price is not None else None)
    banked_fraction, banked_price = 0.0, 0.0
    for position in range(entry_pos, len(bars)):
        if low[position] <= stop:
            gapped = open_[position] < stop
            moved = stop > initial_stop
            reason = ("gap through stop" if gapped
                      else ("trailing stop" if moved else "initial stop"))
            return (position, (float(open_[position]) if gapped else stop), reason,
                    stop, banked_fraction, banked_price)
        if exit_signal is not None and exit_signal[position]:
            return (position, float(close[position]), exit_reason, stop,
                    banked_fraction, banked_price)
        if trigger is not None and banked_fraction == 0.0 and high[position] >= trigger:
            # A gap ABOVE the trigger sells at the open -- a better fill, and real.
            banked_price = max(trigger, float(open_[position]))
            banked_fraction = 0.5
            if scale_out == "half_be":
                stop = max(stop, entry_price)
        if step is not None:
            stop = step(position, stop, float(low[position]))

    last = len(bars) - 1
    return (last, float(close[last]), "open (marked to market)", stop,
            banked_fraction, banked_price)


def pivot_lows(frame: pd.DataFrame, span: int = PIVOT_SPAN) -> list[int]:
    return levels.pivot_lows(frame, span)

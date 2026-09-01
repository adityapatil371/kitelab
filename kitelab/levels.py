"""Horizontal price levels you draw on the chart, and the arithmetic around them.

A level is just a price. What makes it usable in a backtest is knowing the earliest
date it could honestly have been drawn -- which is its SECOND touch, because a single
touch is not yet a line. Signals before that date are hindsight, so `valid_from` is
computed here rather than trusted from the UI.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd

from .config import CLEAN

# Hand-drawn and NOT reproducible from the raw downloads, but it is edited at
# runtime, so it cannot live in the read-only raw directory. It sits in CLEAN
# with the derived data and must be backed up separately -- deleting CLEAN
# throws away work that no rebuild can recreate.
LEVELS_PATH = CLEAN / "levels.json"

# Two different questions need two different bands.
#
# TOUCH_TOLERANCE answers "is price AT the line right now" -- the trade trigger. It has
# to be tight, because the stops these strategies use are 0.5%-1.6% wide, so entering
# 2% away from the line is a materially different trade.
#
# VALIDATION_TOLERANCE answers "is this line real" -- how valid_from is computed. A line
# drawn by eye is often respected at 1-2% without ever coming within 0.25%. Measuring
# legitimacy with the trigger band threw away four levels that price demonstrably
# reacted to; measuring triggers with the validation band would invent phantom setups.
TOUCH_TOLERANCE = 0.0025          # 0.25% -- trade trigger
VALIDATION_TOLERANCE = 0.01       # 1%    -- does this level exist at all
# Bars must leave the level for this long before a new touch counts as a new event,
# so a week of chopping along support is one touch, not thirty.
TOUCH_SEPARATION_BARS = 5

KINDS = ("support", "resistance", "breakout")
NSE_TICK = 0.05


@dataclass
class Level:
    price: float
    kind: str
    created: str
    note: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")


def snap(price: float) -> float:
    """NSE quotes most equities in 5-paise ticks; a clicked price should respect that."""
    return round(round(price / NSE_TICK) * NSE_TICK, 2)


def load_all() -> dict[str, list[dict]]:
    if not LEVELS_PATH.exists():
        return {}
    return json.loads(LEVELS_PATH.read_text())


def save_all(levels: dict[str, list[dict]]) -> None:
    CLEAN.mkdir(parents=True, exist_ok=True)
    LEVELS_PATH.write_text(json.dumps(levels, indent=2, sort_keys=True))


def add(symbol: str, price: float, kind: str, note: str = "") -> Level:
    level = Level(price=snap(price), kind=kind,
                  created=datetime.now().isoformat(timespec="seconds"), note=note)
    store = load_all()
    store.setdefault(symbol, []).append(asdict(level))
    save_all(store)
    return level


def remove(symbol: str, index: int) -> None:
    store = load_all()
    if symbol in store and 0 <= index < len(store[symbol]):
        store[symbol].pop(index)
        save_all(store)


def touch_events(frame: pd.DataFrame, price: float,
                 tolerance: float = TOUCH_TOLERANCE,
                 separation: int = TOUCH_SEPARATION_BARS) -> list[int]:
    """Bar positions where price came to the level, collapsed into distinct events."""
    band_low, band_high = price * (1 - tolerance), price * (1 + tolerance)
    touching = ((frame["low"] <= band_high) & (frame["high"] >= band_low)).to_numpy()

    events: list[int] = []
    previous_touch = -10**9
    for position, is_touching in enumerate(touching):
        if is_touching:
            if position - previous_touch > separation:
                events.append(position)
            previous_touch = position
    return events


def valid_from(frame: pd.DataFrame, price: float,
               tolerance: float = VALIDATION_TOLERANCE, **kwargs):
    """Date of the second touch -- the earliest moment this line existed as a line."""
    events = touch_events(frame, price, tolerance=tolerance, **kwargs)
    if len(events) < 2:
        return None
    return frame.iloc[events[1]]["ts"]


def pivot_lows(frame: pd.DataFrame, span: int = 5) -> list[int]:
    """Positions of swing lows: a low with `span` higher lows on both sides.

    Used for the breakout stop. Note a pivot is only confirmed `span` bars after it
    happens, which the backtest must respect -- see strategies.py.
    """
    low = frame["low"].to_numpy()
    found = []
    for position in range(span, len(low) - span):
        window = low[position - span: position + span + 1]
        if low[position] == window.min() and (window == low[position]).sum() == 1:
            found.append(position)
    return found

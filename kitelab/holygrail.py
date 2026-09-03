"""The Holy Grail: a pullback to the 20 EMA while ADX says the trend is real.

Linda Raschke's setup, as marked by hand in the class spreadsheet
("Titan ADX Daily", 40 trades, 2012-2026). Her rules, in her words:

    1) ADX indicator value should be more 25 or more than it to take trades
    2) Stock should touch the 20 EMA and go up and simultaneously it should show
       the spike at ADX above 25
    3) Target will be previous swing high
    4) Second target will be trailing stoploss which will be previous swing low
    5) Have to take the uptrends in the chart after spotting ADX value more than
       25 no downtrends
    6) Entry will be the candle's high and SL will be swing low

Two things that rule 5 leaves to the eye and code cannot:

UPTREND. ADX measures how hard price is trending, not which way -- a crash reads
as high as a rally. Direction comes from +DI vs -DI, the two lines drawn on the
same ADX panel, and that is what the class's own DI-crossover note uses. So
"uptrend" here is +DI > -DI.

SWING POINTS ARE CONFIRMED LATE. A pivot needs `span` bars on both sides before
anyone can know it was one. Reading a pivot the moment it forms is reading the
future, so every stop and target here uses only pivots already confirmed by the
entry bar -- which is why a stop can sit further back than the eye would put it.

NOTE THE STOP. Rule 6 says "swing low", and that phrase was read here as a
confirmed 5-bar pivot -- which put the stop a median 5.8% below entry where the
class's own marked stops sit 0.8% below. Five times too wide, so positions came
out a fifth of the size they should be.

Measured against all 39 recorded stops in the sheet, "swing low" means THE
SIGNAL CANDLE'S OWN LOW:

    signal candle's own low          median error 0.80%
    lower of the last two candles                 0.66%
    3-bar pivot                                   1.17%
    5-bar pivot (the first reading)               5.82%

which agrees with the class's standing rule that the stop is always the candle
low, and with the DI-crossover note that says "STOPLOSS: signal candle low". The
two-bar variant fits marginally better and is not offered: a 0.14% edge over 39
trades is noise, and a rule stated three times beats a curve fit.

stop="pivot" keeps the wide reading; it is not the default here, but it IS a
defensible reading of the phrase and the dashboard publishes both. Fitting the
sheet is not the same as trading well, and over the 101 in-sample stocks at
2,00,000 and 1% risk the two produce different trades from identical signals:

    stop           distance   expectancy   win rate   avg win/loss    MAR
    signal_low        4.63%     +0.561R      41.1%    2.95R/-1.11R    0.14
    pivot            13.41%     +0.404R      57.5%    1.31R/-0.82R    0.20

The wide stop banks more often and smaller; the tight one is refused more often
for being too small to place (387 trades against 266) and starves the account of
cash sooner (495 skips against 268). Both lose. Neither reading rescues this
rule, which is why choosing between them is a question about the class's wording
and not about which number to quote.

Trades come out in the shape backtest.simulate produces, so portfolio.run, the
dashboard and the reports take them unchanged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import frames, indicators, levels, sizing, slippage
from .backtest import charges

EMA_LENGTH = 20
ADX_LENGTH = 14
PIVOT_SPAN = 5

# TWO READINGS OF THE SAME SETUP.
#
# "raschke" is the original from Street Smarts, and the default. "class" is the
# spreadsheet's paraphrase, which differs in three ways that all turned out to
# matter -- measured over 40 stocks and 172,307 daily bars:
#
#   ADX floor      30 in the original, 25 in the sheet. 18% of bars against 27%.
#   ADX rising     the original wants the trend STRENGTHENING; the sheet says
#                  "spike", which is the same thing, and the first reading here
#                  implemented neither. Adding it cuts qualifying bars 9,669 -> 986.
#   the touch      the original needs price only to REACH the 20 EMA; the first
#                  reading here also demanded the bar close back above it, which
#                  threw away 46% of the pullbacks Raschke would take. The close
#                  is not the confirmation -- breaking the pullback bar's high is,
#                  and that was already the entry trigger. It was confirming twice.
#
# And the original takes the FIRST pullback after ADX qualifies, not every touch
# for as long as the trend runs. That is why the first reading traded so often
# and so badly.
RULES = {
    "raschke": {"floor": 30.0, "rising": True,  "close_above": False, "first_only": True},
    "class":   {"floor": 25.0, "rising": False, "close_above": True,  "first_only": False},
}
ADX_FLOOR = RULES["raschke"]["floor"]
TARGET_FRACTION = 0.5      # how much comes off at the previous swing high

# How long a setup stays live waiting for its high to break. The rules do not say
# a setup expires, so any number here is an invention; it was originally tied to
# PIVOT_SPAN for no reason beyond both being small. Tuned against the class
# spreadsheet -- see the note at the bottom of this docstring block.
TRIGGER_WINDOW = 10


def _rising_pivots(frame: pd.DataFrame, pivots: list[int], col: str,
                   span: int) -> np.ndarray:
    """At each bar: are the last TWO CONFIRMED pivots rising?

    This is the chart reading of a trend -- higher lows for support, higher highs
    for resistance -- which is what a trend line drawn between two swing points
    encodes. Only pivots already confirmed by the bar are used: a pivot needs
    `span` bars on each side, so one drawn "at" a swing could not have been known
    until later, and using it would be reading the future.
    """
    vals = frame[col].to_numpy()
    out = np.zeros(len(frame), dtype=bool)
    seen: list[int] = []
    j = 0
    for i in range(len(frame)):
        while j < len(pivots) and pivots[j] + span <= i:
            seen.append(pivots[j]); j += 1
        if len(seen) >= 2:
            out[i] = vals[seen[-1]] > vals[seen[-2]]
    return out


def setups(day: pd.DataFrame, rules: str = "raschke",
           trend: str = "di", span: int = PIVOT_SPAN) -> pd.DataFrame:
    """Daily bars plus every column the rules are judged on.

    trend   ADX clears the floor, is rising if the reading asks for it, and
            +DI > -DI so the trend is UP -- ADX alone reads a crash as high as
            a rally.
    touch   price reached the 20 EMA. Under "class" it must also close back
            above it.
    signal  both, and under "raschke" only the FIRST touch after the trend
            qualifies -- Raschke's setup is the first pullback in a strong
            trend, not every pullback while it lasts.
    """
    cfg = RULES[rules]
    out = day.copy().reset_index(drop=True)
    ema = indicators.ema(out["close"], EMA_LENGTH)
    adx, plus_di, minus_di = indicators.adx(out["high"], out["low"], out["close"],
                                            ADX_LENGTH)
    out["ema"] = ema
    out["adx"] = adx
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    strong = adx >= cfg["floor"]
    if cfg["rising"]:
        # NOT adx.diff() > 0. Bar-to-bar direction flickers -- ADX ticks up and
        # down constantly inside a strong trend -- so the condition flips every
        # few bars and "the first pullback after it qualifies" fires on nearly
        # every dip. What Raschke means by a RISING ADX is the crossing: the
        # moment it clears the floor on its way up, which happens rarely and
        # marks one trend episode. Held until ADX drops back below the floor.
        crossed = strong & ~strong.shift(1, fill_value=False)
        episode = crossed.cumsum().where(strong)          # NaN once ADX lapses
        out["episode"] = episode
    # WHAT COUNTS AS AN UPTREND. Rule 5 leaves this to the eye, and the class
    # spreadsheet cannot settle it -- it was marked before the uptrend rule
    # existed, which is why it contains downtrend trades. So the readings are
    # offered and the results decide.
    #
    #   di        +DI > -DI, the two lines on the ADX panel itself
    #   hl        higher lows -- what a rising trend line through swing lows says
    #   hh        higher highs
    #   hhhl      both, the textbook definition of an uptrend
    if trend == "di":
        up = (plus_di > minus_di).to_numpy()
    else:
        hl = _rising_pivots(out, levels.pivot_lows(out, span), "low", span)
        hh = _rising_pivots(out, levels.pivot_highs(out, span), "high", span)
        up = {"hl": hl, "hh": hh, "hhhl": hh & hl}[trend]
    out["trend"] = strong & pd.Series(up, index=out.index)
    touch = out["low"] <= ema
    if cfg["close_above"]:
        touch = touch & (out["close"] > ema)
    out["touch"] = touch
    signal = out["trend"] & out["touch"]

    if cfg["first_only"]:
        # Arm on the RISING EDGE of the trend condition -- the bar where ADX
        # first clears the floor while climbing -- and disarm after the touch it
        # produces. Arming on every trend bar instead re-arms the moment after
        # firing, so a long trend fires on nearly every dip and "first pullback"
        # means nothing: it left 762 trades where the strict reading leaves far
        # fewer. The trend must lapse and re-qualify before the rule looks again.
        # One trade per ADX episode: arm when ADX crosses the floor in an
        # uptrend, fire on the first touch of the EMA after that, then stay quiet
        # until ADX has lapsed below the floor and crossed it again.
        ep = out["episode"].to_numpy()
        # `up`, not a fresh +DI test: this path ignored the chosen trend reading
        # and hardcoded the DI one, so every reading gave byte-identical results
        # and the comparison said nothing.
        up_now = up
        tch = out["touch"].to_numpy()
        keep = np.zeros(len(out), dtype=bool)
        used = set()
        for i in range(len(out)):
            e = ep[i]
            if e != e:                      # NaN: ADX below the floor
                continue
            if tch[i] and up_now[i] and e not in used:
                keep[i] = True
                used.add(e)
        signal = pd.Series(keep, index=out.index)
    out["signal"] = signal
    return out


def _confirmed(pivots: list[int], span: int, before: int) -> int | None:
    """The latest pivot KNOWN by bar `before`. A pivot at i is only confirmed at
    i + span, so anything later than that is not yet visible."""
    usable = [i for i in pivots if i + span <= before]
    return usable[-1] if usable else None


def simulate(symbol: str, stop: str = "signal_low", span: int = PIVOT_SPAN,
             adx_floor: float = ADX_FLOOR, wait: int = TRIGGER_WINDOW,
             rules: str = "raschke", trend: str = "di") -> list[dict]:
    """Closed trades, oldest first.

    rules="raschke"    the original: ADX 30 and rising, first pullback only,
                       and the pullback bar need not close above the line
    rules="class"      the spreadsheet's paraphrase
    stop="signal_low"  the signal candle's low -- the standing rule here
    stop="pivot"       the last confirmed multi-bar swing low, for comparison
    """
    frame = setups(frames.daily(symbol), rules, trend, span)
    if len(frame) < 3 * span + EMA_LENGTH:
        return []
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    open_ = frame["open"].to_numpy(float)
    close = frame["close"].to_numpy(float)
    signal = frame["signal"].to_numpy(bool)
    stamps = frame["ts"].tolist()
    total = len(frame)

    lows = levels.pivot_lows(frame, span)
    highs = levels.pivot_highs(frame, span)

    trades: list[dict] = []
    position = span
    while position < total - 1:
        if not signal[position]:
            position += 1
            continue

        # Rule 6: entry is the signal candle's high, taken when a later bar
        # trades through it. A gap straight over the level fills at the open.
        trigger = high[position]
        entry_index = None
        for step in range(position + 1, min(position + 1 + wait, total)):
            if high[step] >= trigger:
                entry_index = step
                break
        if entry_index is None:                 # the high never broke; setup lapsed
            position += 1
            continue
        entry_price = max(trigger, float(open_[entry_index]))

        low_at = _confirmed(lows, span, entry_index)
        high_at = _confirmed(highs, span, entry_index)
        if high_at is None or (stop == "pivot" and low_at is None):
            position += 1
            continue

        # `position` is the SIGNAL candle; entry_index is the bar its high broke
        # on. The stop belongs to the signal candle, not the fill.
        stop_price = (float(low[position]) if stop == "signal_low"
                      else float(low[low_at]))
        target = float(high[high_at])
        risk = entry_price - stop_price
        # A target already behind us is not a target, and a stop above entry is
        # not a stop. Both happen when the pullback ran past its own structure.
        if risk <= 0 or target <= entry_price:
            position += 1
            continue

        shares, risk_taken, capped = sizing.position(entry_price, stop_price)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = entry_index + 1
            continue

        # Rule 3 then rule 4: bank TARGET_FRACTION at the previous swing high,
        # trail the rest on swing lows as they confirm. The stop is checked first,
        # so a bar that breaks both is charged the worse of the two.
        banked_index = banked_price = None
        trail = stop_price
        exit_at = None
        for step in range(entry_index + 1, total):
            if close[step] <= trail:
                exit_at = (step, float(close[step]),
                           "stop" if banked_index is None else "trailing stop")
                break
            if banked_index is None and high[step] >= target:
                banked_index, banked_price = step, max(target, float(open_[step]))
            if banked_index is not None:
                moved = _confirmed(lows, span, step)
                if moved is not None:
                    trail = max(trail, float(low[moved]))
        if exit_at is None:
            break                                # still open at the end of the data

        exit_index, exit_price, reason = exit_at
        # Half came off at the target; the rest at the trail. One blended exit
        # price keeps the trade in the same shape as every other producer here.
        if banked_index is not None:
            exit_price = (TARGET_FRACTION * banked_price
                          + (1 - TARGET_FRACTION) * exit_price)

        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, stamps[entry_index], quoted_entry, +1)
        exit_price = slippage.fill(symbol, stamps[exit_index], quoted_exit, -1)
        buy_value, sell_value = entry_price * shares, exit_price * shares
        gross = (exit_price - entry_price) * shares
        same_session = stamps[entry_index].date() == stamps[exit_index].date()
        cost = charges(buy_value, sell_value, intraday=same_session)

        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[entry_index], "exit_ts": stamps[exit_index],
            "same_session": same_session, "entry_time": "EOD",
            "level": float(trigger), "level_kind": "20 EMA pullback",
            "max_risk_capital": sizing.risk_budget(),
            "risk_taken": risk_taken, "capital_capped": capped,
            "range": risk, "target": target,
            "final_stop": trail,
            "bars_held": exit_index - entry_index,
            "charges_best": cost, "net_profit_best": gross - cost,
            "entry_date": stamps[entry_index], "entry_price": entry_price,
            "quoted_entry": quoted_entry, "quoted_exit": quoted_exit,
            "spread_cost": ((quoted_exit - exit_price)
                            + (entry_price - quoted_entry)) * shares,
            "stop": stop_price, "risk_per_share": risk,
            "exit_date": stamps[exit_index], "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (stamps[exit_index] - stamps[entry_index]).days,
            "sessions_held": exit_index - entry_index,
            "shares": shares, "cost_of_entry": buy_value,
            "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost, "net_profit": gross - cost,
        })
        position = exit_index + 1

    return trades

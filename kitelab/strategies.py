"""The two level-based strategies, simulated on 30-minute candles.

SUPPORT BOUNCE (your "Support Line Strategy")
    A bar touches a support level. If it is red, look at the next bar. The first green
    bar whose body midpoint sits above the line is the signal. Buy-stop at that bar's
    high, stop-loss at its low, target at 1.5R. If price closes decisively below the
    line while waiting, the support has failed and the setup is abandoned.

BREAKOUT (your "Breakout Stratergy")
    A resistance level that has already been touched twice is a confirmed level. The
    first 30-minute close above it is the break. Buy-stop at that bar's high, target at
    1.5R, stop at the last daily pivot low that was ALREADY CONFIRMED at entry time --
    a 5-bar pivot is not visible until 5 bars after it forms, and using it earlier
    would be reading a swing low that had not happened yet.

Both obey each level's valid_from date, so no signal is taken before the level's second
touch established it as a line.

Conservative conventions, all of which cost the strategy rather than flatter it:
    * stop and target inside the same bar  -> assume the stop
    * a gap through the stop               -> fill at the open, not the stop
    * a gap through the target             -> fill at the open (this one helps)
    * one position at a time per symbol    -> overlapping signals are dropped
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import frames, indicators, levels, sizing, trailing, slippage
from .backtest import charges

TIMEFRAME = "30m"
MAX_RISK = 500.0        # your "Max Risk Capital" column
REWARD_RATIO = 1.5      # your "Desired R:R"
CONFIRM_WINDOW = 6      # bars after a touch to find the confirming green candle
ENTRY_WINDOW = 4        # bars for the buy-stop to trigger before the setup goes stale
# How long to give a trade before abandoning it. These differ by strategy because the
# stops differ by an order of magnitude: a support bounce risks ~0.8% and resolves in
# hours, while a breakout risks 8-15% off a daily pivot, putting its 1.5R target 12-22%
# away. A single shared cap starved the breakout -- 55% of its trades timed out flat.
SUPPORT_HOLD_BARS = 130    # ~10 sessions on 30m
BREAKOUT_HOLD_BARS = 780   # ~60 sessions, room for a multi-month move to play out
PIVOT_SPAN = 5          # daily bars either side, for the breakout stop
BREAK_FAIL_MULTIPLE = 2 # closes below level*(1 - 2*tolerance) abandon a support setup


def _body_midpoint(bar) -> float:
    return (bar.open + bar.close) / 2


def _resolve_exit(bars: pd.DataFrame, entry_pos: int, stop: float, target: float,
                  max_hold: int):
    """Walk forward from the entry bar. Returns (position, price, reason)."""
    open_ = bars["open"].to_numpy()
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    limit = min(len(bars), entry_pos + max_hold + 1)

    # The entry bar itself is included: a buy-stop can trigger and then be stopped out
    # within the same 30 minutes.
    for position in range(entry_pos, limit):
        hit_stop = low[position] <= stop
        hit_target = high[position] >= target
        if hit_stop and hit_target:
            return position, stop, "stop (target same bar)"
        if hit_stop:
            gapped = open_[position] < stop
            return position, (open_[position] if gapped else stop), (
                "gap through stop" if gapped else "stop")
        if hit_target:
            gapped = open_[position] > target
            return position, (open_[position] if gapped else target), (
                "gap through target" if gapped else "target")
    if limit - 1 > entry_pos:
        return limit - 1, close[limit - 1], "time exit"
    return None


def _trigger_entry(bars: pd.DataFrame, signal_pos: int, trigger_price: float):
    """Buy-stop above the signal bar's high. Returns (position, fill) or None."""
    open_ = bars["open"].to_numpy()
    high = bars["high"].to_numpy()
    for position in range(signal_pos + 1, min(len(bars), signal_pos + 1 + ENTRY_WINDOW)):
        if high[position] >= trigger_price:
            # Gapping above the trigger means you pay the open, not your limit.
            return position, max(trigger_price, float(open_[position]))
    return None


def _build_trade(symbol, bars, level_price, level_kind, signal_pos,
                 entry_pos, entry_price, stop, max_hold: int,
                 trail_step=None, scale_out: str | None = None,
                 exit_signal=None, exit_reason: str = "exit signal",
                 scale_r: float = 1.0) -> dict | None:
    risk = entry_price - stop
    if risk <= 0:
        return None
    shares, risk_taken, capped = sizing.position(entry_price, stop)
    if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
        return None  # position not viable within the risk budget / capital
    banked_fraction, banked_price = 0.0, 0.0
    if trail_step is not None or exit_signal is not None:
        # Swing-low trail and/or a close-based exit signal: no target, the trade ends
        # when the stop is hit or the signal fires, whichever comes first.
        target = None
        (exit_pos, exit_price, reason, final_stop,
         banked_fraction, banked_price) = trailing.resolve(
            bars, entry_pos, stop, trail_step, entry_price, scale_out,
            exit_signal, exit_reason, scale_r)
    else:
        target = entry_price + REWARD_RATIO * risk
        resolved = _resolve_exit(bars, entry_pos, stop, target, max_hold)
        if resolved is None:
            return None
        exit_pos, exit_price, reason = resolved
        final_stop = stop

    entry_ts, exit_ts = bars.iloc[entry_pos]["ts"], bars.iloc[exit_pos]["ts"]
    # Sized on the quoted price above, filled at the price the book actually gives.
    # The banked half is priced at the exit timestamp rather than its own bar --
    # trailing.resolve does not return that index, and the liquidity profile moves
    # slowly enough that the difference is immaterial on a leg that is off by default.
    quoted_entry, quoted_exit = entry_price, exit_price
    entry_price = slippage.fill(symbol, entry_ts, quoted_entry, +1)
    exit_price = slippage.fill(symbol, exit_ts, quoted_exit, -1)
    if banked_fraction:
        banked_price = slippage.fill(symbol, exit_ts, banked_price, -1)
    buy_value = entry_price * shares
    banked_shares = shares * banked_fraction
    remaining = shares - banked_shares
    sell_value = banked_price * banked_shares + exit_price * remaining
    gross = ((banked_price - entry_price) * banked_shares
             + (exit_price - entry_price) * remaining)
    if banked_fraction:
        reason = reason + f" (half banked at {scale_r:g}R)"
    # Whether a same-day round trip is billed at intraday or delivery rates depends on
    # how the broker classifies it, so carry both and let the report show the range.
    same_session = entry_ts.date() == exit_ts.date()
    cost = charges(buy_value, sell_value)
    cost_best = charges(buy_value, sell_value, intraday=same_session)

    return {
        "symbol": symbol,
        "level": level_price,
        "level_kind": level_kind,
        "signal_time": bars.iloc[signal_pos]["ts"],
        "entry_ts": entry_ts,
        "entry_date": entry_ts.date(),
        "entry_time": entry_ts.strftime("%H:%M"),
        "max_risk_capital": sizing.risk_budget(),
        "risk_taken": risk_taken,
        "capital_capped": capped,
        "entry_price": entry_price,
        "quoted_entry": quoted_entry,
        "quoted_exit": quoted_exit,
        "stop": stop,
        "range": risk,
        "reward_ratio": REWARD_RATIO if target is not None else None,
        "target": target,
        "final_stop": final_stop,
        "risk_per_share": risk,
        "shares": shares,
        "cost_of_entry": buy_value,
        "exit_ts": exit_ts,
        "exit_date": exit_ts.date(),
        "exit_price": exit_price,
        "exit_reason": reason,
        "bars_held": exit_pos - entry_pos,
        "gross_profit": gross,
        "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
        "charges": cost,
        "net_profit": gross - cost,
        "same_session": same_session,
        "charges_best": cost_best,
        "net_profit_best": gross - cost_best,
    }


def _active_levels(symbol: str, kind: str | None, daily: pd.DataFrame) -> list[tuple]:
    """Levels with a valid_from, de-duplicated by price. kind=None means every kind.

    Price is the dedupe key rather than (price, kind), because a level marked both
    support and resistance -- a flip level -- is one line, not two.
    """
    seen, out = set(), []
    for raw in levels.load_all().get(symbol, []):
        if kind is not None and raw["kind"] != kind:
            continue
        if raw["price"] in seen:
            continue
        established = levels.valid_from(daily, raw["price"])
        if established is None:
            continue
        seen.add(raw["price"])
        out.append((raw["price"], raw["kind"], established))
    return out


def support_bounce_trades(symbol: str, trailing_stops: bool = False) -> list[dict]:
    bars = frames.load(symbol, TIMEFRAME)
    daily = frames.load(symbol, "1d")
    trades: list[dict] = []

    # "check when stock touches support/resistance line" -- both kinds. Price can
    # reverse up off an old resistance acting as support just as readily.
    for price, kind, established in _active_levels(symbol, None, daily):
        active = bars[bars["ts"] > established].reset_index(drop=True)
        if active.empty:
            continue
        failed_below = price * (1 - BREAK_FAIL_MULTIPLE * levels.TOUCH_TOLERANCE)
        # 30-minute swing lows: this strategy's initial stop is a 30m candle low and
        # its trades resolve in days, so a daily pivot would never move the stop.
        make_trail = (trailing.intraday_trail(active, trailing.pivot_lows(active))
                      if trailing_stops else None)

        for touch in levels.touch_events(active, price):
            signal_pos = None
            for step in range(touch, min(len(active), touch + CONFIRM_WINDOW + 1)):
                bar = active.iloc[step]
                if bar.close < failed_below:
                    break  # support gave way; this setup is dead
                if bar.close > bar.open and _body_midpoint(bar) > price:
                    signal_pos = step
                    break
            if signal_pos is None:
                continue

            signal = active.iloc[signal_pos]
            triggered = _trigger_entry(active, signal_pos, float(signal.high))
            if triggered is None:
                continue
            entry_pos, entry_price = triggered
            trade = _build_trade(symbol, active, price, kind, signal_pos,
                                 entry_pos, entry_price, float(signal.low),
                                 SUPPORT_HOLD_BARS,
                                 make_trail() if make_trail else None)
            if trade:
                trades.append(trade)
    return trades


def breakout_trades(symbol: str, trailing_stops: bool = False) -> list[dict]:
    bars = frames.load(symbol, TIMEFRAME).copy()
    # "breaks the highest price it has reached" -- the running maximum BEFORE each bar,
    # over the stock's whole recorded history, not just since the level was drawn.
    bars["prior_ath"] = bars["high"].cummax().shift(1)
    daily = frames.load(symbol, "1d").reset_index(drop=True)
    pivots = levels.pivot_lows(daily, PIVOT_SPAN)
    daily_dates = daily["ts"].dt.normalize().to_numpy()
    trades: list[dict] = []

    def confirmed_pivot_low(entry_ts, entry_price: float):
        """Most recent daily pivot low already confirmed before this entry's session."""
        session = pd.Timestamp(entry_ts).normalize().to_numpy()
        today = int((daily_dates <= session).sum()) - 1
        for position in reversed(pivots):
            # A span-bar pivot is only visible span bars later; require it to have been
            # confirmed by the close of the PREVIOUS session, never the current one.
            if position + PIVOT_SPAN <= today - 1 and daily.iloc[position]["low"] < entry_price:
                return float(daily.iloc[position]["low"])
        return None

    for price, _kind, established in _active_levels(symbol, "resistance", daily):
        active = bars[bars["ts"] > established].reset_index(drop=True)
        if len(active) < 2:
            continue
        close = active["close"].to_numpy()
        prior_ath = active["prior_ath"].to_numpy()
        # Daily swing lows: matches this strategy's daily pivot stop and its long holds.
        make_trail = (trailing.daily_trail(daily, pivots, active["ts"])
                      if trailing_stops else None)

        for position in range(1, len(active)):
            if not (close[position] > price and close[position - 1] <= price):
                continue  # only the bar that first closes above the level
            if not close[position] > prior_ath[position]:
                # Crossing an old line on the way back down through a decline is not a
                # breakout. The close has to reach ground price has never traded above.
                continue
            signal = active.iloc[position]
            triggered = _trigger_entry(active, position, float(signal.high))
            if triggered is None:
                continue
            entry_pos, entry_price = triggered
            stop = confirmed_pivot_low(active.iloc[entry_pos]["ts"], entry_price)
            if stop is None:
                continue
            trade = _build_trade(symbol, active, price, "resistance", position,
                                 entry_pos, entry_price, stop, BREAKOUT_HOLD_BARS,
                                 make_trail() if make_trail else None)
            if trade:
                trades.append(trade)
    return trades


def drop_overlaps(trades: list[dict]) -> list[dict]:
    """Keep one position at a time per symbol; a signal during an open trade is skipped."""
    kept: list[dict] = []
    open_until: dict[str, pd.Timestamp] = {}
    for trade in sorted(trades, key=lambda t: t["entry_ts"]):
        busy = open_until.get(trade["symbol"])
        if busy is not None and trade["entry_ts"] <= busy:
            continue
        kept.append(trade)
        open_until[trade["symbol"]] = trade["exit_ts"]
    return kept


# --------------------------------------------------------------------------
# Automatic all-time-high breakout
# --------------------------------------------------------------------------
# Your rule -- "breaks the highest price it has reached, for a second time" --
# needs no hand-drawn line, because an all-time high is a computed number. That is
# what lets this strategy scale to any universe while the support/resistance one
# cannot.
#
# A level arms when price pulls back below a peak; it is broken when price closes
# back above it. Without the pullback requirement, every bar of a rising trend would
# count as a fresh breakout.
ATH_PULLBACK = 0.03


def ath_levels(daily: pd.DataFrame, pullback: float = ATH_PULLBACK) -> list[tuple]:
    """Peaks that price has since pulled back from: (level, date it became tradeable)."""
    high = daily["high"].to_numpy()
    close = daily["close"].to_numpy()
    stamps = daily["ts"].tolist()

    peak, armed, out = float("-inf"), None, []
    for position in range(len(daily)):
        if high[position] > peak:
            peak, armed = float(high[position]), None   # new ground, nothing to break
        elif armed is None and close[position] < peak * (1 - pullback):
            armed = peak
            out.append((peak, stamps[position]))
    return out


def ema_exit_signal(daily: pd.DataFrame, stamps, length: int = 20):
    """True on the last bar of any session whose DAILY close finished below its EMA.

    The rule is a daily-close rule, but breakout entries run on 30-minute bars, so it
    has to be evaluated somewhere in the intraday frame. The honest place is the last
    bar of the session: its close IS the daily close, so acting on it uses nothing you
    would not have known at the bell. Flagging an earlier bar would let the trade react
    to a daily close that had not happened yet.

    Works unchanged when the frame IS daily -- every bar is then its own session end.
    """
    ema = indicators.ema(daily["close"], length).to_numpy()
    below = daily["close"].to_numpy() < ema
    sessions = daily["ts"].dt.normalize().to_numpy()
    bar_sessions = pd.DatetimeIndex(stamps).normalize().to_numpy()
    position = np.searchsorted(sessions, bar_sessions, side="right") - 1
    last_of_session = np.append(bar_sessions[1:] != bar_sessions[:-1], True)
    return last_of_session & (position >= 0) & below[np.maximum(position, 0)]


def ath_breakout_trades(symbol: str, trailing_stops: bool = True,
                        pullback: float = ATH_PULLBACK,
                        timeframe: str = TIMEFRAME,
                        scale_out: str | None = None,
                        exit_rule: str = "trail",
                        scale_r: float = 1.0) -> list[dict]:
    """Breakouts to new all-time highs, detected automatically.

    timeframe="1d" runs entries on daily bars instead of 30-minute ones. That is a
    DIFFERENT strategy, not the same one measured differently -- the entry fills at a
    coarser price. It exists only because Kite serves no intraday data before 2015, so
    it is the only way to look at 2008 at all.

    exit_rule picks how the trade ends. The pivot-low stop is always live underneath:
        "trail"        ratcheting daily swing lows -- the original rule
        "ema20"        out on the first daily close below the 20-EMA, stop never moves
        "trail+ema20"  both; whichever comes first
    """
    if exit_rule not in ("trail", "ema20", "trail+ema20"):
        raise ValueError(f"unknown exit_rule {exit_rule!r}")
    bars = frames.load(symbol, timeframe)
    daily = frames.load(symbol, "1d").reset_index(drop=True)
    pivots = levels.pivot_lows(daily, PIVOT_SPAN)
    daily_dates = daily["ts"].dt.normalize().to_numpy()

    def confirmed_pivot_low(entry_ts, entry_price: float):
        session = pd.Timestamp(entry_ts).normalize().to_numpy()
        today = int((daily_dates <= session).sum()) - 1
        for position in reversed(pivots):
            if position + PIVOT_SPAN <= today - 1 and daily.iloc[position]["low"] < entry_price:
                return float(daily.iloc[position]["low"])
        return None

    trades: list[dict] = []
    intraday_start = bars["ts"].min() if len(bars) else None
    daily_close = daily["close"].to_numpy()
    daily_ts = daily["ts"].to_numpy()
    for price, armed_on in ath_levels(daily, pullback):
        # Daily history reaches back to 2006 but intraday only to 2015. A level armed
        # pre-2015 whose FIRST daily break also happened pre-2015 is already dead --
        # scanning post-2015 intraday for "the first crossing" would fire on some later
        # incidental cross (often a bounce inside a decline), which is exactly the
        # not-a-breakout error this strategy exists to avoid. Skip such levels.
        after = (daily_ts > armed_on.to_numpy()) & (daily_close > price)
        if after.any():
            first_daily_break = daily_ts[after.argmax()]
            if intraday_start is not None and first_daily_break < intraday_start.to_numpy():
                continue
        active = bars[bars["ts"] > armed_on].reset_index(drop=True)
        if len(active) < 2:
            continue
        close = active["close"].to_numpy()
        use_trail = trailing_stops and exit_rule in ("trail", "trail+ema20")
        make_trail = (trailing.daily_trail(daily, pivots, active["ts"])
                      if use_trail else None)
        below_ema = (ema_exit_signal(daily, active["ts"])
                     if exit_rule in ("ema20", "trail+ema20") else None)

        for position in range(1, len(active)):
            if not (close[position] > price and close[position - 1] <= price):
                continue
            signal = active.iloc[position]
            triggered = _trigger_entry(active, position, float(signal.high))
            if triggered is None:
                break
            entry_pos, entry_price = triggered
            stop = confirmed_pivot_low(active.iloc[entry_pos]["ts"], entry_price)
            if stop is None:
                break
            trade = _build_trade(symbol, active, price, "all-time high", position,
                                 entry_pos, entry_price, stop, BREAKOUT_HOLD_BARS,
                                 make_trail() if make_trail else None, scale_out,
                                 below_ema, "below 20 EMA", scale_r)
            if trade:
                trades.append(trade)
            break   # one trade per armed level; the next needs a fresh pullback
    return trades

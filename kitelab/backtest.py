"""EMA-stack strategy, simulated bar by bar on the stored data.

Rules, as written in your spreadsheet plus the stop you specified:

    entry   close is above the 20-EMA on the monthly, weekly AND daily timeframes,
            on the day that becomes true. Filled at that day's close ("buy on eod").
    stop    the low of the entry day.
    exit    whichever comes first --
              * a later day CLOSES at or below the stop -> filled at that close.
                This is stop_on_close=True, the class convention and the
                default (see simulate). Nothing inside the bar matters, so a
                bar whose LOW pierced the stop but whose close recovered does
                not exit. Measured 2026-09-03 with scripts.stops: about 10% of
                winning trades did exactly that, and a trader holding a resting
                stop order would not have had them. Pass stop_on_close=False
                for the broker convention, where a touch fills at the stop and
                a gap through it fills at the open.
              * the EMA stack breaks                     -> filled at that day's close
    re-entry only on a fresh signal. After an exit the stack must go false and turn
            true again; being stopped out while still above the EMAs is not a re-entry.

Point-in-time correctness
-------------------------
The weekly and monthly values on any given day are those of the *forming* bar, not the
finished one. A trader on a Wednesday sees a weekly candle built from Monday to
Wednesday; using the completed Friday-to-Friday bar would be lookahead.

Because ewm(adjust=False) is recursive, the forming bar's EMA is one step from the last
completed bar:

    ema_asof(D) = alpha * close(D) + (1 - alpha) * ema(last completed higher-TF bar)

which is exact and costs O(1) per day rather than rebuilding the weekly frame 2,900
times. `verify_against_screener` checks this against screener.context, which does
rebuild it, so the shortcut is proven rather than assumed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import frames, indicators, screener, sizing, slippage

EMA_LENGTH = 20
SHARES = 10

# Where a decision taken at a close actually gets filled.
#
# False (default, and every result before 2026-08-31): the fill happens AT the close
# that produced the decision. That is a mild lookahead -- you cannot trade a print
# you are still waiting to see -- but it is what the class does on paper, and it is
# what every stored number assumes.
#
# True: you read the close after the bell and act at the NEXT session's open, which
# is what an end-of-day trader who cannot watch the screen actually does. Entry, stop
# exit and EMA-break exit all move one session later; the stop LEVEL is unchanged,
# because that is the line drawn on the chart. This is the honest execution model and
# it is measured, not assumed -- the opens are in our data.
NEXT_OPEN_FILLS = False

# Hysteresis: enter only when price is BAND above every EMA, exit only when it is BAND
# below one of them. The gap between those two lines is a dead zone where nothing
# happens. Without it, entry and exit share a single knife-edge, so price hovering
# around an EMA triggers an exit and a re-entry every few days -- the median holding
# period was 3 sessions on a strategy filtered by MONTHLY EMAs, and 20% of trades
# re-entered the same stock the very next day.
#
# SET TO ZERO 2026-09-05, so this default now buys none of that. The band was
# removed from every EMA family on the board (kitelab.registry.BANDS carries the
# decision), and a module default of 0.02 would have meant `simulate("HAL")` from
# a REPL silently trading a different rule than the same strategy's published row
# -- the kind of gap between the code and the board this project has already been
# burned by. The paragraph above is kept as the record of what the buffer bought,
# because that churn is what comes back.
BAND = 0.0

# Zerodha equity DELIVERY charges, from zerodha.com/charges (checked 2026-08-23).
# These trades hold overnight, so delivery rates apply, not intraday.
BROKERAGE = 0.0
STT_RATE = 0.001           # 0.1% on buy and on sell

# STT HAS NOT ALWAYS BEEN 0.1%, AND THIS BACKTEST RUNS FROM 2006.
#
# Applying one rate across twenty years understates cost in whichever years the
# rate was higher. The schedule below is the mechanism for fixing that: entries
# are (effective_from, rate), newest last, and stt_rate_on() picks the one in
# force. It currently holds a single entry, so behaviour is unchanged.
#
# WHAT NEEDS CHECKING BEFORE ADDING TO IT. Delivery-equity STT is believed to
# have been 0.125% per side before falling to 0.100% around June 2013, but that
# is from recollection, not from a source, and it is not worth changing every
# published number on a half-memory. (The rate changes widely reported for
# 1 October 2024 were on FUTURES AND OPTIONS, which this project does not trade.)
#
# The cost of getting it wrong is bounded and known: 30% of the class stack's
# trades close before June 2013, on Rs4.4 crore of turnover, so the extra
# 0.025% per side would come to about Rs10,900 -- 1.2% of gross profit, against
# an all-in cost load of 12.3%. Worth correcting, not worth guessing.
#
# To enable, verify the rate and date, then add: (pd.Timestamp("2013-06-01"), 0.001)
# after an earlier (pd.Timestamp("2004-10-01"), 0.00125) entry.
STT_SCHEDULE: list[tuple] = [(pd.Timestamp("1900-01-01"), STT_RATE)]


def stt_rate_on(stamp) -> float:
    """The delivery STT rate in force on a given date."""
    when = pd.Timestamp(stamp)
    rate = STT_SCHEDULE[0][1]
    for effective_from, value in STT_SCHEDULE:
        if when >= effective_from:
            rate = value
    return rate
NSE_TXN_RATE = 0.0000307   # 0.00307%
SEBI_RATE = 0.000001       # Rs 10 per crore
GST_RATE = 0.18            # on brokerage + SEBI + transaction charges
STAMP_RATE = 0.00015       # 0.015%, buy side only
DP_PER_SELL = 15.34        # per scrip, on the sell


# Equity INTRADAY rates, from zerodha.com/charges (checked 2026-08-23). These apply
# when a position is opened and closed in the same session.
INTRADAY_BROKERAGE_RATE = 0.0003   # 0.03% per order...
INTRADAY_BROKERAGE_CAP = 20.0      # ...capped at Rs20 per executed order
INTRADAY_STT_RATE = 0.00025        # 0.025%, SELL side only
INTRADAY_STAMP_RATE = 0.00003      # 0.003%, buy side only
# and no DP charge, because nothing reaches the demat account.


# When set, replaces the whole Zerodha equity model with a flat rate on turnover.
# Used for the class assets: crypto exchange fees, index futures, MCX -- none of
# which pay equity STT or demat charges. None = normal Zerodha equity model.
FLAT_FEE_RATE: float | None = None


def charges(buy_value: float, sell_value: float, intraday: bool = False,
            fee_rate: float | None = None) -> float:
    """Round-trip cost. Intraday is roughly half of delivery, mostly because STT drops
    from 0.1% on both sides to 0.025% on the sell side alone.

    `fee_rate` overrides the equity schedule for ONE call, which is what lets a
    single account hold instruments with different cost models -- Bitcoin at an
    exchange fee, MCX futures at another, NSE equities on the Zerodha schedule.
    It was a module global until 2026-09-03, so the whole grid had to run under
    one setting and non-equity instruments needed a separate code path with
    their own hardcoded account. The global still works and still means "every
    call in this process", which is how the fetch-side scripts use it.
    """
    rate = FLAT_FEE_RATE if fee_rate is None else fee_rate
    if rate is not None:
        return rate * (buy_value + sell_value)
    turnover = buy_value + sell_value
    transaction = NSE_TXN_RATE * turnover
    sebi = SEBI_RATE * turnover

    if intraday:
        brokerage = (min(INTRADAY_BROKERAGE_RATE * buy_value, INTRADAY_BROKERAGE_CAP)
                     + min(INTRADAY_BROKERAGE_RATE * sell_value, INTRADAY_BROKERAGE_CAP))
        stt = INTRADAY_STT_RATE * sell_value
        stamp = INTRADAY_STAMP_RATE * buy_value
        demat = 0.0
    else:
        brokerage = BROKERAGE
        stt = STT_RATE * turnover
        stamp = STAMP_RATE * buy_value
        demat = DP_PER_SELL

    gst = GST_RATE * (brokerage + sebi + transaction)
    return brokerage + stt + transaction + sebi + gst + stamp + demat


# How much below its own all-time high a stock may be and still be bought. The
# class idea: a 20 EMA cross means more in a name already making highs than in
# one recovering from a fall. None = no restriction, which is the original rule.
ATH_BAND = 0.10


def ema_stack_signal(symbol: str, length: int = EMA_LENGTH,
                     band: float = BAND, stack: str = "mwd",
                     ath_band: float | None = None) -> pd.DataFrame:
    """Daily bars plus the point-in-time EMA-stack condition.

    stack="mwd"    monthly and weekly must agree with daily (the class rule)
    stack="daily"  the daily 20 EMA alone -- the control that says what the two
                   higher timeframes are actually worth
    ath_band       when set, only bars within this fraction of the running
                   all-time high can trigger an entry. The high is a running max
                   INCLUDING today, which is knowable at the close; exits are
                   left alone, because a rule that refuses to sell what it has
                   already bought is not a filter, it is a trap.
    """
    day = frames.daily(symbol).reset_index(drop=True)
    week = frames.weekly(day)
    month = frames.monthly(day)
    alpha = 2.0 / (length + 1)

    close = day["close"].to_numpy()
    daily_ema = indicators.ema(day["close"], length).to_numpy()
    week_ema = indicators.ema(week["close"], length).to_numpy()
    month_ema = indicators.ema(month["close"], length).to_numpy()

    stamps = day["ts"].to_numpy()
    week_pos = np.searchsorted(week["ts"].to_numpy(), stamps, side="right") - 1
    month_pos = np.searchsorted(month["ts"].to_numpy(), stamps, side="right") - 1

    def forming(positions: np.ndarray, higher_ema: np.ndarray) -> np.ndarray:
        previous = np.where(positions >= 1, higher_ema[np.maximum(positions - 1, 0)], np.nan)
        # In the very first higher-TF bar there is nothing to recurse from, and
        # ewm(adjust=False) seeds itself with the first value -- so the EMA equals the
        # close, and "close > ema" is correctly false.
        return np.where(np.isnan(previous), close, alpha * close + (1 - alpha) * previous)

    week_asof = forming(week_pos, week_ema)
    month_asof = forming(month_pos, month_ema)

    out = day.copy()
    out["daily_ema"] = daily_ema
    out["weekly_ema"] = week_asof
    out["monthly_ema"] = month_asof
    upper, lower = 1 + band, 1 - band
    if stack == "daily":
        # The control for "what do the higher timeframes buy us?". One line in,
        # one line out, nothing above it consulted.
        out["in_stack"] = close > daily_ema
        out["entry_ok"] = close > daily_ema * upper
        out["exit_ok"] = close < daily_ema * lower
    elif stack == "mwd":
        out["in_stack"] = (close > daily_ema) & (close > week_asof) & (close > month_asof)
        # Entry needs ALL three clear of the upper line; exit needs only ONE below
        # the lower line, mirroring the original "any EMA broken" rule.
        out["entry_ok"] = ((close > daily_ema * upper) & (close > week_asof * upper)
                           & (close > month_asof * upper))
        out["exit_ok"] = ((close < daily_ema * lower) | (close < week_asof * lower)
                          | (close < month_asof * lower))
    else:
        raise ValueError(f"unknown stack: {stack!r}")

    if ath_band is not None:
        # Running all-time high of the CLOSE, today included. Buying only within
        # a band of it is a strength filter, not a lookahead: the high so far is
        # known at the close, unlike the high that is still to come.
        peak = day["close"].cummax().to_numpy()
        out["near_ath"] = close >= peak * (1 - ath_band)
        out["entry_ok"] = out["entry_ok"] & out["near_ath"]
    # How many COMPLETED higher-timeframe bars existed. An EMA-20 resting on 3 monthly
    # bars is what TradingView draws, but it is not worth much -- surfaced, not hidden.
    out["weeks_done"] = week_pos
    out["months_done"] = month_pos
    return out


def simulate(symbol: str, length: int = EMA_LENGTH, shares: int = SHARES,
             band: float = BAND, scale_out: str | None = None,
             stop_on_close: bool = True, scale_r: float = 1.0,
             stack: str = "mwd", ath_band: float | None = None) -> list[dict]:
    """Walk the signal series and produce closed trades, oldest first.

    stop_on_close=True is the CLASS convention (2026-08-28): everything --
    the stop included -- is evaluated only at bar closes, because the class
    backtests manually on end-of-bar data. A close at or below the stop sells
    at that close; nothing that happens inside the bar matters.
    stop_on_close=False is the broker convention used by all results before
    2026-08-28: the stop is a live intrabar order (a touch fills at the stop,
    a gap through it fills at the open).
    """
    if NEXT_OPEN_FILLS and scale_out:
        raise ValueError("scale_out under NEXT_OPEN_FILLS is not modelled: the banked "
                         "leg would need its own next-open fill. Run them separately.")
    if NEXT_OPEN_FILLS and not stop_on_close:
        raise ValueError("NEXT_OPEN_FILLS assumes decisions are taken at closes; it is "
                         "meaningless with a live intrabar stop (stop_on_close=False).")
    signal = ema_stack_signal(symbol, length, band, stack, ath_band)
    entry_ok = signal["entry_ok"].to_numpy()
    exit_ok = signal["exit_ok"].to_numpy()
    open_, high, low, close = (signal[c].to_numpy() for c in ("open", "high", "low", "close"))
    stamps = signal["ts"].tolist()
    total = len(signal)

    trades: list[dict] = []
    position = 0
    while position < total:
        fresh_signal = entry_ok[position] and position > 0 and not entry_ok[position - 1]
        if not fresh_signal:
            position += 1
            continue

        stop = float(low[position])
        if NEXT_OPEN_FILLS:
            if position + 1 >= total:
                break                       # signal on the last bar; nothing to fill at
            entry_index = position + 1
            entry_price = float(open_[entry_index])
            if entry_price <= stop:
                # It opened below the line you were going to defend. You would not
                # buy into that, so the signal is skipped rather than filled.
                position += 1
                continue
        else:
            entry_index = position
            entry_price = float(close[position])
        exit_at = None

        # scale_out: sell half at entry + scale_r x R ("half"), optionally moving the
        # stop on the remainder to breakeven ("half_be"). R is the risk taken, entry
        # minus stop, so scale_r = 1 banks once the trade has made what it risked.
        # Same-bar ambiguity goes to the stop.
        risk0 = entry_price - stop
        trigger = entry_price + scale_r * risk0 if scale_out and risk0 > 0 else None
        current_stop = stop
        banked_fraction = banked_price = 0.0
        banked_index = None
        for step in range(entry_index if NEXT_OPEN_FILLS else position + 1, total):
            if stop_on_close:
                if close[step] <= current_stop:
                    exit_at = (step, float(close[step]), "stop (close)")
                    break
            elif low[step] <= current_stop:
                gapped = open_[step] < current_stop
                exit_at = (step, float(open_[step]) if gapped else current_stop,
                           "gap through stop" if gapped else "stop")
                break
            if trigger is not None and banked_fraction == 0.0:
                banked = (close[step] >= trigger if stop_on_close
                          else high[step] >= trigger)
                if banked:
                    banked_price = (float(close[step]) if stop_on_close
                                    else max(trigger, float(open_[step])))
                    banked_fraction = 0.5
                    banked_index = step
                    if scale_out == "half_be":
                        current_stop = max(current_stop, entry_price)
            if exit_ok[step]:
                exit_at = (step, float(close[step]), "ema break")
                break

        if exit_at is None:
            break  # still open at the end of the data; not a closed trade

        exit_index, exit_price, reason = exit_at
        if NEXT_OPEN_FILLS:
            if exit_index + 1 >= total:
                break                       # exit signalled on the last bar, unfillable
            exit_index += 1
            exit_price = float(open_[exit_index])
        risk = entry_price - stop
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue
        # Size on the quoted price -- you place the order before you know the fill --
        # then pay the spread on top. Your own market impact is NOT here; it depends
        # on the real position size, which only the account engine knows. entry_price/exit_price below
        # are the FILLED prices, because that is the cash that actually moves; the
        # untouched screen prices are kept alongside as quoted_*.
        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, stamps[entry_index], quoted_entry, +1)
        exit_price = slippage.fill(symbol, stamps[exit_index], quoted_exit, -1)
        if banked_fraction:
            banked_price = slippage.fill(symbol, stamps[banked_index], banked_price, -1)
        buy_value = entry_price * shares
        banked_shares = shares * banked_fraction
        remaining = shares - banked_shares
        sell_value = banked_price * banked_shares + exit_price * remaining
        gross = ((banked_price - entry_price) * banked_shares
                 + (exit_price - entry_price) * remaining)
        # Spread only -- impact is charged by the account engine, which is the only
        # place that knows the real order size.
        spread_cost = ((quoted_exit - exit_price) * remaining
                       + (entry_price - quoted_entry) * shares)
        if banked_fraction:
            reason = reason + f" (half banked at {scale_r:g}R)"
        same_session = stamps[entry_index].date() == stamps[exit_index].date()
        # ONE fee convention, everywhere: a trade that opens and closes in the same
        # session is billed at intraday rates, which is what Zerodha actually
        # charges and what portfolio.run has always done. It used to be billed
        # delivery here and intraday only into the *_best fields, so the W/D/H
        # trade lists (the only ones with same-session trades) disagreed with the
        # account engine, and with themselves across the fills toggle.
        cost = cost_best = charges(buy_value, sell_value, intraday=same_session)

        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[entry_index],
            "exit_ts": stamps[exit_index],
            "same_session": same_session,
            "entry_time": "EOD",
            "level": None,
            "level_kind": "ema stack",
            "max_risk_capital": sizing.risk_budget(),
            "risk_taken": risk_taken,
            "capital_capped": capped,
            "range": risk,
            "target": None,
            "final_stop": stop,
            "bars_held": exit_index - entry_index,
            "charges_best": cost_best,
            "net_profit_best": gross - cost_best,
            "entry_date": stamps[entry_index],
            "entry_price": entry_price,
            "quoted_entry": quoted_entry,
            "quoted_exit": quoted_exit,
            "spread_cost": spread_cost,
            "stop": stop,
            "risk_per_share": risk,
            "exit_date": stamps[exit_index],
            "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (stamps[exit_index] - stamps[entry_index]).days,
            "sessions_held": exit_index - entry_index,
            "shares": shares,
            "cost_of_entry": buy_value,
            "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost,
            "net_profit": gross - cost,
            "months_done": int(signal["months_done"].iloc[position]),
        })
        position = exit_index + 1

    return trades


def summarise(symbol: str, trades: list[dict]) -> dict:
    if not trades:
        return {"symbol": symbol, "trades": 0}
    gross = np.array([t["gross_profit"] for t in trades])
    net = np.array([t["net_profit"] for t in trades])
    r_values = np.array([t["r_multiple"] for t in trades])
    wins, losses = net[net > 0], net[net <= 0]

    # Drawdown on the equity curve in chronological order, not presentation order.
    equity = np.cumsum(net[::-1])
    drawdown = float(np.min(equity - np.maximum.accumulate(equity))) if len(equity) else 0.0

    return {
        "symbol": symbol,
        "trades": len(trades),
        "wins": int((net > 0).sum()),
        "losses": int((net <= 0).sum()),
        "win_rate_pct": 100 * (net > 0).mean(),
        "gross_profit": gross.sum(),
        "charges": sum(t["charges"] for t in trades),
        "net_profit": net.sum(),
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "expectancy_per_trade": net.mean(),
        "avg_r": float(np.nanmean(r_values)),
        "best": net.max(),
        "worst": net.min(),
        "max_drawdown": drawdown,
        "capital_deployed": max(t["cost_of_entry"] for t in trades),
        "stopped_out": sum(1 for t in trades if "stop" in t["exit_reason"]),
        "ema_breaks": sum(1 for t in trades if t["exit_reason"] == "ema break"),
    }


def verify_against_screener(symbol: str, sessions: int = 200,
                            length: int = EMA_LENGTH) -> tuple[int, int]:
    """Cross-check the fast signal against screener.context, which rebuilds frames.

    Returns (agreements, checked). Any disagreement means the O(1) forming-bar shortcut
    is not equivalent to actually truncating and resampling, and the backtest is wrong.
    """
    signal = ema_stack_signal(symbol, length)
    expression = (
        f"monthly.close > monthly.ema({length}) and weekly.close > weekly.ema({length}) "
        f"and daily.close > daily.ema({length})"
    )
    agree = checked = 0
    for _, row in signal.tail(sessions).iterrows():
        try:
            reference = screener.evaluate(expression, screener.context(symbol, row["ts"]))
        except screener.InsufficientHistory:
            continue
        checked += 1
        agree += int(bool(reference) == bool(row["in_stack"]))
    return agree, checked

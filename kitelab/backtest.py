"""EMA-stack strategy, simulated bar by bar on the stored data.

Rules, as written in your spreadsheet plus the stop you specified:

    entry   close is above the 20-EMA on the monthly, weekly AND daily timeframes,
            on the day that becomes true. Filled at that day's close ("buy on eod").
    stop    the low of the entry day.
    exit    whichever comes first --
              * a later day trades at or below the stop  -> filled at the stop, or at
                that day's open if it gapped straight through it
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

from . import frames, indicators, screener, sizing

EMA_LENGTH = 20
SHARES = 10

# Hysteresis: enter only when price is BAND above every EMA, exit only when it is BAND
# below one of them. The gap between those two lines is a dead zone where nothing
# happens. Without it, entry and exit share a single knife-edge, so price hovering
# around an EMA triggers an exit and a re-entry every few days -- the median holding
# period was 3 sessions on a strategy filtered by MONTHLY EMAs, and 20% of trades
# re-entered the same stock the very next day.
BAND = 0.02

# Zerodha equity DELIVERY charges, from zerodha.com/charges (checked 2026-08-23).
# These trades hold overnight, so delivery rates apply, not intraday.
BROKERAGE = 0.0
STT_RATE = 0.001           # 0.1% on buy and on sell
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


def charges(buy_value: float, sell_value: float, intraday: bool = False) -> float:
    """Round-trip cost. Intraday is roughly half of delivery, mostly because STT drops
    from 0.1% on both sides to 0.025% on the sell side alone."""
    if FLAT_FEE_RATE is not None:
        return FLAT_FEE_RATE * (buy_value + sell_value)
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


def ema_stack_signal(symbol: str, length: int = EMA_LENGTH,
                     band: float = BAND) -> pd.DataFrame:
    """Daily bars plus the point-in-time EMA-stack condition."""
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
    out["in_stack"] = (close > daily_ema) & (close > week_asof) & (close > month_asof)
    # Entry needs ALL three clear of the upper line; exit needs only ONE below the
    # lower line, mirroring the original "any EMA broken" rule.
    upper, lower = 1 + band, 1 - band
    out["entry_ok"] = ((close > daily_ema * upper) & (close > week_asof * upper)
                       & (close > month_asof * upper))
    out["exit_ok"] = ((close < daily_ema * lower) | (close < week_asof * lower)
                      | (close < month_asof * lower))
    # How many COMPLETED higher-timeframe bars existed. An EMA-20 resting on 3 monthly
    # bars is what TradingView draws, but it is not worth much -- surfaced, not hidden.
    out["weeks_done"] = week_pos
    out["months_done"] = month_pos
    return out


def simulate(symbol: str, length: int = EMA_LENGTH, shares: int = SHARES,
             band: float = BAND, scale_out: str | None = None) -> list[dict]:
    """Walk the signal series and produce closed trades, oldest first."""
    signal = ema_stack_signal(symbol, length, band)
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

        entry_price = float(close[position])
        stop = float(low[position])
        exit_at = None

        # scale_out: sell half at entry + 1R ("half"), optionally moving the stop on
        # the remainder to breakeven ("half_be"). Same-bar ambiguity goes to the stop.
        risk0 = entry_price - stop
        trigger = entry_price + risk0 if scale_out and risk0 > 0 else None
        current_stop = stop
        banked_fraction = banked_price = 0.0
        for step in range(position + 1, total):
            if low[step] <= current_stop:
                gapped = open_[step] < current_stop
                exit_at = (step, float(open_[step]) if gapped else current_stop,
                           "gap through stop" if gapped else "stop")
                break
            if trigger is not None and banked_fraction == 0.0 and high[step] >= trigger:
                banked_price = max(trigger, float(open_[step]))
                banked_fraction = 0.5
                if scale_out == "half_be":
                    current_stop = max(current_stop, entry_price)
            if exit_ok[step]:
                exit_at = (step, float(close[step]), "ema break")
                break

        if exit_at is None:
            break  # still open at the end of the data; not a closed trade

        exit_index, exit_price, reason = exit_at
        risk = entry_price - stop
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue
        buy_value = entry_price * shares
        banked_shares = shares * banked_fraction
        remaining = shares - banked_shares
        sell_value = banked_price * banked_shares + exit_price * remaining
        gross = ((banked_price - entry_price) * banked_shares
                 + (exit_price - entry_price) * remaining)
        if banked_fraction:
            reason = reason + " (half banked at 1R)"
        same_session = stamps[position].date() == stamps[exit_index].date()
        cost = charges(buy_value, sell_value)
        cost_best = charges(buy_value, sell_value, intraday=same_session)

        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[position],
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
            "bars_held": exit_index - position,
            "charges_best": cost_best,
            "net_profit_best": gross - cost_best,
            "entry_date": stamps[position],
            "entry_price": entry_price,
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
            "months_done": int(signal["months_done"].iloc[position]),
        })
        position = exit_index + 1

    return trades


def recent_trades(symbol: str, count: int = 25, length: int = EMA_LENGTH,
                  shares: int = SHARES) -> list[dict]:
    """The most recent `count` closed trades, newest first, with running totals.

    Cumulative columns run in presentation order (newest to oldest), matching how your
    existing sheet is laid out.
    """
    selected = simulate(symbol, length, shares)[-count:][::-1]
    cumulative_cost = cumulative_gross = cumulative_net = 0.0
    for number, trade in enumerate(selected, start=1):
        cumulative_cost += trade["cost_of_entry"]
        cumulative_gross += trade["gross_profit"]
        cumulative_net += trade["net_profit"]
        trade["trade_no"] = number
        trade["cum_cost"] = cumulative_cost
        trade["cum_gross"] = cumulative_gross
        trade["cum_net"] = cumulative_net
    return selected


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

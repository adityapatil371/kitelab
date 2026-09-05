"""The EMA stack at three speeds: Q/M/W, M/W/D, W/D/H.

One rule -- two higher timeframes must agree on the trend before the lowest one
is allowed to trigger -- run at three speeds. Q/M/W trades weekly closes, M/W/D
daily (the class strategy), W/D/H hourly.

This used to live in scripts/tf_compare.py alongside the workbook it wrote. The
report scripts were retired on 2026-09-01 because the dashboard replaced them,
but the strategy is not a report, so it moved into the package. The five
assigned stocks tf_compare used to define now live in config.CLASS_ASSIGNED,
because clean_data needs to know about them too.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# `report` went with kitelab/report.py on 2026-09-03: the import was here,
# never called, and was the only thing keeping 328 lines of openpyxl
# workbook-writing -- and the openpyxl dependency itself -- in the project.
from . import backtest, frames, indicators, sizing

LENGTH = 20
# Zero since 2026-09-05, moved in step with kitelab.backtest.BAND and
# kitelab.registry.BANDS -- the two must agree or the M/W/D and Q/M/W rows on the
# board would be reading different rules under the same name. See registry.BANDS
# for why the band went and what went with it.
BAND = 0.0

VARIANTS = [
    ("QMW", "Q/M/W", "quarterly + monthly stacks, traded on WEEKLY closes, stop = entry week's low"),
    ("MWD", "M/W/D", "monthly + weekly stacks, traded on DAILY closes, stop = entry day's low (class strategy)"),
    ("WDH", "W/D/H", "weekly + daily stacks, traded on HOURLY closes, stop = entry hour's low"),
]

# One higher timeframe instead of two, and not always the adjacent one. M/D and
# Q/W SKIP a level: the question is whether the intermediate frame carries its
# own weight or is just standing between two that matter.
PAIRS = [
    ("WD", "W/D", "weekly over DAILY closes"),
    ("MD", "M/D", "monthly over DAILY closes, skipping the weekly"),
    ("MW", "M/W", "monthly over WEEKLY closes"),
    ("QW", "Q/W", "quarterly over WEEKLY closes, skipping the monthly"),
    # ADDED 2026-09-05 so the ATH filter can be asked on them (see
    # registry.ATH_STACKS). They are registered as plain pairs too, without the
    # filter, because otherwise a good ATH-on-Q/M row cannot be read: there
    # would be nothing to say whether the credit belongs to the filter or to the
    # Q/M stack underneath it. Every ATH row on the board now has its own
    # unfiltered control.
    ("QM", "Q/M", "quarterly over MONTHLY closes"),
    ("QD", "Q/D", "quarterly over DAILY closes, skipping monthly and weekly"),
]


def _stack_frames(symbol: str, variant: str):
    """Return (base bars, [higher-TF bar frames]) for one variant.

    The three-frame variants are the class stacks. The PAIRS added on
    2026-09-02 ask what a single higher timeframe is worth, and whether it has
    to be the adjacent one: M/D skips the weekly entirely, Q/W skips the
    monthly. stack_signal loops over `highers`, so one is as valid as two.
    """
    day = frames.daily(symbol)
    if variant == "QMW":
        return frames.weekly(day), [frames.monthly(day), frames.quarterly(day)]
    if variant == "MWD":
        return day, [frames.weekly(day), frames.monthly(day)]
    if variant == "WDH":
        hour = frames.load(symbol, "1h")
        return hour, [day, frames.weekly(day)]
    # pairs: one higher timeframe, one trading timeframe
    if variant == "WD":                       # weekly over daily
        return day, [frames.weekly(day)]
    if variant == "MD":                       # monthly over daily, skipping weekly
        return day, [frames.monthly(day)]
    if variant == "MW":                       # monthly over weekly
        return frames.weekly(day), [frames.monthly(day)]
    if variant == "QW":                       # quarterly over weekly, skipping monthly
        return frames.weekly(day), [frames.quarterly(day)]
    # Q/M TRADES ON MONTHLY BARS, which is the coarsest base on the board and
    # behaves unlike the rest: ~245 bars since 2006, and the stop is the entry
    # MONTH's low, so the risk-per-share is large and sizing puts very few
    # shares on. Expect few trades and wide outcomes; read its trade count
    # before reading its return.
    if variant == "QM":                       # quarterly over monthly
        return frames.monthly(day), [frames.quarterly(day)]
    if variant == "QD":                       # quarterly over daily, skipping two
        return day, [frames.quarterly(day)]
    raise ValueError(variant)


def stack_signal(base: pd.DataFrame, highers: list[pd.DataFrame],
                 length: int = LENGTH, band: float = BAND,
                 ath_band: float | None = None) -> pd.DataFrame:
    """entry_ok/exit_ok on the base bars, higher TFs via forming-bar EMAs.

    ath_band mirrors backtest.ema_stack_signal: when set, only bars within that
    fraction of the running all-time high can trigger an ENTRY, and exits are
    left alone -- a rule that refuses to sell what it has already bought is not
    a filter, it is a trap. The running high is a cummax INCLUDING the current
    bar, which is knowable at its close, unlike the high still to come.

    ONE DELIBERATE DIFFERENCE FROM backtest.py, added 2026-09-05: the high is
    taken over the BASE frame's closes, not the daily ones. A weekly-traded rule
    therefore compares a weekly close against the highest weekly close. That is
    what pine/ema_ath_band.pine draws -- "switch the chart to weekly and you get
    the weekly 20 EMA and the weekly all-time high" -- so the backtest and the
    chart stay the same rule. On a daily base the two definitions coincide.

    Same conventions as backtest.ema_stack_signal: the base EMA includes the
    current bar's close (TradingView convention); each higher-timeframe EMA is
    the forming-bar value alpha*close + (1-alpha)*last-COMPLETED-bar EMA, and
    before any completed higher bar exists it degenerates to the close itself,
    which correctly fails the "close > ema" test.
    """
    alpha = 2.0 / (length + 1)
    close = base["close"].to_numpy()
    base_ema = indicators.ema(base["close"], length).to_numpy()
    # The bar's DECISION session, not its stamp. An aggregated bar is stamped at
    # its FIRST session but closes on its LAST, so a weekly bar running Mon->Fri
    # carries a Monday stamp. Looking a higher timeframe up by that stamp puts a
    # week that straddles a month boundary in the WRONG month, and the code then
    # recurses from the month before that -- one month stale. On ABB 144 of 1,078
    # weekly bars straddle (13.4%), median EMA error 1.44%, max 7.89%, against a
    # 2% band. Base frames that are not aggregated (daily, hourly) have no end_ts
    # and their stamp already IS the decision session.
    decided = base["end_ts"] if "end_ts" in base.columns else base["ts"]
    stamps = decided.to_numpy().astype("datetime64[ns]")

    upper, lower = 1 + band, 1 - band
    entry_ok = close > base_ema * upper
    exit_ok = close < base_ema * lower
    completed_counts = []
    for higher in highers:
        h_ema = indicators.ema(higher["close"], length).to_numpy()
        pos = np.searchsorted(higher["ts"].to_numpy().astype("datetime64[ns]"),
                              stamps, side="right") - 1
        prev = np.where(pos >= 1, h_ema[np.maximum(pos - 1, 0)], np.nan)
        asof = np.where(np.isnan(prev), close, alpha * close + (1 - alpha) * prev)
        entry_ok &= close > asof * upper
        exit_ok |= close < asof * lower
        completed_counts.append(pos)

    if ath_band is not None:
        peak = base["close"].cummax().to_numpy()
        entry_ok = entry_ok & (close >= peak * (1 - ath_band))

    out = base.copy()
    out["entry_ok"] = entry_ok
    out["exit_ok"] = exit_ok
    out["top_tf_done"] = completed_counts[-1]  # completed bars of the HIGHEST TF
    return out


def simulate_variant(symbol: str, variant: str,
                     stop_on_close: bool = True,
                     band: float = BAND,
                     ath_band: float | None = None) -> list[dict]:
    """Closed trades, oldest first. Mirrors backtest.simulate's walk exactly.

    stop_on_close=True is the class convention (everything checked at bar
    closes only); False is the pre-2026-08-28 broker convention.
    """
    base, highers = _stack_frames(symbol, variant)
    signal = stack_signal(base, highers, band=band, ath_band=ath_band)
    entry_ok = signal["entry_ok"].to_numpy()
    exit_ok = signal["exit_ok"].to_numpy()
    open_, high, low, close = (signal[c].to_numpy()
                               for c in ("open", "high", "low", "close"))
    # Stamp each trade on the session it was DECIDED on -- the session whose close
    # is the fill price. Stamping a Mon->Fri weekly bar on the Monday made the
    # account engine free and commit cash up to four days before the price it uses
    # existed, and made the daily curve mark a position from Monday at Friday's
    # price. Non-aggregated bases (daily, hourly) are unaffected: stamp == session.
    stamps = (signal["end_ts"] if "end_ts" in signal.columns else signal["ts"]).tolist()
    total = len(signal)

    trades: list[dict] = []
    position = 0
    while position < total:
        fresh = entry_ok[position] and position > 0 and not entry_ok[position - 1]
        if not fresh:
            position += 1
            continue
        entry_price = float(close[position])
        stop = float(low[position])
        exit_at = None
        for step in range(position + 1, total):
            if stop_on_close:
                if close[step] <= stop:
                    exit_at = (step, float(close[step]), "stop (close)")
                    break
            elif low[step] <= stop:
                gapped = open_[step] < stop
                exit_at = (step, float(open_[step]) if gapped else stop,
                           "gap through stop" if gapped else "stop")
                break
            if exit_ok[step]:
                exit_at = (step, float(close[step]), "ema break")
                break
        if exit_at is None:
            break  # still open; not a closed trade
        exit_index, exit_price, reason = exit_at
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0:
            position = exit_index + 1
            continue
        gross = (exit_price - entry_price) * shares
        buy_value = entry_price * shares
        sell_value = exit_price * shares
        same_session = stamps[position].date() == stamps[exit_index].date()
        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[position],
            "exit_ts": stamps[exit_index],
            "entry_price": entry_price,
            "stop": stop,
            "exit_price": exit_price,
            "exit_reason": reason,
            "shares": shares,
            "risk_taken": risk_taken,
            "capital_capped": capped,
            "gross_profit": gross,
            "charges": backtest.charges(buy_value, sell_value,
                                        intraday=same_session),
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "bars_held": exit_index - position,
            "days_held": (stamps[exit_index] - stamps[position]).days,
            "stop_pct": (entry_price - stop) / entry_price * 100,
            "top_tf_done": int(signal["top_tf_done"].iloc[position]),
        })
        position = exit_index + 1
    return trades


def window_start(symbol: str) -> pd.Timestamp:
    """First hourly bar's day -- the date all three variants can see."""
    return frames.base_15m(symbol)["ts"].min().normalize()


def summarise(trades: list[dict]) -> dict:
    """One convention, shared with every other summary in the project (2026-08-31):
    NET OF CHARGES, and a win is net > 0.

    This used to score on GROSS profit, so this file and band_compare reported a
    different win rate and profit factor for the same trades than the six other
    implementations -- 30.03% and 2.11 against 29.41% and 1.94 -- under column
    headers spelled identically. A trade that made Rs50 and paid Rs80 in charges is
    a loss, because it is.

    Zero-loss profit factor is None, not inf and not 0.0. There were four different
    answers to that case across the project; a ratio with no denominator is not a
    number, so it is not reported as one.
    """
    net = [t["gross_profit"] - t["charges"] for t in trades]
    wins = [v for v in net if v > 0]
    losses = [v for v in net if v <= 0]
    gross = sum(t["gross_profit"] for t in trades)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_win": np.mean(wins) if wins else 0.0,
        "avg_loss": np.mean(losses) if losses else 0.0,
        "expectancy": sum(net) / len(net) if net else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0
                         else None,
        "net": sum(net),
        "gross": gross,
        "charges": sum(t["charges"] for t in trades),
        "median_hold_days": float(np.median([t["days_held"] for t in trades])) if trades else 0.0,
        "median_stop_pct": float(np.median([t["stop_pct"] for t in trades])) if trades else 0.0,
    }

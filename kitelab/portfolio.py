"""Simulate an actual account rather than summing independent trades.

Every result so far added up trades as if each had its own funding. A real account has
one pot of money: if it is tied up in three positions, the fourth signal is missed, and
which signals you happen to catch changes everything.

    * risk is 1% of equity valued at COST: cash plus every open position held at
      the price it was bought at, never at today's mark. The account still
      compounds -- realised profit raises the base -- but an open loser does not
      shrink the next position until it is closed, so a trade opened inside a
      drawdown risks more than 1% of what the account is really worth that day.
      Deliberate: it is the size a trader working from a statement rather than a
      live screen would take, and it keeps sizing independent of the mark. See
      run(), where `equity` sums p["entry_price"], not the day's close.
    * a position is capped by cash on hand
    * signals arriving with no free cash are skipped, not queued
    * one open position per stock

Drawdown is measured on a DAILY MARK-TO-MARKET equity curve: every day, equity =
cash + shares x that day's close for every open position, drawdown = distance below
the running peak, in percent OF THAT PEAK. The old method (equity sampled only when
a trade settled, open positions held at cost, and the rupee dip divided by the FINAL
peak instead of the concurrent one) understated drawdown three separate ways; it is
kept under legacy_* keys so old reports can be reconciled.
"""
from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd

from . import frames, sizing, slippage

# How same-day signals are ordered when the account cannot afford them all.
#   "liquidity" -- most liquid first (the rule, see run()); deterministic
#   None        -- timestamp only, leaving ties to the caller's list order, which
#                  is what every result before 2026-08-31 did. Kept so those
#                  numbers can still be reproduced, not because it is defensible.
TIE_BREAK: str | None = "liquidity"

# WHICH SIGNAL DO YOU TAKE WHEN YOU CANNOT AFFORD THEM ALL?
#
# Scanning is free -- a screener watches the whole market, which is what a real
# trader does and what this project does. The consequence is that a small account
# sees far more signals than it can fund: over 500 stocks at Rs2,00,000 the class
# EMA stack skips 52% of its own for want of cash. So SOMETHING decides which are
# taken, that something is not part of the strategy, and on these settings it is
# deciding half the trades.
#
# It was a hardcoded constant until 2026-09-03. That is indefensible given the
# note above: re-ordering was worth 17.9%-25.6% CAGR on the Turtle and
# 8.3%-18.9% on the EMA stack -- a swing as large as the gap between the
# strategies being compared. One rule, chosen once, never tested against another.
#
# EVERY RULE HERE USES ONLY WHAT WAS KNOWN AT ENTRY. The outcome fields on a
# trade record -- exit_price, gross_profit, r_multiple, bars_held -- would rank
# candidates by how they turned out, which is not a priority rule but a time
# machine. Only entry_price, stop and trailing liquidity are consulted.
#
#   liquidity  most liquid first. The standing rule: a trader facing three
#              breakouts takes the one they can actually fill, and it steers the
#              account away from thin names where spread and impact bite.
#   illiquid   least liquid first. A CONTROL, not a proposal -- if it scores like
#              `liquidity` then the liquidity rule was never doing anything and
#              the 8-10 point swing lives somewhere else.
#   wide       widest stop first, as a percentage of entry. For a fixed rupee
#              risk, position value is risk / stop%, so a wide stop is a CHEAP
#              position: this funds the most trades and diversifies hardest.
#   tight      tightest stop first. The mirror: the best risk-reward geometry per
#              trade, bought by tying up the most cash in each one.
#   time       timestamp only, ties left to list order. What every result before
#              2026-08-31 did. Kept as the null rule, not because it is sane.
PRIORITIES = ["liquidity", "illiquid", "wide", "tight", "time"]


def _stop_pct(t) -> float:
    """Stop distance as a fraction of entry. entry_price and stop are the only
    two fields every producer sets, so this works for every strategy."""
    entry = t["entry_price"]
    return (entry - t["stop"]) / entry if entry else 0.0


def _order(trades: list[dict], priority: str | None) -> list[dict]:
    """Signals oldest first, ties broken by `priority`. Symbol last, always, so
    the result is fully deterministic whatever the rule."""
    if priority is None:
        priority = TIE_BREAK
    if priority in (None, "time"):
        return sorted(trades, key=lambda t: t["entry_ts"])
    keys = {
        "liquidity": lambda t: -slippage.liquidity_at(t["symbol"], t["entry_ts"]),
        "illiquid":  lambda t: slippage.liquidity_at(t["symbol"], t["entry_ts"]),
        "wide":      lambda t: -_stop_pct(t),
        "tight":     lambda t: _stop_pct(t),
    }
    if priority not in keys:
        raise ValueError(f"unknown priority {priority!r}; expected one of {PRIORITIES}")
    rank = keys[priority]
    return sorted(trades, key=lambda t: (t["entry_ts"], rank(t), t["symbol"]))
from .backtest import charges


@lru_cache(maxsize=None)
def _daily_closes(symbol: str):
    day = frames.daily(symbol)
    return (day["ts"].to_numpy().astype("datetime64[ns]"),
            day["close"].to_numpy().astype(float))


def daily_curve(taken: list[dict], capital: float) -> dict:
    """Daily mark-to-market equity curve for an already-simulated set of positions.

    Re-prices the account every trading day (union of the involved symbols'
    sessions): cash moves on entry/exit days exactly as in the simulation, and
    open positions are valued at that day's close (last known close on a day the
    stock did not trade). Drawdown is quoted against the peak standing at the
    time of the dip, never the final peak.
    """
    if not taken:
        return {"curve": [], "cash_curve": [], "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
                "peak_date": None, "trough_date": None}
    symbols = sorted({t["symbol"] for t in taken})
    start = np.datetime64(pd.Timestamp(min(t["entry_ts"] for t in taken)).normalize(), "ns")
    end = np.datetime64(pd.Timestamp(max(t["exit_ts"] for t in taken)).normalize(), "ns")
    calendar = np.unique(np.concatenate([_daily_closes(s)[0] for s in symbols]))
    calendar = calendar[(calendar >= start) & (calendar <= end)]

    aligned = {}
    for s in symbols:
        ts, close = _daily_closes(s)
        idx = np.searchsorted(ts, calendar, side="right") - 1
        aligned[s] = np.where(idx >= 0, close[np.maximum(idx, 0)], np.nan)

    def day_pos(stamp) -> int:
        return int(np.searchsorted(
            calendar, np.datetime64(pd.Timestamp(stamp).normalize(), "ns")))

    # A trade that opens and closes within the SAME session never holds an
    # overnight position: it only moves cash on that day. Keeping it out of
    # the open-positions map matters for intraday strategies, where a symbol
    # can round-trip several times in one day (the map holds one entry per
    # symbol, so same-day trades would collide with each other and with a
    # later overnight entry).
    entries_by_day: dict[int, list] = defaultdict(list)
    exits_by_day: dict[int, list] = defaultdict(list)
    sameday_by_day: dict[int, list] = defaultdict(list)
    for t in taken:
        t = dict(t)
        t["_entry_day"] = day_pos(t["entry_ts"])
        exit_day = day_pos(t["exit_ts"])
        if exit_day == t["_entry_day"]:
            sameday_by_day[exit_day].append(t)
        else:
            entries_by_day[t["_entry_day"]].append(t)
            exits_by_day[exit_day].append(t)

    cash = capital
    open_by_symbol: dict[str, dict] = {}
    curve: list[tuple] = []
    # Cash in hand, day by day. The engine has always known this and thrown it
    # away, so nothing could answer "if a signal fired today, could I afford it?"
    # Measured on the class stack from 2020: the account holds under 5% cash on
    # 85% of days and turns away 76% of its signals for want of money.
    cash_curve: list[tuple] = []
    peak = capital
    running_peak_day = pd.Timestamp(calendar[0]) if len(calendar) else None
    max_dd = 0.0
    max_dd_pct = 0.0
    peak_date = trough_date = None

    def close_out(t) -> None:
        nonlocal cash
        proceeds = t["shares"] * t["exit_price"]
        cash += proceeds - charges(t["shares"] * t["entry_price"], proceeds,
                                   t["same_session"])
        del open_by_symbol[t["symbol"]]

    for i in range(len(calendar)):
        # settle yesterday's positions first, then open today's overnight
        # positions, then net the same-day round trips through cash.
        for t in exits_by_day.get(i, ()):
            close_out(t)
        for t in entries_by_day.get(i, ()):
            cash -= t["shares"] * t["entry_price"]
            open_by_symbol[t["symbol"]] = t
        for t in sameday_by_day.get(i, ()):
            proceeds = t["shares"] * t["exit_price"]
            cash += proceeds - t["shares"] * t["entry_price"] \
                - charges(t["shares"] * t["entry_price"], proceeds, t["same_session"])
        equity = cash + sum(t["shares"] * aligned[s][i]
                            for s, t in open_by_symbol.items())
        day = pd.Timestamp(calendar[i])
        curve.append((day, equity))
        cash_curve.append((day, cash))
        if equity > peak:
            peak, running_peak_day = equity, day
        dip_pct = (equity - peak) / peak
        if dip_pct < max_dd_pct:
            max_dd_pct, peak_date, trough_date = dip_pct, running_peak_day, day
        max_dd = min(max_dd, equity - peak)

    return {"curve": curve, "cash_curve": cash_curve, "max_drawdown": max_dd,
            "max_drawdown_pct": 100 * max_dd_pct,
            "peak_date": peak_date, "trough_date": trough_date}


def ulcer_index(curve) -> float | None:
    """Root-mean-square of the percentage drawdown, day by day.

    Peter Martin's measure. Max drawdown says how deep the worst hole was and
    nothing about how long you sat in it; the Ulcer Index charges for both,
    because a 20% dip you climb out of in a month is not the same experience as
    a 20% dip that lasts three years. Squaring means a long shallow misery
    scores below one deep plunge, which matches how it feels.
    """
    peak = float("-inf")
    squares = []
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            squares.append((100 * (equity - peak) / peak) ** 2)
    return (sum(squares) / len(squares)) ** 0.5 if squares else None


def mar_ratio(cagr_pct, max_drawdown_pct) -> float | None:
    """CAGR divided by the worst drawdown, both over the whole record.

    The comparison actually being made when two strategies are read off side by
    side. Deliberately NOT Sharpe: Sharpe rewards smoothness and penalises the
    large up-moves this kind of strategy lives on, and worse here, it would
    reward an account for sitting in cash -- cash has no volatility, and this
    account is starved of money 80% of the time.

    Not Calmar either, which by its original definition uses only the last 36
    months. The two get conflated constantly; this one uses everything, so it is
    named for what it is.
    """
    if cagr_pct is None or not max_drawdown_pct:
        return None
    return cagr_pct / abs(max_drawdown_pct)


# The most a round trip may cost, as a fraction of the position. Above this the
# trade is refused as too small to be worth placing. 0.5% is roughly a Rs5,400
# floor at 2026 charges; the same number in a world of different fees moves by
# itself, which a rupee constant would not.
MAX_COST_FRACTION = 0.005


def _cash_shares(marked: dict) -> list[float]:
    """Cash as a percent of equity, per day. Empty when there is no curve."""
    eq = dict(marked["curve"])
    return [100 * c / eq[d] for d, c in marked["cash_curve"]
            if eq.get(d) and eq[d] > 0]


def _median_cash_pct(marked: dict) -> float | None:
    shares = sorted(_cash_shares(marked))
    return round(shares[len(shares) // 2], 1) if shares else None


def _fully_invested_pct(marked: dict) -> float | None:
    """Share of days holding under 5% cash -- days a new signal is unaffordable."""
    shares = _cash_shares(marked)
    return round(100 * sum(1 for x in shares if x < 5) / len(shares), 1) if shares else None


def run(trades: list[dict], capital: float = 10_000.0, risk_pct: float = 0.01,
        priority: str | None = None) -> dict:
    # Signals are offered oldest first, and TIES MATTER. Most sessions produce
    # several at once and a constrained account cannot take them all, so whichever
    # is offered first wins the cash. Sorting on the timestamp alone left that to
    # the order symbols happened to sit in a list -- and measured over 25 random
    # orderings on 2026-08-31 that was worth 17.9%-25.6% CAGR on Darvas and
    # 8.3%-18.9% on the EMA stack. An implementation detail cannot be allowed to
    # decide the answer.
    #
    # The rule: most liquid first. A trader facing three breakouts on one morning
    # takes the one they can actually fill, it uses only what the tape showed by
    # the previous close, and it steers the account away from the thin names where
    # spread and impact do their damage. Symbol name breaks any remaining tie so
    # the result is fully deterministic.
    # See PRIORITIES: which signal wins the cash is now a parameter, because on a
    # cash-starved account it decides as much as the strategy does.
    entries = _order(trades, priority)
    cash = capital
    peak = capital
    open_by_symbol: dict[str, dict] = {}
    curve: list[tuple] = [(entries[0]["entry_ts"], capital)] if entries else []
    taken: list[dict] = []
    skipped_cash = skipped_size = skipped_busy = skipped_liquidity = 0
    skipped_tiny = 0
    # Signals offered vs signals affordable, by year. The headline capture rate
    # hides a drift: as equity compounds, position sizes grow with it, so a
    # constrained account takes a SMALLER share of its signals in later years.
    # Without this the result can be an artefact of account size rather than of
    # the rule.
    offered_by_year: dict[int, int] = {}
    taken_by_year: dict[int, int] = {}
    max_drawdown = 0.0
    max_concurrent = 0

    # SETTLEMENT. settle() runs before every entry, so a position closed today
    # releases its cash in time to fund a purchase today. Two consequences worth
    # stating rather than leaving in the code:
    #
    #   - It avoids the ordering defect that bites naive engines, where buy
    #     orders are pseudo-executed against a running balance BEFORE the sells
    #     that fund them, and get rejected for want of money that was about to
    #     arrive. Exits are settled first here, always.
    #   - It assumes same-day proceeds are spendable. Under T+1 brokers commonly
    #     allow 80-100% of sale proceeds against fresh purchases, so this is
    #     defensible, but it IS optimistic and it interacts directly with the
    #     cash starvation this account already runs into. Trade-to-trade scrips
    #     are the exception and are not modelled.
    def settle(upto) -> None:
        nonlocal cash, peak, max_drawdown
        for symbol, pos in list(open_by_symbol.items()):
            if pos["exit_ts"] > upto:
                continue
            proceeds = pos["shares"] * pos["exit_price"]
            # fee_rate rides on the TRADE, so one account can hold instruments
            # with different cost models. Absent (every NSE equity) it is None
            # and the full Zerodha schedule applies.
            cost = charges(pos["shares"] * pos["entry_price"], proceeds,
                           pos["same_session"], pos.get("fee_rate"))
            held = pos.get("margin_held")
            if held is None:
                cash += proceeds - cost
            else:
                # A futures position was never bought outright: the margin comes
                # back and the move is settled in cash. Debiting the full value
                # on entry and crediting it on exit would give the same profit
                # while pretending the account had held ten times its capital.
                cash += held + (proceeds - pos["shares"] * pos["entry_price"]) - cost
            pos["net"] = proceeds - pos["shares"] * pos["entry_price"] - cost
            taken.append(pos)
            del open_by_symbol[symbol]
            equity = cash + sum(p["shares"] * p["entry_price"] for p in open_by_symbol.values())
            curve.append((pos["exit_ts"], equity))
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)

    for trade in entries:
        year = pd.Timestamp(trade["entry_ts"]).year
        offered_by_year[year] = offered_by_year.get(year, 0) + 1
        settle(trade["entry_ts"])
        if trade["symbol"] in open_by_symbol:
            skipped_busy += 1
            continue

        equity = cash + sum(p["shares"] * p["entry_price"] for p in open_by_symbol.values())
        per_share_risk = trade["entry_price"] - trade["stop"]
        if per_share_risk <= 0:
            continue
        # Whole units or fractions is a property of the INSTRUMENT, not of the
        # run: Bitcoin trades in fractions and a share does not, and an account
        # may hold both. Falls back to the module global, which is how the
        # single-instrument scripts still drive it.
        # A FUTURES CONTRACT IS NOT A SHARE. It trades in lots of a fixed size
        # and is funded by margin, not by its full value -- see kitelab.contracts
        # for both numbers and for why they are assumptions rather than data.
        # `multiplier` converts one lot into the units the price is quoted in.
        # Absent (every equity, and spot Bitcoin) this whole branch is skipped
        # and nothing about the existing behaviour changes.
        mult = trade.get("multiplier")
        margin_pct = trade.get("margin_pct")
        if mult:
            lot_value = trade["entry_price"] * mult
            lot_margin = lot_value * margin_pct
            risk_per_lot = per_share_risk * mult
            by_risk = math.floor(equity * risk_pct / risk_per_lot) if risk_per_lot else 0
            by_cash = math.floor(cash / lot_margin) if lot_margin else 0
            lots = min(by_risk, by_cash)
            if lots < 1:
                # One lot is indivisible: an account that cannot fund a single
                # one does not get a smaller position, it gets no trade.
                if by_risk < 1:
                    skipped_size += 1
                else:
                    skipped_cash += 1
                continue
            shares = lots * mult
            cash -= lots * lot_margin
            open_by_symbol[trade["symbol"]] = dict(
                trade, shares=shares, entry_price=trade["entry_price"],
                exit_price=trade["exit_price"], margin_held=lots * lot_margin)
            taken_by_year[year] = taken_by_year.get(year, 0) + 1
            max_concurrent = max(max_concurrent, len(open_by_symbol))
            continue

        fractional = trade.get("fractional", sizing.FRACTIONAL)
        if fractional:
            by_risk = equity * risk_pct / per_share_risk
            by_cash = cash / trade["entry_price"]
            shares = round(min(by_risk, by_cash), 6)
        else:
            by_risk = math.floor(equity * risk_pct / per_share_risk)
            by_cash = math.floor(cash / trade["entry_price"])
            shares = min(by_risk, by_cash)
        if shares <= 0 or (not fractional and shares < 1):
            if by_risk < 1:
                skipped_size += 1      # 1% of equity cannot cover even one share's risk
            else:
                skipped_cash += 1      # the risk rule allows it, the bank balance does not
            continue

        # A POSITION TOO SMALL TO BE WORTH PLACING.
        #
        # Sizing is risk / stop distance, capped by cash. When the account is
        # nearly fully committed -- which on the class stack is 80% of days --
        # the cash cap hands back whatever scraps are left, and the scraps can be
        # tiny: the 10th percentile position on that run was Rs52. Zerodha's DP
        # charge is Rs15.34 per sell whatever the size, so a Rs52 position pays
        # 29% of its own value in fees. It cannot profit. No one would place it.
        #
        # The floor is expressed as a COST FRACTION rather than a rupee figure,
        # so it follows the fee schedule instead of needing a rethink whenever
        # charges change: refuse the trade when a round trip costs more than
        # MAX_COST_FRACTION of the position. At the 2026 rates that bites at
        # roughly Rs5,400.
        value = shares * trade["entry_price"]
        if (value > 0 and charges(value, value, False, trade.get("fee_rate"))
                > MAX_COST_FRACTION * value):
            skipped_tiny += 1
            continue

        # What the stock can actually absorb. The trade-level sizer works off a
        # fixed Rs1,00,000 book; by the time this account has compounded, the same
        # signal can call for an order many times the stock's whole daily turnover.
        allowed = slippage.capped_shares(trade["symbol"], trade["entry_ts"],
                                         trade["entry_price"], shares)
        if allowed < shares:
            shares = allowed if fractional else math.floor(allowed)
            if shares < 1 and not fractional:
                skipped_liquidity += 1
                continue

        # Market impact belongs here and nowhere else: it is a function of the order
        # size, and this is the only place the real order size exists. The spread was
        # already paid at the trade level (kitelab.slippage.fill).
        #
        # Shares were sized against cash / entry_price, but the account is DEBITED at
        # the impacted price, which is higher. So a cash-constrained entry could spend
        # money the account did not have: measured on Q/M/W realistic, 46 entries left
        # cash negative, worst -Rs1,642. Impact falls as the order shrinks, so trimming
        # to what the impacted price can afford converges immediately; the loop is a
        # backstop, and shares only ever go down inside it.
        entry_fill, exit_fill = trade["entry_price"], trade["exit_price"]
        if slippage.ENABLED:
            for _ in range(8):
                entry_fill = trade["entry_price"] * (
                    1.0 + slippage.impact(trade["symbol"], trade["entry_ts"],
                                          trade["entry_price"] * shares))
                if shares * entry_fill <= cash or entry_fill <= 0:
                    break
                affordable = cash / entry_fill
                shares = (round(affordable, 6) if sizing.FRACTIONAL
                          else math.floor(affordable))
                if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
                    break
            if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
                skipped_cash += 1
                continue
            exit_fill *= 1.0 - slippage.impact(trade["symbol"], trade["exit_ts"],
                                               exit_fill * shares)

        cash -= shares * entry_fill
        taken_by_year[year] = taken_by_year.get(year, 0) + 1
        open_by_symbol[trade["symbol"]] = {**trade, "shares": shares,
                                           "entry_price": entry_fill,
                                           "exit_price": exit_fill}
        max_concurrent = max(max_concurrent, len(open_by_symbol))

    settle(max(t["exit_ts"] for t in entries)) if entries else None

    final = cash
    years = ((entries[-1]["exit_ts"] - entries[0]["entry_ts"]).days / 365.25) if entries else 0
    growth = final / capital
    marked = daily_curve(taken, capital)

    # An account that ended at or below zero has NO compound growth rate: there is
    # no rate r with capital x (1+r)^years <= 0. This used to return 0.0 for that
    # case, so an account destroyed down to -Rs7 displayed as "+0.0%/yr" -- the one
    # number a beginner reads as "broke even". 157 of the 3,072 dashboard grid cells
    # were doing exactly that on 2026-08-31.
    #
    # cagr_pct is None when the rate is undefined -- the account was wiped out, or no
    # time elapsed. `wiped` says which. A genuinely flat account still returns 0.0,
    # because for it 0.0 is the true answer. Callers must not coerce None to 0.
    wiped = growth <= 0
    cagr_pct = (100 * (growth ** (1 / years) - 1)
                if years > 0 and growth > 0 else None)
    return {
        "capital": capital, "final": final, "profit": final - capital,
        "return_pct": 100 * (growth - 1),
        "cagr_pct": cagr_pct,
        "wiped": wiped,
        "years": years,
        "signals": len(entries),
        "skipped_cash": skipped_cash, "skipped_size": skipped_size,
        "skipped_busy": skipped_busy,
        "skipped_liquidity": skipped_liquidity,
        # positions the fee schedule would have eaten -- see MAX_COST_FRACTION
        "skipped_tiny": skipped_tiny,
        # [year, offered, taken] -- how much of the strategy the account could
        # afford to run, and whether that share falls away as equity compounds
        "capture": [[y, offered_by_year[y], taken_by_year.get(y, 0)]
                    for y in sorted(offered_by_year)],
        # True drawdown: daily mark-to-market, percent of the concurrent peak.
        "max_drawdown": marked["max_drawdown"],
        "max_drawdown_pct": marked["max_drawdown_pct"],
        "dd_peak_date": marked["peak_date"],
        "dd_trough_date": marked["trough_date"],
        # The numbers every report before 2026-08-27 printed: settlement-sampled,
        # positions at cost, rupee dip divided by the FINAL peak. Kept for
        # reconciliation only -- do not quote these.
        "legacy_max_drawdown": max_drawdown,
        "legacy_max_drawdown_pct": 100 * max_drawdown / peak if peak else 0.0,
        "max_concurrent": max_concurrent,
        "wins": sum(1 for t in taken if t["net"] > 0),
        "curve": marked["curve"],
        "cash_curve": marked["cash_curve"],
        # How much of the account is WAITING rather than working. `fully_invested`
        # is the share of days with under 5% in hand -- the days a new signal is
        # unaffordable no matter how good it looks.
        "median_cash_pct": _median_cash_pct(marked),
        "fully_invested_pct": _fully_invested_pct(marked),
        # Return against pain, two ways. MAR is return per unit of worst dip;
        # the Martin ratio is return per unit of Ulcer, which charges for how
        # long the dips lasted as well as how deep they went.
        "ulcer": (round(ui, 2) if (ui := ulcer_index(marked["curve"])) is not None
                  else None),
        "mar": (round(m, 2) if (m := mar_ratio(
            cagr_pct, marked["max_drawdown_pct"])) is not None else None),
        "legacy_curve": curve,
        "taken": taken,
    }

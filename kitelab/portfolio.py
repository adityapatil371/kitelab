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
cash + the value of every open position at that day's close (shares x close for
an equity; margin plus the move since entry for a future), drawdown = distance
below the running peak, in percent OF THAT PEAK. The old method (equity sampled
only when a trade settled, open positions held at cost, and the rupee dip divided
by the FINAL peak instead of the concurrent one) understated drawdown three
separate ways; it is kept under legacy_* keys so old reports can be reconciled.

ONE BOOK, since 2026-09-07. run() is the only accounting: every debit and credit
it makes goes into a ledger with the position behind it, and daily_curve() marks
that ledger to market without recomputing anything. Before that the curve kept
its own books -- full notional on entry, the equity fee schedule on exit -- and
on any instrument that was not an NSE equity the two disagreed (audit A9: GOLD
at Rs1cr / 1% / 2006 settled at Rs1,23,06,279 while its curve ended at
Rs93,02,789). The curve's last point is now run()["final"] by construction.
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


def _last_close(symbol: str, stamp, strictly_before: bool) -> float | None:
    """The last daily close on -- or, with `strictly_before`, before -- the
    session `stamp` falls in. None when the symbol has no close that early."""
    ts, close = _daily_closes(symbol)
    day = np.datetime64(pd.Timestamp(stamp).normalize(), "ns")
    i = int(np.searchsorted(ts, day, side="left" if strictly_before else "right")) - 1
    return float(close[i]) if i >= 0 else None


def position_value(pos: dict, mark: float) -> float:
    """What one open position is worth to the account when the instrument
    trades at `mark`.

    An equity was bought outright, so it is worth shares x mark. A futures
    position was never bought: the account put up `margin_held` and is owed
    (or owes) the move since entry on the whole contract, which the exchange
    settles into cash every day. Valuing it at shares x mark -- the full
    notional -- is the 2026-09-07 audit's A9 defect: on GOLD at Rs1cr / 1% /
    2006 it made the daily curve end at Rs93,02,789 against a settled final of
    Rs1,23,06,279 and pushed the cash floor to -Rs6.7cr.
    """
    held = pos.get("margin_held")
    if held is None:
        return pos["shares"] * mark
    return held + pos["shares"] * (mark - pos["entry_price"])


def _cost_basis(pos: dict) -> float:
    """The cash the position tied up: its cost for an equity, the margin for a
    future. The legacy at-cost curve is built on this."""
    held = pos.get("margin_held")
    return pos["shares"] * pos["entry_price"] if held is None else held


def _units(x: float) -> float:
    """Fractional units, floored to a millionth.

    round() here used to round UP by as much as 5e-7 of a unit, which on an
    instrument priced in lakhs is real money -- Rs4 of cash the account did not
    have, per entry, on Bitcoin at Rs80 lakh. A trader cannot buy a millionth
    more than the cash covers, so floor; the 1e-9 pads floating-point crumbs
    (0.004 * 1e6 is not exactly 4000.0).
    """
    return math.floor(x * 1e6 + 1e-9) / 1e6


def daily_curve(ledger: list[dict], capital: float) -> dict:
    """Daily mark-to-market equity curve, read off run()'s ledger.

    ONE BOOK (2026-09-07, audit A9). Until then this function kept its own
    accounting: it re-debited the full notional on every entry, re-billed the
    Zerodha equity schedule on every exit with no fee_rate, and knew nothing
    about margin. run() settled futures correctly, so the two disagreed
    whenever an instrument was not an NSE equity -- on GOLD Rs1cr / 1% / 2006
    the settled final was Rs1,23,06,279, the curve ended at Rs93,02,789, and
    the curve's cash floor was -Rs6.7cr, which the page published as
    median_cash -921 on ema|0|GOLD|1|200000|1|2006.

    Now the ledger is the only accounting. Each event carries the position and
    the cash delta run() actually applied; this function replays those deltas
    day by day and marks whatever is open to that day's close (last known
    close on a day the instrument did not trade, its entry price before it has
    any). Nothing here computes a fee or a debit, so the curve's last point IS
    run()'s final cash, to the rupee, and its cash line is run()'s cash.

    The calendar is the union of the held instruments' sessions plus the
    settlement days themselves, so a ledger always yields a curve ending on
    its last settlement even when a price series is sparse. Drawdown is quoted
    against the peak standing at the time of the dip, never the final peak.
    """
    if not ledger:
        return {"curve": [], "cash_curve": [], "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
                "peak_date": None, "trough_date": None}
    symbols = sorted({e["pos"]["symbol"] for e in ledger})
    days = np.array([np.datetime64(pd.Timestamp(e["ts"]).normalize(), "ns") for e in ledger])
    start, end = days.min(), days.max()
    calendar = np.unique(np.concatenate([_daily_closes(s)[0] for s in symbols] + [days]))
    calendar = calendar[(calendar >= start) & (calendar <= end)]

    aligned = {}
    for s in symbols:
        ts, close = _daily_closes(s)
        idx = np.searchsorted(ts, calendar, side="right") - 1
        aligned[s] = np.where(idx >= 0, close[np.maximum(idx, 0)], np.nan)

    # Events in the order run() recorded them, which is settlement order:
    # every close dated D was recorded before any open dated D.
    by_day: dict[int, list] = defaultdict(list)
    for e, d in zip(ledger, days):
        by_day[int(np.searchsorted(calendar, d))].append(e)

    cash = capital
    open_positions: dict[int, dict] = {}
    curve: list[tuple] = []
    # Cash in hand, day by day. The engine has always known this and thrown it
    # away, so nothing could answer "if a signal fired today, could I afford it?"
    # Measured on the class stack from 2020: the account holds under 5% cash on
    # 85% of days and turns away 76% of its signals for want of money.
    cash_curve: list[tuple] = []
    peak = capital
    running_peak_day = pd.Timestamp(calendar[0])
    max_dd = 0.0
    max_dd_pct = 0.0
    peak_date = trough_date = None

    for i in range(len(calendar)):
        for e in by_day.get(i, ()):
            cash += e["cash_delta"]
            if e["kind"] == "open":
                open_positions[id(e["pos"])] = e["pos"]
            else:
                open_positions.pop(id(e["pos"]), None)
        equity = cash
        for pos in open_positions.values():
            mark = aligned[pos["symbol"]][i]
            equity += position_value(pos, mark if np.isfinite(mark) else pos["entry_price"])
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


def _too_small(value: float, fee_rate) -> bool:
    """True when a round trip would cost more than MAX_COST_FRACTION of it."""
    return value > 0 and charges(value, value, False, fee_rate) > MAX_COST_FRACTION * value


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
    # THE ONE BOOK. Every cash movement the account makes is recorded here with
    # the position it belongs to, and daily_curve() replays it -- see there for
    # the 2026-09-07 audit finding (A9) that made the second ledger untenable.
    ledger: list[dict] = []
    skipped_cash = skipped_size = skipped_busy = skipped_liquidity = 0
    skipped_tiny_cash = skipped_tiny_risk = 0
    # Signals offered vs signals affordable, by year. The headline capture rate
    # hides a drift: as equity compounds, position sizes grow with it, so a
    # constrained account takes a SMALLER share of its signals in later years.
    # Without this the result can be an artefact of account size rather than of
    # the rule.
    offered_by_year: dict[int, int] = {}
    taken_by_year: dict[int, int] = {}
    max_drawdown = 0.0
    max_concurrent = 0

    def record(ts, kind: str, pos: dict, cash_delta: float) -> None:
        ledger.append({"ts": ts, "kind": kind, "pos": pos, "cash_delta": cash_delta})

    def open_position(trade: dict, pos: dict, cash_delta: float, year: int) -> None:
        nonlocal cash, max_concurrent
        cash += cash_delta
        open_by_symbol[trade["symbol"]] = pos
        record(trade["entry_ts"], "open", pos, cash_delta)
        taken_by_year[year] = taken_by_year.get(year, 0) + 1
        max_concurrent = max(max_concurrent, len(open_by_symbol))

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
                credit = proceeds - cost
            else:
                # A futures position was never bought outright: the margin comes
                # back and the move is settled in cash. Debiting the full value
                # on entry and crediting it on exit would give the same profit
                # while pretending the account had held ten times its capital.
                credit = held + (proceeds - pos["shares"] * pos["entry_price"]) - cost
            cash += credit
            pos["net"] = proceeds - pos["shares"] * pos["entry_price"] - cost
            pos["charges"] = cost
            taken.append(pos)
            del open_by_symbol[symbol]
            record(pos["exit_ts"], "close", pos, credit)
            equity = cash + sum(_cost_basis(p) for p in open_by_symbol.values())
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

        # Equity for sizing: cash plus every open position at COST (see the
        # module note). For a futures position "cost" is the margin put up plus
        # the move the exchange has already settled into the account -- valued
        # at the last daily settlement BEFORE this session, so nothing is read
        # before it existed. Until 2026-09-07 this line summed shares x entry
        # for every position, which on a 6%-margin gold lot counted sixteen
        # times the cash actually committed and sized the next trade off it.
        equity = cash
        for p in open_by_symbol.values():
            if p.get("margin_held") is None:
                equity += _cost_basis(p)
            else:
                last = _last_close(p["symbol"], trade["entry_ts"], strictly_before=True)
                equity += position_value(p, p["entry_price"] if last is None else last)
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
            pos = dict(trade, shares=lots * mult, entry_price=trade["entry_price"],
                       exit_price=trade["exit_price"], margin_held=lots * lot_margin)
            open_position(trade, pos, -lots * lot_margin, year)
            continue

        # The flag is read ONCE and used everywhere below. Until 2026-09-07 the
        # impact loop re-read the module global instead (audit A8), so a
        # fractional trade that needed trimming to what cash could afford was
        # floored to whole coins and refused: ema|0|BITCOIN|0.5|200000 took 63
        # of 113 signals and reported 50 skipped for cash with median cash at
        # 100%. Honouring the flag takes 113 of 113 and moves CAGR 4.4 -> 5.4.
        fractional = trade.get("fractional", sizing.FRACTIONAL)
        if fractional:
            by_risk = equity * risk_pct / per_share_risk
            by_cash = cash / trade["entry_price"]
            shares = _units(min(by_risk, by_cash))
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
        #
        # WHICH RULE LEFT IT TINY is recorded separately (2026-09-07, audit D4).
        # by_cash < by_risk means the risk rule wanted a real position and the
        # bank balance handed back scraps -- a symptom of a starved account, not
        # of the strategy. Otherwise the risk-sized position itself was under
        # the floor, which says the account is too small for this stop. On W/D
        # at all / Rs2L / 1% / 2018 the split was 53,252 cash scraps against 14
        # risk-sized refusals, out of 95,452 signals -- one number for both
        # hid that almost every refusal was the account, not the rule.
        if _too_small(shares * trade["entry_price"], trade.get("fee_rate")):
            if by_cash < by_risk:
                skipped_tiny_cash += 1
            else:
                skipped_tiny_risk += 1
            continue

        # What the stock can actually absorb. The trade-level sizer works off a
        # fixed Rs1,00,000 book; by the time this account has compounded, the same
        # signal can call for an order many times the stock's whole daily turnover.
        allowed = slippage.capped_shares(trade["symbol"], trade["entry_ts"],
                                         trade["entry_price"], shares)
        if allowed < shares:
            shares = _units(allowed) if fractional else math.floor(allowed)
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
                shares = _units(affordable) if fractional else math.floor(affordable)
                if shares <= 0:
                    break
            if shares <= 0:
                skipped_cash += 1
                continue
            exit_fill *= 1.0 - slippage.impact(trade["symbol"], trade["exit_ts"],
                                               exit_fill * shares)

        # The cap and the impact loop only ever shrink the order, and a shrunk
        # order can land under the cost floor that the full one cleared. Refused
        # on the same terms (2026-09-07, audit A5) and counted with the cash
        # scraps: like cash, the cap is a limit on what can be bought, not on
        # what the rule wanted. Negligible on the board -- one order at 1% of a
        # stock's turnover is rarely near Rs5,400 -- but a floor with a hole is
        # not a floor.
        if _too_small(shares * entry_fill, trade.get("fee_rate")):
            skipped_tiny_cash += 1
            continue

        pos = {**trade, "shares": shares, "entry_price": entry_fill,
               "exit_price": exit_fill}
        open_position(trade, pos, -shares * entry_fill, year)

    settle(max(t["exit_ts"] for t in entries)) if entries else None

    final = cash
    # The span the account lived through: from the first signal it was offered
    # to the LATEST exit it was offered. Until 2026-09-07 the end was the exit
    # of the last-ENTERED signal (audit finding 6), which is earlier than the
    # true end whenever an older trade outlives the newest one -- the usual
    # case for a trend rule that holds winners -- and a short span inflates the
    # annualised rate. OFFERED, not taken: an account that took one trade in
    # 2010 and then refused 224 signals for size was alive for those fifteen
    # years (GOLD at Rs2L, measured 2026-09-07), and annualising its one
    # result over 0.13 years would flatter an early winner beyond recognition.
    if entries:
        years = (max(t["exit_ts"] for t in entries)
                 - min(t["entry_ts"] for t in entries)).days / 365.25
    else:
        years = 0
    growth = final / capital
    marked = daily_curve(ledger, capital)

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
        # positions the fee schedule would have eaten -- see MAX_COST_FRACTION.
        # Split since 2026-09-07 by which rule left them tiny; `skipped_tiny`
        # is their sum, kept for one release so nothing reading it breaks.
        "skipped_tiny": skipped_tiny_cash + skipped_tiny_risk,
        "skipped_tiny_cash": skipped_tiny_cash,
        "skipped_tiny_risk": skipped_tiny_risk,
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
        # Every cash movement with the position behind it, in settlement order.
        # What the curve above was read from; also what a reconciliation reads.
        "ledger": ledger,
        "taken": taken,
    }

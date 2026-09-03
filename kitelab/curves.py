"""Equity-curve arithmetic: how deep, how long, and how it compares to holding.

These used to live in scripts/drawdown_report.py, which also wrote a workbook.
When the report scripts were retired (2026-09-01, the dashboard replaced them)
the dashboard still needed this arithmetic, so it moved into the package where
it belongs. There is nothing about reporting in here -- no openpyxl, no
formatting, just the measurements.
"""
from __future__ import annotations

import pandas as pd


def underwater_stats(curve):
    """(longest spell in days, still-running spell in days)."""
    peak = float("-inf")
    peak_day = None
    longest = current = 0
    for day, eq in curve:
        if eq >= peak:
            peak, peak_day, current = eq, day, 0
        else:
            current = (day - peak_day).days
            longest = max(longest, current)
    return longest, current


def episodes(curve, top: int = 5):
    """Each peak-to-recovery spell: depth %, dates, duration. Deepest first."""
    out = []
    peak = float("-inf")
    peak_day = trough_day = None
    trough = None
    for day, eq in curve:
        if eq >= peak:
            if trough is not None and trough < peak:
                out.append({"peak_day": peak_day, "trough_day": trough_day,
                            "depth_pct": 100 * (trough - peak) / peak,
                            "recovered": day,
                            "days": (day - peak_day).days})
            peak, peak_day = eq, day
            trough, trough_day = eq, day
        elif eq < trough:
            trough, trough_day = eq, day
    if trough is not None and trough < peak:  # still open at the end
        out.append({"peak_day": peak_day, "trough_day": trough_day,
                    "depth_pct": 100 * (trough - peak) / peak,
                    "recovered": None,
                    "days": (curve[-1][0] - peak_day).days})
    return sorted(out, key=lambda e: e["depth_pct"])[:top]


def bh_stats(daily: pd.DataFrame):
    closes = daily["close"]
    dd = closes / closes.cummax() - 1
    years = (daily["ts"].iloc[-1] - daily["ts"].iloc[0]).days / 365.25
    growth = closes.iloc[-1] / closes.iloc[0]
    curve = list(zip(daily["ts"], closes))
    longest, current = underwater_stats(curve)
    return {"cagr": 100 * (growth ** (1 / years) - 1),
            "maxdd": float(100 * dd.min()),
            "trough": daily["ts"].iloc[int(dd.values.argmin())],
            "longest_uw": longest, "current_uw": current, "years": years}


# --------------------------------------------------------- risk metrics ----
#
# ADDED 2026-09-03, after a comparison against backtesting.py (~30 statistics)
# and vectorbt showed this project reporting four: CAGR, max drawdown, MAR and
# Ulcer. Those are all drawdown measures. Nothing here said anything about
# VOLATILITY, so a rule that made its return in one lucky quarter and a rule
# that ground it out steadily scored identically.
#
# Hand-written rather than pulled from empyrical or quantstats: these are about
# thirty lines of well-defined arithmetic over a daily equity curve the account
# engine already produces, and a dependency for thirty lines is how a lean repo
# stops being one. The cost of writing them is that they can be wrong, which is
# what tests/test_metrics.py is for -- each is checked against a hand-computed
# fixture, not against itself.
#
# Every one reads the DAILY MARK-TO-MARKET curve, never the trade list. A
# Sharpe computed over trade returns answers a different question (how good is
# the average trade) from the one asked here (how bumpy was the account).

TRADING_DAYS = 252


def daily_returns(curve):
    """Simple returns from an equity curve of (date, equity) pairs.

    The curve is daily but not necessarily contiguous, and that is deliberate:
    annualising by the COUNT of observations rather than by calendar span is
    what every library here does, and mixing the two is the usual way these
    ratios come out wrong.
    """
    out = []
    previous = None
    for _, equity in curve:
        equity = float(equity)
        if previous is not None and previous > 0:
            out.append(equity / previous - 1.0)
        previous = equity
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


# Below this, a standard deviation is rounding noise rather than volatility. A
# perfectly steady curve gives sd ~1e-18 instead of an exact zero, and dividing
# by that produced a Sharpe of 1.3e15 -- arithmetically right, and useless.
FLAT = 1e-12


def _stdev(xs):
    """Population standard deviation. n, not n-1: these are the realised
    returns of the account that actually ran, not a sample drawn from it."""
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5


def sharpe(curve, risk_free: float = 0.0):
    """Annualised excess return over annualised volatility.

    None, not zero, when volatility is zero: a flat account has an undefined
    ratio, and zero would rank it alongside a genuinely mediocre one.
    """
    rs = daily_returns(curve)
    sd = _stdev(rs)
    if not rs or sd < FLAT:
        return None
    excess = _mean(rs) - risk_free / TRADING_DAYS
    return (excess / sd) * (TRADING_DAYS ** 0.5)


def sortino(curve, risk_free: float = 0.0):
    """Sharpe's downside-only sibling: upside volatility is not risk.

    The denominator divides by the count of ALL returns, not just the negative
    ones -- the standard definition, and the one that keeps a rule with few
    losing days from being flattered by a tiny sample of them.
    """
    rs = daily_returns(curve)
    if not rs:
        return None
    target = risk_free / TRADING_DAYS
    downside = [min(r - target, 0.0) for r in rs]
    dd = (sum(d * d for d in downside) / len(rs)) ** 0.5
    if dd < FLAT:
        return None
    return ((_mean(rs) - target) / dd) * (TRADING_DAYS ** 0.5)


def calmar(cagr_pct, max_drawdown_pct):
    """CAGR over the worst drawdown. The same shape as this project's MAR --
    kept under its own name because that is what every other tool calls it."""
    if cagr_pct is None or not max_drawdown_pct:
        return None
    return cagr_pct / abs(max_drawdown_pct)


def exposure_pct(curve, cash_curve):
    """Share of days with money at work.

    Two rules with the same return are not equal if one was invested a fifth of
    the time -- the idle one carries far less risk for it, and on this project's
    cash-starved accounts exposure is what moves as universe, capital and risk
    change how much the account can afford to hold.
    """
    if not curve or not cash_curve or len(curve) != len(cash_curve):
        return None
    invested = sum(1 for (_, eq), (_, cash) in zip(curve, cash_curve)
                   if float(eq) > 0 and float(cash) < float(eq) * 0.999)
    return round(100.0 * invested / len(curve), 2)

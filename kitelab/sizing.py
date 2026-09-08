"""Position sizing: fixed capital, fixed percentage risk, hard capital cap.

Two constraints, and whichever binds first wins:

    by risk     shares = (capital * risk_pct) / (entry - stop)
    by capital  shares = capital / entry

The capital cap is the one the earlier runs were missing. Sizing by risk alone means
a very tight stop buys an unbounded position -- one support-bounce trade took a
Rs288,500 position to risk Rs500, on an account that never had Rs288,500. When the cap
binds, you simply hold less and risk LESS than the nominal percentage, which is what a
real account does.

Fixed capital, not compounding: every trade is sized off the same base, so results are
comparable across stocks and periods rather than depending on trade order.

WHAT THE DEFAULTS ARE FOR (2026-09-07, audit A11). Every producer -- backtest,
timeframes, darvas, holygrail -- calls position() at these module defaults when it
writes a trade to the signal cache, and a signal that sizes to zero shares is
DROPPED there, before any account sees it. The account (kitelab.portfolio.run)
re-sizes every trade off its own capital and risk, so at the producer stage
position() is nothing more than an "is this worth caching" gate. At Rs1,00,000 that
gate was refusing anything whose per-share risk exceeded Rs1,000 or whose price
exceeded the whole book: MRF on M/W/D cached 14 trades where 105 were possible, and
MRF, HONAUT, PAGEIND and BOSCHLTD lost every Q/M trade -- so the Rs1cr account on
the board never saw trades it could have taken. CAPITAL is therefore the LARGEST
capital on the board (scripts.dashboard_data.CAPITALS), so nothing is filtered out
that any gridded account could fund. This file is in the cache stamp
(signals._SUPPORT), which is what makes the change take effect: every cache
rebuilds against the wider gate.
"""
from __future__ import annotations

import math

CAPITAL = 10_000_000.0   # the largest account on the board -- see the module note
RISK_PCT = 0.01          # 1% of it per trade

# Bitcoin trades in fractions; a whole-coin minimum would reject every signal once
# the price exceeds the account. When True, position() returns fractional units.
FRACTIONAL = False


def risk_budget() -> float:
    return CAPITAL * RISK_PCT


def position(entry_price: float, stop: float) -> tuple[int, float, bool]:
    """Return (shares, rupee risk actually taken, whether the capital cap bound)."""
    per_share_risk = entry_price - stop
    if per_share_risk <= 0 or entry_price <= 0:
        return 0, 0.0, False
    if FRACTIONAL:
        by_risk = risk_budget() / per_share_risk
        by_capital = CAPITAL / entry_price
        units = round(min(by_risk, by_capital), 6)
        return units, units * per_share_risk, by_capital < by_risk
    by_risk = math.floor(risk_budget() / per_share_risk)
    by_capital = math.floor(CAPITAL / entry_price)
    shares = min(by_risk, by_capital)
    return shares, shares * per_share_risk, by_capital < by_risk

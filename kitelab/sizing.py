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
"""
from __future__ import annotations

import math

CAPITAL = 100_000.0    # account size
RISK_PCT = 0.01        # 1% of it per trade


def risk_budget() -> float:
    return CAPITAL * RISK_PCT


def position(entry_price: float, stop: float) -> tuple[int, float, bool]:
    """Return (shares, rupee risk actually taken, whether the capital cap bound)."""
    per_share_risk = entry_price - stop
    if per_share_risk <= 0 or entry_price <= 0:
        return 0, 0.0, False
    by_risk = math.floor(risk_budget() / per_share_risk)
    by_capital = math.floor(CAPITAL / entry_price)
    shares = min(by_risk, by_capital)
    return shares, shares * per_share_risk, by_capital < by_risk

"""Shared fixtures. Hermetic: no price files, no network, no config.

kitelab.portfolio marks the account to market every day, which means it reads
frames.daily() for every symbol it holds. Tests patch _daily_closes instead, so
the account engine can be exercised on invented prices rather than on whatever
happens to be in /data -- results that change when someone refetches a symbol
are not tests.
"""
from __future__ import annotations

import contextlib

import numpy as np
import pandas as pd

from kitelab import backtest, portfolio, sizing, slippage

TS = pd.Timestamp


def trade(symbol="AAA", entry="2020-01-01", exit_="2020-01-10",
          entry_price=100.0, exit_price=110.0, stop=90.0, same_session=False):
    """One closed trade in the shape every producer emits."""
    return {"symbol": symbol,
            "entry_ts": TS(entry), "exit_ts": TS(exit_),
            "entry_price": float(entry_price), "exit_price": float(exit_price),
            "stop": float(stop), "same_session": same_session,
            "shares": 1, "exit_reason": "test"}


@contextlib.contextmanager
def account(prices=None, flat_fee=None, fractional=False,
            participation=None, spread=False, liquidity=None):
    """Run the account engine against invented daily closes.

    Every module-level switch the engine reads is set AND restored here. They
    are globals rather than parameters (see the 2026-09-03 audit), so a test
    that forgot to restore one would silently corrupt every test after it.
    """
    prices = prices or {}
    liquidity = liquidity or {}

    # A real daily calendar, not two endpoints. Drawdown is measured on a DAILY
    # mark-to-market curve, so a two-point series gives the engine two days to
    # find a dip in and every drawdown test silently passes at 0.0.
    default_days = pd.date_range("2019-01-01", "2021-12-31", freq="D")

    def fake_daily(symbol):
        series = prices.get(symbol)
        if series is None:
            ts = default_days.to_numpy(dtype="datetime64[ns]")
            return ts, np.full(len(ts), 100.0)
        ts = np.array([t.to_datetime64() for t, _ in series], dtype="datetime64[ns]")
        return ts, np.array([c for _, c in series], dtype=float)

    # The default signal priority is "most liquid first", which reads the tape.
    # Patched so ordering can be tested on stated liquidity rather than on
    # whichever symbols happen to be in the data store.
    saved = (portfolio._daily_closes, backtest.FLAT_FEE_RATE, sizing.FRACTIONAL,
             slippage.MAX_PARTICIPATION, slippage.ENABLED, slippage.liquidity_at)
    portfolio._daily_closes = fake_daily
    slippage.liquidity_at = lambda symbol, stamp: float(liquidity.get(symbol, 1e7))
    backtest.FLAT_FEE_RATE = flat_fee
    sizing.FRACTIONAL = fractional
    slippage.MAX_PARTICIPATION = participation
    slippage.ENABLED = spread
    try:
        yield
    finally:
        (portfolio._daily_closes, backtest.FLAT_FEE_RATE, sizing.FRACTIONAL,
         slippage.MAX_PARTICIPATION, slippage.ENABLED, slippage.liquidity_at) = saved

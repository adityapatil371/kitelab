"""Indicators, on plain pandas Series.

Smoothing conventions follow TradingView so that values line up when you compare a
scan result against a chart: EMA uses adjust=False, RSI and ATR use Wilder smoothing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing is an EMA with alpha = 1/length.
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / length, adjust=False).mean()


def atr_stop_line(high: pd.Series, low: pd.Series, close: pd.Series,
                  mult: float, length: int = 14) -> np.ndarray:
    """close - mult x ATR(length), the WIDE arm of the board's stop axis.

    Added 2026-09-17. One helper rather than three inline copies, so the three
    engines and kitelab.entries cannot drift apart on what "3xATR" means. NaN
    through the ATR warm-up; every caller skips a signal whose stop is not
    finite rather than inventing one.

    WHY THE STOP BECAME AN AXIS. scripts/board_span.py measured that the
    board's uniform stop -- the entry candle's own low -- ends 54.6% of trades
    on day one, leaving a median holding period of ONE session, which collapses
    the eight exit rules onto 1 independent idea out of 8. The stop was never
    chosen against an alternative; it was inherited. This gives it one.
    """
    return (close - mult * atr(high, low, close, length)).to_numpy(float)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14):
    """Wilder's ADX, with the two directional lines. Returns (adx, plus_di, minus_di).

    ADX measures how STRONGLY price is trending, not which way -- it is built from
    the absolute gap between the two directional lines, so a hard downtrend and a
    hard uptrend both read high. Direction comes from +DI vs -DI, or from whatever
    else the strategy uses.

    Wilder smoothing throughout, as an EMA with alpha = 1/length, matching rsi()
    and atr() above. A bar is directional only if it moved further one way than
    the other: an outside bar that made both a higher high and a lower low counts
    for the larger move alone, and an inside bar counts for neither.
    """
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    alpha = 1 / length
    atr_ = true_range(high, low, close).ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_

    total = plus_di + minus_di
    # Both lines at zero means no range at all; DX is undefined, not 0.
    dx = 100 * (plus_di - minus_di).abs() / total.where(total != 0)
    return dx.ewm(alpha=alpha, adjust=False).mean(), plus_di, minus_di


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(series, fast) - ema(series, slow)
    signal_line = ema(line, signal)
    return line, signal_line, line - signal_line


def bollinger(series: pd.Series, length: int = 20, deviations: float = 2.0):
    basis = sma(series, length)
    width = series.rolling(length).std(ddof=0) * deviations
    return basis - width, basis, basis + width


def highest(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).max()


def lowest(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).min()


def roc(series: pd.Series, length: int = 1) -> pd.Series:
    """Percent change over `length` bars."""
    return series.pct_change(length) * 100

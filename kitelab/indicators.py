"""Indicators, on plain pandas Series.

Smoothing conventions follow TradingView so that values line up when you compare a
scan result against a chart: EMA uses adjust=False, RSI and ATR use Wilder smoothing.
"""
from __future__ import annotations

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

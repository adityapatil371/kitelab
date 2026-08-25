"""Point-in-time screening.

The difference from Chartink: every scan is evaluated as of a chosen moment, using
only bars that existed at that moment. Higher timeframes are rebuilt from the truncated
lower timeframe rather than sliced from a precomputed frame, so the weekly bar you
screen on mid-week is the partial bar a live trader would have been looking at -- never
the finished one that only exists in hindsight.

A query is a Python boolean expression over timeframe namespaces:

    daily.close > daily.ema(20)
    monthly.close > monthly.ema(20) and weekly.close > weekly.ema(20)
    daily.rsi(14) < 30 and daily.volume > 2 * daily.sma_volume(20)

Crossovers are written explicitly with `ago`, which reads awkwardly but leaves no doubt
about which bar is meant:

    daily.close > daily.ema(20) and daily.ago('close', 1) <= daily.ema(20, ago=1)
"""
from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from . import frames, indicators


class Timeframe:
    """One timeframe's bars, exposing scalars at (or `ago` bars before) the last bar."""

    def __init__(self, name: str, frame: pd.DataFrame):
        self.name = name
        self._f = frame.reset_index(drop=True)

    # -- raw fields -------------------------------------------------------
    def ago(self, field: str, n: int = 0) -> float:
        position = len(self._f) - 1 - n
        if position < 0:
            raise InsufficientHistory(f"{self.name}: only {len(self._f)} bars, need {n + 1}")
        return float(self._f.iloc[position][field])

    @property
    def bars(self) -> int:
        return len(self._f)

    @property
    def ts(self):
        return self._f.iloc[-1]["ts"]

    open = property(lambda self: self.ago("open"))
    high = property(lambda self: self.ago("high"))
    low = property(lambda self: self.ago("low"))
    close = property(lambda self: self.ago("close"))
    volume = property(lambda self: self.ago("volume"))

    # -- indicators -------------------------------------------------------
    def _at(self, series: pd.Series, ago: int) -> float:
        position = len(series) - 1 - ago
        if position < 0 or pd.isna(series.iloc[position]):
            raise InsufficientHistory(
                f"{self.name}: not enough bars to evaluate this indicator "
                f"({len(self._f)} available)"
            )
        return float(series.iloc[position])

    def sma(self, length: int, ago: int = 0, source: str = "close") -> float:
        return self._at(indicators.sma(self._f[source], length), ago)

    def ema(self, length: int, ago: int = 0, source: str = "close") -> float:
        return self._at(indicators.ema(self._f[source], length), ago)

    def sma_volume(self, length: int, ago: int = 0) -> float:
        return self._at(indicators.sma(self._f["volume"], length), ago)

    def rsi(self, length: int = 14, ago: int = 0) -> float:
        return self._at(indicators.rsi(self._f["close"], length), ago)

    def atr(self, length: int = 14, ago: int = 0) -> float:
        series = indicators.atr(self._f["high"], self._f["low"], self._f["close"], length)
        return self._at(series, ago)

    def macd(self, ago: int = 0, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
        return self._at(indicators.macd(self._f["close"], fast, slow, signal)[0], ago)

    def macd_signal(self, ago: int = 0, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
        return self._at(indicators.macd(self._f["close"], fast, slow, signal)[1], ago)

    def bb_upper(self, length: int = 20, deviations: float = 2.0, ago: int = 0) -> float:
        return self._at(indicators.bollinger(self._f["close"], length, deviations)[2], ago)

    def bb_lower(self, length: int = 20, deviations: float = 2.0, ago: int = 0) -> float:
        return self._at(indicators.bollinger(self._f["close"], length, deviations)[0], ago)

    def highest(self, length: int, ago: int = 0, source: str = "high") -> float:
        return self._at(indicators.highest(self._f[source], length), ago)

    def lowest(self, length: int, ago: int = 0, source: str = "low") -> float:
        return self._at(indicators.lowest(self._f[source], length), ago)

    def change(self, length: int = 1, ago: int = 0) -> float:
        return self._at(indicators.roc(self._f["close"], length), ago)


class InsufficientHistory(Exception):
    """Raised when a query needs more bars than exist as of the scan moment."""


_SOURCE_CACHE: dict[str, pd.DataFrame] = {}


def _cached(symbol: str, kind: str) -> pd.DataFrame:
    """Parquet does not change mid-run, so read each file once per process."""
    key = f"{symbol}:{kind}"
    if key not in _SOURCE_CACHE:
        _SOURCE_CACHE[key] = frames.base_15m(symbol) if kind == "15m" else frames.daily(symbol)
    return _SOURCE_CACHE[key]


ALIASES = {"d": "daily", "w": "weekly", "m": "monthly"}


class Context(Mapping):
    """Lazy timeframe namespaces for one symbol at one moment.

    Namespaces are built only when a query actually names them. That matters for
    scan_history: rebuilding the 30m and 1h frames costs a per-session loop, and a
    query that only mentions `daily` and `weekly` should never pay for it.

    Higher timeframes are derived AFTER truncation. Slicing a precomputed weekly frame
    would hand back the completed weekly bar even when asof falls mid-week -- lookahead.
    """

    def __init__(self, symbol: str, asof: pd.Timestamp | None = None):
        self.symbol = symbol
        self.asof = asof
        self._built: dict[str, Timeframe] = {}

    def _truncate(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame if self.asof is None else frame[frame["ts"] <= self.asof]

    def _build(self, name: str) -> Timeframe:
        if name in ("daily", "weekly", "monthly"):
            day = self._truncate(_cached(self.symbol, "1d"))
            if name == "daily":
                return Timeframe("1d", day)
            if name == "weekly":
                return Timeframe("1w", frames.weekly(day))
            return Timeframe("1M", frames.monthly(day))
        intraday = self._truncate(_cached(self.symbol, "15m"))
        if name == "m15":
            return Timeframe("15m", intraday)
        return Timeframe(
            "30m" if name == "m30" else "1h",
            frames.resample_intraday(intraday, 30 if name == "m30" else 60),
        )

    def __getitem__(self, key: str) -> Timeframe:
        name = ALIASES.get(key, key)
        if name not in ("daily", "weekly", "monthly", "m15", "m30", "h1"):
            raise KeyError(key)
        if name not in self._built:
            self._built[name] = self._build(name)
        return self._built[name]

    def __iter__(self):
        return iter(("daily", "weekly", "monthly", "m15", "m30", "h1", *ALIASES))

    def __len__(self) -> int:
        return 9


def context(symbol: str, asof: pd.Timestamp | None = None) -> Context:
    return Context(symbol, asof)


SAFE_BUILTINS = {"abs": abs, "min": min, "max": max, "round": round, "len": len}


def evaluate(expression: str, spaces: Context) -> bool:
    return bool(eval(expression, {"__builtins__": SAFE_BUILTINS}, spaces))


def scan(symbols: list[str], expression: str, asof=None) -> list[dict]:
    """Run one query across symbols at a single moment. Returns the matches."""
    moment = pd.Timestamp(asof) if asof is not None else None
    hits = []
    for symbol in symbols:
        try:
            spaces = context(symbol, moment)
            if not spaces["daily"].bars:
                continue
            if evaluate(expression, spaces):
                daily = spaces["daily"]
                hits.append({
                    "symbol": symbol,
                    "as_of": daily.ts,
                    "close": daily.close,
                    "change_pct": daily.change(1),
                })
        except InsufficientHistory:
            continue
    return hits


def scan_history(symbols: list[str], expression: str, sessions: int = 60) -> list[dict]:
    """Replay the same query over the last `sessions` trading days, per symbol.

    This is the point of the whole exercise: it answers 'when would this query have
    fired?', which a live-only screener cannot tell you.
    """
    fired = []
    for symbol in symbols:
        try:
            calendar = frames.daily(symbol)["ts"].tail(sessions).tolist()
        except SystemExit:
            continue
        for moment in calendar:
            try:
                spaces = context(symbol, moment)
                if evaluate(expression, spaces):
                    daily = spaces["daily"]
                    fired.append({
                        "symbol": symbol,
                        "date": moment,
                        "close": daily.close,
                        "change_pct": daily.change(1),
                    })
            except InsufficientHistory:
                continue
    return fired

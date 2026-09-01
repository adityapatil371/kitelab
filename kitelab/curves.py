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

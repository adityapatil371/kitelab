"""How far a trade went against you before it worked, and how far it ran.

MAE (maximum adverse excursion) and MFE (maximum favourable excursion) are the
standard instruments for judging a stop, and this project had neither -- while
arguing about stops more than anything else: the Holy Grail's two readings of
"swing low", the Turtle stopping at the channel low instead of the entry
candle's, and the wide-vs-tight signal priority. Those were debated on outcomes
alone, which cannot distinguish "the stop was too tight" from "the entry was
wrong".

Measured in R -- multiples of the trade's own initial risk -- because that is
the only unit in which a Rs20 stock and a Rs2,000 stock are comparable.

    MAE = (entry - lowest low while open)  / (entry - stop)
    MFE = (highest high while open - entry) / (entry - stop)

A winner with MAE of 1.5R was saved only by not being stopped out, which means
the stop was doing nothing; a stop tightened to 1R would have killed it. That
comparison is the whole point, and worst_survivable() below states it directly.

THE ENTRY BAR IS EXCLUDED, and getting this wrong made the first run of this
module meaningless. Entries fill at the close ("buy on eod"), so the entry day's
low happened BEFORE the position existed -- and for every rule here the stop IS
that low. Including the bar therefore measured the stop against itself and
returned a median adverse excursion of exactly 1.00R for rule after rule, which
is not a finding about markets but an identity. Exposure starts at the next bar.

No lookahead is possible: every bar read is one the trade lived through.
"""
from __future__ import annotations

import pandas as pd


def excursions(trade: dict, daily: pd.DataFrame) -> dict | None:
    """MAE and MFE in R for one closed trade, or None if unmeasurable."""
    entry = float(trade["entry_price"])
    risk = entry - float(trade["stop"])
    if risk <= 0:
        return None
    start, end = pd.Timestamp(trade["entry_ts"]), pd.Timestamp(trade["exit_ts"])
    window = daily[(daily["ts"] > start) & (daily["ts"] <= end)]
    if window.empty:
        return None
    low, high = float(window["low"].min()), float(window["high"].max())
    return {"mae_r": round((entry - low) / risk, 3),
            "mfe_r": round((high - entry) / risk, 3),
            "risk_pct": round(100.0 * risk / entry, 2)}


def worst_survivable(rows: list[dict], pct: float = 90.0) -> float | None:
    """The stop, in R, that `pct` of winners here would have survived.

    A PERCENTILE, not the maximum. R is the entry bar's own range for most of
    these rules, so a trade entered on an unusually quiet day has a tiny R and
    an ordinary wobble becomes a 50R excursion. Taking the max let a handful of
    those set the answer for 95,000 trades -- the first run reported a "tightest
    safe stop" of 155R, which is not a stop, it is an outlier wearing one.

    pct=100 recovers the strict reading: the stop no winner would have hit.
    """
    winners = sorted(r["mae_r"] for r in rows if r.get("won"))
    if not winners:
        return None
    i = min(len(winners) - 1, int(round((pct / 100.0) * (len(winners) - 1))))
    return round(winners[i], 3)


def summarise(rows: list[dict]) -> dict:
    """MAE/MFE split by outcome, which is the only split that says anything.

    Winners and losers are reported apart because the interesting number is the
    GAP: if losers go against you no further than winners do, no stop can tell
    them apart and tightening it only cuts winners.
    """
    def stats(sample, key):
        xs = sorted(r[key] for r in sample)
        if not xs:
            return None
        mid = xs[len(xs) // 2]
        return {"median": round(mid, 3), "worst": round(xs[-1], 3), "n": len(xs)}

    won = [r for r in rows if r.get("won")]
    lost = [r for r in rows if not r.get("won")]
    return {"winners_mae": stats(won, "mae_r"), "losers_mae": stats(lost, "mae_r"),
            "winners_mfe": stats(won, "mfe_r"),
            "stop_holding_90pct_r": worst_survivable(rows, 90.0),
            "stop_holding_all_r": worst_survivable(rows, 100.0),
            "trades": len(rows)}

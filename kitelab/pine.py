"""The user's Pine rule -- Heikin-Ashi no-wick + higher-timeframe EMA + RSI.

WHY THIS IS IN `kitelab/` AND NOT IN `scripts/`, from 2026-09-19. It was written
in scripts/wf_pine.py on 2026-09-11 as a one-off measurement of a rule the user
trades by hand, and a one-off measurement is exactly what `scripts/` is for. On
2026-09-19 the rule joined the board as an ENTRY (NEXT_TESTS item 24), and
CLAUDE.md is unambiguous: strategy logic that produces board trades lives under
kitelab/, because that is the tree signals.stamp() walks. A board row whose
signal came from scripts/ would be the 2026-09-02 holygrail.py bug again -- an
edit to the rule with no change to the cache digest.

WHAT MOVED, AND WHAT DID NOT. The SIGNAL half moved here verbatim: the bars, the
Heikin-Ashi transform, the higher-timeframe EMA filter, RSI, ATR, and the three
entry legs. The TRADE half -- the ATR stop, the 3xATR target, the five
(stop, fill) variants, the walk-forward harness -- stayed in scripts/wf_pine.py,
which now imports from here. Nothing was re-derived; scripts/wf_pine.py's
numbers are unchanged by the move, and a parity check against the pre-move file
over 40 symbols x 2 timeframes was run before it was committed.

WHAT THE BOARD TRADES IS NOT THIS RULE'S EXIT. `kitelab/entries.py` takes the
ENTRY panel only (weekly bars, the full rule including RSI, stamped on the
decision session and shifted one session -- `pine:W-full+1`) and gives it the
same exit every other entry family gets: the stop axis plus a 60-session cap.
That is deliberate, and it is the only way the comparison means anything --
holding the exit fixed is what makes eighteen entries comparable. The rule as
its author trades it, with its own ATR stop and target, is measured separately
in scripts/wf_pine.py and was rejected there on 2026-09-16
(0 of 276 cells at the gate); this is a different question about the same idea.

CONVENTIONS, both settled on 2026-09-17 by scripts/pine_span.py and not
re-opened here:

  the stamp   a weekly firing is dated to `end_ts`, the session the weekly bar
              CLOSED and the decision could first be taken -- NOT the Monday
              frames.weekly() dates the bar to.
  the shift   +1 session. The Pine fills at the next open, so the panel that
              describes what it can actually trade puts the firing on the
              session AFTER the decision. The 2026-09-16 run showed Pine
              findings flip sign with the execution convention, so this was
              chosen once, before any return was read, and written down.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import frames, indicators

EMA_LEN, RSI_LEN, ATR_LEN = 20, 14, 14
RSI_LONG, RSI_SHORT = 50, 50
WICK_TOL = 0.08
WARMUP = max(RSI_LEN, ATR_LEN) + 1     # bars before the daily indicators mean anything

REQUIRED = ["ts", "open", "high", "low", "close"]

# Which bars the rule trades, and which bars the trend filter reads. The Pine
# runs on a daily chart with a WEEKLY EMA filter, so "step it up to weekly"
# means weekly bars with a MONTHLY filter -- the same one-step-up relationship,
# not a weekly filter on a weekly chart (which would compare a close to an EMA
# of itself).
TIMEFRAMES = {
    "D": ("daily bars, weekly EMA(20) filter", lambda day: (day, frames.weekly(day))),
    "W": ("weekly bars, monthly EMA(20) filter",
          lambda day: (frames.weekly(day), frames.monthly(day))),
    # One more rung. NOTE the history this costs: the filter is a 20-period EMA
    # on the higher frame, and on M that frame is QUARTERLY -- 20 closed
    # quarters is five years before the first entry can exist, against one
    # year on W and three months on D. Expect the usable universe to shrink
    # and to tilt towards old listings; main() prints the count it kept.
    "M": ("monthly bars, quarterly EMA(20) filter",
          lambda day: (frames.monthly(day), frames.quarterly(day))),
}


# ------------------------------------------------------------- signals ----
def heikin_ashi(o, h, l, c):
    """The recursive HA open has no vector form; the rest is arithmetic."""
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty_like(ha_c)
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(ha_c)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum.reduce([h, ha_o, ha_c])
    ha_l = np.minimum.reduce([l, ha_o, ha_c])
    return ha_o, ha_h, ha_l, ha_c


def signals(symbol: str, tf: str = "D") -> pd.DataFrame:
    """Base bars plus every column the Pine reads, point-in-time.

    The trend filter is the LAST CLOSED higher-timeframe bar's EMA(20), which
    is what request.security(..., lookahead_off) returns on history -- and the
    only version knowable at the base bar's close. While the first higher bar
    is still forming there is no closed one, so the filter is False and no
    trade can be taken; that is the conservative direction.

    THE HIGHER FRAME IS LOOKED UP BY THE DECISION SESSION, not the stamp.
    frames.weekly() dates a Mon->Fri bar to its MONDAY but the decision is
    taken on its Friday close. Looking a month up by the Monday puts a week
    that straddles a month boundary in the wrong month and reads an EMA one
    month stale -- measured on ABB at 13.4% of weekly bars, median error 1.44%.
    kitelab/timeframes.py:141 made this call first; this follows it.
    """
    if tf not in TIMEFRAMES:
        raise SystemExit(f"unknown timeframe {tf!r}, expected one of {list(TIMEFRAMES)}")
    day = frames.daily(symbol).reset_index(drop=True)
    missing = [c for c in REQUIRED if c not in day.columns]
    if missing:
        raise SystemExit(f"{symbol}: daily frame is missing {missing}")
    if day[REQUIRED].isna().any().any():
        bad = day[REQUIRED].isna().sum().to_dict()
        raise SystemExit(f"{symbol}: unexpected missing values in the candles: {bad}")
    if len(day) < 150:
        return day.iloc[0:0]

    base, higher = TIMEFRAMES[tf][1](day)
    base = base.reset_index(drop=True)
    if len(base) < WARMUP + 30 or len(higher) < EMA_LEN + 2:
        return base.iloc[0:0]

    o, h, l, c = (base[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ha_o, ha_h, ha_l, ha_c = heikin_ashi(o, h, l, c)
    rng = ha_h - ha_l
    lower, upper = ha_o - ha_l, ha_h - ha_o

    decided = (base["end_ts"] if "end_ts" in base.columns else base["ts"])
    higher_ema = indicators.ema(higher["close"], EMA_LEN).to_numpy()
    pos = np.searchsorted(higher["ts"].to_numpy(), decided.to_numpy(), side="right") - 1
    closed = pos - 1                       # lookahead_off: the last CLOSED bar
    # A 20-period EMA seeded two bars ago is not a 20-bar trend. ewm(adjust=False)
    # emits a number from the first bar, so without this the first trades in every
    # symbol are filtered by an EMA that is nearly the close itself. Same reason
    # entries wait WARMUP base bars for RSI and ATR below.
    htf = np.where(closed >= EMA_LEN - 1, higher_ema[np.maximum(closed, 0)], np.nan)

    rsi = indicators.rsi(base["close"], RSI_LEN).to_numpy()
    prev_rsi = np.concatenate([[np.nan], rsi[:-1]])
    atr = indicators.atr(base["high"], base["low"], base["close"], ATR_LEN).to_numpy()

    out = base.copy()
    out["ha_open"], out["ha_high"], out["ha_low"], out["ha_close"] = ha_o, ha_h, ha_l, ha_c
    out["htf_ema"] = htf
    out["rsi"], out["atr"] = rsi, atr
    out["trend_up"] = c > htf
    out["trend_down"] = c < htf
    out["bull_nowick"] = (ha_c > ha_o) & (lower <= rng * WICK_TOL)
    out["bear_nowick"] = (ha_c < ha_o) & (upper <= rng * WICK_TOL)
    out["rsi_long"] = (rsi > RSI_LONG) & (rsi > prev_rsi)
    out["rsi_short"] = (rsi < RSI_SHORT) & (rsi < prev_rsi)
    out["ha_exit_long"] = (ha_c < ha_o) & (upper > rng * WICK_TOL)
    out["ha_exit_short"] = (ha_c > ha_o) & (lower > rng * WICK_TOL)
    out["months_done"] = _months_done(base["ts"])
    return out


def _months_done(ts: pd.Series) -> np.ndarray:
    """Whole months of history behind each bar -- the board's trade dicts carry it."""
    first = ts.iloc[0]
    return ((ts.dt.year - first.year) * 12 + (ts.dt.month - first.month)).to_numpy()


def entry_mask(sig, side, *, use_htf=True, use_wick=True, use_rsi=True):
    """The Pine's condition, with each of its three legs switchable for --ablate."""
    n = len(sig)
    ok = np.arange(n) >= WARMUP
    if use_htf:
        ok &= sig["trend_up" if side == "long" else "trend_down"].to_numpy()
    if use_wick:
        ok &= sig["bull_nowick" if side == "long" else "bear_nowick"].to_numpy()
    if use_rsi:
        ok &= sig["rsi_long" if side == "long" else "rsi_short"].to_numpy()
    return ok

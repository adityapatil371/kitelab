"""Daily bar entry families and cross-sectional panels for research scripts."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kitelab import curves, entries, frames

BAR_LOCAL = ["mr", "vcon", "vol", "pull", "rsi30", "dryup", "low252", "inside"]
FLAGGED = ["cal", "gap", "gapdn"]
PANEL = ["xrank", "mktrel"]
FAMILIES = BAR_LOCAL + FLAGGED + PANEL
STOPS = {"own": None, "atr3": 3.0}
XRANK_TOP = 0.10
REL_LOOKBACK = 20
MOM_LOOKBACK = 252

def close_panel(symbols, tf, window_first, window_last):
    """Wide (bars x symbols) closes on ONE bar index, for xrank and mktrel.

    Both rules are statements about a stock relative to everything else at the
    same instant, so they cannot be computed one symbol at a time. Trimmed to
    the common window so a symbol's rank is never decided against a different
    set of peers than the rest of the run uses.
    """
    cols = {}
    for sym in symbols:
        b = frames.daily(sym)
        s = pd.Series(b["close"].to_numpy(float), index=pd.DatetimeIndex(b["ts"]))
        s = s[~s.index.duplicated(keep="last")]
        cols[sym] = s[(s.index >= window_first) & (s.index <= window_last)]
    wide = pd.DataFrame(cols).sort_index()
    print(f"  panel {tf}: {wide.shape[0]:,} bars x {wide.shape[1]:,} symbols "
          f"({wide.index.min()} .. {wide.index.max()})")
    return wide


def panel_masks(wide: pd.DataFrame):
    """xrank and mktrel as wide boolean panels, at this bar size."""
    ret = wide / wide.shift(MOM_LOOKBACK) - 1.0
    xrank = ret.rank(axis=1, pct=True).gt(1.0 - XRANK_TOP)

    bar_ret = wide.pct_change(fill_method=None)
    proxy = curves.ew_daily_wealth(bar_ret)
    weak = (proxy / proxy.shift(REL_LOOKBACK) - 1.0) < 0.0
    own_up = (wide / wide.shift(REL_LOOKBACK) - 1.0).gt(0.0)
    mktrel = own_up & pd.DataFrame(
        np.repeat(weak.to_numpy()[:, None], wide.shape[1], axis=1),
        index=wide.index, columns=wide.columns)

    listed = wide.notna()
    return {"xrank": (xrank.fillna(False) & listed),
            "mktrel": (mktrel.fillna(False) & listed)}


def fires_for(symbol, entry, bars, masks):
    """The entry mask, from the board's own definition where one exists."""
    if entry in PANEL:
        col = masks[entry].get(symbol)
        if col is None:
            return np.zeros(len(bars), dtype=bool)
        aligned = col.reindex(pd.DatetimeIndex(bars["ts"]))
        return aligned.fillna(False).to_numpy(dtype=bool)
    return entries.signal(symbol, entry, bars)


"""Does shortening the bar help? The board's entry rules at 15m, 1h and daily.

THE QUESTION, set by the user 2026-09-23: "isnt the point of intraday to
quickly buy and sell in a way that leverage risks can be mitigated?" -- after
scripts/wf_leverage.py showed leverage cannot rescue a rule that loses to
buy-and-hold. The intraday claim is different and deserves its own test: going
flat every evening genuinely removes overnight gap risk, which is what killed
the 3x accounts in 2008. So the honest question is whether what you pay for
that protection costs less than the protection is worth.

WHAT IS HELD FIXED. Same 133 stocks, same window, same rule definitions, same
stop, same sizing, same spread model, same charge schedule. ONLY the bar size
changes, and in the sqoff arm, the exit regime. Anything that differs between
two rows of the output differs because of the bar or the regime.

MAXHOLD IS COUNTED IN BARS, NOT DAYS, AND THAT IS A CHOICE. 60 bars is a
quarter on daily bars and 2.4 sessions on 15-minute bars. The alternative --
holding wall-clock time fixed, so a 15m rule waits 1,500 bars -- tests a
different claim. This file tests the claim the teachers actually make: take
the same rule, put it on a faster chart, trade it more often. A chart trader
does not rescale their moving average when they change timeframe, and neither
does this. Consequence worth stating out loud: `pull`'s SMA200 is 8 sessions
on 15m, and `low252` is a 10-session low rather than a one-year low.

THE PARTICIPATION CAP IS THE TRAP HERE, and it is not applied in the trade
layer -- not here and not in entries.simulate, which is what makes the
timeframes comparable. slippage.capped_shares limits ONE order to 1% of the
stock's DAILY turnover. Trade 25 times a session on 15-minute bars and that
same cap silently permits a quarter of the day's volume. So instead of
applying a cap that is wrong by construction at this bar size, the harness
MEASURES the breach: for every trade it compares the order against 1% of the
trailing median turnover of the BAR it fills in. `cap_breach_pct` is the share
of orders that could not have been filled at the size the sizer asked for.
Read it as a ceiling on how much of the intraday result is real.

FIVE OF EIGHTEEN FAMILIES ARE NOT TESTABLE and are excluded rather than
approximated: `pine`, `e1`, `dv`, `pair` and `eath` all filter on MONTHLY or
WEEKLY aggregates of daily bars. A "monthly EMA" recomputed on 15-minute bars
is a different rule wearing the same label, and the project has a standing
rule against that (see the end_ts note in NEXT_TESTS). `cal`, `gap` and
`gapdn` ARE run but flagged: `cal` fires on the first bar of a month and
carries no bar-size content at all, and the two gap rules can only fire on the
09:15 bar, because inside a session bar N opens where bar N-1 closed.

Reads  /data/clean/kitelab/<sym>_15minute.parquet and <sym>_day.parquet
Writes output/measurements/wf_intraday_<date>.csv
       output/figures/wf_intraday_<date>.png
"""
from __future__ import annotations

import argparse
import gc
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kitelab import frames, entries, indicators, sizing, slippage  # noqa: E402
from kitelab.backtest import charges  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "output"
CLEAN = Path("/data/clean/kitelab")

# The 13 testable families. Order is registry order so the CSV lines up with
# the board. The five that need monthly/weekly aggregates are absent by name.
BAR_LOCAL = ["mr", "vcon", "vol", "pull", "rsi30", "dryup", "low252", "inside"]
FLAGGED = ["cal", "gap", "gapdn"]          # run, but see the module docstring
PANEL = ["xrank", "mktrel"]                # need a cross-sectional bar panel
FAMILIES = BAR_LOCAL + FLAGGED + PANEL

STOPS = {"own": None, "atr3": 3.0}
TIMEFRAMES = ["15m", "1h", "1d"]
MAXHOLD = entries.MAXHOLD                  # 60 BARS -- see the docstring
MIN_SESSIONS = 1000
CAP_WINDOW = 50            # bars, for the trailing bar-turnover median
CAP_FRAC = 0.01            # one order <= 1% of that bar's typical turnover
XRANK_TOP = 0.10
REL_LOOKBACK = 20
MOM_LOOKBACK = 252
REQUIRED = ["ts", "open", "high", "low", "close", "volume"]


def universe() -> list[str]:
    """Symbols with deep 15-minute history AND a daily file for the cost model."""
    syms = []
    for path in sorted(CLEAN.glob("*_15minute.parquet")):
        sym = path.name.replace("_15minute.parquet", "")
        if "NIFTY" in sym or "SENSEX" in sym:
            continue                       # indices are not tradeable here
        if not (CLEAN / f"{sym}_day.parquet").exists():
            continue                       # slippage.profile needs daily bars
        syms.append(sym)
    return syms


def bars_for(symbol: str, tf: str) -> pd.DataFrame:
    """One symbol at one bar size, through the engine's own loaders."""
    if tf == "1d":
        out = frames.daily(symbol)
    elif tf == "15m":
        out = frames.base_15m(symbol)
    else:
        out = frames.resample_intraday(frames.base_15m(symbol), 60)
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise SystemExit(f"{symbol} {tf}: frame lacks {missing}")
    return out.reset_index(drop=True)


def close_panel(symbols, tf, window_first, window_last):
    """Wide (bars x symbols) closes on ONE bar index, for xrank and mktrel.

    Both rules are statements about a stock relative to everything else at the
    same instant, so they cannot be computed one symbol at a time. Trimmed to
    the common window so a symbol's rank is never decided against a different
    set of peers than the rest of the run uses.
    """
    cols = {}
    for sym in symbols:
        b = bars_for(sym, tf)
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
    proxy = (1.0 + bar_ret.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
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


def bar_turnover(bars: pd.DataFrame) -> np.ndarray:
    """Trailing median rupee turnover of one bar, shifted -- point-in-time.

    Mirrors slippage.profile's daily ADV construction, at bar scale. The shift
    matters for the same reason it does there: the bar you are filling in has
    not finished when you place the order, so its own volume is not knowable.
    """
    t = pd.Series(bars["close"].to_numpy(float) * bars["volume"].to_numpy(float))
    return t.rolling(CAP_WINDOW, min_periods=5).median().shift(1).to_numpy(float)


def simulate(symbol, bars, entry, stop_mult, masks, square_off=False):
    """Closed trades for one symbol at one bar size. Port of entries.simulate.

    Deliberately NOT a call into entries.simulate: that function hardcodes
    frames.daily(symbol). Everything else here -- rising-edge entries, the stop
    convention, stop-checked-before-maxhold, the sizer, the spread, the charge
    schedule -- is copied from it so the 1d rows of this file reproduce the
    board's own trade layer.
    """
    if bars.empty or len(bars) < 2:
        return []
    fires = fires_for(symbol, entry, bars, masks)
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = indicators.atr(bars["high"], bars["low"], bars["close"],
                         entries.ATR_LEN).to_numpy(float)
    ts = pd.DatetimeIndex(bars["ts"])
    dates = ts.normalize()
    stamps = ts.tolist()
    turn = bar_turnover(bars)
    total = len(bars)

    out = []
    pos = 0
    while pos < total:
        # RISING EDGE only -- mr and pull stay true for many bars, and without
        # this the account would pay a spread every bar to hold one position.
        if not (fires[pos] and pos > 0 and not fires[pos - 1]):
            pos += 1
            continue
        if stop_mult is None:
            stop = float(lo[pos])
        else:
            if not np.isfinite(atr[pos]):
                pos += 1
                continue                   # inside the ATR warm-up
            stop = float(c[pos]) - stop_mult * float(atr[pos])
        entry_i = pos
        entry_px = float(c[pos])
        if entry_px - stop <= 0:
            pos += 1
            continue                       # nothing to risk, nothing to size

        exit_at = None
        for step in range(pos + 1, total):
            if square_off and dates[step] != dates[entry_i]:
                # The session ended. Out on the last bar that was still in it.
                exit_at = (step - 1, float(c[step - 1]), "square-off")
                break
            if c[step] <= stop:             # stop checked FIRST, as in the board
                exit_at = (step, float(c[step]), "stop (close)")
                break
            if step - entry_i >= MAXHOLD:
                exit_at = (step, float(c[step]), f"{MAXHOLD}-bar limit")
                break
        if exit_at is None:
            break                          # still open at the end of the data
        exit_i, exit_px, why = exit_at
        if exit_i <= entry_i:
            pos = entry_i + 1
            continue                       # square-off on the entry bar itself

        shares, risk_taken, capped = sizing.position(entry_px, stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            pos = exit_i + 1
            continue

        fill_in = slippage.fill(symbol, stamps[entry_i], entry_px, +1)
        fill_out = slippage.fill(symbol, stamps[exit_i], exit_px, -1)
        buy_value = fill_in * shares
        gross = (fill_out - fill_in) * shares
        same = stamps[entry_i].date() == stamps[exit_i].date()
        cost = charges(buy_value, fill_out * shares, intraday=same)
        cap_room = CAP_FRAC * turn[entry_i] if np.isfinite(turn[entry_i]) else np.nan

        out.append({
            "symbol": symbol, "entry_ts": stamps[entry_i], "exit_ts": stamps[exit_i],
            "same_session": same, "shares": shares, "buy_value": buy_value,
            "gross": gross, "cost": cost, "net": gross - cost,
            "risk": (entry_px - stop) * shares, "bars_held": exit_i - entry_i,
            "why": why, "turnover": buy_value + fill_out * shares,
            "cap_room": cap_room,
            "cap_breach": bool(np.isfinite(cap_room) and buy_value > cap_room),
            # Size-neutral version of the same question: forget what the sizer
            # asked for -- could this bar absorb a plain retail order at all?
            "absorbs_5L": bool(np.isfinite(cap_room) and cap_room >= 500_000.0),
        })
        pos = exit_i + 1
    return out


def summarise(trades, family, stop, tf, regime, years):
    """One row per arm.

    R-MULTIPLES ARE NOT COMPARABLE ACROSS TIMEFRAMES and are reported anyway,
    last, for continuity with the board. R divides by entry-minus-stop, and on
    a 15-minute bar the entry candle's low sits a few paise under its close --
    so the SAME rupee spread is 10x more R at 15m than at 1d purely because
    the denominator shrank. The columns to read across timeframes are the bps
    ones: they divide by notional, which is what the costs actually bill on.
    """
    if not trades:
        return {"family": family, "stop": stop, "timeframe": tf, "regime": regime,
                "trades": 0}
    d = pd.DataFrame(trades)
    risk = d["risk"].replace(0.0, np.nan)
    notional = d["buy_value"].sum()
    turnover = d["turnover"].sum()
    gross, cost = d["gross"].sum(), d["cost"].sum()
    per_yr = len(d) / years
    net_bps = 1e4 * (gross - cost) / notional
    over_cap = (d["buy_value"] / d["cap_room"]).replace([np.inf, -np.inf], np.nan)
    return {
        "family": family, "stop": stop, "timeframe": tf, "regime": regime,
        "trades": len(d), "trades_per_yr": float(per_yr),
        # --- comparable across bar sizes: everything below divides by notional
        "gross_bps": float(1e4 * gross / notional),
        "cost_bps": float(1e4 * cost / notional),
        "net_bps": float(net_bps),
        "cost_eats_pct": float(100.0 * cost / gross) if gross > 0 else np.nan,
        "net_pct_yr_full_book": float(net_bps / 1e4 * per_yr * 100.0),
        "cost_pct_turnover": float(100.0 * cost / turnover) if turnover else np.nan,
        "win_pct": float(100.0 * (d["net"] > 0).mean()),
        "same_session_pct": float(100.0 * d["same_session"].mean()),
        "median_bars_held": float(d["bars_held"].median()),
        # --- could this size have been filled at all?
        "cap_breach_pct": float(100.0 * d["cap_breach"].mean()),
        "median_order_x_cap": float(over_cap.median()),
        "pct_bars_absorb_5L": float(100.0 * d["absorbs_5L"].mean()),
        # --- board continuity only; see the docstring
        "gross_R": float((d["gross"] / risk).mean()),
        "net_R": float((d["net"] / risk).mean()),
        "gross_total": float(gross), "cost_total": float(cost),
        "net_total": float(gross - cost),
    }


def main() -> None:
    # Line-buffered so a redirected log is readable while the run is going;
    # block buffering makes a live run indistinguishable from a hang.
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", type=int, default=0, help="use only N symbols")
    ap.add_argument("--families", nargs="*", default=None)
    args = ap.parse_args()

    t0 = time.time()
    syms = universe()
    print(f"universe: {len(syms)} symbols with 15-minute history and a daily file")

    kept, spans = [], []
    for s in syms:
        b = frames.base_15m(s)
        n = pd.DatetimeIndex(b["ts"]).normalize().nunique()
        if n >= MIN_SESSIONS:
            kept.append(s)
            spans.append((pd.DatetimeIndex(b["ts"]).min(), pd.DatetimeIndex(b["ts"]).max()))
    print(f"  before >={MIN_SESSIONS}-session filter: {len(syms)}")
    print(f"  after:                                  {len(kept)}")
    if args.pilot:
        kept = kept[:args.pilot]
        spans = spans[:args.pilot]
        print(f"  PILOT: cut to {len(kept)} symbols")
    if not kept:
        raise SystemExit("no symbols survived the session filter")

    first = min(s[0] for s in spans)
    last = max(s[1] for s in spans)
    years = (last - first).days / 365.25
    print(f"  window: {first.date()} .. {last.date()}  ({years:.1f} years)")

    fams = args.families or FAMILIES
    unknown = [f for f in fams if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"not testable at bar scale: {unknown}")

    # The cost model reads daily bars whatever we trade; the spread is a
    # property of the stock, not of the chart. The SIZE cap is not applied --
    # see the module docstring; cap_breach_pct measures it instead.
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    print(f"  slippage.ENABLED={slippage.ENABLED}  "
          f"MAX_PARTICIPATION={slippage.MAX_PARTICIPATION} (breach measured, not applied)")

    masks_by_tf = {}
    if any(f in PANEL for f in fams):
        print("\nbuilding cross-sectional panels for xrank / mktrel:")
        for tf in TIMEFRAMES:
            masks_by_tf[tf] = panel_masks(close_panel(kept, tf, first, last))

    # SYMBOL-OUTER, ARM-INNER. The natural loop -- one arm at a time over every
    # symbol -- reloads all 131 frames once per arm, 26 times over, and at 15m
    # a frame is ~71,000 bars. This order loads each (symbol, bar size) exactly
    # once and runs all 26 arms against it while it is in hand.
    arms = [(f, sn, sm, tf, rg)
            for f in fams
            for sn, sm in STOPS.items()
            for tf in TIMEFRAMES
            for rg in (["hold"] if tf == "1d" else ["hold", "sqoff"])]
    bucket = {a[:2] + a[3:]: [] for a in arms}
    stamp = date.today().isoformat()
    csv = OUT / "measurements" / f"wf_intraday_{stamp}.csv"
    rows, arm_no = [], 0
    print(f"\nrunning {len(arms)} arms over {len(kept)} symbols:")
    for tf in TIMEFRAMES:
        here = [a for a in arms if a[3] == tf]
        t_tf = time.time()
        for i, sym in enumerate(kept, 1):
            bars = bars_for(sym, tf)
            masks = masks_by_tf.get(tf, {})
            for family, stop_name, stop_mult, _, regime in here:
                bucket[(family, stop_name, tf, regime)] += simulate(
                    sym, bars, family, stop_mult, masks,
                    square_off=(regime == "sqoff"))
            if i % 25 == 0 or i == len(kept):
                print(f"  {tf:<4} {i:>4}/{len(kept)} symbols "
                      f"({(time.time() - t_tf) / 60:.1f} min)")

        # Summarise and FREE this bar size before loading the next one, and
        # checkpoint the rows so far. `bucket` holds every trade of every arm;
        # carrying the 15m trades into the 1h pass is what got the 2026-09-24
        # run killed by the OOM reaper right at the boundary, losing all 12.7
        # minutes of completed 15m work.
        for family, stop_name, _, _, regime in here:
            arm_no += 1
            trades = bucket.pop((family, stop_name, tf, regime))
            row = summarise(trades, family, stop_name, tf, regime, years)
            rows.append(row)
            del trades
            print(f"  [{arm_no:3d}/{len(arms)}] {family:<7} {stop_name:<5} "
                  f"{tf:<4} {regime:<6} trades {row['trades']:>7,}"
                  + (f"  gross {row['gross_bps']:+7.1f}bp  cost "
                     f"{row['cost_bps']:5.1f}bp  net {row['net_bps']:+7.1f}bp"
                     f"  x{row['median_order_x_cap']:.0f} cap"
                     if row["trades"] else ""))
        gc.collect()
        pd.DataFrame(rows).to_csv(csv, index=False)
        print(f"  checkpoint: {len(rows)} of {len(arms)} rows -> {csv}")

    out = pd.DataFrame(rows)
    out.to_csv(csv, index=False)
    print(f"\nwrote {csv}")
    print(f"  {out.shape[0]} rows x {out.shape[1]} columns")
    print(out.head(3).to_string(index=False))
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

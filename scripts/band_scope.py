"""How much of the board's execution touches an NSE price band? SCOPING ONLY.

NEXT_TESTS item 16, the free half. NSE caps a stock's daily move at 20%, 10%
or 5% of the previous close depending on the scrip; a stock that reaches the
cap stops trading at that price for the session. Nothing in this engine knows
a band exists, so a stop placed inside a limit-down move is filled here at a
price the exchange would not have printed.

THE POINT OF THIS SCRIPT IS TO DECIDE WHETHER THE EXPENSIVE HALF IS WORTH
DOING. Modelling a band changes which fills are possible, so it lives in
backtest.py or slippage.py -- both in a stamp tier, both costing a 103-150 min
rebuild, and it additionally needs band data per scrip per day that we have
never fetched. If the board's fills essentially never land on a band day, the
item closes here for nothing.

WE DO NOT HAVE THE BAND ASSIGNMENTS, so the band is INFERRED from the bars:
a day is "band-consistent" when its extreme move off the previous close lands
within TOL of one of 5%, 10% or 20%. That is a guess, and it is labelled as
one everywhere it is printed.

The guess is made falsifiable by a LOCAL DENSITY control, which is the whole
statistical content of this script. A binding band does not merely make 5%
days common -- it makes the move pile up AT EXACTLY 5.00% and go no further.
So for each candidate value v, count the days whose extreme lands in
[v-TOL, v+TOL] and compare that against the same-width windows either side,
at v +/- 0.6pp. Without a band the distribution is smooth there and the
excess is ~0; with one there is a spike.

Comparing 5% against 7% instead -- the first draft of this script -- measures
nothing but the fact that big moves are rarer than small ones. The same
spike test is ALSO run at 7%, 13% and 17%, values no band takes, as a
placebo: those must come back near zero or the detector itself is broken.

THREE QUESTIONS, in increasing order of how much they would actually cost us:

  Q1  Is any fill priced OUTSIDE the day's own printed [low, high]? If a fill
      sits inside the traded range, the exchange printed that price, and a
      band cannot have stopped us trading there at all. This is the question
      that can close the item outright.
  Q2  How many fills land on a band-consistent day, over the placebo rate, and
      in which direction? The asymmetry is what matters: a stop exit on a
      limit-DOWN day is the case where we would have been stuck.
  Q3  How many land on a LOCKED day (high == low, no range whatsoever)? That
      is the unambiguous band signature, and the case where the order could
      not have been filled in size regardless of price.

THE USER'S PINE RULE IS INCLUDED, and it is not on the board. It has no
signal cache, because registering it would put it in a stamp tier; its trades
are rebuilt here through scripts.wf_pine.build_all, which costs ~0.3 min for
the whole universe (the expensive part of wf_pine is the 1,380-cell grid, not
the trade build). Only the DAILY variant is measured -- faithful to the Pine
as written, next-open fills, 1.5*ATR stop. A weekly or monthly fill is stamped
on a session inside an aggregated bar, and a price band is a per-session rule,
so the join would be comparing a fill against the wrong day's range. The
weekly and monthly ladders of NEXT_TESTS item 12 are therefore NOT covered
here.

wf_pine's trades_for already charges the spread itself and keeps the uncharged
prices in quoted_entry/quoted_exit, so wa.spread_of must NOT be called on them
-- that is the double-charge trap of NEXT_TESTS item 10.

Reads:  /data/clean/kitelab/signal_cache/<cache>_all.pkl for the 9 registry
        rules, and /data/clean/kitelab/<SYMBOL>_day.parquet through
        kitelab.frames.daily -- the same cleaned frame the engine trades on.
Writes: output/measurements/band_scope_<N>strat_<date>.csv  (per rule)
        output/measurements/band_scope_days_<N>strat_<date>.csv  (per band)
        output/band_scope_<date>.png
Rebuilds nothing. Edits no stamped module. Run:

        PYTHONPATH=/work/kitelab python3 -m scripts.band_scope [--pilot] [--no-pine]
"""
from __future__ import annotations

import pickle
import sys
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from kitelab import frames, registry, slippage
from kitelab.config import CLEAN
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
import scripts.wf_pine as wp

OUT = Path(__file__).resolve().parent.parent / "output"
CACHE = CLEAN / "signal_cache"

# Canonical NSE bands, and the placebo values used as the false-positive
# control. The placebos must be values no band takes and must sit in the same
# part of the distribution as the real ones, or the control is not a control.
BANDS = [5.0, 10.0, 20.0]
PLACEBOS = [7.0, 13.0, 17.0]

# How close the day's extreme must sit to a band to count as a touch, in
# percentage points of the previous close. 0.15pp is loose enough to survive
# tick rounding (one Rs0.05 tick on a Rs50 stock is 0.10pp) and the corporate
# action rescaling frames.daily applies, and tight enough that the placebo
# rate stays small. The placebo absorbs whatever this choice costs.
TOL = 0.15

REQUIRED_TRADE_COLS = ["symbol", "entry_ts", "exit_ts", "entry_price",
                       "exit_price", "exit_reason", "shares"]
REQUIRED_BAR_COLS = ["ts", "open", "high", "low", "close", "volume"]


def pine_fills(symbols, pilot=False):
    """The user's Heikin Ashi + RSI rule, daily, rebuilt rather than cached.

    Built under the board's own execution globals so the charged prices mean
    the same thing as the nine rules' do. trades_for applies slippage.fill
    itself, so the spread is charged exactly once and quoted_* holds the
    uncharged print.
    """
    if pilot:
        symbols = symbols[:120]
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = dd.REALISTIC_PARTICIPATION
    slippage.reset()
    started = time.time()
    trades = wp.build_all(symbols, side="long", stop_mode="atr", fill="open",
                          tf="D", quiet=True)
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    if not trades:
        raise SystemExit("wf_pine built no trades -- refusing to report a zero")
    rows = []
    for t in trades:
        rows.append(("pine|HA-RSI D", t["symbol"], t["entry_ts"], "entry",
                     t["entry_price"], t["quoted_entry"], t["exit_reason"],
                     t["shares"]))
        rows.append(("pine|HA-RSI D", t["symbol"], t["exit_ts"], "exit",
                     t["exit_price"], t["quoted_exit"], t["exit_reason"],
                     t["shares"]))
    print(f"  {'pine|HA-RSI D':<12} {len(trades):>9,} trades -> "
          f"{2 * len(trades):>9,} fills   (rebuilt in "
          f"{time.time() - started:.1f}s, NOT on the board)")
    return rows


def board_fills(pilot=False, no_pine=False):
    """Every entry and exit the 9 board rules booked, as one long frame.

    Prices are the SPREAD-CHARGED ones, because that is what the board books
    and therefore what has to have been printable. wf_attach.spread_of leaves
    slippage.ENABLED True on the way out, so the globals are restored here --
    this script builds no trades afterwards, but the next reader of this file
    should not have to know that.
    """
    rows = []
    strats = registry.REGISTRY[:2] if pilot else registry.REGISTRY
    for strat in strats:
        path = CACHE / f"{strat.cache}_all.pkl"
        if not path.exists():
            raise SystemExit(f"missing signal cache: {path}")
        blob = pickle.loads(path.read_bytes())
        raw = blob["trades"] if isinstance(blob, dict) else blob
        if not raw:
            raise SystemExit(f"empty signal cache: {path}")
        missing = [c for c in REQUIRED_TRADE_COLS if c not in raw[0]]
        if missing:
            raise SystemExit(f"{strat.cache}: trade records lack {missing}")
        if pilot:
            raw = raw[:20_000]
        charged = wa.spread_of(raw)
        slippage.ENABLED = False
        slippage.MAX_PARTICIPATION = None
        slippage.reset()
        label = f"{strat.key}|{strat.variant}"
        for t, c in zip(raw, charged):
            rows.append((label, t["symbol"], t["entry_ts"], "entry",
                         c["entry_price"], t["entry_price"],
                         t["exit_reason"], t["shares"]))
            rows.append((label, t["symbol"], t["exit_ts"], "exit",
                         c["exit_price"], t["exit_price"],
                         t["exit_reason"], t["shares"]))
        print(f"  {label:<12} {len(raw):>9,} trades -> {2 * len(raw):>9,} fills")

    if not no_pine:
        rows.extend(pine_fills(sorted({r[1] for r in rows}), pilot))

    fills = pd.DataFrame(rows, columns=["rule", "symbol", "ts", "side",
                                        "price", "raw_price", "exit_reason",
                                        "shares"])
    fills["ts"] = pd.to_datetime(fills["ts"]).dt.normalize()
    return fills


def day_table(symbols):
    """Per symbol per session: the previous close, the extremes, the verdicts.

    Read through frames.daily so this sees exactly the bars the engine traded
    on -- the same sanitise(), the same listing-break cut, the same corporate
    action rescaling. Reading the parquet directly would compare fills against
    a frame the engine never saw.
    """
    out, skipped = [], []
    for i, symbol in enumerate(symbols, 1):
        try:
            bars = frames.daily(symbol)
        except SystemExit:
            skipped.append(symbol)
            continue
        if bars.empty:
            skipped.append(symbol)
            continue
        missing = [c for c in REQUIRED_BAR_COLS if c not in bars.columns]
        if missing:
            raise SystemExit(f"{symbol}: daily frame lacks {missing}")
        prev = bars["close"].shift(1)
        frame = pd.DataFrame({
            "symbol": symbol,
            "ts": bars["ts"].dt.normalize(),
            "low": bars["low"].to_numpy(),
            "high": bars["high"].to_numpy(),
            "volume": bars["volume"].to_numpy(),
            "up_pct": (bars["high"] / prev - 1.0) * 100.0,
            "dn_pct": (bars["low"] / prev - 1.0) * 100.0,
        })
        frame["locked"] = bars["high"].to_numpy() == bars["low"].to_numpy()
        out.append(frame)
        if i % 250 == 0:
            print(f"    ... {i:,} of {len(symbols):,} symbols read")
    if skipped:
        print(f"  {len(skipped):,} symbols had no usable daily frame "
              f"(first few: {skipped[:5]})")
    days = pd.concat(out, ignore_index=True)
    # The first session of every symbol has no previous close. Drop it rather
    # than call a NaN "not a band day".
    before = len(days)
    days = days.dropna(subset=["up_pct", "dn_pct"]).reset_index(drop=True)
    print(f"  sessions: {before:,} -> {len(days):,} after dropping the first "
          f"bar of each symbol (no previous close)")
    for value in BANDS + PLACEBOS:
        tag = f"{value:g}"
        days[f"up_{tag}"] = (days["up_pct"] - value).abs() <= TOL
        days[f"dn_{tag}"] = (days["dn_pct"] + value).abs() <= TOL
    days["up_band"] = days[[f"up_{v:g}" for v in BANDS]].any(axis=1)
    days["dn_band"] = days[[f"dn_{v:g}" for v in BANDS]].any(axis=1)
    days["up_placebo"] = days[[f"up_{v:g}" for v in PLACEBOS]].any(axis=1)
    days["dn_placebo"] = days[[f"dn_{v:g}" for v in PLACEBOS]].any(axis=1)
    return days


def pct(n, d):
    return 0.0 if d == 0 else 100.0 * n / d


# Where the control windows sit, in percentage points either side of the
# candidate value. 0.6pp is far enough that a band's own spike (half-width
# TOL = 0.15) cannot leak into the control, and near enough that the smooth
# part of the distribution has barely changed slope.
CONTROL_OFFSET = 0.6


def spike(values, centre):
    """(observed, expected) counts in a narrow window around `centre`.

    The expected count is the mean of two control windows of identical width
    at centre +/- CONTROL_OFFSET, which is a local straight-line estimate of
    what the density would be with no band there. Averaging the two sides is
    what makes it robust to the slope of the distribution -- the density is
    falling steeply at 5% and the one-sided count would be biased by it.
    """
    values = values[np.isfinite(values)]
    def count(at):
        return int(((values >= at - TOL) & (values <= at + TOL)).sum())
    left = count(centre - CONTROL_OFFSET)
    right = count(centre + CONTROL_OFFSET)
    return count(centre), (left + right) / 2.0


def main():
    pilot = "--pilot" in sys.argv
    no_pine = "--no-pine" in sys.argv
    started = time.time()
    print(f"band_scope{'  [PILOT]' if pilot else ''} — "
          f"NSE price bands are INFERRED from bars, never fetched.\n")

    print("1. the board's fills")
    fills = board_fills(pilot, no_pine)
    print(f"\n   fills frame: {len(fills):,} rows x {fills.shape[1]} columns")
    print(fills.head(3).to_string(index=False))
    symbols = sorted(fills["symbol"].unique())
    print(f"   {len(symbols):,} distinct symbols, "
          f"{fills['ts'].min().date()} to {fills['ts'].max().date()}\n")

    print("2. the daily bars behind them")
    days = day_table(symbols)
    print(f"\n   day frame: {len(days):,} rows x {days.shape[1]} columns")
    print(days.head(3).to_string(index=False))

    print("\n3. is there a SPIKE at each candidate value? "
          "(all sessions of the traded universe)")
    print("   observed = days in [v-0.15, v+0.15]; expected = the mean of the "
          "same-width\n   windows at v-0.6 and v+0.6. A binding band shows as "
          "observed >> expected.")
    n_days = len(days)
    base = []
    for value in BANDS + PLACEBOS:
        for direction, col in (("up", "up_pct"), ("down", "dn_pct")):
            obs, exp = spike(days[col].to_numpy(),
                             value if direction == "up" else -value)
            base.append({"move_pct": value,
                         "kind": "band" if value in BANDS else "placebo",
                         "direction": direction,
                         "observed": obs, "expected": exp,
                         "excess": obs - exp,
                         "ratio": (obs / exp) if exp else float("nan"),
                         "pct_of_sessions": pct(obs, n_days)})
    base = pd.DataFrame(base).sort_values(["direction", "move_pct"])
    print(base.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    print("\n4. Q1 — is any fill priced outside the day's printed range?")
    before = len(fills)
    merged = fills.merge(days, on=["symbol", "ts"], how="left")
    print(f"   fills: {before:,} -> {len(merged):,} after the merge "
          f"(one day row per symbol-session, so this must not change)")
    unmatched = int(merged["high"].isna().sum())
    print(f"   {unmatched:,} fills ({pct(unmatched, len(merged)):.3f}%) matched "
          f"no session — dropped from every count below")
    merged = merged.dropna(subset=["high"]).reset_index(drop=True)
    print(f"   fills: {len(merged):,} after the drop")

    tick = 0.05
    n = len(merged)
    for col, what in (("raw_price", "the ENGINE's own fill price"),
                      ("price", "the SPREAD-CHARGED price the board books")):
        above = merged[col] > merged["high"] + tick
        below = merged[col] < merged["low"] - tick
        print(f"   {what}:")
        print(f"      above the day's high: {int(above.sum()):,} "
              f"({pct(above.sum(), n):.3f}%)")
        print(f"      below the day's low:  {int(below.sum()):,} "
              f"({pct(below.sum(), n):.3f}%)")
    merged["above_high"] = merged["price"] > merged["high"] + tick
    merged["below_low"] = merged["price"] < merged["low"] - tick
    merged["outside"] = merged["above_high"] | merged["below_low"]
    merged["raw_outside"] = ((merged["raw_price"] > merged["high"] + tick)
                             | (merged["raw_price"] < merged["low"] - tick))
    print("   (one tick of tolerance. A raw fill outside the printed range is "
          "an ENGINE\n   fault; a charged one outside it is the half-spread "
          "model, which is a\n   modelling choice and not a band question.)")
    if int(merged["raw_outside"].sum()):
        bad = merged[merged["raw_outside"]]
        print("   raw fills outside the range, by side and reason:")
        print(bad.groupby(["side", "exit_reason"]).size()
              .sort_values(ascending=False).head(10).to_string())

    print("\n5. Q2 — the same spike test, on the days the board actually "
          "filled on")
    rows = []
    for side in ("entry", "exit"):
        part = merged[merged["side"] == side]
        for value in BANDS + PLACEBOS:
            for direction, col in (("up", "up_pct"), ("down", "dn_pct")):
                obs, exp = spike(part[col].to_numpy(),
                                 value if direction == "up" else -value)
                rows.append({"side": side, "direction": direction,
                             "move_pct": value,
                             "kind": "band" if value in BANDS else "placebo",
                             "fills": len(part), "observed": obs,
                             "expected": exp, "excess": obs - exp,
                             "excess_pct_of_fills": pct(obs - exp, len(part))})
    q2 = pd.DataFrame(rows)
    print(q2.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    band_excess = q2[q2["kind"] == "band"]["excess"].clip(lower=0).sum()
    plac_excess = q2[q2["kind"] == "placebo"]["excess"].clip(lower=0).sum()
    print(f"\n   total spike excess over all bands:    {band_excess:>9,.0f} fills "
          f"({pct(band_excess, n):.4f}% of all fills)")
    print(f"   the same, over the three placebos:   {plac_excess:>9,.0f} fills "
          f"({pct(plac_excess, n):.4f}%)")

    print("\n   the case that actually costs money: a STOP exit on a "
          "limit-down day")
    exits = int((merged["side"] == "exit").sum())
    stops = merged[(merged["side"] == "exit")
                   & merged["exit_reason"].str.contains("stop", case=False,
                                                        na=False)]
    print(f"   stop exits: {len(stops):,} of {exits:,} exits")
    nb = int(stops["dn_band"].sum()) if len(stops) else 0
    npl = int(stops["dn_placebo"].sum()) if len(stops) else 0
    print(f"   on a day whose low is band-consistent: {nb:,} "
          f"({pct(nb, len(stops)):.4f}%), against {npl:,} "
          f"({pct(npl, len(stops)):.4f}%) at the placebo values")
    print("   (a raw RATE, not the spike test -- big down days are simply "
          "commoner\n   at 5% than at 7%, so read the universe-wide spike "
          "table above for\n   whether a band is binding at all)")
    floor = stops[stops["dn_band"] & (stops["raw_price"] <= stops["low"] + tick)]
    print(f"   sold at the day's bottom tick ON such a day: {len(floor):,} "
          f"({pct(len(floor), len(stops)):.4f}% of stop exits)")
    print("   -- this is the realistic worst case: the engine sells into a "
          "session that\n   reached the floor. The price was printed, so the "
          "sale was possible unless\n   the day was LOCKED, which is the next "
          "section.")
    locked_stops = stops[stops["locked"]]
    print(f"   of the stop exits, on a LOCKED day: {len(locked_stops):,} "
          f"({pct(len(locked_stops), len(stops)):.4f}%) "
          f"-- these are the sales that could NOT have happened")

    print("\n6. Q3 — locked days (high == low), the unambiguous signature")
    locked = merged[merged["locked"]]
    print(f"   fills on a locked day: {len(locked):,} ({pct(len(locked), n):.4f}%)")
    if len(locked):
        at_band = locked["up_band"] | locked["dn_band"]
        print(f"   of those, locked AT a band value: {int(at_band.sum()):,} "
              f"({pct(at_band.sum(), n):.4f}% of all fills)")
        print("   -- the rest are simply ILLIQUID: one price printed all day "
              "and no band\n   was involved. That is a liquidity question, not "
              "this item's.")
        print(f"   median volume on a locked fill day: "
              f"{locked['volume'].median():,.0f} shares, against "
              f"{merged['volume'].median():,.0f} on all fill days")

    print("\n   MONEY-WEIGHTED, which is the bound that decides this item. A "
          "count of\n   fills understates it if the impossible fills are the "
          "big ones.")
    merged["notional"] = merged["price"] * merged["shares"]
    total_notional = merged["notional"].sum()
    for name, mask in (
            ("on a locked day", merged["locked"]),
            ("locked AT a band value", merged["locked"] & (merged["up_band"]
                                                           | merged["dn_band"])),
            ("stop exit, locked at a band",
             merged["locked"] & merged["dn_band"] & (merged["side"] == "exit")
             & merged["exit_reason"].str.contains("stop", case=False, na=False)),
    ):
        part = merged[mask]
        print(f"   {name:<28} {len(part):>8,} fills  "
              f"{pct(len(part), n):>7.4f}% by count  "
              f"{pct(part['notional'].sum(), total_notional):>7.4f}% by value")

    print("\n7. per rule. band_pct and placebo_pct are RAW RATES and are not "
          "comparable\n   to each other (5% days are commoner than 7% days "
          "for reasons unrelated to\n   bands); the columns that carry the "
          "answer are locked_pct and raw_outside.")
    per = merged.groupby("rule").agg(
        fills=("price", "size"),
        band_days=("up_band", "sum"), band_days_dn=("dn_band", "sum"),
        plac_days=("up_placebo", "sum"), plac_days_dn=("dn_placebo", "sum"),
        locked=("locked", "sum"), raw_outside=("raw_outside", "sum"),
        charged_outside=("outside", "sum")).reset_index()
    per["band_pct"] = 100.0 * (per["band_days"] + per["band_days_dn"]) / per["fills"]
    per["placebo_pct"] = 100.0 * (per["plac_days"] + per["plac_days_dn"]) / per["fills"]
    per["locked_pct"] = 100.0 * per["locked"] / per["fills"]
    per["charged_outside_pct"] = 100.0 * per["charged_outside"] / per["fills"]
    cols = ["rule", "fills", "band_pct", "placebo_pct", "locked", "locked_pct",
            "raw_outside", "charged_outside_pct"]
    print(per[cols].to_string(index=False, float_format=lambda v: f"{v:,.4f}"))

    stamp = date.today().isoformat()
    n_rules = len(registry.REGISTRY)
    meas = OUT / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    per_path = meas / f"band_scope_{n_rules}strat_{stamp}.csv"
    day_path = meas / f"band_scope_days_{n_rules}strat_{stamp}.csv"
    per.to_csv(per_path, index=False)
    base.to_csv(day_path, index=False)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, col, title in ((axes[0], "up_pct", "up-move: high vs previous close"),
                           (axes[1], "dn_pct", "down-move: low vs previous close")):
        vals = merged[col].to_numpy()
        vals = vals[np.isfinite(vals)]
        vals = vals[(vals > -25) & (vals < 25)]
        ax.hist(vals, bins=500, color="#4C72B0")
        for value in BANDS:
            ax.axvline(value if col == "up_pct" else -value, color="#C44E52",
                       lw=1, label="NSE band" if value == 5.0 else None)
        for value in PLACEBOS:
            ax.axvline(value if col == "up_pct" else -value, color="#999999",
                       lw=1, ls="--", label="placebo" if value == 7.0 else None)
        ax.set_title(title)
        ax.set_xlabel("percent of previous close")
        ax.set_ylabel("board fills")
        ax.set_yscale("log")
        ax.legend()
    fig.suptitle(f"Board fills against inferred NSE price bands "
                 f"({n_rules} rules, {n:,} fills) — bands INFERRED, not fetched")
    fig.tight_layout()
    png = OUT / f"band_scope_{stamp}.png"
    fig.savefig(png, dpi=130)
    plt.close(fig)

    print(f"\nwrote {per_path}")
    print(f"wrote {day_path}")
    print(f"wrote {png}")
    print(f"elapsed {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()

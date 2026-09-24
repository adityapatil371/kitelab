"""TAILORED STOPS: does setting the stop from the stock's own volatility help?

WHY THIS EXISTS. `scripts/waterfall.py` found that our entry rules are worth
nothing once the stop is removed from both sides (+0.06 US, -1.08 NSE), and
that the stop costs random timing -12.75 points a year against the rule's
-3.45. The rules do not pick better entries; they pick entries the stop
damages less. If that is true, the stop is the lever, not the entry.

THE TRAP, AND WHY THIS SCRIPT IS SHAPED THE WAY IT IS. This project has
already measured that stop WIDTH reshuffles the board (Spearman 0.152 between
the own-low and 3-ATR arms), and that selecting on our own criterion scores
PBO 0.412 -- worse than a coin flip. So "try every stop, keep the best per
rule" would manufacture a number that evaporates. Every stop here is therefore
defined by a FIXED FORMULA WRITTEN IN ADVANCE that reads only price
volatility, never returns, and every arm is reported together. Nothing is
chosen by score. (Memory: kitelab-rule-repair-derives-exits.)

THE FOUR STOPS. All are a distance below the entry close.

  own    the entry bar's own low                    board baseline
  atr3   3 x ATR(14)                                board baseline
  blend  3 x sqrt(ATR(14) x ATR(100))               volatility-state aware
  norm   k_f x ATR(14), k_f set PER RULE so that    per-rule, returns-blind
         rule's median stop distance (% of price)
         matches a common target

`blend` is the geometric mean of what the stock is doing NOW and what it
normally does. A rule that fires during a volatility spike gets a stop pulled
in from 3x current ATR; one that fires in a lull gets a wider one. It has no
free parameter and no per-rule fitting at all -- rules differ under it only
because they systematically fire in different volatility states.

`norm` is the explicitly per-rule arm. Rules whose entries land in jumpy
moments get a smaller multiple and rules that land in quiet ones get a larger
multiple, so that every rule is risking the same PERCENTAGE of price. k_f is
computed from entry-day volatility alone. To keep even that honest it is
fitted on the FIRST half of the calendar and applied to the whole run, so the
second half never informed the number that is scored on it.

READS:  CLEAN/US_<SYM>_day.parquet and CLEAN/<SYM>_day.parquet
WRITES: output/measurements/stop_tailor_<date>.csv
        output/measurements/stop_tailor_steps_<date>.csv
        output/figures/stop_tailor_<date>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib                                              # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                # noqa: E402

from kitelab import entries, indicators                        # noqa: E402
from scripts import waterfall as wf                            # noqa: E402
from scripts.us_rules import load, us_universe, nse_universe   # noqa: E402
from scripts.wf_intraday import FAMILIES                       # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output"

SLOW_LEN = 100        # "what this stock normally does"
BASE_MULT = 3.0       # the board's own multiple; NOT tuned here

# filled by fit_k(); family -> ATR multiple for the `norm` stop
K_BY_FAMILY: dict[str, float] = {}


def _atrs(bars):
    """Fast ATR(14) and slow ATR(100), aligned to the bar index."""
    fast = indicators.atr(bars["high"], bars["low"], bars["close"],
                          entries.ATR_LEN).to_numpy(float)
    slow = indicators.atr(bars["high"], bars["low"], bars["close"],
                          SLOW_LEN).to_numpy(float)
    return fast, slow


def stop_blend(bars, family):
    """3 x sqrt(fast ATR x slow ATR), as a price distance.

    Tighter than 3x ATR when the stock is jumpier than usual, wider when it is
    calmer. No free parameter, identical for every rule.
    """
    fast, slow = _atrs(bars)
    with np.errstate(invalid="ignore"):
        return BASE_MULT * np.sqrt(fast * slow)


def stop_norm(bars, family):
    """k_f x ATR(14), with k_f fitted per rule by fit_k() on the first half."""
    fast, _ = _atrs(bars)
    return K_BY_FAMILY.get(family, BASE_MULT) * fast


STOPS = {"own": None, "atr3": BASE_MULT,
         "blend": stop_blend, "norm": stop_norm}


def fit_k(bars_by_uni, fams, masks_by_uni, cut):
    """Set k_f so every rule risks the same PERCENTAGE of price.

    For each rule, collect ATR(14)/close on the days it fires, using only bars
    before `cut`. The board's atr3 stop risks 3 x that ratio; the target is the
    median of those medians across all rules, and k_f is whatever multiple puts
    this rule on the target. Returns are never read -- only where the rule
    fires and how volatile the stock was there.
    """
    ratio = {}
    for f in fams:
        vals = []
        for tag, bars in bars_by_uni.items():
            for s, b in bars.items():
                b = b[pd.DatetimeIndex(b["ts"]) < cut]
                if len(b) < 200:
                    continue
                fires = wf.fires_for(s, f, b, masks_by_uni[tag])
                if not fires.any():
                    continue
                fast, _ = _atrs(b)
                c = b["close"].to_numpy(float)
                with np.errstate(invalid="ignore", divide="ignore"):
                    r = fast / c
                v = r[fires & np.isfinite(r)]
                if v.size:
                    vals.append(v)
        ratio[f] = float(np.median(np.concatenate(vals))) if vals else np.nan
    target = float(np.nanmedian(list(ratio.values())))
    out = {f: (BASE_MULT * target / v if np.isfinite(v) and v > 0 else BASE_MULT)
           for f, v in ratio.items()}
    print(f"\n  fitted on bars before {cut.date()}; target stop distance "
          f"{BASE_MULT * target * 100:.2f}% of price")
    print(f"  {'rule':<8} {'ATR/price at entry':>19} {'k_f':>7} "
          f"{'stop dist %':>12}")
    for f in fams:
        print(f"  {f:<8} {ratio[f] * 100:>18.3f}% {out[f]:>7.2f} "
              f"{out[f] * ratio[f] * 100:>11.2f}%")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", nargs="*", default=FAMILIES)
    ap.add_argument("--capital", type=float, default=1e7)
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--start", default=None)
    args = ap.parse_args()
    t0 = time.time()

    fams = args.families
    bad = [f for f in fams if f not in FAMILIES]
    if bad:
        sys.exit(f"unknown families {bad}; known: {FAMILIES}")
    start = pd.Timestamp(args.start) if args.start else None

    # --- load once, for the k fit; run_universe loads again for the run ---
    books = {"US": us_universe(), "NSE": nse_universe(144)}
    if args.pilot:
        books = {k: v[:args.pilot] for k, v in books.items()}
    bars_by_uni, masks_by_uni = {}, {}
    for tag, syms in books.items():
        d = {}
        for s in syms:
            b = load(s)
            if b is None:
                continue
            if start is not None:
                b = b[pd.DatetimeIndex(b["ts"]) >= start]
            if len(b) < 200:
                continue
            d[s] = b
        bars_by_uni[tag] = d
        print(f"{tag}: {len(d)} of {len(syms)} symbols loaded for the k fit")
        if not d:
            sys.exit(f"{tag}: no symbols loaded")
        first = min(pd.DatetimeIndex(b["ts"])[0] for b in d.values())
        last = max(pd.DatetimeIndex(b["ts"])[-1] for b in d.values())
        print(f"  {first.date()} .. {last.date()}")
        masks_by_uni[tag] = {}
        if any(f in wf.PANEL for f in fams):
            panel = wf.close_panel(list(d), "1d", first, last)
            masks_by_uni[tag] = wf.panel_masks(panel)

    allfirst = min(pd.DatetimeIndex(b["ts"])[0]
                   for d in bars_by_uni.values() for b in d.values())
    alllast = max(pd.DatetimeIndex(b["ts"])[-1]
                  for d in bars_by_uni.values() for b in d.values())
    cut = allfirst + (alllast - allfirst) / 2
    K_BY_FAMILY.update(fit_k(bars_by_uni, fams, masks_by_uni, cut))
    del bars_by_uni, masks_by_uni

    # --- run the waterfall engine with the four stops ---
    wf.STOPS = STOPS
    out = pd.concat([wf.run_universe(t, s, fams, args.capital, t0, start)
                     for t, s in books.items()], ignore_index=True)
    print(f"\nrows: {len(out)}")

    stamp = date.today().isoformat() + (f"_from{args.start}" if args.start else "")
    (OUT / "measurements").mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "measurements" / f"stop_tailor_{stamp}.csv", index=False)

    # --- the comparison, per stop ---
    key = ["universe", "family", "stop"]
    piv = out.pivot_table(index=key, columns="rung", values="cagr_pct")
    dd = out.pivot_table(index=key, columns="rung", values="max_dd_pct")
    slots_hi = f"D0 rule,   + costs, {wf.SLOTS[0]:>2} slots"
    rand_hi = f"E  random, + costs, {wf.SLOTS[0]:>2} slots"
    steps = pd.DataFrame({
        "rule minus buy and hold": piv[slots_hi] - piv["F  buy and hold"],
        "rule minus matched random": piv[slots_hi] - piv[rand_hi],
        "entry worth, stop on both": (piv["B  rule,   no cost, unlimited"]
                                      - piv["A  random, no cost, unlimited"]),
        "entry worth, NO stop either side":
            (piv["X  rule,   no cost, NO STOP"]
             - piv["A0 random, no cost, NO STOP"]),
        "the stop costs the rule": (piv["B  rule,   no cost, unlimited"]
                                    - piv["X  rule,   no cost, NO STOP"]),
        "the stop costs random timing":
            (piv["A  random, no cost, unlimited"]
             - piv["A0 random, no cost, NO STOP"]),
        "rule drawdown minus hold's": (dd[slots_hi] - dd["F  buy and hold"]),
    }).reset_index()
    steps.to_csv(OUT / "measurements" / f"stop_tailor_steps_{stamp}.csv",
                 index=False)

    for tag in books:
        s_ = steps[steps.universe == tag]
        print(f"\n=== {tag}: every stop, side by side "
              f"({s_.stop.nunique()} stops x {s_.family.nunique()} rules) ===")
        print(f"  {'layer':<36} " + "".join(f"{k:>10}" for k in STOPS))
        for col in steps.columns[3:]:
            line = f"  {col:<36} "
            for k in STOPS:
                v = s_[s_.stop == k][col].dropna()
                line += f"{v.median():>+10.2f}" if len(v) else f"{'-':>10}"
            print(line)
        print(f"  {'(rules beating hold, of ' + str(s_.family.nunique()) + ')':<36} "
              + "".join(
                  f"{int((s_[s_.stop == k]['rule minus buy and hold'] > 0).sum()):>10}"
                  for k in STOPS))

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, tag in zip(axes, books):
        s_ = steps[steps.universe == tag]
        for i, k in enumerate(STOPS):
            v = s_[s_.stop == k]["rule minus buy and hold"].dropna()
            ax.bar(i, v.median(), color="steelblue")
            ax.scatter([i] * len(v), v, s=8, color="black", alpha=0.45, zorder=3)
        ax.set_xticks(range(len(STOPS)))
        ax.set_xticklabels(list(STOPS))
        ax.axhline(0, color="crimson", lw=1.2)
        ax.set_title(f"{tag}: rule minus buy and hold, by stop")
        ax.grid(alpha=0.3, axis="y")
    axes[0].set_ylabel("points of CAGR per year")
    fig.tight_layout()
    png = OUT / "figures" / f"stop_tailor_{stamp}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}")
    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

"""Does VOLATILITY TARGETING make leverage survivable?

THE IDEA. `scripts/wf_leverage.py` tested FIXED leverage and it was a rout:
the growth-maximising multiple was below 1.0 in 61 of 72 cells, and at 2x
forty-three of seventy-two accounts were wiped out. But fixed leverage is the
naive version. It carries the LEAST risk when risk is cheap and the MOST when
risk is lethal, because the multiple never moves.

Volatility targeting inverts that: size INVERSELY to how turbulent the market
has been lately, so the account aims at a constant risk rather than a constant
multiple. The reason this is not wishful thinking is that volatility is one of
the few quantities in markets that genuinely persists -- a wild week follows a
wild week far more often than chance, while returns do not persist at all. So
the exposure is steered by something forecastable.

WHAT IS SCORED. Not CAGR alone. The user's stated goal is a shorter or safer
path to a similar place -- so the table reports worst drawdown, the longest
stretch spent underwater (in years), and return per unit of drawdown (Calmar),
alongside growth. A rule that earns less but recovers faster can win here.

HONESTY ABOUT THE PORTFOLIO. This is an equal-weight basket of the symbols
listed on each day, rebalanced daily, no costs on the basket itself. That is
not tradeable as-is; it is a clean market proxy for asking whether the SIZING
overlay helps. Financing on the borrowed fraction IS charged (--fin, default
9%/yr, Indian MTF territory), because that is the cost that decides leverage.
Ruin is absorbing: once equity <= 0 the account stays dead.

READS:  CLEAN/US_<SYM>_day.parquet and CLEAN/<SYM>_day.parquet
WRITES: output/measurements/vol_target_<date>.csv
        output/figures/vol_target_<date>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitelab import frames  # noqa: E402
from scripts.us_rules import us_universe, nse_universe, MIN_SESSIONS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output"
SESSIONS = 252
VOL_WINDOW = 60            # sessions of realised vol behind the sizing
MAX_DAY = 5.00             # sanity guard, see basket()
FIXED = [1.0, 1.5, 2.0, 3.0]
TARGETS = [0.10, 0.15, 0.20, 0.25]
WINDOWS = [20, 60, 120]
LMAX = 3.0                 # a broker will not fund more than this


def basket(syms, tag) -> pd.Series:
    """Equal-weight daily return of everything listed that day."""
    cols, dropped = {}, []
    for s in syms:
        try:
            b = frames.daily(s)
        except Exception:                      # noqa: BLE001 loader loop
            continue
        if b is None or b.empty or len(b) < MIN_SESSIONS:
            continue
        r = pd.Series(b["close"].to_numpy(float),
                      index=pd.DatetimeIndex(b["ts"])).sort_index()
        r = r[~r.index.duplicated(keep="last")].pct_change(fill_method=None)
        # Sanity guard. CLEAN carries a few non-equity series -- CRUDEOIL is a
        # commodity contract whose unit definition changed, giving it a single
        # day of +132,300%. One such row dominates an equal-weight basket and
        # drove the first run's NSE volatility to 274%/yr. The threshold is
        # deliberately far out at +500%: real equities DO move violently
        # (Morgan Stanley +87% on 2008-10-13 is a genuine print, and a tighter
        # guard silently deleted it along with AIG and four others). Only a
        # move no price can make is treated as a data defect.
        if float(r.abs().max()) > MAX_DAY:
            dropped.append((s, float(r.abs().max()) * 100))
            continue
        cols[s] = r
    for sym, mv in dropped:
        print(f"    DROPPED {sym}: largest single-day move {mv:,.0f}%")
    wide = pd.DataFrame(cols)
    print(f"  {tag}: {wide.shape[1]} symbols kept ({len(dropped)} dropped), {wide.shape[0]:,} sessions "
          f"({wide.index.min().date()} .. {wide.index.max().date()})")
    n = wide.notna().sum(axis=1)
    out = wide.mean(axis=1, skipna=True)
    before = len(out)
    out = out[n >= 5].dropna()
    print(f"    sessions with >=5 listed names: {before:,} -> {len(out):,}")
    print(f"    mean {out.mean()*1e4:+.2f} bps/day, "
          f"sd {out.std()*np.sqrt(SESSIONS)*100:.1f}%/yr")
    return out


def path_stats(r: pd.Series, lev: np.ndarray, fin: float) -> dict:
    """Walk the account. Leverage multiplies the day's return; financing is
    charged on the borrowed fraction (L-1), daily. Ruin is absorbing."""
    daily_fin = fin / SESSIONS
    eq, equity, dead = 1.0, [], False
    for x, L in zip(r.to_numpy(float), lev):
        if dead:
            equity.append(0.0)
            continue
        eq *= (1.0 + L * x - max(L - 1.0, 0.0) * daily_fin)
        if eq <= 0.0:
            eq, dead = 0.0, True
        equity.append(eq)
    e = pd.Series(equity, index=r.index)
    peak = e.cummax()
    dd = e / peak - 1.0
    under = (e < peak).astype(int)
    # longest consecutive underwater run, in sessions
    grp = (under != under.shift()).cumsum()
    runs = under.groupby(grp).sum()
    longest = int(runs.max()) if len(runs) else 0
    yrs = len(e) / SESSIONS
    cagr = (e.iloc[-1] ** (1 / yrs) - 1.0) * 100.0 if e.iloc[-1] > 0 else -100.0
    vol = float((r * lev).std() * np.sqrt(SESSIONS) * 100.0)
    mdd = float(dd.min() * 100.0)
    return {"cagr_pct": float(cagr), "vol_pct": vol, "max_dd_pct": mdd,
            "longest_underwater_yrs": longest / SESSIONS,
            "calmar": float(cagr / abs(mdd)) if mdd < 0 else np.nan,
            "ruined": bool(dead), "final_x": float(e.iloc[-1])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fin", type=float, nargs="*", default=[0.0, 0.09],
                    help="financing rates on borrowed money, per year")
    args = ap.parse_args()
    t0 = time.time()

    print("building equal-weight baskets:")
    us = us_universe()
    if not us:
        sys.exit("no US_*_day.parquet in CLEAN -- run scripts.us_fetch first")
    books = {"US": basket(us, "US"), "NSE": basket(nse_universe(len(us)), "NSE")}

    rows = []
    for tag, r in books.items():
        base = float(r.std() * np.sqrt(SESSIONS))
        for fin in args.fin:
            rows.append({"universe": tag, "fin": fin, "arm": "hold", "target": np.nan,
                         "window": np.nan, "median_leverage": 1.0,
                         **path_stats(r, np.full(len(r), 1.0), fin)})
            for L in FIXED[1:]:
                rows.append({"universe": tag, "fin": fin, "arm": f"fixed {L:g}x",
                             "target": np.nan, "window": np.nan, "median_leverage": L,
                             **path_stats(r, np.full(len(r), L), fin)})
            for w in WINDOWS:
                rv = r.rolling(w).std().shift(1) * np.sqrt(SESSIONS)
                for tgt in TARGETS:
                    lev = (tgt / rv).clip(upper=LMAX).fillna(1.0).to_numpy(float)
                    v = path_stats(r, lev, fin)
                    # The only fair yardstick: a FIXED multiple dialled to the
                    # exact same realised volatility. Comparing a vol-targeted
                    # account to plain hold confounds the sizing rule with the
                    # amount of risk taken; this does not.
                    L = v["vol_pct"] / 100.0 / base
                    f = path_stats(r, np.full(len(r), L), fin)
                    rows.append({"universe": tag, "fin": fin, "arm": "voltarget",
                                 "target": tgt, "window": w,
                                 "median_leverage": float(np.median(lev)), **v,
                                 "matched_fixed_L": L,
                                 "matched_fixed_cagr_pct": f["cagr_pct"],
                                 "matched_fixed_dd_pct": f["max_dd_pct"],
                                 "matched_fixed_uw_yrs": f["longest_underwater_yrs"],
                                 "d_cagr_vs_matched": v["cagr_pct"] - f["cagr_pct"],
                                 "d_dd_vs_matched": v["max_dd_pct"] - f["max_dd_pct"]})

    out = pd.DataFrame(rows)
    stamp = date.today().isoformat()
    csv = OUT / "measurements" / f"vol_target_{stamp}.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(csv, index=False)
    print(f"\nwrote {csv}")
    print(f"  {out.shape[0]} rows x {out.shape[1]} columns")
    print(out.head(3).to_string(index=False))

    show = ["arm", "target", "window", "median_leverage", "cagr_pct", "vol_pct",
            "max_dd_pct", "longest_underwater_yrs", "calmar"]
    for tag in books:
        for fin in args.fin:
            s = out[(out.universe == tag) & (out.fin == fin)]
            print(f"\n=== {tag}  financing {fin:.0%} ===")
            print(s[show].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
            v = s[s.arm == "voltarget"]
            print(f"  vs a fixed multiple at the SAME volatility: "
                  f"more growth in {(v.d_cagr_vs_matched > 0).sum()} of {len(v)} arms, "
                  f"shallower drawdown in {(v.d_dd_vs_matched > 0).sum()} of {len(v)}")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, tag in zip(axes, books):
        for fin, mark in zip(args.fin, ["o", "s"]):
            s = out[(out.universe == tag) & (out.fin == fin) & (out.arm == "voltarget")]
            ax.scatter(s.vol_pct, s.d_cagr_vs_matched, marker=mark, s=45,
                       label=f"financing {fin:.0%}")
        ax.axhline(0, color="black", lw=1)
        ax.set_title(f"{tag}: vol targeting minus fixed leverage\nat identical volatility")
        ax.set_xlabel("realised volatility, %/yr")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("CAGR advantage, points/yr")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    png = OUT / "figures" / f"vol_target_{stamp}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}")
    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

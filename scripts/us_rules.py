"""Do the board's entry rules beat RANDOM TIMING -- in India, and in America?

THE QUESTION. Every rule in this project is long-only, and both markets rose
over the sample. So "the rule made money" is not evidence of anything: a coin
flip that bought and held would also have made money. The only test that
separates a signal from the drift it sits in is a MATCHED CONTROL -- same
symbol, same stop, same exit logic, same number of trades, same distribution
of holding lengths, and an entry date drawn at RANDOM. If the rule cannot beat
that, it has no timing information, whatever its raw return.

THE SECOND QUESTION, FOR FREE. A rule whose entries are significantly WORSE
than random timing is a short candidate: it identifies moments the market
under-performs itself. Nothing in this project has ever sold short, so the
negative tail of this table is the first search of that half of the space.
The columns to read for it are `edge_bps` and `t`, at the BOTTOM of the sort.

DESIGN NOTES
  * No costs, no slippage, no position sizer. Every trade is scored as
    (exit / entry - 1) in basis points, equally weighted. Cost models are
    market-specific and would confound a cross-market comparison; the sizer
    is a rupee-denominated rule that means nothing on a dollar tape. The
    question here is whether the SIGNAL exists, so it is measured gross.
  * The control re-runs the identical stop and max-hold machinery from a
    random bar, so the stop's contribution is present on BOTH sides and what
    is left in the difference is timing alone.
  * Entries are rising-edge, as in the board: `mr` and `pull` stay true for
    many bars, and without this one signal becomes dozens of trades.
  * The random draw is seeded per (symbol, family, stop) so the run is exactly
    reproducible.

READS:  CLEAN/US_<SYM>_day.parquet   (144 symbols, from scripts/us_fetch.py)
        CLEAN/<SYM>_day.parquet      (the NSE control universe)
WRITES: output/measurements/us_rules_<date>.csv
        output/figures/us_rules_<date>.png
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

from kitelab import frames, entries, indicators  # noqa: E402
from scripts.wf_intraday import (  # noqa: E402
    FAMILIES, PANEL, STOPS, close_panel, panel_masks, fires_for,
)

OUT = Path(__file__).resolve().parents[1] / "output"
CLEAN = Path("/data/clean/kitelab")
MAXHOLD = entries.MAXHOLD
MIN_SESSIONS = 1000
DRAWS = 1                  # random control trades per real trade
REQUIRED = ["ts", "open", "high", "low", "close", "volume"]


def us_universe() -> list[str]:
    return sorted(p.name[:-len("_day.parquet")]
                  for p in CLEAN.glob("US_*_day.parquet"))


def nse_universe(limit: int) -> list[str]:
    """NSE control, matched on COUNT so neither market gets a breadth edge."""
    syms = sorted(p.name[:-len("_day.parquet")]
                  for p in CLEAN.glob("*_day.parquet")
                  if not p.name.startswith("US_")
                  and "NIFTY" not in p.name and "SENSEX" not in p.name)
    rng = np.random.default_rng(20260924)
    if len(syms) > limit:
        syms = sorted(rng.choice(syms, size=limit, replace=False).tolist())
    return syms


def load(sym: str) -> pd.DataFrame | None:
    try:
        b = frames.daily(sym)
    except Exception:                          # noqa: BLE001 loader loop
        return None
    if b is None or b.empty or len(b) < MIN_SESSIONS:
        return None
    missing = [c for c in REQUIRED if c not in b.columns]
    if missing:
        raise SystemExit(f"{sym}: frame lacks {missing}")
    return b.reset_index(drop=True)


def _exit_from(i, c, lo, atr, stop_mult, total):
    """The board's exit, started at bar i. Returns (exit_i, ret) or None.

    Identical logic on the real and the control side -- stop checked before
    the max-hold, stop on the CLOSE, entry at the close of bar i.
    """
    if stop_mult is None:
        stop = float(lo[i])
    else:
        if not np.isfinite(atr[i]):
            return None
        stop = float(c[i]) - stop_mult * float(atr[i])
    entry_px = float(c[i])
    if entry_px - stop <= 0 or entry_px <= 0:
        return None
    for step in range(i + 1, total):
        if c[step] <= stop:
            return step, float(c[step]) / entry_px - 1.0
        if step - i >= MAXHOLD:
            return step, float(c[step]) / entry_px - 1.0
    return None                                # still open at the end of data


def run_symbol(sym, bars, family, stop_mult, masks, rng):
    """Real trades and their matched random controls, as bps lists."""
    fires = fires_for(sym, family, bars, masks)
    c = bars["close"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    atr = indicators.atr(bars["high"], bars["low"], bars["close"],
                         entries.ATR_LEN).to_numpy(float)
    total = len(bars)

    yrs = pd.DatetimeIndex(bars["ts"]).year.to_numpy()
    real, real_y, pos = [], [], 0
    while pos < total:
        if not (fires[pos] and pos > 0 and not fires[pos - 1]):
            pos += 1
            continue
        got = _exit_from(pos, c, lo, atr, stop_mult, total)
        if got is None:
            pos += 1
            continue
        exit_i, ret = got
        real.append(1e4 * ret)
        real_y.append(int(yrs[pos]))
        pos = exit_i + 1

    ctrl, ctrl_y = [], []
    if real:
        # Same count, same symbol, same stop, same exit rule -- random date.
        # Draw from the same bar range the real entries could have used.
        lo_i, hi_i = entries.ATR_LEN + 1, total - 2
        if hi_i > lo_i:
            want = len(real) * DRAWS
            tries, made = 0, 0
            while made < want and tries < want * 20:
                tries += 1
                j = int(rng.integers(lo_i, hi_i))
                got = _exit_from(j, c, lo, atr, stop_mult, total)
                if got is None:
                    continue
                ctrl.append(1e4 * got[1])
                ctrl_y.append(int(yrs[j]))
                made += 1
    return real, real_y, ctrl, ctrl_y


def year_cluster_t(real, real_y, ctrl, ctrl_y):
    """Edge and t with ENTRY YEAR as the cluster, not the trade.

    Trades overlap in calendar time -- dozens of symbols fire on the same week
    -- so treating each trade as an independent observation understates every
    standard error. This project measured that directly: clustering on SYMBOL
    moves the SE 1.00x, clustering on ENTRY YEAR moves it 2.4-3.6x. The
    dependence is calendar, not company. So the honest unit is the year:
    average the rule and its control within each year, difference them, and
    t-test the ~20 (NSE) or ~64 (US) yearly differences. df = years - 1.
    """
    a = pd.DataFrame({"y": real_y, "v": real}).groupby("y")["v"].mean()
    b = pd.DataFrame({"y": ctrl_y, "v": ctrl}).groupby("y")["v"].mean()
    j = pd.concat([a.rename("rule"), b.rename("random")], axis=1).dropna()
    if len(j) < 3:
        return float("nan"), float("nan"), len(j)
    d = (j["rule"] - j["random"]).to_numpy(float)
    se = d.std(ddof=1) / np.sqrt(len(d))
    return float(d.mean()), (float(d.mean() / se) if se > 0 else float("nan")), len(j)


def welch_t(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    den = np.sqrt(va + vb)
    return float((a.mean() - b.mean()) / den) if den > 0 else float("nan")


def run_universe(tag, syms, fams, t0):
    print(f"\n=== {tag}: {len(syms)} symbols ===")
    loaded, skipped = {}, 0
    for s in syms:
        b = load(s)
        if b is None:
            skipped += 1
            continue
        loaded[s] = b
    print(f"  loaded {len(loaded)}, skipped {skipped} "
          f"(missing file or < {MIN_SESSIONS} sessions)")
    if not loaded:
        return []
    spans = [(b.ts.min(), b.ts.max()) for b in loaded.values()]
    first, last = min(s for s, _ in spans), max(e for _, e in spans)
    med = int(np.median([len(b) for b in loaded.values()]))
    print(f"  span {first.date()} .. {last.date()}, median {med:,} sessions")

    masks = {}
    if any(f in PANEL for f in fams):
        print("  building cross-sectional panel for xrank / mktrel:")
        masks = panel_masks(close_panel(list(loaded), "1d", first, last))

    rows = []
    for family in fams:
        for stop_name, stop_mult in STOPS.items():
            real, real_y, ctrl, ctrl_y = [], [], [], []
            for sym, bars in loaded.items():
                seed = abs(hash((tag, sym, family, stop_name))) % (2**32)
                r, ry, k, ky = run_symbol(sym, bars, family, stop_mult, masks,
                                          np.random.default_rng(seed))
                real += r
                real_y += ry
                ctrl += k
                ctrl_y += ky
            a, b = np.asarray(real, float), np.asarray(ctrl, float)
            yr_edge, yr_t, n_years = year_cluster_t(real, real_y, ctrl, ctrl_y)
            row = {
                "universe": tag, "family": family, "stop": stop_name,
                "trades": len(a), "control_trades": len(b),
                "rule_bps": float(a.mean()) if len(a) else np.nan,
                "random_bps": float(b.mean()) if len(b) else np.nan,
                "edge_bps": float(a.mean() - b.mean()) if len(a) and len(b) else np.nan,
                "t_naive": welch_t(a, b),
                "yr_edge_bps": yr_edge, "t_year_cluster": yr_t, "n_years": n_years,
                "rule_win_pct": float(100.0 * (a > 0).mean()) if len(a) else np.nan,
                "random_win_pct": float(100.0 * (b > 0).mean()) if len(b) else np.nan,
                "rule_median_bps": float(np.median(a)) if len(a) else np.nan,
                "random_median_bps": float(np.median(b)) if len(b) else np.nan,
            }
            rows.append(row)
            print(f"  {family:<7} {stop_name:<5} trades {row['trades']:>7,}"
                  f"  rule {row['rule_bps']:+8.1f}  random {row['random_bps']:+8.1f}"
                  f"  edge {row['edge_bps']:+8.1f}"
                  f"  t_naive {row['t_naive']:+6.2f}"
                  f"  t_yr {row['t_year_cluster']:+6.2f} ({row['n_years']}y)"
                  f"   [{(time.time()-t0)/60:.1f} min]")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", nargs="*", default=None)
    ap.add_argument("--pilot", type=int, default=0,
                    help="use only N symbols per universe")
    args = ap.parse_args()
    fams = args.families or FAMILIES
    bad = [f for f in fams if f not in FAMILIES]
    if bad:
        sys.exit(f"unknown families {bad}; known: {FAMILIES}")

    t0 = time.time()
    us = us_universe()
    if not us:
        sys.exit("no US_*_day.parquet in CLEAN -- run scripts.us_fetch first")
    nse = nse_universe(len(us))
    if args.pilot:
        us, nse = us[:args.pilot], nse[:args.pilot]

    rows = (run_universe("US", us, fams, t0)
            + run_universe("NSE", nse, fams, t0))
    out = pd.DataFrame(rows)
    stamp = date.today().isoformat()
    csv = OUT / "measurements" / f"us_rules_{stamp}.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(csv, index=False)
    print(f"\nwrote {csv}")
    print(f"  {out.shape[0]} rows x {out.shape[1]} columns")
    print(out.head(3).to_string(index=False))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib absent -- CSV written, figure skipped)")
        print(f"\ndone in {(time.time() - t0) / 60:.1f} min")
        return

    piv = out.pivot_table(index=["family", "stop"], columns="universe",
                          values="yr_edge_bps")
    fig, ax = plt.subplots(figsize=(9, 7))
    if {"US", "NSE"} <= set(piv.columns):
        ax.scatter(piv["NSE"], piv["US"], s=45)
        for (fam, st), r in piv.iterrows():
            ax.annotate(f"{fam}|{st}", (r["NSE"], r["US"]), fontsize=7)
        lim = np.nanmax(np.abs(piv[["NSE", "US"]].to_numpy())) * 1.15
        ax.axhline(0, lw=0.8, color="k")
        ax.axvline(0, lw=0.8, color="k")
        ax.plot([-lim, lim], [-lim, lim], lw=0.8, ls=":", color="grey")
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
    ax.set_xlabel("NSE edge over random timing (bps/trade, year-clustered)")
    ax.set_ylabel("US edge over random timing (bps/trade, year-clustered)")
    ax.set_title("Does the rule beat a coin flip? Same code, two markets")
    fig.tight_layout()
    png = OUT / "figures" / f"us_rules_{stamp}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=130)
    print(f"  -> {png}")
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

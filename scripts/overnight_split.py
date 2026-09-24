"""Where does the equity premium accrue -- overnight, or during the session?

Splits every daily bar into two legs:

    overnight : log(open_t / close_{t-1})    held through the close, flat by day
    intraday  : log(close_t / open_t)        flat overnight, held through the day

In LOGS the split is exact and additive at the symbol-day level -- the two legs
sum to the day's total log return with nothing left over and no cross term. That
matters: a first version of this script averaged each leg arithmetically ACROSS
symbols and compounded, and the two legs then did not reconstruct the total,
because the cross-sectional covariance between a stock's two legs was silently
dropped. Same data, incoherent answer. Logs, per symbol, then pool.

The question is structural, not strategic: an intraday-only book is flat
overnight BY CONSTRUCTION, so whatever the overnight leg earns is money it can
never reach however good its signal is. Nothing here is a strategy -- no
parameter is chosen, no rule is tested, no return is selected on. Written
2026-09-24.

Reads  : CLEAN/<symbol>_day.parquet  (every daily file present)
Writes : output/measurements/overnight_split_<date>.csv   (per symbol, both legs)
         output/figures/overnight_split_<date>.png        (cumulative, index only)

**The magnitudes are inflated by microstructure and must not be quoted as
economic returns.** Close and open are both transaction prices that may sit on
either side of the spread, so bid-ask bounce pushes return out of one leg and
into the other; the bias grows as a stock gets less liquid, which is why the
pooled figure for all 1,000+ names is far more extreme than for the liquid 200.
The trustworthy row is NIFTY 50: an index is an average of many names, so the
bounce diversifies away, and its open comes from the pre-open call auction
rather than one thin trade. Read the SIGN and the BREADTH from the stock rows
and the SIZE from the index row.

Hygiene, counted and printed rather than assumed away:
  * a session is dropped if the previous bar is more than GAP_DAYS calendar days
    back -- Kite serves phantom pre-listing bars and does not adjust demergers,
    so splicing across a break invents a return (frames.LISTING_BREAK_DAYS).
  * a session is dropped if either leg exceeds LEG_CAP in absolute log terms;
    at 0.40 (~49%) that is outside the 20% circuit band on both legs at once and
    so is an unadjusted corporate action, not a price.
  * a symbol is dropped below MIN_DAYS clean sessions.
"""
from __future__ import annotations

import datetime as dt
import glob
import os

import numpy as np
import pandas as pd

from kitelab import config as cfg

GAP_DAYS = 7
LEG_CAP = 0.40
MIN_DAYS = 250
LIQUID_TOP = 200
SESSIONS_PER_YEAR = 250
INDEXES = ("NIFTY 50", "NIFTY BANK")

OUT = "output"
MEAS = os.path.join(OUT, "measurements")
FIG = os.path.join(OUT, "figures")


def _legs(path: str):
    """(log-overnight, log-intraday, timestamps, median traded value, drop counts)."""
    d = pd.read_parquet(path)
    if d.empty or "open" not in d.columns:
        return None
    d["ts"] = pd.to_datetime(d["ts"])
    d = d.sort_values("ts")
    d = d[(d["open"] > 0) & (d["close"] > 0)]
    if len(d) < 2:
        return None
    lo = np.log(d["open"] / d["close"].shift(1))
    li = np.log(d["close"] / d["open"])
    have = lo.notna()
    ok_gap = d["ts"].diff().dt.days.le(GAP_DAYS)
    ok_cap = lo.abs().le(LEG_CAP) & li.abs().le(LEG_CAP)
    keep = have & ok_gap & ok_cap
    tv = float((d["close"] * d.get("volume", pd.Series(0.0, index=d.index))).median())
    return (lo[keep], li[keep], d.loc[keep, "ts"], tv,
            int((have & ~ok_gap).sum()), int((have & ok_gap & ~ok_cap).sum()))


def _ann(log_sum: float, n_days: int) -> float:
    """Annualised percent from a sum of daily log returns over n_days sessions."""
    return 100.0 * (np.exp(SESSIONS_PER_YEAR * log_sum / n_days) - 1.0) if n_days else np.nan


def main() -> None:
    files = sorted(glob.glob(os.path.join(str(cfg.CLEAN), "*_day.parquet")))
    print(f"daily files found: {len(files)}")
    if not files:
        raise SystemExit(f"no *_day.parquet under {cfg.CLEAN}")

    rows, curves = [], {}
    raw = kept = d_gap = d_cap = 0
    short = unreadable = 0
    for path in files:
        sym = os.path.basename(path).replace("_day.parquet", "")
        try:
            got = _legs(path)
        except Exception as exc:                      # a truncated file is not a finding
            print(f"  unreadable, skipped: {sym} ({exc})")
            unreadable += 1
            continue
        if got is None:
            continue
        lo, li, ts, tv, n_gap, n_cap = got
        raw += len(lo) + n_gap + n_cap
        d_gap += n_gap
        d_cap += n_cap
        if len(lo) < MIN_DAYS:
            short += 1
            continue
        kept += len(lo)
        rows.append({"symbol": sym, "days": len(lo),
                     "first": ts.iloc[0].date(), "last": ts.iloc[-1].date(),
                     "log_overnight": float(lo.sum()), "log_intraday": float(li.sum()),
                     "median_traded_value": tv})
        if sym in INDEXES:
            curves[sym] = pd.DataFrame({"on": lo.cumsum(), "id": li.cumsum()}).set_index(ts)

    d = pd.DataFrame(rows)
    print(f"symbol-days: {raw:,} candidate -> {kept:,} kept")
    print(f"  dropped, gap > {GAP_DAYS} calendar days (listing break/suspension): {d_gap:,}")
    print(f"  dropped, a leg beyond +/-{LEG_CAP} log (corporate action): {d_cap:,}")
    print(f"  symbols dropped below {MIN_DAYS} clean sessions: {short}"
          f"   unreadable files: {unreadable}")
    print(f"symbols kept: {len(d)} of {len(files)}")
    if d.empty:
        raise SystemExit("nothing survived the filters")

    d["log_total"] = d["log_overnight"] + d["log_intraday"]
    stocks = d[~d["symbol"].isin(INDEXES)]
    liquid = stocks.nlargest(LIQUID_TOP, "median_traded_value")

    print("\n=== annualised, from summed log returns (exact split, no cross term) ===")
    print(f"{'group':<26}{'symbols':>8}{'overnight':>12}{'intraday':>11}{'total':>9}"
          f"{'on > in':>10}")
    groups = [("all stocks", stocks),
              (f"liquid {LIQUID_TOP} by traded value", liquid)]
    groups += [(f"INDEX: {i}", d[d["symbol"] == i]) for i in INDEXES if (d["symbol"] == i).any()]
    for label, sub in groups:
        n = int(sub["days"].sum())
        on, iy = sub["log_overnight"].sum(), sub["log_intraday"].sum()
        share = f"{int((sub.log_overnight > sub.log_intraday).sum())}/{len(sub)}"
        print(f"{label:<26}{len(sub):>8}{_ann(on, n):>11.2f}%{_ann(iy, n):>10.2f}%"
              f"{_ann(on + iy, n):>8.2f}%{share:>10}")
    print("\nquote the INDEX rows for size -- the stock rows are inflated by bid-ask")
    print("bounce, which grows as liquidity falls. Quote the stock rows for breadth.")

    os.makedirs(MEAS, exist_ok=True)
    os.makedirs(FIG, exist_ok=True)
    stamp = dt.date.today().isoformat()
    print(f"\nper-symbol table: {d.shape[0]} rows x {d.shape[1]} columns")
    print(d.head(3).to_string(index=False))
    path = os.path.join(MEAS, f"overnight_split_{stamp}.csv")
    d.to_csv(path, index=False)
    print(f"wrote {path}")

    if not curves:
        print("no index curve to plot")
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(curves), figsize=(6 * len(curves), 5), squeeze=False)
    for ax, (name, c) in zip(axes[0], curves.items()):
        ax.plot(c.index, np.exp(c["on"]), label="overnight only (close -> open)")
        ax.plot(c.index, np.exp(c["id"]), label="intraday only (open -> close)")
        ax.plot(c.index, np.exp(c["on"] + c["id"]), label="both = buy and hold", lw=2, color="k")
        ax.set_yscale("log")
        ax.set_title(f"{name}\ngross of all costs")
        ax.set_ylabel("growth of 1 (log scale)")
        ax.legend()
        ax.grid(alpha=0.3)
    fig.tight_layout()
    figpath = os.path.join(FIG, f"overnight_split_{stamp}.png")
    fig.savefig(figpath, dpi=120)
    print(f"wrote {figpath}")


if __name__ == "__main__":
    main()

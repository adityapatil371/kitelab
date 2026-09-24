"""The short side: mirror entries, scored against random-timing controls.

WHY THIS EXISTS. Every rule the board trades is a BUY. An earlier cheap scan
answered a narrower question -- "are the buy rules secretly bearish?" -- by
reading their returns with the sign flipped, and the answer was no (only
`pull|own` was negative in both markets at |t| > 2, and nothing survived
Bonferroni). That is not the same as asking whether BEARISH SIGNALS EXIST. This
asks that: rules built to fire at the top rather than the bottom, sold short,
with the stop above the entry where a short's stop belongs.

THE MIRROR. Four of the board's families have a genuine opposite -- a 20-day
low becomes a 20-day high, oversold becomes overbought, a 52-week low becomes a
52-week high, a pullback in an uptrend becomes a bounce in a downtrend. Three
more are DIRECTION-NEUTRAL setups (a volatility squeeze, a volume spike, an
inside bar): they say "something is about to happen", not "up" or "down", so
the same signal is simply sold instead of bought. Both kinds are here and the
table says which is which, because a neutral setup failing short says nothing
about a bearish edge while a mirror failing short does.

WHAT A SHORT COSTS THAT A LONG DOES NOT is deliberately NOT modelled here:
borrow fees, recall risk, the uptick-style restrictions Indian markets apply to
intraday shorting, and the fact that NSE cash-market shorts cannot be carried
overnight at all without the futures or SLB route. So these numbers are the
BEST case for shorting. A rule that fails here fails a fortiori; a rule that
passes here has not yet been costed. Round-trip cost is not charged either --
this is the per-trade timing test, the same one us_rules runs for the long
side, and cost enters at the account layer in waterfall.py.

THE ARITHMETIC IS NOT SYMMETRIC. A short's loss is unbounded and its gain is
capped at 100%, so the same stop distance is a different bet. The stop is the
entry bar's own HIGH (mirroring the board's "entry candle's own low") or three
ATRs above the close, and the return is negated: a fall is a gain.

Reads /data/clean/kitelab/{US_,}*_day.parquet. Writes
output/measurements/short_rules_<date>.csv and
output/figures/short_rules_<date>.png. Touches no stamped module.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kitelab import entries, indicators                           # noqa: E402
from scripts.us_rules import (load, us_universe, nse_universe,    # noqa: E402
                              year_cluster_t, welch_t, MIN_SESSIONS)

OUT = Path(__file__).resolve().parents[1] / "output"
FIG, MEAS = OUT / "figures", OUT / "measurements"

MAXHOLD = entries.MAXHOLD          # 60, the board's
STOPS = {"own": None, "atr3": 3.0}
DRAWS = 1


# --- the entries. MIRROR = a real opposite; NEUTRAL = no direction of its own.
def _parts(bars):
    o = pd.Series(bars["open"].to_numpy(float))
    h = pd.Series(bars["high"].to_numpy(float))
    l = pd.Series(bars["low"].to_numpy(float))
    c = pd.Series(bars["close"].to_numpy(float))
    v = pd.Series(bars["volume"].to_numpy(float))
    return o, h, l, c, v


def mr_hi(bars):
    """MIRROR of `mr`. Close at a 20-day HIGH -- short the extreme."""
    _, _, _, c, _ = _parts(bars)
    return c.ge(c.rolling(20, min_periods=20).max()).fillna(False).to_numpy(bool)


def rsi70(bars):
    """MIRROR of `rsi30`. Overbought: 14-period RSI above 70."""
    _, _, _, c, _ = _parts(bars)
    return indicators.rsi(c, entries.RSI_LEN).gt(70).fillna(False).to_numpy(bool)


def high252(bars):
    """MIRROR of `low252`. At or within a whisker of the 52-week high."""
    _, _, _, c, _ = _parts(bars)
    return c.ge(c.rolling(252, min_periods=252).max()).fillna(False).to_numpy(bool)


def bounce(bars):
    """MIRROR of `pull`. `pull` buys a dip inside an uptrend (above the 200-day
    average, below the 20-day). This sells a bounce inside a DOWNTREND: below
    the 200-day average, back above the 20-day."""
    _, _, _, c, _ = _parts(bars)
    return (c.lt(c.rolling(200, min_periods=200).mean())
            & c.gt(c.rolling(20, min_periods=20).mean())).fillna(False).to_numpy(bool)


def gapup(bars):
    """MIRROR of `gapdn`, and the gap-up exhaustion trade. Opens above
    yesterday's close by more than the board's gap threshold."""
    o, _, _, c, _ = _parts(bars)
    return o.gt(c.shift(1) * entries.GAP_UP).fillna(False).to_numpy(bool)


def vcon(bars):
    """NEUTRAL. The board's volatility squeeze: narrowest range in seven bars.
    Sold rather than bought."""
    _, h, l, _, _ = _parts(bars)
    rng = h - l
    return rng.le(rng.rolling(7, min_periods=7).min()).fillna(False).to_numpy(bool)


def vol(bars):
    """NEUTRAL. The board's volume spike: over three times the 50-bar median."""
    _, _, _, _, v = _parts(bars)
    vmed = v.rolling(50, min_periods=50).median()
    return (v.gt(3.0 * vmed) & vmed.gt(0)).fillna(False).to_numpy(bool)


def inside(bars):
    """NEUTRAL. The board's inside bar, sold rather than bought."""
    _, h, l, _, _ = _parts(bars)
    return (h.lt(h.shift(1)) & l.gt(l.shift(1))).fillna(False).to_numpy(bool)


RULES = {"mr_hi": mr_hi, "rsi70": rsi70, "high252": high252, "bounce": bounce,
         "gapup": gapup, "vcon": vcon, "vol": vol, "inside": inside}
KIND = {"mr_hi": "mirror", "rsi70": "mirror", "high252": "mirror",
        "bounce": "mirror", "gapup": "mirror",
        "vcon": "neutral", "vol": "neutral", "inside": "neutral"}


def _exit_short(i, c, hi, atr, stop_mult, total):
    """The board's exit, mirrored. Stop is ABOVE the entry -- the entry bar's
    own high, or three ATRs up -- checked on the close, before the max hold.
    The return is negated, so a fall in price is a gain on the trade."""
    if stop_mult is None:
        stop = float(hi[i])
    else:
        if not np.isfinite(atr[i]):
            return None
        stop = float(c[i]) + stop_mult * float(atr[i])
    entry_px = float(c[i])
    if stop - entry_px <= 0 or entry_px <= 0:
        return None
    for step in range(i + 1, total):
        if c[step] >= stop:
            return step, -(float(c[step]) / entry_px - 1.0)
        if step - i >= MAXHOLD:
            return step, -(float(c[step]) / entry_px - 1.0)
    return None


def run_symbol(sym, bars, family, stop_mult, rng):
    fires = RULES[family](bars)
    c = bars["close"].to_numpy(float)
    hi = bars["high"].to_numpy(float)
    atr = indicators.atr(bars["high"], bars["low"], bars["close"],
                         entries.ATR_LEN).to_numpy(float)
    total = len(bars)
    yrs = pd.DatetimeIndex(bars["ts"]).year.to_numpy()

    real, real_y, pos = [], [], 0
    while pos < total:
        if not (fires[pos] and pos > 0 and not fires[pos - 1]):
            pos += 1
            continue
        got = _exit_short(pos, c, hi, atr, stop_mult, total)
        if got is None:
            pos += 1
            continue
        exit_i, ret = got
        real.append(1e4 * ret)
        real_y.append(int(yrs[pos]))
        pos = exit_i + 1

    ctrl, ctrl_y = [], []
    if real:
        lo_i, hi_i = entries.ATR_LEN + 1, total - 2
        if hi_i > lo_i:
            want, tries, made = len(real) * DRAWS, 0, 0
            while made < want and tries < want * 20:
                tries += 1
                j = int(rng.integers(lo_i, hi_i))
                got = _exit_short(j, c, hi, atr, stop_mult, total)
                if got is None:
                    continue
                ctrl.append(1e4 * got[1])
                ctrl_y.append(int(yrs[j]))
                made += 1
    return real, real_y, ctrl, ctrl_y


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
    med = int(np.median([len(b) for b in loaded.values()]))
    print(f"  median {med:,} sessions")

    rows = []
    for family in fams:
        for stop_name, stop_mult in STOPS.items():
            real, real_y, ctrl, ctrl_y = [], [], [], []
            for sym, bars in loaded.items():
                seed = abs(hash((tag, sym, family, stop_name))) % (2**32)
                r, ry, k, ky = run_symbol(sym, bars, family, stop_mult,
                                          np.random.default_rng(seed))
                real += r
                real_y += ry
                ctrl += k
                ctrl_y += ky
            a, b = np.asarray(real, float), np.asarray(ctrl, float)
            yr_edge, yr_t, n_years = year_cluster_t(real, real_y, ctrl, ctrl_y)
            row = {"universe": tag, "family": family, "kind": KIND[family],
                   "stop": stop_name, "trades": len(a), "control_trades": len(b),
                   "rule_bps": float(a.mean()) if len(a) else np.nan,
                   "random_bps": float(b.mean()) if len(b) else np.nan,
                   "edge_bps": float(a.mean() - b.mean()) if len(a) and len(b) else np.nan,
                   "t_naive": welch_t(a, b),
                   "yr_edge_bps": yr_edge, "t_year_cluster": yr_t,
                   "n_years": n_years,
                   "rule_win_pct": float(100.0 * (a > 0).mean()) if len(a) else np.nan}
            rows.append(row)
            print(f"  {family:<8} {stop_name:<5} {KIND[family]:<7}"
                  f" trades {row['trades']:>7,}"
                  f"  short {row['rule_bps']:+8.1f}  random {row['random_bps']:+8.1f}"
                  f"  edge {row['edge_bps']:+8.1f}"
                  f"  t_yr {row['t_year_cluster']:+6.2f} ({row['n_years']}y)"
                  f"   [{(time.time()-t0)/60:.1f} min]")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", nargs="*", default=sorted(RULES))
    ap.add_argument("--pilot", type=int, default=0)
    args = ap.parse_args()
    bad = [r for r in args.rules if r not in RULES]
    if bad:
        sys.exit(f"unknown rules {bad}; known: {sorted(RULES)}")

    us = us_universe()
    if not us:
        sys.exit("no US_*_day.parquet in CLEAN -- run scripts.us_fetch first")
    books = {"US": us, "NSE": nse_universe(len(us))}
    if args.pilot:
        books = {k: v[:args.pilot] for k, v in books.items()}
    print("universes: " + ", ".join(f"{k} {len(v)}" for k, v in books.items()))
    print(f"rules: {', '.join(args.rules)}   stops: {list(STOPS)}   "
          f"max hold {MAXHOLD} bars")
    print("NOTE: no borrow fee, no recall risk, no NSE overnight-short "
          "restriction. These are the BEST case for shorting.\n")

    t0 = time.time()
    rows = []
    for tag, syms in books.items():
        rows += run_universe(tag, syms, args.rules, t0)
    out = pd.DataFrame(rows)
    if out.empty:
        sys.exit("no rows produced")

    MEAS.mkdir(parents=True, exist_ok=True)
    path = MEAS / f"short_rules_{date.today()}.csv"
    out.to_csv(path, index=False)
    print(f"\nwrote {path}  ({len(out)} rows x {len(out.columns)} cols)")

    print("\n=== short edge over random timing, bps/trade, YEAR-CLUSTERED ===")
    print(out.pivot_table(index=["kind", "family", "stop"], columns="universe",
                          values="yr_edge_bps").round(1).to_string())

    from scipy import stats
    n = len(out)
    df = max(int(out.n_years.median()) - 1, 1)
    tcrit = float(stats.t.ppf(1 - 0.05 / (2 * n), df))
    hits = out[out.t_year_cluster.abs() >= tcrit]
    print(f"\n{len(hits)} of {n} rows clear |t| >= {tcrit:.2f} "
          f"(Bonferroni across {n} rows, df={df}); "
          f"{(out.t_year_cluster >= tcrit).sum()} positive")
    loose = out[out.t_year_cluster.abs() >= 2.0]
    print(f"{len(loose)} of {n} clear an UNCORRECTED |t| >= 2.0, where "
          f"{0.05 * n:.1f} are expected by chance; "
          f"{(out.t_year_cluster >= 2.0).sum()} of those positive")
    if len(loose):
        print(loose[["universe", "kind", "family", "stop", "trades",
                     "yr_edge_bps", "t_year_cluster"]]
              .sort_values("t_year_cluster").to_string(index=False))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib absent -- CSV written, figure skipped")
        return
    FIG.mkdir(parents=True, exist_ok=True)
    fams = args.rules
    fig, ax = plt.subplots(figsize=(9, 6))
    y = np.arange(len(fams))
    for off, (tag, colr) in enumerate([("US", "#1f77b4"), ("NSE", "#d62728")]):
        v = [out[(out.family == f) & (out.universe == tag)].yr_edge_bps.median()
             for f in fams]
        ax.barh(y + (off - 0.5) * 0.4, v, height=0.4, color=colr, label=tag)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{f} ({KIND[f]})" for f in fams])
    ax.axvline(0, color="k", lw=1)
    ax.set_xlabel("short edge over random timing, bps/trade (year-clustered)")
    ax.set_title(f"Mirror rules, short side, max hold {MAXHOLD} bars "
                 "(no borrow cost modelled)")
    ax.legend()
    fig.tight_layout()
    fp = FIG / f"short_rules_{date.today()}.png"
    fig.savefig(fp, dpi=110)
    print(f"wrote {fp}")


if __name__ == "__main__":
    main()

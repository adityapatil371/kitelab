"""Daily rule-minus-hold excess returns, and an honest test on them.
(off-board diagnostic, 2026-09-08)

WHY. The walk-forward gate squashes each three-year window into one win/lose
bit, leaving 7 numbers per cell. scripts.wf_power measured what that costs: at
the board's own noise the gate passes 49.8% of rules with NO edge, while an
honest 5% test on the same 7 numbers needs a 20 pts/yr edge for 80% power.
Seven observations cannot support a test. This script replaces them with the
~5,000 DAILY excess returns already implicit in the same data.

WHAT IT COMPARES. For each cell, portfolio.run's daily mark-to-market equity
curve against a daily equal-weight buy-and-hold curve of the same universe --
the same benchmark validation._hold defines, but as a curve rather than a
single CAGR. Excess is the daily arithmetic difference, rule minus hold.

THE TEST. H0: mean daily excess = 0, against a one-sided H1 > 0, using a
Newey-West HAC standard error. Daily excess returns are serially correlated
(overlapping positions, shared market factor), so an iid standard error would
be far too small and would manufacture significance -- the same mistake
validation.t_iid was corrected for on the trade side. The lag is
max(4*(n/100)^(2/9), 21): the Newey-West rule, floored at one trading month,
erring toward a WIDER interval.

WHAT THIS IS NOT. Still in-sample, still on a survivorship-biased universe
(that bias sits inside BOTH sides, so it does not create a gap), still 950
simultaneously-evaluated cells -- so the Bonferroni and Benjamini-Hochberg
columns matter more than any single p. No number here is a forecast.

BUILDING THE BENCHMARK HERE, NOT IN validation.py. kitelab/*.py is untouched
on purpose: signals.stamp() digests file MTIMES, so editing any module in the
package invalidates every signal cache and forces a ~103-minute rebuild that
would produce byte-identical numbers. The bucket rule below is copied verbatim
from scripts.dashboard_data and asserted to partition the universe; the hold
arithmetic is asserted against validation.buy_and_hold.

Reads:  /data/clean/kitelab/signal_cache/*_all.pkl, the price frames,
        /data/clean/kitelab/dashboard.json (for the built stamp only)
Writes: output/wf_daily_<built date>.csv          (one row per cell)
        output/wf_daily_hold_<built date>.pkl     (checkpoint: hold curves)
Cost:   ~4 minutes for the full 950-cell grid, measured 2026-09-08.
        --limit N runs only the first N strategies, for a smoke test.
        --capped prices the illiquidity (see below) and writes _capped.csv.

WHY --capped EXISTS (2026-09-08). The first full run looked far kinder than
the window test: median excess -1.85 pts/yr, 43.2% of cells ahead. Splitting
it up showed why, and it is not an edge. Every one of the 136 nominally
significant cells sits in the `all`, `small` or `recent` universes -- ZERO in
`large`, ZERO in `mid` -- and the `illiquid` fill priority, which buys the
LEAST traded name first, supplies 56 of them and all 8 Bonferroni survivors.
Measured directly on Turtle_1tf_55_20 over `all` at Rs2L: illiquid-first fills
buy names trading a median Rs41 lakh/day and compound at 47.4%/yr to Rs55.8
crore; liquid-first fills buy names trading Rs316 lakh/day and reach 28.0%.
At Rs55 crore a 1%-risk position is ~Rs5 crore in a stock that trades Rs0.4
crore a DAY. That is not a fill. slippage.MAX_PARTICIPATION was None and
slippage.ENABLED False, so nothing stopped it. With --capped that same cell
falls 47.4% -> 19.6%.
"""
import argparse
import csv
import json
import math
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

import kitelab.config as C
from kitelab import frames, portfolio, registry, validation
from kitelab.config import CLEAN

OUT = Path(__file__).resolve().parent.parent / "output"
CACHE = CLEAN / "signal_cache"

CAPITALS = [200_000, 10_000_000]     # scripts.dashboard_data.CAPITALS
RISK = 0.01                          # validation.RISK
START_DEFAULT = 2018                 # the bucket cut, scripts.dashboard_data
TRADING_DAYS = 252
MIN_DAYS = 250                       # a cell with less than a year of curve is not tested
NW_FLOOR = 21                        # one trading month


# ------------------------------------------------------------- universes ----
def buckets(members, asof=START_DEFAULT):
    """(small, mid, large, recent) by median daily traded value before `asof`.

    Copied verbatim from the nested function in scripts.dashboard_data (it is
    not importable). Cut points are scripts.screen_universe's. `recent` is
    every name with no positive-turnover bar before the cut.
    """
    cut = pd.Timestamp(f"{asof}-01-01")
    turn = {}
    for sym in members:
        try:
            d = frames.daily(sym)
            d = d[d["ts"] < cut]
            v = (d["close"] * d["volume"]).to_numpy(float)
            v = v[v > 0]
            if len(v):
                turn[sym] = float(np.median(v))
        except SystemExit:
            pass
    return ({s for s, t in turn.items() if t < 5e7},
            {s for s, t in turn.items() if 5e7 <= t < 25e7},
            {s for s, t in turn.items() if t >= 25e7},
            {s for s in members if s not in turn})


def closes_matrix(members):
    """(calendar, DataFrame of forward-filled closes) over every member."""
    series = {}
    for sym in members:
        try:
            d = frames.daily(sym)
        except SystemExit:
            continue
        if d is None or len(d) < validation.MIN_HOLD_BARS:
            continue
        s = pd.Series(d["close"].to_numpy(float),
                      index=pd.DatetimeIndex(pd.to_datetime(d["ts"])).normalize())
        series[sym] = s[~s.index.duplicated(keep="last")]
    if not series:
        raise SystemExit("no usable price series -- cannot build a benchmark")
    df = pd.DataFrame(series).sort_index()
    print(f"  price matrix: {df.shape[0]} sessions x {df.shape[1]} symbols "
          f"({len(members) - df.shape[1]} skipped: missing, or under "
          f"{validation.MIN_HOLD_BARS} bars)")
    return df.ffill()


def hold_curve(closes, members):
    """Daily equal-weight buy-and-hold wealth for `members`, as a Series.

    validation._hold's arithmetic, day by day: Rs1 into each member at its
    first close, held. A member that has not listed yet holds its Rs1 in cash
    and earns nothing -- "late money, not free money", the same convention.
    Wealth is the MEAN over members, so the curve starts at 1.0.
    """
    cols = [c for c in closes.columns if c in members]
    if not cols:
        return None
    sub = closes[cols]
    first = sub.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
    wealth = (sub / first).fillna(1.0)          # pre-listing -> Rs1 in cash
    return wealth.mean(axis=1)


# ------------------------------------------------------------------ stats ----
def hac_se(x, lag):
    """Newey-West HAC standard error of the mean of `x`."""
    n = len(x)
    d = x - x.mean()
    s = float(d @ d) / n
    for l in range(1, lag + 1):
        g = float(d[l:] @ d[:-l]) / n
        s += 2.0 * (1.0 - l / (lag + 1)) * g
    if s <= 0:
        return None
    return math.sqrt(s / n)


def nw_lag(n):
    return max(NW_FLOOR, int(4 * (n / 100.0) ** (2.0 / 9.0)))


def norm_sf(z):
    """One-sided upper tail of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def daily_returns(values):
    v = np.asarray(values, dtype=float)
    return v[1:] / v[:-1] - 1.0


# ------------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="run only the first N strategies (smoke test)")
    ap.add_argument("--capped", action="store_true",
                    help="price the illiquidity: cap one order at 1%% of that "
                         "day's traded value and charge market impact")
    args = ap.parse_args()

    # Both are module-level globals READ AT RUN TIME inside portfolio.run
    # (kitelab/portfolio.py:525 capped_shares, :544 impact), so setting them
    # from a script changes no file mtime and invalidates no signal cache.
    # The half-spread is applied earlier, at signal-generation time
    # (kitelab/backtest.py:386), and is baked into the caches -- it stays off
    # either way, so --capped is a LOWER bound on the true friction.
    tag = ""
    if args.capped:
        from kitelab import slippage
        slippage.MAX_PARTICIPATION = 0.01
        slippage.ENABLED = True
        tag = "_capped"
        print("friction ON: order <= 1% of daily traded value, impact charged")

    dash = CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"No dashboard.json at {dash}. Build it: python3 -m scripts.refresh")
    stamp = json.loads(dash.read_bytes())["built"].split()[0]
    print(f"dashboard.json: built {stamp}")

    cfg = C.load()
    members = list(cfg.merged)
    print(f"universe: {len(members)} symbols")

    t0 = time.time()
    small, mid, large, recent = buckets(members)
    if len(small) + len(mid) + len(large) + len(recent) != len(members):
        raise SystemExit("liquidity buckets must partition the universe")
    universes = {"all": None, "large": large, "mid": mid, "small": small, "recent": recent}
    print(f"buckets ({time.time()-t0:.0f}s): large {len(large)}, mid {len(mid)}, "
          f"small {len(small)}, recent {len(recent)}")

    # ---- benchmark curves, checkpointed --------------------------------
    ckpt = OUT / f"wf_daily_hold_{stamp}.pkl"
    OUT.mkdir(exist_ok=True)
    if ckpt.exists():
        holds = pickle.loads(ckpt.read_bytes())
        print(f"\nhold curves: loaded checkpoint {ckpt.name}")
    else:
        print("\nbuilding daily equal-weight hold curves")
        closes = closes_matrix(members)
        holds = {}
        for key, mem in universes.items():
            mem = set(closes.columns) if mem is None else mem
            c = hold_curve(closes, mem)
            if c is None:
                raise SystemExit(f"no hold curve for {key}")
            holds[key] = c
            yrs = (c.index[-1] - c.index[0]).days / 365.25
            cagr = 100 * ((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1)
            print(f"  {key:<7}{len(c):>6} days  {c.index[0].date()} to {c.index[-1].date()}"
                  f"  CAGR {cagr:>6.2f}%")
        ckpt.write_bytes(pickle.dumps(holds))
        print(f"  checkpointed to {ckpt.name}")

    # cross-check against the function the board actually uses
    ref = validation.buy_and_hold(members)
    c = holds["all"]
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    mine = 100 * ((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1)
    print(f"\ncross-check 'all' whole history: validation.buy_and_hold {ref:.2f}%/yr "
          f"vs this curve {mine:.2f}%/yr  (gap {mine-ref:+.2f})")
    if abs(mine - ref) > 1.5:
        raise SystemExit("hold curve disagrees with validation.buy_and_hold by more than "
                         "1.5 pts/yr -- the benchmark is not what this diagnostic assumes")

    # ---- trades --------------------------------------------------------
    strategies = registry._build_registry()
    if args.limit:
        strategies = strategies[:args.limit]
    trades = {}
    for st in strategies:
        p = CACHE / f"{st.cache}_all.pkl"
        if not p.exists():
            print(f"  [skip] {st.cache}: no cache on disk")
            continue
        blob = pickle.loads(p.read_bytes())
        trades[st.cache] = blob["trades"] if isinstance(blob, dict) else blob
    print(f"\ntrade lists: {len(trades)} of {len(strategies)} strategies, "
          f"{sum(len(v) for v in trades.values()):,} trades")

    # ---- the grid ------------------------------------------------------
    fields = ["universe", "strategy", "priority", "capital", "days",
              "rule_cagr", "hold_cagr", "excess_geom_pts", "mean_daily_excess_bp",
              "sd_daily_excess_bp", "nw_lag", "t_hac", "p_one_sided",
              "mde_80_pts_per_year"]
    path = OUT / f"wf_daily{tag}_{stamp}.csv"
    rows, skipped = [], 0
    t0 = time.time()
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for st in strategies:
            if st.cache not in trades:
                continue
            for uni, mem in universes.items():
                sub = (trades[st.cache] if mem is None
                       else [t for t in trades[st.cache] if t["symbol"] in mem])
                if not sub:
                    skipped += 1
                    continue
                hold = holds[uni]
                for pri in portfolio.PRIORITIES:
                    for cap in CAPITALS:
                        r = portfolio.run(sub, float(cap), RISK, priority=pri)
                        curve = r.get("curve") or []
                        if len(curve) < MIN_DAYS:
                            skipped += 1
                            continue
                        idx = pd.DatetimeIndex([pd.Timestamp(d).normalize()
                                                for d, _ in curve])
                        eq = pd.Series([float(v) for _, v in curve], index=idx)
                        eq = eq[~eq.index.duplicated(keep="last")]
                        shared = eq.index.intersection(hold.index)
                        if len(shared) < MIN_DAYS:
                            skipped += 1
                            continue
                        rr = daily_returns(eq.loc[shared].to_numpy())
                        hh = daily_returns(hold.loc[shared].to_numpy())
                        ex = rr - hh
                        n = len(ex)
                        lag = nw_lag(n)
                        se = hac_se(ex, lag)
                        yrs = (shared[-1] - shared[0]).days / 365.25
                        g_r = eq.loc[shared].iloc[-1] / eq.loc[shared].iloc[0]
                        g_h = hold.loc[shared].iloc[-1] / hold.loc[shared].iloc[0]
                        rule_cagr = 100 * (g_r ** (1 / yrs) - 1) if g_r > 0 else None
                        hold_cagr = 100 * (g_h ** (1 / yrs) - 1)
                        t = (ex.mean() / se) if se else None
                        row = {
                            "universe": uni, "strategy": st.cache, "priority": pri,
                            "capital": cap, "days": n,
                            "rule_cagr": None if rule_cagr is None else round(rule_cagr, 2),
                            "hold_cagr": round(hold_cagr, 2),
                            "excess_geom_pts": (None if rule_cagr is None
                                                else round(rule_cagr - hold_cagr, 2)),
                            "mean_daily_excess_bp": round(ex.mean() * 1e4, 3),
                            "sd_daily_excess_bp": round(ex.std(ddof=1) * 1e4, 2),
                            "nw_lag": lag,
                            "t_hac": None if t is None else round(t, 3),
                            "p_one_sided": None if t is None else round(norm_sf(t), 5),
                            # smallest true edge an 80%-power one-sided 5% test
                            # would catch here, in CAGR points per year
                            "mde_80_pts_per_year": (None if se is None else
                                                    round(2.486 * se * TRADING_DAYS * 100, 2)),
                        }
                        w.writerow(row)
                        rows.append(row)
            print(f"  {st.cache:<24} cells so far {len(rows):>4}  "
                  f"{time.time()-t0:>6.0f}s", flush=True)

    print(f"\n{len(rows)} cells tested, {skipped} skipped, {time.time()-t0:.0f}s")
    report(rows)
    print(f"\nwrote {path}")


def report(rows):
    if not rows:
        print("no cells -- nothing to report")
        return
    ok = [r for r in rows if r["t_hac"] is not None]
    t = np.array([r["t_hac"] for r in ok])
    ex = np.array([r["excess_geom_pts"] for r in ok if r["excess_geom_pts"] is not None])
    mde = np.array([r["mde_80_pts_per_year"] for r in ok])
    p = np.array([r["p_one_sided"] for r in ok])

    print(f"\n{'='*70}\nDAILY EXCESS RETURN TEST -- {len(ok)} cells")
    print(f"  median days compared per cell : {int(np.median([r['days'] for r in ok]))}")
    print(f"  median detectable edge (80% power, one-sided 5%): "
          f"{np.median(mde):.2f} CAGR pts/yr")
    print("    -- the same test on 7 windows needed 20 pts/yr (scripts.wf_power)")
    print("\n  geometric excess (rule CAGR - hold CAGR), pts/yr:")
    print(f"    median {np.median(ex):>7.2f}   mean {ex.mean():>7.2f}   "
          f"best {ex.max():>7.2f}   worst {ex.min():>7.2f}")
    print(f"    cells ahead of hold: {int((ex > 0).sum())} of {len(ex)} "
          f"({100*(ex>0).mean():.1f}%)")
    print("\n  HAC t-statistic:")
    print(f"    median {np.median(t):>7.2f}   max {t.max():>7.2f}   min {t.min():>7.2f}")
    print(f"    t > 0            : {int((t > 0).sum())} of {len(t)}")

    alpha = 0.05
    sig = int((p <= alpha).sum())
    bonf = int((p <= alpha / len(p)).sum())
    order = np.sort(p)
    bh_thresh = alpha * (np.arange(1, len(p) + 1)) / len(p)
    below = np.where(order <= bh_thresh)[0]
    bh = int(below.max() + 1) if len(below) else 0
    print("\n  significantly BETTER than hold (one-sided, HAC):")
    print(f"    uncorrected p <= 0.05          : {sig} of {len(p)} "
          f"({100*sig/len(p):.1f}%; ~{0.05*len(p):.0f} expected by chance)")
    print(f"    Bonferroni across {len(p)} cells   : {bonf}")
    print(f"    Benjamini-Hochberg FDR 5%      : {bh}")


if __name__ == "__main__":
    main()

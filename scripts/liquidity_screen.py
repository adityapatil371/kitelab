"""IS THE LIQUIDITY SCREEN A STRATEGY, OR AN ARTIFACT?

WHAT WE FOUND, AND WHY IT IS NOT YET A RESULT. `scripts/waterfall.py` charges
a realism rule: one order may take at most 1% of what a stock actually traded
that day. It is a SIZING rule, not a cost. On NSE it turned out to be worth
more than every entry rule on the board -- the 20-slot rung returned 34.9%/yr
against buy-and-hold's 18.2%, at a quarter of the drawdown, and a RANDOM-entry
arm under the same rule returned 28.6%. Deleting every bar before 2010 barely
moved it. Switching the cap off (--capfrac 1e9) collapsed it to -0.3%.

So the cap is the whole effect. But the cap is a property of our SIMULATOR,
not a portfolio anyone holds. It does two things at once and the waterfall
cannot separate them: it refuses to size up in thin names (a liquidity tilt)
AND it leaves the unspent money in cash (a cash sleeve). Either one alone
could produce the number.

THIS SCRIPT BUILDS THE THING DELIBERATELY AND SEPARATES THE TWO. No entry
rules, no stops, no signals -- just a portfolio, rebalanced monthly, equal
weight inside whatever it holds.

  hold_all      every symbol, fully invested            the benchmark
  top_N         top N by trailing turnover              the tilt, fully invested
  bottom_N      bottom N by trailing turnover           the tilt, reversed
  random_N      N symbols drawn at random               controls for HOLDING FEWER
  capped        weight = min(1/M, 1% of turnover/cap)   the waterfall's rule, rebuilt
  cash_matched  hold_all scaled to capped's exposure    controls for the CASH SLEEVE

`random_N` and `cash_matched` are the whole point. If top_N beats random_N,
the turnover RANKING has content. If capped beats cash_matched, the LIQUIDITY
TILT has content. If neither, the 34.9% was concentration and cash, which
anybody can have for free and which is not an edge.

A SURVIVORSHIP WARNING THAT APPLIES TO EVERY NUMBER HERE. The universe is
symbols that exist today, so absolute returns are inflated on every arm. The
arms are compared against each other, drawn from the identical pool, so the
DIFFERENCES are fair; the levels are not.

READS:  CLEAN/US_<SYM>_day.parquet and CLEAN/<SYM>_day.parquet
WRITES: output/measurements/liquidity_screen_<date>.csv
        output/figures/liquidity_screen_<date>.png
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

from scripts.us_rules import load, us_universe, nse_universe   # noqa: E402
from scripts.waterfall import cagr_of, maxdd_of, MAX_DAY, SESSIONS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output"

TURN_LEN = 60        # trailing sessions the turnover rank is measured over
REBAL = 21           # one month
N_HOLD = 20          # matches the waterfall's 20 slots
CAP_FRAC = 0.01      # the waterfall's own fill cap
DRAWS = 20           # random_N is averaged over this many draws


def build_panel(tag, syms, start=None):
    """Returns (dates, close-to-close returns, turnover), all aligned."""
    print(f"\n=== {tag}: loading {len(syms)} symbols ===")
    bars, bad = {}, []
    for s in syms:
        b = load(s)
        if b is None:
            continue
        c = b["close"].to_numpy(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            mv = np.nanmax(np.abs(np.diff(c) / c[:-1])) if len(c) > 1 else 0.0
        if mv > MAX_DAY:
            bad.append(s)
            continue
        if start is not None:
            b = b[pd.DatetimeIndex(b["ts"]) >= start]
        if len(b) < TURN_LEN * 4:
            continue
        bars[s] = b
    print(f"  loaded {len(bars)} of {len(syms)} "
          f"({len(bad)} not a price series)")
    if not bars:
        sys.exit(f"{tag}: nothing loaded")

    dates = pd.DatetimeIndex(sorted(set().union(
        *(pd.DatetimeIndex(b["ts"]) for b in bars.values()))))
    print(f"  calendar {len(dates):,} sessions "
          f"({dates[0].date()} .. {dates[-1].date()})")
    ret = pd.DataFrame(index=dates, columns=list(bars), dtype=float)
    turn = pd.DataFrame(index=dates, columns=list(bars), dtype=float)
    for s, b in bars.items():
        idx = pd.DatetimeIndex(b["ts"])
        c = b["close"].astype(float)
        ret.loc[idx, s] = (c / c.shift(1) - 1.0).to_numpy()
        turn.loc[idx, s] = (c * b["volume"].astype(float)).to_numpy()
    # Trailing median turnover, shifted one day: the rank on rebalance morning
    # may only use bars already closed. Median not mean, so one block deal
    # cannot promote a stock that is otherwise untradeable.
    turn = turn.rolling(TURN_LEN, min_periods=TURN_LEN // 2).median().shift(1)
    print(f"  returns {ret.shape[0]:,} x {ret.shape[1]}, "
          f"{int(ret.notna().sum().sum()):,} observed")
    print(ret.iloc[:3, :4].to_string())
    return dates, ret, turn


def walk(ret, weights_fn, dates):
    """Monthly rebalance, hold the weights in between. Returns (daily series,
    mean deployed fraction). Cash earns zero."""
    r = ret.to_numpy(float)
    n, m = r.shape
    out = np.zeros(n)
    expo = np.zeros(n)
    w = np.zeros(m)
    for t in range(n):
        if t % REBAL == 0:
            w = weights_fn(t)
        live = np.isfinite(r[t]) & (w > 0)
        out[t] = float((w[live] * r[t][live]).sum())
        expo[t] = float(w[live].sum())
    return pd.Series(out, index=dates), float(expo.mean())


def make_arms(ret, turn, rng):
    """Every arm as a weights function of the row index. Alive = the symbol has
    a turnover reading and a price today; a name that has not listed yet can
    never be held by any arm."""
    tv = turn.to_numpy(float)
    rr = ret.to_numpy(float)
    m = rr.shape[1]

    def alive(t):
        return np.isfinite(tv[t]) & np.isfinite(rr[t])

    def eq(mask):
        w = np.zeros(m)
        k = int(mask.sum())
        if k:
            w[mask] = 1.0 / k
        return w

    def hold_all(t):
        return eq(alive(t))

    def rank_pick(t, top):
        a = alive(t)
        idx = np.flatnonzero(a)
        if idx.size == 0:
            return np.zeros(m)
        order = idx[np.argsort(tv[t][idx])]
        pick = order[-N_HOLD:] if top else order[:N_HOLD]
        mask = np.zeros(m, bool)
        mask[pick] = True
        return eq(mask)

    def random_pick(t):
        idx = np.flatnonzero(alive(t))
        if idx.size == 0:
            return np.zeros(m)
        pick = rng.choice(idx, size=min(N_HOLD, idx.size), replace=False)
        mask = np.zeros(m, bool)
        mask[pick] = True
        return eq(mask)

    def capped(t, capital):
        """The waterfall's rule, rebuilt. Spread the book over N_HOLD names,
        then shrink any position whose order would exceed 1% of that name's
        traded value. Whatever is shrunk away stays in cash."""
        a = alive(t)
        idx = np.flatnonzero(a)
        if idx.size == 0:
            return np.zeros(m)
        # Names chosen WITHOUT looking at turnover -- the waterfall's cap does
        # not pick what to hold, it only shrinks what it was already told to
        # buy. Ranking here too would smuggle the tilt in twice.
        pick = rng.choice(idx, size=min(N_HOLD, idx.size), replace=False)
        w = np.zeros(m)
        want = 1.0 / len(pick)
        room = CAP_FRAC * tv[t][pick] / capital
        w[pick] = np.minimum(want, room)
        return w

    return hold_all, rank_pick, random_pick, capped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1e7)
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--start", default=None)
    args = ap.parse_args()
    t0 = time.time()
    start = pd.Timestamp(args.start) if args.start else None

    books = {"US": us_universe(), "NSE": nse_universe(144)}
    if args.pilot:
        books = {k: v[:args.pilot] for k, v in books.items()}

    rows = []
    curves = {}
    for tag, syms in books.items():
        dates, ret, turn = build_panel(tag, syms, start)
        rng = np.random.default_rng(abs(hash(tag)) % (2**32))
        hold_all, rank_pick, random_pick, capped = make_arms(ret, turn, rng)

        arms = {
            "hold_all": hold_all,
            f"top_{N_HOLD}": lambda t: rank_pick(t, True),
            f"bottom_{N_HOLD}": lambda t: rank_pick(t, False),
            f"random_{N_HOLD}": random_pick,
            "capped": lambda t: capped(t, args.capital),
        }
        series = {}
        for name, fn in arms.items():
            if name.startswith("random"):
                # averaged over DRAWS draws: one draw of 20 names out of 120 is
                # mostly luck, and a single unlucky draw would flatter top_20.
                acc, ex = [], []
                for d in range(DRAWS):
                    rng2 = np.random.default_rng(1000 + d)
                    rp = make_arms(ret, turn, rng2)[2]      # random_pick
                    s, e = walk(ret, rp, dates)
                    acc.append(s)
                    ex.append(e)
                s = pd.concat(acc, axis=1).mean(axis=1)
                e = float(np.mean(ex))
            else:
                s, e = walk(ret, fn, dates)
            series[name] = s
            rows.append({"universe": tag, "arm": name, "cagr_pct": cagr_of(s),
                         "ann_vol_pct": float(s.std() * np.sqrt(SESSIONS) * 100),
                         "max_dd_pct": maxdd_of(s), "deployed_pct": 100 * e})

        # cash_matched: hold_all scaled to capped's average deployment. This is
        # what "half in cash" alone buys you, with NO liquidity tilt at all.
        k = series["capped"]
        e_cap = [r for r in rows if r["universe"] == tag
                 and r["arm"] == "capped"][0]["deployed_pct"] / 100.0
        s = series["hold_all"] * e_cap
        series["cash_matched"] = s
        rows.append({"universe": tag, "arm": "cash_matched", "cagr_pct": cagr_of(s),
                     "ann_vol_pct": float(s.std() * np.sqrt(SESSIONS) * 100),
                     "max_dd_pct": maxdd_of(s), "deployed_pct": 100 * e_cap})
        curves[tag] = series
        del k

        # per decade, on the two arms the question turns on
        print(f"\n  {tag}: by decade, CAGR %/yr")
        dec = pd.Series(dates.year // 10 * 10, index=dates)
        head = ["hold_all", f"top_{N_HOLD}", f"random_{N_HOLD}",
                "capped", "cash_matched"]
        print("    " + f"{'decade':<8}" + "".join(f"{h:>14}" for h in head))
        for d, g in dec.groupby(dec):
            if len(g) < SESSIONS // 2:
                continue
            line = f"    {int(d):<8}"
            for h in head:
                line += f"{cagr_of(series[h].loc[g.index]):>14.2f}"
            print(line)

    out = pd.DataFrame(rows)
    stamp = date.today().isoformat() + (f"_from{args.start}" if args.start else "")
    (OUT / "measurements").mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "measurements" / f"liquidity_screen_{stamp}.csv", index=False)

    for tag in books:
        s_ = out[out.universe == tag].set_index("arm")
        print(f"\n=== {tag}: no rules, no stops, monthly rebalance ===")
        print(f"  {'arm':<14} {'CAGR':>8} {'vol':>8} {'max dd':>9} {'deployed':>10}")
        for a in s_.index:
            r = s_.loc[a]
            print(f"  {a:<14} {r.cagr_pct:>8.2f} {r.ann_vol_pct:>8.2f} "
                  f"{r.max_dd_pct:>9.2f} {r.deployed_pct:>9.1f}%")
        top, ran = s_.loc[f"top_{N_HOLD}"], s_.loc[f"random_{N_HOLD}"]
        cap, cm = s_.loc["capped"], s_.loc["cash_matched"]
        print(f"\n  does the turnover RANKING have content?"
              f"  top_{N_HOLD} - random_{N_HOLD} = "
              f"{top.cagr_pct - ran.cagr_pct:+.2f} points")
        print(f"  does the liquidity TILT have content?"
              f"  capped - cash_matched     = "
              f"{cap.cagr_pct - cm.cagr_pct:+.2f} points")
        print(f"  what CONCENTRATION alone buys? random_{N_HOLD} - hold_all = "
              f"{ran.cagr_pct - s_.loc['hold_all'].cagr_pct:+.2f} points")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, tag in zip(axes, books):
        for name, s in curves[tag].items():
            ax.plot(s.index, (1 + s).cumprod(),
                    lw=2.0 if name in ("hold_all", "capped") else 1.0,
                    label=name)
        ax.set_yscale("log")
        ax.set_title(f"{tag}: growth of 1, no rules")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.tight_layout()
    png = OUT / "figures" / f"liquidity_screen_{stamp}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}")
    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

"""Linda Raschke and Larry Connors, "Street Smarts" (1995) -- the setups we had
never tested, scored in the same harness as the board's own entries.

WHAT THIS ANSWERS. The board's ten entry families are ours. They lose to
buy-and-hold. The obvious objection is that we never tried the rules a
practitioner would actually name, so this codes the book's setups from the text
and puts them through the identical test: every entry is scored against a
RANDOM-TIMING control on the same symbol, same stop, same exit logic, same
trade count, random entry date. What survives is timing skill, not drift --
the control draws from the same survivor symbols, so survivorship differences
out.

WHAT IS FAITHFUL AND WHAT IS NOT. Read this before quoting any number.

  * Her entries are STOP ORDERS filled intraday ("place a buy stop 5-10 ticks
    above the previous 20-day low"). We hold daily bars, so we fire the signal
    on the bar where that stop would have been reachable and enter AT ITS
    CLOSE. That is strictly worse than her fill on a reversal day and strictly
    better on a day that ran away from the stop. It is not her entry price.
  * Her exits are 2 TO 6 BARS with partial profit-taking and a trailed stop.
    Ours is the board's: a fixed stop plus a max hold. Running at MAXHOLD=60
    tests her ENTRY against a 10-30x longer holding period, which is not her
    strategy at all -- so every rule is also run at MAXHOLD=6, her own window.
    Both are reported. Neither models the partial scale-out.
  * 80-20's, Momentum Pinball and the ADX Gapper are DAY TRADES or one-to-two
    day flips in the book. At MAXHOLD=60 they are being asked a question their
    author never asked.
  * MOMENTUM PINBALL's setup (a 3-period RSI of the 1-period change, below 30)
    is coded as written; its TRIGGER is "a buy stop above the HIGH of the first
    hour", which daily bars cannot see. What is tested is the setup with an
    at-the-close entry the next day -- a weakened version, flagged as such.
  * 2-PERIOD ROC is DELIBERATELY ABSENT. Its content is Taylor's buy-day /
    sell-short-day labelling plus an intraday pivot; a daily proxy would be our
    invention wearing her name, which is worse than a gap in the table.
  * WHIPLASH is the one setup that is exactly representable: she buys MOC, and
    so do we.

Reads /data/clean/kitelab/{US_,}*_day.parquet through us_rules.load. Writes
output/measurements/raschke_<date>.csv and output/figures/raschke_<date>.png.
Touches no stamped module: the signals live here, and us_rules.fires_for is
swapped for this module's dispatcher at run time rather than edited into
kitelab/entries.py (which is in the cache stamp and would cost a ~103-min
rebuild to hold ten rules the board does not trade).
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

from kitelab import indicators                                    # noqa: E402
from scripts import us_rules                                      # noqa: E402
from scripts.us_rules import (us_universe, nse_universe,           # noqa: E402
                              run_universe)

OUT = Path(__file__).resolve().parents[1] / "output"
FIG, MEAS = OUT / "figures", OUT / "measurements"

# Her holding window, and the board's. Both are run; the first is her strategy,
# the second is her entry inside our machinery.
HOLDS = [6, 60]

LOOKBACK = 20          # the Turtle lookback she reverses, chapters 4 and 5


# ---------------------------------------------------------------------------
# the setups. Each returns a boolean array, one element per session, True on
# the bar we would enter AT THE CLOSE OF.
# ---------------------------------------------------------------------------
def _parts(bars: pd.DataFrame):
    o = pd.Series(bars["open"].to_numpy(float))
    h = pd.Series(bars["high"].to_numpy(float))
    l = pd.Series(bars["low"].to_numpy(float))
    c = pd.Series(bars["close"].to_numpy(float))
    return o, h, l, c


def tsoup(bars):
    """TURTLE SOUP (ch 4). Today makes a new 20-day low, and the previous
    20-day low was made at least four sessions earlier -- "this is very
    important", because a low set yesterday means nobody was trapped. She buys
    a stop just above the old low the same day; we take the close."""
    o, h, l, c = _parts(bars)
    prior = l.shift(1)
    prior_low = prior.rolling(LOOKBACK, min_periods=LOOKBACK).min()
    # "at least four trading sessions earlier" == the 20-day low is not one of
    # the three most recent bars. Written as two rolling minima rather than an
    # argmin apply: exactly the same test, and it does not run Python per bar.
    recent = prior.rolling(3, min_periods=3).min()
    return (l.le(prior_low) & prior_low.lt(recent)).fillna(False).to_numpy(bool)


def tsoup1(bars):
    """TURTLE SOUP PLUS ONE (ch 5). Same trap, one day later: day one makes the
    new 20-day low AND CLOSES at or below the old one (so the close-only
    breakout players are trapped too), previous low at least three sessions
    back. Entry is day two."""
    o, h, l, c = _parts(bars)
    prior = l.shift(1)
    prior_low = prior.rolling(LOOKBACK, min_periods=LOOKBACK).min()
    recent = prior.rolling(2, min_periods=2).min()      # "at least three"
    day_one = l.le(prior_low) & c.le(prior_low) & prior_low.lt(recent)
    return day_one.shift(1).fillna(False).to_numpy(bool)


def eighty20(bars):
    """80-20's (ch 6). Yesterday opened in the top 20% of its range and closed
    in the bottom 20% -- a full-day reversal that traps the sellers -- and
    today trades back below yesterday's low. Her trade is a DAY TRADE."""
    o, h, l, c = _parts(bars)
    rng = (h - l).replace(0.0, np.nan)
    op = (o - l) / rng
    cp = (c - l) / rng
    trap = op.ge(0.80) & cp.le(0.20)
    return (trap.shift(1) & l.lt(l.shift(1))).fillna(False).to_numpy(bool)


def pinball(bars):
    """MOMENTUM PINBALL (ch 7), SETUP ONLY -- see the module docstring. A
    3-period RSI of the 1-period net change ("LBR/RSI") below 30 on day one;
    entry is day two. Her trigger and her stop are both the first hour's range,
    which daily bars cannot see, so this is her setup with our entry."""
    o, h, l, c = _parts(bars)
    lbr = indicators.rsi(c.diff(), 3)
    return lbr.lt(30).shift(1).fillna(False).to_numpy(bool)


def anti(bars):
    """THE "ANTI" (ch 9). Trend is the slope of the slow stochastic %D. The
    fast line %K pulls back towards it and then HOOKS back up in the direction
    of the slow line. 7-period %K, 10-period %D, as she specifies."""
    o, h, l, c = _parts(bars)
    hh = h.rolling(7, min_periods=7).max()
    ll = l.rolling(7, min_periods=7).min()
    k = 100 * (c - ll) / (hh - ll).replace(0.0, np.nan)
    k = k.rolling(4, min_periods=4).mean()          # her "smoothing, default 4"
    d = k.rolling(10, min_periods=10).mean()
    slow_up = d.gt(d.shift(3))                      # "a definite upward trend"
    pulled = k.shift(1).lt(k.shift(2))              # consolidation into the slow line
    hook = k.gt(k.shift(1))                         # "turn up once again"
    return (slow_up & pulled & hook).fillna(False).to_numpy(bool)


def adxgap(bars):
    """ADX GAPPER (ch 11). A strong trend (12-period ADX above 30, 28-period
    +DI above -DI), a gap AGAINST it, and then the trend resuming: her buy stop
    sits at yesterday's low, so we require the bar to trade back up to it."""
    o, h, l, c = _parts(bars)
    adx12, _, _ = indicators.adx(h, l, c, 12)
    _, pdi, mdi = indicators.adx(h, l, c, 28)
    strong = adx12.gt(30) & pdi.gt(mdi)
    gapped = o.lt(l.shift(1))
    filled = h.ge(l.shift(1))                       # the buy stop is reachable
    return (strong & gapped & filled).fillna(False).to_numpy(bool)


def whiplash(bars):
    """WHIPLASH (ch 12). Gap below yesterday's low, then a day that closes
    above its own open AND in the top half of its range -- the gap failed. She
    buys MOC, which is exactly what our harness does, so this setup is the one
    faithful entry in the file."""
    o, h, l, c = _parts(bars)
    rng = (h - l).replace(0.0, np.nan)
    return (o.lt(l.shift(1)) & c.gt(o)
            & ((c - l) / rng).ge(0.50)).fillna(False).to_numpy(bool)


def gap3(bars):
    """THREE-DAY UNFILLED GAP REVERSAL (ch 13), long side. An unfilled gap DOWN
    -- the whole bar trades below yesterday's low, so the gap never closed --
    and then, within the next three sessions, price trades back above that gap
    day's high. She rests the stop for three sessions; we fire on the first bar
    that reaches it."""
    o, h, l, c = _parts(bars)
    unfilled = h.lt(l.shift(1))
    out = np.zeros(len(c), dtype=bool)
    hv = h.to_numpy(float)
    uf = unfilled.fillna(False).to_numpy(bool)
    for i in np.flatnonzero(uf):
        trigger = hv[i]
        for j in range(i + 1, min(i + 4, len(hv))):
            if hv[j] > trigger:
                out[j] = True
                break
    return out


def idnr4(bars):
    """RANGE CONTRACTION (ch 19). An ID/NR4 bar -- an inside day that is also
    the narrowest range of the last four -- and then the breakout. She brackets
    it with a buy stop and a sell stop; this is the buy side only, so the
    reversal leg of her rule is NOT modelled and the losses are understated."""
    o, h, l, c = _parts(bars)
    rng = h - l
    inside = h.lt(h.shift(1)) & l.gt(l.shift(1))
    nr4 = rng.le(rng.rolling(4, min_periods=4).min())
    setup = (inside & nr4).shift(1)
    return (setup & h.gt(h.shift(1))).fillna(False).to_numpy(bool)


def hvcrabel(bars):
    """HISTORICAL VOLATILITY MEETS TOBY CRABEL (ch 20). Six-day historical
    volatility under half the 100-day reading -- a coiled spring -- plus an
    inside or NR4 bar, then the breakout. Buy side only, same caveat as
    idnr4."""
    o, h, l, c = _parts(bars)
    lr = np.log(c / c.shift(1))
    hv6 = lr.rolling(6, min_periods=6).std()
    hv100 = lr.rolling(100, min_periods=100).std()
    coiled = (hv6 / hv100.replace(0.0, np.nan)).lt(0.50)
    rng = h - l
    inside = h.lt(h.shift(1)) & l.gt(l.shift(1))
    nr4 = rng.le(rng.rolling(4, min_periods=4).min())
    setup = (coiled & (inside | nr4)).shift(1)
    return (setup & h.gt(h.shift(1))).fillna(False).to_numpy(bool)


RULES = {
    "tsoup": tsoup, "tsoup1": tsoup1, "eighty20": eighty20,
    "pinball": pinball, "anti": anti, "adxgap": adxgap,
    "whiplash": whiplash, "gap3": gap3, "idnr4": idnr4, "hvcrabel": hvcrabel,
}


def fires_for(symbol, entry, bars, masks):
    """Dispatcher installed over us_rules.fires_for. Raschke names resolve
    here; anything else is an error rather than a silent fallback, so a typo
    cannot quietly score one of the board's own rules under her name."""
    if entry not in RULES:
        raise KeyError(f"{entry!r} is not a Street Smarts setup: {sorted(RULES)}")
    return RULES[entry](bars)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", nargs="*", default=sorted(RULES))
    ap.add_argument("--holds", nargs="*", type=int, default=HOLDS)
    ap.add_argument("--pilot", type=int, default=0)
    args = ap.parse_args()
    bad = [r for r in args.rules if r not in RULES]
    if bad:
        sys.exit(f"unknown setups {bad}; known: {sorted(RULES)}")

    us = us_universe()
    if not us:
        sys.exit("no US_*_day.parquet in CLEAN -- run scripts.us_fetch first")
    books = {"US": us, "NSE": nse_universe(len(us))}
    if args.pilot:
        books = {k: v[:args.pilot] for k, v in books.items()}
    print("universes: " + ", ".join(f"{k} {len(v)}" for k, v in books.items()))
    print(f"setups: {', '.join(args.rules)}")
    print(f"holding windows: {args.holds} bars "
          f"(6 is hers, 60 is the board's)\n")

    us_rules.fires_for = fires_for              # the only patch; see docstring
    t0 = time.time()
    rows = []
    for hold in args.holds:
        us_rules.MAXHOLD = hold                 # _exit_from reads this global
        print(f"\n########## MAX HOLD = {hold} bars ##########")
        for tag, syms in books.items():
            got = run_universe(tag, syms, args.rules, t0)
            for r in got:
                r["max_hold"] = hold
            rows += got
    out = pd.DataFrame(rows)
    if out.empty:
        sys.exit("no rows produced")

    MEAS.mkdir(parents=True, exist_ok=True)
    path = MEAS / f"raschke_{date.today()}.csv"
    out.to_csv(path, index=False)
    print(f"\nwrote {path}  ({len(out)} rows x {len(out.columns)} cols)")

    print("\n=== edge over random timing, bps per trade, YEAR-CLUSTERED ===")
    print("    (the naive t is not shown: entry-year clustering moves the "
          "standard error 2.4-3.6x)")
    piv = out.pivot_table(index=["family", "stop"],
                          columns=["max_hold", "universe"],
                          values="yr_edge_bps")
    print(piv.round(1).to_string())
    # Bonferroni across every row in the table, at the median degrees of
    # freedom the year clustering leaves. Computed rather than quoted: the
    # 3.24 figure in earlier scripts was for a 26-row table and is wrong here.
    from scipy import stats
    n = len(out)
    df = max(int(out.n_years.median()) - 1, 1)
    tcrit = float(stats.t.ppf(1 - 0.05 / (2 * n), df))
    hits = out[(out.t_year_cluster.abs() >= tcrit)]
    print(f"\n{len(hits)} of {n} rows clear |t| >= {tcrit:.2f} "
          f"(Bonferroni across {n} rows, df={df}); "
          f"{(out.t_year_cluster >= tcrit).sum()} of them positive")
    loose = out[out.t_year_cluster.abs() >= 2.0]
    print(f"{len(loose)} of {n} clear an UNCORRECTED |t| >= 2.0, where "
          f"{0.05 * n:.1f} are expected by chance alone; "
          f"{(out.t_year_cluster >= 2.0).sum()} of those are positive")
    if len(hits):
        print(hits[["universe", "family", "stop", "max_hold", "trades",
                    "yr_edge_bps", "t_year_cluster", "n_years"]]
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
    fig, axes = plt.subplots(1, len(args.holds), figsize=(7 * len(args.holds), 6),
                             squeeze=False)
    for ax, hold in zip(axes[0], args.holds):
        d = out[out.max_hold == hold]
        y = np.arange(len(fams))
        for off, (tag, colr) in enumerate([("US", "#1f77b4"), ("NSE", "#d62728")]):
            v = [d[(d.family == f) & (d.universe == tag)].yr_edge_bps.median()
                 for f in fams]
            ax.barh(y + (off - 0.5) * 0.4, v, height=0.4, color=colr, label=tag)
        ax.set_yticks(y); ax.set_yticklabels(fams)
        ax.axvline(0, color="k", lw=1)
        ax.set_xlabel("edge over random timing, bps/trade (year-clustered)")
        ax.set_title(f"Street Smarts setups, max hold {hold} bars")
        ax.legend()
    fig.tight_layout()
    fp = FIG / f"raschke_{date.today()}.png"
    fig.savefig(fp, dpi=110)
    print(f"wrote {fp}")


if __name__ == "__main__":
    main()

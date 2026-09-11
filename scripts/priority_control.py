"""Does ANY signal-priority rule beat a coin flip once fills are capped?

    python -m scripts.priority_control --pilot      # 1 cell, timing probe
    python -m scripts.priority_control              # the full grid

THE QUESTION. A rule points at ~200 stocks a day; the account can fund ~3. So
something picks, that something is not part of the strategy, and portfolio.py
says so in as many words: "that something is not part of the strategy, and on
these settings it is deciding half the trades." The board sweeps five orderings
(portfolio.PRIORITIES) and has no null control, so it can say which of the five
won but not whether winning meant anything. This script supplies the control.

WHY `time` IS NOT THE CONTROL, though the board treats it as one. It sorts on
entry_ts alone and leaves ties to list order -- and the cached list is grouped by
symbol in config order (HAL, HINDZINC, IRFC, ABB, ...). So `time` means "the
symbol nearer the top of the config file wins the cash, every day, for twenty
years": a fixed, undesigned bias, not an absence of one. A SEEDED SHUFFLE is the
absence of one, and 20 of them give a distribution to score the named rules
against rather than a single number.

HOW A NEW ORDERING IS TESTED WITH NO REBUILD. portfolio._order sorts by
entry_ts with Python's stable sort when priority is "time", so ties keep input
order. Pre-sorting the list and passing priority="time" therefore installs an
arbitrary tie-break WITHOUT editing kitelab/*.py -- so no signal-cache digest
moves and the ~103-minute rebuild is not triggered. Verified 2026-09-11: the
same cell gives 10.43% in cache order and 20.59% under seed 7.

COSTS ARE ON, and that is the point of this run. An earlier uncapped probe had
`illiquid` at 29.71% and `wide` at 24.11%, which is the artefact wf_daily.py
already diagnosed: uncapped, illiquid-first puts Rs5cr into names that trade
Rs0.4cr in a day. Here slippage.ENABLED=True and MAX_PARTICIPATION=0.01 (one
order <= 1% of daily turnover), matching the board's only shipped fill regime,
and the spread is charged with slippage.apply_spread exactly as dashboard_data
does it.

THE FOUR NEW ORDERINGS, none of them on the board. All read only what was on the
tape by the PREVIOUS close, matching slippage.liquidity_at's convention:
  mom_hi / mom_lo   12-month return, strongest / weakest first. The weakest-first
                    arm is a CONTROL, not a proposal: if it scores like mom_hi,
                    momentum was never doing anything.
  atr_lo            calmest first, by 14-day ATR as % of price. Distinct from
                    `tight`, which reads ONE candle's range.
  nearhigh_hi       closest to its 52-week high first. The board uses this as an
                    entry FILTER (the five ath10 variants) but never as a
                    tie-break.

ADDED 2026-09-11, after mom_hi and `tight` came out as the only two orderings
above the coin flip: ARE THEY THE SAME THING? A tight stop means a big position
(value = risk / stop%), and a stock that has been climbing may also be a stock
whose recent candle ranges are small relative to price. If `tight` is just a
noisy proxy for momentum, it should collapse once momentum is held fixed.
  mom_tight    rank-average of both, best-on-both first -- do they ADD?
  mom_resid    momentum percentile with the tightness percentile regressed OUT
               (one global least-squares beta). Survives => momentum is not
               position size in disguise.
  tight_resid  the mirror: tightness with momentum regressed out. Collapses to
               the coin flip => `tight` was only ever measuring momentum.
  mom_wide     high momentum but WIDEST stop first -- the opposite corner, a
               control on whether the pairing direction matters at all.

NOT TESTABLE HERE: sector caps. config.merged is a bare symbol list with no
industry field, so that needs new data, not new code.

BANNED, and portfolio.py already blocks it: ranking on exit_price, net_profit,
r_multiple, bars_held or exit_reason. That is not a priority rule, it is a time
machine.

WHAT THE FULL RUN FOUND (2026-09-11, 38 cells = 19 rules x 2 account sizes,
each against its own 20 shuffles; costs on). Median gap to the shuffle mean,
and how many of the 38 cells beat that mean:

    mom_hi       +3.84 pts   35/38     <- kept, now portfolio.TIE_BREAK
    nearhigh_hi  +2.37 pts   29/38     <- kept
    tight        +1.06 pts   29/38     <- kept
    time         +0.94 pts   23/38     (not a null -- see above)
    illiquid     -0.32 pts   19/38     retired from the board
    liquidity    -0.57 pts   13/38     retired; was the shipped default
    wide         -0.80 pts   16/38     retired
    mom_lo       -3.62 pts    0/38     the control, lost every cell

Split 14 in-sample / 24 out-of-sample cells (the hypothesis was formed on seven
caches; twelve were added afterwards and never looked at first): mom_hi is
+4.68/14-of-14 in-sample and +3.84/21-of-24 out-of-sample, so hindsight is not
carrying it. THE mom_hi COLUMN OF THE CSV IS THE PRE-FIX ONE -- it was written
before the _desc/_asc fix below and puts no-history trades first. The corrected
figures quoted here come from output/measurements/mom_hi_engine_recheck_2026-09-11.csv,
which re-ran mom_hi alone against the same shuffles. Every other column
reproduces under the fix unchanged (checked: nearhigh_hi and tight, diff 0.00).

Reads:  the 19 *_all.pkl signal caches, the daily parquet candles.
Writes: output/measurements/priority_control_2026-09-11.csv   (resumable)
        output/priority_control.png
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import time

import numpy as np

from kitelab import config, frames, portfolio, signals, slippage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
CSV = os.path.join(OUT, "measurements", "priority_control_2026-09-11.csv")
PNG = os.path.join(OUT, "priority_control.png")

# One order <= 1% of the stock's daily turnover -- dashboard_data's
# REALISTIC_PARTICIPATION, restated here so this script has no import on it.
PARTICIPATION = 0.01
RISK = 0.01
CAPITALS = (200_000, 10_000_000)
SEEDS = tuple(range(20))
YEAR = 250          # sessions in a year, this project's convention

# ALL NINETEEN since 2026-09-11 (user: "run it on all 19"). The first pass ran
# seven, chosen to span the board, and mom_hi beat the shuffle in 14 of those 14
# cells. Seven strategies is seven correlated draws, so the full board is the
# check that matters: the twelve added here were NOT looked at before the
# hypothesis was fixed, which makes them a genuine out-of-sample test of it.
# Signal counts run 1,799 to 236,176. Order is registry.REGISTRY's.
CELLS = [
    ("EMA_b0",           "EMA · M/W/D · no band"),
    ("QMW_b0",           "EMA · Q/M/W · no band"),
    ("Turtle_w20_20_10", "Turtle 20-10 + weekly"),
    ("Turtle_w20_55_20", "Turtle 55-20 + weekly"),
    ("Turtle_1tf_20_10", "Turtle 20-10 (1 TF)"),
    ("Turtle_1tf_55_20", "Turtle 55-20 (1 TF)"),
    ("EMA_WD",           "EMA · W/D"),
    ("EMA_MD",           "EMA · M/D"),
    ("EMA_MW",           "EMA · M/W"),
    ("EMA_QW",           "EMA · Q/W"),
    ("EMA_QM",           "EMA · Q/M"),
    ("EMA_QD",           "EMA · Q/D"),
    ("EMA_daily_only",   "EMA · daily only"),
    ("EMA_ath10_MWD",    "EMA · M/W/D · within 10% of the high"),
    ("EMA_ath10_MW",     "EMA · M/W · within 10% of the high"),
    ("EMA_ath10_QM",     "EMA · Q/M · within 10% of the high"),
    ("EMA_ath10_QD",     "EMA · Q/D · within 10% of the high"),
    ("EMA_ath10_WD",     "EMA · W/D · within 10% of the high"),
    ("HolyGrail_swing",  "Holy Grail · swing stop"),
]

REQUIRED = ("symbol", "entry_ts", "entry_price", "stop")


def features(symbols: list[str]) -> dict:
    """Per symbol: session stamps plus three ranking signals, each computed from
    data STRICTLY BEFORE that session, so ranking today's candidates never reads
    today's bar. Mirrors slippage.liquidity_at, which shifts for the same reason.

    Returns {symbol: (ts, mom12, atr_pct, from_high)} as aligned arrays.
    """
    out, skipped = {}, 0
    for sym in symbols:
        try:
            d = frames.daily(sym)
        except SystemExit:
            skipped += 1
            continue
        ts = d["ts"].to_numpy().astype("datetime64[ns]")
        c = d["close"].to_numpy().astype(float)
        h = d["high"].to_numpy().astype(float)
        lo = d["low"].to_numpy().astype(float)
        n = len(c)
        if n < 30:
            skipped += 1
            continue
        prev_c = np.concatenate(([np.nan], c[:-1]))          # previous close

        # 12-month return, previous close vs the close 250 sessions before it.
        mom = np.full(n, np.nan)
        if n > YEAR + 1:
            mom[YEAR + 1:] = prev_c[YEAR + 1:] / c[:n - YEAR - 1] - 1.0

        # 14-day ATR as a fraction of price, ending at the previous bar.
        tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
        atr = np.full(n, np.nan)
        if n > 15:
            k = np.convolve(tr, np.ones(14), "valid") / 14.0   # ends at bar i
            atr[15:] = k[:n - 15] / prev_c[15:]

        # Distance below the trailing 252-session high, ending at the previous bar.
        fh = np.full(n, np.nan)
        if n > 2:
            run = np.full(n, np.nan)
            for i in range(1, n):
                run[i] = np.max(h[max(0, i - 252):i])
            fh = prev_c / run - 1.0
        out[sym] = (ts, mom, atr, fh)
    if skipped:
        print(f"    {skipped} symbols skipped (no frame or under 30 bars)")
    return out


def attach(trades: list[dict], feat: dict) -> int:
    """Write _mom / _atr / _fh onto each trade. Returns how many got all three."""
    ok = 0
    for t in trades:
        f = feat.get(t["symbol"])
        if f is None:
            t["_mom"] = t["_atr"] = t["_fh"] = np.nan
            continue
        ts, mom, atr, fh = f
        i = int(np.searchsorted(ts, np.datetime64(t["entry_ts"], "ns"), "left"))
        if i >= len(ts):
            t["_mom"] = t["_atr"] = t["_fh"] = np.nan
            continue
        t["_mom"], t["_atr"], t["_fh"] = float(mom[i]), float(atr[i]), float(fh[i])
        ok += bool(np.isfinite(mom[i]) and np.isfinite(atr[i]) and np.isfinite(fh[i]))
    return ok


def _pct_rank(v: np.ndarray) -> np.ndarray:
    """Percentile rank in [0, 1], 1 = largest. NaN scores 0.0, i.e. ranked last,
    matching slippage.liquidity_at's "no history sorts last"."""
    out = np.zeros(len(v))
    ok = np.isfinite(v)
    if ok.sum() > 1:
        o = np.argsort(np.argsort(v[ok]))
        out[ok] = o / (ok.sum() - 1)
    return out


def ranks(trades: list[dict]) -> tuple[float, float, float]:
    """Attach percentile ranks and the two residualised scores. Ranks are global
    over the strategy's whole trade list: a rank transform is monotone, so the
    ordering WITHIN any one day is unchanged, and a single global regression is
    far more stable than fitting inside days that often hold only a few
    candidates. Returns (corr, beta, gamma) for reporting.
    """
    mom = np.array([t["_mom"] for t in trades])
    stop_pct = np.array([(t["entry_price"] - t["stop"]) / t["entry_price"]
                         if t["entry_price"] else np.nan for t in trades])
    r_mom = _pct_rank(mom)
    r_tight = _pct_rank(-stop_pct)        # 1 = tightest stop
    ok = np.isfinite(mom) & np.isfinite(stop_pct)
    corr = float(np.corrcoef(r_mom[ok], r_tight[ok])[0, 1]) if ok.sum() > 2 else float("nan")
    beta = float(np.polyfit(r_tight[ok], r_mom[ok], 1)[0]) if ok.sum() > 2 else 0.0
    gamma = float(np.polyfit(r_mom[ok], r_tight[ok], 1)[0]) if ok.sum() > 2 else 0.0
    for i, t in enumerate(trades):
        t["_rmom"], t["_rtight"] = float(r_mom[i]), float(r_tight[i])
        t["_resid_m"] = float(r_mom[i] - beta * r_tight[i])
        t["_resid_t"] = float(r_tight[i] - gamma * r_mom[i])
    return corr, beta, gamma


# FIXED 2026-09-11. The first version of this was one function returning
# +inf for NaN, with a docstring claiming "NaN sorts last whichever direction
# we are going". That is true only for the ascending keys. For a descending
# key written as -_last(x), NaN became -inf and sorted FIRST -- the exact
# opposite -- so every mom_hi and nearhigh_hi run put the trades with no
# history at the front of the queue. The engine sorts them LAST
# (portfolio.momentum_at returns -inf and _order negates it, matching
# slippage.liquidity_at's 0.0 floor). 5.8%-14.0% of trades per cache have no
# history, and re-scoring mom_hi under the engine's convention moved single
# cells by up to 3.5 CAGR points. The two helpers below cannot be got wrong
# the same way: each returns the FINAL sort key, and both send a missing
# value to +inf, which is last in a plain ascending sort either way.
def _desc(x):
    """Biggest first; a missing value sorts LAST, as portfolio._order does."""
    return math.inf if not np.isfinite(x) else -x


def _asc(x):
    """Smallest first; a missing value sorts LAST, as portfolio._order does."""
    return math.inf if not np.isfinite(x) else x


def ordered(trades: list[dict], name: str) -> tuple[list[dict], str]:
    """(list pre-sorted so a stable sort keeps it, priority to pass to run)."""
    if name in portfolio.PRIORITIES:
        return trades, name
    if name.startswith("rand"):
        x = list(trades)
        random.Random(int(name[4:])).shuffle(x)
        return x, "time"
    key = {"mom_tight":   lambda t: -(t["_rmom"] + t["_rtight"]),
           "mom_resid":   lambda t: -t["_resid_m"],
           "tight_resid": lambda t: -t["_resid_t"],
           "mom_wide":    lambda t: -(t["_rmom"] + (1.0 - t["_rtight"])),
           "mom_hi":      lambda t: _desc(t["_mom"]),
           "mom_lo":      lambda t: _asc(t["_mom"]),
           "atr_lo":      lambda t: _asc(t["_atr"]),
           "nearhigh_hi": lambda t: _desc(t["_fh"])}[name]
    # Symbol last so the result is fully deterministic, as portfolio._order does.
    return sorted(trades, key=lambda t: (key(t), t["symbol"])), "time"


def plot(rows: list[dict]) -> None:
    """One panel per cell: the 20 shuffles as a grey band, every named and new
    ordering as a dot. The band is the null; a dot outside it is the finding."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cells: dict = {}
    for r in rows:
        cells.setdefault((r["label"], int(r["capital"])), {})[r["ordering"]] = float(r["cagr"])
    cells = {k: v for k, v in sorted(cells.items()) if len(v) >= 29}
    named = list(portfolio.PRIORITIES)
    new = ["mom_hi", "mom_lo", "atr_lo", "nearhigh_hi"]
    fig, ax = plt.subplots(figsize=(max(13, 0.62 * len(cells)), 7.0))
    xs = range(len(cells))
    for i, ((lab, cap), m) in enumerate(cells.items()):
        rs = sorted(m[f"rand{s}"] for s in SEEDS)
        ax.add_patch(plt.Rectangle((i - 0.34, rs[0]), 0.68, rs[-1] - rs[0],
                                   color="0.85", zorder=1))
        ax.plot([i - 0.34, i + 0.34], [sum(rs) / len(rs)] * 2, color="0.45", lw=1, zorder=2)
        for j, n in enumerate(named):
            ax.plot(i - 0.22 + j * 0.07, m[n], "o", ms=4, color="#1f77b4", zorder=3)
        for j, n in enumerate(new):
            c = {"mom_hi": "#d62728", "mom_lo": "#9467bd",
                 "atr_lo": "#2ca02c", "nearhigh_hi": "#ff7f0e"}[n]
            ax.plot(i + 0.10 + j * 0.07, m[n], "D", ms=4, color=c, zorder=3)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{l[:26]}\n{'Rs2L' if c == 200000 else 'Rs1cr'}"
                        for (l, c) in cells], rotation=90, ha="center", fontsize=6)
    ax.set_ylabel("CAGR %, costs and 1% participation cap ON")
    ax.set_title("Does any signal-priority rule beat a coin flip?\n"
                 "grey band = range of 20 seeded shuffles (the null); "
                 "blue = the 5 on the board; red = 12-month momentum first")
    h = [plt.Line2D([], [], marker="o", ls="", color="#1f77b4", label="board's 5"),
         plt.Line2D([], [], marker="D", ls="", color="#d62728", label="mom_hi (NEW)"),
         plt.Line2D([], [], marker="D", ls="", color="#9467bd", label="mom_lo (control)"),
         plt.Line2D([], [], marker="D", ls="", color="#2ca02c", label="atr_lo (NEW)"),
         plt.Line2D([], [], marker="D", ls="", color="#ff7f0e", label="nearhigh_hi (NEW)")]
    ax.legend(handles=h, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(PNG, dpi=140)
    print(f"  wrote {PNG}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="one cell, for timing")
    args = ap.parse_args()

    cfg = config.load()
    universe = cfg.merged
    print(f"\n  universe: {len(universe)} symbols, first 3: {universe[:3]}")

    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = PARTICIPATION
    slippage.reset()
    print(f"  COSTS ON: slippage.ENABLED={slippage.ENABLED}, "
          f"MAX_PARTICIPATION={slippage.MAX_PARTICIPATION}")

    names = (list(portfolio.PRIORITIES)
             + [f"rand{s}" for s in SEEDS]
             + ["mom_hi", "mom_lo", "atr_lo", "nearhigh_hi"]
             + ["mom_tight", "mom_resid", "tight_resid", "mom_wide"])
    cells = CELLS[:1] if args.pilot else CELLS
    caps = CAPITALS[1:] if args.pilot else CAPITALS
    names = names[:3] + ["rand0", "mom_hi"] if args.pilot else names
    print(f"  {len(cells)} strategies x {len(caps)} account sizes x "
          f"{len(names)} orderings = {len(cells) * len(caps) * len(names)} runs\n")

    done = {}
    if os.path.exists(CSV) and not args.pilot:
        with open(CSV) as fh:
            for r in csv.DictReader(fh):
                done[(r["cache"], r["capital"], r["ordering"])] = r
        print(f"  resuming: {len(done)} runs already on disk\n")

    print("  building ranking features from the daily candles...")
    t0 = time.time()
    feat = features(universe)
    print(f"    {len(feat)} symbols, {time.time() - t0:.0f}s\n")

    rows = list(done.values())
    for cache, label in cells:
        if all((cache, str(cap), name) in done for cap in caps for name in names):
            print(f"  {label}: all {len(caps) * len(names)} runs already on disk")
            continue
        base = signals.load(f"{cache}_all", universe)
        if not base:
            raise SystemExit(f"{label}: no cache -- run scripts.refresh first")
        missing = [k for k in REQUIRED if k not in base[0]]
        if missing:
            raise SystemExit(f"{label}: cached trades lack {missing}")
        n_before = len(base)
        # The spread is charged onto the cached trades, exactly as
        # dashboard_data does -- nothing in the simulation depends on the fill.
        trades = [slippage.apply_spread(t) for t in base]
        got = attach(trades, feat)
        corr, beta, gamma = ranks(trades)
        print(f"  {label}")
        print(f"    {n_before:,} cached -> {len(trades):,} after spread "
              f"(no filter) -> {got:,} with all 3 features "
              f"({100 * got / len(trades):.1f}%)")
        print(f"    rank corr(momentum, tightness) = {corr:+.3f}  "
              f"(beta {beta:+.3f}, gamma {gamma:+.3f})")

        for cap in caps:
            for name in names:
                k = (cache, str(cap), name)
                if k in done:
                    continue
                lst, prio = ordered(trades, name)
                t1 = time.time()
                r = portfolio.run(lst, cap, RISK, priority=prio)
                el = time.time() - t1
                row = {"cache": cache, "label": label, "capital": cap,
                       "ordering": name,
                       "kind": ("named" if name in portfolio.PRIORITIES
                                else "random" if name.startswith("rand")
                                else "new"),
                       "cagr": round(r["cagr_pct"], 3),
                       "mar": round(r["mar"], 3) if r["mar"] is not None else "",
                       "maxdd_pct": round(r["max_drawdown_pct"], 2),
                       "taken": len(r["taken"]), "signals": r["signals"],
                       "max_concurrent": r["max_concurrent"],
                       "exposure_pct": round(r["fully_invested_pct"], 2),
                       "median_cash_pct": round(r["median_cash_pct"], 2),
                       "wiped": r["wiped"], "seconds": round(el, 1)}
                rows.append(row)
                print(f"      Rs{cap:>10,}  {name:<12} CAGR {r['cagr_pct']:7.2f}%"
                      f"  taken {len(r['taken']):>7,}/{r['signals']:,}  ({el:.0f}s)")
                with open(CSV, "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
    plot(rows)
    print(f"\n  wrote {CSV} ({len(rows)} rows) and {PNG}\n")


if __name__ == "__main__":
    main()

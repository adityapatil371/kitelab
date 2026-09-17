"""Is `xrank N=20 / 3xATR / stop-exit` real? The four tests that could kill it.

    python3 -m scripts.xrank_gate --pilot        # time one leg, print an estimate
    python3 -m scripts.xrank_gate --leg placebo  # just the control
    python3 -m scripts.xrank_gate                # all four legs

WHAT IS ON TRIAL. Item 22 measured, at the account layer with costs on, a
median of **+3.51 CAGR points above buy-and-hold** on the `all` universe for
"hold the top 20 names by 252-session return, stop 3xATR below the entry
close, exit only on the stop" (12 of 20 cells beat hold). That is the first
positive median this project has produced in 22 items. It was also the best
of 25 (5 breadths x 5 universes), on a board whose own selection process
scored PBO 0.412 -- worse than a coin flip -- so the prior is bad and the
number is a LEAD, not a finding.

THE FOUR LEGS, cheapest and most decisive first:

  1. PLACEBO. Random names instead of ranked ones, matched session by session
     to the real signal's firing COUNT, same stop, same exit, same accounts,
     `SEEDS` seeds. This asks the only question that matters first: is it the
     momentum RANKING doing the work, or is "hold about 20 names at a time
     with a wide stop" worth +3.51 no matter which names? If the placebo lands
     on the same number, the rule is a portfolio SHAPE and the alpha is zero.
  2. THE BOARD'S OWN GATE. All three priorities (300 cells, not item 22's 92),
     the daily rule-minus-hold HAC t-test (`wf_attach.excess_stats`, the
     dashboard's fourth check) and the trade-level luck gate
     (`validation.bootstrap_one` -> t_gate = min(t_cluster, t_stat), the
     smaller of "beats zero" and "beats random timing on the same stocks"),
     judged against `validation.luck_hurdle` at four honesty levels.
  3. PERIOD. The same cells split by start year. Item 15 found the board's
     shortfall against hold SHRANK after 2017; if this rule's edge lives only
     in 2006-2012 it is a different market's fact, not a rule.
  4. KNIFE-EDGE. N in 15/20/25 crossed with lookback 126/252/504. A real
     effect has shoulders. A best-of-25 artifact is a spike with nothing
     either side of it.

PRE-REGISTERED PREDICTION, written before the run (this project's convention;
the last three predictions logged were all wrong, which is the point of
writing them down):
  (a) The placebo median lands within 2.0 CAGR points of the real +3.51 on
      `all`, i.e. most of the number is portfolio shape, not ranking.
  (b) 0 of the 300 cells clear the daily-excess gate at the board's hurdle.
  (c) t_gate on the `all` trade list is BELOW 1.645 (the uncorrected
      one-sided 5% bar) -- so it fails before multiple testing is even priced.
  (d) The N x lookback surface has shoulders rather than a spike, because
      item 22 already showed N=10 and N=50 either side of the N=20 peak.

WHY THIS IS A NEW FILE UNDER scripts/. Nothing here is in `signals._SUPPORT`
or `signals._ACCOUNT`, so it invalidates no cache and costs no rebuild. The
signal, the trade walk and the spread guard are IMPORTED from
scripts/entry_exit_grid.py, scripts/xrank_account.py and
scripts/xrank_breadth.py rather than copied, so the thing being judged cannot
drift from the thing that was measured.

Reads:  /data/clean/kitelab/dashboard.json     (axes, universe labels, hold key)
        the cleaned parquet candles, via kitelab.frames
        output/wf_attach_hold_<board key>.pkl  (hold curves, already built)
Writes: output/measurements/xrank_gate_<date>.csv   (every cell, every leg)
        output/xrank_gate_<date>.json               (the same, plus the t rows)
        output/xrank_gate_<date>.png                (the four legs, one figure)
"""
from __future__ import annotations

import argparse
import bisect
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from kitelab import config, portfolio, validation
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
import scripts.entry_exit_grid as G
from scripts.wf_pine import hold_cagrs, load_board, universes
from scripts.xrank_account import spread_off, trades_for

OUT = Path(__file__).resolve().parent.parent / "output"

STOP, EXIT = "atr3", "stop"       # the cell on trial
N_LIVE, LOOKBACK = 20, 252
SEEDS = [11, 22, 33, 44, 55]      # placebo draws
NS = [15, 20, 25]                 # knife-edge axes
LOOKBACKS = [126, 252, 504]
PRIO_ONE = ["mom_hi"]             # item 22's slice, for like-for-like compares
BLOCK = 20                        # column-shuffle block, see shuffled_signal


# ------------------------------------------------------------- signals ----
def ranked_signal(p, n, lookback):
    """Top `n` names by trailing `lookback`-session total return, each session.

    Identical to scripts/xrank_breadth.topn_signal at lookback=252, generalised
    on the window. method="first" breaks ties by column order so exactly n
    names fire on a session where several share a return.
    """
    c = p["close"]
    ret = c / c.shift(lookback) - 1.0
    rk = ret.rank(axis=1, ascending=False, method="first")
    return rk.le(n) & c.notna()


def shuffled_signal(p, real, seed):
    """The real firing panel with its SYMBOL LABELS permuted, history-matched.

    WHY THIS EXISTS (2026-09-17, after the first placebo came back confounded).
    placebo_signal matches the real signal's per-session firing COUNT but not
    its PERSISTENCE. Momentum ranking is sticky -- the same 20 names hold the
    top for weeks, and with one position per symbol the account only trades
    when membership changes -- so the real rule made 2,776 trades where the
    count-matched random control made ~31,400. That control varies WHICH names
    and HOW OFTEN at the same time, and turnover is the one thing this project
    has repeatedly shown eats a real gross edge (scripts/wf_pine.py).

    Permuting the columns instead keeps every firing RUN intact and only
    changes which stock it lands on: same sessions, same counts, same spell
    lengths, same number of distinct entries. Identity is then the only
    difference left. The permutation is restricted to blocks of columns with
    similar history length (sorted by count of finite closes, shuffled inside
    blocks of `BLOCK`), because handing a 5,000-session firing pattern to a
    name listed in 2023 would silently drop trades and re-introduce a count
    gap -- the very defect this control is here to remove.
    """
    c = p["close"]
    S = real.to_numpy(bool)
    live = np.isfinite(c.to_numpy(float)).sum(axis=0)
    order = np.argsort(live, kind="stable")
    rng = np.random.default_rng(seed)
    perm = np.empty(len(order), dtype=int)
    for lo in range(0, len(order), BLOCK):
        block = order[lo:lo + BLOCK]
        perm[block] = rng.permutation(block)
    return pd.DataFrame(S[:, perm], index=c.index, columns=c.columns)


def placebo_signal(p, real, seed):
    """Random names, matched session by session to `real`'s firing COUNT.

    The control holds everything constant except WHICH names are picked: the
    same sessions fire, the same number of names fire on each, the eligible
    pool is the same (a name is eligible on a session only if it has both a
    close and a full lookback behind it, exactly as the ranking required), and
    downstream the same stop, exit, spread, sizing and accounts apply. The one
    difference is that the pick is uniform instead of by momentum.
    """
    c = p["close"]
    eligible = (c / c.shift(LOOKBACK) - 1.0).notna().to_numpy() & c.notna().to_numpy()
    counts = real.to_numpy(bool).sum(axis=1)
    rng = np.random.default_rng(seed)
    out = np.zeros(eligible.shape, dtype=bool)
    for i in range(eligible.shape[0]):
        k = int(counts[i])
        if k <= 0:
            continue
        pool = np.flatnonzero(eligible[i])
        if len(pool) <= k:
            out[i, pool] = True
            continue
        out[i, rng.choice(pool, size=k, replace=False)] = True
    return pd.DataFrame(out, index=c.index, columns=c.columns)


# --------------------------------------------------------------- cells ----
def cells_with_daily(trades, unis, holds, hold_cagr, label, *, prios):
    """scripts/stop_sweep.cells_for plus the dashboard's daily-excess test.

    stop_sweep's loop stops at CAGR; the board's fourth gate is a HAC t on
    ~5,000 daily rule-minus-hold returns, which needs the hold CURVE and not
    just its CAGR. Same loop, one more field, so the two cannot disagree.
    """
    cells = {}
    for ukey, members in unis.items():
        subset = (trades if members is None
                  else [t for t in trades if t["symbol"] in members])
        if not subset:
            continue
        subset = sorted(subset, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        hold = holds.get(ukey)
        live_years = dd.gridded_years(stamps)
        for year in dd.START_YEARS:
            cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
            window = subset[cut:] if year in live_years else []
            if not window:
                continue
            for prio in prios:
                for risk in dd.RISKS:
                    for capital in dd.CAPITALS:
                        r = portfolio.run(window, capital, risk / 100, prio)
                        pay = dd.run_payload(r)
                        held = hold_cagr.get(f"{ukey}|{year}")
                        st = wa.excess_stats(r.get("curve") or [], hold)
                        key = f"{label}|{ukey}|{risk:g}|{capital}|1|{year}|{prio}"
                        cells[key] = {
                            "variant": label, "universe": ukey, "risk": risk,
                            "capital": capital, "start": year, "priority": prio,
                            "cagr": pay["cagr"], "taken": pay["taken"],
                            "wiped": bool(pay["wiped"]), "hold_cagr": held,
                            "beats_hold": (None if (held is None or pay["wiped"]
                                                    or pay["cagr"] is None)
                                           else bool(pay["cagr"] > held)),
                            "daily_t": (st or {}).get("t_hac"),
                            "daily_p": (st or {}).get("p"),
                            "daily_excess_pts": (st or {}).get("excess_pts"),
                            "daily_mde_80": (st or {}).get("mde_80"),
                        }
    return cells


def excess_of(cells, universe=None):
    """Median / count of CAGR-points-above-hold over live cells."""
    live = [c for c in cells.values()
            if c["cagr"] is not None and c["hold_cagr"] is not None
            and not c["wiped"] and (universe is None or c["universe"] == universe)]
    if not live:
        return {"cells": 0, "median": float("nan"), "beats": 0}
    ex = np.array([c["cagr"] - c["hold_cagr"] for c in live], dtype=float)
    return {"cells": len(live), "median": float(np.median(ex)),
            "beats": int(sum(1 for c in live if c["beats_hold"])),
            "p90": float(np.percentile(ex, 90)), "max": float(ex.max())}


def build(p, sig, label):
    """One signal panel -> spread-priced, board-shaped trades at STOP."""
    with spread_off():
        built, raw = trades_for(p, sig, stops=[STOP], quiet=True)
    n_raw = raw[STOP]
    priced = wa.spread_of(built[STOP])
    print(f"    {label:<22} {n_raw:>7,} trades -> {len(built[STOP]):>7,} after "
          f"sizing -> {len(priced):>7,} after the spread", flush=True)
    return priced


# ---------------------------------------------------------------- legs ----
def leg_shuffle(p, real, unis, holds, hold_cagr, flat):
    print("\nLEG 1b. LABEL SHUFFLE -- same firing runs, different stocks")
    real_tr = build(p, real, "real (top 20)")
    rows = [{"leg": "shuffle", "arm": "real", "seed": None,
             **excess_of(cells_keep(real_tr, unis, holds, hold_cagr,
                                    "real", PRIO_ONE, flat), "all")}]
    print(f"      real:    `all` median {rows[0]['median']:+6.2f}  "
          f"beats hold {rows[0]['beats']}/{rows[0]['cells']}", flush=True)
    for seed in SEEDS:
        sig = shuffled_signal(p, real, seed)
        tr = build(p, sig, f"shuffle seed {seed}")
        cells = cells_keep(tr, unis, holds, hold_cagr,
                           f"shuffle{seed}", PRIO_ONE, flat)
        rows.append({"leg": "shuffle", "arm": "shuffled", "seed": seed,
                     **excess_of(cells, "all")})
        r = rows[-1]
        print(f"      seed {seed}: `all` median {r['median']:+6.2f}  "
              f"beats hold {r['beats']}/{r['cells']}", flush=True)
    return rows


def leg_placebo(p, real, unis, holds, hold_cagr, flat):
    print("\nLEG 1. PLACEBO -- random names, same count per session, same stop")
    real_tr = build(p, real, "real (top 20)")
    rows = [{"leg": "placebo", "arm": "real", "seed": None,
             **excess_of(cells_keep(real_tr, unis, holds, hold_cagr,
                                    "real", PRIO_ONE, flat), "all")}]
    for seed in SEEDS:
        sig = placebo_signal(p, real, seed)
        tr = build(p, sig, f"placebo seed {seed}")
        cells = cells_keep(tr, unis, holds, hold_cagr,
                           f"placebo{seed}", PRIO_ONE, flat)
        rows.append({"leg": "placebo", "arm": "random", "seed": seed,
                     **excess_of(cells, "all")})
        r = rows[-1]
        print(f"      seed {seed}: `all` median {r['median']:+6.2f}  "
              f"beats hold {r['beats']}/{r['cells']}", flush=True)
    return rows


def cells_keep(trades, unis, holds, hold_cagr, label, prios, flat):
    cells = cells_with_daily(trades, unis, holds, hold_cagr, label, prios=prios)
    for key, c in cells.items():
        flat.append({"key": key, **c})
    return cells


def leg_gate(p, real, unis, holds, hold_cagr, flat):
    print("\nLEG 2. THE BOARD'S OWN GATE -- all three priorities, both tests")
    tr = build(p, real, "real, full board")
    cells = cells_keep(tr, unis, holds, hold_cagr, "gate",
                       dd.PRIORITIES, flat)
    print(f"    {len(cells)} cells built "
          f"(5 universes x {len(dd.START_YEARS)} starts x "
          f"{len(dd.PRIORITIES)} priorities x 2 risks x 2 capitals)")
    live = [c for c in cells.values() if c["daily_t"] is not None]
    print(f"    cells with a daily test: {len(live)} of {len(cells)} "
          f"({len(cells) - len(live)} too short)")

    trows = []
    for ukey, members in unis.items():
        subset = (tr if members is None
                  else [t for t in tr if t["symbol"] in members])
        if len(subset) < validation.MIN_TRADES:
            print(f"    {ukey:<8} {len(subset):>6,} trades -- below MIN_TRADES, skipped")
            continue
        row = validation.bootstrap_one(subset)
        if row is None:
            continue
        row["universe"] = ukey
        trows.append(row)
        print(f"    {ukey:<8} {row['n']:>6,} trades  mean_r {row['mean_r']:+.4f}  "
              f"drift_r {row['drift_r']:+.4f}  t_cluster {row['t_cluster']:>6}  "
              f"t_stat {row['t_stat']:>6}  t_gate {row['t_gate']:>6}", flush=True)
    return cells, trows


def leg_period(cells):
    print("\nLEG 3. PERIOD -- the same cells, split by start year")
    rows = []
    for year in dd.START_YEARS:
        sub = {k: c for k, c in cells.items()
               if c["start"] == year and c["universe"] == "all"}
        st = excess_of(sub)
        rows.append({"leg": "period", "start": year, **st})
        if st["cells"]:
            print(f"    from {year}: `all` median {st['median']:+6.2f}  "
                  f"beats hold {st['beats']}/{st['cells']}")
    return rows


def leg_knife(p, unis, holds, hold_cagr, flat):
    print("\nLEG 4. KNIFE-EDGE -- N x lookback, does the peak have shoulders?")
    rows = []
    for lb in LOOKBACKS:
        for n in NS:
            sig = ranked_signal(p, n, lb)
            tr = build(p, sig, f"N={n} lookback={lb}")
            cells = cells_keep(tr, unis, holds, hold_cagr,
                               f"n{n}lb{lb}", PRIO_ONE, flat)
            st = excess_of(cells, "all")
            rows.append({"leg": "knife", "n": n, "lookback": lb, **st})
            print(f"      N={n:<3} lookback={lb:<4} `all` median "
                  f"{st['median']:+6.2f}  beats hold {st['beats']}/{st['cells']}",
                  flush=True)
    return rows


# -------------------------------------------------------------- report ----
def hurdles(trows, cells):
    """The luck bar at four levels of honesty about how many things were tried."""
    print("\nVERDICT 1 -- the trade-level luck gate")
    levels = {"1 (uncorrected 5%)": 1.0,
              "5 (the breadths tried)": 5.0,
              "25 (breadth x universe, where +3.51 came from)": 25.0,
              "board n_eff 6.2 (what a 10th rule would face)": 6.2}
    if not trows:
        print("    no universe had MIN_TRADES trades -- nothing to gate")
        return []
    out = []
    for row in trows:
        t = validation._gate_t(row)
        line = [f"    {row['universe']:<8} t_gate {t:>6}"]
        for name, n_eff in levels.items():
            bar = validation.luck_hurdle(n_eff)
            line.append(f"{'PASS' if (t or -9) >= bar else 'fail'} @{bar:.2f}")
            out.append({"universe": row["universe"], "n_eff": n_eff,
                        "hurdle": round(bar, 3), "t_gate": t,
                        "pass": bool((t or -9) >= bar)})
        print("  ".join(line))
    print("    levels: " + " | ".join(f"{k} -> {validation.luck_hurdle(v):.2f}"
                                      for k, v in levels.items()))

    print("\nVERDICT 2 -- the daily rule-minus-hold test, the board's fourth gate")
    live = [c for c in cells.values() if c["daily_t"] is not None]
    if live:
        ps = np.array([c["daily_p"] for c in live], dtype=float)
        ts = np.array([c["daily_t"] for c in live], dtype=float)
        pair = [(c["daily_excess_pts"], c["daily_mde_80"]) for c in live
                if c["daily_excess_pts"] is not None and c["daily_mde_80"] is not None]
        big = sum(1 for e, m in pair if e >= m)
        bonf = validation.ALPHA / len(live)
        print(f"    {len(live)} tested cells   median t {np.median(ts):+.2f}   "
              f"max t {ts.max():+.2f}")
        print(f"    p <= 0.05 uncorrected : {int((ps <= 0.05).sum())} of {len(live)}"
              f"   (~{0.05*len(live):.0f} expected with no edge at all)")
        print(f"    p <= {bonf:.2e} Bonferroni: {int((ps <= bonf).sum())} of {len(live)}")
        print(f"    excess at least as big as this much data could DETECT: "
              f"{big} of {len(pair)}")
    return out


def figure(flat, placebo_rows, knife_rows, cells, stamp):
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))

    a = ax[0][0]
    real = [r["median"] for r in placebo_rows if r["arm"] == "real"]
    rand = [r["median"] for r in placebo_rows if r["arm"] in ("random", "shuffled")]
    a.axhline(0, color="#999", lw=0.8)
    a.bar(range(len(rand)), rand, color="#bbbbbb", label="placebo (random names)")
    if real:
        a.axhline(real[0], color="#c0392b", lw=2,
                  label=f"ranked, top {N_LIVE} ({real[0]:+.2f})")
    a.set_xticks(range(len(rand)))
    a.set_xticklabels([f"s{s}" for s in SEEDS] * (len(rand) // max(len(SEEDS), 1)),
                      fontsize=7)
    a.set_ylabel("median CAGR points vs buy-and-hold")
    a.set_title("1. Placebo: does the RANKING do the work?", fontsize=10)
    a.legend(fontsize=8)

    b = ax[0][1]
    grid = np.full((len(LOOKBACKS), len(NS)), np.nan)
    for r in knife_rows:
        grid[LOOKBACKS.index(r["lookback"])][NS.index(r["n"])] = r["median"]
    lim = np.nanmax(np.abs(grid)) or 1.0
    im = b.imshow(grid, cmap="RdYlGn", vmin=-lim, vmax=lim)
    b.set_xticks(range(len(NS)), [f"N={n}" for n in NS])
    b.set_yticks(range(len(LOOKBACKS)), [f"{l}d" for l in LOOKBACKS])
    for i in range(len(LOOKBACKS)):
        for j in range(len(NS)):
            if np.isfinite(grid[i][j]):
                b.text(j, i, f"{grid[i][j]:+.1f}", ha="center", va="center", fontsize=9)
    b.set_title("4. Knife-edge: median excess, `all` universe", fontsize=10)
    fig.colorbar(im, ax=b, fraction=0.045)

    c = ax[1][0]
    live = [x for x in cells.values() if x["daily_t"] is not None]
    if live:
        c.hist([x["daily_t"] for x in live], bins=25, color="#5b8dd6")
    c.axvline(1.645, color="#c0392b", ls="--", label="uncorrected 5%")
    c.axvline(validation.luck_hurdle(6.2), color="#111", ls="--",
              label=f"board hurdle {validation.luck_hurdle(6.2):.2f}")
    c.set_xlabel("HAC t on daily rule-minus-hold excess")
    c.set_ylabel("cells")
    c.set_title("2. The board's fourth gate, all 300 cells", fontsize=10)
    c.legend(fontsize=8)

    d = ax[1][1]
    sub = [x for x in cells.values()
           if x["universe"] == "all" and x["cagr"] is not None
           and x["hold_cagr"] is not None and not x["wiped"]]
    years = sorted({x["start"] for x in sub})
    d.axhline(0, color="#999", lw=0.8)
    if years:
        d.boxplot([[x["cagr"] - x["hold_cagr"] for x in sub if x["start"] == y]
                   for y in years], tick_labels=[str(y) for y in years])
    d.set_ylabel("CAGR points vs buy-and-hold")
    d.set_title("3. Period: `all` universe by start year", fontsize=10)

    fig.suptitle(f"xrank N={N_LIVE} / 3xATR / stop-exit -- four tests  ({stamp})")
    fig.tight_layout()
    path = OUT / f"xrank_gate_{stamp}.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"\n  wrote {path.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leg", default="all",
                    choices=["all", "placebo", "shuffle", "gate", "knife"])
    ap.add_argument("--pilot", action="store_true",
                    help="one placebo seed only, then stop -- times the loop")
    args = ap.parse_args()

    t_start = time.time()
    cfg = config.load()
    members = sorted(cfg.merged)
    print(f"\n  universe: {len(members)} symbols")
    print(f"  on trial: top {N_LIVE} by {LOOKBACK}-session return, "
          f"stop {STOP}, exit {EXIT}")

    print("\n0. panels, board axes and hold curves")
    with spread_off():
        p = G.panels(members)
    print(f"  close panel {p['close'].shape[0]:,} sessions x "
          f"{p['close'].shape[1]:,} symbols")
    payload = load_board()
    unis = universes(set(members), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(members), unis, [], board)
    hold_cagr = hold_cagrs(holds)
    print(f"  universes: {', '.join(f'{k}={len(v) if v else len(members)}' for k, v in unis.items())}")
    print(f"  hold CAGR cells: {len(hold_cagr)}, all|2018 = {hold_cagr.get('all|2018')}")

    real = ranked_signal(p, N_LIVE, LOOKBACK)
    fired = int(real.to_numpy().sum())
    print(f"  signal panel: {fired:,} firings, "
          f"{real.to_numpy().sum(axis=1).max()} max names in one session")

    if args.pilot:
        t0 = time.time()
        flat = []
        sig = placebo_signal(p, real, SEEDS[0])
        tr = build(p, sig, "placebo pilot")
        cells_keep(tr, unis, holds, hold_cagr, "pilot", PRIO_ONE, flat)
        one = time.time() - t0
        print(f"\n  one arm (trades + {len(flat)} cells): {one:.0f}s")
        print(f"  full run is 1 real + {len(SEEDS)} placebo + 1 gate (3x cells) "
              f"+ {len(NS)*len(LOOKBACKS)} knife arms")
        print(f"  estimate: {(one*(1+len(SEEDS)+3+len(NS)*len(LOOKBACKS)) + (time.time()-t_start-one))/60:.1f} min total")
        return

    flat, placebo_rows, knife_rows, trows = [], [], [], []
    cells = {}
    if args.leg in ("all", "placebo"):
        placebo_rows = leg_placebo(p, real, unis, holds, hold_cagr, flat)
    if args.leg in ("all", "shuffle"):
        placebo_rows = placebo_rows + leg_shuffle(p, real, unis, holds,
                                                  hold_cagr, flat)
    if args.leg in ("all", "gate"):
        cells, trows = leg_gate(p, real, unis, holds, hold_cagr, flat)
        leg_period(cells)
    if args.leg in ("all", "knife"):
        knife_rows = leg_knife(p, unis, holds, hold_cagr, flat)

    hrows = hurdles(trows, cells) if cells else []

    stamp = date.today().isoformat()
    OUT.joinpath("measurements").mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(flat)
    cpath = OUT / "measurements" / f"xrank_gate_{stamp}_{args.leg}.csv"
    df.to_csv(cpath, index=False)
    print(f"\n  wrote measurements/{cpath.name} "
          f"({len(df)} rows x {len(df.columns)} columns)")
    jpath = OUT / f"xrank_gate_{stamp}_{args.leg}.json"
    jpath.write_text(json.dumps(
        {"generated": stamp, "board": board, "on_trial":
         {"n": N_LIVE, "lookback": LOOKBACK, "stop": STOP, "exit": EXIT},
         "placebo": placebo_rows, "knife": knife_rows,
         "trade_level": trows, "hurdles": hrows}, indent=1, default=str))
    print(f"  wrote {jpath.name}")
    if args.leg == "all":
        figure(flat, placebo_rows, knife_rows, cells, stamp)
    print(f"\n  total {(time.time()-t_start)/60:.1f} min")


if __name__ == "__main__":
    main()

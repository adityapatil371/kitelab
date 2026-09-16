"""Does the RSI-free Pine rule earn a place on the board? -- the 300-cell test.

THE QUESTION, set by the user at the end of the 2026-09-16 session: "we will
test the rule thoroughly first thing in the next session to see if we can add
it to our set of rules". The candidate is the ported Pine strategy with its
RSI leg REMOVED -- Heikin-Ashi wick condition + higher-timeframe EMA(20)
filter, on weekly bars with a monthly filter.

WHY IT IS ONLY A CANDIDATE. NEXT_TESTS item 12 measured it on ONE cell
(all | 2018 | 1% risk | Rs 1cr | mom_hi) at 11.1 CAGR against a 11.97 hold --
still NEGATIVE, at -0.87. One cell is how this project has been fooled before:
0 of 2,484 board cells have ever cleared the gate, and single cells flatter.
So this script asks the same four questions the board asks of its nine:

  1. the whole grid, 300 scenarios per label, not one cell
  2. charged like the board -- built with the spread OFF, half-spread applied
     exactly once afterwards (the `build()` guard; see THE TRAP below)
  3. the daily-excess HAC test on every cell, then the Benjamini-Hochberg FDR
     gate, both within the candidate alone and pooled with the board's 2,484
  4. redundancy against the nine rules already on the board, raw and demeaned

It runs a 2 x 2: the full Pine and the RSI-free Pine, under the Pine's own
execution convention (ATR stop off the signal close, next-open fill) and under
the board's (previous-bar-low stop, close fill), so the RSI comparison is made
twice and never rests on one execution choice.

THE TRAP this script is written around. `wf_attach.spread_of` sets
slippage.ENABLED = True and never restores it, and `wf_pine.trades_for` calls
slippage.fill while BUILDING. Any loop that builds, charges, builds, charges
therefore charges every leg after the first TWICE. That bug produced item 4's
false "paradox" and has now been hit three times, so the guard lives in the
builder here, not in the caller's ordering -- and the loop below asserts the
flag is off at the top of every single build.

Reads:  the price parquet via kitelab.config, /data/clean/kitelab/dashboard.json
        (its universes, hold curves and the nine strategies' daily_excess
        block), and the cached wf_attach hold-curve pickle.
Writes: output/pine_candidate_<date>.json,
        output/measurements/pine_candidate_<date>.csv (one row per cell), and
        a per-label checkpoint directory output/pine_candidate_ckpt_<board>/
        so a crash never forces a full re-run.
Rebuilds nothing; edits no module in signals._SUPPORT or signals._ACCOUNT.
"""
from __future__ import annotations

import json
import pickle
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config, slippage
import scripts.wf_attach as wa
import scripts.wf_pine as wp
from scripts.attach_diagnostics import bh_threshold

OUT = Path(__file__).resolve().parent.parent / "output"
TF = "W"                      # weekly bars, monthly EMA(20) filter
ALPHA = 0.05

# (label suffix, kwargs into wf_pine.entry_mask via trades_for)
LEGS = [("full", dict()),
        ("norsi", dict(use_rsi=False))]

# (stop_mode, fill, what it is)
CONVENTIONS = [("atr", "open", "the Pine as written"),
               ("low", "close", "the board's own convention")]

# The one cell item 12 measured, charged alike. Reproducing it is the
# self-check: this grid must agree with that run where they overlap.
SELF_CHECK_SCENARIO = "all|1|10000000|1|2018|mom_hi"
SELF_CHECK_EXPECT = {"full|atr-open": 6.1, "norsi|atr-open": 11.1}


def label_of(leg, stop_mode, fill):
    return f"pine{TF}-{leg}|{stop_mode}-{fill}"


# ------------------------------------------------------------- building ----
def build(symbols, stop_mode, fill, kw):
    """Every symbol's trades, built with the spread FORCED OFF.

    Whatever the caller left enabled, the trades come out clean and are charged
    exactly once by wa.spread_of afterwards. The state is restored either way.
    """
    was_enabled, was_cap = slippage.ENABLED, slippage.MAX_PARTICIPATION
    slippage.ENABLED, slippage.MAX_PARTICIPATION = False, None
    slippage.reset()
    trades, skipped = [], 0
    try:
        for sym in symbols:
            try:
                sig = wp.signals(sym, TF)
            except SystemExit:
                skipped += 1
                continue
            if len(sig) == 0:
                skipped += 1
                continue
            trades.extend(wp.trades_for(sym, sig, tf=TF, side="long",
                                        stop_mode=stop_mode, fill=fill, **kw))
    finally:
        slippage.ENABLED, slippage.MAX_PARTICIPATION = was_enabled, was_cap
        slippage.reset()
    return trades, skipped


# -------------------------------------------------------------- the gate ---
def gate(rows, pooled_ps, name):
    """The board's own three bars, applied to one label's cells."""
    # pandas fills a missing daily test with NaN, not None, and NaN would pass
    # an `is not None` guard straight into the p-value list.
    ps = [float(r["daily_p"]) for r in rows if pd.notna(r.get("daily_p"))]
    n = len(ps)
    if not n:
        print(f"  {name:<22} no cell carries the daily-excess test")
        return {}
    own_bh = bh_threshold(ps, ALPHA)
    bonf = ALPHA / n
    # Pooled: the candidate's p-values judged inside the board's own family of
    # tests. Adding a tenth rule adds chances to be lucky; BH must see them all.
    pool = sorted(pooled_ps + ps)
    pool_bh = bh_threshold(pool, ALPHA)
    mdes = sorted(float(r["daily_mde_80"]) for r in rows
                  if pd.notna(r.get("daily_mde_80")))
    out = {
        "n_tested": n,
        "smallest_p": round(min(ps), 6),
        "n_uncorrected": sum(1 for p in ps if p <= ALPHA),
        "expected_by_chance": round(ALPHA * n, 1),
        "own_bh_threshold": round(own_bh, 8),
        "n_own_bh": sum(1 for p in ps if own_bh and p <= own_bh),
        "pooled_bh_threshold": round(pool_bh, 8),
        "n_pooled_bh": sum(1 for p in ps if pool_bh and p <= pool_bh),
        "bonferroni_threshold": bonf,
        "n_bonferroni": sum(1 for p in ps if p <= bonf),
        "median_mde_80": mdes[len(mdes) // 2] if mdes else None,
    }
    print(f"  {name:<22} {n:>5} {out['smallest_p']:>10.4f} "
          f"{out['n_uncorrected']:>8} {out['expected_by_chance']:>10.1f} "
          f"{out['n_own_bh']:>7} {out['n_pooled_bh']:>9} "
          f"{out['n_bonferroni']:>6} {out['median_mde_80']:>9.2f}")
    return out


# ------------------------------------------------------------ redundancy ---
def redundancy(cells_by_label, board_de):
    """Does the candidate say anything the nine do not?

    Same two readings as scripts.redundancy: the raw correlation of per-scenario
    excess-vs-hold, and the correlation after subtracting each scenario's
    cross-strategy mean, which removes the fact that some scenarios are hard for
    everything and leaves rule-vs-rule similarity.
    """
    by_scen: dict[str, dict[str, float]] = {}
    for key, v in board_de.items():
        parts = key.split("|")
        strat, scen = "|".join(parts[:2]), "|".join(parts[2:])
        if v.get("excess_pts") is None:
            continue
        by_scen.setdefault(scen, {})[strat] = float(v["excess_pts"])

    board_names = sorted({n for d in by_scen.values() for n in d})
    cand_names = list(cells_by_label)
    for label, cells in cells_by_label.items():
        for key, c in cells.items():
            parts = key.split("|")
            scen = "|".join(parts[2:])
            e = (c.get("daily") or {}).get("excess_pts")
            if scen in by_scen and e is not None:
                by_scen[scen][label] = float(e)

    names = board_names + cand_names
    scens = sorted(s for s in by_scen if all(n in by_scen[s] for n in names))
    print(f"\n  scenarios carrying ALL {len(names)} rules: {len(scens)} "
          f"(of {len(by_scen)} on the board)")
    if len(scens) < 30:
        print("  too few shared scenarios to correlate; skipping")
        return {}
    X = np.array([[by_scen[s][n] for n in names] for s in scens], float)
    print(f"  matrix: {X.shape[0]} scenarios x {X.shape[1]} rules, "
          f"range {X.min():.2f} to {X.max():.2f} CAGR pts")
    Xd = X - X.mean(axis=1, keepdims=True)
    Craw, Cdem = np.corrcoef(X, rowvar=False), np.corrcoef(Xd, rowvar=False)

    print(f"\n  {'candidate':<22}{'nearest board rule':<16}{'raw r':>8}"
          f"{'demeaned r':>12}{'mean raw r':>12}")
    print("  " + "-" * 70)
    out = {}
    floor = -1.0 / (len(names) - 1)
    for cand in cand_names:
        j = names.index(cand)
        rs = {n: (Craw[j, names.index(n)], Cdem[j, names.index(n)])
              for n in board_names}
        best = max(rs, key=lambda n: rs[n][0])
        mean_raw = float(np.mean([rs[n][0] for n in board_names]))
        out[cand] = {"nearest": best, "raw_r": round(float(rs[best][0]), 3),
                     "demeaned_r": round(float(rs[best][1]), 3),
                     "mean_raw_r": round(mean_raw, 3),
                     "all_raw": {n: round(float(rs[n][0]), 3) for n in board_names}}
        print(f"  {cand:<22}{best:<16}{rs[best][0]:>8.3f}{rs[best][1]:>12.3f}"
              f"{mean_raw:>12.3f}")
    print(f"\n  Centring forces the average off-diagonal correlation to about "
          f"{floor:.3f}\n  by construction, so only a demeaned r well above that "
          f"is evidence of\n  duplication. A candidate near 0.9 raw is a tenth "
          f"label for an idea the\n  board already holds.")
    return out


def main():
    t0 = time.time()
    cfg = config.load()
    symbols = sorted(cfg.merged)
    print(f"universe: {len(symbols)} symbols")
    print(f"timeframe {TF}: {wp.TIMEFRAMES[TF][0]}")

    payload = wp.load_board()
    board_de = payload.get("daily_excess") or {}
    if not board_de:
        raise SystemExit("dashboard.json carries no daily_excess block")
    print(f"  board daily_excess: {len(board_de)} cells across "
          f"{len({'|'.join(k.split('|')[:2]) for k in board_de})} strategies")
    pooled_ps = [v["p"] for v in board_de.values() if v.get("p") is not None]
    print(f"  pooled p-values from the board: {len(pooled_ps)}")

    unis = wp.universes(set(symbols), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(symbols), unis, [], board)
    hold_cagr = wp.hold_cagrs(holds)
    print(f"  hold CAGR by cell: {len(hold_cagr)} entries, "
          f"all|2018 = {hold_cagr.get('all|2018')}")

    ck = OUT / f"pine_candidate_ckpt_{board}"
    ck.mkdir(parents=True, exist_ok=True)

    all_cells, summary = {}, {}
    for leg, kw in LEGS:
        for stop_mode, fill, what in CONVENTIONS:
            label = label_of(leg, stop_mode, fill)
            path = ck / f"{label.replace('|', '__')}.pkl"
            if path.exists():
                cells, stats = pickle.loads(path.read_bytes())
                print(f"  {label:<24} reused {len(cells)} cells")
            else:
                s0 = time.time()
                print(f"  {label:<24} building ({what}); "
                      f"slippage.ENABLED before build = {slippage.ENABLED}",
                      flush=True)
                raw, skipped = build(symbols, stop_mode, fill, kw)
                print(f"    {len(symbols) - skipped} symbols usable "
                      f"({skipped} too short) -> {len(raw):,} trades", flush=True)
                charged = wa.spread_of(raw)
                stats = wp.trade_stats(charged)
                cells = wp.cells_for(charged, unis, holds, hold_cagr, label)
                path.write_bytes(pickle.dumps((cells, stats)))
                print(f"    {len(cells)} cells  [{(time.time()-s0)/60:.1f} min]",
                      flush=True)
            all_cells.update(cells)
            summary[label] = {"leg": leg, "stop": stop_mode, "fill": fill,
                              "convention": what, "trades": stats}

    # ------------------------------------------------------------ frame ----
    rows = []
    for key, c in all_cells.items():
        d = c.get("daily") or {}
        rows.append({k: v for k, v in c.items() if k != "daily"} |
                    {"cell": key} |
                    {f"daily_{k}": v for k, v in d.items()})
    flat = pd.DataFrame(rows)
    print(f"\n  cells frame: {len(flat)} rows x {len(flat.columns)} columns")
    print(flat.head(3).to_string())
    missing = [c for c in ("variant", "cagr", "hold_cagr") if c not in flat]
    if missing:
        raise SystemExit(f"cells frame is missing required columns: {missing}")

    # ---------------------------------------------------------- self-check --
    print("\nSELF-CHECK -- these grid cells must reproduce the item 12 table")
    for suffix, expect in SELF_CHECK_EXPECT.items():
        leg, conv = suffix.split("|")
        key = f"{label_of(leg, *conv.split('-'))}|{SELF_CHECK_SCENARIO}"
        got = all_cells.get(key, {}).get("cagr")
        ok = got is not None and abs(got - expect) < 0.05
        print(f"  {key}\n    CAGR {got} vs item 12's {expect}  "
              f"{'PASS' if ok else 'MISMATCH -- do not quote this run'}")

    # -------------------------------------------------------- the tables ---
    print("\n" + "=" * 92)
    print("TRADE LEVEL -- long only, half-spread charged once")
    print(f"  {'label':<24}{'trades':>9}{'win%':>7}{'expct R':>9}"
          f"{'med days':>10}{'gross':>15}{'charges':>14}")
    for label, s in summary.items():
        st = s["trades"]
        print(f"  {label:<24}{st['n']:>9,}{st['win_rate']:>7}"
              f"{st['expectancy_r']:>9}{st['median_days']:>10}"
              f"{st['gross']:>15,}{st['charges']:>14,}")

    print("\nACCOUNT LEVEL -- every scenario on the board's grid")
    print(f"  {'label':<24}{'cells':>7}{'med CAGR':>10}{'med hold':>10}"
          f"{'med excess':>12}{'beat hold':>11}{'wiped':>7}")
    for label in summary:
        sub = flat[flat["variant"] == label]
        ex = (sub["cagr"] - sub["hold_cagr"]).dropna()
        beat = sub["beats_hold"].dropna()
        print(f"  {label:<24}{len(sub):>7}{sub['cagr'].median():>10.2f}"
              f"{sub['hold_cagr'].median():>10.2f}{ex.median():>12.2f}"
              f"{100 * beat.mean():>10.1f}%{int(sub['wiped'].sum()):>7}")

    print("\nTHE GATE -- the daily-excess HAC test, cell by cell")
    print(f"  {'label':<22}{'cells':>5}{'smallest p':>10}{'p<=0.05':>8}"
          f"{'by chance':>11}{'own BH':>7}{'pooled BH':>9}{'bonf':>6}{'med MDE':>9}")
    gates = {}
    for label in summary:
        sub = flat[flat["variant"] == label]
        gates[label] = gate(sub.to_dict("records"), pooled_ps, label)

    print("\n  'own BH' judges the candidate's cells alone -- the most generous")
    print("  bar it could be given. 'pooled BH' judges them inside the board's")
    print("  own family of 2,484 tests, which is what adding a tenth rule means.")

    print("\nREDUNDANCY AGAINST THE NINE")
    red = redundancy({lab: {k: v for k, v in all_cells.items()
                            if v["variant"] == lab} for lab in summary},
                     board_de)

    # ------------------------------------------------------------ writes ---
    stamp = date.today().isoformat()
    cpath = OUT / "measurements" / f"pine_candidate_{stamp}.csv"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    flat.to_csv(cpath, index=False)
    blob = {"built": payload["built"], "board": board, "generated": stamp,
            "timeframe": TF, "timeframe_label": wp.TIMEFRAMES[TF][0],
            "labels": list(summary), "summary": summary, "gates": gates,
            "redundancy": red, "cells": all_cells}
    jpath = OUT / f"pine_candidate_{stamp}.json"
    jpath.write_text(json.dumps(blob, indent=1, default=str))
    print(f"\n  wrote {jpath.name} and measurements/{cpath.name} "
          f"({len(flat)} rows x {len(flat.columns)} cols)"
          f"  [{(time.time() - t0) / 60:.1f} min]")


if __name__ == "__main__":
    main()

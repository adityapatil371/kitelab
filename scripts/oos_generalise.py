"""Does "the nine lose to buy-and-hold" survive out of sample?

THE QUESTION, asked 2026-09-16 after `scripts/hold_dominance.py` showed the
board's nine rules are not merely unproven against hold but detectably worse
(872 cells uncorrected, 474 after BH, 63 after Bonferroni, against 0 the other
way). Every one of those numbers was measured on the same data that chose the
nine. This script asks whether the finding replicates where it could not have
been manufactured.

WHY THE USUAL OVERFITTING WORRY POINTS THE OTHER WAY. Selection pressure on
2026-09-11 (19 -> 13 -> 9) kept rules that looked GOOD. Any luck that survived
that cut is luck in the rules' favour, so a negative finding on the survivors
is already conservative. The real risks to the claim are different, and there
are two:

  RISK 1, REGIME. The shortfall might live in one stretch of market history --
  a crash the rules sat out badly, or a melt-up no trend rule could keep pace
  with -- and be an artefact of averaging over 2006-2026 rather than a property
  of the rules.

  RISK 2, THE NINE. The claim might be about these nine labels rather than
  about rules of this kind. Nine is a small, hand-picked family.

THE THREE LEGS, and what each can and cannot establish.

  LEG 1 -- OUT OF PERIOD. Every cell's daily excess series is cut at
  SPLIT_DATE and the identical HAC test run on each half independently. Cells
  need >= MIN_DAYS (250) on BOTH sides, so this is the 2006 and 2012 start
  years. **This is a replication test, not a strict holdout**: the late period
  was inside the full-sample numbers the 2026-09-11 cut was made on, so it is
  not virgin data. What it can kill is RISK 1 -- if the shortfall is a regime
  artefact, one half will not carry it. Labelled `regime`, never `holdout`.

  LEG 2 -- OUT OF RULE, and this one IS a holdout. The ten rules cut on
  2026-09-11 are still cached on disk and are run through the same 276-cell
  grid and the same test. They divide on WHY they were cut, and the two halves
  are worth very different amounts:

    * DUPLICATION-CUT (qmw|0, pair|QW, pair|WD, eath|WD). Removed for
      correlating with a rule that was kept, on a criterion CLAUDE.md records
      as uncorrelated with quality: Spearman(average rank, nearest-twin r) =
      -0.18, p = 0.56. With respect to the hold-vs-rule question these four are
      effectively a random draw from the 13, and they took no part in
      establishing the finding. **This is the clean test.**

    * RANK-CUT (hg|swing, eath|QM, eath|QD, pair|QM, pair|QD, eath|MWD).
      Removed for losing -- selection on the very quantity under test. They can
      only confirm the finding trivially, so they are reported in a separate
      block and excluded from the headline. Printed because leaving them out
      silently would be the same sin in reverse.

  LEG 3 -- OUT OF PROJECT, free. The user's TradingView rule
  (`scripts/pine_candidate.py`, 2026-09-16) was designed outside this repo and
  never took part in any selection here. Four labels, 276 cells each, median
  excess -5.32 to -8.97, 0 of 1,104 cells at the gate. Cited, not recomputed.

THE PREDICTIONS, written before the run so the test can fail. If the finding
generalises: (1) both halves of Leg 1 are independently negative for all nine
rules; (2) the four duplication-cut rules lose to hold with a median excess in
the -3 to -15 pts/yr band the nine occupy, and clear ZERO cells in the
favourable direction. If either fails, the claim narrows to the nine, or to a
period, and NEXT_TESTS item 14 is rewritten.

WHAT THIS STILL CANNOT SAY. One market, one country, one asset class, one cost
model, one realised price path. Nothing here is out of sample in the only sense
that would settle the matter -- data that did not exist when the rules were
written. No number here is a forecast.

Reads:  /data/clean/kitelab/dashboard.json          (axes, buckets, daily_excess)
        /data/clean/kitelab/signal_cache/*_all.pkl  (all 19 rules' trades)
        output/wf_attach_hold_<board key>.pkl       (hold curves, already built)
Writes: output/oos_generalise_ckpt_<board key>/*.pkl   (one per label, resumable)
        output/measurements/oos_generalise_<date>.csv  (every cell, both halves)
        output/measurements/oos_generalise_<date>.json
Rebuilds nothing. Edits no stamped module, and is in neither stamp tier.
"""
from __future__ import annotations

import bisect
import json
import pickle
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from kitelab import config, portfolio
from kitelab.config import CLEAN
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
from scripts.attach_diagnostics import bh_threshold
from scripts.wf_daily import MIN_DAYS, norm_sf

OUT = Path(__file__).resolve().parent.parent / "output"
MEAS = OUT / "measurements"        # every finished CSV or JSON result
MEAS.mkdir(parents=True, exist_ok=True)
CACHE = CLEAN / "signal_cache"
ALPHA = 0.05

# Pre-registered: the calendar midpoint of the 2006-2026 span, fixed before the
# run and not chosen from the results. Moving it after seeing the output would
# turn this test into a search.
SPLIT_DATE = pd.Timestamp("2017-01-01")

# cache stem -> (board label, cohort). Cohorts from CLAUDE.md's record of the
# two 2026-09-11 cuts; `board` are the nine the finding was made on.
LABELS = {
    "EMA_b0":            ("ema|0",        "board"),
    "Turtle_w20_20_10":  ("dv|20-10",     "board"),
    "Turtle_w20_55_20":  ("dv|55-20",     "board"),
    "Turtle_1tf_20_10":  ("dv|20-10 1TF", "board"),
    "Turtle_1tf_55_20":  ("dv|55-20 1TF", "board"),
    "EMA_MD":            ("pair|MD",      "board"),
    "EMA_MW":            ("pair|MW",      "board"),
    "EMA_daily_only":    ("e1|daily",     "board"),
    "EMA_ath10_MW":      ("eath|MW",      "board"),
    # pass two, cut for DUPLICATION -- quality-neutral, the clean holdout
    "QMW_b0":            ("qmw|0",        "dup-cut"),
    "EMA_QW":            ("pair|QW",      "dup-cut"),
    "EMA_WD":            ("pair|WD",      "dup-cut"),
    "EMA_ath10_WD":      ("eath|WD",      "dup-cut"),
    # pass one, cut for RANK -- selection on the tested quantity, reported apart
    "HolyGrail_swing":   ("hg|swing",     "rank-cut"),
    "EMA_ath10_QM":      ("eath|QM",      "rank-cut"),
    "EMA_ath10_QD":      ("eath|QD",      "rank-cut"),
    "EMA_QM":            ("pair|QM",      "rank-cut"),
    "EMA_QD":            ("pair|QD",      "rank-cut"),
    "EMA_ath10_MWD":     ("eath|MWD",     "rank-cut"),
}


# ------------------------------------------------------------- plumbing ----
def trades_of(stem):
    """The board's own trades for one rule, spread charged exactly once.

    The cache holds UNSPREAD trades (wf_attach.arm_a_trades), so the half-spread
    is applied here exactly as dashboard_data applies it.

    DO NOT "GUARD" THIS BY RESTORING THE SLIPPAGE GLOBALS. The reflex from
    kitelab-spread-of-leaves-slippage-on is to snapshot ENABLED and
    MAX_PARTICIPATION around `spread_of` and put them back -- and it is WRONG
    here, measured 2026-09-16. That trap is about building trades from bars
    through `slippage.fill` twice; this script builds nothing, it reads finished
    trades. What the globals actually feed here is `portfolio.run`, which reads
    `slippage.ENABLED` (portfolio.py:673) and `slippage.capped_shares`
    (portfolio.py:654) at RUN time. Restoring them drops the 1%-of-turnover
    participation cap, and the cap is most of the friction: on
    ema|0|large|0.5|10000000|1|2006|mom_hi the restored version gave t = -1.777
    against the board's -2.747. The board sets these once and leaves them set,
    so this does too, and self_check() is what proves it.
    """
    p = CACHE / f"{stem}_all.pkl"
    if not p.exists():
        return None
    blob = pickle.loads(p.read_bytes())
    raw = blob["trades"] if isinstance(blob, dict) else blob
    return wa.spread_of(raw)


def split_stats(curve, hold):
    """The same HAC test on the whole curve and on each side of SPLIT_DATE.

    `wa.excess_stats` intersects the curve's dates with the hold series itself,
    so slicing the curve alone is enough -- and it means all three numbers come
    from the one function the board is gated on, not a reimplementation.
    """
    full = wa.excess_stats(curve, hold)
    early = wa.excess_stats([(d, v) for d, v in curve
                             if pd.Timestamp(d) < SPLIT_DATE], hold)
    late = wa.excess_stats([(d, v) for d, v in curve
                            if pd.Timestamp(d) >= SPLIT_DATE], hold)
    return full, early, late


def cells_for(trades, unis, holds, label, cohort):
    """wf_attach's grid loop, with the period split added at the leaf."""
    cells = {}
    for ukey, members in unis.items():
        subset = (trades if members is None
                  else [t for t in trades if t["symbol"] in members])
        if not subset:
            continue
        subset = sorted(subset, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        hold = holds.get(ukey)
        if hold is None:
            continue
        live_years = dd.gridded_years(stamps)
        for year in dd.START_YEARS:
            cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
            window = subset[cut:] if year in live_years else []
            if not window:
                continue
            for prio in dd.PRIORITIES:
                for risk in dd.RISKS:
                    for capital in dd.CAPITALS:
                        r = portfolio.run(window, capital, risk / 100, prio)
                        full, early, late = split_stats(r.get("curve") or [], hold)
                        if full is None:
                            continue
                        key = (f"{label}|{ukey}|{risk:g}|{capital}"
                               f"|1|{year}|{prio}")
                        rec = {"label": label, "cohort": cohort,
                               "universe": ukey, "risk": risk,
                               "capital": capital, "start": year,
                               "priority": prio, "cell": key}
                        for tag, st in (("full", full), ("early", early),
                                        ("late", late)):
                            rec[f"{tag}_t"] = None if st is None else st["t_hac"]
                            rec[f"{tag}_excess"] = (None if st is None
                                                    else st["excess_pts"])
                            rec[f"{tag}_days"] = None if st is None else st["days"]
                        cells[key] = rec
    return cells


# ---------------------------------------------------------- self-check ----
def self_check(rows, payload):
    """Our full-period numbers must BE the board's, cell for cell.

    Without this the script is measuring some other account and every verdict
    below is about nothing. The board stores t_hac to 3 decimals, so the
    comparison is at that tolerance.
    """
    de = payload.get("daily_excess") or {}
    checked = mismatch = 0
    for r in rows:
        if r["cohort"] != "board":
            continue
        want = de.get(r["cell"])
        if not want or want.get("t_hac") is None or r["full_t"] is None:
            continue
        checked += 1
        if abs(float(want["t_hac"]) - float(r["full_t"])) > 0.002:
            mismatch += 1
            if mismatch <= 3:
                print(f"    MISMATCH {r['cell']}: board {want['t_hac']} "
                      f"vs here {r['full_t']:.3f}")
    print("\nSELF-CHECK -- full-period t_hac against dashboard.json")
    print(f"  {checked:,} board cells compared, {mismatch} disagree")
    if checked < 2000 or mismatch:
        raise SystemExit(
            f"  SELF-CHECK FAILED ({checked:,} compared, {mismatch} disagree). "
            f"This harness is not measuring the board's account. Nothing written.")
    print("  PASS -- this is the same arithmetic the finding was made on")


# -------------------------------------------------------------- reports ----
def counts(ps_neg, n):
    """Uncorrected / BH / Bonferroni, the three bars hold_dominance used."""
    ps = sorted(ps_neg)
    bh = bh_threshold(ps, ALPHA)
    return (sum(1 for p in ps if p <= ALPHA),
            sum(1 for p in ps if bh and p <= bh),
            sum(1 for p in ps if p <= ALPHA / max(n, 1)))


def worse_p(t):
    """Left-tail p: the same statistic asking whether hold beats the rule."""
    return float(norm_sf(-t))


def leg1(d):
    """Out of period: does each half carry the shortfall on its own?"""
    sub = d[(d["cohort"] == "board") & d["early_t"].notna() & d["late_t"].notna()]
    print(f"\n{'=' * 78}\nLEG 1 -- OUT OF PERIOD (replication, not a holdout)")
    print(f"  split {SPLIT_DATE.date()}; cells needing >= {MIN_DAYS} days each "
          f"side: {len(sub):,} of {int((d['cohort'] == 'board').sum()):,}")
    if sub.empty:
        print("  no cell spans the split -- nothing to report")
        return
    print(f"\n  {'rule':<16}{'med t EARLY':>13}{'med t LATE':>12}"
          f"{'exc EARLY':>11}{'exc LATE':>10}{'both neg':>10}")
    for rule, g in sub.groupby("label"):
        both = ((g["early_t"] < 0) & (g["late_t"] < 0)).mean()
        print(f"  {rule:<16}{g['early_t'].median():>13.2f}"
              f"{g['late_t'].median():>12.2f}{g['early_excess'].median():>11.2f}"
              f"{g['late_excess'].median():>10.2f}{100 * both:>9.0f}%")
    for tag in ("early", "late"):
        ts = sub[f"{tag}_t"].tolist()
        u, b, bf = counts([worse_p(t) for t in ts], len(ts))
        beats, _, _ = counts([1 - worse_p(t) for t in ts], len(ts))
        print(f"\n  {tag.upper():<6} half: {len(ts):,} cells | worse than hold "
              f"{u:,} uncorrected / {b:,} BH / {bf:,} Bonferroni | "
              f"better {beats:,} uncorrected")
    neg = sum(1 for _, g in sub.groupby("label") if g["late_t"].median() < 0)
    k = sub["label"].nunique()
    print(f"\n  PREDICTION was: all {k} rules negative in BOTH halves.")
    print(f"  Rules with a negative median t in the LATE half: {neg} of {k}.")


def leg2(d):
    """Out of rule: the holdout the 2026-09-11 cut left behind."""
    print(f"\n{'=' * 78}\nLEG 2 -- OUT OF RULE (a genuine holdout)")
    print(f"  {'cohort':<10}{'rule':<16}{'cells':>7}{'med t':>8}"
          f"{'med excess':>12}{'t<0':>7}{'worse .05':>11}{'BEATS .05':>11}")
    summary = {}
    for cohort in ("board", "dup-cut", "rank-cut"):
        sub = d[d["cohort"] == cohort]
        if sub.empty:
            continue
        for rule, g in sorted(sub.groupby("label"),
                              key=lambda kv: -kv[1]["full_t"].median()):
            ps = [worse_p(t) for t in g["full_t"]]
            print(f"  {cohort:<10}{rule:<16}{len(g):>7,}"
                  f"{g['full_t'].median():>8.2f}{g['full_excess'].median():>12.2f}"
                  f"{100 * (g['full_t'] < 0).mean():>6.0f}%"
                  f"{sum(1 for p in ps if p <= ALPHA):>11,}"
                  f"{sum(1 for p in ps if 1 - p <= ALPHA):>11,}")
        ts = sub["full_t"].tolist()
        u, b, bf = counts([worse_p(t) for t in ts], len(ts))
        beats, _, bfb = counts([1 - worse_p(t) for t in ts], len(ts))
        summary[cohort] = {
            "rules": sub["label"].nunique(), "cells": len(ts),
            "median_t": float(sub["full_t"].median()),
            "median_excess": float(sub["full_excess"].median()),
            "rules_negative": sum(1 for _, g in sub.groupby("label")
                                  if g["full_t"].median() < 0),
            "worse_uncorrected": u, "worse_bh": b, "worse_bonferroni": bf,
            "beats_uncorrected": beats, "beats_bonferroni": bfb}
        print()
    print(f"  {'cohort':<10}{'rules':>6}{'cells':>7}{'med exc':>9}{'neg':>7}"
          f"{'worse u/BH/bonf':>22}{'BEATS u/bonf':>15}")
    for cohort, s in summary.items():
        neg = f"{s['rules_negative']}/{s['rules']}"
        worse = (f"{s['worse_uncorrected']:,}/{s['worse_bh']:,}/"
                 f"{s['worse_bonferroni']:,}")
        beats = f"{s['beats_uncorrected']:,}/{s['beats_bonferroni']:,}"
        print(f"  {cohort:<10}{s['rules']:>6}{s['cells']:>7,}"
              f"{s['median_excess']:>9.2f}{neg:>7}{worse:>22}{beats:>15}")
    print("\n  'dup-cut' is the line that matters: four rules removed on a")
    print("  criterion measured to be uncorrelated with quality (Spearman")
    print("  -0.18, p 0.56), which took no part in establishing the finding.")
    print("  'rank-cut' was selected ON performance and can only agree; it is")
    print("  shown so the exclusion is visible rather than silent.")
    return summary


def leg3():
    print(f"\n{'=' * 78}\nLEG 3 -- OUT OF PROJECT (cited, not recomputed)")
    print("  The user's TradingView rule, designed outside this repo and never")
    print("  part of any selection here (scripts/pine_candidate.py, 2026-09-16):")
    print("    4 labels x 276 cells, median excess -8.97 / -5.32 / -7.50 / -6.87")
    print("    0 of 1,104 cells clear the gate in the favourable direction,")
    print("    even judged alone rather than inside the board's family.")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    t0 = time.time()
    payload = json.loads((CLEAN / "dashboard.json").read_text())
    board = wa.board_key(payload)
    print(f"dashboard.json built {payload['built']}, "
          f"{len(payload['grid']):,} grid cells, board key {board}")
    print(f"split date (pre-registered): {SPLIT_DATE.date()}")

    cfg = config.load()
    members = list(cfg.merged)
    print(f"universe: {len(members)} stocks")
    unis = wa.stock_universes(members)
    wa.assert_buckets_match_payload(unis, payload)
    holds = wa.hold_curves(members, unis, [], board)

    ck = OUT / f"oos_generalise_ckpt_{board}"
    ck.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for i, (stem, (label, cohort)) in enumerate(LABELS.items(), 1):
        path = ck / f"{stem}.pkl"
        if path.exists():
            cells = pickle.loads(path.read_bytes())
            print(f"  [{i:2}/{len(LABELS)}] {label:<14} {cohort:<9} reused "
                  f"{len(cells):>4} cells", flush=True)
            rows.extend(cells.values())
            continue
        s0 = time.time()
        trades = trades_of(stem)
        if trades is None:
            missing.append(label)
            print(f"  [{i:2}/{len(LABELS)}] {label:<14} {cohort:<9} "
                  f"NO CACHE ON DISK -- skipped", flush=True)
            continue
        cells = cells_for(trades, unis, holds, label, cohort)
        path.write_bytes(pickle.dumps(cells))
        rows.extend(cells.values())
        print(f"  [{i:2}/{len(LABELS)}] {label:<14} {cohort:<9} "
              f"{len(trades):>7,} trades -> {len(cells):>4} cells "
              f"[{(time.time()-s0)/60:.1f} min]", flush=True)
    if missing:
        print(f"\n  {len(missing)} rules had no cache and are absent: "
              f"{', '.join(missing)}")

    d = pd.DataFrame(rows)
    print(f"\ncells frame: {len(d):,} rows x {len(d.columns)} columns")
    print(d.head(3).to_string())
    before = len(d)
    d = d[d["full_t"].notna()]
    print(f"  rows with a full-period test: {len(d):,} (dropped {before - len(d):,})")

    self_check(rows, payload)
    leg1(d)
    summary = leg2(d)
    leg3()

    print(f"\n{'=' * 78}\nVERDICT")
    dup = summary.get("dup-cut")
    if dup:
        print(f"  holdout rules (dup-cut): {dup['rules']} rules, {dup['cells']:,} "
              f"cells, median excess {dup['median_excess']:.2f} pts/yr")
        print(f"    {dup['rules_negative']}/{dup['rules']} rules negative | "
              f"worse than hold {dup['worse_uncorrected']:,} uncorrected, "
              f"{dup['worse_bh']:,} BH, {dup['worse_bonferroni']:,} Bonferroni")
        print(f"    cells BEATING hold: {dup['beats_uncorrected']:,} uncorrected, "
              f"{dup['beats_bonferroni']:,} Bonferroni")

    out = OUT / "measurements" / f"oos_generalise_{date.today()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False)
    js = MEAS / f"oos_generalise_{date.today()}.json"
    js.write_text(json.dumps({"built": payload["built"],
                              "split_date": str(SPLIT_DATE.date()),
                              "summary": summary}, indent=1))
    print(f"\n  wrote {out.relative_to(OUT.parent)} "
          f"({len(d)} rows x {len(d.columns)} cols) and {js.name}"
          f"  [{(time.time()-t0)/60:.1f} min]")


if __name__ == "__main__":
    main()

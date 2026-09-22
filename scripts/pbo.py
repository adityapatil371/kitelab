"""Was the 19 -> 9 cut selecting signal, or fitting noise?

THE QUESTION. Every test this project runs asks "is THIS cell real?". None ask
"is the PROCEDURE that picked the winners any good?". On 2026-09-11 we ranked
19 rules over the rebuilt momentum board and kept 9. That ranking was made on
the full sample, so it cannot tell us whether the rule that came top would have
come top on data it had not seen.

PBO -- the probability of backtest overfitting (Bailey, Borwein, Lopez de Prado
& Zhu 2015) -- answers exactly that, and it answers it about the SELECTION, not
about any one cell. The construction:

  1. Put the N trials side by side as daily return series of equal length.
  2. Cut the timeline into S equal blocks (S = 16 here).
  3. For each of the C(S, S/2) = 12,870 ways of choosing half the blocks as
     TRAINING and the complement as TESTING:
       - find the trial that scores best in training,
       - look up where that same trial ranks in testing,
       - record its relative rank w = rank / (N + 1) and the logit ln(w/(1-w)).
  4. PBO = the fraction of splits where the training winner lands BELOW the
     testing median, i.e. logit <= 0.

Read it as: pick the best-looking rule on half the history; how often is it a
below-average rule on the other half? 0.0 means the ranking is perfectly
durable. 0.5 means it carries no information at all -- you may as well have
drawn a name out of a hat. Above 0.5 means the procedure is actively
anti-predictive, which happens when the thing being ranked is noise and the
winner is simply whoever got luckiest.

WHY N = 19 AND NOT 2,700. The trials must be the things we actually chose
among. We did not choose among cells -- the grid's 300 scenarios are all kept
and reported. We chose among RULES: 19 became 13 on rank, then 9 on redundancy.
So N = 19, one PBO per scenario, and the spread across scenarios is the result
rather than any single number.

TWO METRICS, BOTH REPORTED. The textbook statistic ranks trials on the Sharpe
ratio of their own returns. But this project judges everything against
buy-and-hold, so the ranking that actually drove the cut is the one on daily
EXCESS over hold. Neither is obviously the right one, so both are computed and
printed side by side; where they disagree, say so rather than picking.

PRE-REGISTERED PREDICTION, written before the run (2026-09-16). I expect a HIGH
PBO, somewhere in 0.3-0.6. The reasoning: scripts/redundancy.py put the board
at 3-9 independent ideas inside 9 labels and n_eff at 4.0, and item 14 showed
not one of the 19 beats hold. Ranking near-copies of one another, none of which
has an edge, should be close to ranking noise. If PBO comes back well BELOW
0.3, that is evidence the cut found something durable and I was wrong.

WHAT THIS CANNOT SAY. A low PBO would mean the RANKING is durable, not that any
rule is good -- a reliable ordering of losers is still an ordering of losers.
And the blocks are contiguous calendar time, so a rule whose edge is
regime-bound (see NEXT_TESTS item 15: eight of nine shortfalls shrank after
2017) will look unstable here for a reason that is not overfitting.

Reads:  /data/clean/kitelab/signal_cache/<stem>_all.pkl (finished trades, all
        19 rules), /data/clean/kitelab/dashboard.json (for the self-check).
Writes: output/measurements/pbo_<date>.csv, output/measurements/pbo_<date>.json,
        output/figures/pbo_<date>.png. Rebuilds nothing, edits no stamped module.
"""
from __future__ import annotations

import bisect
import json
import math
import pickle
import time
from datetime import date
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from kitelab import portfolio
from kitelab.config import CLEAN, load
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
from scripts.wf_daily import MIN_DAYS

OUT = Path(__file__).resolve().parent.parent / "output"
FIG = OUT / "figures"              # every PNG
MEAS = OUT / "measurements"        # every finished CSV or JSON result
FIG.mkdir(parents=True, exist_ok=True)
MEAS.mkdir(parents=True, exist_ok=True)
CACHE = CLEAN / "signal_cache"

# Blocks, and the split count they imply. S must be even. 16 is the value the
# original paper uses and it gives C(16,8) = 12,870 splits -- enough resolution
# on a probability without the combinatorics running away.
S_BLOCKS = 16
PRIORITY = "mom_hi"

# The scenarios. A clean factorial subset of the board, NOT the whole grid:
# 1,140 cells at the measured 0.614 s/cell is ~12 min, where all 300 scenarios
# would be ~54. Start years 2022 and 2024 are excluded on purpose -- 16 blocks
# of a 430-day run is 27 days a block, too few for a Sharpe to mean anything.
START_YEARS = [2006, 2012, 2018]
RISKS = [0.5, 1.0]
CAPITALS = [200_000, 10_000_000]

# All 19, the set the 2026-09-11 cut actually chose among. Label and cohort
# match scripts/oos_generalise.py so the two results can be joined.
LABELS = {
    "EMA_b0": ("ema|0", "board"), "Turtle_w20_20_10": ("dv|20-10", "board"),
    "Turtle_w20_55_20": ("dv|55-20", "board"),
    "Turtle_1tf_20_10": ("dv|20-10 1TF", "board"),
    "Turtle_1tf_55_20": ("dv|55-20 1TF", "board"),
    "EMA_MD": ("pair|MD", "board"), "EMA_MW": ("pair|MW", "board"),
    "EMA_daily_only": ("e1|daily", "board"), "EMA_ath10_MW": ("eath|MW", "board"),
    "QMW_b0": ("qmw|0", "dup-cut"), "EMA_QW": ("pair|QW", "dup-cut"),
    "EMA_WD": ("pair|WD", "dup-cut"), "EMA_ath10_WD": ("eath|WD", "dup-cut"),
    "HolyGrail_swing": ("hg|swing", "rank-cut"),
    "EMA_ath10_QM": ("eath|QM", "rank-cut"), "EMA_ath10_QD": ("eath|QD", "rank-cut"),
    "EMA_QM": ("pair|QM", "rank-cut"), "EMA_QD": ("pair|QD", "rank-cut"),
    "EMA_ath10_MWD": ("eath|MWD", "rank-cut"),
}
LABELS_BY_NAME = {lab: (stem, coh) for stem, (lab, coh) in LABELS.items()}


def trades_of(stem):
    """The board's own trades for one rule, spread charged exactly once.

    DO NOT "GUARD" THIS BY RESTORING THE SLIPPAGE GLOBALS -- the same trap
    scripts/oos_generalise.py documents at length. portfolio.run reads
    slippage.ENABLED (portfolio.py:673) and slippage.capped_shares
    (portfolio.py:654) at RUN time, so putting the globals back drops the
    1%-of-turnover participation cap and quietly removes most of the friction.
    The board sets them once and leaves them set; so does this.
    """
    p = CACHE / f"{stem}_all.pkl"
    if not p.exists():
        return None
    blob = pickle.loads(p.read_bytes())
    raw = blob["trades"] if isinstance(blob, dict) else blob
    return wa.spread_of(raw)


def daily_series(curve, hold, capital, first_day):
    """One rule's daily equity on the HOLD calendar, as a returns array.

    Every trial in a scenario must sit on an identical date index or the blocks
    do not line up and the ranks compare different spans. portfolio.daily_curve
    starts at the rule's first trade, which differs per rule, so the curve is
    laid on the hold curve's own dates from `first_day` on: days before the
    first trade are flat at `capital` (the account exists, holding cash), and
    any day the curve skips is carried forward.
    """
    dates = hold.index[hold.index >= first_day]
    if len(dates) < MIN_DAYS:
        return None, None
    if not curve:
        eq = pd.Series(float(capital), index=dates)
    else:
        idx = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d, _ in curve])
        raw = pd.Series([float(v) for _, v in curve], index=idx)
        raw = raw[~raw.index.duplicated(keep="last")]
        eq = raw.reindex(dates.union(raw.index)).ffill().reindex(dates)
        eq = eq.fillna(float(capital))
    r_rule = eq.to_numpy()[1:] / eq.to_numpy()[:-1] - 1.0
    h = hold.loc[dates].to_numpy()
    r_hold = h[1:] / h[:-1] - 1.0
    return r_rule, r_rule - r_hold


def block_moments(mat):
    """(n, sum, sumsq) per block per trial -- the only per-split inputs needed.

    A Sharpe over any UNION of blocks is a function of those three sums alone,
    so 12,870 splits become three matrix products instead of 12,870 passes over
    the returns. The tail days that do not divide evenly into S blocks are
    dropped, identically for every trial.
    """
    t, n = mat.shape
    size = t // S_BLOCKS
    cube = mat[:size * S_BLOCKS].reshape(S_BLOCKS, size, n)
    return (np.full(S_BLOCKS, float(size)), cube.sum(axis=1), (cube ** 2).sum(axis=1))


def _sharpe(masks, counts, sums, sumsq):
    """Annualised Sharpe for every (split, trial) pair at once -> (C, N)."""
    n = masks @ counts                      # (C,)
    mean = (masks @ sums) / n[:, None]      # (C, N)
    var = (masks @ sumsq) / n[:, None] - mean ** 2
    sd = np.sqrt(np.maximum(var, 1e-300))
    return np.where(sd > 1e-150, mean / sd * np.sqrt(252.0), 0.0)


def pbo_of(mat, masks):
    """PBO and the logit spread for one scenario's returns matrix (T x N)."""
    t, n = mat.shape
    if t < S_BLOCKS * 20 or n < 3:
        return None
    counts, sums, sumsq = block_moments(mat)
    s_is = _sharpe(masks, counts, sums, sumsq)
    s_oos = _sharpe(1.0 - masks, counts, sums, sumsq)
    best = np.argmax(s_is, axis=1)                                  # (C,)
    picked = s_oos[np.arange(len(best)), best]
    rank = (s_oos <= picked[:, None]).sum(axis=1)                   # 1 = worst
    w = np.clip(rank / (n + 1.0), 1e-6, 1 - 1e-6)
    lg = np.log(w / (1 - w))
    return {"pbo": float((w <= 0.5).mean()), "n_splits": int(len(lg)),
            "median_logit": float(np.median(lg)),
            "median_oos_rank": float(np.median(rank)), "n_trials": n,
            "days": int(t), "logits": lg}


# ------------------------------------------------------------- the run ----
def scenarios():
    """The factorial subset of the board this runs on."""
    out = []
    for ukey in ("all", "large", "mid", "small", "recent"):
        for year in START_YEARS:
            for risk in RISKS:
                for capital in CAPITALS:
                    out.append((ukey, year, risk, capital))
    return out


def curves_for(label, trades, unis, holds, board_stem):
    """Every scenario's daily returns for ONE rule, checkpointed.

    Mirrors wf_attach.cells_for's loop -- sort once by entry_ts, bisect each
    start year off the sorted list, same gridded_years collapse -- so a cell
    the grid wrote as None cannot come back here carrying a curve. The extra
    step is that the curve is laid on the hold calendar from 1 January of the
    start year, identically for all 19 rules, which is what lets the 16 blocks
    line up across trials.
    """
    ck = OUT / f"pbo_ckpt_{board_stem}"
    ck.mkdir(parents=True, exist_ok=True)
    path = ck / f"{label.replace('|', '_')}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())

    out = {}
    for ukey, year, risk, capital in scenarios():
        members = unis[ukey]
        subset = (trades if members is None
                  else [t for t in trades if t["symbol"] in members])
        if not subset:
            continue
        subset = sorted(subset, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        if year not in dd.gridded_years(stamps):
            continue
        cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
        window = subset[cut:]
        if not window:
            continue
        r = portfolio.run(window, capital, risk / 100, PRIORITY)
        curve = r.get("curve") or []
        hold = holds[ukey]
        rr, rx = daily_series(curve, hold, capital, pd.Timestamp(f"{year}-01-01"))
        if rr is None:
            continue
        st = wa.excess_stats(curve, hold)          # for the self-check only
        out[f"{ukey}|{year}|{risk}|{capital}"] = {
            "rule": rr.astype(np.float32), "excess": rx.astype(np.float32),
            "t_hac": None if st is None else st["t_hac"],
            # scripts.dashboard_data's own cell key, built from the label so
            # the self-check can join straight onto dashboard.json
            "cell": f"{label}|{ukey}|{risk:g}|{capital}|{dd.GRID_FILLS[0]}"
                    f"|{year}|{PRIORITY}",
        }
    path.write_bytes(pickle.dumps(out))
    return out


def self_check(store, payload):
    """The full-period t_hac must reproduce dashboard.json, cell for cell.

    Same guard scripts/oos_generalise.py carries. This script re-runs the
    portfolio from the signal caches rather than reading the board's numbers,
    so if any of it has drifted -- the slippage globals, the priority, the
    start-year collapse -- the t_hac will not match and the PBO below would be
    measuring something other than the board.
    """
    de = payload.get("daily_excess") or {}
    checked = bad = 0
    for label, per in store.items():
        if LABELS_BY_NAME[label][1] != "board":
            continue
        for rec in per.values():
            want = de.get(rec["cell"])
            if not want or want.get("t_hac") is None or rec["t_hac"] is None:
                continue
            checked += 1
            if abs(float(want["t_hac"]) - float(rec["t_hac"])) > 0.005:
                bad += 1
                if bad <= 3:
                    print(f"    MISMATCH {rec['cell']}: {rec['t_hac']} vs "
                          f"board {want['t_hac']}")
    print("\nSELF-CHECK -- full-period t_hac against dashboard.json")
    print(f"  {checked:,} board cells compared, {bad} disagree")
    if bad:
        raise SystemExit("  FAIL -- refusing to report a PBO on curves that do "
                         "not reproduce the board")
    print("  PASS -- these are the board's own accounts, kept as daily curves")
    return checked


def run_pbo(store, masks, metric):
    """One PBO per scenario, over the trials present in that scenario."""
    labels = sorted(store)
    keys = sorted({k for per in store.values() for k in per})
    print(f"\n  scenarios seen across all rules: {len(keys)}")
    rows, dropped_n, dropped_len = [], 0, 0
    for key in keys:
        present = [lab for lab in labels if key in store[lab]]
        if len(present) < 3:
            dropped_n += 1
            continue
        lens = {len(store[lab][key][metric]) for lab in present}
        if len(lens) != 1:
            dropped_len += 1
            continue
        mat = np.column_stack([store[lab][key][metric] for lab in present]).astype(float)
        res = pbo_of(mat, masks)
        if res is None:
            dropped_len += 1
            continue
        ukey, year, risk, capital = key.split("|")
        rows.append({"metric": metric, "scenario": key, "universe": ukey,
                     "start": int(year), "risk": float(risk),
                     "capital": int(capital), "n_trials": res["n_trials"],
                     "days": res["days"], "n_splits": res["n_splits"],
                     "pbo": res["pbo"], "median_logit": res["median_logit"],
                     "median_oos_rank": res["median_oos_rank"],
                     "trials": ",".join(present), "_logits": res["logits"]})
    print(f"  {metric:<7} usable scenarios: {len(rows)} "
          f"(dropped {dropped_n} with <3 trials, {dropped_len} too short or ragged)")
    return rows


def report(rows, metric, name):
    d = pd.DataFrame([{k: v for k, v in r.items() if k != "_logits"} for r in rows])
    if d.empty:
        print(f"\n  no usable scenarios for {name}")
        return d
    print(f"\n{'=' * 78}\n{name}")
    print(f"  trials per scenario: {d['n_trials'].min()}-{d['n_trials'].max()} rules"
          f" | splits each: {d['n_splits'].iloc[0]:,} | blocks: {S_BLOCKS}")
    print(f"  PBO across {len(d)} scenarios: median {d['pbo'].median():.3f}, "
          f"range {d['pbo'].min():.3f} to {d['pbo'].max():.3f}")
    print(f"  scenarios with PBO >= 0.5 (winner is a coin flip or worse): "
          f"{int((d['pbo'] >= 0.5).sum())} of {len(d)}")
    print(f"  median logit across scenarios: {d['median_logit'].median():.3f} "
          f"(0.0 = the winner lands exactly on the testing median)")
    for axis in ("universe", "start", "capital"):
        print(f"\n  by {axis}")
        for val, sub in d.groupby(axis):
            print(f"    {str(val):<10}{len(sub):>4} scen   PBO "
                  f"{sub['pbo'].median():>6.3f}   med logit "
                  f"{sub['median_logit'].median():>7.3f}")
    return d


def picture(rows_excess, rows_raw, stamp):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, rows, name in ((axes[0], rows_excess, "excess over hold"),
                           (axes[1], rows_raw, "own returns")):
        if not rows:
            continue
        lg = np.concatenate([r["_logits"] for r in rows])
        ax.hist(lg, bins=60, color="#4c72b0", edgecolor="none")
        ax.axvline(0.0, color="#c44e52", lw=1.5)
        pbo = float((lg <= 0).mean())
        ax.set_title(f"ranking on {name}\npooled PBO = {pbo:.3f}")
        ax.set_xlabel("logit of the winner's out-of-sample rank\n"
                      "(left of the red line = below the testing median)")
        ax.set_ylabel("splits")
    fig.suptitle(f"Probability of backtest overfitting, {S_BLOCKS} blocks, "
                 f"all {len(LABELS)} rules as trials")
    fig.tight_layout()
    p = FIG / f"pbo_{stamp}.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    print(f"  wrote {p.relative_to(OUT.parent)}")


def main():
    t0 = time.time()
    payload = json.loads((CLEAN / "dashboard.json").read_text())
    board = wa.board_key(payload)
    print(f"dashboard.json built {payload['built']}, "
          f"{len(payload['grid']):,} grid cells, board key {board}")
    print(f"blocks {S_BLOCKS}, splits per scenario "
          f"{math.comb(S_BLOCKS, S_BLOCKS // 2):,}, priority {PRIORITY}")

    cfg = load()
    members = cfg.merged
    print(f"universe: {len(members)} stocks")
    unis = wa.stock_universes(members)
    wa.assert_buckets_match_payload(unis, payload)
    holds = wa.hold_curves(members, unis, [], board)

    want = scenarios()
    print(f"\nscenarios requested: {len(want)} "
          f"({len(want) // (len(START_YEARS) * len(RISKS) * len(CAPITALS))} universes "
          f"x {len(START_YEARS)} starts x {len(RISKS)} risks x {len(CAPITALS)} capitals)")
    print(f"cells to run: {len(want) * len(LABELS):,}")

    store = {}
    for i, (stem, (label, cohort)) in enumerate(LABELS.items(), 1):
        s0 = time.time()
        trades = trades_of(stem)
        if trades is None:
            print(f"  [{i:2}/{len(LABELS)}] {label:<14} skipped -- no signal cache")
            continue
        per = curves_for(label, trades, unis, holds, board)
        store[label] = per
        print(f"  [{i:2}/{len(LABELS)}] {label:<14} {cohort:<9} "
              f"{len(trades):>7,} trades -> {len(per):>3} scenarios "
              f"[{(time.time()-s0)/60:.1f} min]", flush=True)

    print(f"\nrules with curves: {len(store)} of {len(LABELS)}")
    self_check(store, payload)

    masks = np.zeros((math.comb(S_BLOCKS, S_BLOCKS // 2), S_BLOCKS))
    for r, combo in enumerate(combinations(range(S_BLOCKS), S_BLOCKS // 2)):
        masks[r, list(combo)] = 1.0

    rows_x = run_pbo(store, masks, "excess")
    rows_r = run_pbo(store, masks, "rule")
    d_x = report(rows_x, "excess", "RANKED ON DAILY EXCESS OVER HOLD "
                                   "(the criterion the 19 -> 9 cut actually used)")
    d_r = report(rows_r, "rule", "RANKED ON OWN RETURNS (the textbook statistic)")

    print(f"\n{'=' * 78}\nVERDICT")
    for d, name in ((d_x, "excess over hold"), (d_r, "own returns")):
        if d.empty:
            continue
        med = d["pbo"].median()
        print(f"  ranking on {name:<18} median PBO {med:.3f}"
              f"   ({int((d['pbo'] >= 0.5).sum())}/{len(d)} scenarios at or above 0.5)")
    print("\n  Read 0.5 as 'the rule that looked best on half the history was")
    print("  below average on the other half as often as not' -- a ranking with")
    print("  no information in it. Lower is a more durable ranking; it is NOT a")
    print("  statement that any rule beats buy-and-hold, which item 14 settled")
    print("  separately and in the other direction.")
    print(f"\n  PRE-REGISTERED: I predicted 0.3-0.6. Outcome: "
          f"{'' if d_x.empty else f'{d_x.pbo.median():.3f} on the excess ranking'}.")

    stamp = date.today()
    out = OUT / "measurements" / f"pbo_{stamp}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    full = pd.concat([d for d in (d_x, d_r) if not d.empty], ignore_index=True)
    full.to_csv(out, index=False)
    (MEAS / f"pbo_{stamp}.json").write_text(json.dumps({
        "built": payload["built"], "board_key": board, "blocks": S_BLOCKS,
        "priority": PRIORITY, "n_trials": len(store),
        "median_pbo_excess": None if d_x.empty else float(d_x["pbo"].median()),
        "median_pbo_rule": None if d_r.empty else float(d_r["pbo"].median()),
    }, indent=2))
    picture(rows_x, rows_r, stamp)
    print(f"  wrote {out.relative_to(OUT.parent)} ({len(full)} rows x "
          f"{len(full.columns)} cols)  [{(time.time()-t0)/60:.1f} min]")


if __name__ == "__main__":
    main()

"""Do any of Kakushadze's 101 alphas make a better cash-priority than mom_hi?

    python3 -m scripts.alpha_priority --pilot     # 1 alpha x 1 cell, timing
    python3 -m scripts.alpha_priority             # the 52 clean alphas
    python3 -m scripts.alpha_priority --vwap      # add the 29 on substituted vwap

THE QUESTION, and why it is a priority test and not a strategy test. A rule
points at ~200 stocks a day and the account can fund ~3, so something decides
which. `scripts/priority_control.py` measured that choice against a null on
2026-09-11: `mom_hi` (strongest 12-month return first) beat the mean of 20
seeded shuffles by +3.84 CAGR points in 35 of 38 cells and is now the default.
The 101 are cross-sectional RANKING formulas -- exactly the shape of that
decision -- so they can be dropped into the same harness and scored on the same
axis. They are NOT being tested as strategies; none of them says when to buy.

WHAT IT REUSES, EXACTLY. The 38 cells (19 cached rules x 2 account sizes), the
costs (slippage.ENABLED, MAX_PARTICIPATION 0.01), the spread charged with
slippage.apply_spread, RISK 1%, and -- the expensive part -- **the 20 seeded
shuffles already in output/measurements/priority_control_2026-09-11.csv**. The
null costs nothing to reuse, so only the alphas are computed here.

HOW AN ORDERING IS INSTALLED WITHOUT A REBUILD. portfolio._order sorts by
entry_ts with a STABLE sort when priority="time", so pre-sorting the trade list
and passing "time" installs an arbitrary tie-break without touching kitelab/*.py
-- no signal-cache digest moves and no 103-150 min rebuild is triggered. Same
trick, same verification, as priority_control.py.

POINT-IN-TIME. The engine reads the PREVIOUS close for every priority feature
(`portfolio.momentum_at`, `slippage.liquidity_at` -- "ranking today's candidates
by today's turnover would be lookahead"). So every alpha is **shifted one
session** before lookup: a trade entering on day t is ranked by the alpha as of
t-1. That is a real handicap on alphas built from the entry bar's own range, and
it is the honest reading.

DIRECTION. Highest alpha first, once. The paper's alphas are signed weights --
positive means long -- and the sign is already inside each formula (many open
with `-1 *`). The mirror (lowest first) is the control that `mom_lo` played for
`mom_hi`, and it is NOT run here: it would double a 35-minute job and double the
number of trials. Left as a follow-up for whichever alpha, if any, stands out.

WHAT THIS RUN CAN AND CANNOT SETTLE, stated before it runs. 52 alphas against
the same 38 cells is 52 fresh chances to be lucky. The null is built by
resampling the existing shuffles (one per cell, 10,000 draws) rather than from
20 raw draws, so p resolves finely enough for a Benjamini-Hochberg correction
across the 52. Even so the cells are correlated -- 19 rules at 2 account sizes,
and the rules themselves correlate up to 0.646 -- so the null understates the
true spread and a small p here is weaker than it looks. This run can rank the
alphas against mom_hi and rule bad ones out. It cannot certify a winner.

PRE-REGISTERED PREDICTION (written before the run): 0 to 3 alphas clear BH, and
none beats mom_hi's +3.84 median gap. Reason: mom_hi survived an out-of-sample
split here already, and these 52 formulas were fitted to US equities on
intraday data this workbench does not have.

Reads:  scripts/data/alpha101.json, the 19 *_all.pkl signal caches, the daily
        parquet candles, output/measurements/priority_control_2026-09-11.csv
Writes: output/alpha_ckpt_<n>.pkl          (per-alpha values at trade coords)
        output/measurements/alpha_priority_<date>.csv   (resumable)
        output/alpha_priority_<date>.png
Touches nothing under kitelab/, so no cache and no grid partition is invalidated.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import pickle
import re
import time

import numpy as np

from kitelab import config, portfolio, signals, slippage
from scripts import alpha_eval as A
from scripts.priority_control import CELLS, CAPITALS, PARTICIPATION, RISK, REQUIRED

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
TODAY = dt.date.today().isoformat()
CSV = os.path.join(OUT, "measurements", f"alpha_priority_{TODAY}.csv")
PNG = os.path.join(OUT, f"alpha_priority_{TODAY}.png")
NULL_CSV = os.path.join(OUT, "measurements", "priority_control_2026-09-11.csv")
# The mom_hi COLUMN OF NULL_CSV IS THE PRE-FIX ONE -- written before the
# _desc/_asc NaN-sentinel fix, so it sorted no-history trades first and reads
# +4.94 / 37 of 38 instead of the corrected +3.84 / 35 of 38. The recheck below
# re-ran mom_hi alone against the SAME 20 shuffles (verified here: its
# shuffle_mean matches the mean of NULL_CSV's rand rows to 3.6e-15 in all 38
# cells), so its `engine` column drops in as mom_hi's CAGR with no rescaling.
# Every other named ordering in NULL_CSV reproduces under the fix unchanged.
MOM_HI_CSV = os.path.join(OUT, "measurements",
                          "mom_hi_engine_recheck_2026-09-11.csv")
CKPT = os.path.join(OUT, f"alpha_ckpt_{TODAY}.pkl")
N_NULL_DRAWS = 10_000


def split_alphas() -> tuple[list[str], list[str], list[str]]:
    """(clean, needs-vwap, blocked). Blocked = needs an industry classification
    or market cap, neither of which exists for these 1,000 NSE names."""
    clean, vwap, blocked = [], [], []
    for k in sorted(A.FORMULAS, key=int):
        f = A.FORMULAS[k].lower()
        if "indneutralize" in f or re.search(r"\bcap\b", f):
            blocked.append(k)
        elif "vwap" in f:
            vwap.append(k)
        else:
            clean.append(k)
    return clean, vwap, blocked


def _desc(x: float) -> float:
    """Biggest first; a missing value sorts LAST, as portfolio._order does.
    priority_control.py:_desc, restated so this script has no hidden coupling
    to the bug that was fixed there on 2026-09-11 (NaN became -inf and sorted
    FIRST, moving single cells by up to 3.5 CAGR points)."""
    return math.inf if not np.isfinite(x) else -x


def _asc(x: float) -> float:
    """SMALLEST first -- the mirror of _desc. A missing value still sorts LAST,
    which is the point: flipping the direction must not also flip where the
    no-history trades go, or the mirror measures two changes at once."""
    return math.inf if not np.isfinite(x) else x


def coords(trades: list[dict], index, columns) -> tuple[np.ndarray, np.ndarray]:
    """Row/column positions of each trade's (entry session, symbol) in the panel.
    -1 where the session or the symbol is not in the panel."""
    col_of = {s: i for i, s in enumerate(columns)}
    stamps = index.to_numpy().astype("datetime64[ns]")
    ts = np.array([np.datetime64(t["entry_ts"], "ns") for t in trades])
    rows = np.searchsorted(stamps, ts, "left")
    rows = np.where((rows < len(stamps)) & (stamps[np.minimum(rows, len(stamps) - 1)] == ts),
                    rows, -1)
    cols = np.array([col_of.get(t["symbol"], -1) for t in trades])
    return rows, cols


def build_values(alphas: list[str], cell_coords: dict, panel, got: dict) -> dict:
    """{alpha: {cache: float32 vector over that cache's trades}}.

    Each alpha is computed ONCE over the whole panel, SHIFTED ONE SESSION, read
    at every cache's trade coordinates, and then thrown away -- holding 52 full
    (5,124 x 1,000) matrices would be ~2 GB for no reason.
    """
    print(f"  reading {sum(len(v[0]) for v in cell_coords.values()):,} trade "
          f"coordinates out of each alpha\n")
    t0 = time.time()
    want = set(cell_coords)
    for n, key in enumerate(alphas, 1):
        # A checkpointed alpha counts as done only if it covers EVERY cache this
        # run needs. Checking `key in got` alone let a --pilot checkpoint (one
        # cache) satisfy the full 19-cache run, which then died at cache 2 with
        # KeyError 'QMW_b0' twenty minutes in.
        if want <= set(got.get(key, ())):
            continue
        t1 = time.time()
        # shift(1): rank today's candidates on yesterday's close, as the engine does.
        mat = A.evaluate(key, panel).shift(1).to_numpy(dtype="float32")
        per = {}
        for cache, (rows, cs) in cell_coords.items():
            v = np.full(len(rows), np.nan, dtype="float32")
            ok = (rows >= 0) & (cs >= 0)
            v[ok] = mat[rows[ok], cs[ok]]
            per[cache] = v
        got[key] = per
        cover = np.mean([np.isfinite(v).mean() for v in per.values()])
        print(f"  [{n:>2}/{len(alphas)}] alpha#{key:<4} {time.time()-t1:5.1f}s  "
              f"defined at {cover:5.1%} of trades")
        with open(CKPT, "wb") as fh:
            pickle.dump(got, fh, protocol=4)
    print(f"\n  {len(alphas)} alphas in {(time.time()-t0)/60:.1f} min -> {CKPT}\n")
    return got


def cells_caches(cells) -> list[str]:
    """The cache stems this run needs, in cell order."""
    return [c for c, _ in cells]


# ----------------------------------------------------- the trade caches ---
_STALE_OK = {"code", "n_code", "producer"}
_TOLERATED: list = []


def cached_trades(cache: str, universe) -> list[dict]:
    """The board's own cached trades for one rule, WITHOUT the stamp gate.

    `signals.load` returns None for 10 of these 19 and the run dies at the
    first one. The reason is not that any trade moved: those 10 rules were CUT
    from the board on 2026-09-11, so `signals._narrow` can no longer resolve
    their name to a producer and they fall back to the whole-board digest --
    "an unrecognised cache name gets the whole-board digest, not the loosest
    one". The cache is judged against a wider set of modules than the one it
    was written under, and mismatches by construction.

    So the stamp is checked HERE instead, and only the three keys that encode
    that scope change are tolerated. `universe`, `n_symbols`, `data` and
    `n_files` must still match exactly -- a cache built from a different
    universe or different price files is refused as loudly as before.
    Verified 2026-09-16: all 19 differ on nothing but those three keys, and
    all 19 trade counts equal those in output/logs/pbo_run.log from the same
    day. `scripts/pbo.py` and `scripts/oos_generalise.py` read these same
    bytes and check nothing at all; this is the stricter version of that.
    """
    path = os.path.join(signals.CACHE, f"{cache}_all.pkl")
    if not os.path.exists(path):
        raise SystemExit(f"{cache}: no signal cache at {path} -- "
                         "run scripts.refresh first")
    with open(path, "rb") as fh:
        blob = pickle.load(fh)
    if not isinstance(blob, dict):
        raise SystemExit(f"{cache}: pre-2026-09-01 unstamped cache, refusing")
    got = blob.get("stamp", {})
    want = signals.stamp(universe, account=False,
                         producer=signals._narrow(f"{cache}_all", False))
    diff = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
    hard = [k for k in diff if k not in _STALE_OK]
    if hard:
        raise SystemExit(
            f"\n  {cache}: signal cache disagrees on {hard}, which is NOT the\n"
            "  off-the-board scope change this script tolerates. It was built\n"
            "  from a different universe or different price files. Rebuild:\n"
            "      python3 -m scripts.dashboard_data\n")
    if diff:
        _TOLERATED.append(cache)
    return blob["trades"]


# --------------------------------------------------------- the null ------
def shuffle_null() -> dict:
    """{(cache, capital): [20 shuffle CAGRs]} from the 2026-09-11 run.

    These are the same 38 cells under 20 seeded random orderings, costs on --
    the absence of a priority rule, which `time` is not (it sorts on entry_ts
    alone and leaves ties to config order, a fixed undesigned bias).
    """
    if not os.path.exists(NULL_CSV):
        raise SystemExit(f"need {NULL_CSV} -- run scripts.priority_control first")
    out: dict = {}
    named: dict = {}
    with open(NULL_CSV) as fh:
        for r in csv.DictReader(fh):
            k = (r["cache"], int(r["capital"]))
            if r["ordering"].startswith("rand"):
                out.setdefault(k, []).append(float(r["cagr"]))
            else:
                named.setdefault(r["ordering"], {})[k] = float(r["cagr"])
    sizes = {len(v) for v in out.values()}
    print(f"  null: {len(out)} cells x {sizes} seeded shuffles from {os.path.basename(NULL_CSV)}")

    # Overwrite the stale mom_hi column -- see MOM_HI_CSV above.
    if not os.path.exists(MOM_HI_CSV):
        raise SystemExit(f"need {MOM_HI_CSV} -- the mom_hi column of "
                         f"{os.path.basename(NULL_CSV)} is the pre-fix one")
    fixed = {}
    with open(MOM_HI_CSV) as fh:
        for r in csv.DictReader(fh):
            fixed[(r["cache"], int(r["capital"]))] = float(r["engine"])
    stale = named.get("mom_hi", {})
    moved = sum(1 for k, v in fixed.items() if abs(v - stale.get(k, v)) > 1e-9)
    named["mom_hi"] = fixed
    print(f"  mom_hi: replaced by {os.path.basename(MOM_HI_CSV)} "
          f"({len(fixed)} cells, {moved} differ from the pre-fix column)")
    return out, named


def score(gaps: dict, null: dict, rng) -> tuple[float, int, float]:
    """(median gap to the shuffle mean, cells above it, permutation p).

    The p-value's null is built by drawing ONE of the 20 shuffles per cell and
    scoring it against the mean of the others, 10,000 times. That keeps each
    cell's own scale and gives a finer resolution than the 20 raw draws, but it
    treats the 38 cells as independent and they are not, so it understates the
    spread -- see the run's docstring.
    """
    cells = sorted(gaps)
    stat = float(np.median([gaps[c] for c in cells]))
    above = sum(1 for c in cells if gaps[c] > 0)
    cols = []
    for c in cells:
        v = np.asarray(null[c])
        loo = v - (v.sum() - v) / (len(v) - 1)      # each shuffle vs the other 19
        cols.append(loo)
    stack = np.array(cols)                           # cells x 20
    pick = rng.integers(0, stack.shape[1], size=(N_NULL_DRAWS, stack.shape[0]))
    draws = np.median(stack[np.arange(stack.shape[0]), pick], axis=1)
    p = float((np.abs(draws) >= abs(stat)).mean())
    return stat, above, max(p, 1.0 / N_NULL_DRAWS)


def bh(pvals: list[float], q: float = 0.05) -> float:
    """Benjamini-Hochberg threshold: the largest p_(i) with p_(i) <= i*q/m."""
    m = len(pvals)
    keep = [p for i, p in enumerate(sorted(pvals), 1) if p <= i * q / m]
    return max(keep) if keep else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="1 alpha x 1 cell, for timing")
    ap.add_argument("--mirror", action="store_true",
                    help="rank LOWEST-first instead of highest-first")
    ap.add_argument("--vwap", action="store_true",
                    help="also run the 29 alphas needing a vwap stand-in")
    args = ap.parse_args()

    clean, vwap, blocked = split_alphas()
    print(f"\n  alpha101: {len(clean)} clean | {len(vwap)} need a vwap stand-in "
          f"| {len(blocked)} blocked (industry classification or market cap)")
    alphas = clean + (vwap if args.vwap else [])
    if args.pilot:
        alphas = alphas[:1]
    print(f"  running {len(alphas)} alphas, "
          + ("lowest-first (MIRROR)" if args.mirror else "highest-first"))

    global CSV, PNG
    if args.mirror:
        CSV = CSV.replace(".csv", "_mirror.csv")
        PNG = PNG.replace(".png", "_mirror.png")
        print("  MIRROR: ranking lowest-first (the checkpointed alpha values "
              "are reused unchanged; only the sort flips)")
    order_key = _asc if args.mirror else _desc

    cfg = config.load()
    universe = cfg.merged
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = PARTICIPATION
    slippage.reset()
    print(f"  COSTS ON: slippage.ENABLED={slippage.ENABLED}, "
          f"MAX_PARTICIPATION={slippage.MAX_PARTICIPATION}")

    cells = CELLS[:1] if args.pilot else CELLS
    caps = CAPITALS[1:] if args.pilot else CAPITALS
    print(f"  {len(cells)} rules x {len(caps)} account sizes x {len(alphas)} alphas "
          f"= {len(cells) * len(caps) * len(alphas)} runs\n")

    null, named = shuffle_null()

    # ---- stage A: every alpha's value at every trade's entry (shifted 1) ----
    got: dict = {}
    if os.path.exists(CKPT):
        with open(CKPT, "rb") as fh:
            got = pickle.load(fh)
        print(f"  checkpoint {os.path.basename(CKPT)}: {len(got)} alphas already computed")
    need = [k for k in alphas if not set(cells_caches(cells)) <= set(got.get(k, ()))]
    if need:
        panel = A.build_panel(sorted(universe))
        idx, cols = panel["close"].index, panel["close"].columns
        cell_coords = {}
        for cache, label in cells:
            base = cached_trades(cache, universe)
            cell_coords[cache] = coords(base, idx, cols)
            hit = ((cell_coords[cache][0] >= 0) & (cell_coords[cache][1] >= 0)).mean()
            print(f"    {label:<38} {len(base):>8,} trades, "
                  f"{hit:5.1%} land on a panel session")
            del base
        if _TOLERATED:
            print(f"\n    {len(_TOLERATED)} of {len(cells)} caches are off the "
                  "2026-09-11 board, so their stamp is checked against the whole")
            print("    board rather than their own producer. Universe, symbol "
                  "count and price files match exactly on every one;")
            print("    only the code-digest SCOPE differs: "
                  + ", ".join(sorted(_TOLERATED)))
        got = build_values(alphas, cell_coords, panel, got)
        del panel

    # ---- stage B: one portfolio run per (rule, capital, alpha) ----
    done, rows = {}, []
    if os.path.exists(CSV) and not args.pilot:
        with open(CSV) as fh:
            for r in csv.DictReader(fh):
                done[(r["cache"], r["capital"], r["alpha"])] = r
        rows = list(done.values())
        print(f"  resuming: {len(done)} runs already on disk\n")

    t_start = time.time()
    for cache, label in cells:
        if all((cache, str(c), k) in done for c in caps for k in alphas):
            print(f"  {label}: all {len(caps) * len(alphas)} runs already on disk")
            continue
        base = cached_trades(cache, universe)
        missing = [k for k in REQUIRED if k not in base[0]]
        if missing:
            raise SystemExit(f"{label}: cached trades lack {missing}")
        n_before = len(base)
        trades = [slippage.apply_spread(t) for t in base]
        print(f"\n  {label}: {n_before:,} cached -> {len(trades):,} after spread (no filter)")
        for cap in caps:
            for key in alphas:
                if (cache, str(cap), key) in done:
                    continue
                v = got[key][cache]
                # Symbol last so the ordering is fully deterministic, as
                # portfolio._order does.
                lst = sorted(range(len(trades)),
                             key=lambda i: (order_key(float(v[i])), trades[i]["symbol"]))
                lst = [trades[i] for i in lst]
                t1 = time.time()
                r = portfolio.run(lst, cap, RISK, priority="time")
                el = time.time() - t1
                base_cagr = float(np.mean(null[(cache, cap)]))
                rows.append({
                    "cache": cache, "label": label, "capital": cap, "alpha": key,
                    "formula": A.FORMULAS[key],
                    "defined_pct": round(100 * float(np.isfinite(v).mean()), 2),
                    "cagr": round(r["cagr_pct"], 3),
                    "shuffle_mean_cagr": round(base_cagr, 3),
                    "gap": round(r["cagr_pct"] - base_cagr, 3),
                    "mar": round(r["mar"], 3) if r["mar"] is not None else "",
                    "maxdd_pct": round(r["max_drawdown_pct"], 2),
                    "taken": len(r["taken"]), "signals": r["signals"],
                    "exposure_pct": round(r["fully_invested_pct"], 2),
                    "wiped": r["wiped"], "seconds": round(el, 1)})
                with open(CSV, "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
            print(f"    Rs{cap:>10,}  {len(alphas)} alphas done  "
                  f"[{(time.time()-t_start)/60:.1f} min elapsed]")


    report(rows, null, named, alphas, clean)

def report(rows, null, named, alphas, clean) -> None:
    """Rank the alphas against the shuffle null and against mom_hi."""
    rng = np.random.default_rng(20260916)
    by_alpha: dict = {}
    for r in rows:
        by_alpha.setdefault(str(r["alpha"]), {})[(r["cache"], int(r["capital"]))] = float(r["gap"])

    print("\n" + "=" * 78)
    print("REFERENCE -- the named orderings on the same 38 cells (2026-09-11)")
    for name in ("mom_hi", "nearhigh_hi", "tight", "time", "liquidity", "mom_lo"):
        if name not in named:
            continue
        gaps = {k: v - float(np.mean(null[k])) for k, v in named[name].items() if k in null}
        if not gaps:
            continue
        s, a, p = score(gaps, null, rng)
        print(f"  {name:<14} median gap {s:+6.2f} pts   above the shuffle mean "
              f"{a:>2}/{len(gaps)}   p {p:.4f}")

    scored = []
    for key in alphas:
        gaps = {k: v for k, v in by_alpha.get(key, {}).items() if k in null}
        if len(gaps) < len(null):
            print(f"  alpha#{key}: only {len(gaps)} of {len(null)} cells -- skipped")
            continue
        s, a, p = score(gaps, null, rng)
        scored.append({"alpha": key, "stat": s, "above": a, "n": len(gaps), "p": p,
                       "group": "clean" if key in clean else "vwap"})
    scored.sort(key=lambda d: -d["stat"])
    thr = bh([d["p"] for d in scored], 0.05)

    print("\n" + "=" * 78)
    print(f"THE {len(scored)} ALPHAS, best first")
    print(f"  {'alpha':<10} {'group':<6} {'median gap':>11} {'above null':>11} {'p':>8}  BH")
    for d in scored:
        mark = "  <- clears BH" if d["p"] <= thr and thr > 0 else ""
        print(f"  #{d['alpha']:<9} {d['group']:<6} {d['stat']:+11.2f} "
              f"{d['above']:>7}/{d['n']:<3} {d['p']:>8.4f}{mark}")

    mom = None
    if "mom_hi" in named:
        g = {k: v - float(np.mean(null[k])) for k, v in named["mom_hi"].items() if k in null}
        mom = score(g, null, rng)[0]

    print("\n" + "=" * 78)
    print("VERDICT")
    pos = [d for d in scored if d["stat"] > 0]
    sig = [d for d in scored if thr > 0 and d["p"] <= thr]
    print(f"  alphas with a positive median gap to the shuffle null: {len(pos)} of {len(scored)}")
    print(f"  clearing Benjamini-Hochberg at q=0.05 (threshold p <= {thr:.4f}): {len(sig)}")
    if mom is not None:
        beat = [d for d in scored if d["stat"] > mom]
        print(f"  beating mom_hi's {mom:+.2f} median gap: {len(beat)} "
              f"{[('#' + d['alpha']) for d in beat[:8]]}")
    print("\n  PRE-REGISTERED: 0 to 3 clear BH, and none beats mom_hi.")
    print("  A small p here is weaker than it looks: the 38 cells are 19 rules")
    print("  at 2 account sizes and the rules correlate up to 0.646, so the")
    print("  resampled null understates the true spread. This ranks and rules")
    print("  out; it does not certify a winner.")

    plot(scored, thr, mom)
    with open(CSV) as fh:
        n = sum(1 for _ in fh) - 1
    print(f"\n  wrote {CSV} ({n} rows) and {PNG}\n")


def plot(scored, thr, mom) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(max(10, 0.22 * len(scored)), 6.0))
    xs = range(len(scored))
    cols = ["#d62728" if (thr > 0 and d["p"] <= thr) else
            ("#1f77b4" if d["group"] == "clean" else "#7f7f7f") for d in scored]
    ax.bar(xs, [d["stat"] for d in scored], color=cols)
    ax.axhline(0, color="black", lw=1)
    if mom is not None:
        # Drawn from the number report() just printed, never hardcoded: the two
        # disagreed in the pilot because report() read the pre-fix mom_hi column.
        ax.axhline(mom, color="#2ca02c", ls="--", lw=1.2,
                   label=f"mom_hi, the incumbent ({mom:+.2f} pts)")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"#{d['alpha']}" for d in scored], rotation=90, fontsize=6)
    ax.set_ylabel("median CAGR gap to the shuffle null, points/yr")
    ax.set_title(f"Alpha101 as a cash-priority: {len(scored)} alphas over 38 cells, "
                 f"costs on ({TODAY})")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(PNG, dpi=140)


if __name__ == "__main__":
    main()

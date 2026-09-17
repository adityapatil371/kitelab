"""Where the user's Pine rule lands on the board's OWN selection criterion.

    python3 -m scripts.pine_span --pilot    # 150 symbols, times the loop
    python3 -m scripts.pine_span            # the full run

WHY THIS EXISTS. The Pine (HA no-wick + HTF EMA + RSI) was rejected on
2026-09-16 because it ranked badly -- 0 of 276 cells at the gate. The NEXT day
`scripts/pbo.py` measured the probability of backtest overfitting of
rank-selection on this board at 0.412, worse than a coin flip, and the board
was rebuilt returns-blind: `scripts/board_span.py` picks entries by COVERAGE of
the firing space (maximin on d = 1 - max(phi, 0)), never by what they earned.
The Pine was never offered to that criterion -- `entry_exit_grid.build_signals`
holds nine simple conditions and no HA rule -- so its exclusion from today's
board is an oversight rather than a decision. This script closes that gap by
measuring the one number the criterion reads.

NO RETURNS ARE READ. Not CAGR, not R, not excess-vs-hold, nowhere in this file.
The only thing computed is WHICH stock-sessions each rule fires on. That is the
point: the 2026-09-16 rejection and this measurement must not be able to
contaminate each other.

WHAT IS COMPARED. Three panel families, all boolean, all on the same daily
session x symbol rectangle, all restricted to LISTED cells (the rectangle is
~40% empty and counting empty cells as agreement inflates phi):

  board:<key>|<variant>  the 20 rows on the board now, recovered from their
                         signal caches (an entry stamp IS a signal)
  cand:<name>            the nine candidates board_span.py chose from
  pine:<tf>-<legs>       the Pine, daily and weekly, with and without its RSI leg
  new:<name>             five further entry rules, PRE-REGISTERED below on
                         2026-09-17 before any of this was run

THE FIVE NEW RULES, and why each is a different question from the board's ten.
Written down before the first run, so the shelf cannot be edited into a
flattering answer afterwards -- which is the only thing that stops this from
becoming the rank-selection the board was rebuilt to escape.

  new:gapdn    open <= previous close x 0.97. `cand:gap` is the up-gap; nothing
               anywhere on the board or the shelf fires on a gap DOWN.
  new:inside   high < previous high AND low > previous low. `vcon` asks how
               NARROW a bar is; this asks whether it fits inside yesterday's,
               which is containment and not width.
  new:rsi30    RSI(14) closes above 30 having closed below it. The board holds
               no oscillator at all -- ten trend, range and calendar rules.
  new:low252   close at its lowest in 252 sessions. `mr` is a 20-day low; a
               one-year low is a different population of stocks, not a longer
               version of the same one.
  new:dryup    volume below half its 50-day median. The exact opposite of
               `vol` (volume 3x its median), and untested in either direction.

TWO CAVEATS, STATED RATHER THAN IMPLIED.

  1. The board panels are TRADES, so a rule cannot re-fire on a stock it is
     already holding; candidate and Pine panels are raw conditions with no such
     block. This understates the board rules' firing rate, which biases phi
     DOWN for everyone equally -- it does not favour the Pine.
  2. The Pine's best variant in 2026-09-16 was WEEKLY, and the board is daily.
     A weekly firing is stamped on the session the weekly bar CLOSED (`end_ts`,
     the decision session -- not the Monday the bar is dated to). The `+1`
     panels stamp the next session instead, because the Pine fills at the next
     open; both are reported, since the 2026-09-16 run showed Pine findings can
     flip sign with the execution convention.

Reads:  the cleaned parquet candles, via kitelab.frames
        <CLEAN>/signal_cache/*_all.pkl          (the board's 20 entry panels)
Writes: output/measurements/pine_span_<date>.csv
        output/pine_span_<date>.json
        output/pine_span_<date>.png
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from kitelab import config, indicators
from scripts import entry_exit_grid as G
from scripts import entry_zoo as Z
from scripts import wf_pine as P
from scripts.xrank_account import spread_off

OUT = Path(__file__).resolve().parent.parent / "output"

# The slate `board_span.py` actually selected on 2026-09-17: ten entry families,
# maximin on the firing panel, max pairwise phi 0.103. That number is the bar a
# new entry has to clear to be "as distinct as the ones already there".
SLATE_MAX_PHI = 0.103

# (label, timeframe, use_rsi, session shift). The shift is the convention test
# of caveat 2 above, run only on the full weekly rule to keep the matrix small.
PINE_PANELS = [
    ("pine:D-full", "D", True, 0),
    ("pine:D-norsi", "D", False, 0),
    ("pine:W-full", "W", True, 0),
    ("pine:W-norsi", "W", False, 0),
    ("pine:W-full+1", "W", True, 1),
]


def phi(x: np.ndarray, y: np.ndarray) -> float:
    """Phi between two boolean vectors, from the 2x2 table.

    Same quantity as entry_zoo.phi_jaccard's first return value; computed from
    counts in float64 rather than a float32 mean, which is why main() checks
    the two against each other before trusting this one.
    """
    a = float(np.count_nonzero(x & y))
    b = float(np.count_nonzero(x & ~y))
    c = float(np.count_nonzero(~x & y))
    d = float(len(x)) - a - b - c
    den = (a + b) * (c + d) * (a + c) * (b + d)
    if den <= 0:
        return float("nan")
    return (a * d - b * c) / np.sqrt(den)


def extra_signals(p):
    """The five pre-registered rules in the module docstring, as panels.

    Same shape and same treatment as entry_exit_grid.build_signals: every rule
    reads only the current bar and older ones, and every panel is masked to
    listed cells so an unlisted stock cannot register a firing.
    """
    o, h, l, c, v = (p["open"], p["high"], p["low"], p["close"], p["volume"])
    listed = c.notna()
    sig = {}
    sig["gapdn"] = o.le(c.shift(1) * 0.97)
    sig["inside"] = h.lt(h.shift(1)) & l.gt(l.shift(1))
    r = indicators.rsi(c, 14)
    sig["rsi30"] = r.gt(30) & r.shift(1).le(30)
    sig["low252"] = c.le(c.rolling(252, min_periods=252).min())
    vmed = v.rolling(50, min_periods=50).median()
    sig["dryup"] = v.lt(0.5 * vmed) & vmed.gt(0)
    for k in sig:
        sig[k] = (sig[k].fillna(False) & listed)
        print(f"  new:{k:<10} fires on {int(sig[k].to_numpy().sum()):>9,} "
              f"stock-sessions")
    return sig


def pine_panels(members, index, columns):
    """The Pine's firing panels, placed on the board's daily session index.

    One scripts.wf_pine.signals() call per (symbol, timeframe); the RSI leg is
    switched off with the same entry_mask the 2026-09-16 run used, so `norsi`
    here is that run's `pineW-norsi` and not a re-derivation of it.
    """
    row_pos = {t: i for i, t in enumerate(index)}
    col_pos = {s: i for i, s in enumerate(columns)}
    mats = {lab: np.zeros((len(index), len(columns)), dtype=bool)
            for lab, _, _, _ in PINE_PANELS}
    tfs = sorted({tf for _, tf, _, _ in PINE_PANELS})
    kept = {tf: 0 for tf in tfs}
    empty = {tf: 0 for tf in tfs}
    placed = {lab: 0 for lab in mats}
    missed = {lab: 0 for lab in mats}

    t0 = time.time()
    for n, symbol in enumerate(members, 1):
        ci = col_pos.get(symbol)
        if ci is None:
            continue
        for tf in tfs:
            sig = P.signals(symbol, tf)
            if sig.empty:
                empty[tf] += 1
                continue
            kept[tf] += 1
            stamp = (sig["end_ts"] if "end_ts" in sig.columns else sig["ts"])
            rows = np.array([row_pos.get(pd.Timestamp(t).normalize(), -1)
                             for t in stamp], dtype=int)
            for lab, want_tf, use_rsi, shift in PINE_PANELS:
                if want_tf != tf:
                    continue
                mask = P.entry_mask(sig, "long", use_rsi=use_rsi)
                r = rows[mask]
                r = r[r >= 0] + shift
                r = r[r < len(index)]
                missed[lab] += int(mask.sum()) - len(r)
                placed[lab] += len(r)
                mats[lab][r, ci] = True
        if n % 250 == 0:
            print(f"    ... {n:,} of {len(members):,} symbols "
                  f"({time.time() - t0:.0f}s)", flush=True)

    for tf in tfs:
        print(f"  {tf}: {kept[tf]:,} symbols with usable bars, "
              f"{empty[tf]:,} too short for the warmup")
    for lab in mats:
        print(f"  {lab:<16} {placed[lab]:>9,} firings placed, "
              f"{missed[lab]:>7,} off the daily index")
    return {lab: pd.DataFrame(m, index=index, columns=columns)
            for lab, m in mats.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    stamp = date.today().isoformat()

    cfg = config.load()
    members = sorted(cfg.merged)
    if args.pilot:
        members = members[:150]
    print(f"\n  universe: {len(members):,} symbols"
          f"{'  [PILOT]' if args.pilot else ''}")

    print("\n1. price panels")
    with spread_off():
        p = G.panels(members)
    close = p["close"]
    listed = close.notna()
    n_listed = int(listed.to_numpy().sum())
    print(f"  panel {close.shape[0]:,} sessions x {close.shape[1]:,} symbols, "
          f"{n_listed:,} listed stock-sessions")
    print(f"  first 3 sessions: {[str(t.date()) for t in close.index[:3]]}")

    print("\n2. the nine candidates board_span.py chose from")
    cand = G.build_signals(p)

    print(f"\n3. the board's {len(Z.registry.REGISTRY)} rows, from their caches")
    board = Z.board_signals(close.index, close.columns)

    print("\n4. the Pine, on the same rectangle")
    pine = pine_panels(members, close.index, close.columns)

    print("\n4b. the five pre-registered new rules")
    extra = extra_signals(p)

    panels = {}
    panels.update({f"board:{k}": v for k, v in board.items()})
    panels.update({f"cand:{k}": v for k, v in cand.items()})
    panels.update(pine)
    panels.update({f"new:{k}": v for k, v in extra.items()})
    fired = {k: int((v & listed).to_numpy().sum()) for k, v in panels.items()}
    drop = [k for k, n in fired.items() if n < 100]
    for k in drop:
        print(f"  dropping {k}: only {fired[k]} firings, below 100")
        panels.pop(k)
    print(f"  {len(panels)} panels: "
          f"{sum(1 for k in panels if k.startswith('board:'))} board, "
          f"{sum(1 for k in panels if k.startswith('cand:'))} candidate, "
          f"{sum(1 for k in panels if k.startswith('pine:'))} pine, "
          f"{sum(1 for k in panels if k.startswith('new:'))} new")

    print("\n5. phi over listed cells only")
    m = listed.to_numpy()
    names = list(panels)
    vec = {k: panels[k].fillna(False).to_numpy()[m] for k in names}

    # The fast path is only usable if it agrees with the function board_span.py
    # and entry_zoo.py already report. Two pairs, both directions of the board.
    for a, b in ((names[0], names[1]), (names[0], names[-1])):
        ref, _ = Z.phi_jaccard(panels[a], panels[b], listed)
        mine = phi(vec[a], vec[b])
        if not (np.isfinite(ref) and abs(ref - mine) < 1e-3):
            raise SystemExit(f"phi disagrees with entry_zoo on {a} vs {b}: "
                             f"{mine} against {ref}")
    print("  fast phi agrees with entry_zoo.phi_jaccard on 2 checked pairs")

    board_names = [k for k in names if k.startswith("board:")]
    other = [k for k in names if not k.startswith("board:")]
    rows = []
    for k in other:
        pairs = sorted(((abs(phi(vec[k], vec[bn])), bn) for bn in board_names),
                       reverse=True)
        best, nearest = pairs[0]
        rows.append({"panel": k,
                     "kind": k.split(":")[0].replace("cand", "candidate"),
                     "firings": fired[k],
                     "pct_of_stock_sessions": round(100.0 * fired[k] / n_listed, 4),
                     "max_phi_vs_board": round(float(best), 4),
                     "nearest_board_row": nearest,
                     "second_phi": round(float(pairs[1][0]), 4),
                     "second_row": pairs[1][1],
                     "mean_phi_vs_board": round(
                         float(np.mean([v for v, _ in pairs])), 4)})
    flat = pd.DataFrame(rows).sort_values("max_phi_vs_board")

    # The board's own spread, for scale. Two rows of the SAME entry family differ
    # only in stop width, so their overlap says nothing about variety of ideas --
    # cross-family pairs are the comparable population.
    fam = {bn: bn.split("|")[0] for bn in board_names}
    cross = [abs(phi(vec[a], vec[b]))
             for i, a in enumerate(board_names) for b in board_names[i + 1:]
             if fam[a] != fam[b]]
    same = [abs(phi(vec[a], vec[b]))
            for i, a in enumerate(board_names) for b in board_names[i + 1:]
            if fam[a] == fam[b]]

    print("\n  the board's own overlap, for scale:")
    print(f"    cross-family pairs ({len(cross)}): median "
          f"{np.median(cross):.3f}, max {np.max(cross):.3f}")
    print(f"    same-family pairs  ({len(same)}): median "
          f"{np.median(same):.3f}, max {np.max(same):.3f}")
    print(f"    the slate board_span.py selected had max phi {SLATE_MAX_PHI}")

    print("\n  every panel NOT on the board, by overlap with it "
          "(lower = more distinct):\n")
    print(f"    {'panel':<16}{'firings':>10}{'% sess':>9}{'max phi':>9}"
          f"  nearest board row")
    for r in flat.to_dict("records"):
        print(f"    {r['panel']:<16}{r['firings']:>10,}"
              f"{r['pct_of_stock_sessions']:>9.3f}{r['max_phi_vs_board']:>9.3f}"
              f"  {r['nearest_board_row']}")

    pine_rows = flat[flat["kind"] == "pine"]
    best_pine = pine_rows.iloc[0]
    n_worse = int((flat[flat["kind"] == "candidate"]["max_phi_vs_board"]
                   > best_pine["max_phi_vs_board"]).sum())
    print(f"\n  the Pine's most distinct panel is {best_pine['panel']} at "
          f"max phi {best_pine['max_phi_vs_board']:.3f}")
    print(f"  {n_worse} of the nine candidates overlap the board MORE than it does")
    print(f"  the selected slate's max phi was {SLATE_MAX_PHI}: the Pine is "
          f"{'INSIDE' if best_pine['max_phi_vs_board'] <= SLATE_MAX_PHI else 'OUTSIDE'}"
          f" that bar")

    # ---- step 6: the selection rule itself, not just the number it reads
    # max phi answers "how close is the nearest board row". The criterion that
    # BUILT the board is greedy farthest-point: given the ten families already
    # chosen, the next pick is whichever shelf panel has the largest distance
    # to its nearest chosen panel, d = 1 - max(phi, 0). One panel per board
    # FAMILY (the `own` arm -- both arms share the entry rule, so carrying both
    # would let a family vote twice), plus every shelf panel not on the board.
    chosen = [k for k in board_names if k.endswith("|own")]
    shelf = [k for k in other if k != "cand:rand"                    # designated control
             and not (k.startswith("cand:")
                      and any(f"board:{k[5:]}|" in c + "|" for c in chosen))]
    print("\n6. what the board's own selection rule would add next")
    print(f"   chosen: {len(chosen)} families   shelf: {len(shelf)} panels")
    print(f"   {'pick':>5}  {'panel':<16}{'dist to nearest chosen':>24}"
          f"{'max phi':>9}")
    picks = []
    pool = list(shelf)
    for step in range(1, len(pool) + 1):
        scored = [(min(1.0 - max(phi(vec[q], vec[c]), 0.0) for c in chosen), q)
                  for q in pool]
        d, pick = max(scored)
        picks.append({"step": step, "panel": pick, "min_distance": round(d, 4),
                      "max_phi_to_chosen": round(1.0 - d, 4)})
        print(f"   {step:>5}  {pick:<16}{d:>24.4f}{1.0 - d:>9.4f}")
        chosen.append(pick)
        pool.remove(pick)
    pine_step = next((r["step"] for r in picks if r["panel"].startswith("pine:")),
                     None)
    print(f"\n   the first Pine panel enters at pick {pine_step} of {len(picks)}")

    # ---- figure
    fig, (a, b) = plt.subplots(1, 2, figsize=(14, 6),
                               gridspec_kw={"width_ratios": [1.1, 1]})
    ordered = flat.sort_values("max_phi_vs_board", ascending=False)
    colours = [{"pine": "#c0392b", "new": "#27ae60"}.get(k, "#7f8c8d")
               for k in ordered["kind"]]
    a.barh(range(len(ordered)), ordered["max_phi_vs_board"], color=colours)
    a.set_yticks(range(len(ordered)), ordered["panel"], fontsize=8)
    a.axvline(SLATE_MAX_PHI, color="#2980b9", lw=1.2,
              label=f"slate max phi {SLATE_MAX_PHI}")
    a.set_xlabel("max |phi| against any of the 20 board rows")
    a.set_title("How much each rule already overlaps the board\n"
                "(red = the user's Pine; lower is more distinct)", fontsize=10)
    a.legend(fontsize=8)

    pn = [k for k in names if k.startswith("pine:")]
    M = np.array([[phi(vec[q], vec[bn]) for bn in board_names] for q in pn])
    im = b.imshow(M, cmap="RdBu_r", vmin=-0.15, vmax=0.15, aspect="auto")
    b.set_yticks(range(len(pn)), pn, fontsize=8)
    b.set_xticks(range(len(board_names)), board_names, rotation=90, fontsize=6)
    b.set_title("Pine vs each board row (phi)", fontsize=10)
    fig.colorbar(im, ax=b, fraction=0.045)
    fig.suptitle(f"The Pine on the board's own returns-blind criterion  ({stamp})")
    fig.tight_layout()
    png = OUT / f"pine_span_{stamp}.png"
    fig.savefig(png, dpi=130)
    plt.close(fig)
    print(f"\n  wrote {png.name}")

    csv = OUT / "measurements" / f"pine_span_{stamp}.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    flat.to_csv(csv, index=False)
    print(f"  wrote measurements/{csv.name} "
          f"({len(flat)} rows x {flat.shape[1]} columns)")

    blob = {"stamp": stamp, "pilot": args.pilot, "symbols": len(members),
            "listed_stock_sessions": n_listed,
            "slate_max_phi": SLATE_MAX_PHI,
            "board_cross_family": {"median": round(float(np.median(cross)), 4),
                                   "max": round(float(np.max(cross)), 4),
                                   "pairs": len(cross)},
            "board_same_family": {"median": round(float(np.median(same)), 4),
                                  "max": round(float(np.max(same)), 4),
                                  "pairs": len(same)},
            "panels": flat.to_dict("records"),
            "next_picks": picks, "pine_enters_at_pick": pine_step}
    jpath = OUT / f"pine_span_{stamp}.json"
    jpath.write_text(json.dumps(blob, indent=2))
    print(f"  wrote {jpath.name}")
    print(f"\n  total {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

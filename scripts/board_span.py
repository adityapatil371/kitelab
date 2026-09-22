"""How much genuine VARIETY does the board carry, and which slate carries most?

    python3 -m scripts.board_span --pilot    # 150 symbols, times the loop
    python3 -m scripts.board_span            # the full run

READ THIS FIRST, 2026-09-17 (updated after the rebuild this file caused).
The board is no longer the nine rows the narrative below argues about. It is
TWENTY: ten entry families x two stop widths, built from the slate this script
chose. Six of the nine candidates in step 2 are now board rows, so a re-run
compares e.g. cand:mr against board:mr|own -- near-twins by construction, and
NOT evidence of redundancy. Re-run this to ask "does the board that got built
span the space?", not to re-choose a board.

THE QUESTION (as asked on 2026-09-17, before the rebuild). The dashboard shows
nine strategy labels. scripts/redundancy.py
measured (2026-09-11) that the thirteen before the cut carried only 3-9
independent ideas, and that the surviving nine still leave n_eff at 4.0. The
user's objection, 2026-09-17: the board is "the same thing wearing different
clothes", and the slate I proposed to fix it was picked on PAST RANK -- which
is the criterion scripts/pbo.py measured at 0.412, WORSE THAN A COIN FLIP out
of sample. So rank cannot choose the board. Coverage has to.

WHAT IS PRE-REGISTERED HERE, before any number is looked at:

  * NO RETURNS ARE READ. Not CAGR, not R, not excess-vs-hold, nowhere in this
    file. The only input is WHEN each rule fires and WHEN each exit rule gets
    out. A slate chosen this way cannot be chosen by hindsight, because the
    information needed for hindsight is never loaded.
  * THE SELECTION RULE is maximin-distance (farthest-point) greedy, the
    standard space-filling design criterion: distance d(i,j) = 1 - max(phi,0);
    seed with the most distant PAIR; then repeatedly add whichever candidate's
    CLOSEST already-chosen neighbour is furthest away. Deterministic, no
    tuning, and it is the same algorithm whichever 18 rules you hand it.
  * THE SLATE SIZE is 9, today's board size, so the comparison is cost-neutral:
    same number of cells, same rebuild, different span.
  * THE PREDICTION: the maximin slate's n_eff beats the current board's 4.0 by
    at least 2 ideas, and at least three of item 19's nine candidates displace
    an incumbent. (Written before the run. The last four predictions logged in
    this project were all wrong, which is exactly why they get written down.)

TWO AXES, because a board is a grid and both sides of it can be redundant:

  1. ENTRY SPAN. One boolean panel per board row (today 20, one per
     registry.REGISTRY entry) recovered from its signal cache by
     scripts.entry_zoo.board_signals, plus the nine candidates
     of scripts.entry_exit_grid.build_signals -- compared pairwise by phi over
     LISTED cells only (the rectangle is ~40% empty and counting blanks as
     agreement flatters every pair).
  2. EXIT SPAN. The board has ONE exit convention hard-wired into every
     engine, and item 20 measured the exit carrying 3.5x the entry's share of
     the trade-level spread. Exits are not boolean, so they are compared on
     WHEN they get out: Spearman correlation between the exit-horizon vectors
     that scripts.entry_exit_grid.exits_for_symbol returns, pooled over a
     sample of symbols, for all 8 exits x 3 stop widths.

Reads:  the cleaned parquet candles, via kitelab.frames
        <CLEAN>/signal_cache/*_all.pkl        (one per board row, entry stamps)
Writes: output/measurements/board_span_<date>.csv   (every pair, both axes)
        output/measurements/board_span_<date>.json               (matrices + the slate)
        output/figures/board_span_<date>.png                (heatmaps + the n_eff curve)
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from kitelab import config
import scripts.entry_exit_grid as G
import scripts.entry_zoo as Z
from scripts.redundancy import meff
from scripts.xrank_account import spread_off

OUT = Path(__file__).resolve().parent.parent / "output"
FIG = OUT / "figures"              # every PNG
MEAS = OUT / "measurements"        # every finished CSV or JSON result
FIG.mkdir(parents=True, exist_ok=True)
MEAS.mkdir(parents=True, exist_ok=True)
# Slate size. The selection axis is ENTRIES, and the board carries 10 entry
# families (each registered twice, once per stop arm in registry.STOPS), so 10
# keeps a re-run cost-neutral against what is on the page. Was 9 for the
# 2026-09-17 selection run, when the board was nine single-stop rows.
SLATE_K = 10
# Designated NULL CONTROLS, excluded from the selection pool but kept in the
# matrix as a ruler for what "no relationship" looks like. This exclusion is
# returns-blind: both are labelled controls in the source that defines them,
# before any measurement exists. `cand:rand` fires at the median rate of the
# other eight on a fixed seed (entry_exit_grid.build_signals); board `pair|*`
# rows exist only to control an ATH row (kitelab/registry.py). Leaving them in
# broke the pilot -- maximin distance LOVES a coin flip, because noise is
# orthogonal to everything by construction. A space-filling design must fill a
# space of ideas, and a control carries none.
CONTROLS = ("cand:rand",)
EXIT_SAMPLE = 150        # symbols drawn for the exit-horizon correlations
SEED = 20260917


# --------------------------------------------------------- entry span ----
def phi_matrix(panels: dict, listed) -> tuple[np.ndarray, list]:
    """Pairwise phi over listed cells. Symmetric, unit diagonal."""
    names = list(panels)
    n = len(names)
    C = np.eye(n)
    t0 = time.time()
    for i in range(n):
        for j in range(i + 1, n):
            phi, _ = Z.phi_jaccard(panels[names[i]], panels[names[j]], listed)
            C[i, j] = C[j, i] = 0.0 if not np.isfinite(phi) else phi
        print(f"    row {i + 1:>2} of {n} ({time.time() - t0:.0f}s)", flush=True)
    return C, names


def maximin(C: np.ndarray, names: list, k: int) -> list:
    """Farthest-point greedy on d = 1 - max(phi, 0). See the docstring.

    Deterministic and returns-blind. Ties break on the lower index, which is
    the order the panels were built in, not any ordering by quality.
    """
    D = 1.0 - np.clip(C, 0.0, 1.0)
    np.fill_diagonal(D, 0.0)
    i, j = np.unravel_index(np.argmax(D), D.shape)
    chosen = [int(i), int(j)]
    while len(chosen) < k:
        rest = [x for x in range(len(names)) if x not in chosen]
        best = max(rest, key=lambda x: (min(D[x, y] for y in chosen), -x))
        chosen.append(best)
    return chosen


def span_of(C: np.ndarray, idx: list) -> dict:
    """The three redundancy readings scripts/redundancy.py insists on together."""
    sub = C[np.ix_(idx, idx)]
    lam = np.sort(np.abs(np.linalg.eigvalsh(sub)))[::-1]
    frac = np.cumsum(lam) / lam.sum()
    off = sub[np.triu_indices(len(idx), k=1)]
    return {"k": len(idx), "kaiser": int((lam >= 1.0).sum()),
            "pc90": int(np.searchsorted(frac, 0.90) + 1),
            "li_ji": round(meff(sub), 2),
            "max_phi": round(float(np.max(np.abs(off))), 3),
            "mean_phi": round(float(np.mean(np.abs(off))), 3)}


# ---------------------------------------------------------- exit span ----
def exit_horizons(p, members, rng):
    """{(exit, stop): pooled exit-horizon vector} over a sample of symbols.

    exits_for_symbol returns, for EVERY bar, how many sessions a position
    opened there stays open. Two exit rules are redundant when those numbers
    move together, which is a statement about timing alone -- no price, no
    profit, nothing a hindsight choice could hide in.
    """
    O, H, L, C = (p[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    cols = list(p["close"].columns)
    pick = rng.choice(len(cols), size=min(EXIT_SAMPLE, len(cols)), replace=False)
    acc = {}
    for ci in pick:
        c_full = C[:, ci]
        ok = np.isfinite(c_full)
        if ok.sum() < 400:
            continue
        s, e = int(np.argmax(ok)), int(len(ok) - np.argmax(ok[::-1]))
        o, h, l, c = O[s:e, ci], H[s:e, ci], L[s:e, ci], C[s:e, ci]
        if not np.isfinite(c).all():
            c = np.nan_to_num(c, nan=0.0)
        pc = np.concatenate([[np.nan], c[:-1]])
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
        lines = {"own": l, "atr2": c - 2.0 * atr, "atr3": c - 3.0 * atr}
        for sname, stop_l in lines.items():
            ex = G.exits_for_symbol(o, h, l, c, stop_l, np.random.default_rng(SEED))
            for xname in G.EXITS:
                k_out = ex[xname][0].astype(float)
                acc.setdefault((xname, sname), []).append(k_out)
    return {key: np.concatenate(v) for key, v in acc.items()}


def spearman_matrix(horizons: dict) -> tuple[np.ndarray, list]:
    keys = list(horizons)
    n = len(keys)
    L = min(len(v) for v in horizons.values())
    R = np.eye(n)
    ranks = {}
    for key in keys:
        v = horizons[key][:L]
        ranks[key] = pd.Series(v).rank().to_numpy()
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ranks[keys[i]], ranks[keys[j]]
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() < 100 or a[ok].std() == 0 or b[ok].std() == 0:
                continue
            R[i, j] = R[j, i] = float(np.corrcoef(a[ok], b[ok])[0, 1])
    return R, [f"{x}|{s}" for x, s in keys]


# ------------------------------------------------------------ reporting --
def figure(C, names, board_idx, slate_idx, R, rnames, curve, stamp):
    fig = plt.figure(figsize=(15, 10))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1])

    a = fig.add_subplot(gs[0, 0])
    order = board_idx + [i for i in range(len(names)) if i not in board_idx]
    M = C[np.ix_(order, order)]
    im = a.imshow(M, cmap="RdBu_r", vmin=-0.3, vmax=0.3)
    a.set_xticks(range(len(order)), [names[i] for i in order],
                 rotation=90, fontsize=6)
    a.set_yticks(range(len(order)), [names[i] for i in order], fontsize=6)
    a.axhline(len(board_idx) - 0.5, color="k", lw=1)
    a.axvline(len(board_idx) - 0.5, color="k", lw=1)
    a.set_title("Entry overlap (phi). Box = today's board", fontsize=10)
    fig.colorbar(im, ax=a, fraction=0.045)

    b = fig.add_subplot(gs[0, 1])
    im2 = b.imshow(R, cmap="RdBu_r", vmin=-1, vmax=1)
    b.set_xticks(range(len(rnames)), rnames, rotation=90, fontsize=5)
    b.set_yticks(range(len(rnames)), rnames, fontsize=5)
    b.set_title("Exit-timing overlap (Spearman on horizon)", fontsize=10)
    fig.colorbar(im2, ax=b, fraction=0.045)

    c = fig.add_subplot(gs[1, 0])
    ks = [x["k"] for x in curve]
    c.plot(ks, [x["li_ji"] for x in curve], "o-", label="Li-Ji n_eff")
    c.plot(ks, [x["kaiser"] for x in curve], "s--", label="Kaiser")
    c.plot(ks, ks, color="#bbb", lw=0.8, label="one idea per rule")
    c.set_xlabel("rules on the slate (maximin order)")
    c.set_ylabel("independent ideas")
    c.set_title("How fast does adding a rule stop adding an idea?", fontsize=10)
    c.legend(fontsize=8)

    d = fig.add_subplot(gs[1, 1])
    d.axis("off")
    rows = [("today's board", span_of(C, board_idx)),
            (f"maximin slate (k={len(slate_idx)})", span_of(C, slate_idx))]
    txt = [f"{'slate':<26}{'k':>3}{'Kaiser':>8}{'90%':>6}{'Li-Ji':>8}"
           f"{'maxphi':>8}{'meanphi':>9}"]
    for label, s in rows:
        txt.append(f"{label:<26}{s['k']:>3}{s['kaiser']:>8}{s['pc90']:>6}"
                   f"{s['li_ji']:>8}{s['max_phi']:>8}{s['mean_phi']:>9}")
    txt.append("")
    txt.append("maximin slate, in the order it was chosen:")
    for n_, i in enumerate(slate_idx, 1):
        txt.append(f"  {n_}. {names[i]}")
    d.text(0.0, 1.0, "\n".join(txt), family="monospace", fontsize=8,
           va="top", ha="left")

    fig.suptitle(f"Board span -- entries and exits, no returns read  ({stamp})")
    fig.tight_layout()
    path = FIG / f"board_span_{stamp}.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  wrote {path.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    cfg = config.load()
    members = sorted(cfg.merged)
    if args.pilot:
        members = members[:150]
    print(f"\n  universe: {len(members)} symbols")

    print("\n1. panels")
    with spread_off():
        p = G.panels(members)
    close = p["close"]
    listed = close.notna()
    print(f"  panel {close.shape[0]:,} sessions x {close.shape[1]:,} symbols, "
          f"{int(listed.to_numpy().sum()):,} listed stock-sessions")

    print("\n2. the nine candidate entries (scripts.entry_exit_grid)")
    cand = G.build_signals(p)
    print(f"\n3. the board's {len(Z.registry.REGISTRY)} rows, "
          "from their signal caches")
    board = Z.board_signals(close.index, close.columns)

    panels = {f"cand:{k}": v for k, v in cand.items()}
    panels.update({f"board:{k}": v for k, v in board.items()})
    fired = {k: int(v.to_numpy().sum()) for k, v in panels.items()}
    drop = [k for k, n in fired.items() if n < 100]
    for k in drop:
        print(f"  dropping {k}: only {fired[k]} firings, below 100")
        panels.pop(k)
    print(f"  {len(panels)} panels compared "
          f"({sum(1 for k in panels if k.startswith('board:'))} board, "
          f"{sum(1 for k in panels if k.startswith('cand:'))} candidate)")

    print("\n4. entry overlap, phi over listed cells only")
    C, names = phi_matrix(panels, listed)
    board_idx = [i for i, n in enumerate(names) if n.startswith("board:")]
    pool = [i for i, n in enumerate(names)
            if n not in CONTROLS and not n.startswith("board:pair|")]
    print(f"  selection pool: {len(pool)} of {len(names)} "
          f"(dropped {len(names) - len(pool)} designated controls)")
    sub = maximin(C[np.ix_(pool, pool)], [names[i] for i in pool],
                  min(SLATE_K, len(pool)))
    slate_idx = [pool[i] for i in sub]

    print("\n  the ten most overlapping ENTRY pairs (phi):")
    pairs = sorted(((abs(C[i, j]), names[i], names[j])
                    for i in range(len(names)) for j in range(i + 1, len(names))),
                   reverse=True)[:10]
    for v, a, b in pairs:
        print(f"    {v:5.3f}  {a:<18} {b}")

    print("\n5. exit-timing overlap, on a sample of symbols")
    horizons = exit_horizons(p, members, np.random.default_rng(SEED))
    R, rnames = spearman_matrix(horizons)
    off = R[np.triu_indices(len(rnames), k=1)]
    print(f"  {len(rnames)} exit x stop combinations, "
          f"{len(horizons[list(horizons)[0]]):,} pooled bars")
    print(f"  median |Spearman| between exit rules: {np.median(np.abs(off)):.3f}"
          f"   max {np.max(np.abs(off)):.3f}")
    print("  the ten most overlapping EXIT pairs (Spearman on horizon):")
    xp = sorted(((abs(R[i, j]), rnames[i], rnames[j])
                 for i in range(len(rnames)) for j in range(i + 1, len(rnames))),
                reverse=True)[:10]
    for v, a, b in xp:
        print(f"    {v:5.3f}  {a:<14} {b}")
    lam = np.sort(np.abs(np.linalg.eigvalsh(R)))[::-1]
    print(f"  exit axis carries {int((lam >= 1.0).sum())} Kaiser ideas / "
          f"{meff(R):.2f} Li-Ji, out of {len(rnames)} labels")

    # Per stop width. The board hard-wires stop = the entry candle's own low,
    # so `own` is the only column the dashboard actually occupies. If the exit
    # rules collapse onto each other there, the board's exit axis is not a
    # choice at all -- the stop has already ended the trade.
    print("\n  exit ideas WITHIN one stop width (8 labels each):")
    print(f"    {'stop':<6}{'Kaiser':>8}{'Li-Ji':>8}{'med|rho|':>10}"
          f"{'med horizon':>13}{'ends day 1':>12}")
    for sname in ("own", "atr2", "atr3"):
        idx = [i for i, n in enumerate(rnames) if n.endswith("|" + sname)]
        sub = R[np.ix_(idx, idx)]
        o = np.abs(sub[np.triu_indices(len(idx), k=1)])
        pooled = np.concatenate([horizons[(x, sname)] for x in G.EXITS])
        pooled = pooled[np.isfinite(pooled)]
        e = np.sort(np.abs(np.linalg.eigvalsh(sub)))[::-1]
        print(f"    {sname:<6}{int((e >= 1.0).sum()):>8}{meff(sub):>8.2f}"
              f"{np.median(o):>10.3f}{np.median(pooled):>11.0f}d"
              f"{(pooled <= 1).mean() * 100:>11.1f}%")

    print("\n6. VERDICT -- independent ideas, same slate size")
    curve = [span_of(C, slate_idx[:k]) for k in range(2, len(slate_idx) + 1)]
    rows = [{"slate": "today's board", **span_of(C, board_idx)},
            {"slate": "maximin", **span_of(C, slate_idx)},
            {"slate": "all candidates+board", **span_of(C, list(range(len(names))))}]
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n  maximin slate, in the order chosen (no returns were read):")
    kept = [n for n in (names[i] for i in slate_idx) if n.startswith("board:")]
    for n_, i in enumerate(slate_idx, 1):
        print(f"    {n_}. {names[i]}")
    print(f"\n  incumbents retained: {len(kept)} of {len(board_idx)}   "
          f"displaced by candidates: {len(slate_idx) - len(kept)}")

    stamp = date.today().isoformat()
    OUT.joinpath("measurements").mkdir(parents=True, exist_ok=True)
    flat = [{"axis": "entry", "a": names[i], "b": names[j], "value": round(C[i, j], 4)}
            for i in range(len(names)) for j in range(i + 1, len(names))]
    flat += [{"axis": "exit", "a": rnames[i], "b": rnames[j],
              "value": round(R[i, j], 4)}
             for i in range(len(rnames)) for j in range(i + 1, len(rnames))]
    df = pd.DataFrame(flat)
    cpath = OUT / "measurements" / f"board_span_{stamp}.csv"
    df.to_csv(cpath, index=False)
    print(f"\n  wrote measurements/{cpath.name} "
          f"({len(df)} rows x {len(df.columns)} columns)")
    jpath = MEAS / f"board_span_{stamp}.json"
    jpath.write_text(json.dumps(
        {"generated": stamp, "names": names, "entry_phi": C.tolist(),
         "exit_names": rnames, "exit_spearman": R.tolist(),
         "board_idx": board_idx, "slate": [names[i] for i in slate_idx],
         "spans": rows, "curve": curve}, indent=1, default=str))
    print(f"  wrote {jpath.name}")
    figure(C, names, board_idx, slate_idx, R, rnames, curve, stamp)
    print(f"\n  total {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

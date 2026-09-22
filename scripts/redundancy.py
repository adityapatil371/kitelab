"""How many independent rules does the board actually contain?

    python3 -m scripts.redundancy                  # the live board
    python3 -m scripts.redundancy <board.json>     # any saved payload

THE QUESTION. The board lists 13 strategies, ranks them, and reports a best.
That is only 13 chances to be lucky if the 13 are 13 different ideas. The
2026-09-11 re-rank already noticed the problem in passing -- `pair|MW`,
`pair|QW` and `qmw|0` correlate 0.936-0.963 and rank 1st, 4th and 3rd -- but
it measured pairs, and pairs cannot answer "how many ideas are here".

WHY THE NAIVE CORRELATION OVERSTATES IT. All 13 rules are scored on the same
300 scenarios, and some scenarios are hard for everything: the cross-strategy
mean excess-vs-hold ranges -19.08 to +3.59 CAGR points/yr (sd 4.34, measured
2026-09-11). Two rules both losing in the same hard scenario is a fact about
the scenario, not about the rules. So every correlation is computed TWICE:
raw, and after subtracting each scenario's cross-strategy mean, which removes
the shared difficulty and leaves rule-vs-rule similarity.

RESULT 2026-09-11, on the 13-strategy board built 17:50 IST: the duplication
survives the stricter test. 0.963 -> 0.922, 0.961 -> 0.921, 0.936 -> 0.863.
It is real, not an artefact of shared scenarios.

WHY THE DEMEANED NEGATIVES ARE NOT DIVERSIFICATION. Centring each row forces
the average off-diagonal correlation to about -1/(k-1) = -0.083 by
construction; the observed mean is -0.081, i.e. essentially all of it. Only a
correlation well ABOVE that floor is evidence of anything. Centring also drops
the matrix to rank k-1, so its spectrum is not independent evidence either --
see HOW FIRM IS THE COUNT below, which exists to stop the Li-Ji number being
quoted as if it were precise.

WHAT THIS DOES NOT CHANGE. The gate. Benjamini-Hochberg is valid under
positive dependence, and the "12 cells reach p<=0.05 against ~195 expected by
chance" line is unaffected by correlation -- correlation changes that count's
variance, not its expectation. Redundancy changes the evidential weight of the
LEADERBOARD, not the verdict.

Not imported by the build; reads a finished payload and writes nothing the
build reads. Nothing here is in signals._SUPPORT or signals._ACCOUNT, so
running or editing it costs no rebuild.

Reads:  CLEAN/dashboard.json (the daily_excess block; the diagnostics leg must
        have run) and kitelab.registry for the labels.
Writes: output/redundancy_<built date>.png -- the two heatmaps, side by side.
Prints: cluster membership, the spectrum, and the nearest-twin table.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")          # no display in the container; PNG only
import matplotlib.pyplot as plt
from scipy.cluster.hierarchy import linkage, fcluster, dendrogram
from scipy.spatial.distance import squareform

from kitelab import registry
from kitelab.config import CLEAN

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
FIG = os.path.join(OUT, "figures")       # every PNG
os.makedirs(FIG, exist_ok=True)


def load(path):
    """The payload, its daily-excess block, and the strategy labels."""
    d = json.load(open(path))
    de = d.get("daily_excess") or {}
    if not de:
        raise SystemExit(
            f"{path} carries no daily_excess block -- the diagnostics leg has "
            "not run. python3 -m scripts.wf_attach && "
            "python3 -m scripts.attach_diagnostics")
    print(f"\nboard: {path}")
    print(f"built {d['built']}: {len(d['grid']):,} grid cells, "
          f"{len(de):,} carrying the daily-excess test")

    label = {}
    for s in registry.REGISTRY:
        v = s.variant
        v = f"{v:g}" if isinstance(v, float) else str(v)
        label[f"{s.key}|{v}"] = s.label
    print(f"{len(label)} registered strategies")
    return d, de, label


def matrix(de, label):
    """Reshape the flat cell dict into scenario x strategy, or refuse to."""
    by_cell: dict[str, dict[str, float]] = {}
    for key, v in de.items():
        parts = key.split("|")
        st, scen = "|".join(parts[:2]), "|".join(parts[2:])
        if st not in label:
            raise SystemExit(f"cell names a strategy not in the registry: {st}")
        e = v["excess_pts"]
        if e is None:
            # daily_excess entries carry None; statistics.median raises on them
            # and numpy would propagate NaN through every correlation below.
            raise SystemExit(f"excess_pts is None for {key}")
        by_cell.setdefault(scen, {})[st] = float(e)

    names, scens = list(label), sorted(by_cell)
    print(f"\nreshaped to {len(scens)} scenarios x {len(names)} strategies")

    # A ragged board would silently compare different cell mixes per pair,
    # which is exactly the bias this script exists to remove.
    missing = [(s, n) for s in scens for n in names if n not in by_cell[s]]
    if missing:
        raise SystemExit(f"ragged board: {len(missing)} strategy/scenario "
                         f"gaps, first {missing[:3]}")

    X = np.array([[by_cell[s][n] for n in names] for s in scens], dtype=float)
    print(f"matrix X: {X.shape[0]} rows (scenarios), "
          f"{X.shape[1]} columns (strategies)")
    print(f"no NaN: {not np.isnan(X).any()}; "
          f"range {X.min():.2f} to {X.max():.2f} CAGR pts")
    print("\nfirst 3 rows (scenario, then excess vs hold, first 4 strategies):")
    for s, row in zip(scens[:3], X[:3]):
        cells = "  ".join(f"{n}={x:7.2f}" for n, x in zip(names[:4], row[:4]))
        print(f"  {s:<44} {cells}")
    return X, names, scens


def meff(C):
    """Li & Ji (2005) effective number of independent tests, from eigenvalues.

    Coarse on purpose-baited data: it reduces to (#eigenvalues >= 1) plus
    (k - sum of their integer parts), so it moves in steps of roughly one.
    Never quote it alone -- report it beside Kaiser and the 90% count.
    """
    lam = np.abs(np.linalg.eigvalsh(C))
    return float(sum((l >= 1.0) + (l - np.floor(l)) for l in lam))


def clusters(C, cut):
    """Average-linkage grouping at correlation `cut`, on distance 1 - r."""
    D = np.clip(1.0 - C, 0.0, 2.0)
    np.fill_diagonal(D, 0.0)
    Z = linkage(squareform(D, checks=False), method="average")
    return fcluster(Z, t=1.0 - cut, criterion="distance"), Z


def figure(C_raw, C_dem, names, scens, built):
    order = dendrogram(clusters(C_dem, 0.95)[1], no_plot=True)["leaves"]
    fig, axes = plt.subplots(1, 2, figsize=(15, 7))
    for ax, (tag, C) in zip(axes, [("Raw", C_raw),
                                   ("Scenario-demeaned", C_dem)]):
        M = C[np.ix_(order, order)]
        im = ax.imshow(M, vmin=-1, vmax=1, cmap="RdBu_r")
        lb = [names[i] for i in order]
        ax.set_xticks(range(len(lb)))
        ax.set_xticklabels(lb, rotation=90, fontsize=8)
        ax.set_yticks(range(len(lb)))
        ax.set_yticklabels(lb, fontsize=8)
        ax.set_title(f"{tag} correlation of excess-vs-hold\n"
                     f"across {len(scens)} shared scenarios", fontsize=10)
        for i in range(len(lb)):
            for j in range(len(lb)):
                if i != j and abs(M[i, j]) > 0.80:
                    ax.text(j, i, f"{M[i, j]:.2f}", ha="center", va="center",
                            fontsize=6, color="white")
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle(f"kitelab board, built {built} -- how many rules are "
                 f"really here?", fontsize=11)
    fig.tight_layout()
    png = os.path.join(FIG, f"redundancy_{built.split()[0]}.png")
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else str(CLEAN / "dashboard.json")
    d, de, label = load(path)
    X, names, scens = matrix(de, label)
    k = len(names)

    common = X.mean(axis=1)
    Xd = X - common[:, None]
    print(f"\nper-scenario common factor: mean excess ranges "
          f"{common.min():.2f} to {common.max():.2f} CAGR pts "
          f"(sd {common.std():.2f})")

    C_raw = np.clip(np.corrcoef(X, rowvar=False), -1.0, 1.0)
    C_dem = np.clip(np.corrcoef(Xd, rowvar=False), -1.0, 1.0)

    for tag, C in [("RAW", C_raw), ("DEMEANED", C_dem)]:
        print(f"\n{'=' * 70}\n{tag} correlation\n{'=' * 70}")
        off = C[np.triu_indices_from(C, k=1)]
        print(f"  {len(off)} pairs: median r {np.median(off):.3f}, "
              f"max {off.max():.3f}, min {off.min():.3f}")
        print(f"  pairs above 0.95: {(off > 0.95).sum()};  "
              f"above 0.80: {(off > 0.80).sum()}")
        print(f"  effective independent tests (Li-Ji): {meff(C):.2f} of {k}")
        for cut in (0.95, 0.90, 0.80):
            lab_, _ = clusters(C, cut)
            print(f"\n  cut at r > {cut}: {lab_.max()} groups")
            for g in range(1, lab_.max() + 1):
                mem = [names[i] for i in range(k) if lab_[i] == g]
                if len(mem) > 1:
                    idx = [names.index(m) for m in mem]
                    sub = C[np.ix_(idx, idx)]
                    lo = sub[np.triu_indices_from(sub, k=1)].min()
                    print(f"    [{len(mem)}] {', '.join(mem):<48} "
                          f"weakest r {lo:.3f}")
                else:
                    print(f"    [1] {mem[0]}")

    print(f"\n{'=' * 70}\nWHAT THE DEMEANING COSTS\n{'=' * 70}")
    off_d = C_dem[np.triu_indices_from(C_dem, k=1)]
    print(f"  row-centering {k} columns forces mean off-diagonal r to about "
          f"-1/(k-1) = {-1 / (k - 1):.3f}")
    print(f"  observed mean demeaned r: {off_d.mean():.3f}")
    print("  => the negatives are arithmetic, not diversification. Only r well")
    print("     ABOVE that floor is a real duplicate.")

    print(f"\n{'=' * 70}\nEIGENVALUE SPECTRUM (raw correlation matrix)\n{'=' * 70}")
    lam = np.linalg.eigvalsh(C_raw)[::-1]
    cum = np.cumsum(lam) / k * 100
    print(f"  {'PC':>4}{'eigenvalue':>13}{'% var':>9}{'cum %':>9}")
    for i, (l, c) in enumerate(zip(lam, cum), 1):
        print(f"  {i:>4}{l:>13.3f}{100 * l / k:>9.1f}{c:>9.1f}")
    n90 = int(np.searchsorted(cum, 90.0) + 1)
    print(f"\n  PC1 alone explains {100 * lam[0] / k:.1f}% -- the common "
          f"'this scenario was hard for everything' factor.")
    print(f"  {n90} components are needed to reach 90% of the variance.")
    print(f"  eigenvalues above 1 (Kaiser): {(lam > 1).sum()}")

    pc1 = np.linalg.eigh(C_raw)[1][:, -1]
    if pc1.mean() < 0:
        pc1 = -pc1
    print("\n  PC1 loading per strategy (all same sign => a true common factor):")
    for n, l in sorted(zip(names, pc1), key=lambda t: -t[1]):
        print(f"    {n:<14}{l:>7.3f}   {label[n]}")

    print(f"\n{'=' * 70}\nNEAREST TWIN PER RULE (demeaned; floor is "
          f"{-1 / (k - 1):.3f})\n{'=' * 70}")
    print(f"  {'strategy':<14}{'max r':>8}  {'twin':<14}{'PC1 load':>10}  label")
    rows = []
    for i, n in enumerate(names):
        r = C_dem[i].copy()
        r[i] = -np.inf
        j = int(np.argmax(r))
        rows.append((float(r[j]), n, names[j], float(pc1[i])))
    for r, n, tw, l in sorted(rows, reverse=True):
        print(f"  {n:<14}{r:>8.3f}  {tw:<14}{l:>10.3f}  {label[n]}")
    print("\n  Bottom of this list = the rules the board would actually lose")
    print("  information by dropping.")

    print(f"\n{'=' * 70}\nHOW FIRM IS THE COUNT?\n{'=' * 70}")
    for tag, C in [("raw", C_raw), ("demeaned", C_dem)]:
        lam_ = np.linalg.eigvalsh(C)[::-1]
        print(f"  {tag:<10} rank {np.linalg.matrix_rank(C):>3}  "
              f"smallest eigenvalue {lam_[-1]:.2e}  "
              f"Li-Ji = {(lam_ >= 1).sum()} + ({k} - "
              f"{np.floor(lam_).sum():.0f}) = {meff(C):.2f}")
    print("  The demeaned matrix is rank-deficient BY CONSTRUCTION (row-centring).")
    print("  Its matching Li-Ji is a coincidence of the formula, not confirmation.")
    print("\n  The honest count is a RANGE, not a number:")
    print(f"    Kaiser (eigenvalue > 1)          {(lam > 1).sum()} of {k}")
    print(f"    components to reach 90% variance {n90} of {k}")
    print(f"    Li-Ji effective tests            {meff(C_raw):.0f} of {k}")
    print("  Whatever the criterion, the board holds fewer ideas than labels.\n")

    figure(C_raw, C_dem, names, scens, d["built"])


if __name__ == "__main__":
    main()

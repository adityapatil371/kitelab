"""When is the best time to sell? A holding-period sweep across every rule.

    python3 -m scripts.exit_sweep

scripts/entry_edge.py compared each rule's own exit against two fixed holds (60
and 250 sessions) and found the rule's exit ahead of both once the comparison
was put on a per-session footing. Two horizons is not a curve, and "60 beats
250" does not locate an optimum. This sweeps ten horizons, so the shape is
visible: whether return per session in the market falls away monotonically with
holding time, peaks somewhere, or is simply flat and the exit rule is noise.

READ THE COLUMNS ONLY AFTER NORMALISING, WHICH IS WHY EVERY FIGURE HERE IS
PER-SESSION-ANNUALISED. The raw table reads backwards: the daily families hold a
MEDIAN OF 3 SESSIONS, so any fixed 250-session hold "wins" on a gross return
column purely by containing more calendar. Annualising by mean sessions held is
what makes a 3-session rule and a 200-session rule comparable at all.

THE TOLL IS THE REASON SHORT HORIZONS LOSE, and it is charged here. A 0.222%
statutory round trip is 0.222% whether the position was held 3 sessions or 250;
paid 83 times a year it is -6.7%/yr, paid once it is -0.2%/yr. That single term
is most of the shape of the curve below, so it is broken out rather than netted
silently.

WHAT THIS IS NOT: a search for the best horizon to adopt. Ten horizons x 19
rules is 190 numbers, and the best of 190 is a lucky number, not a finding --
the same multiple-testing problem the board's Benjamini-Hochberg bar exists to
handle. Read the SHAPE, not the argmax.

Reads:  every registered rule's *_all.pkl signal cache and the daily
        parquet candles. The rule count comes from registry.REGISTRY.
Writes: output/measurements/exit_sweep_<N>strat_<date>.csv
        output/exit_sweep_curve.png
"""
from __future__ import annotations

import csv
import math
import os

import numpy as np

import matplotlib
matplotlib.use("Agg")            # no display in the container; PNG only
import matplotlib.pyplot as plt  # noqa: E402

from kitelab import config  # noqa: E402
from scripts.entry_edge import (close_matrix, forward, hold_index,  # noqa: E402
                                load_entries, per_trade_sessions,
                                N_RULES, STAMP, TOLL, YEAR)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
# Same stamp as entry_edge: the file names the board it describes.
CSV_PATH = os.path.join(OUT, "measurements", f"exit_sweep_{STAMP}.csv")
CURVE = os.path.join(OUT, "exit_sweep_curve.png")

HOLDS = (3, 5, 10, 20, 40, 60, 90, 120, 180, 250)


def net_rate(logret, sessions):
    """Annualised return per session in market, AFTER the statutory round trip.

    The toll is charged once per trade, so its annual bite is (YEAR/sessions) x
    TOLL -- the whole reason a 3-session horizon and a 250-session one are not
    comparable gross.
    """
    if sessions <= 0:
        return float("nan")
    return math.expm1(YEAR * (float(np.mean(logret)) - TOLL) / sessions)


def main() -> None:
    cfg = config.load()
    universe = cfg.merged
    print(f"\n  {len(universe)} stocks.\n")

    index, symbols, filled, traded, last_row = close_matrix(universe)
    col_of = {s: j for j, s in enumerate(symbols)}
    print()
    entries = load_entries(universe, index, col_of)
    hold = hold_index(filled, traded)

    yrs = len(index) / YEAR
    mkt = (hold[-1] / hold[0]) ** (1 / yrs) - 1
    print(f"\n  equal-weight hold: {100 * mkt:.2f}% a year over {yrs:.1f} years")
    print("  Every column below is % a year PER SESSION IN THE MARKET, net of the")
    print("  0.222% round trip. 'own' is the rule's own exit signal.\n")

    hdr = ("  {:<34}{:>7}{:>7}".format("rule", "sess", "own")
           + "".join(f"{h:>7}" for h in HOLDS))
    print(hdr); print("  " + "-" * (len(hdr) - 2))

    rows, curves = [], {}
    for strat, (r, c, g, e) in entries.items():
        if r.size == 0:
            continue
        s_mean, _tot, lg = per_trade_sessions(r, e, g)
        own = net_rate(lg, s_mean)
        line = {"rule": strat.label, "mean_sessions_held": round(s_mean, 2),
                "own_exit_pct_yr": round(100 * own, 3)}
        vals = []
        for h in HOLDS:
            f = forward(filled, last_row, r, c, h)
            v = net_rate(np.log1p(np.clip(f, -0.999, None)), float(h))
            vals.append(v)
            line[f"hold_{h}_pct_yr"] = round(100 * v, 3)
        curves[strat.label] = vals
        rows.append(line)
        print(f"  {strat.label:<34}{s_mean:>7.1f}{100 * own:>7.1f}"
              + "".join(f"{100 * v:>7.1f}" for v in vals))

    grid = np.array([curves[k] for k in curves])
    owns = np.array([r["own_exit_pct_yr"] for r in rows])
    print("  " + "-" * (len(hdr) - 2))
    print(f"  {f'MEDIAN OF THE {N_RULES} RULES':<34}{'':>7}{np.median(owns):>7.1f}"
          + "".join(f"{100 * np.median(grid[:, k]):>7.1f}"
                    for k in range(len(HOLDS))))
    print(f"\n  equal-weight hold, same units: {100 * mkt:>5.1f}")
    best = HOLDS[int(np.argmax(np.median(grid, axis=0)))]
    beat = int((owns > 100 * np.median(grid, axis=0).max()).sum())
    print(f"\n  Best fixed horizon by the MEDIAN rule: {best} sessions. The rule's")
    print(f"  own exit still beats that median-best fixed hold for {beat} of "
          f"{len(rows)} rules.")
    print("  Read the shape, not the argmax: the best of 190 numbers is lucky.")
    print("  Rates are WHILE INVESTED and assume the next trade starts the day")
    print("  this one ends -- upper bounds, applied to every column alike.\n")

    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    for k in curves:
        ax.plot(HOLDS, [100 * v for v in curves[k]], color="#bbb", lw=0.8)
    ax.plot(HOLDS, [100 * np.median(grid[:, k]) for k in range(len(HOLDS))],
            marker="o", color="#1f77b4", lw=2, label=f"median of the {N_RULES} rules")
    ax.axhline(np.median(owns), color="#d62728", ls="--", lw=1.5,
               label="median rule's OWN exit")
    ax.axhline(100 * mkt, color="#2ca02c", ls=":", lw=1.5,
               label="equal-weight buy & hold")
    ax.set_xlabel("sessions held before selling")
    ax.set_ylabel("% a year, per session in the market, net of the toll")
    ax.set_title("When to sell: fixed holding periods vs the rule's own exit")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CURVE, dpi=140)
    print(f"  wrote {CSV_PATH}, {CURVE}\n")


if __name__ == "__main__":
    main()

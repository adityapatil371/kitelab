"""The three figures for scripts/build_n500_report.py. Reads the measurement
CSVs, writes PNGs.

Palette and conventions are scripts/report_figures.py's, deliberately: the two
documents will sit side by side on the same desk, and a reader who has learned
that blue means "ahead" in one should not have to relearn it in the other.

Light surface only: this is print.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e3e2de"
TODAY_C = "#2a78d6"    # categorical slot 1 -- today's membership list
PIT_C = "#eb6834"      # categorical slot 2 -- the point-in-time list
TRI_C = "#8a8985"      # the real index: neutral, it is the reference
NEG = "#d03b3b"


def _frame(ax, xlabel="", ylabel=""):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=3, color=GRID)
    if xlabel:
        ax.set_xlabel(xlabel, color=INK2, fontsize=8.5)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=8.5)


def _save(fig, path):
    fig.patch.set_facecolor(SURFACE)
    fig.savefig(path, dpi=200, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"    wrote {path}")


# --------------------------------------------------------------- figure 1 --
def fig_hold_ladder(hold, path):
    """Three baskets, three start years. `hold` is {year: {basket: cagr}}.

    Grouped bars rather than lines: there are only three start years and they
    are not a time series -- 2012 is not "before" 2018 in any sense a reader
    should be invited to interpolate across.
    """
    years = sorted(hold)
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    _frame(ax, ylabel="annual return, %")
    w = 0.26
    series = [("today", TODAY_C, "Today's Nifty 500 list"),
              ("pit", PIT_C, "A list you could have built that year"),
              ("tri", TRI_C, "The real Nifty 500 index")]
    for i, (k, c, lab) in enumerate(series):
        xs = [j + (i - 1) * w for j in range(len(years))]
        vs = [hold[y][k] for y in years]
        ax.bar(xs, vs, width=w, color=c, label=lab, zorder=3)
        for x, v in zip(xs, vs):
            ax.text(x, v + 0.35, f"{v:.1f}", ha="center", va="bottom",
                    fontsize=7.6, color=INK)
    for j, y in enumerate(years):
        gap = hold[y]["today"] - hold[y]["pit"]
        top = max(hold[y]["today"], hold[y]["pit"])
        ax.annotate("", xy=(j - w, top + 2.6), xytext=(j, top + 2.6),
                    arrowprops=dict(arrowstyle="<->", color=INK2, lw=0.9))
        ax.text(j - w / 2, top + 3.0, f"+{gap:.2f}", ha="center", va="bottom",
                fontsize=8.2, color=INK, fontweight="bold")
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([f"held from {y}" for y in years], color=INK2, fontsize=8.5)
    ax.set_ylim(0, max(hold[y]["today"] for y in years) + 4.5)
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    # Below the axes, not inside it: the premium arrow sits above the tallest
    # pair in every group, so there is no free corner in the plot area.
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.10), labelcolor=INK2)
    _save(fig, path)


# --------------------------------------------------------------- figure 2 --
def fig_rule_dumbbell(rows, hold_today, hold_pit, path):
    """Each rule twice: on the point-in-time list, and on today's list.

    A dumbbell, because the quantity of interest is the GAP between the two
    dots for one rule, and a reader should read it without doing arithmetic.
    """
    rows = sorted(rows, key=lambda r: r["premium"])
    fig, ax = plt.subplots(figsize=(7.2, 7.4))
    _frame(ax, xlabel="annual return, %")
    for i, r in enumerate(rows):
        a, b = r["pit_cagr"], r["today_cagr"]
        ax.plot([a, b], [i, i], color=NEG if b < a else GRID,
                lw=1.6, zorder=2, solid_capstyle="round")
        ax.plot([a], [i], "o", color=PIT_C, markersize=5.2, zorder=3)
        ax.plot([b], [i], "o", color=TODAY_C, markersize=5.2, zorder=3)
    ax.axvline(0, color=INK2, lw=0.8, zorder=1)
    ax.axvline(hold_pit, color=PIT_C, lw=1.0, ls=":", zorder=1)
    ax.axvline(hold_today, color=TODAY_C, lw=1.0, ls=":", zorder=1)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["strategy"] for r in rows], fontsize=7.4, color=INK2,
                       fontfamily="monospace")
    ax.set_ylim(-1, len(rows))
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    handles = [
        plt.Line2D([], [], marker="o", ls="", color=PIT_C, markersize=6,
                   label="on a list you could have built that year"),
        plt.Line2D([], [], marker="o", ls="", color=TODAY_C, markersize=6,
                   label="on today's Nifty 500 list"),
        plt.Line2D([], [], ls=":", color=TODAY_C,
                   label="buy-and-hold, today's list"),
        plt.Line2D([], [], ls=":", color=PIT_C,
                   label="buy-and-hold, that year's list"),
    ]
    # Below the axes: the dots run the full width at every height, so any
    # in-plot corner sits on top of four or five rules.
    ax.legend(handles=handles, frameon=False, fontsize=7.8, ncol=2,
              loc="upper center", bbox_to_anchor=(0.5, -0.072),
              labelcolor=INK2)
    _save(fig, path)


# --------------------------------------------------------------- figure 3 --
def fig_overlap(overlap, n_today, path):
    """How much of today's list a trader that year could have owned.

    `overlap` is {year: n_shared}. One stacked bar per start year: the shared
    part, and the part that was not in that year's top five hundred at all.
    """
    years = sorted(overlap)
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    _frame(ax, xlabel="names of today's list")
    for i, y in enumerate(years):
        shared = overlap[y]
        rest = n_today - shared
        ax.barh(i, shared, color=TODAY_C, height=0.55, zorder=3)
        ax.barh(i, rest, left=shared, color=GRID, height=0.55, zorder=3)
        ax.text(shared / 2, i, f"{shared} were already there",
                ha="center", va="center", fontsize=7.8, color="white")
        ax.text(shared + rest / 2, i, f"{rest} were not",
                ha="center", va="center", fontsize=7.8, color=INK2)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels([f"in {y}" for y in years], color=INK2, fontsize=8.5)
    ax.set_xlim(0, n_today)
    ax.invert_yaxis()
    _save(fig, path)

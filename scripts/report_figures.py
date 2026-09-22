"""The four figures for scripts/build_report.py. Reads the payload, writes PNGs.

Palette is the data-viz reference instance, validated 2026-09-22 with
scripts/validate_palette.js: categorical blue #2a78d6 (the tight stop) and
orange #eb6834 (the wide stop) pass all six checks on a light surface. The
excess histogram is a DIVERGING encoding -- blue and red poles about a neutral
grey midpoint at zero -- because its variable has a meaningful zero (level with
buy-and-hold) and a sign that is the whole point.

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
TIGHT = "#2a78d6"      # categorical slot 1 -- the entry bar's own low
WIDE = "#eb6834"       # categorical slot 2 -- 3 x ATR(14)
POS = "#2a78d6"        # diverging: ahead of hold
NEG = "#d03b3b"        # diverging: behind hold
MID = "#8a8985"

VARIANT_COLOR = {"own": TIGHT, "atr3": WIDE}
VARIANT_NAME = {"own": "Stop at the entry bar's own low",
                "atr3": "Stop 3 x ATR(14) below entry"}


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
def fig_excess_histogram(excess, path):
    """Where every tested scenario landed against buy-and-hold.

    One bar per bucket, coloured by sign. Diverging, because zero means
    'exactly level with holding' and which side of it you are on is the
    question.
    """
    fig, ax = plt.subplots(figsize=(7.2, 3.1))
    _frame(ax, "Rule's return minus buy-and-hold's, percentage points per year",
           "Scenarios")
    n, bins, patches = ax.hist(excess, bins=60, edgecolor=SURFACE, linewidth=0.4)
    for count, left, patch in zip(n, bins[:-1], patches):
        patch.set_facecolor(POS if left >= 0 else NEG)
    ax.axvline(0, color=MID, linewidth=1.6, zorder=5)
    ahead = sum(1 for e in excess if e > 0)
    ax.annotate("level with holding",
                xy=(0, max(n) * 0.97), xytext=(3.0, max(n) * 0.97),
                color=INK2, fontsize=8, va="center",
                arrowprops=dict(arrowstyle="-", color=MID, linewidth=0.9))
    ax.annotate(f"{ahead:,} of {len(excess):,} scenarios\nland on this side",
                xy=(0.80, 0.56), xycoords="axes fraction", color=POS,
                fontsize=8.5, ha="center", weight="bold")
    ax.annotate(f"{len(excess) - ahead:,} land here",
                xy=(0.20, 0.72), xycoords="axes fraction", color=NEG,
                fontsize=8.5, ha="center", weight="bold")
    _save(fig, path)


# --------------------------------------------------------------- figure 2 --
def fig_rule_dotplot(rows, path):
    """Every rule's median result against hold, both stop widths, one line each.

    A dot plot, not a bar chart: the quantity is a difference that can sit on
    either side of zero, and bars from a non-zero baseline mislead.
    """
    order = sorted(rows, key=lambda r: r["median_excess"])
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    _frame(ax, "Median result minus buy-and-hold, percentage points per year", "")
    ys = range(len(order))
    for y, r in zip(ys, order):
        ax.plot([r["median_excess"], 0], [y, y], color=GRID, linewidth=1.0,
                zorder=1)
        ax.plot(r["median_excess"], y, "o", markersize=6.5,
                color=VARIANT_COLOR[r["variant"]],
                markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
    ax.axvline(0, color=MID, linewidth=1.6, zorder=2)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([r["short"] for r in order], fontsize=7.6, color=INK)
    ax.set_ylim(-0.8, len(order) - 0.2)
    ax.text(0.4, len(order) - 0.9, "buy-and-hold", color=INK2, fontsize=8,
            rotation=90, va="top")
    handles = [plt.Line2D([], [], marker="o", linestyle="", markersize=6.5,
                          color=VARIANT_COLOR[v], label=VARIANT_NAME[v])
               for v in ("own", "atr3")]
    # upper left: the sorted dots crowd the right edge at the top, so this is
    # the only corner of either chart with no marks under it.
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=8,
              labelcolor=INK2)
    _save(fig, path)


# --------------------------------------------------------------- figure 3 --
def fig_multiple_testing(observed, expected, total, path):
    """How many scenarios cleared each bar, against how many luck alone gives.

    Two series, so a legend is present and both are directly labelled.
    """
    labels = ["Looks like a winner\n(p < 0.05, uncorrected)",
              f"Survives correction for\nhaving tried {total:,} times"]
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    _frame(ax, "", "Scenarios")
    x = [0, 1]
    w = 0.34
    b1 = ax.bar([i - w / 2 for i in x], expected, w, color=MID,
                label="Expected from luck alone")
    b2 = ax.bar([i + w / 2 for i in x], observed, w, color=TIGHT,
                label="Actually found")
    for bars in (b1, b2):
        for bar in bars:
            ax.annotate(f"{int(bar.get_height()):,}",
                        xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=8.5, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5, color=INK)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper right")
    ax.set_ylim(0, max(expected + observed) * 1.22)
    _save(fig, path)


# --------------------------------------------------------------- figure 4 --
def fig_return_vs_risk(rows, hold_cagr, path):
    """Return against the worst drawdown that bought it.

    Buy-and-hold appears as a horizontal REFERENCE LINE, not a point: this
    payload records hold's return for every scenario but never its drawdown,
    so there is no honest x-coordinate to place it at. A point drawn at a
    guessed drawdown would be the most-read mark on the chart.
    """
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    _frame(ax, "Worst peak-to-trough fall in the account, %",
           "Annual compound return, %")
    for v in ("own", "atr3"):
        pts = [r for r in rows if r["variant"] == v]
        ax.plot([abs(r["maxdd"]) for r in pts], [r["cagr"] for r in pts], "o",
                markersize=6.5, color=VARIANT_COLOR[v],
                markeredgecolor=SURFACE, markeredgewidth=1.2,
                label=VARIANT_NAME[v], zorder=3)
    ax.axhline(hold_cagr, color=INK, linewidth=1.5, zorder=2)
    ax.annotate(f"Buy and hold all 1,000 stocks: {hold_cagr:.1f}% a year",
                xy=(0.985, hold_cagr), xycoords=("axes fraction", "data"),
                xytext=(0, 5), textcoords="offset points",
                fontsize=8.5, color=INK, weight="bold", ha="right")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    _save(fig, path)

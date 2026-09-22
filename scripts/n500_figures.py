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
    # 4.9in, not the 7.4 it was drawn at until 2026-09-22: at 150mm wide in
    # the report that was 153mm tall, taller than the space left on its page
    # once the prose was cut, so it took a whole page and left a third of the
    # one before it blank. The width, and so the label size, is unchanged;
    # only the row pitch tightens, to ~11pt for a 7.4pt label.
    fig, ax = plt.subplots(figsize=(7.2, 4.9))
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


# ----------------------------------------------- the analysis figures ------
# Added 2026-09-22. The three above answer "what does the membership list do
# to a backtest". These four answer "what is this index, and what did it do",
# which is a different question and the one a trader actually asked.

def fig_dispersion(cagr, portfolio, tri, median, path, lo=-40, hi=70):
    """Every member's own CAGR, with the portfolio and the index on top of it.

    A histogram rather than a box plot: the shape is the point. The
    distribution is right-skewed, which is exactly why the equal-weight
    portfolio lands well above the typical member -- and a reader should see
    the long right tail doing that lifting rather than be told about it.

    The tail runs past 260%/yr, which would flatten everything else, so names
    outside [lo, hi] are drawn as their OWN bars past a visible gap rather
    than clipped into the end bins. Clipping was the first version and it
    lied: it made a pile-up look like a real cluster of companies at exactly
    70%/yr.
    """
    import numpy as np
    cagr = np.asarray(cagr, dtype=float)
    bins = np.linspace(lo, hi, 45)
    step = bins[1] - bins[0]
    under = int((cagr < lo).sum())
    over = int((cagr > hi).sum())
    inside = cagr[(cagr >= lo) & (cagr <= hi)]

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    _frame(ax, xlabel="each company's own annual return since the start, %",
           ylabel="companies")
    ax.hist(inside, bins=bins, color=GRID, edgecolor=SURFACE, linewidth=0.6,
            zorder=3)
    for n, x in ((under, lo - 2.4 * step), (over, hi + 2.4 * step)):
        if n:
            ax.bar(x, n, width=step, color=INK2, alpha=0.45, zorder=3)
    top = ax.get_ylim()[1]
    ax.axvspan(lo - 3.2 * step, 0, color=NEG, alpha=0.05, zorder=1)
    ax.text(lo - 3.0 * step, top * 0.94,
            f"{int((cagr < 0).sum())} of {len(cagr)} lost money",
            fontsize=8, color=NEG, va="top")
    if over:
        ax.annotate(f"{over} ran away,\nthe best at {cagr.max():.0f}%/yr",
                    xy=(hi + 2.4 * step, over), xytext=(hi - 9 * step,
                                                        top * 0.62),
                    fontsize=7.8, color=INK2, ha="right",
                    arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
    if under:
        ax.text(lo - 2.4 * step, under + top * 0.03, f"{under}",
                fontsize=7.4, color=INK2, ha="center")
    for x, c, ls in ((median, INK, "-"), (tri, TRI_C, "--"),
                     (portfolio, TODAY_C, "-")):
        ax.axvline(x, color=c, lw=1.8, ls=ls, zorder=5)
    handles = [plt.Line2D([], [], color=INK, lw=1.8,
                          label=f"the middle company, {median:.1f}%/yr"),
               plt.Line2D([], [], color=TODAY_C, lw=1.8,
                          label=f"holding all of them, {portfolio:.1f}%/yr"),
               plt.Line2D([], [], color=TRI_C, lw=1.8, ls="--",
                          label=f"the real Nifty 500, {tri:.1f}%/yr")]
    ax.legend(handles=handles, frameon=False, fontsize=8, ncol=3,
              loc="upper center", bbox_to_anchor=(0.5, -0.20), labelcolor=INK2)
    ax.set_xlim(lo - 3.8 * step, hi + 3.8 * step)
    # A thin break mark either side, so the two outlier bars are visibly off
    # the scale rather than looking like the next bin along.
    for x in (lo - 1.1 * step, hi + 1.1 * step):
        ax.axvline(x, color=GRID, lw=0.9, ls=(0, (2, 2)), zorder=2)
    # The end tick is dropped: "-40" and "below -40" printed on top of each
    # other in the first version.
    ticks = [t for t in range(int(lo) + 20, int(hi) + 1, 20)]
    ax.set_xticks(ticks + [lo - 2.4 * step, hi + 2.4 * step])
    ax.set_xticklabels([str(t) for t in ticks]
                       + [f"below\n{lo:.0f}", f"above\n{hi:.0f}"])
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    _save(fig, path)


def fig_drawdown(dd, index_dd, path):
    """How far each member fell from its own peak, with the index for scale.

    The whole finding is that the index line and the lines inside it are not
    the same kind of object, so both go on one axis.
    """
    import numpy as np
    dd = np.asarray(dd, dtype=float)
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    _frame(ax, xlabel="deepest fall from the company's own peak, %",
           ylabel="companies")
    ax.hist(dd, bins=np.linspace(-100, 0, 41), color=GRID, edgecolor=SURFACE,
            linewidth=0.6, zorder=3)
    med = float(np.median(dd))
    ax.axvline(med, color=NEG, lw=1.8, zorder=5)
    ax.axvline(index_dd, color=TRI_C, lw=1.8, zorder=5)
    handles = [plt.Line2D([], [], color=NEG, lw=1.8,
                          label=f"median company, {med:.0f}%"),
               plt.Line2D([], [], color=TRI_C, lw=1.8,
                          label=f"the index itself, {index_dd:.0f}%")]
    ax.legend(handles=handles, frameon=False, fontsize=8, ncol=2,
              loc="upper center", bbox_to_anchor=(0.5, -0.22), labelcolor=INK2)
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    _save(fig, path)


def fig_concentration(shares, path):
    """Cumulative share of the trading, against how many names it takes.

    `shares` is one share per name, largest first, summing to 1. The straight
    line is what the curve would look like if every name traded alike; the
    distance between them IS the concentration.
    """
    import numpy as np
    shares = np.asarray(shares, dtype=float)
    cum = 100 * np.cumsum(shares)
    n = len(shares)
    xs = np.arange(1, n + 1)
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    _frame(ax, xlabel="number of companies, busiest first",
           ylabel="share of all trading, %")
    ax.plot(xs, 100 * xs / n, color=GRID, lw=1.4, ls="--", zorder=2,
            label="if every company traded alike")
    ax.plot(xs, cum, color=TODAY_C, lw=2.0, zorder=4,
            label="what actually happens")
    half = int(np.searchsorted(cum, 50) + 1)
    ax.plot([half, half], [0, 50], color=INK2, lw=0.8, ls=":", zorder=3)
    ax.plot([0, half], [50, 50], color=INK2, lw=0.8, ls=":", zorder=3)
    ax.plot([half], [50], "o", color=INK, markersize=4.5, zorder=6)
    ax.annotate(f"{half} companies are half the trading",
                xy=(half, 50), xytext=(half + n * 0.06, 38),
                fontsize=8.2, color=INK,
                arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
    ax.set_xlim(0, n)
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK2)
    ax.grid(color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    _save(fig, path)


def fig_sectors(sectors, portfolio, path):
    """Median company return by NSE industry, worst at the bottom.

    `sectors` is the already-filtered list of dicts from the profile JSON.
    Bars are coloured against the equal-weight portfolio, because "did this
    sector keep up with simply holding everything" is the comparison a reader
    makes anyway.
    """
    rows = sorted(sectors, key=lambda r: r["median_cagr"])
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _frame(ax, xlabel="median company's annual return, %")
    ys = range(len(rows))
    ax.barh(list(ys), [r["median_cagr"] for r in rows], height=0.62,
            color=[TODAY_C if r["median_cagr"] >= portfolio else GRID
                   for r in rows], zorder=3)
    ax.axvline(portfolio, color=INK2, lw=1.2, ls=":", zorder=5)
    ax.text(portfolio, len(rows) - 0.3,
            f"  holding everything, {portfolio:.1f}%", fontsize=7.8,
            color=INK2, va="center")
    for i, r in enumerate(rows):
        ax.text(r["median_cagr"] + 0.4, i, f"{r['median_cagr']:.1f}",
                va="center", fontsize=7.4, color=INK)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([f"{r['industry']}  ({r['n']})" for r in rows],
                       fontsize=7.8, color=INK2)
    ax.set_xlim(0, max(r["median_cagr"] for r in rows) * 1.18)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    _save(fig, path)

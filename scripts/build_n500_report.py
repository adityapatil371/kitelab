"""Build output/nifty500_report.pdf -- what the Nifty 500 is, and what it did.

    python3 -m scripts.build_n500_report

Reads   output/measurements/n500_profile_<year>_<date>.json   (the index itself)
        output/measurements/n500_profile_<year>_<date>.csv     (one row per member)
        output/measurements/n500_hold_<year>_<date>.csv        (the four baskets)
        output/measurements/n500_grid_<year>_<date>.csv        (36 rows, 2 arms)
        output/measurements/n500_grid_check_<year>_<date>.json (self-check)
        data/keep/nifty500.json                                (the study universe)
Writes  output/figures/n500_fig[1-5].png and output/nifty500_report.pdf

STRUCTURE, AND WHY IT IS THIS ONE. The first version of this document led with
membership bias and never described the index at all; the reader's verdict was
that it read as a critique of the Nifty 500 rather than an analysis of it. The
order now is composition, return, drawdown, sectors, and only then the two
biases -- which stay, in one section, because they are what tells the reader
how much of the first four to believe.

Nothing is simulated here and nothing cached is read, so this cannot trigger a
rebuild. The measurements were made by scripts.n500_profile, scripts.n500_hold
and scripts.n500_grid, each of which self-checks against the built board or
against validation.buy_and_hold before writing.

NO NUMBER IN THIS FILE IS TYPED. Every figure in the document is read out of
those measurement files at build time and formatted here -- the same rule
scripts/build_report.py is held to, for the same reason: a number typed into
prose is a number that goes stale silently. Tables and figures also NUMBER
themselves (see Counter), and the prose names none of them, because this
document has already been reordered once and hand-typed references survived
the move as lies.

WHO IT IS FOR. The same reader as output/strategy_report.pdf -- an experienced
discretionary trader who does not read statistics. The two documents are meant
to sit side by side, so the styles, palette and page furniture are deliberately
the first report's, imported rather than copied.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import re
import pathlib
import statistics as st
import sys

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

from scripts import n500_figures as figs
from scripts import n500_text as text
from scripts.build_report import (BOX, INK2, NEG, POS, RULE, S, data_table,
                                  dash, inr)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"
MEAS = OUTDIR / "measurements"
FIGDIR = OUTDIR / "figures"
PDF = OUTDIR / "nifty500_report.pdf"

YEARS = [2012, 2018, 2022]
REFERENCE_YEAR = 2018          # the one start year the prose quotes by default

# Basket labels as scripts.n500_hold writes them, mapped to the short keys the
# report uses. Matched on the PREFIX, because the labels carry the start year
# and the constituent count inside them.
BASKETS = {"TODAY-EARLY": "early", "TODAY": "today", "PIT": "pit",
           "ALL": "all", "TRI": "tri"}


def P(txt, style="body"):
    return Paragraph(txt, S[style])


def picture(path, width):
    """An Image at `width`, height taken from the PNG so nothing is stretched.

    Typing both dimensions is how a figure ends up subtly squashed after its
    matplotlib size changes, and a squashed chart misreports the quantity it
    encodes.
    """
    with open(path, "rb") as fh:
        head = fh.read(32)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise SystemExit(f"{path} is not a PNG with a leading IHDR chunk")
    w = int.from_bytes(head[16:20], "big")
    h = int.from_bytes(head[20:24], "big")
    return Image(str(path), width=width, height=width * h / w)


def pct(x, places=2):
    return f"{x:.{places}f}%"


def pts(x, places=2):
    """A signed difference in annual-return points. The sign is the message."""
    return f"{x:+.{places}f}"


# --------------------------------------------------------------- loading ---
def newest(pattern):
    hits = sorted(glob.glob(str(MEAS / pattern)))
    if not hits:
        raise SystemExit(
            f"no measurement file matching {pattern} in {MEAS}.\n"
            f"Build them first:\n"
            f"  python3 -m scripts.n500_hold --start <year>\n"
            f"  python3 -m scripts.n500_grid --start <year>")
    return pathlib.Path(hits[-1])


def load_hold(year):
    path = newest(f"n500_hold_{year}_*.csv")
    rows = list(csv.DictReader(open(path)))
    print(f"  {path.name}: {len(rows)} rows x {len(rows[0])} columns")
    out = {}
    for r in rows:
        key = next((v for k, v in BASKETS.items() if r["basket"].startswith(k)),
                   None)
        if key is None:
            raise SystemExit(f"unrecognised basket label {r['basket']!r} in "
                             f"{path}; expected one of {sorted(BASKETS)}")
        out[key] = r
    missing = set(BASKETS.values()) - set(out)
    if missing:
        raise SystemExit(f"{path} is missing basket(s) {sorted(missing)}")
    out["_meta"] = rows[0]
    return out


def load_grid(year):
    path = newest(f"n500_grid_{year}_*.csv")
    rows = list(csv.DictReader(open(path)))
    print(f"  {path.name}: {len(rows)} rows x {len(rows[0])} columns")
    for r in rows:
        for f in ("today_cagr", "pit_cagr", "premium", "today_maxdd",
                  "pit_maxdd"):
            r[f] = float(r[f])
        for f in ("today_taken", "pit_taken", "n_built_trades"):
            r[f] = int(r[f])
    return rows


def load_check(year):
    path = newest(f"n500_grid_check_{year}_*.json")
    blob = json.loads(path.read_text())
    print(f"  {path.name}: board {blob['board_built']}, "
          f"cell {blob['account_check']['cell']}")
    return blob


def load_profile(year):
    """The per-member table and its summary. Both, because both are checked.

    The JSON carries the headline numbers; the CSV carries the 480 rows the
    figures are drawn from. Loading them separately would allow a stale pair,
    so the row count is checked against the summary's own n_measured here.
    """
    jp = newest(f"n500_profile_{year}_*.json")
    blob = json.loads(jp.read_text())
    cp = newest(f"n500_profile_{year}_*.csv")
    rows = list(csv.DictReader(open(cp)))
    print(f"  {jp.name}: {blob['n_measured']} members measured, "
          f"{len(blob['sectors'])} industries, portfolio "
          f"{blob['portfolio_cagr']}%/yr")
    print(f"  {cp.name}: {len(rows)} rows x {len(rows[0])} columns")
    for r in rows[:3]:
        print(f"    {r['symbol']} {r['industry']} "
              f"{float(r['own_cagr']):.2f}%/yr {float(r['max_drawdown']):.1f}%")
    if len(rows) != blob["n_measured"]:
        raise SystemExit(
            f"{cp.name} has {len(rows)} rows but {jp.name} says "
            f"{blob['n_measured']} members were measured. They are from "
            f"different runs. Nothing was written.")
    for r in rows:
        for f in ("own_cagr", "max_drawdown", "traded_value", "multiple"):
            r[f] = float(r[f])
        r["bars"] = int(r["bars"])
    return {"summary": blob, "rows": rows}


def load_all():
    print("  reading the measurements")
    hold = {y: load_hold(y) for y in YEARS}
    grid = {y: load_grid(y) for y in YEARS}
    chk = {y: load_check(y) for y in YEARS}
    prof = {y: load_profile(y) for y in YEARS}
    uni = json.loads((ROOT / "data" / "keep" / "nifty500.json").read_text())
    print(f"  nifty500.json: {uni['n_constituents']} constituents, "
          f"{len(uni['study'])} in the study")

    boards = {c["board_built"] for c in chk.values()} | \
             {hold[y]["today"]["board_built"] for y in YEARS}
    if len(boards) != 1:
        raise SystemExit(
            f"the measurements were checked against different boards: "
            f"{sorted(boards)}. Rebuild them against one board before "
            f"publishing, or the document mixes two grids.")
    print(f"  all measurements check against one board: {boards.pop()}")

    # The two halves of this document are computed by different scripts over
    # what is meant to be the same basket. n500_hold's TODAY row and
    # n500_profile's portfolio are both validation.buy_and_hold over the study
    # list, so they must agree -- and if they ever do not, the first half and
    # the last half of the document are describing different portfolios.
    for y in YEARS:
        a = float(hold[y]["today"]["cagr"])
        b = float(prof[y]["summary"]["portfolio_cagr"])
        if abs(a - b) > 0.001:
            raise SystemExit(
                f"at {y} the hold measurement says the equal-weight basket "
                f"returned {a:.4f}%/yr and the profile says {b:.4f}%/yr. The "
                f"two halves of this document are not describing the same "
                f"portfolio. Nothing was written.")
    print(f"  the profile and the hold agree on the basket at all "
          f"{len(YEARS)} start years")
    return hold, grid, chk, prof, uni


# ------------------------------------------------------------- the story ---
# Tables and figures number themselves. The order of this document changed
# once already; hand-typed numbers survived the move as lies, and the prose
# now names no table at all so that a reordering can never strand a reference.
class Counter:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return self.n


TAB = Counter()
FIG = Counter()


def cap(kind, counter, body):
    return P(f"<b>{kind} {counter()}.</b> {body}", "caption")


def verdict_box(prof, hold):
    """The three measurements, as numbers, before any explanation of them."""
    y = REFERENCE_YEAR
    s = prof[y]["summary"]
    med = s["cagr_percentiles"]["50"]
    lines = [
        (f"<b>Three different numbers are all &lsquo;what the Nifty 500 "
         f"did&rsquo; since {y}.</b> The real cap-weighted index returned "
         f"{pct(s['tri_cagr'])} a year. An equal-weight basket of every "
         f"member returned {pct(s['portfolio_cagr'])}. The company in the "
         f"middle of the list returned {pct(med)}."),
        (f"<b>The index hides what holding its members felt like.</b> The "
         f"index itself fell {pct(abs(s['tri_drawdown']), 0)} at its worst. "
         f"The typical member fell {pct(abs(s['median_drawdown']), 0)}, and "
         f"{s['n_halved']} of the {s['n_measured']} members more than halved "
         f"at some point."),
        (f"<b>And today&rsquo;s membership list was drawn knowing how the "
         f"story ended.</b> Holding it back to {y} earns "
         f"<b>{pts(float(hold[y]['today']['inclusion_premium']))} points a "
         f"year</b> that nobody could have earned. The last section is how "
         f"to subtract that from everything above it."),
    ]
    rows = [[Paragraph(f"<b>{i}.</b>", S["cell"]), Paragraph(t, S["cell"])]
            for i, t in enumerate(lines, 1)]
    t = Table(rows, colWidths=[7 * mm, 159 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BOX),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, POS),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def cover(prof, hold, chk, uni, story):
    y = REFERENCE_YEAR
    story.append(P(text.TITLE, "title"))
    story.append(P(dash(text.SUBTITLE), "subtitle"))
    story.append(P(dash(text.WHY_THIS_EXISTS), "lead"))
    story.append(Spacer(1, 4))
    story.append(verdict_box(prof, hold))
    story.append(Spacer(1, 10))
    story.append(P(
        f"Measured on the actual NSE constituent file: "
        f"{uni['n_constituents']} names, {len(uni['study'])} of them with "
        f"usable price history, {prof[y]['summary']['n_measured']} with "
        f"enough of it to return a figure, over {len(YEARS)} start years "
        f"({', '.join(str(x) for x in YEARS)}), against the board built "
        f"{chk[y]['board_built']}. Every figure below is read out of a "
        f"measurement file at build time; none is typed into the prose. "
        f"Nothing here is investment advice and no number is a forecast.",
        "small"))
    story.append(PageBreak())


# ------------------------------------------- 1. what the index actually is --
def industry_table(prof, top=8):
    s = prof[REFERENCE_YEAR]["summary"]
    rows_all = sorted(s["sectors"], key=lambda r: -r["n"])
    head = rows_all[:top]
    tail = rows_all[top:]
    rows = [[r["industry"], f"{r['n']}", pct(r["share_of_trading"], 1)]
            for r in head]
    rows.append([f"<b>The other {len(tail)} industries</b>",
                 f"<b>{sum(r['n'] for r in tail)}</b>",
                 f"<b>{pct(sum(r['share_of_trading'] for r in tail), 1)}</b>"])
    return data_table(
        ["Industry, as NSE tags it", "Companies", "Share of all trading"],
        rows, [96 * mm, 30 * mm, 40 * mm], aligns=[1, 2],
        highlight=[(len(rows), INK2)])


def section_what_it_is(prof, f_conc, story):
    s = prof[REFERENCE_YEAR]["summary"]
    story.append(P("What the Nifty 500 actually is", "h1"))
    story.append(P(dash(text.COMPOSITION_LEAD), "lead"))
    story.append(industry_table(prof))
    biggest = max(s["sectors"], key=lambda r: r["n"])
    story.append(cap("Table", TAB,
        f"The {len(s['sectors'])} industry tags NSE puts on the "
        f"{s['n_measured']} measurable constituents, largest first. "
        f"{biggest['industry']} alone is {biggest['n']} companies and "
        f"{pct(biggest['share_of_trading'], 1)} of the money traded. Share of "
        f"trading is the median day&rsquo;s turnover over the last "
        f"{s['recent_bars']} sessions, not index weight &mdash; see below."))
    story.append(picture(f_conc, 158 * mm))
    c = s["concentration"]
    story.append(cap("Figure", FIG,
        f"Cumulative share of a typical day&rsquo;s trading, adding companies "
        f"from the busiest downwards. The ten busiest names are "
        f"{pct(c['10'], 1)} of it, the busiest fifty are {pct(c['50'], 1)}, "
        f"and the busiest two hundred are {pct(c['200'], 1)}. A list of "
        f"{s['n_measured']} names this lopsided trades like a list of about "
        f"{s['effective_names']:.0f} equal ones."))
    story.append(P(dash(text.CONCENTRATION_WHAT_IT_IS_NOT), "small"))
    tv = sorted(r["traded_value"] for r in prof[REFERENCE_YEAR]["rows"])
    story.append(P(
        f"{dash(text.CONCENTRATION_READ)} The busiest name trades about "
        f"{tv[-1] / tv[0]:,.0f} times the money the quietest does &mdash; "
        f"Rs {inr(tv[-1] / 1e7)} crore a day against Rs "
        f"{tv[0] / 1e7:,.1f} crore."))


# -------------------------------------------- 2. the index vs its members ---
def spread_table(prof):
    label = [("100", "The best company"),
             ("90", "Better than nine in ten"),
             ("75", "Upper quarter"),
             ("50", "<b>The company in the middle</b>"),
             ("25", "Lower quarter"),
             ("10", "Worse than nine in ten"),
             ("0", "The worst company")]
    header = ["Annual return since the start"] + [f"from {y}" for y in YEARS]
    rows = [[lab] + [pct(prof[y]["summary"]["cagr_percentiles"][k], 1)
                     for y in YEARS] for k, lab in label]
    rows.append(["&mdash; of which, companies that lost money"]
                + [f"{prof[y]['summary']['n_negative']} of "
                   f"{prof[y]['summary']['n_measured']}" for y in YEARS])
    rows.append(["<b>An equal-weight basket of all of them</b>"]
                + [f"<b>{pct(prof[y]['summary']['portfolio_cagr'], 1)}</b>"
                   for y in YEARS])
    rows.append(["<b>The real Nifty 500, cap weight, dividends in</b>"]
                + [f"<b>{pct(prof[y]['summary']['tri_cagr'], 1)}</b>"
                   for y in YEARS])
    rows.append(["&mdash; members that beat it"]
                + [f"{prof[y]['summary']['n_beat_index']} of "
                   f"{prof[y]['summary']['n_measured']}" for y in YEARS])
    rows.append(["<b>Where the basket ranks among its own members</b>"]
                + [f"<b>{prof[y]['summary']['portfolio_percentile_of_members']:.0f}"
                   f"th percentile</b>" for y in YEARS])
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3],
                      highlight=[(len(rows) - 3, POS), (len(rows), POS)])


def section_returns(prof, f_disp, story):
    y = REFERENCE_YEAR
    s = prof[y]["summary"]
    story.append(P("What it did, and what its members did", "h1"))
    story.append(P(dash(text.DISPERSION_LEAD), "lead"))
    story.append(spread_table(prof))
    p = s["cagr_percentiles"]
    story.append(cap("Table", TAB,
        f"The spread of member returns, by start year. Read down a column, "
        f"never across one: the columns cover different spans. From {y} the "
        f"middle company returned {pct(p['50'], 1)} a year while the basket "
        f"holding all of them returned {pct(s['portfolio_cagr'], 1)} &mdash; "
        f"which is better than "
        f"{s['portfolio_percentile_of_members']:.0f} per cent of the very "
        f"companies it holds."))
    story.append(picture(f_disp, 146 * mm))
    story.append(cap("Figure", FIG,
        f"Every one of the {s['n_measured']} members from {y}, by its own "
        f"annual return. The long right tail is the whole story: the best "
        f"name compounded at {pct(p['100'], 0)} a year and there is nothing "
        f"symmetrical to it on the left, because the worst a share can do is "
        f"lose everything."))
    story.append(P(dash(text.DISPERSION_WHY)))
    story.append(P(dash(text.DISPERSION_READ)))
    bars = sorted(r["bars"] for r in prof[REFERENCE_YEAR]["rows"])
    story.append(P(
        f"{dash(text.OWN_HISTORY_CAVEAT)} The longest history in the column "
        f"is {bars[-1]:,} trading days and the shortest is {bars[0]}.",
        "small"))


# -------------------------------------------------- 3. the drawdown --------
def drawdown_table(prof):
    header = ["Deepest fall from a running peak"] + [f"from {y}"
                                                     for y in YEARS]
    key = [("tri_drawdown", "<b>The real index itself</b>"),
           ("shallowest_drawdown", "The member that fell least"),
           ("median_drawdown", "<b>The member in the middle</b>"),
           ("worst_drawdown", "The member that fell most")]
    rows = [[lab] + [pct(prof[y]["summary"][k], 1) for y in YEARS]
            for k, lab in key]
    rows.append(["<b>Members that more than halved</b>"]
                + [f"<b>{prof[y]['summary']['n_halved']} of "
                   f"{prof[y]['summary']['n_measured']}</b>" for y in YEARS])
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3], highlight=[(3, NEG), (5, NEG)])


def section_pain(prof, f_dd, story):
    y = REFERENCE_YEAR
    s = prof[y]["summary"]
    story.append(P("The drawdown nobody quotes", "h1"))
    story.append(P(dash(text.PAIN_LEAD), "lead"))
    story.append(drawdown_table(prof))
    story.append(cap("Table", TAB,
        f"Falls are measured on closing prices, each member against its own "
        f"peak inside the window. From {y} the index&rsquo;s worst fall was "
        f"{pct(abs(s['tri_drawdown']), 1)} while the middle member&rsquo;s "
        f"was {pct(abs(s['median_drawdown']), 1)} &mdash; and "
        f"{100 * s['n_halved'] / s['n_measured']:.0f} per cent of members "
        f"lost more than half their value at some point along the way."))
    story.append(picture(f_dd, 158 * mm))
    story.append(cap("Figure", FIG,
        f"The same {s['n_measured']} members, by how far each fell from its "
        f"own peak. The index&rsquo;s own worst fall is the single grey line, "
        f"sitting well to the right of almost every company inside it."))
    story.append(P(dash(text.PAIN_READ)))
    story.append(P(dash(text.PAIN_STAKES)))


# ------------------------------------------------------- 4. sectors --------
def section_sectors(prof, f_sec, story):
    y = REFERENCE_YEAR
    s = prof[y]["summary"]
    big = [r for r in s["sectors"] if r["n"] >= s["min_sector_names"]]
    best = max(big, key=lambda r: r["median_cagr"])
    worst = min(big, key=lambda r: r["median_cagr"])
    heaviest = max(big, key=lambda r: r["share_of_trading"])
    story.append(P("Where the returns came from", "h1"))
    story.append(P(dash(text.SECTOR_LEAD), "lead"))
    story.append(picture(f_sec, 150 * mm))
    story.append(cap("Figure", FIG,
        f"The middle company&rsquo;s annual return in each industry from "
        f"{y}, worst at the bottom, with the number of companies in "
        f"brackets. {best['industry']} at {pct(best['median_cagr'], 1)} "
        f"against {worst['industry']} at {pct(worst['median_cagr'], 1)}: a "
        f"gap of {pts(best['median_cagr'] - worst['median_cagr'], 1)} points "
        f"a year between typical members of the same index. Industries with "
        f"fewer than {s['min_sector_names']} members are left out, which "
        f"drops {len(s['sectors']) - len(big)} of {len(s['sectors'])}."))
    story.append(P(
        f"Concretely: {heaviest['industry']} is the heaviest industry in the "
        f"list on both counts &mdash; {heaviest['n']} companies and "
        f"{pct(heaviest['share_of_trading'], 1)} of the trading &mdash; and "
        f"its typical member returned {pct(heaviest['median_cagr'], 1)} a "
        f"year against {pct(s['portfolio_cagr'], 1)} for simply holding "
        f"everything."))
    story.append(P(dash(text.SECTOR_LIMITS), "small"))


# --------------------------------- 5. how to read every number above -------
def hold_table(hold):
    label = {"today": "Today&rsquo;s Nifty 500 list, equal weight",
             "pit": "A list buildable that year, equal weight",
             "all": "The board&rsquo;s own 1,000 stocks (the check)",
             "tri": "The real Nifty 500 index, cap weight"}
    header = ["Basket, held to today"] + [f"from {y}" for y in YEARS]
    rows = []
    for k in ("today", "pit", "all", "tri"):
        rows.append([label[k]] + [pct(float(hold[y][k]["cagr"]))
                                  for y in YEARS])
    rows.append(["<b>The membership bias</b> (today &minus; buildable)"]
                + [f"<b>{pts(float(hold[y]['today']['inclusion_premium']))}</b>"
                   for y in YEARS])
    rows.append(["<b>Equal weight over the real index</b>"]
                + [f"<b>{pts(float(hold[y]['today']['cagr']) - float(hold[y]['tri']['cagr']))}</b>"
                   for y in YEARS])
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3],
                      highlight=[(len(rows) - 1, POS), (len(rows), POS)])


def rule_summary_table(hold, grid):
    header = ["The gap, in annual return points"] + [f"from {y}"
                                                     for y in YEARS]
    rows = [
        ["The gap for simply holding"]
        + [pts(float(hold[y]["today"]["inclusion_premium"])) for y in YEARS],
        ["<b>The gap for a trading rule</b> (median of 36)"]
        + [f"<b>{pts(st.median([r['premium'] for r in grid[y]]))}</b>"
           for y in YEARS],
        ["&mdash; middle half of the 36 rules"]
        + [f"{pts(st.quantiles([r['premium'] for r in grid[y]], n=4)[0], 1)} "
           f"to {pts(st.quantiles([r['premium'] for r in grid[y]], n=4)[2], 1)}"
           for y in YEARS],
        ["&mdash; rules the wrong list flattered"]
        + [f"{sum(1 for r in grid[y] if r['premium'] > 0)} of {len(grid[y])}"
           for y in YEARS],
        ["<b>Rules that appear to beat holding</b>, today&rsquo;s list"]
        + [f"<b>{sum(1 for r in grid[y] if r['today_cagr'] > float(hold[y]['today']['cagr']))}"
           f" of {len(grid[y])}</b>" for y in YEARS],
        ["&mdash; the same rules, on a list buildable that year"]
        + [f"{sum(1 for r in grid[y] if r['pit_cagr'] > float(hold[y]['pit']['cagr']))}"
           f" of {len(grid[y])}" for y in YEARS],
    ]
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3], highlight=[(2, POS), (5, NEG)])


def section_reading(hold, grid, f_dumb, story):
    y = REFERENCE_YEAR
    story.append(P("How to read every number above", "h1"))
    story.append(P(dash(text.READING_LEAD), "lead"))
    story.append(P(dash(text.THE_TWO_LISTS)))
    story.append(hold_table(hold))
    story.append(cap("Table", TAB,
        f"Equal-weight buy-and-hold to the present from all {len(YEARS)} "
        f"start years, on today&rsquo;s list and on a list a trader could "
        f"have written down that year. The gap is positive at every start "
        f"year, so it is not an artefact of one period. The third row is not "
        f"a result &mdash; it is the check described in the next section."))
    story.append(P(
        f"{dash(text.NOT_THE_IPOS)} At the {y} start, keeping only the "
        f"{int(float(hold[y]['early']['n_named']))} of "
        f"{int(float(hold[y]['today']['n_named']))} study names already "
        f"trading then moves the basket by "
        f"{pts(float(hold[y]['early']['cagr']) - float(hold[y]['today']['cagr']))}"
        f" points &mdash; small, and the wrong sign for the flotation "
        f"explanation."))
    story.append(P(dash(text.EQUAL_WEIGHT_GAP)))
    story.append(P("And a rule makes it worse, not better", "h2"))
    # How widely that holds is counted, not asserted: at one start year the
    # rule median sits BELOW the hold premium, and a flat claim would be wrong
    # there.
    wins = [yr for yr in YEARS
            if st.median([r["premium"] for r in grid[yr]])
            > float(hold[yr]["today"]["cagr"]) - float(hold[yr]["pit"]["cagr"])]
    story.append(P(
        f"<b>{dash(text.RULES_PREDICTION)}</b> That holds at {len(wins)} of "
        f"the {len(YEARS)} start years measured, including the {y} start the "
        f"rest of this section reports."))
    story.append(rule_summary_table(hold, grid))
    story.append(cap("Table", TAB,
        f"All {len(grid[y])} board rows run twice, once on each list, at the "
        f"board&rsquo;s own default account settings. Every rule is scored "
        f"against buy-and-hold on whichever list it was run on, so the last "
        f"two rows are a like-for-like count."))
    story.append(picture(f_dumb, 150 * mm))
    story.append(cap("Figure", FIG,
        f"Each of the {len(grid[y])} rules twice, from {y}. The orange dot is "
        f"what the rule earned on a list buildable that year; the blue dot is "
        f"what the same rule earned on today&rsquo;s list. The bar between "
        f"them is the illusion. Red bars are the few rules the wrong list "
        f"made look worse. The dotted lines are buy-and-hold on each list."))
    story.append(P(dash(text.RULES_WHY)))
    story.append(P(dash(text.RULES_STAKES)))


def section_trust(chk, hold, prof, story):
    y = REFERENCE_YEAR
    c = chk[y]["account_check"]
    story.append(P("Why these numbers are worth more than an assertion", "h1"))
    story.append(P(dash(text.TRUST_ONE), "lead"))
    rows = [["Annual return", pct(c["board"]["cagr"], 1),
             pct(c["here"]["cagr"], 1)],
            ["Final balance", f"Rs {inr(c['board']['final'])}",
             f"Rs {inr(c['here']['final'])}"],
            ["Worst drawdown", pct(c["board"]["maxdd"], 1),
             pct(c["here"]["maxdd"], 1)],
            ["Trades taken", f"{c['board']['taken']:,}",
             f"{c['here']['taken']:,}"],
            ["Risk-adjusted return", f"{c['board']['sharpe']}",
             f"{c['here']['sharpe']}"]]
    story.append(data_table(
        [f"Cell <font face='Courier'>{c['cell']}</font>",
         "As the board recorded it", "As recomputed here"],
        rows, [66 * mm, 50 * mm, 50 * mm], aligns=[1, 2]))
    story.append(cap("Table", TAB,
        f"The account check at the {y} start, against the board built "
        f"{chk[y]['board_built']}. The same rule is checked at the "
        f"{' and '.join(chk[k]['account_check']['cell'].split('|')[-2] for k in YEARS if k != y)}"
        f" starts too, where the board recorded "
        f"{' and '.join(pct(chk[k]['account_check']['board']['cagr'], 1) for k in YEARS if k != y)}"
        f" instead, so agreement is not one lucky match."))
    story.append(P(dash(text.TRUST_TWO)))
    t = chk[y]["trade_check"]
    story.append(P(
        f"At the {y} run that was {t['n_trades']:,} trades across "
        f"{t['n_symbols']} symbols of rule "
        f"<font face='Courier'>{t['strategy']}</font>, all identical. "
        f"{chk[y]['n_built_symbols']} of the {chk[y]['n_study']} study names "
        f"needed building this way; the other "
        f"{chk[y]['n_study'] - chk[y]['n_built_symbols']} came from the "
        f"board&rsquo;s own stored trades, untouched.", "small"))
    s = prof[y]["summary"]
    story.append(P(
        f"The buy-and-hold figures carry the same discipline, twice over. The "
        f"board&rsquo;s own benchmark recomputed here is "
        f"{pct(float(hold[y]['all']['cagr']))} against the "
        f"{hold[y]['all']['selfcheck_board_hold']}% the board recorded. And "
        f"the per-company table behind every spread and drawdown in the first "
        f"half is rebuilt into a portfolio and made to reproduce the Nifty "
        f"500 basket&rsquo;s own figure from its own parts: "
        f"{s['selfcheck_rebuilt_cagr']:.4f}% "
        f"against {s['portfolio_cagr']:.4f}%, or nothing is written.", "small"))


def section_limits(story):
    story.append(P("What this does not tell you", "h1"))
    for block in (text.LIMIT_SURVIVORSHIP, text.LIMIT_CLOSES,
                  text.LIMIT_ONE_INDEX, text.LIMIT_NOT_ADVICE):
        story.append(P(dash(block)))
    story.append(P("In one paragraph", "h2"))
    story.append(P(dash(text.CLOSING), "lead"))


# ----------------------------------------------------------- the appendix --
def section_appendix(grid, hold, uni, prof, story):
    y = REFERENCE_YEAR
    # No PageBreak here. It used to start the appendix on a fresh page and
    # left the page before it three-quarters empty, which the build's own
    # layout audit refuses. The appendix flows on instead.
    story.append(P("Appendix: which stocks, and every rule", "h1"))
    story.append(P("Which stocks are in the study", "h2"))
    story.append(P(dash(text.UNIVERSE_LEAD)))
    dropped = uni["study_dropped"]
    rows = [[f"<font face='Courier'>{s}</font>", "No price history anywhere"]
            for s in dropped["no_price_file"]]
    rows += [[f"<font face='Courier'>{s}</font>", dash(why)]
             for s, why in sorted(dropped["data_defect"].items())]
    story.append(data_table(["Dropped", "Why"], rows, [30 * mm, 136 * mm]))
    story.append(cap("Table", TAB,
        f"The complete list of exclusions: {len(rows)} of "
        f"{uni['n_constituents']}, leaving {len(uni['study'])}. Placeholder "
        f"rows NSE leaves in the file for pending demergers are removed "
        f"before this table, by ISIN rather than by name."))
    story.append(P(dash(text.UNIVERSE_RULE)))
    story.append(P(dash(text.UNIVERSE_TOO_NEW)))
    thin = [s for s in hold[y]["today"]["too_thin_names"].split(";") if s]
    story.append(P(
        f"From {y}, that is {len(thin)} of the {len(uni['study'])}, leaving "
        f"{prof[y]['summary']['n_measured']} measured: "
        f"<font face='Courier'>{', '.join(thin)}</font>. The threshold is "
        f"{hold[y]['today']['min_hold_bars']} trading days, and it is the "
        f"board&rsquo;s own, unchanged for this study.", "small"))
    story.append(P(f"Every rule, from {y}", "h2"))
    story.append(P(
        f"All {len(grid[y])} board rows, sorted by how much the wrong list "
        f"flattered them. &lsquo;Trades&rsquo; is the count on "
        f"today&rsquo;s list. The name before the bar is the entry rule; the "
        f"part after it is the stop width &mdash; "
        f"<font face='Courier'>own</font> is the entry bar&rsquo;s own low, "
        f"<font face='Courier'>atr3</font> is three times the 14-day average "
        f"range below entry.", "small"))
    rows = []
    for r in sorted(grid[y], key=lambda r: -r["premium"]):
        rows.append([f"<font face='Courier'>{r['strategy']}</font>",
                     pct(r["today_cagr"], 1), pct(r["pit_cagr"], 1),
                     pts(r["premium"], 1), pct(r["today_maxdd"], 1),
                     f"{r['today_taken']:,}"])
    story.append(data_table(
        ["Rule", "Today&rsquo;s list", f"Buildable in {y}", "Gap",
         "Worst fall", "Trades"],
        rows, [36 * mm, 28 * mm, 30 * mm, 22 * mm, 26 * mm, 24 * mm],
        aligns=[1, 2, 3, 4, 5]))


# ------------------------------------------------------------ the layout ---
def page_furniture(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(INK2)
    canvas.drawString(22 * mm, 13 * mm,
                      f"{text.TITLE}  |  kitelab, "
                      f"{dt.date.today():%B %Y}  |  not investment advice")
    canvas.drawRightString(A4[0] - 22 * mm, 13 * mm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(22 * mm, 16.5 * mm, A4[0] - 22 * mm, 16.5 * mm)
    canvas.restoreState()


class AuditDoc(BaseDocTemplate):
    """A document that records where each flowable actually landed.

    There is no PDF renderer in this container, so the only way to see the
    finished pages is to ask reportlab where it put things WHILE it puts them.
    Extracting text back out of the PDF cannot answer this -- it reflows, so
    it loses the page geometry that is the whole question.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.placed = []

    def afterFlowable(self, flowable):
        # reportlab clears self.frame once it closes the last frame, so the
        # final flowable of the document arrives with nowhere to measure.
        # Carry the previous reading rather than dropping the row.
        if self.frame is not None:
            self._left = self.frame._y - self.frame._y1
        self.placed.append((self.page, flowable, getattr(self, "_left", 0.0)))


def page_report(doc):
    """Print the page map and name every layout fault it can see.

    Three faults, all of which reportlab will commit silently:
      - a heading alone at the foot of a page, its text overleaf;
      - a caption separated from the table or figure it describes;
      - a page left mostly blank before a deliberate break.
    """
    skip = ("Spacer", "LCActionFlowable")
    solid = [(p, f, left) for p, f, left in doc.placed
             if type(f).__name__ not in skip]
    faults = []
    for i, (p, f, left) in enumerate(solid):
        nxt = solid[i + 1] if i + 1 < len(solid) else None
        prv = solid[i - 1] if i else None
        if isinstance(f, Paragraph) and f.style.name in ("h1", "h2"):
            if nxt and nxt[0] != p:
                faults.append(f"orphan heading on page {p}: "
                              f"{_plain(f.text)[:50]}")
        if isinstance(f, Paragraph) and f.style.name == "cap":
            if prv and prv[0] != p:
                faults.append(f"caption on page {p} is adrift from the "
                              f"{type(prv[1]).__name__} it describes")

    frame_h = A4[1] - 42 * mm
    ends = {}
    for p, f, left in solid:
        ends[p] = left
    last = max(ends)
    for p, left in sorted(ends.items()):
        if p not in (1, last) and left > frame_h * 0.55:
            faults.append(f"page {p} is {left / frame_h:.0%} empty -- move a "
                          f"break or a section")

    print(f"    pages: {last}")
    for p in sorted(ends):
        items = [f for q, f, _ in solid if q == p]
        head = next((_plain(f.text)[:44] for f in items
                     if isinstance(f, Paragraph)
                     and f.style.name in ("h1", "h2", "t")), "(continues)")
        print(f"      p{p:<3} {len(items):2d} flowables, "
              f"{ends[p] / mm:5.1f}mm free   {head}")
    if faults:
        for why in faults:
            print(f"    LAYOUT FAULT: {why}")
        raise SystemExit(f"{len(faults)} layout fault(s). Nothing was written.")


def _plain(txt):
    return re.sub(r"<[^>]+>", "", txt or "(continued)")


def build(story):
    doc = AuditDoc(str(PDF), pagesize=A4,
                   leftMargin=22 * mm, rightMargin=22 * mm,
                   topMargin=20 * mm, bottomMargin=22 * mm,
                   title=text.TITLE, author="kitelab")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=page_furniture)])
    doc.build(story)
    page_report(doc)


# A table taller than a page cannot be kept with its caption -- KeepTogether
# cannot break what will not fit -- so only a short one is bound. The 36-row
# rule table is the one that exceeds that, and it carries no caption.
_MAX_BOUND_ROWS = 14


def _bindable(f):
    if isinstance(f, Image):
        return True
    return isinstance(f, Table) and len(f._cellvalues) <= _MAX_BOUND_ROWS


def _is_head(f):
    return isinstance(f, Paragraph) and f.style.name in ("h1", "h2")


def bind_figures(story):
    """Glue each figure or short table to its caption, and each heading to what follows.

    The two are appended as separate flowables, so reportlab is free to break
    between them, and on 2026-09-22 it did: trimming the prose moved two
    captions onto the page after their own figure and the build's layout audit
    refused to write. Binding them here rather than at each call site keeps the
    section builders readable and cannot be forgotten for a new figure. The
    audit still sees the parts, not the wrapper: it reads doc.placed, which is
    recorded as each inner flowable lands.
    """
    out, i = [], 0
    while i < len(story):
        nxt = story[i + 1] if i + 1 < len(story) else None
        if (_bindable(story[i]) and isinstance(nxt, Paragraph)
                and nxt.style.name == "cap"):
            out.append(KeepTogether([story[i], nxt]))
            i += 2
        elif _is_head(story[i]):
            # A heading alone at the foot of a page is the other fault the
            # audit refuses. A run of headings counts as one -- the appendix
            # opens h1 then h2, and binding only the pair left the h2 stranded.
            # Only a Paragraph is bound on: a table or a figure can exceed the
            # frame, and KeepTogether cannot break what will not fit.
            j = i
            while j < len(story) and _is_head(story[j]):
                j += 1
            if j < len(story) and isinstance(story[j], Paragraph):
                out.append(KeepTogether(story[i:j + 1]))
                i = j + 1
            else:
                out.append(story[i])
                i += 1
        else:
            out.append(story[i])
            i += 1
    return out


def check_layout(story):
    """Every paragraph must fit the frame and wrap to at least one line.

    Measured with reportlab's own wrap(), never by extracting text back out of
    the finished PDF: extraction reflows, so it answers a different question
    and has been wrong here before. A paragraph that wraps to zero lines is an
    entity typo that reportlab swallows silently.
    """
    width = A4[0] - 44 * mm
    height = A4[1] - 42 * mm
    empty, tall = 0, 0
    for item in story:
        if isinstance(item, Paragraph):
            _, h = item.wrap(width, 1000)
            if h <= 0:
                empty += 1
                print(f"    EMPTY PARAGRAPH: {item.text[:70]!r}")
        elif isinstance(item, (Image, KeepTogether)):
            # These cannot be split across a page. A Table can (repeatRows
            # carries its header over), so an over-tall one is not a fault.
            # KeepTogether refuses to wrap outside a build, so measure what
            # it is holding instead.
            parts = item._content if isinstance(item, KeepTogether) else [item]
            h = sum(part.wrap(width, height)[1] for part in parts)
            if h > height:
                tall += 1
                print(f"    TOO TALL for the frame: {type(item).__name__} "
                      f"{h / mm:.0f}mm vs {height / mm:.0f}mm")
    if empty or tall:
        raise SystemExit(
            f"{empty} paragraph(s) render to nothing (almost always a "
            f"malformed entity or tag) and {tall} unsplittable flowable(s) "
            f"are taller than the frame. Nothing was written.")
    print(f"    layout: {sum(isinstance(i, Paragraph) for i in story)} "
          f"paragraphs non-empty, "
          f"{sum(isinstance(i, (Image, KeepTogether)) for i in story)} "
          f"unsplittable flowables fit the frame")


def main():
    OUTDIR.mkdir(exist_ok=True)
    FIGDIR.mkdir(parents=True, exist_ok=True)
    hold, grid, chk, prof, uni = load_all()
    y = REFERENCE_YEAR
    s = prof[y]["summary"]
    rows = prof[y]["rows"]

    print("  drawing figures")
    f_conc, f_disp, f_dd, f_sec, f_dumb = (
        FIGDIR / "n500_fig1_concentration.png",
        FIGDIR / "n500_fig2_dispersion.png",
        FIGDIR / "n500_fig3_drawdown.png",
        FIGDIR / "n500_fig4_sectors.png",
        FIGDIR / "n500_fig5_rules.png")

    # Shares are derived here rather than stored, so the curve and the
    # summary's own concentration numbers cannot drift apart: both are the
    # same column, sorted the same way.
    tv = sorted((r["traded_value"] for r in rows), reverse=True)
    total = sum(tv)
    figs.fig_concentration([v / total for v in tv], f_conc)
    figs.fig_dispersion([r["own_cagr"] for r in rows],
                        s["portfolio_cagr"], s["tri_cagr"],
                        s["cagr_percentiles"]["50"], f_disp)
    figs.fig_drawdown([r["max_drawdown"] for r in rows],
                      s["tri_drawdown"], f_dd)
    figs.fig_sectors([r for r in s["sectors"]
                      if r["n"] >= s["min_sector_names"]],
                     s["portfolio_cagr"], f_sec)
    figs.fig_rule_dumbbell(grid[y], float(hold[y]["today"]["cagr"]),
                           float(hold[y]["pit"]["cagr"]), f_dumb)

    print("  assembling the document")
    story = []
    cover(prof, hold, chk, uni, story)
    section_what_it_is(prof, f_conc, story)
    section_returns(prof, f_disp, story)
    section_pain(prof, f_dd, story)
    # No PageBreak between pain and sectors. It was here until the prose was
    # cut on 2026-09-22; with the shorter text it fired onto an 82%-empty
    # page, which the layout audit refuses.
    section_sectors(prof, f_sec, story)
    section_reading(hold, grid, f_dumb, story)
    section_trust(chk, hold, prof, story)
    section_limits(story)
    section_appendix(grid, hold, uni, prof, story)

    story = bind_figures(story)
    check_layout(story)
    build(story)
    print(f"\n  wrote {PDF.relative_to(ROOT)} "
          f"({PDF.stat().st_size / 1024:.0f} KB), "
          f"{TAB.n} tables and {FIG.n} figures")


if __name__ == "__main__":
    sys.exit(main())

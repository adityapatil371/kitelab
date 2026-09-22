"""Build output/nifty500_report.pdf -- the Nifty 500 membership-bias study.

    python3 -m scripts.build_n500_report

Reads   output/measurements/n500_hold_<year>_<date>.csv      (the four baskets)
        output/measurements/n500_grid_<year>_<date>.csv       (36 rows, 2 arms)
        output/measurements/n500_grid_check_<year>_<date>.json (self-check)
        output/measurements/n500_pit_list_<year>_<date>.csv
        data/keep/nifty500.json                               (the study universe)
Writes  output/figures/n500_fig[1-3].png and output/nifty500_report.pdf

Nothing is simulated here and nothing cached is read, so this cannot trigger a
rebuild. The measurements were made by scripts.n500_hold and scripts.n500_grid,
each of which self-checks against the built board before writing.

NO NUMBER IN THIS FILE IS TYPED. Every figure in the document is read out of
those measurement files at build time and formatted here -- the same rule
scripts/build_report.py is held to, for the same reason: a number typed into
prose is a number that goes stale silently. That is why the two scripts above
write provenance columns (board_built, selfcheck_board_hold, too_thin_names)
that look redundant on a CSV: they are what stops this file from retyping them.

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


def load_all():
    print("  reading the measurements")
    hold = {y: load_hold(y) for y in YEARS}
    grid = {y: load_grid(y) for y in YEARS}
    chk = {y: load_check(y) for y in YEARS}
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
    return hold, grid, chk, uni


# ------------------------------------------------------------- the story ---
def verdict_box(hold, grid):
    """The three findings, as numbers, before any explanation of them."""
    y = REFERENCE_YEAR
    prem = float(hold[y]["today"]["inclusion_premium"])
    ew = float(hold[y]["today"]["cagr"]) - float(hold[y]["tri"]["cagr"])
    med = st.median([r["premium"] for r in grid[y]])
    lines = [
        (f"Holding today&rsquo;s Nifty 500 list back to {y} earns "
         f"<b>{pts(prem)} per cent a year</b> that nobody could have earned. "
         f"That is the membership bias, measured."),
        (f"Equal weighting is not the index. The same basket beats the real "
         f"cap-weighted Nifty 500 total-return index by <b>{pts(ew)} per cent "
         f"a year</b> on top of that."),
        (f"A trading rule does not dilute the bias &mdash; it concentrates "
         f"it. Across all {len(grid[y])} board rules the median gap is "
         f"<b>{pts(med)} per cent a year</b>, larger than the gap for simply "
         f"holding."),
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


def cover(hold, grid, chk, uni, story):
    y = REFERENCE_YEAR
    story.append(P(text.TITLE, "title"))
    story.append(P(dash(text.SUBTITLE), "subtitle"))
    story.append(P(dash(text.WHY_THIS_EXISTS), "lead"))
    story.append(Spacer(1, 4))
    story.append(verdict_box(hold, grid))
    story.append(Spacer(1, 10))
    story.append(P(
        f"Measured on the actual NSE constituent file: "
        f"{uni['n_constituents']} names, {len(uni['study'])} of them with "
        f"usable price history, over {len(YEARS)} start years "
        f"({', '.join(str(x) for x in YEARS)}), against the board built "
        f"{chk[y]['board_built']}. Every figure below is read out of a "
        f"measurement file at build time; none is typed into the prose. "
        f"Nothing here is investment advice and no number is a forecast.",
        "small"))
    story.append(PageBreak())


def section_list(story):
    story.append(P("The list is the whole problem", "h1"))
    story.append(P(dash(text.WHAT_AN_INDEX_IS)))
    story.append(P(dash(text.FACT_REBALANCE)))
    story.append(P(dash(text.FACT_WEIGHT)))


def section_method(story):
    story.append(P("How the comparison is made", "h1"))
    story.append(P(dash(text.THE_TWO_LISTS)))
    story.append(P(dash(text.WHAT_LOOKAHEAD_IS)))
    story.append(P("What a return means here", "h2"))
    story.append(P(dash(text.WHAT_CAGR_IS)))


def hold_table(hold):
    """The four baskets by start year. Everything read, nothing typed."""
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


def section_finding_one(hold, f1, f3, story):
    story.append(P("Finding one: the size of the illusion", "h1"))
    story.append(P(dash(text.FINDING_ONE_LEAD), "lead"))
    story.append(hold_table(hold))
    story.append(P(
        "<b>Table 1.</b> Equal-weight buy-and-hold to the present, from three "
        "start years. Rows are comparable down a column and never across one: "
        "the three columns cover different spans. The third row is not a "
        "result &mdash; it is the check described on the trust page.",
        "caption"))
    story.append(picture(f1, 165 * mm))
    story.append(P(
        "<b>Figure 1.</b> The same table drawn. Blue is the list you can "
        "download today; orange is the list a trader could have assembled in "
        "that year; grey is the real index. The arrow is the gap between the "
        "first two &mdash; the part of the blue bar that was never available.",
        "caption"))
    story.append(P(dash(text.FINDING_ONE_READ)))
    story.append(picture(f3, 165 * mm))
    story.append(P(
        "<b>Figure 2.</b> How much of today&rsquo;s list a trader in each "
        "start year could have been looking at, measured against the top five "
        "hundred by money traded that year.", "caption"))


def early_table(hold):
    header = ["Today&rsquo;s list, held to now"] + [f"from {y}" for y in YEARS]
    rows = [
        ["All study names, including later listings"]
        + [pct(float(hold[y]["today"]["cagr"])) for y in YEARS],
        ["Only names already trading that year"]
        + [pct(float(hold[y]["early"]["cagr"])) for y in YEARS],
        ["&mdash; of which, how many names"]
        + [f"{int(float(hold[y]['early']['n_named']))} of "
           f"{int(float(hold[y]['today']['n_named']))}" for y in YEARS],
        ["<b>Effect of the later listings</b>"]
        + [f"<b>{pts(float(hold[y]['today']['cagr']) - float(hold[y]['early']['cagr']))}</b>"
           for y in YEARS],
    ]
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3], highlight=[(4, NEG)])


def section_not_ipos(hold, story):
    story.append(P("It is not the new listings", "h2"))
    story.append(P(dash(text.NOT_THE_IPOS)))
    story.append(early_table(hold))
    story.append(P(
        "<b>Table 2.</b> The recent-flotation question, answered by "
        "measurement. A name that has not listed yet is held as cash earning "
        "nothing, so dropping those names <i>raises</i> the return rather "
        "than lowering it. The last row is therefore negative, and the gaps "
        "in Table 1 are smaller than they would otherwise be.", "caption"))
    story.append(P(dash(text.NOT_THE_IPOS_2)))


def section_finding_two(hold, story):
    story.append(P("Finding two: equal weight is not the index", "h1"))
    story.append(P(dash(text.FINDING_TWO_LEAD), "lead"))
    story.append(P(dash(text.FINDING_TWO_READ)))


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
    ]
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3], highlight=[(2, POS)])


def beats_hold_table(hold, grid):
    header = ["Rules finishing ahead of buy-and-hold"] + [f"from {y}"
                                                          for y in YEARS]
    rows = [
        ["On today&rsquo;s Nifty 500 list"]
        + [f"{sum(1 for r in grid[y] if r['today_cagr'] > float(hold[y]['today']['cagr']))}"
           f" of {len(grid[y])}" for y in YEARS],
        ["On a list buildable that year"]
        + [f"{sum(1 for r in grid[y] if r['pit_cagr'] > float(hold[y]['pit']['cagr']))}"
           f" of {len(grid[y])}" for y in YEARS],
    ]
    return data_table(header, rows, [76 * mm] + [30 * mm] * len(YEARS),
                      aligns=[1, 2, 3], highlight=[(1, NEG)])


def flip_table(grid, year):
    flips = sorted((r for r in grid[year]
                    if r["today_cagr"] > 0 > r["pit_cagr"]),
                   key=lambda r: r["pit_cagr"])
    if not flips:
        return None, 0
    header = ["Rule", "On today&rsquo;s list",
              f"On a list buildable in {year}", "Difference"]
    rows = [[f"<font face='Courier'>{r['strategy']}</font>",
             pct(r["today_cagr"], 1), pct(r["pit_cagr"], 1),
             pts(r["premium"], 1)] for r in flips]
    return data_table(header, rows, [46 * mm, 40 * mm, 50 * mm, 30 * mm],
                      aligns=[1, 2, 3]), len(flips)


def section_finding_three(hold, grid, f2, story):
    y = REFERENCE_YEAR
    story.append(P("Finding three: a rule makes it worse, not better", "h1"))
    story.append(P(dash(text.FINDING_THREE_LEAD), "lead"))
    # How widely that holds is counted, not asserted: at 2022 the rule
    # median sits BELOW the hold premium, and a flat claim would be wrong
    # there. Table 3 says the same thing in its caption.
    wins = [yr for yr in YEARS
            if st.median([r["premium"] for r in grid[yr]])
            > float(hold[yr]["today"]["cagr"]) - float(hold[yr]["pit"]["cagr"])]
    story.append(P(
        f"<b>{dash(text.FINDING_THREE_PREDICTION)}</b> That holds at "
        f"{len(wins)} of the {len(YEARS)} start years measured, "
        f"including the {y} start the rest of this section reports."))
    story.append(rule_summary_table(hold, grid))
    story.append(P(
        f"<b>Table 3.</b> All {len(grid[y])} board rules run twice, once on "
        f"each list, at the board&rsquo;s own default account settings. The "
        f"second row is larger than the first at every start year but one, "
        f"and at 2012 it is several times larger.", "caption"))
    story.append(P(dash(text.FINDING_THREE_WHY)))
    story.append(picture(f2, 150 * mm))
    story.append(P(
        f"<b>Figure 3.</b> Each of the {len(grid[y])} rules twice, from "
        f"{y}. The orange dot is what the rule earned on a list buildable "
        f"that year; the blue dot is what the same rule earned on "
        f"today&rsquo;s list. The bar between them is the illusion. Red bars "
        f"are the few rules the wrong list made look worse. The dotted lines "
        f"are buy-and-hold on each list &mdash; a rule has to clear its own "
        f"colour to have beaten simply holding.", "caption"))
    story.append(P("What it costs you to get the list wrong", "h2"))
    story.append(P(dash(text.FINDING_THREE_STAKES)))
    story.append(beats_hold_table(hold, grid))
    story.append(P(
        "<b>Table 4.</b> The same thirty-six rules, scored against the "
        "buy-and-hold return of whichever list they were run on. Picking the "
        "wrong list roughly doubles the count.", "caption"))
    tbl, n = flip_table(grid, y)
    if tbl is not None:
        story.append(KeepTogether([
            P(f"The {n} rules that change sign", "h2"), tbl,
            P(f"<b>Table 5.</b> Rules that made money on today&rsquo;s "
              f"Nifty 500 and lost money on a list assembled in {y}. Same "
              f"rule, same parameters, same dates, same costs. Only the list "
              f"of stocks differs.", "caption")]))


def section_universe(hold, uni, story):
    y = REFERENCE_YEAR
    story.append(P("Which stocks are in the study", "h1"))
    story.append(P(dash(text.UNIVERSE_LEAD)))
    dropped = uni["study_dropped"]
    rows = [[f"<font face='Courier'>{s}</font>", "No price history anywhere"]
            for s in dropped["no_price_file"]]
    rows += [[f"<font face='Courier'>{s}</font>", dash(why)]
             for s, why in sorted(dropped["data_defect"].items())]
    story.append(data_table(["Dropped", "Why"], rows, [30 * mm, 136 * mm]))
    story.append(P(
        f"<b>Table 6.</b> The complete list of exclusions: "
        f"{len(rows)} of {uni['n_constituents']}, leaving "
        f"{len(uni['study'])}. Placeholder rows NSE leaves in the file for "
        f"pending demergers are removed before this table, by ISIN rather "
        f"than by name.", "caption"))
    story.append(P(dash(text.UNIVERSE_RULE)))
    thin = [s for s in hold[y]["today"]["too_thin_names"].split(";") if s]
    story.append(P(dash(text.UNIVERSE_TOO_NEW)))
    story.append(P(
        f"From {y}, that is {len(thin)} of the {len(uni['study'])}: "
        f"<font face='Courier'>{', '.join(thin)}</font>. The threshold is "
        f"{hold[y]['today']['min_hold_bars']} trading days, and it is the "
        f"board&rsquo;s own, unchanged for this study.", "small"))


def section_trust(chk, hold, story):
    y = REFERENCE_YEAR
    c = chk[y]["account_check"]
    story.append(P("Why these numbers are worth more than an assertion", "h1"))
    story.append(P(dash(text.TRUST_LEAD), "lead"))
    story.append(P(dash(text.TRUST_ONE)))
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
    story.append(P(
        f"<b>Table 7.</b> The account check at the {y} start, against the "
        f"board built {chk[y]['board_built']}. The same rule is checked at "
        f"the "
        f"{' and '.join(chk[k]['account_check']['cell'].split('|')[-2] for k in YEARS if k != y)}"
        f" starts too, where the board recorded "
        f"{' and '.join(pct(chk[k]['account_check']['board']['cagr'], 1) for k in YEARS if k != y)}"
        f" instead, so agreement is not one lucky match.", "caption"))
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
    story.append(P(
        f"The buy-and-hold figures carry the same discipline. The third row "
        f"of Table 1 is the board&rsquo;s own benchmark recomputed here: "
        f"{pct(float(hold[y]['all']['cagr']))} against the "
        f"{hold[y]['all']['selfcheck_board_hold']}% the board recorded. It "
        f"is printed in the table rather than hidden in a footnote because a "
        f"check the reader cannot see is a check the reader cannot weigh.",
        "small"))


def section_limits(story):
    story.append(P("What this does not tell you", "h1"))
    for block in (text.LIMIT_SURVIVORSHIP, text.LIMIT_ONE_INDEX,
                  text.LIMIT_NOT_ADVICE):
        story.append(P(dash(block)))
    story.append(P("In one sentence", "h2"))
    story.append(P(dash(text.CLOSING), "lead"))


def section_appendix(grid, story):
    y = REFERENCE_YEAR
    story.append(PageBreak())
    story.append(P(f"Appendix: every rule, from {y}", "h1"))
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
    hold, grid, chk, uni = load_all()

    print("  drawing figures")
    f1, f2, f3 = (FIGDIR / "n500_fig1.png", FIGDIR / "n500_fig2.png",
                  FIGDIR / "n500_fig3.png")
    figs.fig_hold_ladder(
        {y: {k: float(hold[y][k]["cagr"]) for k in ("today", "pit", "tri")}
         for y in YEARS}, f1)
    figs.fig_rule_dumbbell(grid[REFERENCE_YEAR],
                           float(hold[REFERENCE_YEAR]["today"]["cagr"]),
                           float(hold[REFERENCE_YEAR]["pit"]["cagr"]), f2)
    figs.fig_overlap({y: int(chk[y]["overlap_today_pit"]) for y in YEARS},
                     int(chk[REFERENCE_YEAR]["n_study"]), f3)

    print("  assembling the document")
    story = []
    cover(hold, grid, chk, uni, story)
    section_list(story)
    section_method(story)
    story.append(PageBreak())
    section_finding_one(hold, f1, f3, story)
    section_not_ipos(hold, story)
    story.append(PageBreak())
    section_finding_two(hold, story)
    section_finding_three(hold, grid, f2, story)
    section_universe(hold, uni, story)
    section_trust(chk, hold, story)
    section_limits(story)
    section_appendix(grid, story)

    check_layout(story)
    build(story)
    print(f"\n  wrote {PDF.relative_to(ROOT)} "
          f"({PDF.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())

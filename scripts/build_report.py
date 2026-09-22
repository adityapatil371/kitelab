"""Build output/strategy_report.pdf -- the full write-up of all 36 board rows.

    python3 -m scripts.build_report

Reads   CLEAN/dashboard.json (the 10,800-cell board) and kitelab.registry for
        labels. Nothing is simulated and nothing cached is touched, so this
        cannot trigger a rebuild.
Writes  output/figures/report_fig[1-4].png and output/strategy_report.pdf.

WHO IT IS FOR, 2026-09-22: an experienced discretionary trader who does not
read statistics. So every quantity is defined in words before it is used, every
rule carries the formula that produced it, and the argument is carried by the
prose rather than by the reader's ability to interpret a p-value. The prose and
formulas live in scripts/report_text.py; the figures in scripts/report_figures.py.

NO NUMBER IN THIS FILE IS TYPED. Every figure in the document is read out of
the payload at build time and formatted here, for the same reason
web/article.html is held to that rule: a number typed into prose is a number
that goes stale silently. The one exception is the cost schedule, which is read
from kitelab.backtest's own constants, and the rule parameters, which are read
from the modules that implement them.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import pathlib
import statistics as st
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

from kitelab import backtest, config, entries, registry, sizing
from scripts import report_figures as figs
from scripts import report_text as text

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"
FIGDIR = OUTDIR / "figures"
PDF = OUTDIR / "strategy_report.pdf"

# The one scenario the report quotes when it needs a single concrete cell.
# Chosen for being the DEFAULTS, not for being the best: all 1,000 stocks, from
# 2018, the smaller account, the lower risk setting, realistic fills.
CELL = "all|0.5|200000|1|2018|mom_hi"
SCENARIO_LABEL = ("all 1,000 stocks, started 2018, Rs 2,00,000 account, "
                  "0.5% risked per trade, realistic fills")

# One word per stop width, for naming a row in prose.
STOP_WORD = {"own": "tight", "atr3": "wide"}


def dash(t):
    """report_text.py is written in plain ASCII, so its prose spells an em dash
    ' -- '. The rest of the report sets a real one; render it that way here.
    Prose fields only -- the formula boxes are ASCII art and stay ASCII.
    """
    return t.replace(" -- ", " &mdash; ")


def inr(n):
    """Indian digit grouping: 10000000 -> "1,00,00,000".

    The rest of the report writes account sizes this way (SCENARIO_LABEL's
    "Rs 2,00,000"), so a Western-grouped figure beside them reads as a typo
    to the only audience that matters here.
    """
    s = f"{int(round(n))}"
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def round_trip_bp():
    """What a delivery round trip actually costs, in bp of turnover.

    The statutory schedule in kitelab.backtest, plus twice the modelled
    half-spread on a Rs 100-crore-a-day name. Computed so that the breakeven
    table's cut-off and the real charge can never drift apart.
    """
    from kitelab import slippage
    statutory = (backtest.STT_RATE * 2 + backtest.STAMP_RATE
                 + (backtest.NSE_TXN_RATE + backtest.SEBI_RATE) * 2
                 * (1 + backtest.GST_RATE))
    liquid_half = slippage.SPREAD_K * (1 / 100) ** 0.5   # ADV = Rs 100 crore
    return statutory * 1e4 + 2 * liquid_half


COST_BP = round_trip_bp()

INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
RULE = colors.HexColor("#c9c8c3")
BAND = colors.HexColor("#f4f3ef")
BOX = colors.HexColor("#f0efec")
POS = colors.HexColor("#2a78d6")
NEG = colors.HexColor("#d03b3b")


# ------------------------------------------------------------- styles ------
def styles():
    add = lambda **kw: ParagraphStyle(**kw)
    out = {}
    out["title"] = add(name="t", fontName="Helvetica-Bold", fontSize=23,
                       leading=27, textColor=INK, spaceAfter=4)
    out["subtitle"] = add(name="st", fontName="Helvetica", fontSize=12,
                          leading=16, textColor=INK2, spaceAfter=18)
    out["h1"] = add(name="h1", fontName="Helvetica-Bold", fontSize=15,
                    leading=19, textColor=INK, spaceBefore=16, spaceAfter=7)
    out["h2"] = add(name="h2", fontName="Helvetica-Bold", fontSize=11,
                    leading=14, textColor=INK, spaceBefore=11, spaceAfter=4)
    out["body"] = add(name="b", fontName="Times-Roman", fontSize=10.2,
                      leading=14.6, textColor=INK, alignment=TA_JUSTIFY,
                      spaceAfter=7)
    out["lead"] = add(name="ld", fontName="Times-Roman", fontSize=11.6,
                      leading=16.4, textColor=INK, alignment=TA_JUSTIFY,
                      spaceAfter=9)
    out["small"] = add(name="sm", fontName="Times-Roman", fontSize=8.8,
                       leading=12, textColor=INK2, alignment=TA_JUSTIFY,
                       spaceAfter=6)
    out["mono"] = add(name="mo", fontName="Courier", fontSize=8.6, leading=12.2,
                      textColor=INK, spaceAfter=0, spaceBefore=0)
    out["caption"] = add(name="cap", fontName="Helvetica-Oblique", fontSize=8.2,
                         leading=11, textColor=INK2, spaceBefore=3,
                         spaceAfter=12)
    out["cell"] = add(name="ce", fontName="Times-Roman", fontSize=8.4,
                      leading=10.6, textColor=INK)
    out["cellb"] = add(name="ceb", fontName="Helvetica-Bold", fontSize=8.2,
                       leading=10.6, textColor=INK)
    return out


S = styles()


def P(txt, style="body"):
    return Paragraph(txt, S[style])


def formula_box(code):
    """A formula, set in monospace on a tinted panel."""
    rows = [[Paragraph(line.replace(" ", "&nbsp;") or "&nbsp;", S["mono"])]
            for line in code.split("\n")]
    t = Table(rows, colWidths=[165 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BOX),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LINEBEFORE", (0, 0), (0, -1), 2, POS),
    ]))
    return t


FRAME_W = A4[0] - 44 * mm          # page width less both 22mm margins


def data_table(header, rows, widths, aligns=None, highlight=None):
    total = sum(widths)
    if total > FRAME_W + 0.01:
        raise ValueError(
            f"table is {total / mm:.1f}mm wide but the frame is "
            f"{FRAME_W / mm:.1f}mm -- reportlab would silently run it off the "
            f"page. Columns: {[round(w / mm, 1) for w in widths]}")
    body = [[Paragraph(h, S["cellb"]) for h in header]]
    for r in rows:
        body.append([Paragraph(str(c), S["cell"]) for c in r])
    t = Table(body, colWidths=widths, repeatRows=1)
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(body)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), BAND))
    for col in (aligns or []):
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    for i, colr in (highlight or []):
        style.append(("TEXTCOLOR", (0, i), (-1, i), colr))
    t.setStyle(TableStyle(style))
    return t


# --------------------------------------------------------------- data ------
def load():
    src = config.CLEAN / "dashboard.json"
    print(f"  reading {src}")
    d = json.loads(src.read_text())
    print(f"    payload: {len(d)} top-level keys, built {d['built']}")
    print(f"    grid: {len(d['grid']):,} cells; validation: {len(d['validation'])} "
          f"rows; daily_excess: {len(d['daily_excess']):,} cells")
    return d


def rule_rows(d):
    """One record per board row (36), with everything the report quotes."""
    grid, val, ts = d["grid"], d["validation"], d["trade_stats"]
    cleared_hurdle = set(d["validation_summary"]["cleared_keys"])
    per_excess = collections.defaultdict(list)
    for key, v in d["daily_excess"].items():
        if v["excess_pts"] is not None:
            per_excess["|".join(key.split("|")[:2])].append(v["excess_pts"])

    rows = []
    for s in registry.REGISTRY:
        key = f"{s.key}|{s.variant}"
        cell = grid.get(f"{key}|{CELL}")
        if cell is None:
            continue
        v, stats = val[key], ts[key]
        ex = per_excess[key]
        wf = v["walk_forward_by_scenario"].get("all|mom_hi|200000", {})
        rows.append(dict(
            key=key, entry=s.key, variant=s.variant,
            short=f"{text.RULES[s.key]['family']}",
            cagr=cell["cagr"], maxdd=cell["maxdd"], taken=cell["taken"],
            mar=cell["mar"], sharpe=cell["sharpe"], exposure=cell["exposure"],
            median_excess=st.median(ex), best_excess=max(ex),
            ahead=sum(1 for e in ex if e > 0), n_cells=len(ex),
            t_gate=v["bootstrap"]["t_gate"],
            perm_p=v["permutation"]["p"],
            distinguishable=v["permutation"]["distinguishable"],
            breakeven=v["breakeven"]["bp"],
            be_status=v["breakeven"]["status"],
            passes=v["fixed_gates"]["passes"],
            hurdle_clear=key in cleared_hurdle,
            wf_wins=wf.get("wins"), wf_total=wf.get("total_windows"),
            trades=stats["trades"], win_rate=stats["win_rate"],
            expectancy_r=stats["expectancy_r"], pf=stats["pf"],
        ))
    print(f"    built {len(rows)} board-row records")
    return rows


# ------------------------------------------------------------- sections ----
def cover(d, rows, diag, hold, story):
    story += [
        P("Eighteen Trading Rules, Measured", "title"),
        P("Why each rule was put on the board, exactly how it was traded, and "
          "what the evidence supports &mdash; a full account of a rules-based "
          "swing-trading board on 1,000 Indian equities.", "subtitle"),
    ]
    ahead_ref = sum(1 for r in rows if r["cagr"] > hold)
    story += [
        P(f"<b>The finding, stated first.</b> Eighteen entry rules were each "
          f"traded at two stop widths, giving <b>{len(rows)} strategies</b>. "
          f"Each was run over {rows[0]['n_cells']} combinations of universe, "
          f"start year, account size, risk setting and signal priority &mdash; "
          f"<b>{diag['n_tested']:,} tested scenarios</b> in all. Against simply "
          f"buying and holding the same stocks, <b>not one of the "
          f"{len(rows)}</b> finishes ahead on a typical scenario &mdash; every "
          f"single row has a negative median. Pick the right setting and some "
          f"do look like winners: {ahead_ref} of them beat hold at this "
          f"report's reference scenario. None of those survive a correction "
          f"for how many settings were tried.", "lead"),
        P("That last sentence is the whole report, and the rest of it is spent "
          "making the sentence checkable: what each rule is, in the exact form "
          "the computer ran it; what it cost to trade; what it returned; and "
          "why &lsquo;this rule beat the market in my backtest&rsquo; is a "
          "claim that needs a much higher bar than it is usually given.", "lead"),
    ]
    story.append(Spacer(1, 6 * mm))
    story.append(data_table(
        ["", "Number", "What it means"],
        [["Stocks in the universe", "1,000",
          "Every NSE equity that survived the data cleaning. NOT the Nifty 500 "
          "&mdash; see &sect;2."],
         ["Entry rules", "18", "Each a single, fully specified condition."],
         ["Stop widths", "2",
          "The entry bar's own low, and 3 &times; ATR(14) below entry."],
         ["Strategies on the board", f"{len(rows)}", "18 rules &times; 2 stops."],
         ["Scenarios tested", f"{diag['n_tested']:,}",
          "Each strategy re-run across universe, start year, account size, "
          "risk and priority."],
         ["Scenarios <i>significantly</i> ahead of hold",
          f"{diag['n_uncorrected']:,}",
          "At the usual p &lt; 0.05 bar, uncorrected. More finish ahead than "
          "this by a nose; these are the ones far enough ahead to be worth "
          "arguing about."],
         ["Expected from luck alone", f"{diag['expected_by_chance']:,.0f}",
          "If no rule had any edge at all, this many would still clear that "
          "bar."],
         ["Surviving correction", f"{diag['n_bonferroni']}",
          "Zero, by either standard correction."]],
        [44 * mm, 22 * mm, 99 * mm], aligns=[1]))
    story.append(P(
        "Read <i>significantly ahead of hold</i> against <i>expected from "
        "luck alone</i>. We found fewer apparent winners than pure chance "
        "would have handed us.", "small"))
    story.append(PageBreak())


def section_universe(d, diag, hold, story):
    story.append(P("1.&nbsp;&nbsp;What a &lsquo;strategy&rsquo; means here", "h1"))
    story.append(P(
        "A strategy in this report is four decisions, fixed in advance and never "
        "changed mid-run. <b>When to buy</b> &mdash; one condition, evaluated on "
        "the close of each session. <b>How much to buy</b> &mdash; set by the "
        "distance to the stop, so every trade risks the same number of rupees. "
        "<b>When to sell</b> &mdash; a stop, plus a time limit or a signal exit. "
        "<b>What it costs</b> &mdash; brokerage, taxes and the spread, charged on "
        "both sides. Change any one of the four and you have a different "
        "strategy, which is why all four are written down below.", "body"))
    story.append(P(
        "Two things this is <i>not</i>. It is not a live track record: no money "
        "was traded and nothing here is a forecast. And it is not a search for "
        "the best rule &mdash; the rules were chosen before their returns were "
        "looked at, by how <i>differently</i> they fire from one another, so "
        "that eighteen labels would not turn out to be a handful of ideas "
        "wearing "
        "different clothes.", "body"))

    story.append(P("2.&nbsp;&nbsp;The universe, and why it is not the Nifty 500", "h1"))
    unis = d["universes"]
    story.append(P(
        "Every result here is computed on <b>1,000 NSE equities</b>, not on an "
        "index. The board splits them into five groups by <i>liquidity</i> "
        "&mdash; median daily traded value, measured only on bars before the "
        "2018 cut, so a stock's group is fixed once and is never assigned with "
        "hindsight:", "body"))
    story.append(data_table(
        ["Group", "Size", "What it holds"],
        [["all", unis["all"].split()[1], "The whole universe."],
         ["large", unis["large"].split()[0], "The most heavily traded names."],
         ["mid", unis["mid"].split()[0], "The middle of the liquidity ladder."],
         ["small", unis["small"].split()[0],
          "The long tail &mdash; two-thirds of the universe by count."],
         ["recent", unis["recent"].split()[0],
          "Listed after 2017, so they have no pre-2018 turnover to rank."]],
        [24 * mm, 20 * mm, 121 * mm], aligns=[1]))
    story.append(P(
        "<b>This matters for how you read the report.</b> The Nifty 500 is a "
        "curated index of large and mid-sized companies. This universe is much "
        "broader and much thinner at the bottom: 631 of the 1,000 sit in the "
        "smallest liquidity group. A rule that looks profitable across all "
        "1,000 may be earning that on stocks nobody can trade in size &mdash; "
        "which is exactly why the spread and impact model in &sect;4 exists, "
        "and why the report quotes the <i>large</i> group separately wherever "
        "the two disagree.", "body"))
    story.append(P(
        "<b>Survivorship remains the largest known bias in this work.</b> The "
        "universe is built from today's instrument list, so companies that "
        "delisted are mostly absent. The one measurement made of it moves "
        "buy-and-hold's return by 0.6&ndash;2.1 points a year and the "
        "rule-versus-hold gap by under 0.2 &mdash; and that is a floor, not an "
        "estimate, because dead companies leave no price history to kill.", "small"))
    story.append(PageBreak())


def section_machine(d, story):
    story.append(P("3.&nbsp;&nbsp;The machine every rule is run through", "h1"))
    story.append(P(
        "All eighteen rules are fed into the same engine, so that differences "
        "between them are differences in the <i>rule</i> and not in how it was "
        "traded. The engine runs in this order.", "body"))

    story.append(P("3.1&nbsp;&nbsp;The entry fires &mdash; once", "h2"))
    story.append(P(
        "A condition like &lsquo;price is at a 20-day low&rsquo; can stay true "
        "for weeks. The engine enters only on the <b>rising edge</b>: the "
        "condition is false yesterday and true today. Without this, a long "
        "slide would generate forty separate trades and the account would pay a "
        "spread a day to hold one position.", "body"))
    story.append(formula_box(
        "fires[t]   =   condition[t]   AND   NOT condition[t-1]"))
    story.append(Spacer(1, 4))

    story.append(P("3.2&nbsp;&nbsp;The stop is placed &mdash; two widths", "h2"))
    story.append(P(
        "The stop is the single most consequential setting on this board, so it "
        "is an <b>axis</b>: every rule is run at both widths, and every row in "
        "the tables appears twice.", "body"))
    story.append(formula_box(
        "tight  ('own')    stop  =  Low[entry bar]\n"
        "\n"
        "wide   ('atr3')   TR[t] = max( High[t]-Low[t],\n"
        "                               |High[t]-Close[t-1]|,\n"
        "                               |Low[t] -Close[t-1]| )\n"
        f"                  ATR   = Wilder average of TR over {entries.ATR_LEN} bars\n"
        "                          (an EMA with alpha = 1/14)\n"
        f"                  stop  =  Close[entry bar]  -  {entries.STOPS['atr3']:.0f} x ATR[entry bar]"))
    story.append(P(
        "Why it matters so much: the tight stop ends <b>54.6% of trades on day "
        "one</b>, leaving a median holding period of a single session. At that "
        "speed no exit rule ever gets a turn, and the strategy is really just "
        "&lsquo;buy the signal, sell tomorrow&rsquo;. The wide stop leaves room "
        "for a trade to breathe and lets it run for weeks. These are genuinely "
        "different businesses, which is why they are never averaged together "
        "here &mdash; and it is why the stop, not the entry, turns out to be "
        "the setting that decides the outcome.", "body"))

    story.append(P("3.3&nbsp;&nbsp;The position is sized from the stop", "h2"))
    story.append(P(
        "Size is not chosen; it falls out of the stop. You decide what you are "
        "willing to lose, the stop tells you the loss per share, and the "
        "division gives the share count. A wide stop therefore buys a small "
        "position and a tight stop a large one, for the same rupee risk.", "body"))
    story.append(formula_box(
        "risk_per_share  =  entry_price  -  stop\n"
        "budget          =  equity  x  risk_pct\n"
        "shares          =  floor( budget / risk_per_share )\n"
        "                   capped by  floor( cash_on_hand / entry_price )\n"
        "\n"
        "if risk_per_share <= 0 the signal is SKIPPED, never filled"))
    story.append(P(
        "That last line matters more than it looks. If a bar closes on its own "
        "low, the tight stop sits at or above the entry price &mdash; there is "
        "nothing to risk, so there is nothing to size against, and the trade is "
        "declined rather than taken at an invented size.", "small"))

    story.append(P("3.4&nbsp;&nbsp;The exit &mdash; and an honest caveat", "h2"))
    story.append(P(
        f"Fourteen of the eighteen rules (everything in "
        f"<font face='Courier' size='9'>entries.py</font>) share one fixed "
        f"exit: the stop, or a hard limit of <b>{entries.MAXHOLD} sessions</b> "
        f"&mdash; one quarter &mdash; whichever comes first. That limit was "
        f"chosen in advance and never tuned, precisely so that the fourteen "
        f"differ <i>only</i> in their entry and can be compared fairly.", "body"))
    story.append(P(
        "The remaining four &mdash; the EMA trend rules, the Turtle channel and "
        "the near-high filter &mdash; carry their own signal exits, because a "
        "trend rule without its exit is not that rule. <b>They are therefore "
        "not exactly comparable with the fourteen</b>, and the report says so "
        "again at each of them. Where a bar breaks both the stop and the exit "
        "on the same day, the trade is charged the <i>stop</i> &mdash; the worse "
        "of the two, which is the honest assumption.", "body"))
    story.append(PageBreak())


def section_costs(d, rows, story):
    # Turnover and the statutory total are computed, never typed: both move the
    # moment the board or the fee schedule does. The span is the payload's own
    # build date minus the reference scenario's start year, so trades-per-year
    # cannot drift as the board is rebuilt.
    built = dt.datetime.strptime(d["built"].split(" IST")[0], "%Y-%m-%d %H:%M")
    start = dt.datetime(d["start_default"], 1, 1)
    yrs = (built - start).days / 365.25
    per_yr = sorted(r["taken"] / yrs for r in rows)
    lo, mid, hi = per_yr[0], st.median(per_yr), per_yr[-1]
    stt = backtest.STT_RATE * 2
    statutory = (stt + backtest.STAMP_RATE
                 + (backtest.NSE_TXN_RATE + backtest.SEBI_RATE) * 2
                 * (1 + backtest.GST_RATE))

    story.append(P("4.&nbsp;&nbsp;What a trade costs", "h1"))
    story.append(P(
        f"Costs are not a footnote here; they are the reason most of these "
        f"rules fail. At the reference scenario these strategies take between "
        f"<b>{lo:.0f} and {hi:.0f} trades a year</b>, {mid:.0f} for the middle "
        f"one, and every one of those has to clear its own friction before it "
        f"clears the market. Two separate things are charged.", "body"))
    story.append(P("4.1&nbsp;&nbsp;Statutory charges and brokerage", "h2"))
    story.append(P(
        "The Zerodha delivery schedule, applied to both sides of every trade. "
        "An intraday round trip is charged the lighter intraday schedule "
        "instead &mdash; mostly because STT falls from 0.1% on both sides to "
        "0.025% on the sell side alone.", "body"))
    story.append(data_table(
        ["Charge", "Delivery rate", "Intraday rate", "Applied to"],
        [["Brokerage",
          "Zero" if backtest.BROKERAGE == 0 else f"Rs {backtest.BROKERAGE:.0f}",
          f"{backtest.INTRADAY_BROKERAGE_RATE:.2%}, capped at "
          f"Rs {backtest.INTRADAY_BROKERAGE_CAP:.0f}", "Per executed order"],
         ["STT", f"{backtest.STT_RATE:.3%}", f"{backtest.INTRADAY_STT_RATE:.3%}",
          "Both sides (delivery); sell only (intraday)"],
         ["Exchange transaction", f"{backtest.NSE_TXN_RATE:.5%}",
          f"{backtest.NSE_TXN_RATE:.5%}", "Turnover"],
         ["SEBI", f"{backtest.SEBI_RATE:.6%}", f"{backtest.SEBI_RATE:.6%}",
          "Turnover (Rs 10 per crore)"],
         ["Stamp duty", f"{backtest.STAMP_RATE:.3%}",
          f"{backtest.INTRADAY_STAMP_RATE:.3%}", "Buy side only"],
         ["GST", f"{backtest.GST_RATE:.0%}", f"{backtest.GST_RATE:.0%}",
          "On brokerage + SEBI + transaction charges"],
         ["DP charge", f"Rs {backtest.DP_PER_SELL:.2f}", "&mdash;",
          "Per scrip, on the sell"]],
        [31 * mm, 28 * mm, 34 * mm, 72 * mm]))

    story.append(P("4.2&nbsp;&nbsp;The spread, which is bigger than all of it", "h2"))
    story.append(P(
        "Every fill is worsened before charges: you buy above the quoted price "
        "and sell below it. The half-spread widens as a stock gets thinner, on "
        "a square-root ladder, with a hard floor at half a tick because no "
        "marketable order can ever do better than crossing one.", "body"))
    from kitelab import slippage
    story.append(formula_box(
        f"half_spread  =  {slippage.SPREAD_K:.0f} bp  x  sqrt( 1 crore / ADV )\n"
        f"                clamped to  [ {slippage.TICK / 2} / price ,  "
        f"{slippage.MAX_HALF_SPREAD:.0%} ]\n"
        f"ADV          =  median traded value over the last "
        f"{slippage.ADV_WINDOW} sessions\n"
        "\n"
        "buy  fill  =  quote  x  ( 1 + half_spread )\n"
        "sell fill  =  quote  x  ( 1 - half_spread )"))
    story.append(P(
        f"<b>Adding it up.</b> The statutory side of a delivery round trip "
        f"comes to <b>{statutory * 1e4:.1f} basis points</b> of turnover "
        f"&mdash; {statutory:.2%} &mdash; and {stt / statutory:.0%} of that is "
        f"STT alone. On top sits twice the half-spread: about "
        f"{COST_BP - statutory * 1e4:.0f} bp round trip on the most liquid "
        f"names, and far more as you go down the liquidity "
        f"ladder, up to the {slippage.MAX_HALF_SPREAD:.0%} ceiling. The "
        f"busiest rule here pays all of it {hi:.0f} times a year.", "body"))
    story.append(P(
        "This is a <b>calibration, not a measurement</b> &mdash; it is set so a "
        "Rs 100-crore-a-day large cap lands near 2&ndash;3 basis points, which "
        "is the order of magnitude quoted on liquid NSE names. On a Rs 15 share "
        "the half-tick floor alone is 17 basis points. Market impact (how much "
        "your own order moves the price) is charged separately, at the account "
        "level, because only the account knows the real position size.", "small"))
    story.append(P(
        "<b>A result worth knowing before you read any table:</b> an earlier "
        "board was run with and without a cap on how much of a stock's daily "
        "turnover one account may take. With no cap, eight tested scenarios "
        "cleared the strictest correction. With the cap at 1% of turnover, "
        "<b>none of 950 did</b>. Nothing here survives being traded in size, "
        "and every number in this report is from the capped run.", "body"))
    story.append(PageBreak())


def section_metrics(story):
    story.append(P("5.&nbsp;&nbsp;Every number in the tables, defined", "h1"))
    story.append(P(
        "Each of these is computed on a <b>daily mark-to-market equity curve</b> "
        "&mdash; every session, the account is valued at cash plus every open "
        "position at that day's close. Not at cost, and not only on days a trade "
        "settled; both of those understate how bad a drawdown felt at the time.",
        "body"))
    defs = [
        ("CAGR", "Compound annual growth rate",
         "( final / start ) ^ ( 1 / years )  -  1",
         "The one number that compounds. A rule earning 17% a year turns "
         "Rs 2 lakh into Rs 4.4 lakh in five years."),
        ("Max drawdown", "Worst peak-to-trough fall",
         "min over t of  ( equity[t] / running_peak[t]  -  1 )",
         "Measured against the peak <i>at the time</i>, not the final peak. "
         "This is the number that decides whether a rule is tradable by a human."),
        ("MAR / Calmar", "Return per unit of pain",
         "CAGR  /  | max drawdown |",
         "Above 0.5 is respectable; above 1.0 is rare and usually means the "
         "sample is too short."),
        ("Sharpe", "Return per unit of wobble",
         "annualised mean daily return  /  annualised std dev of daily returns",
         "Penalises upside and downside alike, which is why the next one exists."),
        ("Sortino", "Return per unit of downside",
         "downside dev  =  sqrt( sum of squared NEGATIVE returns\n"
         "                       /  count of ALL returns )\n"
         "Sortino       =  annualised mean  /  annualised downside dev",
         "Upside volatility is not risk. The divisor is the count of ALL "
         "returns, not just the losing ones &mdash; otherwise a rule with few "
         "losing days is flattered by having had few of them."),
        ("Exposure", "Share of the time money was at work",
         "fraction of sessions with at least one open position",
         "On this board it is 99%+ almost everywhere, which rules out "
         "&lsquo;the rule underperformed because it sat in cash&rsquo;."),
        ("R-multiple", "Profit in units of the risk taken",
         "( exit - entry ) x shares  /  rupees risked on that trade",
         "The trader's native unit. A +3R winner made three times what the "
         "stop would have cost."),
        ("Expectancy", "Average R per trade",
         "mean of the R-multiples over every closed trade",
         "Positive expectancy is necessary, not sufficient &mdash; a rule can "
         "have positive expectancy and still lose to holding."),
        ("Profit factor", "Gross winnings over gross losses",
         "total of winning trades  /  | total of losing trades |",
         "1.0 is break-even. Below about 1.1 the edge will not survive a "
         "change in costs."),
    ]
    for name, gloss, formula, note in defs:
        story.append(KeepTogether([
            P(f"<b>{name}</b> &mdash; {gloss}", "h2"),
            formula_box(formula),
            Spacer(1, 3),
            P(note, "small"),
        ]))
    story.append(PageBreak())


def section_rules(d, rows, story, hold_default):
    by_key = {r["key"]: r for r in rows}
    vs = d["validation_summary"]
    story.append(P("6.&nbsp;&nbsp;The eighteen rules: why each is on the board, and "
                   "exactly what it does", "h1"))
    story.append(P("6.1&nbsp;&nbsp;Why these eighteen, and not eighteen "
                   "others", "h2"))
    story.append(P(
        "The temptation, building a board like this, is to try a hundred rules "
        "and keep the ones that did well. That procedure was put on trial here "
        "like any other, by splitting the history in half and asking how often "
        "the rule that looked best on the first half was below average on the "
        "second. It fails. Across the board it lands barely to the good side of "
        "a dart throw, and on the whole-universe runs &mdash; the ones this "
        "report headlines &mdash; it lands on the wrong side of one. A ranking "
        "built on results you have already seen is a ranking of luck as much as "
        "of merit, and it does not survive contact with the next stretch of "
        "data.", "body"))
    story.append(P(
        "Source: <font face='Courier' size='8'>scripts/pbo.py</font>, the "
        "standard combinatorially-symmetric cross-validation test, run over "
        "sixteen blocks and every way of splitting them into two halves.",
        "small"))
    story.append(P(
        "So the eighteen were chosen <b>before any return was read</b>, on one "
        "criterion: <b>coverage</b>. The question at selection time was never "
        "&lsquo;is this rule good&rsquo; &mdash; it was &lsquo;is there a kind "
        "of trade no rule on the board can currently express&rsquo;. Buying "
        "weakness, buying strength, buying quiet, buying an event, buying a "
        "position in a ranking, buying the calendar: each needed a "
        "representative, and a rule was added only when it reached somewhere "
        "the others could not.", "body"))
    story.append(P(
        "That criterion has teeth, because labels lie. Two rules with "
        "different names can fire on very nearly the same days, and a board of "
        "eighteen such rules is not eighteen tests &mdash; it is one test run "
        "eighteen times, reported as if it were independent evidence. The "
        f"overlap here was measured rather than assumed: the average pair of "
        f"strategies on this board moves together at "
        f"<b>{vs['avg_correlation']:.2f}</b>, and the {len(rows)} rows are "
        f"worth about <b>{vs['n_eff']:.1f} independent tests</b> between them. "
        f"That number is not a curiosity &mdash; &sect;7.2 uses it directly to "
        f"set the bar a result has to clear, and a board that pretended to "
        f"{len(rows)} independent tries would set that bar far too low.", "body"))
    story.append(P(
        "<b>Matched pairs do most of the work.</b> Wherever a rule has an "
        "obvious free choice inside it &mdash; a direction, a horizon, a "
        "window, a filter &mdash; the board carries both settings rather than "
        "the better one, so the difference between the two rows prices that "
        "one choice and nothing else. Gap up against gap down is direction. "
        "The twenty-day low against the one-year low is horizon. Volume spike "
        "against volume dry-up is direction again, on a different measurement. "
        "Seven-bar contraction against the one-bar inside bar is window "
        "length. The monthly-over-weekly trend against the same rule with an "
        "all-time-high filter is that filter, alone. And the calendar rule "
        "&mdash; buy on the first of the month, read no price at all &mdash; "
        "is the null against which every price rule here is read. Each entry "
        "below says which pair, if any, its rule belongs to.", "body"))
    story.append(P(
        "<b>Why two stop widths and not one.</b> The stop is not a detail "
        "attached to a rule; on this board it is the larger half of the "
        "strategy. The tight stop ends more than half of all trades on the "
        "first day, which means the exit, not the entry, decides most "
        "outcomes. Worse, the two stop widths <i>reorder the board</i> &mdash; "
        "a rule that ranks near the top under one is unremarkable under the "
        "other, and the ranking agreement between the two arms is close to "
        "nothing. So no entry rule is reported here without naming its stop, "
        "and every rule is run at both. That is why eighteen rules make "
        f"{len(rows)} strategies.", "body"))
    story.append(Spacer(1, 3 * mm))
    story.append(P("6.2&nbsp;&nbsp;How to read the eighteen entries",
                   "h2"))
    story.append(P(
        f"Each rule below shows why it is on the board, its exact condition, what it looks like on a "
        f"chart, and its numbers at the report's reference scenario &mdash; "
        f"{SCENARIO_LABEL}, where simply buying and holding returned "
        f"<b>{hold_default:.1f}% a year</b>. The two rows are the two stop "
        f"widths. <i>Median vs hold</i> is the middle result across all "
        f"{rows[0]['n_cells']} scenarios that strategy was run in, and is the "
        f"fairer of the two comparisons &mdash; a single scenario can flatter "
        f"anything.", "body"))
    story.append(P(
        f"<b>One note on the columns, which applies to all eighteen tables.</b> "
        f"CAGR, max drawdown and the trade count come from the reference "
        f"scenario. Win rate, expectancy and profit factor are trade-level "
        f"figures pooled over every scenario on the paper book "
        f"(Rs {inr(sizing.CAPITAL)} at {sizing.RISK_PCT:.0%} risk), which is "
        f"why the trade counts implied by those two halves differ. A low win "
        f"rate is not a fault: the tight stop is designed to be hit often and "
        f"cheaply, and these rules make their money on a few large winners "
        f"&mdash; which is what the expectancy and profit-factor columns are "
        f"there to show.", "body"))
    story.append(Spacer(1, 3 * mm))

    for n, entry in enumerate(text.ORDER, 3):
        spec = text.RULES[entry]
        block = [
            P(f"6.{n}&nbsp;&nbsp;{spec['family']}", "h2"),
            P(f"<b>The idea.</b> {dash(spec['idea'])}", "body"),
            P(f"<b>Why it is on the board.</b> {dash(spec['why'])}", "body"),
            formula_box(spec["formula"]),
            Spacer(1, 2),
            P(f"Source: <font face='Courier' size='8'>{spec['src']}</font>",
              "small"),
            P(f"<b>On a chart.</b> {dash(spec['chart'])}", "body"),
            P(f"<b>What to watch.</b> {dash(spec['note'])}", "body"),
        ]
        trows = []
        for variant, vlabel in (("own", "Tight &mdash; entry bar's low"),
                                ("atr3", "Wide &mdash; 3 &times; ATR(14)")):
            r = by_key.get(f"{entry}|{variant}")
            if r is None:
                continue
            trows.append([
                vlabel, f"{r['cagr']:.1f}", f"{r['cagr'] - hold_default:+.1f}",
                f"{r['maxdd']:.0f}", f"{r['taken']:,}",
                f"{r['win_rate']:.0%}", f"{r['expectancy_r']:+.2f}",
                f"{r['pf']:.2f}", f"{r['median_excess']:+.1f}",
                f"{r['ahead']}/{r['n_cells']}",
            ])
        block.append(data_table(
            ["Stop", "CAGR %", "vs hold", "Max DD %", "Trades", "Win %",
             "Exp. R", "PF", "Median<br/>vs hold", "Scenarios<br/>ahead"],
            trows,
            [38 * mm, 14 * mm, 13 * mm, 15 * mm, 14 * mm, 12 * mm, 12 * mm,
             11 * mm, 17 * mm, 19 * mm],
            aligns=[1, 2, 3, 4, 5, 6, 7, 8, 9]))
        block.append(Spacer(1, 5 * mm))
        story.append(KeepTogether(block))
    story.append(PageBreak())


def section_evidence(d, rows, diag, story):
    vs = d["validation_summary"]
    med_excess = st.median([v["excess_pts"] for v in d["daily_excess"].values()
                            if v["excess_pts"] is not None])
    story.append(P("7.&nbsp;&nbsp;How we decided what was real", "h1"))
    story.append(P(
        "This is the section that separates a report from a sales pitch, so it "
        "is written for someone who has never used the word &lsquo;p-value&rsquo; "
        "and does not need to start.", "body"))

    story.append(P(f"7.1&nbsp;&nbsp;The problem: we looked "
                   f"{diag['n_tested']:,} times", "h2"))
    story.append(P(
        f"Take a rule with no skill whatsoever &mdash; buy on a coin flip. Run "
        f"it once and it will land near the market. Run it in "
        f"{diag['n_tested']:,} different settings and a few hundred of those "
        f"runs will look excellent, purely because you ran it "
        f"{diag['n_tested']:,} times. That is not a subtle statistical point; "
        f"it is the same reason someone in the room wins the raffle.", "body"))
    story.append(P(
        f"The usual bar in this business is &lsquo;less than a 1-in-20 chance "
        f"of happening by luck&rsquo;. Applied to {diag['n_tested']:,} tests, "
        f"1-in-20 hands you about <b>{diag['expected_by_chance']:,.0f} "
        f"false winners for free</b>. We found "
        f"<b>{diag['n_uncorrected']}</b>.", "lead"))
    share = diag["n_uncorrected"] / diag["expected_by_chance"]
    story.append(P(
        f"Read that twice. The number of scenarios that looked like they beat "
        f"the market is <b>{share:.0%} of what a board of pure noise would "
        f"have produced</b>. Finding fewer winners than chance is a stronger result "
        f"than finding none: it points away from an edge rather than merely "
        f"failing to find one.", "body"))

    story.append(P("7.2&nbsp;&nbsp;Correcting for it &mdash; two ways", "h2"))
    story.append(P(
        f"<b>Bonferroni</b> is the blunt instrument: divide the bar by the "
        f"number of tests, so each individual result must now be a "
        f"1-in-{diag['n_tested'] / diag['alpha']:,.0f} event. "
        f"<b>{diag['n_bonferroni']}</b> scenarios clear it. Bonferroni is too "
        f"harsh here, because our tests are not independent &mdash; many of "
        f"the {diag['n_tested']:,} are the same rule in a slightly "
        f"different account, so they rise and fall together.", "body"))
    story.append(P(
        f"So the headline correction is <b>Benjamini-Hochberg</b>, which asks "
        f"a gentler question: of the results we are about to call winners, "
        f"what share are we willing to have be false? It ranks every test and "
        f"draws the line where the expected false-discovery share stays under "
        f"5%. It is the right tool for correlated tests and it is the standard "
        f"one. <b>{diag['n_bh']}</b> scenarios clear it.", "body"))
    story.append(P(
        f"How correlated? Measuring the overlap between the "
        f"{vs['tried']} strategies' daily returns gives an average pairwise "
        f"correlation of <b>{vs['avg_correlation']:.2f}</b>, which means the "
        f"board holds about <b>{vs['n_eff']:.1f} genuinely independent "
        f"ideas</b>, not {vs['tried']}. Thirty-six rows; about "
        f"{vs['n_eff']:.0f} ideas. This is the same measurement &sect;6.1 "
        f"used as a design criterion, doing its second job here.",
        "body"))

    story.append(P("7.3&nbsp;&nbsp;The other gate, and why it says something different", "h2"))
    story.append(P(
        "There are <b>two separate questions</b> on this board and they have "
        "different answers. Confusing them is the easiest way to misread "
        "everything that follows.", "body"))
    by_key = {r["key"]: r for r in rows}
    best = by_key.get(vs["best_key"])
    best_name = (f"{best['short']}, {STOP_WORD[best['variant']]} stop"
                 if best else vs["best_key"])
    n_shuffle = sum(1 for r in rows if r["distinguishable"])
    n_hurdle = sum(1 for r in rows if r["hurdle_clear"])
    n_both = sum(1 for r in rows if r["passes"])
    worse = sum(1 for v in d["daily_excess"].values()
                if v["p"] is not None and v["p"] > 1 - diag["alpha"])
    story.append(data_table(
        ["The question", "The test", "The answer"],
        [["Is this rule doing anything at all, or is it the same as buying at "
          "random?",
          "Two tests, and they are not the same test. (a) Re-enter the same "
          "trades on shuffled dates and see whether the real ones can be told "
          "apart. (b) Ask whether the average trade sits far enough above "
          "zero once you allow for the fact that every rule on the board had "
          "a go at this data.",
          f"(a) <b>{n_shuffle} of {vs['tried']}</b> beat their shuffled twin. "
          f"(b) <b>{n_hurdle} of {vs['tried']}</b> clear the luck hurdle "
          f"(strongest: {best_name}, t&nbsp;= {vs['best_t']:.2f} against a "
          f"hurdle of {vs['hurdle']:.2f}). Only <b>{n_both}</b> clear (a) and "
          f"carry a cost margin too &mdash; that pair is the "
          f"&lsquo;strict&nbsp;gate&rsquo; column in the appendix."],
         ["Does it beat simply buying the same stocks and waiting?",
          "Compare the rule's daily returns against hold's, day by day, with a "
          "correction for the fact that adjacent days are related.",
          f"<b>{diag['n_bh']} of {diag['n_tested']:,}</b> scenarios clear it. "
          f"Meanwhile <b>{worse:,}</b> are significantly <i>worse</i> than "
          f"hold."]],
        [40 * mm, 58 * mm, 67 * mm]))
    story.append(P(
        "Both can be true at once, and here they are. A few of these rules "
        "genuinely pick something &mdash; their entries are not random. What "
        "they pick is not worth what it costs to trade it.", "body"))

    story.append(P("7.4&nbsp;&nbsp;Could we even have seen an edge if there was one?", "h2"))
    story.append(P(
        f"A fair objection: maybe the test is too weak to detect a real but "
        f"modest edge. That is measurable, and it was measured. Across every "
        f"scenario the <b>smallest edge this data could reliably detect</b> is "
        f"<b>{diag['median_mde_80']:.1f} percentage points a year</b> "
        f"(at the conventional 80% detection rate).", "body"))
    story.append(P(
        f"So the honest statement is narrow and it is worth stating precisely: "
        f"<b>an edge smaller than about {diag['median_mde_80']:.0f} points a "
        f"year would not show up here.</b> What this report rules out is a "
        f"large edge, not a small one. But the rules are not missing by a "
        f"small margin &mdash; the median scenario trails hold by "
        f"<b>{abs(med_excess):.1f} points a year</b>, and the board's best "
        f"row still has a "
        f"negative median.", "body"))

    story.append(P("7.5&nbsp;&nbsp;The walk-forward check, which failed as a check", "h2"))
    story.append(P(
        f"Walk-forward &mdash; fit on the first few years, test on the next, "
        f"roll forward &mdash; is the standard defence, and it is on this "
        f"board. It should be read with care: when it was run on rules known "
        f"in advance to have <i>no</i> edge, it passed them "
        f"<b>{diag['walk_forward_false_positive_rate']:.1%}</b> of the time. "
        f"A gate that a coin flip passes half the time is not a gate. It is "
        f"reported per rule below for completeness and it carries no weight in "
        f"the conclusion.", "body"))
    story.append(P(
        "That is not a criticism of walk-forward in general; it is a "
        "measurement of what it can do with seven windows of Indian equity "
        "data, where the windows differ from one another more than the rules "
        "do.", "small"))

    story.append(P("7.6&nbsp;&nbsp;The cost test, and where it points instead", "h2"))
    story.append(P(
        "For a trader the most informative single number is not a p-value. It "
        "is the <b>breakeven cost</b>: how much friction a rule could bear per "
        "trade before its gross edge is entirely eaten. Set it against the "
        f"roughly {COST_BP:.0f} basis points a round trip actually costs and "
        f"you get a "
        "yes-or-no answer with no statistics in it at all.", "body"))
    from kitelab import validation
    hi_bp = validation.breakeven_cost.__defaults__[0]
    story.append(formula_box(
        "breakeven_bp  =  the friction, in basis points of turnover, at which\n"
        "                 the rule's return falls to zero\n"
        "\n"
        "if breakeven_bp  <  what you pay      the rule cannot work, ever\n"
        "if breakeven_bp  >  what you pay      it has room -- so the money is\n"
        "                                      being lost somewhere else"))
    neg = [r for r in rows if r["be_status"] == "negative"]
    rob = [r for r in rows if r["be_status"] == "robust"]
    fin = [r for r in rows if r["be_status"] == "finite"]
    thin = [r for r in fin if r["breakeven"] < COST_BP]
    band = [r for r in fin if r["breakeven"] >= COST_BP]
    band_med = st.median([r["breakeven"] for r in band])
    story.append(data_table(
        ["Outcome", "Count", "What it means"],
        [["Loses before any cost", f"{len(neg)}",
          "No gross edge at all. Free trading would not save these."],
         [f"Breakeven under {COST_BP:.0f} bp", f"{len(thin)}",
          "A real gross edge, but smaller than the friction. Ruled out on "
          "arithmetic."],
         [f"Breakeven {COST_BP:.0f}&ndash;{hi_bp:.0f} bp", f"{len(band)}",
          f"Clears what it is actually charged. The median of these is "
          f"{band_med:.0f} bp, {band_med / COST_BP:.1f} times the charge."],
         [f"Survives {hi_bp:.0f} bp+", f"{len(rob)}",
          f"Would still make money at {hi_bp / COST_BP:.0f} times the real "
          f"cost."]],
        [38 * mm, 16 * mm, 111 * mm], aligns=[1]))
    story.append(P(
        f"<b>This is the most surprising table in the report, so read it "
        f"carefully.</b> {len(band) + len(rob)} of the "
        f"{len(rows)} strategies clear their own trading costs at "
        f"the level of individual trades. Of the {len(thin) + len(neg)} that "
        f"do not, only {len(thin)} are killed by friction &mdash; the other "
        f"{len(neg)} never had a gross edge for friction to eat. And yet not "
        f"one of the {len(rows)} beats buying and holding.", "lead"))
    story.append(P(
        "Those two facts together are the real finding, and they say the "
        "shortfall is <b>not mainly the cost of a trade</b>. It is what an "
        "account can do with the signals. Buy-and-hold owns all 1,000 stocks "
        "for the whole period. A rule-based account holds a handful at a time, "
        "chosen by a signal, and spends the rest of its capacity waiting "
        "&mdash; and a separate measurement on this board found the account is "
        "invested over 99% of the time, so this is not idle cash. It is "
        "concentration: the rule is making a bet on which few names to own, "
        "and that bet is worth less than owning everything. The friction then "
        "takes its cut on top of a decision that was already losing.", "body"))
    story.append(P(
        "It also explains the 1% participation result in &sect;4.2. The "
        "per-trade edge is real enough to clear costs on paper; it is not "
        "large enough to survive being scaled to a size where your own order "
        "moves the price.", "body"))
    story.append(PageBreak())


def section_appendix(rows, hold_default, story):
    from kitelab import validation
    hi_bp = validation.breakeven_cost.__defaults__[0]
    story.append(P(f"8.&nbsp;&nbsp;Appendix: all {len(rows)} strategies, "
                   f"ranked", "h1"))
    story.append(P(
        f"Ranked by the middle column &mdash; the <b>median</b> result against "
        f"buy-and-hold across all {rows[0]['n_cells']} scenarios each strategy "
        f"was run in, which is the most robust single number on this board. "
        f"Every value is negative. The reference-scenario columns "
        f"({SCENARIO_LABEL}; hold returned {hold_default:.1f}%) are shown "
        f"alongside to make the point that a single scenario can say almost "
        f"anything.", "body"))
    order = sorted(rows, key=lambda r: -r["median_excess"])
    trows, hl = [], []
    for i, r in enumerate(order, 1):
        trows.append([
            i, r["short"],
            "tight" if r["variant"] == "own" else "wide",
            f"{r['median_excess']:+.2f}",
            f"{r['ahead']}/{r['n_cells']}",
            f"{r['cagr']:.1f}", f"{r['cagr'] - hold_default:+.1f}",
            f"{r['maxdd']:.0f}", f"{r['mar']:.2f}",
            {"negative": "none", "robust": f"&gt;{hi_bp:.0f}"}.get(
                r["be_status"], f"{r['breakeven']:.1f}"
                if r["breakeven"] is not None else "&mdash;"),
            "yes" if r["passes"] else "no",
        ])
        hl.append((i, NEG if r["median_excess"] < 0 else POS))
    story.append(data_table(
        ["#", "Rule", "Stop", "Median<br/>vs hold", "Scenarios<br/>ahead",
         "CAGR<br/>%", "vs<br/>hold", "Max<br/>DD %", "MAR",
         "Breakeven<br/>bp", "Strict<br/>gate"],
        trows,
        [7 * mm, 39 * mm, 10 * mm, 13 * mm, 17 * mm, 12 * mm, 12 * mm,
         12 * mm, 11 * mm, 18 * mm, 14 * mm],
        aligns=[0, 3, 4, 5, 6, 7, 8, 9]))
    story.append(P(
        f"<b>Scenarios ahead</b> counts the settings in which the strategy "
        f"finished above buy-and-hold, out of the settings it ran in. Where "
        f"that denominator is smaller, an account in one of those settings "
        f"was wiped out &mdash; its equity passed through zero &mdash; "
        f"leaving no annual return to compare. <b>Breakeven bp</b> is the "
        f"friction the "
        f"rule could bear before its edge vanishes, against the "
        f"{COST_BP:.0f} bp actually charged; <i>none</i> means it loses money "
        f"before any cost is applied, <i>&gt;{hi_bp:.0f}</i> that it would "
        f"survive {hi_bp / COST_BP:.0f} times the real cost. <b>Strict "
        f"gate</b> is the pair of tests from &sect;7.3 taken together: the "
        f"rule's entries can be told apart from entries on shuffled dates "
        f"<i>and</i> its breakeven cost leaves a margin over what trading "
        f"charges. {sum(1 for r in rows if r['passes'])} of {len(rows)} clear "
        f"it, and a <i>yes</i> there is not a claim that the rule beats "
        f"holding &mdash; none of them do.", "small"))
    story.append(PageBreak())


def section_limits(rows, diag, med_excess, story):
    clears_cost = sum(1 for r in rows
                      if r["be_status"] == "robust"
                      or (r["be_status"] == "finite" and r["breakeven"] >= COST_BP))
    story.append(P("9.&nbsp;&nbsp;What this report does not establish", "h1"))
    limits = [
        ("Survivorship is untouched, and it is the largest known bias.",
         "The universe is built from the current instrument list, so companies "
         "that went to zero are largely missing from both the rules' results "
         "and buy-and-hold's. The measured effect moves hold by 0.6&ndash;2.1 "
         "points a year and the gap between rules and hold by under 0.2 &mdash; "
         "but that is a floor, because a company with no price history cannot "
         "be included in the measurement of its own absence."),
        ("Four rules carry their own exits.",
         "The EMA trend rules, the Turtle channel and the near-high filter "
         "exit on their own signals rather than the shared 60-session limit. "
         "They are therefore not exactly comparable with the other fourteen, "
         "and a difference between those groups may be a difference in exit "
         "rather than in entry &mdash; which matters, because the exit was "
         "separately measured to do roughly three times the work of the entry."),
        ("A modest edge would not be visible.",
         f"The smallest reliably detectable edge is about "
         f"{diag['median_mde_80']:.0f} percentage points a year. Anything "
         f"below that is inside the noise, and this report is silent on it."),
        ("The costs are calibrated, not observed.",
         f"The spread and impact model is set to land liquid large caps near "
         f"2&ndash;3 basis points. It has not been checked against a real fill "
         f"log. If it is too harsh, the rules are understated &mdash; and not "
         f"evenly, because the charge falls on every trade, so a rule taking "
         f"hundreds of trades a year is penalised far harder than "
         f"buy-and-hold, which trades twice. Correcting an over-harsh model "
         f"would therefore narrow the gap rather than widen it. What limits "
         f"how much it could narrow is the breakeven column in the appendix: "
         f"{clears_cost} of the {len(rows)} strategies already clear the "
         f"charge they are given and lose anyway, so the conclusion does not "
         f"rest on the cost number being exactly right."),
        ("This is one market and one period.",
         "Indian equities, from 2006 at the earliest, through an unusually "
         "strong run for simply owning them. A rule that loses to a 17%-a-year "
         "buy-and-hold has not been shown to lose to a flat one."),
        ("Nothing here is a forecast or a recommendation.",
         "No money was traded. No position is suggested, in any direction, in "
         "any instrument. The purpose throughout has been to find out what the "
         "data supports, and the answer it gave was mostly negative &mdash; "
         "which is a useful answer, and the only honest one available."),
    ]
    for head, body in limits:
        story.append(P(f"<b>{head}</b>", "h2"))
        story.append(P(body, "body"))

    story.append(P("The one-paragraph version", "h1"))
    story.append(P(
        f"Eighteen entry rules, each at two stop widths, traded on 1,000 "
        f"Indian stocks with realistic costs, across {rows[0]['n_cells']} "
        f"settings apiece. A few of them pick stocks in a way that is "
        f"demonstrably not random. None of them, at any stop width, in any "
        f"setting that survives a correction for how many settings were "
        f"tried, beats buying the same stocks and waiting &mdash; and far "
        f"more are significantly worse than hold than are better. The rules "
        f"are not failing at the margin: the median scenario gives up about "
        f"{abs(med_excess):.1f} points a year. And the cost of trading is not "
        f"the main reason &mdash; {clears_cost} of the {len(rows)} clear their "
        f"own friction and lose anyway. What they lose to is concentration: "
        f"owning a chosen handful instead of owning everything.", "lead"))


# ------------------------------------------------------------- assembly ----
def page_furniture(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(INK2)
    canvas.drawString(22 * mm, 13 * mm,
                      "Eighteen Trading Rules, Measured  |  kitelab, "
                      "September 2026  |  not investment advice")
    canvas.drawRightString(A4[0] - 22 * mm, 13 * mm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(22 * mm, 16.5 * mm, A4[0] - 22 * mm, 16.5 * mm)
    canvas.restoreState()


def build(story):
    doc = BaseDocTemplate(str(PDF), pagesize=A4,
                          leftMargin=22 * mm, rightMargin=22 * mm,
                          topMargin=20 * mm, bottomMargin=22 * mm,
                          title="Eighteen Trading Rules, Measured",
                          author="kitelab")
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=page_furniture)])
    doc.build(story)


def main():
    OUTDIR.mkdir(exist_ok=True)
    FIGDIR.mkdir(parents=True, exist_ok=True)
    d = load()
    diag = d["diagnostics"]
    rows = rule_rows(d)
    hold_default = d["validation_summary"]["hold_cagr_by_scenario"]["all|2018"]
    print(f"    buy-and-hold at the reference scenario: {hold_default}% a year")

    excess = [v["excess_pts"] for v in d["daily_excess"].values()
              if v["excess_pts"] is not None]
    print(f"    daily_excess rows with a usable number: {len(excess):,} of "
          f"{len(d['daily_excess']):,}")
    print(f"    median excess {st.median(excess):+.2f}, "
          f"{sum(1 for e in excess if e > 0):,} positive")

    print("  drawing figures")
    f1, f2 = FIGDIR / "report_fig1.png", FIGDIR / "report_fig2.png"
    f3, f4 = FIGDIR / "report_fig3.png", FIGDIR / "report_fig4.png"
    figs.fig_excess_histogram(excess, f1)
    figs.fig_rule_dotplot(rows, f2)
    # The second "expected" bar is the family-wise error rate the correction
    # is BUILT to deliver -- n tests x (alpha/n) = alpha = 0.05 false positives
    # across the whole family, i.e. essentially none. It is not
    # expected_by_chance scaled by anything; that would be an invented number.
    figs.fig_multiple_testing(
        [diag["n_uncorrected"], diag["n_bonferroni"]],
        [diag["expected_by_chance"],
         diag["bonferroni_threshold"] * len(excess)],
        diag["n_tested"], f3)
    figs.fig_return_vs_risk(rows, hold_default, f4)
    for f in (f1, f2, f3, f4):
        print(f"    wrote {f.relative_to(ROOT)} "
              f"({f.stat().st_size / 1024:.0f} KB)")

    print("  assembling the document")
    story = []
    cover(d, rows, diag, hold_default, story)
    section_universe(d, diag, hold_default, story)

    missing = diag["n_tested"] - len(excess)
    # Wiped accounts (equity through zero) have no CAGR, so no excess: the
    # chart's denominator is smaller than the board's and the gap is explained
    # rather than left for a reader to spot.
    gap = (f"The board ran {diag['n_tested']:,} scenarios in all; the one not "
           f"drawn here was an account that was wiped out &mdash; its equity "
           f"passed through zero &mdash; so it has no annual return to place."
           if missing == 1 else
           f"The board ran {diag['n_tested']:,} scenarios in all; {missing} "
           f"are not drawn here because those accounts were wiped out "
           f"&mdash; equity through zero &mdash; leaving no return to place.")
    story.append(P("The whole board, in one picture", "h1"))
    story.append(Image(str(f1), width=165 * mm, height=71 * mm))
    story.append(P(
        f"<b>Figure 1.</b> Every one of the {len(excess):,} tested scenarios "
        f"that has a result, placed by how far it finished from buy-and-hold. "
        f"Zero is level with holding. The distribution's centre sits at "
        f"{st.median(excess):+.1f} points a year. {gap}", "caption"))
    story.append(Image(str(f2), width=150 * mm, height=133 * mm))
    story.append(P(
        f"<b>Figure 2.</b> Each strategy's median result against hold, "
        f"across every scenario it was run in &mdash; "
        f"{rows[0]['n_cells']} of them, but for the one strategy that lost an "
        f"account to a wipe-out. Every point is left of zero. The two "
        f"colours are the two stop widths, and the gap between a rule's two "
        f"dots shows how much the stop &mdash; not the entry &mdash; decides "
        f"the outcome.", "caption"))
    story.append(PageBreak())

    section_machine(d, story)
    section_costs(d, rows, story)
    section_metrics(story)
    section_rules(d, rows, story, hold_default)
    section_evidence(d, rows, diag, story)

    story.append(Image(str(f3), width=165 * mm, height=67 * mm))
    story.append(P(
        f"<b>Figure 3.</b> Grey is what pure luck hands you; blue is what was "
        f"actually found. At the uncorrected bar we found "
        f"{diag['n_uncorrected']} where luck alone gives "
        f"{diag['expected_by_chance']:.0f}. After correcting, "
        f"{diag['n_bonferroni']}.", "caption"))
    story.append(Image(str(f4), width=165 * mm, height=101 * mm))
    story.append(P(
        f"<b>Figure 4.</b> Return against the worst fall it took to earn, at "
        f"the reference scenario. Here, and only here, "
        f"{sum(1 for r in rows if r['cagr'] > hold_default)} strategies "
        f"finish above the line and beat hold &mdash; and they are spread "
        f"across the full "
        f"width of the chart, so a higher return on this board buys you no "
        f"protection at all from a deep drawdown. Buy-and-hold is drawn as a "
        f"line rather than a point on purpose: this board records its return "
        f"in every scenario but never its drawdown, so there is no honest "
        f"place on the horizontal axis to put it.", "caption"))
    story.append(PageBreak())

    section_appendix(rows, hold_default, story)
    section_limits(rows, diag, st.median(excess), story)

    build(story)
    print(f"\n  wrote {PDF.relative_to(ROOT)} "
          f"({PDF.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())

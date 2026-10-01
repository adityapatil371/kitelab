"""Shared print palette, figure styling and ReportLab page scaffolding."""
from __future__ import annotations

from functools import partial

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph)

SURFACE = "#fcfcfb"
FIG_INK = "#0b0b0b"
FIG_INK2 = "#52514e"
GRID = "#e3e2de"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
GREY = "#8a8985"
RED = "#d03b3b"

INK = colors.HexColor(FIG_INK)
INK2 = colors.HexColor(FIG_INK2)
RULE = colors.HexColor("#c9c8c3")
BAND = colors.HexColor("#f4f3ef")
BOX = colors.HexColor("#f0efec")
POS = colors.HexColor(BLUE)
NEG = colors.HexColor(RED)


def _frame(ax, xlabel="", ylabel=""):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=FIG_INK2, labelsize=8, length=3, color=GRID)
    if xlabel:
        ax.set_xlabel(xlabel, color=FIG_INK2, fontsize=8.5)
    if ylabel:
        ax.set_ylabel(ylabel, color=FIG_INK2, fontsize=8.5)


def _save(fig, path):
    from matplotlib import pyplot as plt

    fig.patch.set_facecolor(SURFACE)
    fig.savefig(path, dpi=200, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"    wrote {path}")


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


def page_furniture(canvas, doc, title, date):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(INK2)
    canvas.drawString(22 * mm, 13 * mm,
                      f"{title}  |  kitelab, {date}  |  not investment advice")
    canvas.drawRightString(A4[0] - 22 * mm, 13 * mm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(22 * mm, 16.5 * mm, A4[0] - 22 * mm, 16.5 * mm)
    canvas.restoreState()


def build_document(story, path, title, date, doc_class=BaseDocTemplate):
    doc = doc_class(str(path), pagesize=A4,
                    leftMargin=22 * mm, rightMargin=22 * mm,
                    topMargin=20 * mm, bottomMargin=22 * mm,
                    title=title, author="kitelab")
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                      onPage=partial(page_furniture,
                                                     title=title, date=date))])
    doc.build(story)
    return doc

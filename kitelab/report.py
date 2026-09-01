"""Shared Excel writing for the strategy backtests.

One workbook per strategy: a Summary comparing exit rules, then a sheet per exit rule.
Layout follows your own file -- rule text on row 1, headers on row 4, trades from row 5,
newest first within each stock, cumulative columns running down the sheet.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT = Path(__file__).resolve().parent.parent / "output"
HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
LOSS_FONT = Font(color="C00000")

COLUMNS = [
    ("Trade #", "trade_no", "0", 8), ("Date", "entry_date", "yyyy-mm-dd", 11),
    ("Time", "entry_time", "@", 7), ("Stock Name", "symbol", "@", 11),
    ("Level", "level", "#,##0.00", 10), ("Line Type", "level_kind", "@", 11),
    ("Risk Budget", "max_risk_capital", "#,##0", 11),
    ("Risk Taken", "risk_taken", "#,##0.00", 11),
    ("Capital Capped", "capital_capped", "@", 13),
    ("Entry Price", "entry_price", "#,##0.00", 11),
    ("Initial Stop", "stop", "#,##0.00", 11), ("Range", "range", "#,##0.00", 9),
    ("Target", "target", "#,##0.00", 10), ("Final Stop", "final_stop", "#,##0.00", 11),
    ("No. of Shares", "shares", "#,##0", 12),
    ("Cost of Entry", "cost_of_entry", "#,##0.00", 13),
    ("Cum Cost", "cum_cost", "#,##0.00", 13),
    ("Actual Exit", "exit_price", "#,##0.00", 11),
    ("Exit Date", "exit_date", "yyyy-mm-dd", 11),
    ("Exit Reason", "exit_reason", "@", 20), ("Bars Held", "bars_held", "#,##0", 10),
    ("Profit", "gross_profit", "#,##0.00", 11),
    ("Cum Profit", "cum_gross", "#,##0.00", 12),
    ("R Multiple", "r_multiple", "0.00", 10),
    ("Same Session", "same_session", "@", 12),
    # One fee convention (see backtest.charges callers): same-session trades are
    # billed intraday, everything else delivery. There used to be a "(delivery)"
    # trio and an "(intraday)" trio side by side; under one convention they hold
    # the same number, and two identically-shaped columns with different labels is
    # exactly how a reader ends up comparing a sheet against itself.
    ("Charges", "charges", "#,##0.00", 13),
    ("Net", "net_profit", "#,##0.00", 12),
    ("Cum Net", "cum_net", "#,##0.00", 15),
]

SUMMARY_FIELDS = [
    ("Group / Stock", "exit_rule", "@", 24), ("Trades", "trades", "0", 9),
    ("Median Turnover", "turnover", "#,##0", 15),
    ("Wins", "wins", "0", 8), ("Win Rate %", "win_rate_pct", "0.0", 11),
    ("Gross Profit", "gross_profit", "#,##0.00", 13),
    ("Same-day Trades", "same_day", "0", 13),
    ("Charges", "charges", "#,##0.00", 13),
    ("Net", "net_profit", "#,##0.00", 13),
    ("Expectancy / Trade", "expectancy", "#,##0.00", 16),
    ("Profit Factor", "profit_factor", "0.00", 12),
    ("Avg R", "avg_r", "0.00", 9), ("Best R", "best_r", "0.00", 9),
    ("Avg Win", "avg_win", "#,##0.00", 12), ("Avg Loss", "avg_loss", "#,##0.00", 12),
    ("Best Trade % of Gross", "top_share_pct", "0.0", 18),
]


# The trade fields order() accumulates. Not every producer emits them: the
# kitelab.timeframes variants (Q/M/W, W/D/H) build 19-key dicts and carry none
# of these, so an unguarded run raised a bare KeyError several frames deep. Fail
# with a sentence that names the producer instead.
_ORDER_REQUIRES = ("cost_of_entry", "gross_profit", "net_profit", "net_profit_best",
                   "same_session")


def order(trades: list[dict], symbols: list[str]) -> list[dict]:
    if trades:
        missing = [k for k in _ORDER_REQUIRES if k not in trades[0]]
        if missing:
            raise KeyError(
                f"report.order() needs {missing} and this trade list has none of them "
                f"({len(trades[0])} keys). Trade lists from kitelab.timeframes "
                "(the Q/M/W and W/D/H variants) are a reduced shape and cannot be "
                "written with the full trade-sheet writer.")
    ordered: list[dict] = []
    for symbol in symbols:
        mine = [t for t in trades if t["symbol"] == symbol]
        ordered.extend(sorted(mine, key=lambda t: t["entry_ts"], reverse=True))
    # net_best is no longer a column (one fee convention -- it equals net), but the
    # key is still produced: the shape guard above requires net_profit_best, and
    # every trade builder emits it. The two report scripts that read it directly
    # were retired on 2026-09-01.
    cost = gross = net = net_best = 0.0
    for number, trade in enumerate(ordered, start=1):
        cost += trade["cost_of_entry"]
        gross += trade["gross_profit"]
        net += trade["net_profit"]
        net_best += trade["net_profit_best"]
        trade.update(trade_no=number, cum_cost=cost, cum_gross=gross, cum_net=net,
                     cum_net_best=net_best,
                     final_stop=trade.get("final_stop", trade["stop"]),
                     same_session="same day" if trade["same_session"] else "overnight")
    return ordered


def stats(exit_rule: str, trades: list[dict]) -> dict:
    if not trades:
        return {"exit_rule": exit_rule, "trades": 0}
    net = [t["net_profit"] for t in trades]
    gross = [t["gross_profit"] for t in trades]
    wins = [v for v in net if v > 0]
    losses = [v for v in net if v <= 0]
    r_values = [t["r_multiple"] for t in trades]
    total_gross = sum(gross)
    return {
        "exit_rule": exit_rule, "trades": len(trades), "wins": len(wins),
        "win_rate_pct": 100 * len(wins) / len(trades),
        "gross_profit": total_gross,
        "same_day": sum(1 for t in trades if t["same_session"] in (True, "same day")),
        "charges": sum(t["charges"] for t in trades),
        "net_profit": sum(net),
        "charges_best": sum(t["charges_best"] for t in trades),
        "net_best": sum(t["net_profit_best"] for t in trades),
        "expectancy": sum(net) / len(trades),
        # None, not 0.0: a list with no losing trades has NO profit factor -- the
        # denominator is zero. Returning 0.0 made an all-winning list render as the
        # WORST possible score. The project had four different answers to this
        # (0.0 here, inf in tf_compare, NaN in band_compare, None in four others);
        # this is the one. Print it with pf_cell().
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else None,
        "avg_r": sum(r_values) / len(r_values), "best_r": max(r_values),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        # Concentration check: if one trade is most of the profit, the result is that
        # trade, not the strategy.
        "top_share_pct": (100 * max(gross) / total_gross) if total_gross > 0 else 0.0,
    }


def _header(sheet, fields, row: int) -> None:
    for index, (heading, _, _, width) in enumerate(fields, start=1):
        cell = sheet.cell(row=row, column=index, value=heading)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width


def write_trade_sheet(book: Workbook, title: str, rule: str, trades: list[dict]) -> None:
    sheet = book.create_sheet(title)
    sheet["A1"] = rule
    sheet["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=len(COLUMNS))
    sheet.row_dimensions[1].height = 42
    _header(sheet, COLUMNS, 4)
    for offset, trade in enumerate(trades):
        for index, (_, key, number_format, _) in enumerate(COLUMNS, start=1):
            value = trade.get(key)
            cell = sheet.cell(row=5 + offset, column=index, value=value)
            cell.number_format = number_format
            if key in ("gross_profit", "net_profit", "net_profit_best", "r_multiple") \
                    and isinstance(value, (int, float)) and value < 0:
                cell.font = LOSS_FONT
    sheet.freeze_panes = "A5"
    if not trades:
        sheet["A5"] = "No trades qualified under this rule."


def write_summary(book: Workbook, rows: list[dict], note: str) -> None:
    sheet = book.create_sheet("Summary", 0)
    _header(sheet, SUMMARY_FIELDS, 1)
    for offset, row in enumerate(rows):
        for index, (_, key, number_format, _) in enumerate(SUMMARY_FIELDS, start=1):
            value = row.get(key)
            cell = sheet.cell(row=2 + offset, column=index, value=value)
            cell.number_format = number_format
            if key in ("net_profit", "net_best", "expectancy", "avg_r") \
                    and isinstance(value, (int, float)) and value < 0:
                cell.font = LOSS_FONT
    anchor = 3 + len(rows)
    sheet.cell(row=anchor, column=1, value="Notes").font = Font(bold=True)
    for offset, line in enumerate(note.strip().split("\n")):
        sheet.cell(row=anchor + 1 + offset, column=1, value=line.strip())
    sheet.freeze_panes = "A2"


def save(book: Workbook, filename: str) -> Path:
    OUTPUT.mkdir(exist_ok=True)
    target = OUTPUT / filename
    book.save(target)
    return target


# --------------------------------------------------- cached formula values ----
#
# openpyxl writes a formula as <f>...</f><v/> -- the formula with an EMPTY cached
# result. Excel and LibreOffice recalculate on open and fill it in; the viewer on
# this machine does not, and renders the cell blank. LibreOffice is not installed
# here, so a workbook full of formulas ships as a workbook full of blank columns
# (3,130 in EMA Timeframe Comparison, 520 in EMA Showcase, 20,493 in Drawdown
# Proof, all measured 2026-08-31).
#
# The fix is to write the value we already computed in Python into the <v> slot
# next to the formula. The formula stays live -- open the file in real Excel and
# it recalculates exactly as before -- but the cell is readable without Excel.
#
# This started life inside scripts/band_sweep.py. It lives here so every writer
# can use the same one instead of each growing its own copy or, more commonly,
# not having one at all.

_CELL_RE = re.compile(r'<c r="([A-Z]+\d+)"([^>]*)>(<f[^>]*>)(.*?)(</f>)<v\s*/>(</c>)', re.S)
# The fallback literal inside an IFERROR. Quotes are NOT escaped in XML element
# text (only < > & are), so the literal appears as ,"-") -- but accept the escaped
# form too rather than depend on that.
_IFERROR_FALLBACK_RE = re.compile(
    r'^IFERROR\(.*,\s*(?:"|&quot;)(.*?)(?:"|&quot;)\s*\)$', re.S)


def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class FormulaValues:
    """Records the value of each formula written, then injects them into the file.

        fv = report.FormulaValues()
        fv.write(sheet, row, 9, f"=(F{row}-D{row})*G{row}", trade["gross_profit"],
                 "#,##0.00")
        target = report.save(book, "Whatever.xlsx")
        fv.inject(target, book)

    `record()` is the lower-level door for code that builds its cell references
    separately from its values (scripts/band_sweep.py does).
    """

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], object] = {}

    def record(self, sheet_title: str, coordinate: str, value) -> None:
        self._values[(sheet_title, coordinate)] = value

    def write(self, sheet, row: int, column: int, formula: str, value,
              number_format: str | None = None):
        cell = sheet.cell(row=row, column=column, value=formula)
        if number_format is not None:
            cell.number_format = number_format
        self.record(sheet.title, cell.coordinate, value)
        return cell

    def inject(self, target: Path, book: Workbook, quiet: bool = False) -> tuple[int, int]:
        """Rewrite `target` in place, filling every <v/> we have a value for.

        Returns (filled, still_blank). A non-zero still_blank is the warning that
        this whole mechanism exists to make impossible to miss, so it prints.
        """
        names = {f"xl/worksheets/sheet{i}.xml": n
                 for i, n in enumerate(book.sheetnames, start=1)}
        filled = blank = 0
        tmp = str(target) + ".tmp"
        with zipfile.ZipFile(target) as zin, \
                zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.namelist():
                blob = zin.read(item)
                if item in names:
                    sheet_name = names[item]
                    per_sheet = [0, 0]

                    def fill(m, sheet_name=sheet_name, per_sheet=per_sheet):
                        ref, attrs, f_open, formula, f_close, c_close = m.groups()
                        value = self._values.get((sheet_name, ref))
                        if value is None:
                            # An IFERROR formula whose guarded branch is what fires
                            # (a stock with no losing trades divides by zero) has a
                            # known result: the literal in the formula's own
                            # fallback. Read it off the formula rather than leave a
                            # blank -- this is the 12 cells band_sweep used to miss.
                            fallback = _IFERROR_FALLBACK_RE.match(formula.strip())
                            if fallback is None:
                                per_sheet[1] += 1
                                return m.group(0)
                            value = fallback.group(1)
                        per_sheet[0] += 1
                        if isinstance(value, str):
                            attrs = re.sub(r'\s+t="[^"]*"', "", attrs) + ' t="str"'
                            body = f"<v>{_xml_escape(value)}</v>"
                        else:
                            body = f"<v>{value}</v>"
                        return (f'<c r="{ref}"{attrs}>{f_open}{formula}{f_close}'
                                f"{body}{c_close}")

                    xml, _ = _CELL_RE.subn(fill, blob.decode())
                    filled += per_sheet[0]
                    blank += per_sheet[1]
                    if per_sheet[0] and not quiet:
                        print(f"    {sheet_name}: {per_sheet[0]:,} formula cells "
                              f"given cached values")
                    if per_sheet[1]:
                        print(f"    WARNING  {sheet_name}: {per_sheet[1]:,} formula "
                              "cells still have NO cached value and will render "
                              "blank without Excel")
                    blob = xml.encode()
                zout.writestr(item, blob)
        shutil.move(tmp, target)
        if blank:
            print(f"  WARNING  {blank:,} of {filled + blank:,} formula cells in "
                  f"{target.name} have no cached value")
        return filled, blank


# CAGR is None when the rate is undefined -- portfolio.run returns that for an
# account wiped out to zero or below, rather than the 0.0 that used to read as
# "broke even". Spreadsheet cells say so in a word; number_format leaves a string
# alone, so the column stays readable either way.
WIPED_LABEL = "wiped"
# A profit factor with no losing trades in the denominator. Said in words, because a
# blank cell reads as "not computed" and 0.0 reads as "terrible".
PF_NO_LOSSES = "no losses"


def pf_cell(value):
    """A profit factor ready for a worksheet cell or a print."""
    return PF_NO_LOSSES if value is None else value


def cagr_cell(result: dict):
    """The CAGR of a portfolio.run() result, ready for a worksheet cell."""
    value = result.get("cagr_pct")
    return WIPED_LABEL if value is None else value

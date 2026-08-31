"""Shared Excel writing for the strategy backtests.

One workbook per strategy: a Summary comparing exit rules, then a sheet per exit rule.
Layout follows your own file -- rule text on row 1, headers on row 4, trades from row 5,
newest first within each stock, cumulative columns running down the sheet.
"""
from __future__ import annotations

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
    ("Charges (delivery)", "charges", "#,##0.00", 13),
    ("Net (delivery)", "net_profit", "#,##0.00", 12),
    ("Cum Net (delivery)", "cum_net", "#,##0.00", 15),
    ("Charges (intraday)", "charges_best", "#,##0.00", 13),
    ("Net (intraday)", "net_profit_best", "#,##0.00", 12),
    ("Cum Net (intraday)", "cum_net_best", "#,##0.00", 15),
]

SUMMARY_FIELDS = [
    ("Group / Stock", "exit_rule", "@", 24), ("Trades", "trades", "0", 9),
    ("Median Turnover", "turnover", "#,##0", 15),
    ("Wins", "wins", "0", 8), ("Win Rate %", "win_rate_pct", "0.0", 11),
    ("Gross Profit", "gross_profit", "#,##0.00", 13),
    ("Same-day Trades", "same_day", "0", 13),
    ("Charges (delivery)", "charges", "#,##0.00", 13),
    ("Net (delivery)", "net_profit", "#,##0.00", 13),
    ("Charges (intraday)", "charges_best", "#,##0.00", 13),
    ("Net (intraday)", "net_best", "#,##0.00", 13),
    ("Expectancy / Trade", "expectancy", "#,##0.00", 16),
    ("Profit Factor", "profit_factor", "0.00", 12),
    ("Avg R", "avg_r", "0.00", 9), ("Best R", "best_r", "0.00", 9),
    ("Avg Win", "avg_win", "#,##0.00", 12), ("Avg Loss", "avg_loss", "#,##0.00", 12),
    ("Best Trade % of Gross", "top_share_pct", "0.0", 18),
]


# The trade fields order() accumulates. Not every producer emits them: the
# tf_compare timeframe variants (Q/M/W, W/D/H) build 19-key dicts and carry none
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
                f"({len(trades[0])} keys). Trade lists from scripts.tf_compare "
                "(the Q/M/W and W/D/H variants) are a reduced shape and cannot be "
                "written with the full trade-sheet writer.")
    ordered: list[dict] = []
    for symbol in symbols:
        mine = [t for t in trades if t["symbol"] == symbol]
        ordered.extend(sorted(mine, key=lambda t: t["entry_ts"], reverse=True))
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
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else 0.0,
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

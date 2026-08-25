"""Backtest both level strategies with swing-low trailing stops, into their own file.

    python -m scripts.trailing_trades

Writes output/Level Strategies Trailing.xlsx -- separate from the fixed-target run, so
the two can be compared side by side. The Summary sheet carries both.

Only the exit changes. Entries, levels, initial stops and position sizing are identical
to the fixed-target version; the 1.5R target is removed and the stop ratchets up to each
newly confirmed swing low instead.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from kitelab import config, strategies

OUTPUT = Path(__file__).resolve().parent.parent / "output"
HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
LOSS_FONT = Font(color="C00000")

RULES = {
    "Support Bounce": (
        "RULE : entry unchanged -- price touches support, first green 30-minute candle "
        "with its body midpoint above the line, buy-stop at that candle's high. "
        "EXIT CHANGED: no 1.5R target. Initial stop is the signal candle's low, then "
        "ratchets up to each newly confirmed 30-minute swing low. The stop never moves "
        "down. A 5-bar pivot is only used once confirmed, 5 bars after it forms."
    ),
    "Breakout": (
        "RULE : entry unchanged -- first 30-minute close above a twice-touched "
        "resistance level, buy-stop at that candle's high. "
        "EXIT CHANGED: no 1.5R target. Initial stop is the last confirmed daily pivot "
        "low, then ratchets up to each newly confirmed daily swing low. The stop never "
        "moves down. No holding limit -- trades still open at the end of the data are "
        "marked to market at the last price and labelled."
    ),
}

COLUMNS = [
    ("Trade #", "trade_no", "0", 8), ("Date", "entry_date", "yyyy-mm-dd", 11),
    ("Time", "entry_time", "@", 7), ("Stock Name", "symbol", "@", 11),
    ("Level", "level", "#,##0.00", 10), ("Max Risk Capital", "max_risk_capital", "#,##0", 11),
    ("Entry Price", "entry_price", "#,##0.00", 11),
    ("Initial Stop", "stop", "#,##0.00", 11),
    ("Range", "range", "#,##0.00", 9),
    ("Final Stop", "final_stop", "#,##0.00", 11),
    ("Stop Raised By", "stop_raised", "#,##0.00", 12),
    ("No. of Shares", "shares", "#,##0", 12), ("Cost of Entry", "cost_of_entry", "#,##0.00", 13),
    ("Cum Cost", "cum_cost", "#,##0.00", 13), ("Actual Exit", "exit_price", "#,##0.00", 11),
    ("Exit Date", "exit_date", "yyyy-mm-dd", 11), ("Exit Reason", "exit_reason", "@", 20),
    ("Bars Held", "bars_held", "#,##0", 10),
    ("Profit", "gross_profit", "#,##0.00", 11), ("Cum Profit", "cum_gross", "#,##0.00", 12),
    ("R Multiple", "r_multiple", "0.00", 10),
    ("Same Session", "same_session", "@", 12),
    ("Charges (delivery)", "charges", "#,##0.00", 13),
    ("Net (delivery)", "net_profit", "#,##0.00", 12),
    ("Cum Net (delivery)", "cum_net", "#,##0.00", 15),
    ("Charges (intraday)", "charges_best", "#,##0.00", 13),
    ("Net (intraday)", "net_profit_best", "#,##0.00", 12),
    ("Cum Net (intraday)", "cum_net_best", "#,##0.00", 15),
]


def _order(trades: list[dict], symbols: list[str]) -> list[dict]:
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
                     same_session="same day" if trade["same_session"] else "overnight",
                     stop_raised=trade["final_stop"] - trade["stop"])
    return ordered


def _write_sheet(book: Workbook, title: str, rule: str, trades: list[dict]) -> None:
    sheet = book.create_sheet(title)
    sheet["A1"] = rule
    sheet["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=len(COLUMNS))
    sheet.row_dimensions[1].height = 40
    for index, (heading, _, _, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=4, column=index, value=heading)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    for offset, trade in enumerate(trades):
        for index, (_, key, number_format, _) in enumerate(COLUMNS, start=1):
            value = trade.get(key)
            cell = sheet.cell(row=5 + offset, column=index, value=value)
            cell.number_format = number_format
            if key in ("gross_profit", "net_profit", "r_multiple") and isinstance(
                    value, (int, float)) and value < 0:
                cell.font = LOSS_FONT
    sheet.freeze_panes = "A5"
    if not trades:
        sheet["A5"] = "No trades."


def _stats(name: str, exit_rule: str, trades: list[dict]) -> dict:
    if not trades:
        return {"strategy": name, "exit_rule": exit_rule, "trades": 0}
    net = [t["net_profit"] for t in trades]
    wins = [v for v in net if v > 0]
    r_values = [t["r_multiple"] for t in trades]
    return {
        "strategy": name, "exit_rule": exit_rule, "trades": len(trades),
        "wins": len(wins), "win_rate_pct": 100 * len(wins) / len(trades),
        "gross_profit": sum(t["gross_profit"] for t in trades),
        "charges": sum(t["charges"] for t in trades),
        "net_profit": sum(net), "expectancy": sum(net) / len(trades),
        "same_day": sum(1 for t in trades if t["same_session"]),
        "charges_best": sum(t["charges_best"] for t in trades),
        "net_best": sum(t["net_profit_best"] for t in trades),
        "avg_r": sum(r_values) / len(r_values), "best_r": max(r_values),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": (sum(v for v in net if v <= 0) / (len(net) - len(wins))
                     if len(net) > len(wins) else 0.0),
    }


def _write_summary(book: Workbook, rows: list[dict]) -> None:
    sheet = book.create_sheet("Summary", 0)
    fields = [("Strategy", "strategy", "@", 18), ("Exit Rule", "exit_rule", "@", 22),
              ("Trades", "trades", "0", 9), ("Wins", "wins", "0", 8),
              ("Win Rate %", "win_rate_pct", "0.0", 11),
              ("Gross Profit", "gross_profit", "#,##0.00", 13),
              ("Same-day Trades", "same_day", "0", 13),
              ("Charges (delivery)", "charges", "#,##0.00", 13),
              ("Net (delivery)", "net_profit", "#,##0.00", 13),
              ("Charges (intraday)", "charges_best", "#,##0.00", 13),
              ("Net (intraday)", "net_best", "#,##0.00", 13),
              ("Expectancy / Trade", "expectancy", "#,##0.00", 16),
              ("Avg R", "avg_r", "0.00", 9), ("Best R", "best_r", "0.00", 9),
              ("Avg Win", "avg_win", "#,##0.00", 12),
              ("Avg Loss", "avg_loss", "#,##0.00", 12)]
    for index, (heading, _, _, width) in enumerate(fields, start=1):
        cell = sheet.cell(row=1, column=index, value=heading)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    for offset, row in enumerate(rows):
        for index, (_, key, number_format, _) in enumerate(fields, start=1):
            value = row.get(key)
            cell = sheet.cell(row=2 + offset, column=index, value=value)
            cell.number_format = number_format
            if key in ("net_profit", "net_best", "expectancy", "avg_r") and isinstance(
                    value, (int, float)) and value < 0:
                cell.font = LOSS_FONT
    sheet.freeze_panes = "A2"


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    cfg = config.load()
    OUTPUT.mkdir(exist_ok=True)
    book = Workbook()
    book.remove(book.active)

    builders = {"Support Bounce": strategies.support_bounce_trades,
                "Breakout": strategies.breakout_trades}
    summaries = []
    print()
    for name, build in builders.items():
        # Same entries both ways; only the exit differs.
        fixed = strategies.drop_overlaps(
            [t for s in cfg.symbols for t in build(s, trailing_stops=False)])
        trailed = strategies.drop_overlaps(
            [t for s in cfg.symbols for t in build(s, trailing_stops=True)])
        summaries.append(_stats(name, "fixed 1.5R target", fixed))
        summaries.append(_stats(name, "swing-low trailing", trailed))
        _write_sheet(book, f"{name} (Trailing)", RULES[name], _order(trailed, cfg.symbols))
        print(f"  {name:<16} fixed {len(fixed):>3} trades   trailing {len(trailed):>3} trades")

    _write_summary(book, summaries)
    target = OUTPUT / "Level Strategies Trailing.xlsx"
    book.save(target)
    print(f"\n  written: {target}\n")


if __name__ == "__main__":
    main()

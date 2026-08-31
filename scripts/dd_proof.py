"""Auditable proof of the headline drawdown number.

    python -m scripts.dd_proof

Writes output/Drawdown Proof.xlsx for the EMA account (the whole universe, Rs250,000,
1% risk, class close-only convention) and its headline true max drawdown:

    How To Check  -- instructions for auditing this file by hand
    Equity Curve  -- every trading day 2006-2026: cash and position value are
                     hard numbers, but equity, the running peak, the daily
                     drawdown and the MAX DRAWDOWN cell are LIVE FORMULAS --
                     Excel itself does the drawdown arithmetic, not our code
    Trough X-Ray  -- the worst day and its preceding peak day, every open
                     position listed with that day's Kite close, so any line
                     can be spot-checked on TradingView
    Trade Ledger  -- all taken trades, with profit as live formulas

The script re-walks the account day by day, independently of
portfolio.daily_curve, and refuses to write the file unless its equity numbers
match the engine's to the paisa.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from kitelab import portfolio, report
from kitelab.backtest import charges

CACHE = Path(__file__).resolve().parent.parent / "data" / "signal_cache"
CAPITAL = 250_000.0
RISK = 0.01

HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
FORMULA_FONT = Font(color="0000AA")


def account_walk(taken: list[dict], capital: float):
    """Day-by-day cash / position-value split, plus position snapshots."""
    symbols = sorted({t["symbol"] for t in taken})
    start = np.datetime64(pd.Timestamp(min(t["entry_ts"] for t in taken)).normalize(), "ns")
    end = np.datetime64(pd.Timestamp(max(t["exit_ts"] for t in taken)).normalize(), "ns")
    calendar = np.unique(np.concatenate([portfolio._daily_closes(s)[0] for s in symbols]))
    calendar = calendar[(calendar >= start) & (calendar <= end)]
    aligned = {}
    for s in symbols:
        ts, close = portfolio._daily_closes(s)
        idx = np.searchsorted(ts, calendar, side="right") - 1
        aligned[s] = np.where(idx >= 0, close[np.maximum(idx, 0)], np.nan)

    def pos_of(stamp):
        return int(np.searchsorted(calendar, np.datetime64(pd.Timestamp(stamp).normalize(), "ns")))

    entries, exits = {}, {}
    for t in taken:
        e = dict(t)
        e["_in"], e["_out"] = pos_of(t["entry_ts"]), pos_of(t["exit_ts"])
        entries.setdefault(e["_in"], []).append(e)
        exits.setdefault(e["_out"], []).append(e)

    cash = capital
    open_pos: dict[str, dict] = {}
    days = []
    snapshots = {}
    for i in range(len(calendar)):
        for t in exits.get(i, ()):
            if t["_in"] < i:
                proceeds = t["shares"] * t["exit_price"]
                cash += proceeds - charges(t["shares"] * t["entry_price"], proceeds,
                                           t["same_session"])
                del open_pos[t["symbol"]]
        for t in entries.get(i, ()):
            cash -= t["shares"] * t["entry_price"]
            open_pos[t["symbol"]] = t
        for t in exits.get(i, ()):
            if t["_in"] == i:
                proceeds = t["shares"] * t["exit_price"]
                cash += proceeds - charges(t["shares"] * t["entry_price"], proceeds,
                                           t["same_session"])
                del open_pos[t["symbol"]]
        value = sum(t["shares"] * aligned[s][i] for s, t in open_pos.items())
        day = pd.Timestamp(calendar[i])
        days.append((day, cash, value))
        snapshots[day] = [(s, t["entry_ts"], t["entry_price"], t["shares"],
                           float(aligned[s][i])) for s, t in sorted(open_pos.items())]
    return days, snapshots


def main() -> None:
    signals = pickle.loads((CACHE / "EMA_all.pkl").read_bytes())
    n_stocks = len({t["symbol"] for t in signals})
    r = portfolio.run(signals, CAPITAL, RISK)
    days, snapshots = account_walk(r["taken"], CAPITAL)

    # refuse to publish unless the independent walk matches the engine exactly
    engine = {d: e for d, e in r["curve"]}
    worst = max(abs(cash + value - engine[day]) for day, cash, value in days)
    assert worst < 1e-6, f"equity mismatch up to Rs{worst}"
    print(f"  walk matches engine on {len(days):,} days (max diff Rs{worst:.9f})")
    peak_day, trough_day = r["dd_peak_date"], r["dd_trough_date"]
    print(f"  headline: {r['max_drawdown_pct']:.2f}% ({peak_day.date()} -> {trough_day.date()})")

    book = Workbook()
    # Every formula below also carries the value Python already computed, so the
    # blue columns read without Excel recalculating them (report.FormulaValues).
    # This file wrote 20,493 blank formula cells before that existed.
    values = report.FormulaValues()
    book.remove(book.active)

    sheet = book.create_sheet("How To Check")
    sheet.column_dimensions["A"].width = 110
    lines = [
        ("THE CLAIM BEING PROVEN", True),
        (f"One account, Rs2,50,000, trading the 20-EMA stack on {n_stocks} NSE stocks at 1% "
         f"risk per trade (class convention: stop checked at closes only), suffered a "
         f"maximum drawdown of {r['max_drawdown_pct']:.1f}%: from its equity peak on "
         f"{peak_day.date()} to its low on {trough_day.date()}.", False),
        ("", False),
        ("CHECK 1 -- LET EXCEL DO THE MATH (no trust in our code needed)", True),
        ("On the Equity Curve sheet, only Cash and Positions Value are typed-in "
         "numbers. Equity, Running Peak, Drawdown % and the MAX DRAWDOWN cell at the "
         "top are ordinary Excel formulas (shown in blue) that YOUR copy of Excel "
         "recalculates. Change any number and everything updates. The drawdown "
         "arithmetic is therefore checkable by inspection: peak = MAX(previous peak, "
         "today's equity); drawdown = equity/peak - 1.", False),
        ("", False),
        ("CHECK 2 -- SPOT-CHECK REAL PRICES (no trust in our data needed)", True),
        ("The Trough X-Ray sheet lists every open position on the peak day and on the "
         "worst day, with the closing price used. Open any of those stocks on "
         "TradingView or Kite, look up the close on that date, and compare. Prices "
         "are official NSE closes from Zerodha Kite; TradingView may differ by "
         "paise. Shares x close summed with cash gives the equity -- as a formula.", False),
        ("", False),
        ("CHECK 3 -- AUDIT THE TRADES THEMSELVES", True),
        ("The Trade Ledger sheet has all the trades the account took: entry date and "
         "price, stop, exit date and price, shares, fees. Profit columns are live "
         "formulas. Any single trade can be replayed on a chart: entry is the close "
         "of the signal day, the stop is the entry day's low CHECKED AT CLOSES ONLY "
         "(class convention), exits follow the 2%-below-any-EMA rule or a close at/below "
         "the stop.", False),
        ("", False),
        ("WHY THE OLD, SMALLER NUMBER WAS WRONG", True),
        (f"Earlier reports said about {r['legacy_max_drawdown_pct']:.0f}% for this account. That method only looked "
         "at equity when a trade settled, valued open positions at what was PAID for "
         "them (so a stock down 40% still counted at full cost), and divided the "
         "worst rupee dip by the final peak instead of the peak at the time. Marking "
         "positions to market every day -- which is what a real account statement "
         "does -- is what produces the headline number.", False),
        ("", False),
        ("HONEST CAVEATS", True),
        (f"The stock universe is today's surviving {n_stocks} stocks (survivorship bias: the "
         "real 2008 would have been WORSE). No slippage is modelled. Fees use "
         "Zerodha's delivery charge model.", False),
    ]
    for row, (text, bold) in enumerate(lines, start=1):
        cell = sheet.cell(row=row, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True)

    # ---- equity curve with live formulas ----
    sheet = book.create_sheet("Equity Curve")
    sheet["A1"] = ("Only Cash and Positions Value are numbers. Everything in blue is a "
                   "formula your Excel recalculates.")
    sheet["A2"] = "MAX DRAWDOWN"
    sheet["A2"].font = Font(bold=True)
    first_data = 5
    last_data = first_data + len(days) - 1
    # The same walk the rows below perform, run once up front so the headline cell
    # can carry its own cached value. It is also a free cross-check: this MIN must
    # equal the drawdown the engine reports.
    _peak = None
    dd_series = []
    for _day, _cash, _value in days:
        _equity = round(_cash, 2) + round(_value, 2)
        _peak = _equity if _peak is None else max(_peak, _equity)
        dd_series.append(_equity / _peak - 1)
    b2 = values.write(sheet, 2, 2, f"=MIN(F{first_data}:F{last_data})",
                      min(dd_series), "0.00%")
    b2.font = Font(bold=True, color="C00000")
    sheet["C2"] = f"reached on {trough_day.date()}, measured from the {peak_day.date()} peak"
    headers = ["Date", "Cash", "Positions Value", "Equity", "Running Peak", "Drawdown %"]
    widths = [12, 14, 15, 14, 14, 12]
    for col, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = sheet.cell(row=4, column=col, value=h)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        sheet.column_dimensions[get_column_letter(col)].width = w
    for offset, (day, cash, value) in enumerate(days):
        row = first_data + offset
        sheet.cell(row=row, column=1, value=day).number_format = "yyyy-mm-dd"
        # Cash and Positions Value are STORED rounded to the paisa, and every formula
        # on the row reads those stored cells. So the cached values are derived from
        # the rounded numbers too -- otherwise the sheet disagrees with itself by
        # ~3e-8 the moment Excel recalculates it.
        cash_r, value_r = round(cash, 2), round(value, 2)
        sheet.cell(row=row, column=2, value=cash_r).number_format = "#,##0"
        sheet.cell(row=row, column=3, value=value_r).number_format = "#,##0"
        equity = cash_r + value_r
        running_peak = equity if offset == 0 else max(running_peak, equity)
        eq = values.write(sheet, row, 4, f"=B{row}+C{row}", equity, "#,##0")
        pk = values.write(sheet, row, 5,
                          f"=MAX(E{row-1},D{row})" if offset else f"=D{row}",
                          running_peak, "#,##0")
        dd = values.write(sheet, row, 6, f"=D{row}/E{row}-1",
                          equity / running_peak - 1, "0.00%")
        for cell in (eq, pk, dd):
            cell.font = FORMULA_FONT
    sheet.freeze_panes = "A5"

    # ---- trough x-ray ----
    sheet = book.create_sheet("Trough X-Ray")
    sheet.column_dimensions["A"].width = 14
    for col, w in zip("BCDEF", (12, 12, 10, 14, 15)):
        sheet.column_dimensions[col].width = w
    row = 1
    for label, day in (("THE PEAK", peak_day), ("THE WORST DAY", trough_day)):
        cell = sheet.cell(row=row, column=1,
                          value=f"{label}: {day.date()} -- every open position, at that "
                                f"day's official NSE close")
        cell.font = Font(bold=True)
        row += 1
        for col, h in enumerate(["Stock", "Entry Date", "Entry Price", "Shares",
                                 "Close That Day", "Value (=shares x close)"], start=1):
            c = sheet.cell(row=row, column=col, value=h)
            c.font = Font(bold=True)
            c.fill = HEADER_FILL
        row += 1
        first = row
        block_total = 0.0
        for symbol, entry_ts, entry_price, shares, close in snapshots[day]:
            sheet.cell(row=row, column=1, value=symbol)
            sheet.cell(row=row, column=2,
                       value=pd.Timestamp(entry_ts).date()).number_format = "yyyy-mm-dd"
            sheet.cell(row=row, column=3, value=round(entry_price, 2)).number_format = "0.00"
            sheet.cell(row=row, column=4, value=shares)
            sheet.cell(row=row, column=5, value=round(close, 2)).number_format = "0.00"
            v = values.write(sheet, row, 6, f"=D{row}*E{row}",
                             round(shares * close, 2), "#,##0")
            v.font = FORMULA_FONT
            block_total += shares * close
            row += 1
        cash_that_day = next(c for d, c, _ in days if d == day)
        sheet.cell(row=row, column=5, value="Cash").font = Font(bold=True)
        sheet.cell(row=row, column=6, value=round(cash_that_day, 2)).number_format = "#,##0"
        row += 1
        sheet.cell(row=row, column=5, value="EQUITY").font = Font(bold=True)
        block_equity = block_total + cash_that_day
        tot = values.write(sheet, row, 6, f"=SUM(F{first}:F{row-1})",
                           round(block_equity, 2), "#,##0")
        tot.font = Font(bold=True, color="0000AA")
        if label == "THE PEAK":
            peak_total_row = row
            peak_equity = block_equity
        else:
            drop = sheet.cell(row=row + 1, column=5, value="FALL FROM PEAK")
            drop.font = Font(bold=True)
            pc = values.write(sheet, row + 1, 6, f"=F{row}/F{peak_total_row}-1",
                              round(block_equity / peak_equity - 1, 12), "0.00%")
            pc.font = Font(bold=True, color="C00000")
        row += 3

    # ---- trade ledger ----
    sheet = book.create_sheet("Trade Ledger")
    headers = ["#", "Stock", "Entry Date", "Entry Price", "Stop", "Exit Date",
               "Exit Price", "Shares", "Fees", "Gross Profit", "Net Profit"]
    widths = [7, 12, 12, 11, 11, 12, 11, 9, 10, 12, 12]
    for col, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = sheet.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        sheet.column_dimensions[get_column_letter(col)].width = w
    ledger = sorted(r["taken"], key=lambda t: t["entry_ts"])
    for number, t in enumerate(ledger, start=1):
        row = number + 1
        fee = charges(t["shares"] * t["entry_price"], t["shares"] * t["exit_price"],
                      t["same_session"])
        sheet.cell(row=row, column=1, value=number)
        sheet.cell(row=row, column=2, value=t["symbol"])
        sheet.cell(row=row, column=3,
                   value=pd.Timestamp(t["entry_ts"]).date()).number_format = "yyyy-mm-dd"
        sheet.cell(row=row, column=4, value=round(t["entry_price"], 2)).number_format = "0.00"
        sheet.cell(row=row, column=5, value=round(t["stop"], 2)).number_format = "0.00"
        sheet.cell(row=row, column=6,
                   value=pd.Timestamp(t["exit_ts"]).date()).number_format = "yyyy-mm-dd"
        sheet.cell(row=row, column=7, value=round(t["exit_price"], 2)).number_format = "0.00"
        sheet.cell(row=row, column=8, value=t["shares"])
        sheet.cell(row=row, column=9, value=round(fee, 2)).number_format = "0.00"
        gross = (t["exit_price"] - t["entry_price"]) * t["shares"]
        g = values.write(sheet, row, 10, f"=(G{row}-D{row})*H{row}",
                         round(gross, 2), "#,##0.00")
        n = values.write(sheet, row, 11, f"=J{row}-I{row}",
                         round(gross - fee, 2), "#,##0.00")
        for cell in (g, n):
            cell.font = FORMULA_FONT
    sheet.freeze_panes = "A2"

    target = report.save(book, "Drawdown Proof.xlsx")
    values.inject(target, book)
    print(f"  written: {target}")


if __name__ == "__main__":
    main()

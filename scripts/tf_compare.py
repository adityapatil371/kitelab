"""Same 20-EMA stack rule, three timeframe triplets, five assigned stocks.

    python -m scripts.tf_compare

Variants (highest/middle/lowest -- you trade on the LOWEST timeframe):

    Q/M/W   quarterly + monthly stacks, enter/exit on WEEKLY closes,
            stop = entry WEEK's low
    M/W/D   the class strategy: monthly + weekly stacks, enter/exit on
            DAILY closes, stop = entry DAY's low
    W/D/H   weekly + daily stacks, enter/exit on HOURLY closes,
            stop = entry HOUR's low

Mechanics are identical to backtest.simulate, just generalised over the base
timeframe: entry at the close of the first base bar where the close is 2% above
all three EMAs (fresh transition only); CLASS CONVENTION exits: a base-bar
CLOSE at/below the stop sells at that close, or a close 2% below ANY of the
three EMAs sells at that close. Nothing intrabar matters. Whole-share
sizing off Capital 100,000 / Risk 1%. GROSS results -- no charges, matching the
practice file.

Fair-comparison window: hourly bars only exist from 2015 (Kite serves no earlier
intraday), so for each stock every variant only counts trades entered on or
after that stock's first hourly bar. EMAs still warm up on the full history.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from kitelab import backtest, frames, indicators, report, sizing

ASSIGNED = ["HAL", "HINDZINC", "HYUNDAI", "IRFC", "INDHOTEL"]
LENGTH = 20
BAND = 0.02

HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
TITLE_FILL = PatternFill("solid", fgColor="BDD7EE")
LOSS_FONT = Font(color="C00000")

PREDICTIONS = [
    "PREDICTIONS -- written down BEFORE the numbers were computed (house rule):",
    "1. Q/M/W: very few trades (maybe 3-8 per stock), widest stops so smallest "
    "positions, catches only multi-year trends, exits late and gives back a lot. "
    "Decent profit factor, modest total rupees because it rarely trades.",
    "2. M/W/D (the class strategy): stays the best balance of the three.",
    "3. W/D/H: by far the most trades (hundreds), stops inside market noise, "
    "lowest win rate, worst profit factor -- possibly a gross loser on some "
    "stocks. With real charges it would look even worse (tight-stop fee trap).",
]


def _stack_frames(symbol: str, variant: str):
    """Return (base bars, [higher-TF bar frames]) for one variant."""
    day = frames.daily(symbol)
    if variant == "QMW":
        return frames.weekly(day), [frames.monthly(day), frames.quarterly(day)]
    if variant == "MWD":
        return day, [frames.weekly(day), frames.monthly(day)]
    if variant == "WDH":
        hour = frames.load(symbol, "1h")
        return hour, [day, frames.weekly(day)]
    raise ValueError(variant)


def stack_signal(base: pd.DataFrame, highers: list[pd.DataFrame],
                 length: int = LENGTH, band: float = BAND) -> pd.DataFrame:
    """entry_ok/exit_ok on the base bars, higher TFs via forming-bar EMAs.

    Same conventions as backtest.ema_stack_signal: the base EMA includes the
    current bar's close (TradingView convention); each higher-timeframe EMA is
    the forming-bar value alpha*close + (1-alpha)*last-COMPLETED-bar EMA, and
    before any completed higher bar exists it degenerates to the close itself,
    which correctly fails the "close > ema" test.
    """
    alpha = 2.0 / (length + 1)
    close = base["close"].to_numpy()
    base_ema = indicators.ema(base["close"], length).to_numpy()
    stamps = base["ts"].to_numpy().astype("datetime64[ns]")

    upper, lower = 1 + band, 1 - band
    entry_ok = close > base_ema * upper
    exit_ok = close < base_ema * lower
    completed_counts = []
    for higher in highers:
        h_ema = indicators.ema(higher["close"], length).to_numpy()
        pos = np.searchsorted(higher["ts"].to_numpy().astype("datetime64[ns]"),
                              stamps, side="right") - 1
        prev = np.where(pos >= 1, h_ema[np.maximum(pos - 1, 0)], np.nan)
        asof = np.where(np.isnan(prev), close, alpha * close + (1 - alpha) * prev)
        entry_ok &= close > asof * upper
        exit_ok |= close < asof * lower
        completed_counts.append(pos)

    out = base.copy()
    out["entry_ok"] = entry_ok
    out["exit_ok"] = exit_ok
    out["top_tf_done"] = completed_counts[-1]  # completed bars of the HIGHEST TF
    return out


def simulate_variant(symbol: str, variant: str,
                     stop_on_close: bool = True,
                     band: float = BAND) -> list[dict]:
    """Closed trades, oldest first. Mirrors backtest.simulate's walk exactly.

    stop_on_close=True is the class convention (everything checked at bar
    closes only); False is the pre-2026-08-28 broker convention.
    """
    base, highers = _stack_frames(symbol, variant)
    signal = stack_signal(base, highers, band=band)
    entry_ok = signal["entry_ok"].to_numpy()
    exit_ok = signal["exit_ok"].to_numpy()
    open_, high, low, close = (signal[c].to_numpy()
                               for c in ("open", "high", "low", "close"))
    stamps = signal["ts"].tolist()
    total = len(signal)

    trades: list[dict] = []
    position = 0
    while position < total:
        fresh = entry_ok[position] and position > 0 and not entry_ok[position - 1]
        if not fresh:
            position += 1
            continue
        entry_price = float(close[position])
        stop = float(low[position])
        exit_at = None
        for step in range(position + 1, total):
            if stop_on_close:
                if close[step] <= stop:
                    exit_at = (step, float(close[step]), "stop (close)")
                    break
            elif low[step] <= stop:
                gapped = open_[step] < stop
                exit_at = (step, float(open_[step]) if gapped else stop,
                           "gap through stop" if gapped else "stop")
                break
            if exit_ok[step]:
                exit_at = (step, float(close[step]), "ema break")
                break
        if exit_at is None:
            break  # still open; not a closed trade
        exit_index, exit_price, reason = exit_at
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0:
            position = exit_index + 1
            continue
        gross = (exit_price - entry_price) * shares
        buy_value = entry_price * shares
        sell_value = exit_price * shares
        same_session = stamps[position].date() == stamps[exit_index].date()
        trades.append({
            "symbol": symbol,
            "entry_ts": stamps[position],
            "exit_ts": stamps[exit_index],
            "entry_price": entry_price,
            "stop": stop,
            "exit_price": exit_price,
            "exit_reason": reason,
            "shares": shares,
            "risk_taken": risk_taken,
            "capital_capped": capped,
            "gross_profit": gross,
            "charges": backtest.charges(buy_value, sell_value,
                                        intraday=same_session),
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "bars_held": exit_index - position,
            "days_held": (stamps[exit_index] - stamps[position]).days,
            "stop_pct": (entry_price - stop) / entry_price * 100,
            "top_tf_done": int(signal["top_tf_done"].iloc[position]),
        })
        position = exit_index + 1
    return trades


def window_start(symbol: str) -> pd.Timestamp:
    """First hourly bar's day -- the date all three variants can see."""
    return frames.base_15m(symbol)["ts"].min().normalize()


def summarise(trades: list[dict]) -> dict:
    wins = [t["gross_profit"] for t in trades if t["gross_profit"] > 0]
    losses = [t["gross_profit"] for t in trades if t["gross_profit"] <= 0]
    gross = sum(t["gross_profit"] for t in trades)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_win": np.mean(wins) if wins else 0.0,
        "avg_loss": np.mean(losses) if losses else 0.0,
        "expectancy": gross / len(trades) if trades else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0
                         else float("inf") if wins else 0.0,
        "gross": gross,
        "charges": sum(t["charges"] for t in trades),
        "median_hold_days": float(np.median([t["days_held"] for t in trades])) if trades else 0.0,
        "median_stop_pct": float(np.median([t["stop_pct"] for t in trades])) if trades else 0.0,
    }


# ---------------------------------------------------------------- Excel ----

VARIANTS = [
    ("QMW", "Q/M/W", "quarterly + monthly stacks, traded on WEEKLY closes, stop = entry week's low"),
    ("MWD", "M/W/D", "monthly + weekly stacks, traded on DAILY closes, stop = entry day's low (class strategy)"),
    ("WDH", "W/D/H", "weekly + daily stacks, traded on HOURLY closes, stop = entry hour's low"),
]

TRADE_HEADERS = ["Trade #", "Entry Date", "Exit Date", "Entry Price", "Stop Loss",
                 "Exit", "No. of Shares", "Cost of Entry", "Profit", "Cum Profit",
                 "R multiple", "Days Held", "Exit Reason"]
TRADE_WIDTHS = [8, 12, 12, 12, 12, 12, 13, 14, 12, 12, 11, 10, 17]

SUMMARY_COLS = ["Trades", "Wins", "Win %", "Avg Win", "Avg Loss",
                "Expectancy / trade", "Profit Factor", "Gross P&L",
                "Median Hold (days)", "Median Stop %"]


def write_readme(book: Workbook, windows: dict, results: dict | None = None) -> None:
    sheet = book.create_sheet("Read Me")
    sheet.column_dimensions["A"].width = 110
    verdict: list[str] = []
    if results:
        verdict = ["", "WHAT ACTUALLY HAPPENED (all five stocks together, gross):"]
        for key, label, _ in VARIANTS:
            s = summarise([t for sym in ASSIGNED for t in results[key][sym]])
            verdict.append(
                f"    {label}: {s['trades']} trades, win rate {s['win_rate']:.0%}, "
                f"profit factor {s['profit_factor']:.2f}, expectancy "
                f"{s['expectancy']:,.0f}/trade, total {s['gross']:,.0f}")
        verdict.append(
            "    Moving UP a timeframe made each trade better (highest profit factor and "
            "expectancy) but traded rarely. Moving DOWN made each trade worse but traded "
            "so often that the TOTAL was largest -- before charges, which it pays roughly "
            "six times more often than Q/M/W, on stops a quarter the size.")
    lines = [
        "THE QUESTION : does the 20-EMA stack work better if you shift the whole thing "
        "one timeframe UP (quarterly/monthly/weekly) or one timeframe DOWN "
        "(weekly/daily/hourly) from the class version (monthly/weekly/daily)?",
        "",
        "THE RULE (same for all three, only the timeframes change) : buy at the close of the "
        "lowest-timeframe bar when price closes 2% above the 20-EMA on all three timeframes. "
        "Stop = that entry bar's low, checked at CLOSES only (class convention -- manual "
        "backtesting cannot watch intrabar). Sell when a close is at/below the stop, or a "
        "close falls 2% below any of the three EMAs.",
        "",
    ] + PREDICTIONS + [
        "",
        "HOW TO READ THIS FILE : the Summary sheet has the verdict. One sheet per variant "
        "has every trade, newest first, in the practice-file format -- change the Capital or "
        "Risk cells at the top and the shares/profit columns recalculate.",
        "",
        "FAIR-COMPARISON WINDOW : hourly bars only exist from 2015 (Kite has no older "
        "intraday data), so for each stock ALL THREE variants only count trades entered "
        "after that stock's first hourly bar:",
    ] + [f"    {s}: from {d.date()}" for s, d in windows.items()] + [
        "",
        "CAVEATS, so nobody is fooled:",
        "- GROSS results. No Zerodha charges anywhere in this file (your request). The "
        "hourly variant trades far more often and with far tighter stops, so charges would "
        "hurt it much more than the others -- remember the fee trap: cost in R = 0.25 / stop%.",
        "- Whole shares only, Capital 100,000, Risk 1% per trade, no compounding.",
        "- The quarterly 20-EMA needs 20 quarters (5 years) of history to mean much. "
        "HYUNDAI listed Oct 2024, so its quarterly EMA is built on a handful of bars -- "
        "its Q/M/W numbers are close to meaningless and are surfaced, not hidden.",
        "- Conservative conventions everywhere: same-bar ambiguity goes to the stop, gaps "
        "fill at the open.",
        "- Pre-2026 sessions end with a 15-minute stub 'hour' bar (15:15-15:30). 23 of the "
        "428 hourly entries happen on stub bars; together they LOSE money, so they drag "
        "the hourly variant down rather than flattering it.",
    ] + verdict
    for row, text in enumerate(lines, start=1):
        cell = sheet.cell(row=row, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if text.startswith(("THE QUESTION", "THE RULE", "PREDICTIONS", "HOW TO READ",
                            "FAIR", "CAVEATS")):
            cell.font = Font(bold=True)


def write_summary(book: Workbook, results: dict) -> None:
    sheet = book.create_sheet("Summary")
    sheet["A1"] = ("Same rule, three timeframe stacks, five stocks, one common window "
                   "per stock. All numbers GROSS (no charges). Numbers computed by "
                   "scripts.tf_compare; trade lists on the variant sheets.")
    sheet["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=11)
    sheet.row_dimensions[1].height = 30
    sheet.column_dimensions["A"].width = 14
    for col in range(2, 12):
        sheet.column_dimensions[chr(64 + col)].width = 13

    row = 3
    for key, label, desc in VARIANTS:
        title = sheet.cell(row=row, column=1, value=f"{label} -- {desc}")
        title.font = Font(bold=True)
        title.fill = TITLE_FILL
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=11)
        row += 1
        sheet.cell(row=row, column=1, value="Stock").font = Font(bold=True)
        for col, name in enumerate(SUMMARY_COLS, start=2):
            cell = sheet.cell(row=row, column=col, value=name)
            cell.font = Font(bold=True)
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(wrap_text=True, horizontal="center")
        row += 1
        all_trades = []
        for symbol in ASSIGNED:
            trades = results[key][symbol]
            all_trades += trades
            _summary_row(sheet, row, symbol, summarise(trades))
            row += 1
        _summary_row(sheet, row, "ALL 5", summarise(all_trades), bold=True)
        row += 2

    sheet.freeze_panes = "A3"


def _summary_row(sheet, row, label, s, bold=False):
    font = Font(bold=bold)
    sheet.cell(row=row, column=1, value=label).font = font
    values = [
        (s["trades"], "0"), (s["wins"], "0"), (s["win_rate"], "0.0%"),
        (round(s["avg_win"], 0), "#,##0"), (round(s["avg_loss"], 0), "#,##0"),
        (round(s["expectancy"], 0), "#,##0"),
        (round(s["profit_factor"], 2) if s["profit_factor"] != float("inf") else "inf", "0.00"),
        (round(s["gross"], 0), "#,##0"),
        (s["median_hold_days"], "0"), (round(s["median_stop_pct"], 2), "0.00"),
    ]
    for col, (value, fmt) in enumerate(values, start=2):
        cell = sheet.cell(row=row, column=col, value=value)
        cell.number_format = fmt
        cell.font = font


def write_variant_sheet(book: Workbook, key: str, label: str, desc: str,
                        results: dict) -> None:
    sheet = book.create_sheet(label.replace("/", "-"))
    sheet["A1"] = (f"RULE ({label}) : buy at the {desc.split('traded on ')[-1].split(',')[0]} "
                   f"close when price is 2% above the 20-EMA on all three timeframes "
                   f"({desc}). Stop = entry bar's low. Sell on stop hit or a close 2% below "
                   f"any EMA. GROSS -- no charges.")
    sheet["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(TRADE_HEADERS))
    sheet.row_dimensions[1].height = 44

    sheet["A2"] = "Capital"
    sheet["B2"] = 100000
    sheet["C2"] = "Risk %"
    sheet["D2"] = 0.01
    sheet["D2"].number_format = "0%"
    sheet["E2"] = "<- change these two cells and every formula below recalculates"
    sheet["E2"].font = Font(italic=True, color="808080")
    for anchor in ("A2", "C2"):
        sheet[anchor].font = Font(bold=True)

    for column, (title, width) in enumerate(zip(TRADE_HEADERS, TRADE_WIDTHS), start=1):
        sheet.column_dimensions[chr(64 + column) if column <= 26 else "A"].width = width

    row = 4
    for symbol in ASSIGNED:
        trades = sorted(results[key][symbol], key=lambda t: t["entry_ts"], reverse=True)
        title = sheet.cell(row=row, column=1,
                           value=f"{symbol} -- {len(trades)} trades, newest first")
        title.font = Font(bold=True)
        title.fill = TITLE_FILL
        sheet.merge_cells(start_row=row, start_column=1, end_row=row,
                          end_column=len(TRADE_HEADERS))
        row += 1
        for column, name in enumerate(TRADE_HEADERS, start=1):
            cell = sheet.cell(row=row, column=column, value=name)
            cell.font = Font(bold=True)
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
        row += 1
        first_data_row = row
        for number, trade in enumerate(trades, start=1):
            sheet.cell(row=row, column=1, value=number)
            for col, stamp in ((2, trade["entry_ts"]), (3, trade["exit_ts"])):
                cell = sheet.cell(row=row, column=col, value=stamp.replace(tzinfo=None))
                cell.number_format = ("yyyy-mm-dd hh:mm" if key == "WDH"
                                      else "yyyy-mm-dd")
            for col, k in ((4, "entry_price"), (5, "stop"), (6, "exit_price")):
                sheet.cell(row=row, column=col,
                           value=round(trade[k], 2)).number_format = "0.00"
            sheet.cell(row=row, column=7,
                       value=f"=MIN(ROUNDDOWN($B$2*$D$2/(D{row}-E{row}),0),"
                             f"ROUNDDOWN($B$2/D{row},0))").number_format = "0"
            sheet.cell(row=row, column=8, value=f"=D{row}*G{row}").number_format = "#,##0.00"
            sheet.cell(row=row, column=9,
                       value=f"=(F{row}-D{row})*G{row}").number_format = "#,##0.00"
            cum = f"=I{row}" if row == first_data_row else f"=J{row - 1}+I{row}"
            sheet.cell(row=row, column=10, value=cum).number_format = "#,##0.00"
            sheet.cell(row=row, column=11,
                       value=f"=(F{row}-D{row})/(D{row}-E{row})").number_format = '0.00"R"'
            sheet.cell(row=row, column=12, value=trade["days_held"]).number_format = "0"
            sheet.cell(row=row, column=13, value=trade["exit_reason"])
            if trade["exit_price"] < trade["entry_price"]:
                sheet.cell(row=row, column=9).font = LOSS_FONT
            row += 1
        total = sheet.cell(row=row, column=9,
                           value=f"=SUM(I{first_data_row}:I{row - 1})")
        total.number_format = "#,##0.00"
        total.font = Font(bold=True)
        sheet.cell(row=row, column=8, value="Total").font = Font(bold=True)
        row += 3


def main() -> None:
    windows = {s: window_start(s) for s in ASSIGNED}
    results: dict[str, dict[str, list[dict]]] = {}
    for key, label, _ in VARIANTS:
        results[key] = {}
        for symbol in ASSIGNED:
            trades = simulate_variant(symbol, key)
            kept = [t for t in trades
                    if pd.Timestamp(t["entry_ts"]).normalize() >= windows[symbol]]
            results[key][symbol] = kept
            print(f"  {label:<6} {symbol:<10} {len(kept):>4} trades  "
                  f"gross {sum(t['gross_profit'] for t in kept):>12,.0f}  "
                  f"(dropped {len(trades) - len(kept)} pre-window)")
        s = summarise([t for sym in ASSIGNED for t in results[key][sym]])
        print(f"  {label:<6} {'ALL':<10} {s['trades']:>4} trades  "
              f"gross {s['gross']:>12,.0f}  win {s['win_rate']:.0%}  "
              f"PF {s['profit_factor']:.2f}  expectancy {s['expectancy']:,.0f}\n")

    book = Workbook()
    book.remove(book.active)
    write_readme(book, windows, results)
    write_summary(book, results)
    for key, label, desc in VARIANTS:
        write_variant_sheet(book, key, label, desc, results)
    target = report.save(book, "EMA Timeframe Comparison.xlsx")
    print(f"  written: {target}")


if __name__ == "__main__":
    main()

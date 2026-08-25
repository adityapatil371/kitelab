"""One workbook containing every analysis run on the 199-stock universe.

    python -m scripts.full_report

Everything is recomputed fresh from the Parquet files at build time, so all sheets
describe the same data snapshot. Takes several minutes -- the breakout scan over 199
stocks is the slow part.
"""
from __future__ import annotations

import statistics as st

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from kitelab import backtest, config, frames, portfolio, report, strategies

HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
LOSS_FONT = Font(color="C00000")

BUCKETS = [(0, 1e6, "under Rs10 lakh/day"), (1e6, 1e7, "Rs10L - Rs1cr"),
           (1e7, 1e8, "Rs1cr - Rs10cr"), (1e8, 1e15, "over Rs10cr/day")]
CAPITALS = [10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000,
            2_500_000, 5_000_000, 10_000_000]
RISKS = [0.01, 0.02, 0.03, 0.05, 0.10]
BANDS = [0.0, 0.005, 0.01, 0.02, 0.03, 0.05]
PEAK, BOTTOM, YEAREND = (pd.Timestamp("2020-01-14"), pd.Timestamp("2020-03-23"),
                         pd.Timestamp("2020-12-31"))

_TURNOVER: dict[str, float] = {}


def turnover(symbol: str) -> float:
    if symbol not in _TURNOVER:
        try:
            daily = frames.daily(symbol).tail(500)
            _TURNOVER[symbol] = float(st.median(daily["close"] * daily["volume"]))
        except Exception:
            _TURNOVER[symbol] = 0.0
    return _TURNOVER[symbol]


def buy_hold_cagr(symbol: str) -> float | None:
    try:
        daily = frames.daily(symbol)
    except SystemExit:
        return None
    if len(daily) < 500:
        return None
    years = (daily["ts"].iloc[-1] - daily["ts"].iloc[0]).days / 365.25
    if years < 3 or daily["close"].iloc[0] <= 0:
        return None
    growth = daily["close"].iloc[-1] / daily["close"].iloc[0]
    return 100 * (growth ** (1 / years) - 1) if growth > 0 else None


def write_table(sheet, anchor_row: int, title: str, headers: list[str],
                widths: list[int], rows: list[list], formats: list[str]) -> int:
    """Write one titled table; returns the next free row."""
    cell = sheet.cell(row=anchor_row, column=1, value=title)
    cell.font = Font(bold=True, size=12)
    header_row = anchor_row + 1
    for column, (heading, width) in enumerate(zip(headers, widths), start=1):
        head = sheet.cell(row=header_row, column=column, value=heading)
        head.font = Font(bold=True)
        head.fill = HEADER_FILL
        head.alignment = Alignment(horizontal="center", wrap_text=True)
        letter = sheet.cell(row=1, column=column).column_letter
        sheet.column_dimensions[letter].width = max(
            sheet.column_dimensions[letter].width or 0, width)
    for offset, row in enumerate(rows):
        for column, (value, fmt) in enumerate(zip(row, formats), start=1):
            data = sheet.cell(row=header_row + 1 + offset, column=column, value=value)
            data.number_format = fmt
            if isinstance(value, (int, float)) and value < 0 and fmt != "@":
                data.font = LOSS_FONT
    return header_row + 1 + len(rows) + 2


def strategy_row(label: str, trades: list[dict]) -> list:
    s = report.stats(label, trades)
    return [label, s.get("trades", 0), s.get("win_rate_pct", 0), s.get("gross_profit", 0),
            s.get("charges", 0), s.get("net_profit", 0), s.get("expectancy", 0),
            s.get("profit_factor", 0), s.get("avg_r", 0), s.get("top_share_pct", 0)]


STRAT_HEADERS = ["Group", "Trades", "Win %", "Gross", "Charges", "Net",
                 "Expectancy", "Profit Factor", "Avg R", "Best Trade % of Gross"]
STRAT_WIDTHS = [22, 9, 8, 13, 12, 13, 11, 12, 8, 18]
STRAT_FORMATS = ["@", "0", "0.0", "#,##0", "#,##0", "#,##0", "#,##0", "0.00", "0.00", "0.0"]


def main() -> None:
    cfg = config.load()
    in_sample, out_sample = cfg.in_sample, cfg.out_of_sample
    everything = cfg.all_symbols

    print(f"\n  scanning {len(everything)} stocks (this is the slow part)...\n", flush=True)
    signals: dict[str, list[dict]] = {}
    for name, build in [("Breakout", lambda s: strategies.ath_breakout_trades(s, True)),
                        ("EMA", backtest.simulate)]:
        collected = []
        for index, symbol in enumerate(everything, 1):
            try:
                collected.extend(build(symbol))
            except SystemExit:
                pass
            if index % 40 == 0:
                print(f"    {name}: {index}/{len(everything)}", flush=True)
        signals[name] = collected
        print(f"    {name}: {len(collected):,} trades", flush=True)

    in_set, out_set = set(in_sample), set(out_sample)
    split = {name: {"in": [t for t in trades if t["symbol"] in in_set],
                    "out": [t for t in trades if t["symbol"] in out_set]}
             for name, trades in signals.items()}

    book = Workbook()
    book.remove(book.active)

    # ---- Strategy Results ------------------------------------------------
    sheet = book.create_sheet("Strategy Results")
    row = write_table(sheet, 1, "Breakout -- all-time-high, swing-low trailing stop",
                      STRAT_HEADERS, STRAT_WIDTHS,
                      [strategy_row(f"IN-SAMPLE ({len(in_sample)} stocks)", split["Breakout"]["in"]),
                       strategy_row(f"OUT-OF-SAMPLE ({len(out_sample)} stocks)", split["Breakout"]["out"])],
                      STRAT_FORMATS)
    row = write_table(sheet, row, "EMA -- 20-EMA stack, 2% band, entry-day-low stop",
                      STRAT_HEADERS, STRAT_WIDTHS,
                      [strategy_row(f"IN-SAMPLE ({len(in_sample)} stocks)", split["EMA"]["in"]),
                       strategy_row(f"OUT-OF-SAMPLE ({len(out_sample)} stocks)", split["EMA"]["out"])],
                      STRAT_FORMATS)
    sheet.cell(row=row, column=1, value=(
        "IN-SAMPLE = the 49 Nifty Next 50 stocks the parameters were tuned on; not evidence. "
        "OUT-OF-SAMPLE = 150 random NSE stocks never seen by any parameter. "
        "Both profit factors staying well above 1.0 out-of-sample is the overfitting test passing."))

    # ---- Liquidity gradient ---------------------------------------------
    sheet = book.create_sheet("Liquidity")
    row = 1
    for name in signals:
        rows = []
        for lo, hi, label in BUCKETS:
            bucket = [t for t in split[name]["out"] if lo <= turnover(t["symbol"]) < hi]
            if bucket:
                rows.append(strategy_row(label, bucket))
        row = write_table(sheet, row, f"{name} -- out-of-sample, split by liquidity",
                          STRAT_HEADERS, STRAT_WIDTHS, rows, STRAT_FORMATS)
    sheet.cell(row=row, column=1, value=(
        "The decisive evidence: profit factor RISES with liquidity in both strategies. "
        "Fake backtest edges do the opposite -- they live in illiquid stocks where "
        "simulated fills are fiction."))

    # ---- Band sweep ------------------------------------------------------
    sheet = book.create_sheet("Band Sweep")
    rows = []
    for band in BANDS:
        trades = []
        for symbol in in_sample:
            try:
                trades.extend(backtest.simulate(symbol, band=band))
            except SystemExit:
                pass
        s = report.stats(f"{band * 100:.1f}%", trades)
        rows.append([f"{band * 100:.1f}%", s["trades"],
                     st.median(t["bars_held"] for t in trades) if trades else 0,
                     s["gross_profit"], s["charges"], s["net_profit"],
                     s["expectancy"], s["profit_factor"]])
    row = write_table(sheet, 1, "EMA hysteresis band sweep (in-sample 49 stocks)",
                      ["Band", "Trades", "Median Hold (sessions)", "Gross", "Charges",
                       "Net", "Expectancy", "Profit Factor"],
                      [8, 9, 20, 13, 12, 13, 11, 12], rows,
                      ["@", "0", "0", "#,##0", "#,##0", "#,##0", "#,##0", "0.00"])
    sheet.cell(row=row, column=1, value=(
        "Why the 2% band exists: at 0% the entry and exit share one knife-edge, so price "
        "hovering at an EMA exits and re-enters every few days. Everything improves "
        "monotonically as the band widens -- the signature of a real effect. 2% was chosen "
        "as the balance of expectancy vs total profit; because it was chosen HERE, these "
        "49 stocks stop being evidence (see Strategy Results for the honest numbers)."))

    # ---- Portfolio simulation -------------------------------------------
    sheet = book.create_sheet("Portfolio Simulation")
    row = 1
    port_headers = ["Capital", "Risk", "Final", "Return %", "CAGR %", "Max DD %",
                    "Taken", "Skipped (too small)", "Skipped (no cash)"]
    port_widths = [12, 7, 13, 10, 9, 10, 8, 17, 15]
    port_formats = ["#,##0", "0%", "#,##0", "0", "0.0", "0.0", "0", "0", "0"]
    for name in signals:
        rows = []
        for capital in CAPITALS:
            r = portfolio.run(signals[name], capital, 0.01)
            rows.append([capital, 0.01, r["final"], r["return_pct"], r["cagr_pct"],
                         r["max_drawdown_pct"], len(r["taken"]) if isinstance(r["taken"], list) else r["taken"],
                         r["skipped_size"], r["skipped_cash"]])
        row = write_table(sheet, row, f"{name} -- one account, every affordable signal, 1% risk",
                          port_headers, port_widths, rows, port_formats)
    rows = []
    for capital in (10_000, 100_000):
        for risk in RISKS:
            r = portfolio.run(signals["Breakout"], capital, risk)
            rows.append([capital, risk, r["final"], r["return_pct"], r["cagr_pct"],
                         r["max_drawdown_pct"], len(r["taken"]) if isinstance(r["taken"], list) else r["taken"],
                         r["skipped_size"], r["skipped_cash"]])
    row = write_table(sheet, row, "Breakout -- raising risk instead of capital",
                      port_headers, port_widths, rows, port_formats)
    sheet.cell(row=row, column=1, value=(
        "The capital floor: below ~Rs50,000 both strategies destroy the account, because "
        "1% risk cannot afford most signals and the account is forced into the illiquid "
        "tail where the edge is negative. Raising risk % makes it worse, not better. "
        "Above ~Rs2.5 lakh, adding money buys nothing -- the one-position-per-stock rule "
        "binds, not cash."))

    # ---- 2020 crash ------------------------------------------------------
    sheet = book.create_sheet("2020 Crash")
    def equity_at(curve, when):
        prior = [e for ts, e in curve if ts <= when]
        return prior[-1] if prior else (curve[0][1] if curve else 0)
    rows = []
    for name in signals:
        r = portfolio.run(signals[name], 250_000, 0.01)
        e_peak = equity_at(r["curve"], PEAK)
        e_bot = equity_at(r["curve"], BOTTOM)
        e_end = equity_at(r["curve"], YEAREND)
        rows.append([name, e_peak, e_bot, 100 * (e_bot / e_peak - 1),
                     e_end, 100 * (e_end / e_peak - 1)])
    crash, recovery = [], []
    for symbol in everything:
        try:
            daily = frames.daily(symbol)
        except SystemExit:
            continue
        if daily["ts"].iloc[0] > PEAK:
            continue
        def px(when):
            sub = daily[daily.ts <= when]
            return float(sub["close"].iloc[-1]) if len(sub) else None
        a, b, c = px(PEAK), px(BOTTOM), px(YEAREND)
        if a and b and c:
            crash.append(100 * (b / a - 1))
            recovery.append(100 * (c / a - 1))
    rows.append([f"BUY & HOLD (median, {len(crash)} stocks)", None, None,
                 st.median(crash) if crash else 0, None,
                 st.median(recovery) if recovery else 0])
    row = write_table(sheet, 1, "COVID crash: 14 Jan 2020 peak -> 23 Mar bottom -> 31 Dec 2020",
                      ["Strategy", "Equity at Peak", "Equity at Bottom", "Peak->Bottom %",
                       "Equity Year End", "Peak->Year End %"],
                      [30, 14, 15, 14, 15, 15], rows,
                      ["@", "#,##0", "#,##0", "0.0", "#,##0", "0.0"])
    sheet.cell(row=row, column=1, value=(
        "The one bear market in the data. Buy-and-hold fell ~40%; the strategies were "
        "roughly flat to positive through it -- the stop/trend mechanism doing its job. "
        "They then lagged the V-shaped recovery, which is the worst possible shape for "
        "trend-following. A long grinding bear (2008-style) is NOT in this data."))

    # ---- Worst losses ----------------------------------------------------
    sheet = book.create_sheet("Worst Losses")
    worst = sorted(((t["r_multiple"], name, t) for name, trades in signals.items()
                    for t in trades), key=lambda x: x[0])[:10]
    rows = [[name, t["symbol"], t["entry_ts"].date(), r, t["exit_reason"],
             t["net_profit"]] for r, name, t in worst]
    row = write_table(sheet, 1, "Ten worst trades by R multiple (all 199 stocks, both strategies)",
                      ["Strategy", "Stock", "Entry Date", "R Multiple", "Exit Reason", "Net P&L"],
                      [11, 12, 12, 11, 20, 12], rows,
                      ["@", "@", "yyyy-mm-dd", "0.00", "@", "#,##0"])
    sheet.cell(row=row, column=1, value=(
        "Every entry here is an overnight gap through the stop -- the one risk a stop-loss "
        "cannot cover. This table is why risking 5% per trade is ruin: multiply the R "
        "column by your risk-per-trade to see what one gap does to the account."))

    # ---- Per stock -------------------------------------------------------
    sheet = book.create_sheet("Per Stock")
    by_symbol = {name: {} for name in signals}
    for name, trades in signals.items():
        for t in trades:
            by_symbol[name].setdefault(t["symbol"], []).append(t)
    rows = []
    for symbol in everything:
        entry = [symbol, "in-sample" if symbol in in_set else "out-of-sample",
                 turnover(symbol), buy_hold_cagr(symbol)]
        for name in ("Breakout", "EMA"):
            mine = by_symbol[name].get(symbol, [])
            s = report.stats(symbol, mine) if mine else {}
            entry += [len(mine), s.get("net_profit", 0), s.get("profit_factor", 0)]
        rows.append(entry)
    write_table(sheet, 1, f"All {len(everything)} stocks",
                ["Stock", "Group", "Median Turnover", "Buy&Hold CAGR %",
                 "B'out Trades", "B'out Net", "B'out PF",
                 "EMA Trades", "EMA Net", "EMA PF"],
                [13, 14, 15, 15, 11, 12, 9, 11, 12, 9], rows,
                ["@", "@", "#,##0", "0.0", "0", "#,##0", "0.00", "0", "#,##0", "0.00"])
    sheet.freeze_panes = "A3"

    # ---- Read Me ---------------------------------------------------------
    sheet = book.create_sheet("Read Me", 0)
    sheet.column_dimensions["A"].width = 118
    pf = {n: report.stats("x", split[n]["out"]).get("profit_factor", 0) for n in signals}
    story = [
        ("WHAT THIS WORKBOOK IS", True),
        ("Every analysis from the kitelab project, recomputed in one pass over 199 NSE stocks "
         "(49 Nifty Next 50 + 150 randomly drawn). Data: Zerodha Kite daily & 30-minute candles.", False),
        ("", False),
        ("THE STORY, IN ORDER", True),
        ("1. Hand-backtesting showed a 76% win rate on 47 trades. The same rules applied "
         "mechanically to every day of history won ~38% -- the gap is selection bias: "
         "trades that worked stand out when you scroll a chart; the ones that fizzled don't.", False),
        ("2. Fixed 1.5R profit targets made both level strategies losers. Letting winners run "
         "(swing-low trailing stop) flipped them: the profit came from rare large winners "
         "that the target had been amputating.", False),
        ("3. Fees are charged on POSITION size, risk is chosen on STOP size. A tight stop "
         "means a huge position and huge fees for the same rupee risk -- the tight-stop trap.", False),
        ("4. The EMA rule whipsawed: median hold 3 sessions on a monthly-filtered strategy. "
         "A 2% hysteresis band (enter 2% above, exit 2% below) cut fees 83%. See Band Sweep.", False),
        (f"5. Overfitting test: parameters frozen, then run on 150 never-seen stocks. "
         f"Profit factors held -- Breakout {pf.get('Breakout', 0):.2f}, EMA {pf.get('EMA', 0):.2f} "
         f"out-of-sample. See Strategy Results.", False),
        ("6. The edge STRENGTHENS with liquidity (see Liquidity). Fake edges do the opposite. "
         "This is the strongest single piece of evidence the effect is real.", False),
        ("7. But a real account extracts almost none of it: Rs10,000 dies at every risk "
         "setting, Rs1 lakh earns ~4-6% CAGR with 40-70% drawdowns, and above ~Rs2.5 lakh "
         "extra money buys nothing. See Portfolio Simulation.", False),
        ("8. Buy-and-hold on the same stocks returned ~12% CAGR median over the same period -- "
         "though the period is almost entirely a bull market, which stacks that comparison "
         "against stop-based strategies.", False),
        ("9. In the one crash in the data (COVID 2020), buy-and-hold fell ~40% while the "
         "strategies were roughly flat to positive -- the protection is real. See 2020 Crash.", False),
        ("", False),
        ("CAVEATS THAT STILL STAND", True),
        ("Survivorship: the universe is today's instrument list; companies that died are "
         "invisible, flattering everything. Slippage is not modelled. One market, one "
         "mostly-bull decade. No 2008-style grinding bear exists in this data.", False),
    ]
    for row_index, (text, bold) in enumerate(story, start=1):
        cell = sheet.cell(row=row_index, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True)
        elif text:
            sheet.row_dimensions[row_index].height = 30

    target = report.save(book, "Full Analysis 199 Stocks.xlsx")
    print(f"\n  written: {target}\n")


if __name__ == "__main__":
    main()

"""One workbook containing every analysis run on the 199-stock universe, written so
that someone new to backtesting can read it cold.

    python -m scripts.full_report

Every sheet opens with WHAT THIS SHEET SHOWS and HOW TO READ IT in plain words, a
Glossary defines the jargon, and the Read Me tells the whole story in order.
Everything is recomputed fresh from the Parquet files at build time. Takes several
minutes -- the breakout scan over 199 stocks is the slow part.
"""
from __future__ import annotations

import statistics as st

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

from kitelab import backtest, config, frames, portfolio, report, strategies

HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
EXPLAIN_FILL = PatternFill("solid", fgColor="FFF7E0")
LOSS_FONT = Font(color="C00000")

BUCKETS = [(0, 1e6, "TINY: under Rs10 lakh traded/day"),
           (1e6, 1e7, "SMALL: Rs10 lakh - Rs1 crore"),
           (1e7, 1e8, "MEDIUM: Rs1 - Rs10 crore"),
           (1e8, 1e15, "LARGE: over Rs10 crore/day")]
CAPITALS = [10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000,
            2_500_000, 5_000_000, 10_000_000]
RISKS = [0.01, 0.02, 0.03, 0.05, 0.10]
BANDS = [0.0, 0.005, 0.01, 0.02, 0.03, 0.05]
# The capital x risk map: 15 capital levels x 20 risk levels = 300 simulated
# accounts per strategy. 0% risk is omitted because it buys zero shares.
RISK_GRID = [i / 200 for i in range(1, 21)]          # 0.5% .. 10.0%
CAP_GRID = [10_000, 15_000, 20_000, 30_000, 40_000, 50_000, 75_000, 100_000,
            150_000, 200_000, 300_000, 400_000, 500_000, 750_000, 1_000_000]
PEAK, BOTTOM, YEAREND = (pd.Timestamp("2020-01-14"), pd.Timestamp("2020-03-23"),
                         pd.Timestamp("2020-12-31"))
PEAK08, BOTTOM08, END09 = (pd.Timestamp("2008-01-08"), pd.Timestamp("2008-10-27"),
                           pd.Timestamp("2009-12-31"))

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


# ---------------------------------------------------------------------------
# layout helpers
# ---------------------------------------------------------------------------

def explain(sheet, anchor_row: int, heading: str, lines: list[str],
            span: int = 10) -> int:
    """A highlighted plain-language block. Returns the next free row."""
    head = sheet.cell(row=anchor_row, column=1, value=heading)
    head.font = Font(bold=True, size=11)
    head.fill = EXPLAIN_FILL
    sheet.merge_cells(start_row=anchor_row, start_column=1,
                      end_row=anchor_row, end_column=span)
    row = anchor_row + 1
    for line in lines:
        cell = sheet.cell(row=row, column=1, value=line)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.fill = EXPLAIN_FILL
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
        sheet.row_dimensions[row].height = max(15, 14 * (1 + len(line) // 105))
        row += 1
    return row + 1


def write_table(sheet, anchor_row: int, title: str, headers: list[str],
                widths: list[int], rows: list[list], formats: list[str]) -> int:
    cell = sheet.cell(row=anchor_row, column=1, value=title)
    cell.font = Font(bold=True, size=12)
    header_row = anchor_row + 1
    for column, (heading, width) in enumerate(zip(headers, widths), start=1):
        head = sheet.cell(row=header_row, column=column, value=heading)
        head.font = Font(bold=True)
        head.fill = HEADER_FILL
        head.alignment = Alignment(horizontal="center", wrap_text=True)
        letter = get_column_letter(column)
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


STRAT_HEADERS = ["Group", "Trades", "Win %", "Gross Profit", "Charges", "Net Profit",
                 "Avg Profit / Trade", "Profit Factor", "Avg R", "Best Trade % of Total"]
STRAT_WIDTHS = [30, 9, 8, 13, 12, 13, 14, 12, 8, 16]
STRAT_FORMATS = ["@", "0", "0.0", "#,##0", "#,##0", "#,##0", "#,##0", "0.00", "0.00", "0.0"]

GLOSSARY = [
    ("Profit Factor", "For every Rs1 the strategy lost, how many rupees it made back. "
     "1.00 = broke even. 1.50 = made Rs1.50 per Rs1 lost. Below 1.00 = losing money. "
     "This is the single most useful number in this file.",
     "Above 1.3 is decent, above 1.5 is good."),
    ("Win rate / Win %", "How often trades made money. A LOW win rate is not bad by "
     "itself -- these strategies win rarely but win big. What matters is win rate "
     "TOGETHER with how large wins are versus losses.",
     "Meaningless alone. Read it next to Profit Factor."),
    ("Expectancy / Avg Profit per Trade", "Average rupees made per trade after "
     "everything, including losers. Positive = the strategy adds money on average.",
     "Positive, and large compared to the charges per trade."),
    ("R multiple", "Profit measured in units of what you risked. Risked Rs1,000 and "
     "made Rs3,000 = +3R. Lost the full risk = -1R. Lets big and small trades be "
     "compared fairly.",
     "Losses should cluster at -1R (stop worked). Worse than -1R = a gap."),
    ("Stop loss", "A price where you give up and sell to cap the damage. All our "
     "trades have one.", "-"),
    ("Trailing stop", "A stop loss that moves UP as the stock rises (never down). "
     "Locks in gains while letting winners run. Both strategies use one.", "-"),
    ("Gap", "A stock opening far below yesterday's close (overnight news). Price "
     "jumps OVER your stop, so you sell much lower than planned. The one risk a "
     "stop loss cannot prevent. See Worst Losses.", "-"),
    ("Charges", "Real Zerodha costs: brokerage, STT, exchange fees, GST, stamp duty, "
     "demat fee. Charged on POSITION size, not on risk -- so tight stops (big "
     "positions) cost the most in fees.", "Small next to gross profit."),
    ("Drawdown / Max DD", "The worst peak-to-valley fall of the account. A 50% "
     "drawdown needs a 100% gain just to get back to even -- and most people give "
     "up before that.", "Smaller is better. Above 40% is very hard to live through."),
    ("CAGR", "Compound annual growth rate -- the steady yearly % that would produce "
     "the same final result. The honest way to state a multi-year return.",
     "Compare with ~7% from a fixed deposit and ~12% from buy-and-hold here."),
    ("In-sample", "The 49 stocks we TUNED the settings on. Results there are like "
     "scoring your own practice exam -- not proof of anything.", "-"),
    ("Out-of-sample", "150 random stocks the settings had never seen, with nothing "
     "re-tuned. The real exam. Only these results count as evidence.", "-"),
    ("Overfitting", "Accidentally tuning a strategy to fit past data's noise -- like "
     "memorising last year's exam paper. Looks brilliant in testing, fails on new "
     "data. The out-of-sample test exists to catch it.", "-"),
    ("Turnover / Liquidity", "How many rupees of a stock change hands daily. Liquid "
     "stocks can be bought at the screen price; tiny stocks cannot -- which is where "
     "fake backtest profits usually hide.", "-"),
    ("Survivorship bias", "Our stock list only contains companies alive TODAY. Every "
     "company that went bust is invisible, which makes ALL results here look better "
     "than reality -- especially buy-and-hold in 2008.", "-"),
    ("Whipsaw", "Price wobbling around a signal line, triggering buy-sell-buy-sell "
     "in quick succession, paying fees each time. See Band Sweep for the cure.", "-"),
    ("Hysteresis band / dead zone", "Buy only when price is 2% ABOVE the line, sell "
     "only 2% BELOW it. In between, do nothing. This kills whipsaw.", "-"),
    ("Buy and hold", "Just buying the stock and never selling -- the benchmark every "
     "strategy must beat to be worth its effort.", "-"),
    ("All-time high (ATH)", "The highest price a stock has EVER traded. The breakout "
     "strategy buys when price pushes into brand-new high ground.", "-"),
]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

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
    pf_out = {n: report.stats("x", split[n]["out"]).get("profit_factor", 0)
              for n in signals}

    book = Workbook()
    book.remove(book.active)

    # ---- Strategy Results ------------------------------------------------
    sheet = book.create_sheet("Strategy Results")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "The report card. Each strategy was tested twice: on the 49 stocks we tuned the "
        "settings on (the practice exam -- ignore it), and on 150 random stocks it had "
        "never seen (the real exam -- this is the evidence).",
        "HOW TO READ IT: look at the Profit Factor column of the OUT-OF-SAMPLE rows. "
        "Above 1.00 means the strategy makes money; above 1.5 is genuinely good. "
        "'Best Trade % of Total' checks the profit isn't one lucky trade -- under ~20% is healthy.",
        "The strategies: BREAKOUT buys when a stock pushes to a brand-new all-time high "
        "after a pullback. EMA buys when price is 2% above its 20-day, 20-week and "
        "20-month average lines, and sells when it drops 2% below any of them. Both "
        "trail a stop loss up under the price and size every trade to risk 1% of a "
        "Rs1,00,000 account.",
    ])
    row = write_table(sheet, row, "Breakout", STRAT_HEADERS, STRAT_WIDTHS,
                      [strategy_row(f"Practice exam - IN-SAMPLE ({len(in_sample)} stocks)",
                                    split["Breakout"]["in"]),
                       strategy_row(f"REAL EXAM - OUT-OF-SAMPLE ({len(out_sample)} stocks)",
                                    split["Breakout"]["out"])],
                      STRAT_FORMATS)
    row = write_table(sheet, row, "EMA", STRAT_HEADERS, STRAT_WIDTHS,
                      [strategy_row(f"Practice exam - IN-SAMPLE ({len(in_sample)} stocks)",
                                    split["EMA"]["in"]),
                       strategy_row(f"REAL EXAM - OUT-OF-SAMPLE ({len(out_sample)} stocks)",
                                    split["EMA"]["out"])],
                      STRAT_FORMATS)
    explain(sheet, row, "WHAT IT MEANS", [
        f"Both passed the real exam: Breakout {pf_out.get('Breakout', 0):.2f}, EMA "
        f"{pf_out.get('EMA', 0):.2f} profit factor on stocks they were never tuned for. "
        "The per-trade edge is real. Whether a real account can capture it is a separate "
        "question -- see Portfolio Simulation.",
        "Notice the win rates: roughly 20-40%. These strategies are WRONG most of the "
        "time and profitable anyway, because winners are far bigger than losers. If you "
        "trade them expecting to be right often, you will abandon them at exactly the "
        "wrong moment.",
    ])

    # ---- Liquidity -------------------------------------------------------
    sheet = book.create_sheet("Liquidity")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "The same out-of-sample trades, split by how heavily each stock trades every day "
        "(its turnover). Why this matters: a backtest assumes you buy at the screen "
        "price. In a tiny stock that is fiction -- your own order moves the price. So "
        "FAKE backtest profits almost always hide in the tiniest stocks.",
        "HOW TO READ IT: if the TINY bucket had the best profit factor, these results "
        "would be an illusion. The healthy pattern is the opposite: worst in TINY, "
        "strong in MEDIUM and LARGE -- stocks you could actually trade.",
    ])
    for name in signals:
        rows = []
        for lo, hi, label in BUCKETS:
            bucket = [t for t in split[name]["out"] if lo <= turnover(t["symbol"]) < hi]
            if bucket:
                rows.append(strategy_row(label, bucket))
        row = write_table(sheet, row, f"{name} (out-of-sample trades only)",
                          STRAT_HEADERS, STRAT_WIDTHS, rows, STRAT_FORMATS)
    explain(sheet, row, "WHAT IT MEANS", [
        "The edge is weakest exactly where fake edges are strongest. That inversion is "
        "the single best piece of evidence in this file that the strategies are real. "
        "Practical rule that falls out of it: trade these only in stocks doing at least "
        "Rs1 crore a day.",
    ])

    # ---- Band Sweep ------------------------------------------------------
    sheet = book.create_sheet("Band Sweep")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "Why the EMA rule has a 2% 'dead zone'. With no band (0%), buying and selling "
        "share the same line -- price wobbling on that line buys and sells every few "
        "days (whipsaw), paying fees each round trip. The median trade lasted just 3 "
        "sessions on a strategy that is supposed to follow MONTHLY trends.",
        "HOW TO READ IT: go down the rows as the band widens. Watch trades and charges "
        "collapse while profit-per-trade and profit factor climb. When every column "
        "improves smoothly in one direction, the effect is real, not luck.",
    ])
    sweeps = [
        ("the 49 tuning stocks", in_sample, [
            "The band cut fee bills by more than 80% by removing pointless churn. 2% was "
            "picked as the balance between profit-per-trade and total profit. Honesty "
            "note: because 2% was CHOSEN by looking at THIS table, these 49 stocks "
            "stopped being evidence."]),
        ("the 150 never-seen stocks -- VALIDATION", out_sample, [
            "The same sweep on stocks the 2% was never chosen from. The same smooth "
            "pattern appearing here -- fewer trades, collapsing charges, rising profit "
            "per trade as the band widens -- means the band captures something real "
            "about how these rules trade, not a quirk of the 49.",
            "Discipline note: we look at this table but do NOT re-pick the band from "
            "it. Choosing the best value from a validation table would just start a "
            "new round of overfitting on new stocks."]),
    ]
    for sweep_label, members, meaning in sweeps:
        rows = []
        for band in BANDS:
            trades = []
            for symbol in members:
                try:
                    trades.extend(backtest.simulate(symbol, band=band))
                except SystemExit:
                    pass
            s = report.stats(f"{band * 100:.1f}%", trades)
            rows.append([f"{band * 100:.1f}%", s["trades"],
                         st.median(t["bars_held"] for t in trades) if trades else 0,
                         s["gross_profit"], s["charges"], s["net_profit"],
                         s["expectancy"], s["profit_factor"]])
        row = write_table(sheet, row, f"EMA with different band widths (on {sweep_label})",
                          ["Band", "Trades", "Median Days Held", "Gross Profit", "Charges",
                           "Net Profit", "Avg Profit / Trade", "Profit Factor"],
                          [8, 9, 17, 13, 12, 13, 15, 12], rows,
                          ["@", "0", "0", "#,##0", "#,##0", "#,##0", "#,##0", "0.00"])
        row = explain(sheet, row, "WHAT IT MEANS", meaning)

    # ---- Portfolio Simulation -------------------------------------------
    sheet = book.create_sheet("Portfolio Simulation")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "Everything before this sheet added up trades as if each had its own money. A "
        "real account has ONE pot: when cash is tied up, new signals are skipped. This "
        "simulates that -- one account, signals taken in order, skip what you cannot "
        "afford, risk 1% of equity per trade -- equity being cash plus open positions "
        "valued at what they COST, not at today's price.",
        "HOW TO READ IT: 'CAGR %' is the honest yearly return; 'Max DD %' is the worst "
        "fall you would have sat through. 'Skipped (too small)' counts signals where 1% "
        "risk could not buy even one share -- the small-account trap.",
    ])
    port_headers = ["Capital", "Risk", "Final Value", "Total Return %", "CAGR %",
                    "Max DD %", "Trades Taken", "Skipped (too small)", "Skipped (no cash)"]
    port_widths = [12, 7, 13, 13, 9, 10, 12, 17, 15]
    port_formats = ["#,##0", "0%", "#,##0", "0", "0.0", "0.0", "0", "0", "0"]
    for name in signals:
        rows = []
        for capital in CAPITALS:
            r = portfolio.run(signals[name], capital, 0.01)
            rows.append([capital, 0.01, r["final"], r["return_pct"], r["cagr_pct"],
                         r["max_drawdown_pct"], len(r["taken"]),
                         r["skipped_size"], r["skipped_cash"]])
        row = write_table(sheet, row, f"{name}: same strategy, different account sizes",
                          port_headers, port_widths, rows, port_formats)
    rows = []
    for capital in (10_000, 100_000):
        for risk in RISKS:
            r = portfolio.run(signals["Breakout"], capital, risk)
            rows.append([capital, risk, r["final"], r["return_pct"], r["cagr_pct"],
                         r["max_drawdown_pct"], len(r["taken"]),
                         r["skipped_size"], r["skipped_cash"]])
    row = write_table(sheet, row,
                      "Breakout: does raising the risk % rescue a small account? (No.)",
                      port_headers, port_widths, rows, port_formats)
    explain(sheet, row, "WHAT IT MEANS", [
        "Rs10,000 is destroyed at EVERY risk setting -- not because the strategy is bad, "
        "but because 1% of Rs10,000 cannot afford most trades, so the account is forced "
        "into only the cheapest, tightest-stop trades (the worst ones), while fixed fees "
        "eat ~15% of every trade's risk budget.",
        "Around Rs1,00,000 the strategies start working; near Rs2,50,000 they reach full "
        "strength. Beyond that, extra money adds nothing -- the one-position-per-stock "
        "rule becomes the limit, not cash.",
        "And raising risk % makes a small account die FASTER: one overnight gap at 5% "
        "risk can take half the account (see Worst Losses). The lever that feels like "
        "the fix is the trap.",
    ])

    # ---- Risk Map --------------------------------------------------------
    sheet = book.create_sheet("Risk Map")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "One simulated account for EVERY combination of starting capital (rows, Rs10,000 "
        "to Rs10 lakh) and risk-per-trade (columns, 0.5% to 10%) -- 300 separate "
        "simulations per strategy. Each cell is that account's CAGR: its yearly % "
        "return over the whole period. 0% risk is not shown because risking nothing "
        "buys zero shares.",
        "HOW TO READ IT: green = the account grew each year, red = it shrank. Read a "
        "row left-to-right to see what raising risk does at your capital level; read a "
        "column top-to-bottom to see what more capital does at your risk level. The "
        "charts below each grid plot CAGR against capital at four risk settings.",
        "The map is bumpy rather than smooth, and that is honest: changing any setting "
        "changes WHICH trades the account can afford, and a different trade list can "
        "swing the outcome hard. Treat broad regions as meaningful, single cells as "
        "noise.",
    ], span=len(RISK_GRID) + 1)
    for name in signals:
        title = sheet.cell(row=row, column=1,
                           value=f"{name}: CAGR % for every capital x risk combination")
        title.font = Font(bold=True, size=12)
        hdr = row + 1
        corner = sheet.cell(row=hdr, column=1, value="Capital \\ Risk")
        corner.font = Font(bold=True)
        corner.fill = HEADER_FILL
        sheet.column_dimensions["A"].width = 14
        for j, riskv in enumerate(RISK_GRID, start=2):
            cell = sheet.cell(row=hdr, column=j, value=f"{riskv * 100:g}%")
            cell.font = Font(bold=True)
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center")
            sheet.column_dimensions[get_column_letter(j)].width = 7
        for i, capital in enumerate(CAP_GRID):
            r_index = hdr + 1 + i
            cap_cell = sheet.cell(row=r_index, column=1, value=capital)
            cap_cell.number_format = "#,##0"
            cap_cell.font = Font(bold=True)
            for j, riskv in enumerate(RISK_GRID, start=2):
                result = portfolio.run(signals[name], capital, riskv)
                cell = sheet.cell(row=r_index, column=j,
                                  value=round(result["cagr_pct"], 1))
                cell.number_format = "0.0"
        last_col = get_column_letter(1 + len(RISK_GRID))
        data_range = f"B{hdr + 1}:{last_col}{hdr + len(CAP_GRID)}"
        # Same fixed colour scale on both grids so their colours are comparable.
        sheet.conditional_formatting.add(data_range, ColorScaleRule(
            start_type="num", start_value=-25, start_color="F8696B",
            mid_type="num", mid_value=0, mid_color="FFFFFF",
            end_type="num", end_value=12, end_color="63BE7B"))
        chart = LineChart()
        chart.title = f"{name}: CAGR % vs capital, at four risk levels"
        chart.y_axis.title = "CAGR %"
        chart.x_axis.title = "Starting capital (Rs)"
        chart.height, chart.width = 10, 26
        categories = Reference(sheet, min_col=1, min_row=hdr + 1,
                               max_row=hdr + len(CAP_GRID))
        for riskv in (0.01, 0.02, 0.05, 0.10):
            j = 2 + RISK_GRID.index(riskv)
            series = Reference(sheet, min_col=j, min_row=hdr,
                               max_row=hdr + len(CAP_GRID))
            chart.add_data(series, titles_from_data=True)
        chart.set_categories(categories)
        sheet.add_chart(chart, f"B{hdr + len(CAP_GRID) + 2}")
        row = hdr + len(CAP_GRID) + 24
    explain(sheet, row, "WHAT IT MEANS", [
        "The bottom-left is red for both strategies: small accounts lose at EVERY risk "
        "setting, and moving right (more risk) makes the red deeper, not lighter. The "
        "healthy region starts around Rs1,00,000 at low risk and is broadest near "
        "Rs2,50,000+ at 1-2%. Above that, adding capital changes little -- the "
        "one-position-per-stock rule, not money, becomes the limit.",
    ], span=len(RISK_GRID) + 1)

    # ---- crash sheets ----------------------------------------------------
    def equity_at(curve, when):
        prior = [e for ts, e in curve if ts <= when]
        return prior[-1] if prior else (curve[0][1] if curve else 0)

    def buy_hold_window(peak, bottom, end):
        crash, recovery, count = [], [], 0
        for symbol in everything:
            try:
                daily = frames.daily(symbol)
            except SystemExit:
                continue
            if daily["ts"].iloc[0] > peak:
                continue
            def px(when):
                sub = daily[daily.ts <= when]
                return float(sub["close"].iloc[-1]) if len(sub) else None
            a, b, c = px(peak), px(bottom), px(end)
            if a and b and c:
                count += 1
                crash.append(100 * (b / a - 1))
                recovery.append(100 * (c / a - 1))
        return crash, recovery, count

    def crash_sheet(sheet_name, preamble, title, peak, bottom, end,
                    strategy_names, meaning):
        sheet = book.create_sheet(sheet_name)
        row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", preamble)
        rows = []
        for name in strategy_names:
            r = portfolio.run(signals[name], 250_000, 0.01)
            e_peak = equity_at(r["curve"], peak)
            e_bot = equity_at(r["curve"], bottom)
            e_end = equity_at(r["curve"], end)
            rows.append([name, e_peak, e_bot, 100 * (e_bot / e_peak - 1),
                         e_end, 100 * (e_end / e_peak - 1)])
        crash, recovery, count = buy_hold_window(peak, bottom, end)
        rows.append([f"BUY & HOLD (median of {count} stocks)", None, None,
                     st.median(crash) if crash else 0, None,
                     st.median(recovery) if recovery else 0])
        row = write_table(sheet, row, title,
                          ["Strategy", "Account at Peak", "Account at Bottom",
                           "Peak to Bottom %", "Account at Window End", "Peak to End %"],
                          [30, 14, 15, 14, 17, 15], rows,
                          ["@", "#,##0", "#,##0", "0.0", "#,##0", "0.0"])
        explain(sheet, row, "WHAT IT MEANS", meaning)

    crash_sheet(
        "2020 Crash",
        ["What happened to a Rs2,50,000 account through the COVID crash, versus simply "
         "holding the same stocks. 'Peak to Bottom %' is the pain during the fall; "
         "'Peak to End %' is where you stood once the dust settled."],
        "COVID: 14 Jan 2020 peak -> 23 Mar bottom -> 31 Dec 2020",
        PEAK, BOTTOM, YEAREND, list(signals),
        ["Buy-and-hold fell ~40% peak to bottom; the strategies were roughly flat -- the "
         "EMA even made money DURING the crash, because its exit rule had sold rising "
         "stocks as they broke their trend lines on the way down.",
         "Then the market recovered in a near-vertical V -- the worst possible shape for "
         "these strategies, which had sold and were slow to get back in. Protection on "
         "the way down, lag on the way up."],
    )
    crash_sheet(
        "2008 Crash",
        ["The same test through the 2008 collapse -- a ten-month grinding bear, the "
         "opposite shape to COVID's V. EMA ONLY: the Breakout strategy needs 30-minute "
         "price data, which does not exist before 2015 from any Kite source.",
         "Extra warning for this sheet: our stock list only contains companies alive "
         "TODAY. The many companies 2008 actually killed are invisible, which makes the "
         "buy-and-hold row look far better than the real 2008 experience was."],
        "2008: 8 Jan 2008 peak -> 27 Oct 2008 bottom -> 31 Dec 2009",
        PEAK08, BOTTOM08, END09, ["EMA"],
        ["At the bottom the EMA was ahead: down ~50% versus ~68% for holding. The stops "
         "did cut the damage. But 2008's crash came as huge overnight GAPS that jump "
         "straight over stops, and its bear-market rallies kept triggering re-entries "
         "that lost again.",
         "Then buy-and-hold rode the giant 2009 rebound back while the EMA, stopped out "
         "near the lows, lagged badly. Over the FULL 2008-09 cycle, holding won.",
         "Put both crash sheets together and the honest conclusion is: the strategies "
         "halve the fall, and pay for it in the recovery. Whether that trade is worth it "
         "depends on the crash's shape -- which nobody knows in advance."],
    )

    # ---- Worst Losses ----------------------------------------------------
    sheet = book.create_sheet("Worst Losses")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "The ten worst trades across everything, measured in R (multiples of what was "
        "risked). A working stop loss caps a loss at -1R. Every trade here is worse than "
        "-1R, and every one is an overnight GAP: the stock opened far below the stop, so "
        "the sale happened at the open, not at the planned price.",
        "HOW TO READ IT: multiply the R column by your risk-per-trade to see the rupee "
        "damage at your settings. A -18R trade at 1% risk costs 18% of the account. At "
        "5% risk the same single trade costs 91%.",
    ])
    worst = sorted(((t["r_multiple"], name, t) for name, trades in signals.items()
                    for t in trades), key=lambda x: x[0])[:10]
    rows = [[name, t["symbol"], t["entry_ts"].date(), r, t["exit_reason"],
             t["net_profit"]] for r, name, t in worst]
    row = write_table(sheet, row, "Ten worst trades (all 199 stocks, both strategies)",
                      ["Strategy", "Stock", "Entry Date", "R Multiple", "Exit Reason",
                       "Net P&L (at 1% risk)"],
                      [11, 12, 12, 11, 20, 18], rows,
                      ["@", "@", "yyyy-mm-dd", "0.00", "@", "#,##0"])
    explain(sheet, row, "WHAT IT MEANS", [
        "This table is the whole argument for small risk-per-trade. Gaps cannot be "
        "prevented by any stop, only survived by position sizing. Keeping risk at 1% "
        "means the worst night in twenty years of data cost under a fifth of the "
        "account. At 5% it would have ended it.",
    ])

    # ---- Per Stock -------------------------------------------------------
    sheet = book.create_sheet("Per Stock")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "Every one of the 199 stocks individually: which test group it was in, how "
        "liquid it is, what simply holding it returned per year, and how each strategy "
        "did on it. Use this to look up any stock you care about.",
        "HOW TO READ IT: expect MOST stocks to be small losers for the strategies and a "
        "minority to be large winners -- that is the design, not a flaw. A strategy row "
        "with very few trades means little either way.",
    ])
    by_symbol = {name: {} for name in signals}
    for name, trades in signals.items():
        for t in trades:
            by_symbol[name].setdefault(t["symbol"], []).append(t)
    rows = []
    for symbol in everything:
        entry = [symbol, "tuning (in-sample)" if symbol in in_set else "real exam (out)",
                 turnover(symbol), buy_hold_cagr(symbol)]
        for name in ("Breakout", "EMA"):
            mine = by_symbol[name].get(symbol, [])
            s = report.stats(symbol, mine) if mine else {}
            entry += [len(mine), s.get("net_profit", 0), s.get("profit_factor", 0)]
        rows.append(entry)
    write_table(sheet, row, f"All {len(everything)} stocks",
                ["Stock", "Group", "Daily Turnover (median Rs)", "Buy&Hold CAGR %",
                 "B'out Trades", "B'out Net", "B'out PF",
                 "EMA Trades", "EMA Net", "EMA PF"],
                [13, 17, 20, 15, 11, 12, 9, 11, 12, 9], rows,
                ["@", "@", "#,##0", "0.0", "0", "#,##0", "0.00", "0", "#,##0", "0.00"])
    sheet.freeze_panes = f"A{row + 2}"

    # ---- Glossary --------------------------------------------------------
    sheet = book.create_sheet("Glossary", 1)
    row = explain(sheet, 1, "EVERY TERM USED IN THIS FILE, IN PLAIN WORDS",
                  ["Read this sheet once and every other sheet becomes readable."], span=3)
    for column, (heading, width) in enumerate(
            zip(["Term", "What it means", "What a good value looks like"],
                [22, 95, 42]), start=1):
        head = sheet.cell(row=row, column=column, value=heading)
        head.font = Font(bold=True)
        head.fill = HEADER_FILL
        sheet.column_dimensions[get_column_letter(column)].width = width
    for offset, (term, meaning, good) in enumerate(GLOSSARY):
        r = row + 1 + offset
        sheet.cell(row=r, column=1, value=term).font = Font(bold=True)
        cell = sheet.cell(row=r, column=2, value=meaning)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.cell(row=r, column=3, value=good).alignment = Alignment(
            wrap_text=True, vertical="top")
        sheet.row_dimensions[r].height = max(28, 14 * (1 + len(meaning) // 92))
    sheet.freeze_panes = f"A{row + 1}"

    # ---- Read Me ---------------------------------------------------------
    sheet = book.create_sheet("Read Me", 0)
    sheet.column_dimensions["A"].width = 118
    story = [
        ("WHAT THIS FILE IS", True),
        ("We took two trading strategies -- one that buys all-time-high breakouts, one "
         "that follows 20-period average lines on three timeframes -- and tested them "
         "the hard way: on 199 NSE stocks, over up to 20 years of real Zerodha price "
         "data, with real fees, and with every trick we could think of to catch "
         "ourselves being fooled. This file is everything we found.", False),
        ("", False),
        ("THE WHOLE STORY IN THREE LINES", True),
        ("1. Both strategies genuinely work, per trade -- proven on stocks they were "
         "never tuned for.", False),
        ("2. A small account cannot capture that edge: fees and capital limits eat it, "
         "and below ~Rs50,000 the account is destroyed.", False),
        ("3. Even a big account roughly matches a fixed deposit at far more stress -- "
         "the strategies' real product is not higher returns, it is smaller crashes.", False),
        ("", False),
        ("WHICH SHEET ANSWERS WHICH QUESTION", True),
        ("Do the strategies actually make money?  ->  Strategy Results", False),
        ("How do I know it isn't luck or curve-fitting?  ->  Strategy Results (the "
         "out-of-sample rows) and Liquidity", False),
        ("Why does the EMA rule have that 2% buffer?  ->  Band Sweep", False),
        ("How much money would I need? What returns? How bad do the falls get?  ->  "
         "Portfolio Simulation", False),
        ("Every capital-and-risk combination as one colour-coded picture  ->  Risk Map", False),
        ("What happens in a market crash?  ->  2020 Crash and 2008 Crash", False),
        ("What is the single worst thing that can happen?  ->  Worst Losses", False),
        ("How did one specific stock do?  ->  Per Stock", False),
        ("What does this word mean?  ->  Glossary", False),
        ("", False),
        ("HOW WE GOT HERE -- THE TEN FINDINGS, IN ORDER", True),
        ("1. THE 76% ILLUSION. Backtesting by scrolling charts by hand showed a 76% win "
         "rate on 47 trades. A computer applying the SAME rules to EVERY day of history "
         "found 267 trades winning ~38%. Nobody cheated: trades that worked stand out "
         "when you scroll; the ones that fizzled are invisible. This is why hand "
         "backtests always flatter.", False),
        ("2. SELLING WINNERS EARLY WAS THE KILLER. The original rules took profit at a "
         "fixed 1.5x risk. Removing the target and trailing a stop under the price "
         "instead turned losing strategies into winning ones -- because ALL the profit "
         "lives in a few huge winners the target had been cutting short.", False),
        ("3. THE TIGHT-STOP FEE TRAP. Fees are charged on the SIZE of your position, "
         "but you choose risk by your STOP distance. A tight stop forces a huge position "
         "-- so 'safer' tight stops quietly cost 3-4x more in fees for the same risk.", False),
        ("4. WHIPSAW. The EMA rule was re-buying the same stock within days of selling "
         "it, over and over, paying fees each time. A 2% dead zone between the buy line "
         "and sell line cut the total fee bill by more than 80%. (Band Sweep)", False),
        ("5. THE OVERFITTING TEST. Because we tuned that 2% on 49 stocks, we froze every "
         "setting and re-ran on 150 random stocks the rules had never seen. Both "
         f"strategies held: Breakout {pf_out.get('Breakout', 0):.2f}, EMA "
         f"{pf_out.get('EMA', 0):.2f} profit factor. Memorised answers fail new exams; "
         "these did not. (Strategy Results)", False),
        ("6. THE LIQUIDITY FINGERPRINT. Fake backtest profits hide in tiny stocks where "
         "simulated prices are fiction. Our edge is WEAKEST there and strongest in "
         "heavily-traded stocks -- the reverse of the fake pattern. (Liquidity)", False),
        ("7. THE SMALL-ACCOUNT TRAP. A Rs10,000 account died at every risk setting -- "
         "it can only afford the worst trades, and fixed fees eat it alive. Raising "
         "risk % kills it faster. The floor is ~Rs1,00,000; full strength ~Rs2,50,000; "
         "beyond that more money adds nothing. (Portfolio Simulation)", False),
        ("8. THE BENCHMARK NOBODY BEATS EASILY. Simply buying and holding these same "
         "stocks returned ~12% a year over the period. The strategies earned less with "
         "more effort -- their real value shows up only in crashes.", False),
        ("9. CRASH PROTECTION IS REAL BUT SHAPE-DEPENDENT. COVID 2020: buy-and-hold "
         "fell 40%, the strategies were flat to positive -- clear win. 2008: the EMA "
         "fell ~50% vs ~68% for holding, then lagged the 2009 rebound and lost the full "
         "cycle. The strategies halve the fall and pay for it in the recovery.", False),
        ("10. EVERY WRONG NUMBER ANNOUNCED ITSELF. Three times in this project a result "
         "made no sense -- and each time chasing it uncovered a real bug (a wrong "
         "breakout definition, a 10-day cap starving trades, dead levels leaking through "
         "a data boundary). The lesson worth more than any strategy: when a number "
         "surprises you, it is a finding, not an answer.", False),
        ("", False),
        ("WHAT THIS FILE STILL CANNOT TELL YOU", True),
        ("Companies that went bankrupt are missing from the data (only today's "
         "survivors are testable), so every result -- especially buy-and-hold in 2008 "
         "-- looks better than reality. Slippage (getting a worse fill than the screen "
         "price) is not modelled. This is one country's market, and the past, however "
         "long, is not the future.", False),
        ("Data: Zerodha Kite. Daily candles 2006-2026 for ~74 stocks, later starts for "
         "the rest; 30-minute candles exist only from 2015, so the Breakout strategy is "
         "untestable before then. Charges: Zerodha delivery rates as of Aug 2026.", False),
    ]
    for row_index, (text, bold) in enumerate(story, start=1):
        cell = sheet.cell(row=row_index, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True, size=12)
        elif text:
            sheet.row_dimensions[row_index].height = 15 * (1 + len(text) // 105)

    target = report.save(book, "Full Analysis 199 Stocks.xlsx")
    print(f"\n  written: {target}\n")


if __name__ == "__main__":
    main()

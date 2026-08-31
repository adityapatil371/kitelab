"""Machine backtest of both strategies on the six class-assignment instruments.

    python -m scripts.assets_report

Writes output/Assets Backtest.xlsx: a Summary explaining data sources, per-asset
results for both strategies, a one-account simulation, buy-and-hold benchmarks,
Bitcoin's two bear markets, and one full trade-list sheet per asset.

Fee models are flat per-side approximations (crypto exchange / futures), swapped in
via backtest.FLAT_FEE_RATE so strategies and the portfolio simulator stay consistent.
Bitcoin sizes in fractional coins (sizing.FRACTIONAL).
"""
from __future__ import annotations

import pandas as pd
from openpyxl import Workbook

from kitelab import backtest, frames, portfolio, report, sizing, strategies
from scripts.full_report import explain, write_table

# symbol, breakout timeframe, per-side fee rate, description
ASSETS = [
    ("BITCOIN", "30m", 0.0010,
     "Binance BTCUSDT. Prices in US DOLLARS, clock is UTC, trades 24/7. "
     "Fee model ~0.10% per side (typical crypto exchange). Indian 30% gains tax and "
     "1% TDS are NOT modelled."),
    ("NIFTY 50", "30m", 0.0005,
     "NSE index. You cannot buy an index directly -- real trading uses futures or an "
     "index fund, approximated here at 0.05% per side."),
    ("NIFTY BANK", "30m", 0.0005,
     "NSE index, same caveat and fee model as NIFTY 50."),
    ("GOLD", "1d", 0.0005,
     "MCX continuous futures, daily candles only (no stitched intraday exists), so the "
     "breakout enters on DAILY closes here. Continuous series have small price jumps "
     "at each monthly contract rollover -- treat results with an extra grain of salt."),
    ("SILVER", "1d", 0.0005, "MCX continuous futures; same caveats as GOLD."),
    ("CRUDEOIL", "1d", 0.0005, "MCX continuous futures; same caveats as GOLD."),
]

BTC_BEARS = [
    ("2018 bear", pd.Timestamp("2017-12-17"), pd.Timestamp("2018-12-15"),
     pd.Timestamp("2019-12-31")),
    ("2022 bear", pd.Timestamp("2021-11-10"), pd.Timestamp("2022-11-21"),
     pd.Timestamp("2023-12-31")),
]

RESULT_HEADERS = ["Asset", "Trades", "Win %", "Gross", "Fees", "Net",
                  "Avg Profit / Trade", "Profit Factor", "Avg R", "Best Trade % of Total"]
RESULT_WIDTHS = [14, 9, 8, 13, 11, 13, 14, 12, 8, 16]
RESULT_FORMATS = ["@", "0", "0.0", "#,##0", "#,##0", "#,##0", "#,##0", "0.00", "0.00", "0.0"]


def price_drawdown(daily: pd.DataFrame) -> float:
    closes = daily["close"]
    return float(100 * ((closes / closes.cummax()) - 1).min())


def equity_at(curve, when):
    prior = [e for ts, e in curve if ts <= when]
    return prior[-1] if prior else (curve[0][1] if curve else 0)


def main() -> None:
    results: dict[str, dict] = {}
    print()
    for symbol, brk_tf, fee, _ in ASSETS:
        backtest.FLAT_FEE_RATE = fee
        # Fractional units for EVERY asset: MCX gold quotes near Rs1 lakh a unit
        # and index points have no share size, so whole-unit flooring silently
        # rejected almost all breakout trades (their pivot stops are wide, so
        # Rs1,000 of risk buys well under one unit).
        sizing.FRACTIONAL = True
        try:
            ema = backtest.simulate(symbol)
            brk = strategies.ath_breakout_trades(symbol, True, timeframe=brk_tf)
            daily = frames.daily(symbol)
            years = (daily["ts"].iloc[-1] - daily["ts"].iloc[0]).days / 365.25
            growth = daily["close"].iloc[-1] / daily["close"].iloc[0]
            results[symbol] = {
                "ema": ema, "brk": brk, "daily": daily,
                "port_ema": portfolio.run(ema, 100_000, 0.01),
                "port_brk": portfolio.run(brk, 100_000, 0.01),
                "bh_cagr": 100 * (growth ** (1 / years) - 1),
                "bh_dd": price_drawdown(daily),
                "years": years,
            }
            print(f"  {symbol:<11} EMA {len(ema):>3} trades, Breakout {len(brk):>3} trades, "
                  f"{years:.1f} yrs of data")
        finally:
            backtest.FLAT_FEE_RATE = None
            sizing.FRACTIONAL = False

    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("Summary")
    row = explain(sheet, 1, "WHAT THIS FILE IS", [
        "The class-assignment instruments -- GOLD, NIFTY, BITCOIN, SILVER, BANK NIFTY, "
        "CRUDE OIL -- machine-tested with the same two strategies validated on 199 NSE "
        "stocks. Each person hand-backtests their own instrument; this is the machine "
        "answer to compare against. Sizing: 100,000 currency units of capital, 1% risk "
        "per trade, in FRACTIONAL units for every asset -- these are studied as price "
        "series, not lot-sized futures contracts.",
        "READ WITH CARE: each instrument is ONE price history. There is no 199-stock "
        "cross-section here, so a good or bad number can be one lucky path. Fees are "
        "flat approximations per asset (stated in the coverage table); Bitcoin is in "
        "USD on a UTC clock; the MCX series have contract-rollover jumps.",
    ])
    row = write_table(sheet, row, "Data coverage and fee models",
                      ["Asset", "Breakout enters on", "Fee per side", "Notes"],
                      [14, 16, 12, 105],
                      [[s, "daily closes" if tf == "1d" else "30-minute closes",
                        f"{fee * 100:.2f}%", note] for s, tf, fee, note in ASSETS],
                      ["@", "@", "@", "@"])

    for label, key in [("EMA (20-EMA stack, 2% band, entry-day-low stop)", "ema"),
                       ("Breakout (all-time high, swing-low trailing stop)", "brk")]:
        rows = []
        for symbol, *_ in ASSETS:
            s = report.stats(symbol, results[symbol][key])
            rows.append([symbol, s.get("trades", 0), s.get("win_rate_pct", 0),
                         s.get("gross_profit", 0), s.get("charges", 0),
                         s.get("net_profit", 0), s.get("expectancy", 0),
                         s.get("profit_factor", 0), s.get("avg_r", 0),
                         s.get("top_share_pct", 0)])
        row = write_table(sheet, row, label, RESULT_HEADERS, RESULT_WIDTHS, rows,
                          RESULT_FORMATS)

    rows = []
    for symbol, *_ in ASSETS:
        r = results[symbol]
        for name, port in [("EMA", r["port_ema"]), ("Breakout", r["port_brk"])]:
            rows.append([f"{symbol} - {name}", port["final"], port["return_pct"],
                         report.cagr_cell(port), port["max_drawdown_pct"],
                         len(port["taken"])])
        rows.append([f"{symbol} - BUY & HOLD", None, None, r["bh_cagr"], r["bh_dd"], None])
    row = write_table(sheet, row,
                      "One account per asset: 100,000 at 1% risk, versus just holding it",
                      ["Asset - Approach", "Final Value", "Return %", "CAGR %",
                       "Max DD %", "Trades Taken"],
                      [24, 13, 11, 10, 10, 12], rows,
                      ["@", "#,##0", "0", "0.0", "0.0", "0"])

    rows = []
    for label, peak, bottom, end in BTC_BEARS:
        curve = results["BITCOIN"]["port_ema"]["curve"]
        e_peak, e_bot, e_end = (equity_at(curve, t) for t in (peak, bottom, end))
        daily = results["BITCOIN"]["daily"]
        def px(when):
            sub = daily[daily.ts <= when]
            return float(sub["close"].iloc[-1])
        rows.append([f"{label}: EMA account", 100 * (e_bot / e_peak - 1),
                     100 * (e_end / e_peak - 1)])
        rows.append([f"{label}: buy & hold", 100 * (px(bottom) / px(peak) - 1),
                     100 * (px(end) / px(peak) - 1)])
    row = write_table(sheet, row,
                      "Bitcoin's two bear markets (peak -> bottom, and peak -> recovery window end)",
                      ["Window", "Peak to Bottom %", "Peak to Window End %"],
                      [26, 16, 20], rows, ["@", "0.0", "0.0"])
    explain(sheet, row, "WHAT IT MEANS", [
        "Bitcoin's bears (-84% in 2018, -77% in 2022 for a holder) are far deeper than "
        "anything in the NSE data, which makes it the sternest test yet of whether the "
        "trend-following exit actually protects. Compare each EMA row against the "
        "buy-and-hold row directly beneath it.",
        "The glossary and full methodology live in 'Full Analysis 199 Stocks.xlsx' -- "
        "this file deliberately reuses the exact same rules with nothing re-tuned.",
    ])

    for symbol, brk_tf, fee, note in ASSETS:
        trades = report.order(results[symbol]["ema"] + results[symbol]["brk"], [symbol])
        rule = (f"{note}  Both strategies' trades, newest first; the 'Line Type' column "
                f"says which rule fired: 'ema stack' or 'all-time high'. Fees here are "
                f"the flat {fee * 100:.2f}%-per-side model, not Zerodha equity charges.")
        report.write_trade_sheet(book, symbol.replace("/", "-"), rule, trades)

    target = report.save(book, "Assets Backtest.xlsx")
    print(f"\n  written: {target}\n")


if __name__ == "__main__":
    main()

"""Test the teacher's scale-out rule: sell half at +1R, let the rest run.

    python -m scripts.scaleout_test --universe assets
    python -m scripts.scaleout_test --universe stocks

Three exit variants, identical entries:

    BASELINE   the exits as validated so far (nothing banked)
    HALF       sell half the position at entry + 1R; the rest runs with the normal
               exit; the stop stays where it was
    HALF + BE  sell half at +1R AND move the stop on the remainder to breakeven
               (the "free trade" version)

Prediction recorded before the results: win rate up, average R and total profit
down -- because these systems earn from rare huge winners and the rule halves
exactly those trades -- with a gentler equity curve as the compensation.
"""
from __future__ import annotations

import argparse

from openpyxl import Workbook

from kitelab import backtest, config, report, sizing, strategies
from scripts.full_report import explain, write_table

MODES = [("BASELINE (no scale-out)", None),
         ("HALF: bank half at +1R", "half"),
         ("HALF + BE: bank half, stop to breakeven", "half_be")]

ASSET_SPECS = {"BITCOIN": ("30m", 0.0010), "NIFTY 50": ("30m", 0.0005),
               "NIFTY BANK": ("30m", 0.0005), "GOLD": ("1d", 0.0005),
               "SILVER": ("1d", 0.0005), "CRUDEOIL": ("1d", 0.0005)}

HEADERS = ["Exit Variant", "Trades", "Banked", "Win %", "Gross", "Charges", "Net",
           "Avg Profit / Trade", "Profit Factor", "Avg R", "Best Trade % of Total",
           "Equity Max DD"]
WIDTHS = [34, 8, 8, 8, 13, 11, 13, 14, 12, 8, 16, 13]
FORMATS = ["@", "0", "0", "0.0", "#,##0", "#,##0", "#,##0", "#,##0", "0.00", "0.00",
           "0.0", "#,##0"]


def equity_max_dd(trades: list[dict]) -> float:
    """Drawdown of the chronological sum of trade P&L -- a smoothness gauge."""
    equity = peak = dd = 0.0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        equity += t["net_profit"]
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return dd


def mode_row(label: str, trades: list[dict]) -> list:
    s = report.stats(label, trades)
    banked = sum(1 for t in trades if "banked" in t["exit_reason"])
    return [label, s.get("trades", 0), banked, s.get("win_rate_pct", 0),
            s.get("gross_profit", 0), s.get("charges", 0), s.get("net_profit", 0),
            s.get("expectancy", 0), s.get("profit_factor", 0), s.get("avg_r", 0),
            s.get("top_share_pct", 0), equity_max_dd(trades)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="assets", choices=["assets", "stocks"])
    args = parser.parse_args()

    if args.universe == "assets":
        jobs = list(ASSET_SPECS.items())
        tag = "Assets"
    else:
        cfg = config.load()
        jobs = [(s, ("30m", None)) for s in cfg.all_symbols]
        tag = "199 Stocks"

    collected = {("EMA", label): [] for label, _ in MODES}
    collected.update({("Breakout", label): [] for label, _ in MODES})
    print(f"\n  universe: {tag} ({len(jobs)} instruments)\n", flush=True)
    for index, (symbol, (brk_tf, fee)) in enumerate(jobs, 1):
        backtest.FLAT_FEE_RATE = fee
        sizing.FRACTIONAL = fee is not None   # fractional units for the assets run
        try:
            for label, mode in MODES:
                try:
                    collected[("EMA", label)].extend(
                        backtest.simulate(symbol, scale_out=mode))
                    collected[("Breakout", label)].extend(
                        strategies.ath_breakout_trades(symbol, True, timeframe=brk_tf,
                                                       scale_out=mode))
                except SystemExit:
                    break
        finally:
            backtest.FLAT_FEE_RATE = None
            sizing.FRACTIONAL = False
        if index % 25 == 0:
            print(f"    {index}/{len(jobs)} instruments done", flush=True)

    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("Scale-Out Test")
    row = explain(sheet, 1, "WHAT THIS SHEET SHOWS", [
        "The teacher's rule under test: once profit equals the initial risk (+1R), sell "
        "HALF the position, then let the rest run. Two versions: with the stop left "
        "alone, and with the stop moved to breakeven (the 'free trade'). Entries are "
        "IDENTICAL in all three rows of each table -- only the exit changes.",
        "PREDICTION, WRITTEN BEFORE THE RESULTS: win rate up, average R and total "
        "profit down (these systems live off rare huge winners, and the rule halves "
        "exactly those trades), equity curve smoother. 'Equity Max DD' is the "
        "drawdown of summed trade P&L -- the smoothness gauge. 'Banked' counts trades "
        "that reached +1R and sold half.",
        f"Universe: {tag}. Same fees and sizing as that universe's main report.",
    ], span=12)
    for strategy in ("EMA", "Breakout"):
        rows = [mode_row(label, collected[(strategy, label)]) for label, _ in MODES]
        row = write_table(sheet, row, f"{strategy} -- three exit variants, same entries",
                          HEADERS, WIDTHS, rows, FORMATS)
    explain(sheet, row, "HOW TO JUDGE IT", [
        "There is no single right answer here -- it is a trade you choose: profit given "
        "up (Net column) in exchange for smoothness bought (Equity Max DD and Win %). "
        "If the drawdown barely improves while the net drops a lot, the rule is a bad "
        "deal for these systems. If the drawdown shrinks meaningfully, it may be worth "
        "it to a trader who abandons strategies during losing streaks -- the best "
        "system is the one you can actually stick with.",
    ], span=12)

    target = report.save(book, f"Scale-Out Test ({tag}).xlsx")
    print(f"\n  written: {target}\n")


if __name__ == "__main__":
    main()

"""Run each strategy across the universes and write one workbook per strategy.

    python -m scripts.wide_test --universe both
    python -m scripts.wide_test --universe out --sequential

Writes output/Breakout Backtest.xlsx and output/EMA Backtest.xlsx. Each holds a Summary
comparing in-sample against out-of-sample, then a trade sheet per group.

IN-SAMPLE is the 49 stocks the parameters were chosen on -- those results are not
evidence of anything. OUT-OF-SAMPLE is 150 randomly drawn NSE companies that no
parameter has ever seen, and nothing is re-tuned for them. That comparison is the
whole point of this script.
"""
from __future__ import annotations

import argparse
import statistics as st

from openpyxl import Workbook

from kitelab import backtest, config, frames, report, strategies

_TURNOVER: dict[str, float] = {}


def median_turnover(symbol: str) -> float:
    """Median daily rupee turnover -- how tradeable this stock actually is."""
    if symbol not in _TURNOVER:
        try:
            daily = frames.daily(symbol).tail(500)
            _TURNOVER[symbol] = float(st.median(daily["close"] * daily["volume"]))
        except Exception:
            _TURNOVER[symbol] = 0.0
    return _TURNOVER[symbol]


def per_stock(trades: list[dict], symbols: list[str]) -> list[dict]:
    rows = []
    for symbol in symbols:
        mine = [t for t in trades if t["symbol"] == symbol]
        if not mine:
            continue
        net = [t["net_profit"] for t in mine]
        wins = [v for v in net if v > 0]
        losses = [v for v in net if v <= 0]
        rows.append({
            "exit_rule": f"   {symbol}", "trades": len(mine),
            "turnover": median_turnover(symbol),
            "wins": len(wins), "win_rate_pct": 100 * len(wins) / len(mine),
            "gross_profit": sum(t["gross_profit"] for t in mine),
            "charges": sum(t["charges"] for t in mine), "net_profit": sum(net),
            "charges_best": sum(t["charges_best"] for t in mine),
            "net_best": sum(t["net_profit_best"] for t in mine),
            "expectancy": sum(net) / len(mine),
            "profit_factor": ((sum(wins) / abs(sum(losses)))
                              if losses and sum(losses) else None),
            "avg_r": sum(t["r_multiple"] for t in mine) / len(mine),
            "best_r": max(t["r_multiple"] for t in mine),
        })
    return rows


RULES = {
    "Breakout": (
        "RULE : price reaches an all-time high, pulls back 3% below it, then a 30-minute "
        "candle closes back above that high -- a break into new ground. Buy-stop at that "
        "candle's high. Stop-loss = the last DAILY pivot low already confirmed at entry "
        "time, then ratcheted up to each newly confirmed daily swing low, never down. "
        "No target, no holding limit. Sizing: Rs100,000 capital, 1% risk, capital-capped."
    ),
    "EMA": (
        "RULE : close is above the 20-EMA on the monthly, weekly AND daily timeframes, by "
        "a 2% margin. Buy at that day's close. Stop-loss = the entry day's low. Exit when "
        "the stop is hit, or when the close falls 2% BELOW any of the three EMAs. The gap "
        "between those two lines is a dead zone that stops whipsaw re-entry. "
        "Sizing: Rs100,000 capital, 1% risk, capital-capped."
    ),
}

NOTE = (
    "IN-SAMPLE = the 49 Nifty Next 50 stocks the parameters were chosen on. Not evidence.\n"
    "OUT-OF-SAMPLE = 150 randomly drawn NSE companies (seed 20260823), never seen by any "
    "parameter. Nothing was re-tuned for them.\n"
    "If out-of-sample profit factor and expectancy hold up, the edge is real. If they "
    "collapse, the in-sample result was fitted.\n"
    "Median Turnover is median daily rupee volume over the last 500 sessions. Below about "
    "Rs1 crore, a Rs100,000 position moves the price and the backtest fills are fiction.\n"
    "'Best Trade % of Gross' above ~50% means the result is one trade, not a strategy."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="both", choices=["in", "out", "both"])
    parser.add_argument("--sequential", action="store_true",
                        help="one open position per stock (drops overlapping signals)")
    args = parser.parse_args()

    cfg = config.load()
    groups = {"in": ("In-Sample", cfg.in_sample), "out": ("Out-of-Sample", cfg.out_of_sample)}
    picked = ["in", "out"] if args.universe == "both" else [args.universe]

    builders = {
        "Breakout": lambda s: strategies.ath_breakout_trades(s, trailing_stops=True),
        "EMA": backtest.simulate,
    }

    print()
    missing: list[str] = []
    for name, build in builders.items():
        book = Workbook()
        book.remove(book.active)
        summary: list[dict] = []
        for group in picked:
            label, members = groups[group]
            raw: list[dict] = []
            for symbol in members:
                try:
                    raw.extend(build(symbol))
                except SystemExit:
                    if symbol not in missing:
                        missing.append(symbol)
            kept = strategies.drop_overlaps(raw) if args.sequential else raw
            trades = report.order(kept, members)
            stats = report.stats(label.upper(), trades)
            stats["turnover"] = None
            summary.append(stats)
            summary.extend(per_stock(trades, members))
            summary.append({"exit_rule": ""})
            report.write_trade_sheet(book, label, RULES[name], trades)
            print(f"  {name:<9} {label:<14} {len(trades):>6,} trades   "
                  f"net {stats.get('net_profit', 0):>12,.0f}   "
                  f"PF {str(report.pf_cell(stats.get('profit_factor'))):>5}   "
                  f"exp {stats.get('expectancy', 0):>7,.0f}   "
                  f"best trade {stats.get('top_share_pct', 0):>4.1f}%")
        note = NOTE + (f"\nNO DATA (run: python -m scripts.backfill --all): "
                       f"{', '.join(missing)}" if missing else "")
        report.write_summary(book, summary, note)
        print(f"  -> {report.save(book, f'{name} Backtest.xlsx')}\n")

    if missing:
        print(f"  !! {len(missing)} stocks have no data: {', '.join(missing[:10])}"
              f"{' ...' if len(missing) > 10 else ''}\n")


if __name__ == "__main__":
    main()

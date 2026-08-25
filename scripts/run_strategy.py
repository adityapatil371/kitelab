"""Backtest one level strategy under both exit rules, into its own workbook.

    python -m scripts.run_strategy support
    python -m scripts.run_strategy breakout
    python -m scripts.run_strategy both

Each workbook holds a Summary comparing the two exit rules, plus one sheet per rule.
"""
from __future__ import annotations

import argparse

from openpyxl import Workbook

from kitelab import config, report, strategies

SPECS = {
    "support": {
        "file": "Support Resistance Strategy.xlsx",
        "label": "Support / Resistance Bounce",
        "build": strategies.support_bounce_trades,
        "rules": {
            "fixed 1.5R target": (
                "RULE : 1. price touches ANY of your lines -- support or resistance, "
                "since an old resistance can act as support. 2. if that 30-minute candle "
                "is red, check the next one. 3. the first green candle whose body "
                "midpoint is above the line is the signal. 4. buy-stop at that candle's "
                "high, stop-loss at its low, target 1.5x the risk. 5. abandon if price "
                "closes decisively below the line while waiting. No signal before the "
                "level's second touch."
            ),
            "swing-low trailing": (
                "RULE : entry identical to the fixed-target sheet. EXIT CHANGED: no "
                "target. The stop starts at the signal candle's low and ratchets up to "
                "each newly confirmed 30-minute swing low, never down. A 5-bar pivot is "
                "only used once confirmed, 5 bars after it forms."
            ),
        },
        "note": (
            "Uses every marked line, support and resistance both.\n"
            "A level marked as both kinds counts once, not twice.\n"
            "Charges: delivery rates are the safe assumption. The intraday columns show what "
            "same-day trades would cost if billed as intraday -- confirm with Zerodha which applies.\n"
            "Check 'Best Trade % of Gross'. Above ~50% means the result is one trade, not a strategy."
        ),
    },
    "breakout": {
        "file": "Breakout Strategy.xlsx",
        "label": "Breakout",
        "build": strategies.breakout_trades,
        "rules": {
            "fixed 1.5R target": (
                "RULE : 1. a resistance level already touched twice is confirmed. "
                "2. a 30-minute candle closes above it AND above the highest price the "
                "stock has ever traded -- a break into new all-time-high ground, not a "
                "bounce back through an old line during a decline. 3. buy-stop at that "
                "candle's high, target 1.5x the risk. 4. stop-loss is the last daily "
                "pivot low already confirmed at entry time."
            ),
            "swing-low trailing": (
                "RULE : entry identical to the fixed-target sheet. EXIT CHANGED: no "
                "target. The stop starts at the confirmed daily pivot low and ratchets "
                "up to each newly confirmed daily swing low, never down. No holding "
                "limit -- trades still open at the end of the data are marked to market "
                "and labelled."
            ),
        },
        "note": (
            "The break must close above the all-time high, per your rule.\n"
            "Previously any close above an old resistance counted, which fired 141 signals -- "
            "none of them at a new high, and many 30-60% below it during declines.\n"
            "Check 'Best Trade % of Gross'. Above ~50% means the result is one trade, not a strategy."
        ),
    },
}


def run(key: str, symbols: list[str]) -> None:
    spec = SPECS[key]
    book = Workbook()
    book.remove(book.active)
    summaries = []
    for label, trailing in (("fixed 1.5R target", False), ("swing-low trailing", True)):
        raw = [t for s in symbols for t in spec["build"](s, trailing_stops=trailing)]
        trades = report.order(strategies.drop_overlaps(raw), symbols)
        summaries.append(report.stats(label, trades))
        report.write_trade_sheet(book, label.replace("1.5R", "1.5R ").title()[:31],
                                 spec["rules"][label], trades)
        print(f"  {spec['label']:<28} {label:<20} {len(trades):>4} trades "
              f"({len(raw)} raw)")
    report.write_summary(book, summaries, spec["note"])
    print(f"  -> {report.save(book, spec['file'])}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("which", nargs="?", default="both",
                        choices=["support", "breakout", "both"])
    args = parser.parse_args()
    cfg = config.load()
    print()
    for key in (["support", "breakout"] if args.which == "both" else [args.which]):
        run(key, cfg.symbols)


if __name__ == "__main__":
    main()

"""How many stocks does Darvas actually need?

    python -m scripts.darvas_breadth

Darvas earned 18.5% across the whole universe and 5.7% on a ten-stock basket, with a
stop about 13% below entry (measured 2026-08-31 at 1% risk, Rs2,50,000; these four
figures are a snapshot of that run, not recomputed on import -- the table this
script prints is the live version and wins any disagreement). That is not a
stock-picking effect, it is a capital-deployment one: the stop sits far enough
below entry that at 1% risk each position is roughly an eighth of the account, and
the rule needs a dozen or so running at once to have its money working.

So the question "how many stocks" is really "how many does it take before the
account is fully invested". This draws random baskets at a range of sizes and
reports both halves: what you earn, and whether the money was working. The
telling column is signals turned away for lack of cash -- while that is zero the
account is idling, and more stocks will still help.

The EMA stack runs alongside as a control: its stop is much closer to entry (3.9%
in that same 2026-08-31 run), so each position is a quarter of the account and it
should saturate far sooner.
"""
from __future__ import annotations

import random

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from kitelab import config, portfolio, report, signals

CAPITAL = 250_000.0
# The whole universe is appended at run time, whatever size it currently is.
SIZES = [5, 10, 15, 20, 30, 50, 75, 100, 150]
SEED = 20260831
# Big baskets are slow to simulate and barely vary; small ones are fast and wild.
DRAWS = {5: 60, 10: 60, 15: 60, 20: 60, 30: 40, 50: 40, 75: 25, 100: 25, 150: 15}
# Turtle_w20_* is the two-timeframe rule the dashboard now builds. The old
# "Darvas_20_10_all" is pre-gate data -- same label, different strategy.
STRATEGIES = [("Turtle 20/10 +weekly", "Turtle_w20_20_10_all"),
              ("Turtle 20/10 daily-only", "Turtle_1tf_20_10_all"),
              ("EMA M/W/D", "EMA_all")]
RISKS = [0.5, 1.0, 2.0]


def load(name: str, symbols) -> dict:
    """Cached trades grouped by symbol. signals.require refuses a cache built
    from a different universe, different price files or different code."""
    trades = signals.require(name, symbols)
    by_symbol: dict[str, list] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    return by_symbol


def sweep(by_symbol, symbols, risk_pct) -> list[dict]:
    rows = []
    # SIZES stops below the universe; the whole universe is the final row, one
    # draw, because there is only one way to pick all of them.
    sizes = [n for n in SIZES if n < len(symbols)] + [len(symbols)]
    for size in sizes:
        rng = random.Random(SEED + size)
        draws = ([sorted(symbols)] if size >= len(symbols)
                 else [rng.sample(symbols, size) for _ in range(DRAWS[size])])
        cagr, dd, concurrent, starved, taken = [], [], [], [], []
        for basket in draws:
            subset = [t for s in basket for t in by_symbol.get(s, [])]
            if not subset:
                continue
            r = portfolio.run(subset, CAPITAL, risk_pct / 100)
            cagr.append(r["cagr_pct"])
            dd.append(r["max_drawdown_pct"])
            concurrent.append(r["max_concurrent"])
            taken.append(len(r["taken"]))
            starved.append(100 * r["skipped_cash"] / max(r["signals"], 1))
        if not cagr:
            continue
        # Wiped baskets have no CAGR. They are counted, not averaged in as 0 --
        # a 0 would sit above every basket that merely lost money.
        n_wiped = sum(1 for c in cagr if c is None)
        cagr = [c for c in cagr if c is not None]
        if not cagr:
            rows.append({"size": size, "draws": n_wiped, "wiped": n_wiped,
                         "median": None, "p10": None, "p90": None, "worst": None,
                         "best": None, "spread": None, "losing": 100,
                         "median_dd": round(float(np.median(dd)), 1),
                         "concurrent": round(float(np.median(concurrent)), 1),
                         "starved": round(float(np.median(starved)), 1),
                         "taken": int(np.median(taken))})
            continue
        rows.append({
            "size": size, "draws": len(cagr) + n_wiped, "wiped": n_wiped,
            "median": round(float(np.median(cagr)), 2),
            "p10": round(float(np.percentile(cagr, 10)), 2),
            "p90": round(float(np.percentile(cagr, 90)), 2),
            "worst": round(min(cagr), 2), "best": round(max(cagr), 2),
            "spread": round(float(np.percentile(cagr, 90) - np.percentile(cagr, 10)), 2),
            "losing": round(100 * (n_wiped + sum(1 for c in cagr if c < 0))
                            / (len(cagr) + n_wiped)),
            "median_dd": round(float(np.median(dd)), 1),
            "concurrent": round(float(np.median(concurrent)), 1),
            "starved": round(float(np.median(starved)), 1),
            "taken": int(np.median(taken)),
        })
    return rows


def show(title, rows) -> None:
    print(f"\n  {title}")
    print(f"  {'stocks':>7}{'draws':>7}{'median':>9}{'p10':>8}{'p90':>8}{'spread':>8}"
          f"{'lost money':>12}{'max held':>10}{'cash-starved':>14}")
    print("  " + "-" * 83)
    for r in rows:
        print(f"  {r['size']:>7}{r['draws']:>7}{r['median']:>8.1f}%{r['p10']:>7.1f}%"
              f"{r['p90']:>7.1f}%{r['spread']:>7.1f}{r['losing']:>11}%"
              f"{r['concurrent']:>10.0f}{r['starved']:>13.0f}%", flush=True)


def main() -> None:
    cfg = config.load()
    symbols = sorted(cfg.all_symbols)
    book = Workbook()
    book.remove(book.active)

    sheet = book.create_sheet("How many stocks")
    sheet["A1"] = (
        "Random baskets of each size, one Rs2,50,000 account at the stated risk, perfect "
        "fills. 'cash-starved' is the share of signals turned away because the money was "
        "already committed -- while it reads 0% the account is idling and more stocks "
        "will still help; once it climbs, adding stocks mostly changes WHICH trades you "
        "take rather than how many. 'spread' is p90 minus p10, i.e. how much your result "
        "depends on which stocks you happened to pick.")
    columns = [("Strategy", 15), ("Risk %", 9), ("Stocks", 9), ("Baskets", 9),
               ("Median CAGR %", 14), ("p10", 9), ("p90", 9), ("Spread p90-p10", 15),
               ("% of baskets that lost", 21), ("Median max DD %", 16),
               ("Median positions held", 20), ("Signals starved of cash %", 24),
               ("Median trades taken", 19)]
    for index, (name, width) in enumerate(columns, start=1):
        sheet.cell(3, index, name).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[3]:
        cell.fill = PatternFill("solid", fgColor="BDD7EE")
    line = 4

    def dump(label, risk, rows):
        nonlocal line
        for r in rows:
            values = [label, risk, r["size"], r["draws"], r["median"], r["p10"], r["p90"],
                      r["spread"], r["losing"], r["median_dd"], r["concurrent"],
                      r["starved"], r["taken"]]
            for index, value in enumerate(values, start=1):
                cell = sheet.cell(line, index, value)
                if index in (5, 6, 7, 8, 10, 12):
                    cell.number_format = "0.0"
            line += 1

    for label, cache in STRATEGIES:
        by_symbol = load(cache, symbols)
        rows = sweep(by_symbol, symbols, 1.0)
        show(f"{label} at 1% risk", rows)
        dump(label, 1.0, rows)

    turtle = load("Turtle_w20_20_10_all", symbols)
    for risk in RISKS:
        if risk == 1.0:
            continue
        rows = sweep(turtle, symbols, risk)
        show(f"Turtle 20/10 +weekly at {risk}% risk", rows)
        dump("Turtle 20/10 +weekly", risk, rows)

    print(f"\n  written: {report.save(book, 'Darvas Breadth.xlsx')}")


if __name__ == "__main__":
    main()

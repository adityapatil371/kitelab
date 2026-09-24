"""The 56 trades the Pine script has to reproduce, in TradingView's own shape.

WHY THIS EXISTS. pine/pullback_uptrend.pine is a mirror of the lab's `pull`
rule with the 3 x ATR stop, and a mirror is only worth anything if someone
checks it. TradingView's Strategy Tester has a "List of Trades" tab with an
export button; this writes the workbook's trades in the same column order, so
the two files can be put side by side and read across.

READS   output/measurements/hg_vs_pull_RELIANCE.xlsx, sheet "pull - 3xATR stop"
        (built by scripts/compare_hg_pull.py; nothing is re-simulated here)
WRITES  output/measurements/pull_atr3_RELIANCE_pine_check.csv

Oldest first, which is the order TradingView exports in -- the workbook sheet
is newest first because that is the class layout. The exit DATE is not a
column in the sheet; it is rebuilt as entry date + "Days held", which is the
calendar gap between the two stamps, so it is exact rather than approximate.
"""
import csv
import datetime
import os

import openpyxl

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "output")
MEAS = os.path.join(OUT, "measurements")
BOOK = os.path.join(MEAS, "hg_vs_pull_RELIANCE.xlsx")
SHEET = "pull - 3xATR stop"
DEST = os.path.join(MEAS, "pull_atr3_RELIANCE_pine_check.csv")

# Every column this script reads. Missing one is a changed workbook, not a
# recoverable condition.
REQUIRED = ["Trade #", "Date", "Entry Price", "Stop Loss", "Exit",
            "No. of Shares", "Cost of Entry", "Profit", "Why it ended",
            "Days held"]


def main() -> None:
    book = openpyxl.load_workbook(BOOK, data_only=True)
    if SHEET not in book.sheetnames:
        raise SystemExit(f"{BOOK} has no sheet {SHEET!r}; found {book.sheetnames}")
    sheet = book[SHEET]

    header = [cell.value for cell in sheet[5]]
    rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=6)]
    print(f"loaded {SHEET}: {len(rows)} rows x {len(header)} columns")
    print("first 3 rows (the sheet is newest first):")
    for row in rows[:3]:
        print("   ", row[:6])

    missing = [name for name in REQUIRED if name not in header]
    if missing:
        raise SystemExit(f"{BOOK}:{SHEET} is missing required columns: {missing}")
    where = {name: header.index(name) for name in REQUIRED}

    blank = [row for row in rows if row[where["Date"]] is None]
    print(f"rows before dropping blanks: {len(rows)}")
    rows = [row for row in rows if row[where["Date"]] is not None]
    print(f"rows after  dropping blanks: {len(rows)}   (dropped {len(blank)})")
    if not rows:
        raise SystemExit("no trades left to write")

    rows.sort(key=lambda row: row[where["Date"]])
    print(f"re-sorted oldest first: {rows[0][where['Date']].date()} "
          f".. {rows[-1][where['Date']].date()}")

    with open(DEST, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["trade", "date in", "price in", "stop", "date out",
                      "price out", "qty", "buy value", "profit (gross)",
                      "cum profit", "why it ended", "days held"])
        running = 0.0
        for number, row in enumerate(rows, start=1):
            profit = float(row[where["Profit"]])
            running += profit
            entry_on = row[where["Date"]]
            out.writerow([
                number,
                entry_on.date().isoformat(),
                round(float(row[where["Entry Price"]]), 2),
                round(float(row[where["Stop Loss"]]), 2),
                # The sheet has no exit-date column, but "Days held" is
                # calendar days between the two stamps, so the exit date is
                # exactly recoverable -- no re-simulation needed.
                (entry_on + datetime.timedelta(
                    days=int(row[where["Days held"]]))).date().isoformat(),
                round(float(row[where["Exit"]]), 2),
                int(row[where["No. of Shares"]]),
                round(float(row[where["Cost of Entry"]]), 2),
                round(profit, 2),
                round(running, 2),
                row[where["Why it ended"]],
                int(row[where["Days held"]]),
            ])

    print(f"wrote {len(rows)} trades to {DEST}")
    print(f"total gross profit: Rs {running:,.2f}   "
          f"(the Strategy Tester's Net Profit at 0% commission)")


if __name__ == "__main__":
    main()

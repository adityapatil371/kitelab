"""Apply the entry-candle-low stop loss to the DI +/- table.

Ad-hoc, not part of the pipeline (see experiments/turtle_vs_ema_hal.py for the precedent).

The DI table already SIZED its positions off the entry candle's low -- share
counts match min(floor(1000 / (entry - low)), floor(100000 / entry)) on all
106 trades -- but it never used that low as an EXIT. Trades ran to the DI
crossover however far the price fell first, so losses ran past the 1000 rupee
risk budget (largest was -6646). The 20 EMA table on the same sheet does apply
the stop, so the two columns were not comparable.

Rules, all verified against the 84 EMA trades before being applied here:
  entry price = the entry bar's CLOSE            (84/84 on EMA, 106/106 on DI)
  stop        = the entry bar's LOW              (84/84 on EMA)
  shares      = min(floor(1000/(entry-stop)), floor(100000/entry))   (84/84)
  exit        = the stop, on the first bar after entry whose low touches it;
                if that bar OPENED below the stop, the open instead (EMA trade
                69 exits at 87.00 against a 93.80 stop, so that is the sheet's
                own convention). Otherwise the original DI signal exit.

Exit dates are not recorded in the sheet, so they are recovered by matching
each recorded exit price to the first close after entry. That reproduces the
sheet's own "Average days held" for DI to 22.84 vs 22.80, which is the check
that the recovered dates are right.

Reads : ema vs di manappuram.xlsx, /data/clean/kitelab/MANAPPURAM_day.parquet
Writes: ema vs di manappuram.xlsx (in place, after a timestamped backup)
"""

from __future__ import annotations

import datetime as dt
import pathlib
import shutil

import numpy as np
import openpyxl
import pandas as pd

BOOK = pathlib.Path("/work/kitelab/ema vs di manappuram.xlsx")
BARS = pathlib.Path("/data/clean/kitelab/MANAPPURAM_day.parquet")

RISK = 1000.0        # rupees risked per trade
CAP = 100_000.0      # rupee cap on a single position

DI_HEADER_ROW = 110              # Excel row of the DI table's header
DI_FIRST, DI_LAST = 111, 216     # Excel rows of the DI trades
CMP_FIRST, CMP_LAST = 3, 15      # Excel rows of the comparison metrics
COLS = ["n", "date", "entry", "stop", "exit", "shares",
        "cost", "cumcost", "profit", "cumprofit", "pps"]


def load_bars() -> pd.DataFrame:
    d = pd.read_parquet(BARS)
    d["ts"] = pd.to_datetime(d["ts"]).dt.normalize()
    d = d.set_index("ts").sort_index()
    print(f"bars: {len(d)} rows, {len(d.columns)} cols, "
          f"{d.index.min().date()} .. {d.index.max().date()}")
    print(d.head(3).to_string())
    return d


def load_table(skiprows: int, nrows: int, label: str) -> pd.DataFrame:
    t = pd.read_excel(BOOK, header=None, skiprows=skiprows, nrows=nrows, names=COLS)
    t["date"] = pd.to_datetime(t["date"]).dt.normalize()
    print(f"\n{label}: {len(t)} rows, {len(t.columns)} cols")
    print(t.head(3).to_string(index=False))
    if t["date"].isna().any() or t["entry"].isna().any() or t["shares"].isna().any():
        raise SystemExit(f"{label}: missing Date/Entry/Shares -- refusing to guess")
    return t


def check_ema_rules(e: pd.DataFrame, d: pd.DataFrame) -> None:
    """The EMA table is the specification. If it stops matching, stop."""
    j = e.join(d[["low", "close", "open"]], on="date")
    if j["low"].isna().any():
        raise SystemExit("EMA: some entry dates have no price bar")
    if not ((j["entry"] - j["close"]).abs() < 0.005).all():
        raise SystemExit("EMA: entry price is not the entry bar's close")
    if not ((j["stop"] - j["low"]).abs() < 0.005).all():
        raise SystemExit("EMA: stop is not the entry bar's low")
    want = np.minimum(np.floor(RISK / (e["entry"] - e["stop"])),
                      np.floor(CAP / e["entry"]))
    if not (want == e["shares"]).all():
        raise SystemExit("EMA: share count does not match the sizing rule")
    print("\nEMA table matches all four rules (close entry, low stop, sizing, "
          f"{len(e)}/{len(e)}) -- using them for DI")


def exit_bar(d: pd.DataFrame, entry_date, exit_px):
    """The first bar after entry whose close is the recorded exit price."""
    after = d.loc[d.index > entry_date]
    hit = after.index[(after["close"] - exit_px).abs() < 0.005]
    if len(hit) == 0:
        raise SystemExit(f"no bar closes at {exit_px} after {entry_date.date()}")
    return hit[0]


def apply_stop(di: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in di.iterrows():
        stop = round(float(d.at[r["date"], "low"]), 2)
        sig = exit_bar(d, r["date"], r["exit"])
        win = d.loc[(d.index > r["date"]) & (d.index <= sig)]
        touched = win.index[win["low"] <= stop + 1e-9]
        if len(touched):
            b = touched[0]
            gap = float(d.at[b, "open"])
            px = round(gap, 2) if gap < stop - 1e-9 else stop
            rows.append((int(r["n"]), r["date"], r["entry"], stop, px, b, True,
                         r["shares"], r["exit"], sig))
        else:
            rows.append((int(r["n"]), r["date"], r["entry"], stop, r["exit"], sig,
                         False, r["shares"], r["exit"], sig))
    n = pd.DataFrame(rows, columns=["n", "date", "entry", "stop", "exit", "exit_date",
                                    "stopped", "shares", "old_exit", "old_exit_date"])
    n["pps"] = (n["exit"] - n["entry"]).round(2)
    n["profit"] = (n["shares"] * n["pps"]).round(2)
    n["old_profit"] = (n["shares"] * (n["old_exit"] - n["entry"])).round(2)
    n["cost"] = (n["shares"] * n["entry"]).round(2)
    n["cumprofit"] = n["profit"].cumsum().round(2)
    return n


def stats(profit: pd.Series, cost: pd.Series, days) -> dict:
    """Wins are profit > 0; everything else is a loss, which is how the sheet
    counted its one flat trade (#93, entry == exit) as one of 53 losses."""
    w, l = profit[profit > 0], profit[profit <= 0]
    return {
        "Trades": len(profit), "Wins": len(w), "Losses": len(l),
        "Win rate": len(w) / len(profit), "Profit": profit.sum(),
        "Average win": w.mean(), "Average loss": l[l < 0].mean(),
        "Profit factor": w.sum() / abs(l.sum()) if l.sum() else float("nan"),
        "Expectancy per trade": profit.mean(),
        "Largest win": profit.max(), "Largest loss": profit.min(),
        "Average days held": float(np.mean(days)), "Capital deployed": cost.sum(),
    }


def main() -> None:
    d = load_bars()
    ema = load_table(22, 84, "EMA table")
    di = load_table(110, 106, "DI table")
    check_ema_rules(ema, d)

    if di["stop"].notna().any():
        raise SystemExit("DI Stop Loss column is not empty -- already applied?")
    sized = np.minimum(np.floor(RISK / (di["entry"] - d.loc[di["date"], "low"].values)),
                       np.floor(CAP / di["entry"]))
    print(f"DI shares already consistent with a candle-low stop: "
          f"{int((sized == di['shares']).sum())}/{len(di)}")

    n = apply_stop(di, d)
    before = stats(n["old_profit"], n["cost"], (n["old_exit_date"] - n["date"]).dt.days)
    after = stats(n["profit"], n["cost"], (n["exit_date"] - n["date"]).dt.days)

    print(f"\nDI trades: {len(n)} before, {len(n)} after (no trade dropped)")
    print(f"stopped out by the candle low: {int(n['stopped'].sum())} of {len(n)}")
    print(f"unchanged (signal exit first) : {int((~n['stopped']).sum())} of {len(n)}")
    print(f"\n{'metric':<22}{'DI before':>15}{'DI after':>15}")
    for k in before:
        print(f"{k:<22}{before[k]:>15,.4f}{after[k]:>15,.4f}")

    bak = BOOK.with_name(f"{BOOK.stem} BACKUP {dt.datetime.now():%Y%m%d-%H%M%S}.xlsx")
    shutil.copy2(BOOK, bak)
    print(f"\nbackup written: {bak.name}")

    wb = openpyxl.load_workbook(BOOK)
    ws = wb["Sheet1"]
    if ws[f"A{DI_HEADER_ROW}"].value != "Trade #" or ws["A2"].value != "COMPARISON":
        raise SystemExit("workbook layout is not what this script expects")

    ws[f"L{DI_HEADER_ROW}"] = "Exit Date"
    ws[f"M{DI_HEADER_ROW}"] = "Stopped?"
    for i, (_, r) in enumerate(n.iterrows()):
        row = DI_FIRST + i
        if ws[f"A{row}"].value != r["n"]:
            raise SystemExit(f"row {row} is trade {ws[f'A{row}'].value}, expected {r['n']}")
        ws[f"D{row}"] = r["stop"]
        ws[f"E{row}"] = r["exit"]
        ws[f"I{row}"] = r["profit"]
        ws[f"J{row}"] = r["cumprofit"]
        ws[f"K{row}"] = r["pps"]
        ws[f"L{row}"] = r["exit_date"].to_pydatetime()
        ws[f"M{row}"] = "STOP" if r["stopped"] else ""

    order = [ws[f"A{r}"].value for r in range(CMP_FIRST, CMP_LAST + 1)]
    for r, label in zip(range(CMP_FIRST, CMP_LAST + 1), order):
        if label not in after:
            raise SystemExit(f"comparison row {r} is {label!r}, not a metric I computed")
        ws[f"C{r}"] = after[label]
    print(f"comparison DI column rewritten for {len(order)} metrics")

    wb.save(BOOK)
    print(f"saved: {BOOK.name}")


if __name__ == "__main__":
    main()

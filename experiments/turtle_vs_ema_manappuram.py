"""20 EMA vs Turtle 55/20 on MANAPPURAM, both with a candle-low stop.

Ad-hoc comparison, not part of the pipeline. Follows experiments/turtle_vs_ema_hal.py,
with one difference the user asked for: there are no hand-drawn rectangles
this time, so BOTH systems are generated from the price data here.

Both systems carry the SAME stop -- the entry bar's own low, fixed for the
life of the trade -- so the only thing that differs between the two tables is
the entry signal.

System "20 EMA": kitelab's M/W/D 20-EMA rule, backtest.simulate(band=0.02,
  stack="mwd", stop_on_close=False). Verified 2026-09-08 to reproduce the 84
  EMA trades in "ema vs di manappuram.xlsx" exactly -- entry price, stop, exit
  price and share count all 84/84 -- so this is the sheet's own system,
  regenerated rather than read. simulate's stop already IS the entry candle's
  low; the script asserts that rather than assuming it.

System "Turtle 55/20": long-only Donchian breakout, written here.
  entry = close above the highest HIGH of the prior 55 bars, taken only when flat
  stop  = the entry bar's LOW
  exit  = whichever comes first --
            * a later bar's low touches the stop -> filled at the stop, or at
              that bar's OPEN if it gapped below (the workbook's own convention)
            * close below the lowest LOW of the prior 20 bars -> at that close
  One position at a time; a trade still open on the last bar is marked to it.

Two other stops were tested on the Turtle on 2026-09-08 and are NOT in the
output, but are recorded here so the choice is not silently lost:
  most recent swing low (pivot, 2 bars either side): 32 trades, -415
  2 x ATR(20), the original Turtle "2N" stop:        36 trades, -5,680
  the entry candle's low (this script):              44 trades, +15,410
The candle-low variant wins on profit and loses on everything else: a 13.6%
win rate, 31 of 44 trades stopped out, and its whole result is one trade
(2016-03-08, +41,900). Strip each column's best trade and candle-low is the
WORST of the three at -26,490. Read the profit row with that in mind.

Both systems, identically:
  entry price = the signal bar's close (quoted, pre-slippage, no charges)
  shares      = min(floor(1000 / (entry - stop)), floor(100000 / entry))
                -- risk 1,000 per trade, capped at 100,000 in one position,
                the convention the workbook itself uses
  span        = 2014-05-13 (the first EMA trade) to the last bar on file

Reads : /data/clean/kitelab/MANAPPURAM_day.parquet
        /work/kitelab/ema vs di manappuram.xlsx   (read-only, for the cross-check)
Writes: /work/kitelab/output/turtle_vs_ema_manappuram.xlsx   (new file)
Runs in seconds.
"""
import numpy as np
import pandas as pd

from kitelab import backtest

SYMBOL = "MANAPPURAM"
BARS = "/data/clean/kitelab/MANAPPURAM_day.parquet"
BOOK = "/work/kitelab/ema vs di manappuram.xlsx"
OUT = "/work/kitelab/output/turtle_vs_ema_manappuram.xlsx"
SHEET = "EMA vs Turtle MANAPPURAM"
RISK, CAP = 1000.0, 100_000.0
ENTRY_LOOKBACK, EXIT_LOOKBACK = 55, 20

# ---------------------------------------------------------------- prices ----
d = pd.read_parquet(BARS)
print(f"{BARS}: {d.shape[0]} rows, {d.shape[1]} cols")
print(d.head(3).to_string(index=False))
need = {"ts", "open", "high", "low", "close"}
if need - set(d.columns):
    raise SystemExit(f"missing required columns: {sorted(need - set(d.columns))}")
d["ts"] = pd.to_datetime(d["ts"]).dt.normalize()
d = d.set_index("ts").sort_index()
if d[["open", "high", "low", "close"]].isna().any().any():
    raise SystemExit("price bars contain missing OHLC values -- refusing to guess")

o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
dates, n = d.index, len(d)


def size(entry: float, stop: float) -> int:
    return int(min(RISK // (entry - stop), CAP // entry))


# ------------------------------------------------------------ 20 EMA side ----
COLS = ["n", "date", "entry", "stop", "exit", "shares", "cost", "cumcost",
        "profit", "cumprofit", "pps"]
sheet = pd.read_excel(BOOK, header=None, skiprows=22, nrows=84, names=COLS)
sheet["date"] = pd.to_datetime(sheet["date"]).dt.normalize()
SPAN_START = sheet["date"].min()
print(f"\nworkbook EMA table: {sheet.shape[0]} rows -- span starts {SPAN_START.date()}")

raw = backtest.simulate(SYMBOL, band=0.02, stack="mwd", stop_on_close=False)
print(f"backtest.simulate({SYMBOL}, band=0.02, stack='mwd', stop_on_close=False): "
      f"{len(raw)} trades, all history")
ema_tr = [t for t in raw if pd.Timestamp(t["entry_date"]).normalize() >= SPAN_START]
print(f"  restricted to entry >= {SPAN_START.date()}: {len(ema_tr)} trades")

rows = []
for t in sorted(ema_tr, key=lambda t: t["entry_date"]):
    ed = pd.Timestamp(t["entry_date"]).normalize()
    xd = pd.Timestamp(t["exit_date"]).normalize()
    entry = round(t["quoted_entry"], 2)
    stop = round(t["stop"], 2)
    exit_px = round(t["quoted_exit"], 2)
    if entry <= stop:
        raise SystemExit(f"EMA trade {ed.date()}: entry {entry} <= stop {stop}")
    sh = size(entry, stop)
    rows.append({"Entry Date": ed, "Entry Price": entry, "Stop Loss": stop,
                 "Exit Date": xd, "Exit Price": exit_px, "Shares": sh,
                 "Cost of Entry": round(sh * entry, 2),
                 "Profit": round(sh * (exit_px - entry), 2),
                 "Days Held": (xd - ed).days, "Exit Reason": t["exit_reason"]})
ema = pd.DataFrame(rows)

chk = sheet.merge(ema, left_on="date", right_on="Entry Date")
print(f"  cross-check against the workbook: {len(chk)} of {len(sheet)} dates matched, "
      f"entry {int((chk['entry'] - chk['Entry Price']).abs().lt(.005).sum())}/{len(chk)}, "
      f"stop {int((chk['stop'] - chk['Stop Loss']).abs().lt(.005).sum())}/{len(chk)}, "
      f"exit {int((chk['exit'] - chk['Exit Price']).abs().lt(.005).sum())}/{len(chk)}, "
      f"shares {int((chk['shares'] == chk['Shares']).sum())}/{len(chk)}")
if len(chk) != len(sheet):
    raise SystemExit("regenerated EMA trades do not line up with the workbook")

entry_lows = np.round(d.loc[ema["Entry Date"], "low"].to_numpy(float), 2)
if not np.allclose(ema["Stop Loss"].to_numpy(float), entry_lows, atol=0.005):
    raise SystemExit("EMA stop is not the entry candle's low -- convention changed")
print("  stop == entry candle low on every EMA trade: confirmed")

# ----------------------------------------------------------- Turtle side ----
# Donchian levels use strictly PRIOR bars: shift(1) so the signal bar is never
# part of the level it has to break.
hi55 = pd.Series(h).rolling(ENTRY_LOOKBACK).max().shift(1).to_numpy()
lo20 = pd.Series(l).rolling(EXIT_LOOKBACK).min().shift(1).to_numpy()

rows, skipped, still_open = [], 0, 0
i = ENTRY_LOOKBACK
while i < n:
    if not (np.isfinite(hi55[i]) and c[i] > hi55[i]):
        i += 1
        continue
    entry = round(float(c[i]), 2)
    stop = round(float(l[i]), 2)
    if stop >= entry:                     # a bar that closed on its own low
        skipped += 1
        i += 1
        continue
    sh = size(entry, stop)
    k, exit_px, exit_i, reason = i + 1, None, None, None
    while k < n:
        if l[k] <= stop + 1e-9:
            exit_px = round(float(o[k]), 2) if o[k] < stop - 1e-9 else stop
            exit_i = k
            reason = "gap through stop" if o[k] < stop - 1e-9 else "stop"
            break
        if np.isfinite(lo20[k]) and c[k] < lo20[k]:
            exit_px, exit_i, reason = round(float(c[k]), 2), k, "20-day low exit"
            break
        k += 1
    if exit_px is None:
        exit_px, exit_i, reason = round(float(c[-1]), 2), n - 1, "open at data end"
        still_open += 1
    rows.append({"Entry Date": dates[i], "Entry Price": entry, "Stop Loss": stop,
                 "Exit Date": dates[exit_i], "Exit Price": exit_px, "Shares": sh,
                 "Cost of Entry": round(sh * entry, 2),
                 "Profit": round(sh * (exit_px - entry), 2),
                 "Days Held": (dates[exit_i] - dates[i]).days, "Exit Reason": reason})
    i = exit_i + 1                        # flat until the trade closes
turtle_all = pd.DataFrame(rows)
print(f"\nTurtle 55/20 breakouts taken, all history: {len(turtle_all)} trades"
      + (f" ({skipped} skipped: entry bar closed at or below its own low)" if skipped else ""))
turtle = turtle_all[turtle_all["Entry Date"] >= SPAN_START].reset_index(drop=True)
print(f"  restricted to entry >= {SPAN_START.date()}: {len(turtle)} trades"
      + (f", {still_open} still open on the last bar" if still_open else ""))

# ------------------------------------------------------------------ tables ---
print(f"\ncomparison span: {SPAN_START.date()} to {dates.max().date()}")
COLUMNS = ["Entry Date", "Entry Price", "Stop Loss", "Exit Date", "Exit Price",
           "Shares", "Cost of Entry", "Profit", "Return %", "Days Held",
           "Cum Profit", "Exit Reason"]


def finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Return %"] = (100 * df["Profit"] / df["Cost of Entry"]).round(2)
    df["Cum Profit"] = df["Profit"].cumsum().round(2)
    df["Entry Date"] = df["Entry Date"].dt.date
    df["Exit Date"] = df["Exit Date"].dt.date
    df = df[COLUMNS]
    df.insert(0, "Trade #", range(1, len(df) + 1))
    return df


ema_out, turtle_out = finish(ema), finish(turtle)

pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)
print(f"\n=== 20 EMA, candle-low stop, {SYMBOL} ({len(ema_out)} trades) ===")
print(ema_out.to_string(index=False))
print(f"\n=== Turtle 55/20, candle-low stop, {SYMBOL} ({len(turtle_out)} trades) ===")
print(turtle_out.to_string(index=False))


def metrics(df: pd.DataFrame) -> dict:
    """Wins are Profit > 0; a flat trade counts as a loss, as the workbook does."""
    w, ls = df[df["Profit"] > 0], df[df["Profit"] <= 0]
    gross_win, gross_loss = w["Profit"].sum(), -ls["Profit"].sum()
    p = df["Profit"]
    return {"Trades": len(df), "Wins": len(w), "Losses": len(ls),
            "Win rate": f"{100 * len(w) / len(df):.1f}%",
            "Profit": round(p.sum(), 2),
            "Average win": round(w["Profit"].mean(), 2) if len(w) else 0.0,
            "Average loss": round(ls["Profit"].mean(), 2) if len(ls) else 0.0,
            "Profit factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
            "Expectancy per trade": round(p.mean(), 2),
            "Median trade": round(p.median(), 2),
            "Largest win": round(p.max(), 2),
            "Largest loss": round(p.min(), 2),
            "Profit without best trade": round(p.sum() - p.max(), 2),
            "Average days held": round(df["Days Held"].mean(), 1),
            "Capital deployed": round(df["Cost of Entry"].sum(), 2)}


me, mt = metrics(ema_out), metrics(turtle_out)
keys = list(me)
comparison = pd.DataFrame({"Metric": keys,
                           "20 EMA (candle-low stop)": [me[k] for k in keys],
                           "Turtle 55/20 (candle-low stop)": [mt[k] for k in keys]})
print("\n=== COMPARISON ===")
print(comparison.to_string(index=False))
print("\nExit reason mix:")
for name, df in (("20 EMA", ema_out), ("Turtle 55/20", turtle_out)):
    print(f"  {name:<14}" + ", ".join(f"{k}: {v}" for k, v in
                                      df["Exit Reason"].value_counts().items()))

# ------------------------------------------------------------------- excel ---
NOTES = [
    "Both systems carry the same stop: the entry bar's own low, fixed for the "
    "life of the trade. The only difference between the two tables is the entry "
    "signal.",
    "Sizing on both: shares = min(1000 / (entry - stop), 100000 / entry) -- "
    "risk 1,000 per trade, capped at 100,000 in one position, the same "
    "convention as 'ema vs di manappuram.xlsx'.",
    "Prices are quoted closes, pre-slippage, with no brokerage or taxes "
    "deducted, on both sides.",
    "'Profit without best trade' is the row to read next to 'Profit'. Each "
    "system's result rests on a single 2016 trade; without it the 20 EMA loses "
    "1,838 and the Turtle loses 26,490.",
    "A stop this tight can be gapped through, and then the loss exceeds the "
    "1,000 budget -- see the 'gap through stop' rows.",
]


def block(xl, title: str, df: pd.DataFrame, row: int) -> int:
    pd.DataFrame({title: []}).to_excel(xl, sheet_name=SHEET, index=False, startrow=row)
    df.to_excel(xl, sheet_name=SHEET, index=False, startrow=row + 2)
    return row + 2 + len(df) + 3


with pd.ExcelWriter(OUT, engine="openpyxl") as xl:
    r = 0
    r = block(xl, f"20 EMA, candle-low stop, {SYMBOL} ({len(ema_out)} trades)", ema_out, r)
    r = block(xl, f"Turtle 55/20, candle-low stop, {SYMBOL} ({len(turtle_out)} trades)",
              turtle_out, r)
    r = block(xl, f"COMPARISON  ({SPAN_START.date()} to {dates.max().date()})",
              comparison, r)
    for note in NOTES:
        pd.DataFrame({note: []}).to_excel(xl, sheet_name=SHEET, index=False, startrow=r)
        r += 1
    ws = xl.sheets[SHEET]
    for col, width in zip("ABCDEFGHIJKLM",
                          (8, 12, 12, 11, 12, 12, 9, 14, 11, 10, 11, 13, 18)):
        ws.column_dimensions[col].width = width

print(f"\nsaved: {OUT} (one sheet: {SHEET!r})")

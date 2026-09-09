"""Ad-hoc comparison, not part of the pipeline (iterate in chat first).

System "Turtle 55/20": the 11 HAL rectangles in rectangles.csv are the
user's own manual Turtle 55/20-day breakout trades (entry/exit dates only;
priced here at that day's close).
System "EMA M/W/D": kitelab's own M/W/D 20-EMA strategy (backtest.simulate),
band=0.02 and stop_on_close=False -- the exact rule written in the DI+
reference sheet's header, run with the convention noted as reproducing
that sheet 25/25.
Both run on HAL, 2020-07-03 (earliest rectangle) to the latest data on
file, merged into one chronological sheet.
"""
import pandas as pd

from kitelab import backtest, frames

CAPITAL = 100_000.0

rect = pd.read_csv("rectangles.csv", parse_dates=["entry_date", "exit_date"])
print(f"rectangles.csv: {rect.shape[0]} rows, {rect.shape[1]} cols")
print(rect.head(3))
assert set(rect["symbol"].unique()) == {"NSE:HAL"}, "expected only NSE:HAL rows"

day = frames.daily("HAL")
print(f"\nHAL_day.parquet: {day.shape[0]} rows, {day.shape[1]} cols")
print(day.head(3))
required = {"ts", "open", "high", "low", "close"}
missing = required - set(day.columns)
if missing:
    raise SystemExit(f"HAL daily frame is missing required columns: {missing}")

day = day.set_index(day["ts"].dt.normalize())
close = day["close"]


def price_on_or_after(d: pd.Timestamp) -> tuple[pd.Timestamp, float]:
    idx = close.index.searchsorted(d)
    if idx >= len(close):
        raise SystemExit(f"no HAL price on/after {d.date()}")
    return close.index[idx], float(close.iloc[idx])


# ---------- System "Turtle 55/20": rectangle buy/sell ----------
rows_a = []
cum_profit_a = 0.0
for i, r in rect.sort_values("entry_date").reset_index(drop=True).iterrows():
    entry_dt, entry_px = price_on_or_after(r["entry_date"])
    exit_dt, exit_px = price_on_or_after(r["exit_date"])
    shares = int(CAPITAL // entry_px)
    cost = shares * entry_px
    profit = shares * (exit_px - entry_px)
    cum_profit_a += profit
    rows_a.append({
        "System": "Turtle 55/20", "Entry Date": entry_dt.date(), "Entry Price": round(entry_px, 2),
        "Exit Date": exit_dt.date(), "Exit Price": round(exit_px, 2), "Shares": shares,
        "Cost of Entry": round(cost, 2), "Profit": round(profit, 2),
        "Return %": round(100 * profit / cost, 2), "Days Held": (exit_dt - entry_dt).days,
        "Cum Profit (own system)": round(cum_profit_a, 2),
        "Exit Reason": "manual 20-day-low exit",
    })
table_a = pd.DataFrame(rows_a)

span_start = rect["entry_date"].min()  # 2020-07-03, fixed per instruction
data_end = close.index.max()
print(f"\ncomparison span: {span_start.date()} to {data_end.date()} (latest HAL bar on file)")

# ---------- Table B: every kitelab M/W/D trade from span_start to the latest data ----------
trades = backtest.simulate("HAL", band=0.02, stack="mwd", stop_on_close=False)
print(f"\nbacktest.simulate('HAL', band=0.02, stack='mwd', stop_on_close=False): "
      f"{len(trades)} trades total (all history)")
in_span = [t for t in trades if span_start <= pd.Timestamp(t["entry_date"])]
print(f"restricted to entry_date >= {span_start.date()}: {len(in_span)} trades")

# Re-sized onto the reference sheet's own convention (Capital 100,000, Risk 1%)
# instead of kitelab's 1cr/1%-risk paper book, so both systems sit on the same
# capital base and rupee figures are actually comparable.
RISK_PCT = 0.01
RISK_BUDGET = CAPITAL * RISK_PCT  # 1,000
rows_b = []
cum_profit_b = 0.0
skipped = 0
for t in sorted(in_span, key=lambda t: t["entry_date"]):
    entry_dt_b, exit_dt_b = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
    entry_px, exit_px, stop = t["quoted_entry"], t["quoted_exit"], t["stop"]
    risk_per_share = entry_px - stop
    if risk_per_share <= 0:
        skipped += 1
        continue
    shares = int(RISK_BUDGET // risk_per_share)
    cost = shares * entry_px
    profit = shares * (exit_px - entry_px)
    cum_profit_b += profit
    rows_b.append({
        "System": "EMA M/W/D", "Entry Date": entry_dt_b.date(), "Entry Price": round(entry_px, 2),
        "Exit Date": exit_dt_b.date(), "Exit Price": round(exit_px, 2), "Shares": shares,
        "Cost of Entry": round(cost, 2), "Profit": round(profit, 2),
        "Return %": round(100 * profit / cost, 2) if cost else 0.0,
        "Days Held": (exit_dt_b - entry_dt_b).days,
        "Cum Profit (own system)": round(cum_profit_b, 2), "Exit Reason": t["exit_reason"],
    })
if skipped:
    print(f"skipped {skipped} EMA trade(s) with non-positive risk (entry <= stop)")
table_b = pd.DataFrame(rows_b)

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 60)

trade_cols = ["Entry Date", "Entry Price", "Exit Date", "Exit Price", "Shares",
              "Cost of Entry", "Profit", "Return %", "Days Held",
              "Cum Profit (own system)", "Exit Reason"]
turtle_out = table_a[trade_cols].copy()
turtle_out.insert(0, "Trade #", range(1, len(turtle_out) + 1))
ema_out = table_b[trade_cols].copy()
ema_out.insert(0, "Trade #", range(1, len(ema_out) + 1))

print(f"\n=== Turtle 55/20 trades, HAL ({len(turtle_out)}) ===")
print(turtle_out.to_string(index=False))
print(f"\n=== EMA M/W/D trades, HAL ({len(ema_out)}) ===")
print(ema_out.to_string(index=False))


def metrics(df: pd.DataFrame) -> dict:
    wins = df[df["Profit"] > 0]
    losses = df[df["Profit"] <= 0]
    gross_win = wins["Profit"].sum()
    gross_loss = -losses["Profit"].sum()  # positive number
    return {
        "Trades": len(df),
        "Wins": len(wins),
        "Losses": len(losses),
        "Win rate": f"{100 * len(wins) / len(df):.1f}%",
        "Profit": round(df["Profit"].sum(), 2),
        "Average win": round(wins["Profit"].mean(), 2) if len(wins) else 0.0,
        "Average loss": round(losses["Profit"].mean(), 2) if len(losses) else 0.0,
        "Profit factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "Expectancy per trade": round(df["Profit"].mean(), 2),
        "Largest win": round(df["Profit"].max(), 2),
        "Largest loss": round(df["Profit"].min(), 2),
        "Average days held": round(df["Days Held"].mean(), 1),
        "Capital deployed": round(df["Cost of Entry"].mean(), 2),
    }


metric_rows = ["Trades", "Wins", "Losses", "Win rate", "Profit", "Average win",
               "Average loss", "Profit factor", "Expectancy per trade",
               "Largest win", "Largest loss", "Average days held", "Capital deployed"]
m_turtle, m_ema = metrics(turtle_out), metrics(ema_out)
comparison = pd.DataFrame(
    {"Metric": metric_rows,
     "Turtle 55/20": [m_turtle[k] for k in metric_rows],
     "EMA M/W/D": [m_ema[k] for k in metric_rows]})

print("\n=== COMPARISON ===")
print("Note: both systems now sit on the same capital base -- 100,000 capital. "
      "Turtle deploys it flat per trade (no stop); EMA sizes at 1% risk per trade "
      "off (entry - stop), same convention as the DI+ reference sheet's own header. "
      "Prices are quoted (pre-slippage) close prices, no charges deducted, for both.")
print(comparison.to_string(index=False))

# ---------- One sheet: Turtle table, EMA table, comparison, stacked ----------
out_path = "output/turtle_vs_ema_hal.xlsx"
sheet = "Turtle vs EMA HAL"
note = ("Both systems on 100,000 capital: Turtle deploys it flat per trade (no "
        "stop); EMA sizes at 1% risk per trade off (entry - stop), the reference "
        "sheet's own convention. Quoted prices, no charges, for both.")
with pd.ExcelWriter(out_path, engine="openpyxl") as xl:
    startrow = 0
    pd.DataFrame({f"Turtle 55/20 trades, HAL ({len(turtle_out)})": []}).to_excel(
        xl, sheet_name=sheet, index=False, startrow=startrow)
    startrow += 2
    turtle_out.to_excel(xl, sheet_name=sheet, index=False, startrow=startrow)
    startrow += len(turtle_out) + 3

    pd.DataFrame({f"EMA M/W/D trades, HAL ({len(ema_out)})": []}).to_excel(
        xl, sheet_name=sheet, index=False, startrow=startrow)
    startrow += 2
    ema_out.to_excel(xl, sheet_name=sheet, index=False, startrow=startrow)
    startrow += len(ema_out) + 3

    pd.DataFrame({"COMPARISON": []}).to_excel(
        xl, sheet_name=sheet, index=False, startrow=startrow)
    startrow += 2
    comparison.to_excel(xl, sheet_name=sheet, index=False, startrow=startrow)
    startrow += len(comparison) + 2
    pd.DataFrame({note: []}).to_excel(
        xl, sheet_name=sheet, index=False, startrow=startrow)

print(f"\nsaved: {out_path} (one sheet: '{sheet}')")

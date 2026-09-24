"""Trade-by-trade comparison of Holy Grail and `pull` on one symbol, into Excel.

Both rules buy a PULLBACK INSIDE AN UPTREND -- the same idea, with the trend
checked two different ways. Holy Grail asks ADX; `pull` asks a 200-day average.
Holy Grail was dropped from the board on 2026-09-11 and `pull` was added on
2026-09-17, so `pull` is the row that now occupies its slot. They have never
been compared trade by trade, which is what this writes out.

THE LAYOUT IS THE CLASSWORK ONE (2026-09-23), copied from
output/classwork/DI_vs_20EMA_MANAPPURAM.xlsx: a RULE line and a
Capital/Risk% line above each trade table, the ten class columns
(Trade # ... Cum Profit), trades newest first, and a Comparison sheet of
profit factor / expectancy / largest win / max drawdown. The first version of
this file used its own layout and the Rs1cr producer book; both are gone.

    Sizing here is the CLASS account: Rs1,00,000 capital, 1% risked per trade,
    shares = floor(min(1000 / (entry - stop), 100000 / entry)), no compounding.
    sizing.CAPITAL / RISK_PCT are rebound in main() for the life of the
    process only -- nothing under kitelab/ is edited, so no cache is touched.
    A signal that sizes to under one share is DROPPED by the producers, so the
    trade counts here can differ from a Rs1cr run; main() prints by how many.

READS   /data/clean/kitelab/<SYMBOL>_day.parquet   (via kitelab.frames.daily)
WRITES  output/measurements/hg_vs_pull_<SYMBOL>.xlsx

Nothing under /data/raw is opened and nothing is written outside output/, so
this is safe to run while a backfill is in flight. It lives in scripts/ and
imports from kitelab/ without touching it, so it costs no cache rebuild.

Usage:  python3 -m scripts.compare_hg_pull [SYMBOL]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import backtest, entries, frames, holygrail, sizing, slippage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
MEAS = OUT / "measurements"

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "RELIANCE"

# The stop the user chose, and the one the board row traded. holygrail's
# "signal_low" and entries' stop_mult=None are the SAME rule -- the signal
# candle's own low -- which is what makes the two sheets comparable at all.
HG_STOP = "signal_low"

# The class account, from DI_vs_20EMA_MANAPPURAM.xlsx row 2. The board's own
# producers run at Rs1cr (sizing.CAPITAL) so no gridded account is filtered
# out; that book describes nobody, which is why the rupee columns on the first
# version of this file were unreadable as a P&L.
CLASS_CAPITAL = 100_000.0
CLASS_RISK_PCT = 0.01

REQUIRED = ["ts", "open", "high", "low", "close", "volume"]

# The ten class columns, then the additions. Columns K onward are NOT in the
# class layout; they are flagged as additions in each sheet's second line.
CLASS_COLUMNS = [
    ("n", "Trade #", "int"),
    ("entry_date", "Date", "date"),
    ("entry_price", "Entry Price", "price"),
    ("stop", "Stop Loss", "price"),
    ("exit_price", "Exit", "price"),
    ("shares", "No. of Shares", "int"),
    ("cost_of_entry", "Cost of Entry", "rs"),
    ("cum_cost", "Cum Cost", "rs"),
    ("gross_profit", "Profit", "rs"),
    ("cum_profit", "Cum Profit", "rs"),
]
EXTRA_COLUMNS = [
    ("per_share", "Per share", "price"),
    ("target", "Target", "price"),
    ("exit_reason", "Why it ended", "text"),
    ("days_held", "Days held", "int"),
    ("charges", "Charges", "rs"),
    ("net_profit", "Net Profit", "rs"),
    ("r_multiple", "R multiple", "r"),
]
COLUMNS = CLASS_COLUMNS + EXTRA_COLUMNS

RULE_TEXT = {
    "Holy Grail":
        "RULE : 1. ADX(14) at or above 30 and rising, with +DI above -DI, says "
        "the trend is real. 2. wait for price to pull back to the 20-day EMA. "
        "3. place a buy-stop at that signal candle's HIGH; it fills on a later "
        "bar within 10 sessions, gap-adjusted to the open, and the setup is "
        "VOID if the signal candle's low is breached before the fill. 4. stop "
        "loss = the signal candle's own low. 5. bank half the position at the "
        "previous confirmed swing high, then trail the rest on confirmed swing "
        "lows. Linda Raschke's setup as taught in class, run from scratch on "
        "{SYMBOL} (NSE) over its full daily history; the stop is judged on the "
        "CLOSE, not intrabar.",
    "pull - own stop":
        "RULE : 1. buy at the CLOSE when the close is above its 200-day simple "
        "average (the trend is up) AND below its 20-day simple average (it has "
        "pulled back). Rising edge only -- one entry per fresh signal, not one "
        "a day while the condition holds. 2. stop loss = the signal candle's "
        "own low. 3. no target: sell when a close lands at or below the stop, "
        "or after {MAXHOLD} sessions, whichever comes first. This is the board "
        "row that took Holy Grail's slot on 2026-09-17.",
    "pull - 3xATR stop":
        "RULE : entries exactly as the `pull - own stop` sheet. 2. stop loss = "
        "the entry close minus 3 x ATR(14) -- the wide arm of the board's stop "
        "axis, not a different entry idea. 3. no target: sell when a close "
        "lands at or below the stop, or after {MAXHOLD} sessions. The stop is "
        "also a SIZING input, so the same signal buys FEWER shares here.",
}


def require_columns(frame: pd.DataFrame, name: str) -> None:
    """Stop with a clear error rather than producing a half-right sheet."""
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise SystemExit(f"{name}: required column(s) missing: {missing}")
    for col in REQUIRED:
        n_null = int(frame[col].isna().sum())
        if n_null:
            raise SystemExit(f"{name}: column {col!r} has {n_null} missing values")


def to_frame(trades: list[dict]) -> pd.DataFrame:
    """One row per closed trade, NEWEST FIRST, in the class column order.

    Newest first is the class sheet's own ordering, so Cum Cost and Cum Profit
    accumulate BACKWARDS down the page and the last row carries the total. The
    drawdown on the Comparison sheet is computed chronologically instead and
    says so in its label.
    """
    labels = [label for _, label, _ in COLUMNS]
    if not trades:
        return pd.DataFrame(columns=labels)

    ordered = sorted(trades, key=lambda t: t["entry_date"], reverse=True)
    rows = []
    for n, t in enumerate(ordered, start=1):
        t = dict(t)
        t["n"] = n
        t["per_share"] = float(t["exit_price"]) - float(t["entry_price"])
        t["days_held"] = (pd.Timestamp(t["exit_date"])
                          - pd.Timestamp(t["entry_date"])).days
        rows.append({label: t.get(key) for key, label, _ in COLUMNS})
    out = pd.DataFrame(rows)
    out["Cum Cost"] = out["Cost of Entry"].cumsum()
    out["Cum Profit"] = out["Profit"].cumsum()
    for key, label, kind in COLUMNS:
        if kind == "date":
            out[label] = pd.to_datetime(out[label])
        elif kind != "text":
            out[label] = pd.to_numeric(out[label], errors="coerce")
    return out[labels]


def max_drawdown(trades: list[dict]) -> float:
    """Deepest fall in cumulative gross profit, oldest trade to newest."""
    if not trades:
        return float("nan")
    order = sorted(trades, key=lambda t: t["entry_date"])
    cum = np.cumsum([float(t["gross_profit"]) for t in order])
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float(np.min(cum - peak))


def summarise(name: str, trades: list[dict]) -> dict:
    """The class Comparison metrics, then the four this session needed."""
    if not trades:
        return {"Rule": name, "Trades": 0}
    profit = np.array([float(t["gross_profit"]) for t in trades])
    charge = np.array([float(t["charges"]) for t in trades])
    cost = np.array([float(t["cost_of_entry"]) for t in trades])
    risked = np.array([float(t["risk_taken"]) for t in trades])
    r = np.array([float(t["r_multiple"]) for t in trades])
    capped = np.array([bool(t["capital_capped"]) for t in trades])
    days = np.array([(pd.Timestamp(t["exit_date"])
                      - pd.Timestamp(t["entry_date"])).days for t in trades],
                    dtype=float)
    dist = np.array([100.0 * (t["entry_price"] - t["stop"]) / t["entry_price"]
                     for t in trades])
    wins, losses = profit[profit > 0], profit[profit <= 0]
    return {
        "Rule": name,
        "Trades": len(trades),
        "Wins": int((profit > 0).sum()),
        "Losses": int((profit <= 0).sum()),
        "Win rate": float((profit > 0).mean()),
        "First entry": min(t["entry_date"] for t in trades),
        "Last exit": max(t["exit_date"] for t in trades),
        "Total gross profit": float(profit.sum()),
        "Average win": float(wins.mean()) if wins.size else np.nan,
        "Average loss": float(losses.mean()) if losses.size else np.nan,
        "Profit factor (gross wins / |gross losses|)":
            float(wins.sum() / abs(losses.sum())) if losses.sum() else np.nan,
        "Expectancy per trade (gross)": float(profit.mean()),
        "Largest win": float(profit.max()),
        "Largest loss": float(profit.min()),
        "Worst R": float(r.min()),
        "Gross profit excluding the single largest win":
            float(profit.sum() - profit.max()),
        "Max drawdown on Cum Profit (chronological)": max_drawdown(trades),
        "Average days held (calendar)": float(days.mean()),
        "Longest hold (calendar days)": float(days.max()),
        "Total capital deployed (sum of Cost of Entry)": float(cost.sum()),
        "Charges (Zerodha equity delivery, round trip)": float(charge.sum()),
        "Net profit after charges": float(profit.sum() - charge.sum()),
        "Charges as % of gross": (float(charge.sum() / abs(profit.sum()) * 100)
                                  if profit.sum() else np.nan),
        # --- the four that decide whether the R column may be read at all ---
        "Median stop distance %": float(np.median(dist)),
        "Trades that lost worse than -1R": int((r < -1.05).sum()),
        "Sized by capital cap, not risk": float(capped.mean()),
        "Median rupees actually risked": float(np.median(risked)),
        "Average R": float(r.mean()),
        "Median R": float(np.median(r)),
    }


def reason_counts(name: str, trades: list[dict]) -> pd.Series:
    return pd.Series([t["exit_reason"] for t in trades], dtype=object).value_counts()


def main() -> None:
    print(f"=== {SYMBOL}: Holy Grail vs pull ===\n")

    day = frames.daily(SYMBOL).reset_index(drop=True)
    print(f"loaded {SYMBOL}: {day.shape[0]} rows x {day.shape[1]} columns")
    require_columns(day, SYMBOL)
    print("first 3 rows:")
    print(day.head(3).to_string(index=False))
    print(f"date range: {str(day['ts'].min())[:10]} .. {str(day['ts'].max())[:10]}")
    print(f"backtest.NEXT_OPEN_FILLS = {backtest.NEXT_OPEN_FILLS}\n")

    # ---- how many days each rule even fires on, before any trade is taken ----
    pull_fires = entries.signal(SYMBOL, "pull", day)
    fresh = pull_fires & ~np.concatenate([[False], pull_fires[:-1]])
    print(f"pull: {len(day)} sessions -> {int(pull_fires.sum())} sessions where the "
          f"condition holds -> {int(fresh.sum())} fresh (rising-edge) signals")

    setups = holygrail.setups(frames.daily(SYMBOL))
    hg_sig = setups["signal"].to_numpy(bool)
    print(f"Holy Grail: {len(setups)} sessions -> {int(hg_sig.sum())} signal candles "
          f"(a buy-stop is then placed and may never fill)\n")

    # ---- the trades, at BOTH account sizes, so the drop is visible -----------
    print(f"producer book: Rs{sizing.CAPITAL:,.0f} at {sizing.RISK_PCT:.0%} "
          f"= Rs{sizing.risk_budget():,.0f} per trade")
    board = {name: len(trades) for name, trades in run_all()}

    sizing.CAPITAL, sizing.RISK_PCT = CLASS_CAPITAL, CLASS_RISK_PCT
    print(f"class book:    Rs{sizing.CAPITAL:,.0f} at {sizing.RISK_PCT:.0%} "
          f"= Rs{sizing.risk_budget():,.0f} per trade  <- the sheets use this\n")
    blocks = run_all()

    for name, trades in blocks:
        n_before = board[name]
        note = "" if len(trades) == n_before else \
            f"   ({n_before - len(trades)} dropped: under one share on a Rs1 lakh book)"
        print(f"{name:20s} {len(trades):4d} closed trades{note}")
    print()

    _TRADE_STAMPS.extend((t["entry_date"], float(t["entry_price"]))
                         for _, trades in blocks for t in trades)
    assert not slippage.ENABLED, "slippage must stay OFF: these lists are gross of spread"

    summary = pd.DataFrame([summarise(n, t) for n, t in blocks])
    print(summary.T.to_string(header=False))
    print()
    for name, trades in blocks:
        if trades:
            print(f"{name}: exit reasons")
            print(reason_counts(name, trades).to_string())
            print()

    MEAS.mkdir(parents=True, exist_ok=True)
    path = MEAS / f"hg_vs_pull_{SYMBOL}.xlsx"
    write_workbook(path, summary, blocks)
    print(f"wrote {path}  ({path.stat().st_size:,} bytes)")


def run_all() -> list[tuple[str, list[dict]]]:
    """The three trade lists, at whatever sizing.CAPITAL currently is."""
    return [
        ("Holy Grail", holygrail.simulate(SYMBOL, stop=HG_STOP)),
        ("pull - own stop", entries.simulate(SYMBOL, "pull", None)),
        ("pull - 3xATR stop", entries.simulate(SYMBOL, "pull", 3.0)),
    ]


# ------------------------------------------------------------------ Excel ----

READ_ME = [
    ("What this file is", ""),
    ("", "Every trade two rules would have taken on {SYMBOL}, side by side, in "
         "the layout of the class comparison sheets: one sheet per rule, a "
         "RULE line and a Capital/Risk line above each table, trades newest "
         "first, and a Comparison sheet."),
    ("", ""),
    ("The two rules", ""),
    ("Holy Grail",
     "Linda Raschke's setup, as taught in your class. Wait for ADX to say a "
     "trend is real, wait for price to pull back to its 20-day exponential "
     "average, then buy the bounce."),
    ("pull",
     "The same idea with a simpler trend test: the close is above its 200-day "
     "average (so the trend is up) and below its 20-day average (so it has "
     "pulled back). Buy that. Two sheets, because the stop width is a separate "
     "choice: the signal candle's own low, or 3 x ATR."),
    ("Why compare these two",
     "Holy Grail was dropped from the board on 2026-09-11 and pull was added "
     "on 2026-09-17. pull is the row that now sits in Holy Grail's slot. They "
     "had never been compared trade by trade."),
    ("", ""),
    ("WHERE THIS DIFFERS FROM DI_vs_20EMA_MANAPPURAM.xlsx", ""),
    ("Charges are the real schedule, not the approximation",
     "That sheet used 0.001187406 x buy + 0.001037406 x sell + 15.34. Here "
     "every trade is charged through kitelab.backtest.charges(): brokerage, "
     "STT, stamp duty, exchange transaction, SEBI turnover, GST and the DP "
     "charge on each sell, on the Zerodha equity delivery schedule."),
    ("Charges are on the trade sheets, not only the Comparison",
     "That sheet kept its trade tables gross to match a class reference it was "
     "reproducing by hand. There is no reference to match here, so Charges and "
     "Net Profit sit beside each trade. The Profit column is still GROSS, as "
     "in the class sheets."),
    ("Six columns were added after Cum Profit",
     "Per share, Target, Why it ended, Days held, Charges, Net Profit, R "
     "multiple. Everything from Trade # to Cum Profit is the class layout "
     "unchanged."),
    ("", ""),
    ("READ THIS BEFORE COMPARING THE TOTALS", ""),
    ("They differ in the EXIT, not only the entry",
     "Holy Grail sells half the position at the previous swing high and trails "
     "the rest upward on later swing lows. pull has no target at all: it holds "
     "until the stop is hit or {MAXHOLD} sessions pass, whichever comes first. "
     "This project has separately measured that the exit does about 3.5 times "
     "as much work as the entry, so most of any gap below is the exit "
     "machinery and not the entry idea."),
    ("The R column is NOT comparable between these two rules",
     "Both rules say 'the stop is the signal candle's low', but they buy at "
     "different points in that candle: Holy Grail at its HIGH, pull at its "
     "CLOSE. Close-to-low is a much shorter drop than high-to-low, so pull's "
     "stop sits a median {PULL_DIST}% below entry against Holy Grail's "
     "{HG_DIST}%. Two consequences, both on the Comparison sheet. First, a "
     "stop that close is jumped straight over: {PULL_BAD} of pull's {PULL_N} "
     "trades lost MORE than the -1R the stop was supposed to cap them at, the "
     "worst at {PULL_WORST}R, because the stop is judged on the closing price "
     "and the close lands well past it. Second, risking {RISKPCT} of the "
     "account against a stop that tight asks for a position larger than the "
     "whole account, so {PULL_CAPPED} of pull's trades are sized by the "
     "capital cap instead and risk a median Rs{PULL_RISKED} rather than the "
     "nominal Rs{RISKBUDGET}. Compare these two rules on the rupee columns "
     "and on the stop distance, not on R."),
    ("They also differ in the entry TRIGGER",
     "Holy Grail places a resting buy order at the signal candle's high and "
     "only trades if a later bar (within {WAIT} sessions) trades through it -- "
     "many signals never become trades. pull buys at the signal bar's close, "
     "so every fresh signal becomes a trade."),
    ("What is the SAME",
     "The account (Rs{CAPITAL} at {RISKPCT} per trade, not compounding), the "
     "sizing formula, the charges, the price data, and the rule that a stop is "
     "judged on the closing price rather than intrabar."),
    ("", ""),
    ("Terms used in the columns", ""),
    ("Stop Loss",
     "The price at which you give up on the trade. On two of the three sheets "
     "it is the low of the candle that produced the signal; on the third it is "
     "the entry close minus three times the Average True Range, a measure of "
     "how far the stock moves in a typical day."),
    ("Target",
     "A price at which you take money off the table. Only Holy Grail has one; "
     "pull's Target column is blank because the rule has none."),
    ("Cost of Entry",
     "Entry price times shares -- what the position was worth on the day you "
     "bought it. Cum Cost adds these DOWN the sheet, and the sheet runs newest "
     "first, so the bottom row carries the total. That is the class sheet's "
     "own convention."),
    ("Profit",
     "GROSS: exit price minus entry price, times shares, before any charge. "
     "Net Profit is the same figure after charges."),
    ("R multiple",
     "Profit measured in units of what you risked. Risking Rs1,000 and making "
     "Rs2,000 is +2R; being stopped out for the full amount is -1R. Read the "
     "warning above before comparing it across sheets."),
    ("Profit factor",
     "Gross winnings divided by gross losings. Above 1 means the winners paid "
     "for the losers before charges."),
    ("Expectancy per trade",
     "Average gross profit per trade -- total gross divided by the number of "
     "trades."),
    ("Max drawdown on Cum Profit",
     "The deepest fall from a running high in cumulative profit, computed "
     "OLDEST trade to newest, which is the opposite order to the sheets."),
    ("", ""),
    ("Two things NOT in these numbers", ""),
    ("The half-spread",
     "The board's headline figures also charge the bid-ask spread -- the gap "
     "between what a buyer pays and a seller gets, which you cross on the way "
     "in and again on the way out. It is not a fixed number: kitelab.slippage "
     "scales it to how much the stock trades, and for {SYMBOL} on these trade "
     "dates it works out at a median {HALFSPREAD} basis points each side, so "
     "{ROUNDSPREAD} bp on a round trip. These trade lists do NOT carry it, "
     "only the statutory charges, so every Net Profit here is that much "
     "kinder than the board's. A basis point is one hundredth of one percent."),
    ("A portfolio",
     "Each rule is sized as if it were the only trade in the account, off the "
     "same Rs{CAPITAL} base every time. Real accounts run out of money and "
     "cannot take every signal. The board's account layer has reversed "
     "trade-level findings before, so do not read a Net profit here as what "
     "an account would have earned."),
    ("Neither is a recommendation",
     "This is a description of what these rules did on past data. It is not a "
     "forecast and not advice to trade either of them."),
]


_TRADE_STAMPS: list = []


def _median_half_spread() -> float:
    """The symbol's half-spread in basis points, median over the trade dates.

    Read from kitelab.slippage WITHOUT enabling it: half_spread() is a pure
    lookup on the liquidity profile and changes no module state. The trade
    lists themselves stay spread-free -- this figure exists only so the Read me
    can say how big the missing cost is.
    """
    if not _TRADE_STAMPS:
        return float("nan")
    bps = [1e4 * slippage.half_spread(SYMBOL, stamp, price)
           for stamp, price in _TRADE_STAMPS]
    return float(np.median(bps))


def substitutions(summary: pd.DataFrame) -> dict:
    """Every figure in the prose, taken from the run that just happened.

    The prose is written once and the symbol is an argument, so a typed number
    here would be a lie on the second symbol.
    """
    by_rule = summary.set_index("Rule")
    hg, pull = by_rule.loc["Holy Grail"], by_rule.loc["pull - own stop"]
    return {
        "{SYMBOL}": SYMBOL,
        "{CAPITAL}": f"{CLASS_CAPITAL:,.0f}",
        "{RISKPCT}": f"{CLASS_RISK_PCT:.0%}",
        "{RISKBUDGET}": f"{CLASS_CAPITAL * CLASS_RISK_PCT:,.0f}",
        "{MAXHOLD}": str(entries.MAXHOLD),
        "{WAIT}": str(holygrail.TRIGGER_WINDOW),
        "{HG_DIST}": f"{hg['Median stop distance %']:.2f}",
        "{PULL_DIST}": f"{pull['Median stop distance %']:.2f}",
        "{PULL_BAD}": f"{int(pull['Trades that lost worse than -1R'])}",
        "{PULL_N}": f"{int(pull['Trades'])}",
        "{PULL_WORST}": f"{pull['Worst R']:.1f}",
        "{PULL_CAPPED}": f"{pull['Sized by capital cap, not risk']:.0%}",
        "{PULL_RISKED}": f"{pull['Median rupees actually risked']:,.0f}",
        "{HALFSPREAD}": f"{_median_half_spread():.1f}",
        "{ROUNDSPREAD}": f"{2 * _median_half_spread():.1f}",
    }


def fill(text: str, subs: dict, where: str) -> str:
    for k, v in subs.items():
        text = text.replace(k, v)
    if "{" in text:
        raise SystemExit(f"{where}: unfilled placeholder: {text}")
    return text


# Comparison rows whose format is not the default two-decimal rupee.
CMP_DATES = {"First entry", "Last exit"}
CMP_PERCENT = {"Win rate", "Sized by capital cap, not risk"}
CMP_COUNTS = {"Trades", "Wins", "Losses", "Trades that lost worse than -1R"}
CMP_PLAIN = {"Profit factor (gross wins / |gross losses|)",
             "Average days held (calendar)", "Longest hold (calendar days)",
             "Median stop distance %", "Charges as % of gross",
             "Average R", "Median R"}


def write_workbook(path: Path, summary: pd.DataFrame,
                   blocks: list[tuple[str, list[dict]]]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    subs = substitutions(summary)
    money, price = '#,##0.00', '0.00'
    bold, head_fill = Font(bold=True), PatternFill("solid", fgColor="EDEDED")
    top = Alignment(vertical="top", wrap_text=True)

    book = Workbook()
    book.remove(book.active)

    # --- Read me ---------------------------------------------------------
    ws = book.create_sheet("Read me")
    for head, body in READ_ME:
        ws.append([head, fill(body, subs, f"Read me / {head!r}")])
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 100
    for row in ws.iter_rows():
        row[0].font, row[0].alignment, row[1].alignment = bold, top, top

    # --- Comparison ------------------------------------------------------
    ws = book.create_sheet("Comparison")
    names = list(summary["Rule"])
    ws.append([fill(f"{SYMBOL} (NSE) -- Holy Grail vs pull, every trade in each "
                    f"rule's full daily history", subs, "Comparison title")])
    ws.append([f"Sizing for all three: shares = floor(min("
               f"{CLASS_CAPITAL * CLASS_RISK_PCT:,.0f} / (entry - stop), "
               f"{CLASS_CAPITAL:,.0f} / entry)); no compounding. Profit is GROSS; "
               f"charges are kitelab.backtest.charges() -- the Zerodha equity "
               f"delivery schedule, not the flat approximation the class sheets "
               f"used."])
    ws.append([])
    ws.append(["Metric"] + names)
    for label in summary.columns:
        if label == "Rule":
            continue
        ws.append([label] + [summary.loc[summary["Rule"] == n, label].iloc[0]
                             for n in names])
    ws.append([])
    ws.append(["Why the trades ended"])
    reasons = pd.DataFrame({n: reason_counts(n, t) for n, t in blocks}).fillna(0)
    ws.append([""] + names)
    for why, row in reasons.astype(int).iterrows():
        ws.append([why] + [int(row[n]) for n in names])

    ws.column_dimensions["A"].width = 52
    for i in range(len(names)):
        ws.column_dimensions[get_column_letter(2 + i)].width = 20
    ws["A1"].font = bold
    for cell in ws[4]:
        cell.font, cell.fill, cell.alignment = bold, head_fill, top
    ws["A1"].alignment = ws["A2"].alignment = top
    for row in ws.iter_rows(min_row=5):
        label = str(row[0].value or "")
        for cell in row[1:]:
            if label in CMP_DATES:
                cell.number_format = 'yyyy-mm-dd'
            elif not isinstance(cell.value, (int, float)):
                continue
            elif label in CMP_PERCENT:
                cell.number_format = '0.0%'
            elif label in CMP_COUNTS:
                cell.number_format = '#,##0'
            elif label in CMP_PLAIN:
                cell.number_format = price
            else:
                cell.number_format = money

    # --- one sheet per rule, in the class layout --------------------------
    widths = {"Trade #": 8, "Date": 12, "Why it ended": 18, "Days held": 10,
              "No. of Shares": 13}
    for name, trades in blocks:
        ws = book.create_sheet(name[:31])
        ws.append([fill(RULE_TEXT[name], subs, f"{name} RULE")])
        ws.append(["Capital", CLASS_CAPITAL, "Risk %", f"{CLASS_RISK_PCT:.0%}"])
        ws.append(["Profit is GROSS. Columns K onward (Per share ... R multiple) "
                   "are additions to the class layout; Trade # to Cum Profit is "
                   "that layout unchanged. Trades run NEWEST FIRST, so Cum Cost "
                   "and Cum Profit total on the bottom row."])
        ws.append([])
        frame = to_frame(trades)
        ws.append(list(frame.columns))
        for record in frame.itertuples(index=False):
            ws.append([None if pd.isna(v) else v for v in record])

        ws["A1"].alignment = ws["A3"].alignment = top
        ws["A1"].font = bold
        ws["A2"].font = bold
        header_row = 5
        for cell in ws[header_row]:
            cell.font, cell.fill, cell.alignment = bold, head_fill, top
        ws.freeze_panes = f"A{header_row + 1}"
        kinds = {label: kind for _, label, kind in COLUMNS}
        for i, header in enumerate(frame.columns, start=1):
            letter = get_column_letter(i)
            ws.column_dimensions[letter].width = widths.get(header, 14)
            kind = kinds[header]
            for cell in ws[letter][header_row:]:
                if kind == "date":
                    cell.number_format = 'yyyy-mm-dd'
                elif kind == "rs":
                    cell.number_format = money
                elif kind in ("price", "r"):
                    cell.number_format = price
                elif kind == "int":
                    cell.number_format = '#,##0'
        if len(frame):
            last = get_column_letter(len(frame.columns))
            ws.auto_filter.ref = f"A{header_row}:{last}{header_row + len(frame)}"

    book.save(path)


if __name__ == "__main__":
    main()

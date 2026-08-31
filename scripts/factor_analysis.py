"""What actually drives the profit? Every lever, ranked by how much it moves the result.

    python -m scripts.factor_analysis

One reference account -- EMA M/W/D, 2% band, the whole universe, 1% risk,
Rs2,50,000 -- and then each factor is varied ON ITS OWN across its plausible
range while everything else is held still. The swing in CAGR is that factor's
influence. This is a tornado analysis, not a regression: the levers interact,
so the ranking says "what would change my outcome most", not "what causes
profit" in a causal sense.

Writes output/Factor Analysis.xlsx and prints the ranking.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from kitelab import backtest, config, portfolio, report, slippage

CACHE = Path(__file__).resolve().parent.parent / "data" / "signal_cache"
DASH = Path(__file__).resolve().parent.parent / "data" / "dashboard.json"
CAPITAL = 250_000.0
RISK = 0.01


def load_cache(name):
    return pickle.loads((CACHE / f"{name}.pkl").read_bytes())


def cagr(trades, capital=CAPITAL, risk=RISK):
    """CAGR of the one-account run. Raises if the account was wiped out.

    Every lever in this tornado is measured as a SWING in CAGR, so a configuration
    with no CAGR at all cannot be ranked against the others. It used to arrive here
    as 0.0 and quietly take part in the ranking as if it were break-even.
    """
    r = portfolio.run(trades, capital, risk)
    if r["cagr_pct"] is None:
        raise SystemExit(
            f"  account WIPED OUT at capital {capital:,.0f} / risk {risk:.2%} "
            f"(final Rs{r['final']:,.2f}) -- it has no CAGR, so this lever cannot "
            "be ranked by CAGR swing")
    return r["cagr_pct"]


def main() -> None:
    cfg = config.load()
    d = json.loads(DASH.read_text())
    # The grid and basket keys gained a fills dimension on 2026-08-31. This study
    # compares LEVERS against one another, so it holds fills at perfect ("0") and
    # prices execution as one of the levers below. Stripping the suffix here keeps
    # every lookup underneath in the original shape.
    grid = {k.rsplit("|", 1)[0]: v for k, v in d["grid"].items() if k.endswith("|0")}
    d["basket10"] = {k.rsplit("|", 1)[0]: v for k, v in d["basket10"].items()
                     if k.endswith("|0")}
    ref_key = "ema|0.02|all|1|250000"
    ref = grid[ref_key]["cagr"]
    print(f"\n  reference account: EMA M/W/D, 2% band, all {len(cfg.all_symbols)}, "
          f"1% risk, Rs2,50,000 "
          f"-> CAGR {ref:.1f}%\n")

    factors = []      # (name, low_label, low, high_label, high, note)

    # ---- everything already in the precomputed grid ----------------------
    def sweep(name, keys_labels, note):
        vals = [(lab, grid[k]["cagr"]) for lab, k in keys_labels if k in grid]
        vals.sort(key=lambda x: x[1])
        factors.append((name, vals[0][0], vals[0][1], vals[-1][0], vals[-1][1], note, vals))

    sweep("Strategy / timeframe stack",
          [("EMA W/D/H (hourly)", "wdh|0.02|all|1|250000"),
           ("EMA M/W/D (daily)", "ema|0.02|all|1|250000"),
           ("EMA Q/M/W (weekly)", "qmw|0.02|all|1|250000"),
           ("ATH Breakout", "brk|0.02|all|1|250000"),
           ("Darvas 20/10", "dv|20-10|all|1|250000")],
          "which rule you trade at all")
    sweep("Band (dead zone) 0-5%",
          [(f"{b*100:g}% band", f"ema|{b:g}|all|1|250000") for b in d["bands"]],
          "how far past the EMA price must close")
    sweep("Risk per trade 0.25-2%",
          [(f"{r}% risk", f"ema|0.02|all|{r:g}|250000") for r in d["risks"]],
          "position size as a share of equity")
    sweep("Universe / breadth",
          [(d["universes"][u], f"ema|0.02|{u}|1|250000") for u in d["universes"]],
          "how many stocks you follow")
    sweep("Starting capital",
          [(f"Rs{c:,}", f"ema|0.02|all|1|{c}") for c in d["capitals"]],
          "how much money you begin with")

    # ---- which ten stocks you happen to pick ------------------------------
    b = d["basket10"]["ema|0.02|1"]
    factors.append(("WHICH 10 stocks you pick", "worst of 75 draws", b["worst"],
                    "best of 75 draws", b["best"],
                    "pure luck of the draw (measured at Rs1,00,000)",
                    [("worst", b["worst"]), ("p10", b["p10"]), ("median", b["median"]),
                     ("p90", b["p90"]), ("best", b["best"])]))

    # ---- levers that need their own runs ----------------------------------
    ema = load_cache("EMA_all")
    print("  running the extra scenarios:", flush=True)

    real_charges = portfolio.charges
    portfolio.charges = lambda *a, **k: 0.0
    free = cagr(ema)
    portfolio.charges = real_charges
    factors.append(("Brokerage charges", "charged (real)", ref, "zero fees", free,
                    "Zerodha delivery costs", [("real fees", ref), ("no fees", free)]))
    print(f"    charges: {ref:.1f}% -> {free:.1f}% without fees", flush=True)

    # Measured now rather than guessed: the per-stock spread ladder plus market
    # impact, under the participation cap that makes the orders fillable. The old
    # flat percentage haircut is gone -- see kitelab/slippage.py.
    slippage.ENABLED, slippage.MAX_PARTICIPATION = True, 0.01
    slippage.reset()
    realistic = cagr([slippage.apply_spread(t) for t in ema])
    slippage.ENABLED, slippage.MAX_PARTICIPATION = False, None
    slippage.reset()
    slip = sorted([("realistic fills", realistic), ("perfect fills", ref)],
                  key=lambda x: x[1])
    factors.append(("Execution: spread + market impact", slip[0][0], slip[0][1],
                    slip[-1][0], slip[-1][1],
                    "the gap between the price you see and the price you get, "
                    "measured per stock", slip))
    print(f"    execution: {ref:.1f}% -> {realistic:.1f}% with real fills", flush=True)

    # NO scale-out row. portfolio.run prices a trade as shares x ONE exit price and
    # a scale-out has two; the banked leg never reaches the trade record, so an
    # account figure for it is not merely noisy, it is wrong -- 1,522 of 1,523
    # banked trades disagree with the engine's formula. The honest trade-level
    # sweep is scripts/scaleout_r_test.py and the dashboard's Scale-out page.
    print("    scale-out: deliberately excluded, see the comment", flush=True)

    intrabar = []
    for s in cfg.all_symbols:
        try:
            intrabar.extend(backtest.simulate(s, stop_on_close=False))
        except SystemExit:
            pass
    ib = cagr(intrabar)
    factors.append(("Stop convention", "broker stop (intrabar)", ib,
                    "class rule (checked at closes)", ref,
                    "whether a wick can stop you out", [("intrabar", ib), ("close-only", ref)]))
    print(f"    convention: intrabar {ib:.1f}% vs close-only {ref:.1f}%", flush=True)

    # The ONE row in this ranking that is not recomputed each run, and cannot be:
    # it measures the effect of a data repair that has already happened, so the
    # "before" state no longer exists in data/. Kept because the lever is real and
    # belongs in the comparison, labelled so it never reads as a live measurement.
    DQ_BEFORE, DQ_AFTER, DQ_MEASURED = 5.3, 11.9, "2026-08-26"
    factors.append((f"Data quality (corrupt bars, measured {DQ_MEASURED}, frozen)",
                    "before repair", DQ_BEFORE, "after repair", DQ_AFTER,
                    "zero-price bars in Kite's 2015-18 intraday history; measured on "
                    f"Breakout, same settings. HISTORICAL, {DQ_MEASURED}: the repair has "
                    "since been applied, so the 'before' number cannot be reproduced from "
                    "today's data. Every other row in this table is recomputed each run",
                    [("before", DQ_BEFORE), ("after", DQ_AFTER)]))

    breadth_rows = d.get("breadth", {}).get("ema", [])
    if breadth_rows:
        pts = sorted(((f"{r['size']} stocks", r["median"]) for r in breadth_rows),
                     key=lambda x: x[1])
        factors.append((f"Watchlist size (5 to {len(cfg.all_symbols)} stocks)",
                        pts[0][0], pts[0][1],
                        pts[-1][0], pts[-1][1],
                        "how many stocks you follow at all", pts))

    factors.sort(key=lambda f: abs(f[4] - f[2]), reverse=True)

    print(f"\n  {'factor':<38} {'low':>8} {'high':>8} {'swing':>8}")
    print("  " + "-" * 66)
    for name, lo_l, lo, hi_l, hi, note, _ in factors:
        print(f"  {name:<38} {lo:>7.1f}% {hi:>7.1f}% {hi-lo:>7.1f}")

    # ---- workbook --------------------------------------------------------
    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("Ranking")
    sheet["A1"] = (f"What moves the profit, most to least. Reference account: EMA M/W/D, 2% band, "
                   f"all {len(cfg.all_symbols)} stocks, 1% risk, Rs2,50,000 = {ref:.1f}% CAGR. "
                   f"Each factor is varied "
                   f"ALONE across its plausible range; the swing is how many CAGR points that "
                   f"single choice is worth. Levers interact, so read this as 'what would change "
                   f"my outcome most', not as a causal model.")
    cols = ["Rank", "Factor", "Worst setting", "Worst CAGR %", "Best setting", "Best CAGR %",
            "Swing (points)", "What it means"]
    for c, (name, w) in enumerate(zip(cols, [6, 34, 24, 12, 26, 12, 13, 52]), start=1):
        sheet.cell(3, c, name).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(c)].width = w
    for i, (name, lo_l, lo, hi_l, hi, note, _) in enumerate(factors, start=1):
        row = 3 + i
        for c, v in enumerate([i, name, lo_l, round(lo, 1), hi_l, round(hi, 1),
                               round(hi - lo, 1), note], start=1):
            cell = sheet.cell(row, c, v)
            if c in (4, 6, 7):
                cell.number_format = "0.0"
        sheet.cell(row, 7).font = Font(bold=True)

    detail = book.create_sheet("Every setting")
    detail["A1"] = "Every value behind the ranking, factor by factor."
    r = 3
    for name, *_rest, values in factors:
        cell = detail.cell(r, 1, name)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="BDD7EE")
        r += 1
        detail.cell(r, 1, "setting").font = Font(bold=True)
        detail.cell(r, 2, "CAGR %").font = Font(bold=True)
        r += 1
        for label, value in values:
            detail.cell(r, 1, label)
            detail.cell(r, 2, round(value, 2)).number_format = "0.00"
            r += 1
        r += 1
    detail.column_dimensions["A"].width = 34
    detail.column_dimensions["B"].width = 10

    target = report.save(book, "Factor Analysis.xlsx")
    print(f"\n  written: {target}")


if __name__ == "__main__":
    main()

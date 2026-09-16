"""Re-run wf_pine's ablation with every leg charged the SAME way, and report
the account internals the original table left out.

WHY. `wf_pine.ablate` builds seven versions of the Pine rule back to back and
calls `wf_attach.spread_of` after each one. That helper sets
`slippage.ENABLED = True` and never restores it, and `wf_pine.trades_for`
(:349) calls `slippage.fill` while BUILDING a trade. So leg 1 is built with
slippage off and charged the half-spread once; legs 2-7 are built with it on
and charged twice. Proved 2026-09-16 by printing the flag at the top of each
leg (leg 1 False, legs 2-7 True) and by rebuilding one leg both ways.

That is the whole of NEXT_TESTS item 4. "Dropping the RSI leg RAISES account
CAGR while per-trade expectancy FALLS (0.105 -> 0.057)" compared leg 1 against
leg 4, i.e. one backpack against two. Charged alike on 40 symbols the
expectancies are 0.100 and 0.096 -- the same number -- and the account earns
more simply because it gets ~18% more trades. There is no paradox to explain.

This is the third instance of the same bug shape (turtle_hold 2026-09-16, the
stop_sweep guard written against it, now here), so the guard lives in
`build()` below rather than in the caller's ordering.

Reads:  the price files via kitelab.config, /data/clean/kitelab/dashboard.json
        (for the buy-and-hold curve of the same cell), and the cached
        wf_attach hold pickle. Writes: output/measurements/pine_ablate_fix_
        <date>.csv and prints two tables. Rebuilds nothing, edits no stamped
        module.
"""
from __future__ import annotations

import csv
import time
from datetime import date
from pathlib import Path

import pandas as pd

from kitelab import config, portfolio, slippage
import scripts.dashboard_data as dd
import scripts.wf_attach as wa
import scripts.wf_pine as wp
from scripts.wf_pine import hold_cagrs, load_board, universes

OUT = Path(__file__).resolve().parent.parent / "output"
TF = "W"                      # the timeframe item 4 is about
START = pd.Timestamp("2018-01-01")
CAPITAL, RISK = 10_000_000, 0.01

LEGS = [("full rule", dict()),
        ("no HTF EMA filter", dict(use_htf=False)),
        ("no wick condition", dict(use_wick=False)),
        ("no RSI condition", dict(use_rsi=False)),
        ("HTF filter alone", dict(use_wick=False, use_rsi=False)),
        ("wick alone", dict(use_htf=False, use_rsi=False)),
        ("RSI alone", dict(use_htf=False, use_wick=False))]


def _build(symbols, kw):
    trades = []
    skipped = 0
    for sym in symbols:
        try:
            sig = wp.signals(sym, TF)
        except SystemExit:
            skipped += 1
            continue
        if len(sig) == 0:
            skipped += 1
            continue
        trades.extend(wp.trades_for(sym, sig, tf=TF, side="long",
                                    stop_mode="atr", fill="open", **kw))
    return trades, skipped


def build(symbols, kw):
    """_build with the spread forced OFF, whatever the caller left on."""
    was_enabled, was_cap = slippage.ENABLED, slippage.MAX_PARTICIPATION
    slippage.ENABLED, slippage.MAX_PARTICIPATION = False, None
    slippage.reset()
    try:
        return _build(symbols, kw)
    finally:
        slippage.ENABLED, slippage.MAX_PARTICIPATION = was_enabled, was_cap
        slippage.reset()


def account(trades):
    window = [t for t in sorted(trades, key=lambda t: t["entry_ts"])
              if pd.Timestamp(t["entry_ts"]) >= START]
    if not window:
        return None, len(window)
    return dd.run_payload(portfolio.run(window, CAPITAL, RISK,
                                        dd.PRIORITY_DEFAULT)), len(window)


def row(name, trades):
    st = wp.trade_stats(trades)
    pay, in_window = account(trades)
    return {"leg": name, "trades": st["n"], "win_pct": st.get("win_rate"),
            "expectancy_r": st.get("expectancy_r"), "in_window": in_window,
            "cagr": None if pay is None else pay["cagr"],
            "taken": None if pay is None else pay["taken"],
            "signals": None if pay is None else pay["signals"],
            "wiped": None if pay is None else pay["wiped"],
            "maxdd": None if pay is None else pay["maxdd"],
            "exposure": None if pay is None else pay["exposure"]}


def table(title, rows, held):
    print(f"\n  {title}")
    print(f"  {'leg':<20}{'trades':>9}{'win%':>7}{'expct R':>9}{'taken':>8}"
          f"{'sigs':>8}{'CAGR':>7}{'vs hold':>9}{'maxdd':>8}")
    print("  " + "-" * 85)
    for r in rows:
        gap = None if (r["cagr"] is None or held is None) else round(r["cagr"] - held, 2)
        print(f"  {r['leg']:<20}{r['trades']:>9,}{r['win_pct']:>7}"
              f"{r['expectancy_r']:>9}{(r['taken'] or 0):>8,}{(r['signals'] or 0):>8,}"
              f"{str(r['cagr']):>7}{str(gap):>9}{str(r['maxdd']):>8}")


def main():
    t0 = time.time()
    cfg = config.load()
    symbols = sorted(cfg.merged)
    print(f"universe: {len(symbols)} symbols")
    print(f"timeframe {TF}: {wp.TIMEFRAMES[TF][0]}")

    payload = load_board()
    unis = universes(set(symbols), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(symbols), unis, [], board)
    held = hold_cagrs(holds).get("all|2018")
    print(f"  buy-and-hold on the same window: {held} %/yr")
    print(f"  stop=atr, fill=open, universe all, start 2018, risk {RISK:.0%}, "
          f"capital {CAPITAL:,}, priority {dd.PRIORITY_DEFAULT}")

    # --- arm 1: every leg built clean, charged the half-spread exactly once --
    fair = []
    for name, kw in LEGS:
        s0 = time.time()
        trades, skipped = build(symbols, kw)
        charged = wa.spread_of(trades)
        r = row(name, charged)
        r["arm"] = "fair"
        fair.append(r)
        print(f"    fair  {name:<20} {r['trades']:>7,} trades "
              f"({skipped} symbols skipped) {time.time()-s0:>5.0f}s", flush=True)

    # --- arm 2: the original sequence, reproduced exactly ---------------------
    # slippage OFF at entry, then spread_of after each leg leaves it ON, so
    # every leg after the first is built through slippage.fill and charged
    # again afterwards. This arm exists to prove the correction, not to be
    # believed: it should reproduce output/logs/wf_pine_W_ablate_2026-09-11.log.
    slippage.ENABLED, slippage.MAX_PARTICIPATION = False, None
    slippage.reset()
    as_run = []
    for name, kw in LEGS:
        s0 = time.time()
        flag = slippage.ENABLED
        trades, _ = _build(symbols, kw)          # deliberately UNGUARDED
        charged = wa.spread_of(trades)
        r = row(name, charged)
        r["arm"] = "as_run"
        r["built_with_slippage_on"] = flag
        as_run.append(r)
        print(f"    as-run {name:<20} {r['trades']:>7,} trades "
              f"(ENABLED at build: {flag}) {time.time()-s0:>5.0f}s", flush=True)

    print("\n" + "=" * 89)
    table("AS THE 2026-09-11 RUN CHARGED IT -- leg 1 once, legs 2-7 twice", as_run, held)
    table("CHARGED ALIKE -- every leg built clean, half-spread applied once", fair, held)

    print("\n  'taken' is how many of the trades the account could actually")
    print("  fund; 'sigs' how many it was offered. The gap is the cash")
    print("  constraint, and it is what the CAGR column is really measuring.")

    out = OUT / "measurements" / f"pine_ablate_fix_{date.today()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = as_run + fair
    cols = ["arm", "leg", "trades", "win_pct", "expectancy_r", "in_window",
            "taken", "signals", "cagr", "wiped", "maxdd", "exposure"]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n  wrote {out.relative_to(OUT.parent)} ({len(rows)} rows x {len(cols)} cols)"
          f"  [{(time.time()-t0)/60:.1f} min]")


if __name__ == "__main__":
    main()

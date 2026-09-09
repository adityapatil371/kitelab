"""How much of the board is the fill-timing lookahead?

THE CHEAT. kitelab/backtest.py ships NEXT_OPEN_FILLS = False, and its own
docstring calls it what it is: the fill happens AT the close that produced the
decision -- "a mild lookahead -- you cannot trade a print you are still waiting
to see". All 10,260 cells on the board are priced that way. The `fill` grid
axis does NOT touch it: dashboard_data.execution() sets the slippage globals
and the participation cap only, and nothing anywhere in the build ever assigns
NEXT_OPEN_FILLS. So the number nobody has is: how much of the board is timing?

ADDED 2026-09-09, prompted by a proposal to paper-trade the rules on live data.
A live paper trade cannot fill at a close it has not seen yet -- it has to act
at the next session's open -- so without this number a forward test cannot say
whether a shortfall is the rules failing or the lookahead being removed. That
makes this a precondition for the paper-trading rig, not a side quest.

WHAT IT DOES. For every strategy whose engine implements the flag, it rebuilds
the trade lists twice from bars -- arm A at the board's convention, arm B at
next-open fills -- and runs both through the same account engine at the same
execution settings. The CAGR difference is the lookahead.

WHICH STRATEGIES. ALL 19 SINCE 2026-09-09. The first run of this script could
only measure the 3 rows built by kitelab/backtest.py, because it was the only
engine with a next-open path: timeframes.py "mirrors backtest.simulate's walk
exactly" (its own docstring) as a SEPARATE reimplementation, and darvas.py and
holygrail.py are separate again. The flag now lives in backtest.py as the whole
board's switch and the other three read it through the module. Eligibility is
still DERIVED from the registry rather than hardcoded, and build_arm catches the
per-engine refusals, so a family that gains or loses a path is reflected here
without an edit.

ONE FAMILY MOVES LESS THAN THE OTHERS, and it is not a gap. holygrail.py's
ENTRY is a resting buy-stop at the signal candle's high, filled intrabar on a
LATER bar -- it never had the fill-timing lookahead to remove. The flag moves
its two exits and nothing else. That does NOT make the hg row near zero, as
this paragraph once claimed: measured 2026-09-09 on the cached 11,256 arm-A
trades against each bar's own close, the next open sits +0.05 R above a
close-triggered stop or trail exit and +0.16 R above the close that banks the
target half (only 34% and 24% of cases go the other way), together +14.5% of
the rule's total net profit. Both exits are read at closes that the next open
tends to beat, so arm B is BETTER for hg -- which is what the -3.87 pts/yr
median (all 10 cells negative) on the 2026-09-09 full run says. Overnight
drift after a weak close, momentum after a strong one; the code path was
right, the prediction was wrong.

TWO TRAPS THIS IS BUILT AROUND.

  1. The signal cache cannot see this flag. signals.stamp() digests module
     MTIMES, and setting a global changes no file, so a cache built under arm A
     would be served to arm B without a word of complaint. And unlike the
     spread, next-open timing changes WHICH BARS a trade reads, so it cannot be
     applied to a cached trade after the fact. This script therefore never
     calls signals.load or signals.save: it builds from bars every time and
     writes nothing into the cache directory. That is also why it is slow.

  2. slippage.apply_spread is NOT idempotent -- a second pass moves the entry
     again (measured 2026-09-08: 46.5 bps on a real cached trade). It is
     applied exactly ONCE per arm, after execution(), mirroring
     scripts/dashboard_data.py:814-816, so the absolute numbers are comparable
     to the board rather than only to each other.

THE SELF-CHECK. Arm A over the full universe must reproduce the board's own
ema|0|all|1|10000000|1|2018|liquidity cell. If it does not, this harness is
wrong and the difference it reports means nothing -- so the run says so loudly
instead of printing a number. A pilot run (--symbols N) cannot reproduce a
board cell and reports the check as SKIPPED rather than passing it vacuously.

NOTHING HERE IS EDITED INTO kitelab/. NEXT_OPEN_FILLS is set as a run-time
global, the way dashboard_data.execution() sets the slippage globals: editing
kitelab/*.py for a diagnostic would move its mtime and invalidate every signal
cache on the box, forcing a ~103-minute rebuild to answer a minutes-long
question.

Reads:  /data/clean/kitelab/*.parquet (via kitelab.frames), and
        /data/clean/kitelab/dashboard.json for the self-check baseline.
Writes: output/wf_lookahead_<date>.csv, output/wf_lookahead_<date>.png

    python3 -m scripts.wf_lookahead --symbols 60   # timed pilot, no self-check
    python3 -m scripts.wf_lookahead                # full universe
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")          # no windows -- CLAUDE.md
import matplotlib.pyplot as plt
import pandas as pd

from kitelab import backtest, config, portfolio, registry, slippage
from kitelab.config import CLEAN
from scripts import dashboard_data as dd

OUT = Path(__file__).resolve().parent.parent / "output"

# The board cell arm A must reproduce. Read off the built grid, not constructed:
# the key format is dashboard_data's business, not this script's.
SELF_CHECK_KEY = "ema|0|all|1|10000000|1|2018|liquidity"
SELF_CHECK_STRAT, SELF_CHECK_YEAR, SELF_CHECK_RISK = "ema|0", 2018, 1.0
SELF_CHECK_CAPITAL, SELF_CHECK_PRIORITY = 10_000_000, "liquidity"
SELF_CHECK_TOL = 0.05          # the board rounds cagr to 1 decimal place

# Every trade this script prices must carry these, or portfolio.run is being
# handed a shape it cannot reason about.
REQUIRED = ("symbol", "entry_ts", "exit_ts", "entry_price", "exit_price",
            "charges", "same_session", "net_profit")


# What the flag actually moves, per producer. Keyed on Strategy.module so an
# engine that loses its path shows up as a KeyError here rather than being
# silently measured as zero.
MOVES = {"backtest.py":   "entry + exits",
         "timeframes.py": "entry + exits",
         "darvas.py":     "entry + exits",
         "holygrail.py":  "exits only (entry is a resting buy-stop already; NOT near zero, see above)"}


def eligible() -> list:
    """Strategies whose engine implements NEXT_OPEN_FILLS, from the registry."""
    ok, skipped = [], []
    for s in registry.REGISTRY:
        (ok if s.module in MOVES else skipped).append(s)

    print(f"strategies on the board: {len(registry.REGISTRY)}")
    print(f"  eligible -- engine has a next-open path: {len(ok)}")
    by_mod: dict[str, list[str]] = {}
    for s in ok:
        by_mod.setdefault(s.module, []).append(f"{s.key}|{dd.tag(s.variant)}")
    for mod, keys in sorted(by_mod.items()):
        print(f"      {mod:<16} {len(keys):>2}: {MOVES[mod]}")
        print(f"      {'':<16}    {', '.join(keys)}")
    print(f"  skipped  -- engine has none:              {len(skipped)}")
    for s in skipped:
        print(f"      {s.module:<16} {s.key}|{dd.tag(s.variant)}")
    if not ok:
        raise SystemExit("No strategy implements NEXT_OPEN_FILLS -- nothing to measure.")
    return ok


def check_shape(trades: list[dict], key: str) -> None:
    if not trades:
        raise SystemExit(f"{key}: produced no trades at all -- refusing to price nothing.")
    missing = [c for c in REQUIRED if c not in trades[0]]
    if missing:
        raise SystemExit(f"{key}: trades are missing {missing}. portfolio.run needs "
                         f"every field in REQUIRED; has the trade shape changed?")


def build_arm(strategies, universe, next_open: bool) -> dict:
    """Trade lists for one fill convention. Built from bars, never cached."""
    backtest.NEXT_OPEN_FILLS = next_open
    # Build UNSPREAD, exactly as the dashboard builds its signal caches:
    # signal_lists() runs before execution() does (dashboard_data.py:726 vs :814).
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()

    label = "next-open fills" if next_open else "fill at the signal close"
    print(f"\n  arm {'B' if next_open else 'A'} -- {label} "
          f"(NEXT_OPEN_FILLS = {next_open})", flush=True)
    out = {}
    for strat in strategies:
        key = f"{strat.key}|{dd.tag(strat.variant)}"
        trades, skipped, t0 = [], 0, time.time()
        try:
            for symbol in universe:
                try:
                    trades.extend(strat.build(symbol))
                except SystemExit:     # unusable bars; cached_signals skips these too
                    skipped += 1
        except ValueError as exc:      # the scale_out / stop_on_close guards
            print(f"      {key:<12} SKIPPED under this convention -- {exc}", flush=True)
            continue
        check_shape(trades, key)
        out[key] = trades
        print(f"      {key:<12} {len(trades):>7,} trades   "
              f"{skipped:>4} symbols skipped   {time.time() - t0:>5.0f}s", flush=True)
    return out


def price(named: dict) -> dict:
    """Charge the spread ONCE, at the board's Realistic-fills settings.

    Leaves slippage in that state on purpose: the participation cap is read
    inside portfolio.run, so the account pass must see it too.
    """
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = dd.REALISTIC_PARTICIPATION
    slippage.reset()
    return {k: [slippage.apply_spread(t) for t in v] for k, v in named.items()}


def cells(named: dict, years, risks) -> dict:
    """CAGR per (strategy, start year, risk) at the self-check capital/priority."""
    out = {}
    for key, trades in named.items():
        subset = sorted(trades, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        print(f"      {key}: {len(subset):,} trades before the start-year cut")
        for year in years:
            cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
            window = subset[cut:]
            print(f"        from {year}: {len(window):,} trades after the cut", flush=True)
            for risk in risks:
                if not window:
                    out[(key, year, risk)] = None
                    continue
                r = portfolio.run(window, SELF_CHECK_CAPITAL, risk / 100,
                                  SELF_CHECK_PRIORITY)
                out[(key, year, risk)] = (None if r["cagr_pct"] is None
                                          else float(r["cagr_pct"]))
    return out


def self_check(arm_a: dict, full_universe: bool) -> str:
    if not full_universe:
        return "SKIPPED -- a pilot cannot reproduce a board cell built over 1,000 symbols"
    path = CLEAN / "dashboard.json"
    if not path.exists():
        return f"SKIPPED -- no dashboard.json at {path}"
    cell = json.loads(path.read_bytes()).get("grid", {}).get(SELF_CHECK_KEY)
    if not cell or cell.get("cagr") is None:
        return f"SKIPPED -- {SELF_CHECK_KEY} is not on the built board"
    mine = arm_a.get((SELF_CHECK_STRAT, SELF_CHECK_YEAR, SELF_CHECK_RISK))
    if mine is None:
        return "SKIPPED -- this run did not produce the matching cell"
    got, want = round(mine, 1), float(cell["cagr"])
    ok = abs(got - want) <= SELF_CHECK_TOL
    return (f"{'PASS' if ok else 'FAIL'} -- {SELF_CHECK_KEY}: board {want:.1f}%/yr, "
            f"this harness {got:.1f}%/yr")


def figure(rows: list[dict], stamp: str) -> Path:
    fig, ax = plt.subplots(figsize=(7, 7))
    have = [r for r in rows if r["cagr_close_fill"] is not None
            and r["cagr_next_open"] is not None]
    for key in sorted({r["strategy"] for r in have}):
        pts = [r for r in have if r["strategy"] == key]
        ax.scatter([p["cagr_close_fill"] for p in pts],
                   [p["cagr_next_open"] for p in pts], s=45, alpha=0.8, label=key)
    if have:
        lo = min(min(p["cagr_close_fill"], p["cagr_next_open"]) for p in have)
        hi = max(max(p["cagr_close_fill"], p["cagr_next_open"]) for p in have)
        pad = 0.05 * (hi - lo or 1.0)
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], "k--", lw=1,
                label="no lookahead (y = x)")
    ax.set_xlabel("CAGR %/yr -- fill at the signal close (the board)")
    ax.set_ylabel("CAGR %/yr -- fill at the next open (tradable)")
    ax.set_title("The fill-timing lookahead\npoints below the line = the board was flattered")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = OUT / f"wf_lookahead_{stamp}.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", type=int, default=None,
                    help="pilot on the first N symbols to time the run "
                         "(skips the self-check, which needs the full universe)")
    args = ap.parse_args()

    t_start = time.time()
    cfg = config.load()
    universe = list(cfg.merged)
    print(f"universe: {len(universe)} symbols")
    full = args.symbols is None
    if not full:
        universe = universe[:args.symbols]
        print(f"  PILOT: first {len(universe)} symbols -- timing run, not a result")

    strategies = eligible()
    years, risks = dd.START_YEARS, dd.RISKS
    print(f"\ncells per arm: {len(strategies)} strategies x {len(years)} start years "
          f"x {len(risks)} risks = {len(strategies) * len(years) * len(risks)}"
          f"  (capital Rs{SELF_CHECK_CAPITAL:,}, priority '{SELF_CHECK_PRIORITY}')")

    base_a = build_arm(strategies, universe, next_open=False)
    base_b = build_arm(strategies, universe, next_open=True)
    backtest.NEXT_OPEN_FILLS = False       # leave the module as we found it

    shared = sorted(set(base_a) & set(base_b))
    print(f"\nstrategies with BOTH arms: {len(shared)} of {len(base_a)} built")
    if not shared:
        raise SystemExit("No strategy produced both arms -- nothing to compare.")

    sample = base_a[shared[0]][:3]
    print(f"\nfirst 3 trades of {shared[0]} (arm A), {len(REQUIRED)} required fields:")
    for t in sample:
        print(f"    {t['symbol']:<14} {str(t['entry_ts'])[:10]} -> {str(t['exit_ts'])[:10]}"
              f"   entry {t['entry_price']:>9.2f}  exit {t['exit_price']:>9.2f}")

    print("\n  charging the spread once per arm (costs + 1% participation cap)")
    priced_a = price({k: base_a[k] for k in shared})
    priced_b = price({k: base_b[k] for k in shared})

    print("\n  account pass, arm A (fill at the signal close):")
    cells_a = cells(priced_a, years, risks)
    print("\n  account pass, arm B (fill at the next open):")
    cells_b = cells(priced_b, years, risks)

    verdict = self_check(cells_a, full)
    print(f"\nSELF-CHECK: {verdict}")

    rows = []
    for key in shared:
        for year in years:
            for risk in risks:
                a, b = cells_a.get((key, year, risk)), cells_b.get((key, year, risk))
                rows.append({"strategy": key, "start_year": year, "risk_pct": risk,
                             "cagr_close_fill": None if a is None else round(a, 3),
                             "cagr_next_open": None if b is None else round(b, 3),
                             "lookahead_pts": None if a is None or b is None
                                              else round(a - b, 3)})

    gaps = [r["lookahead_pts"] for r in rows if r["lookahead_pts"] is not None]
    print(f"\ncells compared: {len(rows)}, with a CAGR on both sides: {len(gaps)}")

    print(f"\n{'strategy':<12}{'from':>6}{'risk':>7}{'close fill':>12}"
          f"{'next open':>12}{'lookahead':>11}")
    for r in rows:
        fmt = lambda v: "  wiped" if v is None else f"{v:>7.2f}"
        print(f"{r['strategy']:<12}{r['start_year']:>6}{r['risk_pct']:>7.1f}"
              f"{fmt(r['cagr_close_fill']):>12}{fmt(r['cagr_next_open']):>12}"
              f"{fmt(r['lookahead_pts']):>11}")

    if gaps:
        print("\nLOOKAHEAD, points of CAGR per year the board gains from filling "
              "at a close it has not seen:")
        print(f"  median {statistics.median(gaps):+.2f}   "
              f"min {min(gaps):+.2f}   max {max(gaps):+.2f}   "
              f"cells where it FLATTERED the board: {sum(g > 0 for g in gaps)}/{len(gaps)}")
        for key in shared:
            g = [r["lookahead_pts"] for r in rows
                 if r["strategy"] == key and r["lookahead_pts"] is not None]
            if g:
                print(f"    {key:<12} median {statistics.median(g):+.2f} pts/yr "
                      f"over {len(g)} cells")

    OUT.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    if not full:
        stamp += f"_pilot{len(universe)}"
    csv_path = OUT / f"wf_lookahead_{stamp}.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {csv_path}")
    print(f"wrote {figure(rows, stamp)}")
    print(f"total {time.time() - t_start:.0f}s")
    if verdict.startswith("FAIL"):
        raise SystemExit("\nSELF-CHECK FAILED -- this harness does not reproduce the "
                         "board, so the lookahead number above is not trustworthy. "
                         "Do not quote it.")


if __name__ == "__main__":
    main()

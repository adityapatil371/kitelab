"""Stage 2 of the Nifty 500 study: the board's 36 rules, run twice.

    python3 -m scripts.n500_grid --pilot 2      # measured estimate, then stop
    python3 -m scripts.n500_grid                # the full run, start 2018
    python3 -m scripts.n500_grid --start 2012

WHAT THIS MEASURES. scripts.n500_hold showed that BUYING AND HOLDING today's
Nifty 500 list from 2018 earns +4.01 CAGR points a year that nobody could have
earned in 2018, because NSE rebalances the index every March and September and
today's membership was partly awarded for going up. That is the membership
bias, measured on a passive basket.

This asks the next question: does the same bias flatter a RULE? A trading rule
only buys a stock when its own condition fires, so it is not obvious that a
flattered universe flatters the rule by the same amount -- it might buy the
inflated names rarely, or it might buy them exactly when they are running.
So every board row is run on two universes and the gap is reported per row:

  TODAY  the 496-name Nifty 500 study universe (data/keep/nifty500.json).
         Today's membership. This is the arm with look-ahead in it.
  PIT    a point-in-time top 500 for the start year, ranked on median daily
         traded value using ONLY bars dated before the start
         (output/measurements/n500_pit_list_<year>_<date>.csv, written by
         scripts.n500_hold). This is a list you could have built that year.
  ALL    cfg.merged, the board's own 1,000. Not reported -- it is the
         self-check, and it must reproduce a built grid cell to the digit.

WHAT IT IS NOT. Neither arm is survivorship-free. Both are drawn from stocks
that still trade today, so companies that died between the start year and now
are missing from BOTH sides and the measured gap is a FLOOR on the real bias,
not an estimate of it. Say so wherever the number is quoted.

THE TWO SELF-CHECKS, both before anything is written:
  1. ACCOUNT. Arm ALL reproduces dashboard.json's SELF_CHECK_KEY cell exactly
     (cagr, final, maxdd, taken, sharpe). If it does not, this harness is
     simulating a different account and says so instead of printing a number.
  2. TRADES. 109 of the 496 study names are outside cfg.merged, so they have
     no signal cache and their trades are built here by strat.build(). That is
     only sound if a trade built here is identical to a trade the board
     cached. So a sample of IN-universe symbols is rebuilt and compared to the
     cache field by field. If they differ, the 109 are not on the same footing
     as the 387 and the run stops.

WHAT IT DOES NOT TOUCH. Nothing under kitelab/ and not scripts/dashboard_data.py.
signals.stamp() digests those, and editing one invalidates every signal cache
and grid checkpoint -- a ~103-178 minute rebuild. Everything here is out of band.

Reads:  /data/clean/kitelab/dashboard.json
        /data/clean/kitelab/signal_cache/*_all.pkl
        data/keep/nifty500.json
        output/measurements/n500_pit_list_<year>_<date>.csv
        the cleaned parquet candles, via kitelab.frames
Writes: output/measurements/n500_grid_<year>_<date>.csv
        output/measurements/n500_grid_ckpt_<year>_<board>/<strategy>.pkl
Cost:   run --pilot 2 first; it prints a measured estimate for the full run.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import pickle
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from kitelab import backtest, config, portfolio, registry, slippage
from kitelab.config import CLEAN
from scripts import dashboard_data as dd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "measurements"
CACHE = CLEAN / "signal_cache"
KEEP = ROOT / "data" / "keep"

START_DEFAULT = 2018

# The board cell arm ALL must reproduce. Every slot except the family is read
# from the file that defines it -- see scripts/wf_attach.py, which lost this
# leg twice at the end of a finished grid by hard-coding an axis value the
# board later retired. The year is the only slot this script varies.
SELF_CHECK_FAMILY = "e1"

# How many in-universe symbols to rebuild for self-check 2. Small, because it
# is a proof of identity, not a sample statistic: one mismatched field on one
# symbol is enough to stop the run.
TRADE_CHECK_N = 8

# Fields compared in self-check 2. The whole dict is not compared because a
# float built today can differ from one pickled a fortnight ago in the last
# bit; these are the fields the account actually consumes, rounded.
TRADE_FIELDS = ["symbol", "entry_ts", "exit_ts", "entry_price", "exit_price",
                "shares", "exit_reason", "net_profit"]


def self_check_key(year):
    v = registry.variants(SELF_CHECK_FAMILY)[0]
    return (f"{SELF_CHECK_FAMILY}|{dd.tag(v)}|all|{max(dd.RISKS):g}"
            f"|{max(dd.CAPITALS)}|{dd.GRID_FILLS[0]}|{year}|{dd.PRIORITY_DEFAULT}")


def board_key(payload, year):
    """Which board, which start year, and which version of THIS file.

    The built timestamp alone cannot see that this script's arithmetic moved:
    wf_attach.board_key() records the afternoon that cost it a stale hold
    curve. So the source is hashed in too, and a checkpoint written by an
    older version of this script is simply not found.
    """
    h = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]
    return f"{payload['built'].split()[0]}_{year}_{h}"


# ------------------------------------------------------------ universes ----
def load_today(path):
    blob = json.load(open(path))
    study = blob["study"]
    print(f"  read {path}")
    print(f"    {blob['n_constituents']} constituents, {len(study)} in the study "
          f"({len(blob['study_dropped']['no_price_file'])} no price file, "
          f"{len(blob['study_dropped']['data_defect'])} known data defect)")
    return study


def load_pit(year, stamp):
    path = OUT / f"n500_pit_list_{year}_{stamp}.csv"
    if not path.exists():
        raise SystemExit(
            f"no point-in-time list at {path}.\n"
            f"Build it first: python3 -m scripts.n500_hold --start {year}")
    rows = list(csv.DictReader(open(path)))
    if "symbol" not in (rows[0] if rows else {}):
        raise SystemExit(f"{path} has no 'symbol' column; columns are "
                         f"{list(rows[0]) if rows else 'none'}")
    syms = sorted({r["symbol"].strip().upper() for r in rows if r["symbol"].strip()})
    print(f"  read {path}: {len(rows)} rows -> {len(syms)} symbols")
    return syms


# --------------------------------------------------------------- trades ----
def spread_of(trades):
    """The board's own execution: costs on, one order <= 1% of daily turnover.

    Same two globals in the same order as dashboard_data.execution(). The
    participation cap is enforced inside portfolio.run, not here.
    """
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = dd.REALISTIC_PARTICIPATION
    slippage.reset()
    return [slippage.apply_spread(t) for t in trades]


def cached_trades(strat):
    p = CACHE / f"{strat.cache}_all.pkl"
    if not p.exists():
        return None
    blob = pickle.loads(p.read_bytes())
    return blob["trades"] if isinstance(blob, dict) else blob


def build_trades(strat, symbols):
    """Trades for symbols the board has no cache for, built the board's way.

    UNSPREAD and at CLOSE fills, because that is the state the signal caches
    were written in: dashboard_data builds the cache first and charges the
    spread afterwards. Self-check 2 proves these two lines put a rebuilt trade
    byte-for-byte where the cache put it.
    """
    backtest.NEXT_OPEN_FILLS = False
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    out = []
    for symbol in symbols:
        try:
            out.extend(strat.build(symbol))
        except SystemExit:
            pass            # no bars, or too few -- the board skips these too
    return out


def account(trades, members, year):
    """One cell: the board's account run on `members` from January of `year`."""
    subset = [t for t in trades if members is None or t["symbol"] in members]
    subset.sort(key=lambda t: t["entry_ts"])
    stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
    window = subset[bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01")):]
    if not window:
        return None
    return dd.run_payload(portfolio.run(window, max(dd.CAPITALS),
                                        max(dd.RISKS) / 100, dd.PRIORITY_DEFAULT))


# ---------------------------------------------------------- self-checks ----
def check_account(payload, strategies, year):
    key = self_check_key(year)
    want = payload["grid"].get(key)
    if want is None:
        raise SystemExit(f"self-check cell {key} is not in the grid. The board "
                         f"has start years "
                         f"{sorted({k.split('|')[6] for k in payload['grid']})}.")
    skey, band = key.split("|")[0], key.split("|")[1]
    strat = next((s for s in strategies
                  if f"{s.key}|{dd.tag(s.variant)}" == f"{skey}|{band}"), None)
    if strat is None:
        raise SystemExit(f"self-check strategy {skey}|{band} is not in the registry")
    raw = cached_trades(strat)
    if raw is None:
        raise SystemExit(f"self-check needs {strat.cache}_all.pkl and it is missing")
    got = account(spread_of(raw), None, year)
    bad = [f for f in ("cagr", "final", "maxdd", "taken", "sharpe")
           if got[f] != want[f]]
    if bad:
        raise SystemExit(
            f"\n  SELF-CHECK 1 FAILED on {key}: {bad} disagree with the built "
            f"grid.\n  board {[want[f] for f in bad]} vs here "
            f"{[got[f] for f in bad]}\n  This harness is not simulating the "
            f"board's account. Nothing was written.\n")
    print(f"  SELF-CHECK 1 PASSED (account): {key}")
    print(f"    reproduces the board -- cagr {got['cagr']}, "
          f"final {got['final']:,}, taken {got['taken']}")
    return {"cell": key, "fields": ["cagr", "final", "maxdd", "taken", "sharpe"],
            "board": {f: want[f] for f in
                      ("cagr", "final", "maxdd", "taken", "sharpe")},
            "here": {f: got[f] for f in
                     ("cagr", "final", "maxdd", "taken", "sharpe")}}


def check_trades(strategies, members):
    """A rebuilt trade must equal the cached one, or the 109 are not comparable."""
    strat = next(s for s in strategies if cached_trades(s) is not None)
    cache = cached_trades(strat)
    have = sorted({t["symbol"] for t in cache})
    sample = have[:TRADE_CHECK_N]
    rebuilt = build_trades(strat, sample)

    def canon(ts):
        rows = []
        for t in ts:
            rows.append(tuple(
                round(t[f], 4) if isinstance(t.get(f), float) else t.get(f)
                for f in TRADE_FIELDS))
        return sorted(rows)

    a = canon([t for t in cache if t["symbol"] in set(sample)])
    b = canon(rebuilt)
    print(f"  self-check 2 (trades): {strat.key}|{dd.tag(strat.variant)} on "
          f"{len(sample)} cached symbols -- {len(a)} cached vs {len(b)} rebuilt")
    if a != b:
        diff = next((x for x, y in zip(a, b) if x != y), ("length", len(a), len(b)))
        raise SystemExit(
            f"\n  SELF-CHECK 2 FAILED: a trade rebuilt here is not the trade "
            f"the board cached.\n  first disagreement: {diff}\n  The 109 study "
            f"names outside cfg.merged would not be on the same footing as the "
            f"387 inside it. Nothing was written.\n")
    print("  SELF-CHECK 2 PASSED (trades): rebuilt == cached, field for field")
    return {"strategy": f"{strat.key}|{dd.tag(strat.variant)}",
            "n_symbols": len(sample), "n_trades": len(a),
            "fields": TRADE_FIELDS}


# ----------------------------------------------------------------- main ----
def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=START_DEFAULT)
    ap.add_argument("--pilot", type=int, metavar="N",
                    help="run only the first N strategies, print a measured "
                         "estimate for the full run, then stop")
    args = ap.parse_args()
    year = args.start

    dash = CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"No dashboard.json at {dash}. "
                         f"Build it: python3 -m scripts.refresh")
    payload = json.loads(dash.read_bytes())
    print(f"dashboard.json: built {payload['built']}, "
          f"{len(payload['grid']):,} grid cells")

    cfg = config.load()
    merged = set(cfg.merged)
    print(f"  cfg.merged: {len(merged)} stocks")

    stamp = date.today().isoformat()
    today = load_today(KEEP / "nifty500.json")
    pit = load_pit(year, stamp)
    outside = sorted(s for s in today if s not in merged)
    print(f"  TODAY arm: {len(today)} names, {len(today)-len(outside)} cached, "
          f"{len(outside)} to build")
    print(f"  PIT   arm: {len(pit)} names, "
          f"{sum(1 for s in pit if s in merged)} cached, "
          f"{sum(1 for s in pit if s not in merged)} to build")
    print(f"  overlap TODAY n PIT: {len(set(today) & set(pit))} names "
          f"({len(set(today) & set(pit))/len(today):.1%} of TODAY)")

    strategies = registry._build_registry()
    print(f"  registry: {len(strategies)} strategy rows\n")
    chk1 = check_account(payload, strategies, year)
    chk2 = check_trades(strategies, merged)

    board = board_key(payload, year)
    ckpt_dir = OUT / f"n500_grid_ckpt_{board}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    print(f"  checkpoints: {ckpt_dir}\n")

    run = strategies[:args.pilot] if args.pilot else strategies
    if args.pilot:
        print(f"PILOT: {args.pilot} of {len(strategies)} strategies\n")

    arms = {"today": set(today), "pit": set(pit)}
    rows, t0 = [], time.time()
    for i, strat in enumerate(run, 1):
        rid = f"{strat.key}|{dd.tag(strat.variant)}"
        ck = ckpt_dir / f"{strat.cache}.pkl"
        if ck.exists():
            rows.append(pickle.loads(ck.read_bytes()))
            print(f"  [{i:2d}/{len(run)}] {rid:22s} (checkpoint)")
            continue
        ts = time.time()
        cache = cached_trades(strat)
        if cache is None:
            print(f"  [{i:2d}/{len(run)}] {rid:22s} NO SIGNAL CACHE -- skipped")
            continue
        extra = build_trades(strat, outside)
        allt = spread_of(list(cache) + extra)
        row = {"strategy": rid, "module": strat.module, "start": year,
               "n_cached_trades": len(cache), "n_built_trades": len(extra)}
        for arm, members in arms.items():
            got = account(allt, members, year)
            row[f"{arm}_cagr"] = None if got is None else got["cagr"]
            row[f"{arm}_taken"] = None if got is None else got["taken"]
            row[f"{arm}_maxdd"] = None if got is None else got["maxdd"]
            row[f"{arm}_sharpe"] = None if got is None else got["sharpe"]
        row["premium"] = (None if row["today_cagr"] is None
                          or row["pit_cagr"] is None
                          else round(row["today_cagr"] - row["pit_cagr"], 2))
        ck.write_bytes(pickle.dumps(row))
        rows.append(row)
        print(f"  [{i:2d}/{len(run)}] {rid:22s} today {row['today_cagr']:>7} "
              f"pit {row['pit_cagr']:>7}  premium {row['premium']:>6}  "
              f"({len(extra):,} built trades, {time.time()-ts:.1f}s)")
    took = time.time() - t0

    if args.pilot:
        print(f"\n  PILOT took {took/60:.2f} min for {args.pilot} strategies.")
        print(f"  Estimated full run: "
              f"{took/60/args.pilot*len(strategies):.1f} min (scaled by "
              f"strategy count; trade lists differ in size, so treat this as "
              f"an order of magnitude, not a promise).")
        print("  Checkpoints are kept -- the full run will reuse this work.\n")
        return

    done = [r for r in rows if r.get("premium") is not None]
    prem = sorted(r["premium"] for r in done)
    print(f"\n  {len(done)} of {len(strategies)} rows measured, {took/60:.1f} min")
    if done:
        mid = prem[len(prem)//2] if len(prem) % 2 else \
            round((prem[len(prem)//2 - 1] + prem[len(prem)//2]) / 2, 2)
        print("  premium (today - pit), CAGR points per year:")
        print(f"    median {mid}, range {prem[0]} to {prem[-1]}, "
              f"{sum(1 for p in prem if p > 0)} of {len(prem)} positive")

    dest = OUT / f"n500_grid_{year}_{stamp}.csv"
    cols = list(rows[0]) if rows else []
    with open(dest, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {dest} ({len(rows)} rows x {len(cols)} columns)")

    # Provenance the report reads rather than retypes: which board was
    # reproduced, on which cell, and what the two checks agreed on.
    side = OUT / f"n500_grid_check_{year}_{stamp}.json"
    side.write_text(json.dumps(
        {"start": year, "board_built": payload["built"],
         "n_study": len(today), "n_pit": len(pit),
         "n_built_symbols": len(outside),
         "overlap_today_pit": len(set(today) & set(pit)),
         "account_check": chk1, "trade_check": chk2}, indent=1))
    print(f"  wrote {side}")


if __name__ == "__main__":
    main()

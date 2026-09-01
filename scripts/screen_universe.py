"""Choose which stocks belong in the universe, on evidence rather than by hand.

    python -m scripts.screen_universe --candidates   # write the candidate list
    python -m scripts.screen_universe --rank         # screen what has been fetched
    python -m scripts.screen_universe --rank --target 500

Two phases, because daily candles cost ~4 requests per symbol and 15-minute ~22:

    1. --candidates   every ordinary NSE equity we do not already have, written to
                      data/candidates.txt
    2. backfill those DAILY ONLY  (a fifth of the price of a full pull)
    3. --rank         apply the quality gate to whatever arrived, stratify the
                      survivors across liquidity, and write data/accepted.txt
    4. backfill the accepted list properly, then add them to config.local.toml

THE QUALITY GATE. Every defect this project has been bitten by is a rejection rule
here, so bad data is refused at the door instead of being discovered months later in
a published number:

    history        >= MIN_YEARS of daily bars. The Q/M/W stack needs 20 QUARTERLY
                   bars to mean anything; a stock with 89 bars cannot support the
                   indicators computed from it.
    liquidity      >= MIN_TURNOVER median daily traded value. Below that, fills are
                   fiction and the slippage model is guessing.
    seams          no long gap with the price on a different level either side --
                   the VINEETLAB defect (Rs1,556 -> Rs41 across 350 days).
    padding        no run of invented bars in front of the first real trade -- the
                   JMFINANCIL defect (191 bars at Rs0.14 before Rs30.99).
    splits         no single-day move whose ratio lands on a common split fraction.
                   Kite serves prices UNADJUSTED, so a 2:1 split reads as a 50%
                   crash and stops a position out at a loss that never happened.
    integrity      no bar whose high/low fails to contain its own open/close, no
                   negative volume, no duplicated or out-of-order timestamps.

STRATIFICATION. Survivors are bucketed by median traded value and sampled evenly
across the buckets, so the universe spans micro to large rather than drifting to
whatever segment happens to have the cleanest files. Within a bucket the longest
history wins, because history is the one thing that cannot be acquired later.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config
from kitelab.config import DATA

MIN_YEARS = 5.0            # 20 quarterly bars for the Q/M/W stack
MIN_BARS = 1_100          # ~4.4 trading years; belt and braces with MIN_YEARS
MIN_TURNOVER = 2_000_000  # Rs20 lakh median daily traded value
BUCKETS = [(0, 1e7, "micro  < Rs1cr"), (1e7, 5e7, "small  Rs1-5cr"),
           (5e7, 2.5e8, "mid    Rs5-25cr"), (2.5e8, float("inf"), "large  > Rs25cr")]
SPLIT_RATIOS = [1/2, 1/3, 1/4, 1/5, 1/10, 2/5, 3/5, 2/3, 3/2, 2.0, 5/2, 3.0, 5.0, 10.0]


def ordinary_equities() -> pd.DataFrame:
    """Every ordinary NSE equity in the newest instruments dump."""
    dumps = sorted(DATA.glob("instruments_NSE_*.parquet"))
    if not dumps:
        raise SystemExit("No instruments_NSE_*.parquet -- run a backfill first, "
                         "which caches the daily instrument dump.")
    d = pd.read_parquet(dumps[-1])
    eq = d[(d.segment == "NSE") & (d.instrument_type == "EQ")].copy()

    def ordinary(s: str) -> bool:
        if re.search(r"-SG$|-GS$|^\d", s):            # government securities, bonds
            return False
        if re.search(r"-SM$|-ST$|-RE$|-BE$|-BZ$|-N\d$|-IV$|-PP$", s):
            return False                              # SME, rights, T2T, NCDs
        if "ETF" in s or s.endswith("BEES"):
            return False
        return True

    eq = eq[eq.tradingsymbol.map(ordinary) & (eq.lot_size == 1)]
    return eq.drop_duplicates("tradingsymbol").reset_index(drop=True), dumps[-1]


def assess(symbol: str) -> dict | None:
    """Every quality check, on the daily file. None if there is no file yet."""
    p = DATA / f"{symbol}_day.parquet"
    if not p.exists():
        return None
    try:
        d = pd.read_parquet(p, columns=["ts", "open", "high", "low", "close", "volume"])
    except Exception as exc:
        return {"symbol": symbol, "reject": f"unreadable ({type(exc).__name__})"}
    if d.empty:
        return {"symbol": symbol, "reject": "empty file"}

    ts = pd.to_datetime(d["ts"])
    o, h, l, c = (d[k].to_numpy(float) for k in ["open", "high", "low", "close"])
    v = d["volume"].to_numpy(float)
    traded = v > 0
    out = {"symbol": symbol, "bars": len(d), "reject": ""}

    def fail(why: str) -> dict:
        out["reject"] = why
        return out

    if not traded.any():
        return fail("no traded bar at all")
    years = (ts.iloc[-1] - ts.iloc[0]).days / 365.25
    out["years"] = round(years, 1)
    turnover = float(np.median((c * v)[traded]))
    out["turnover"] = turnover

    if ts.duplicated().any():
        return fail("duplicate timestamps")
    if (ts.diff() < pd.Timedelta(0)).any():
        return fail("timestamps out of order")
    if (v < 0).any():
        return fail("negative volume")
    if len(d) < MIN_BARS or years < MIN_YEARS:
        return fail(f"history {years:.1f}y / {len(d)} bars < {MIN_YEARS}y")
    if turnover < MIN_TURNOVER:
        return fail(f"turnover Rs{turnover:,.0f} < Rs{MIN_TURNOVER:,}")

    pos = (h > 0) & (l > 0) & (o > 0) & (c > 0)
    if int((pos & ((h < l) | (h < np.maximum(o, c) - 1e-9)
                   | (l > np.minimum(o, c) + 1e-9))).sum()) > 5:
        return fail("high/low does not contain open/close, repeatedly")

    first = int(traded.argmax())
    if first > 20:
        return fail(f"{first} invented bars before the first real trade")

    t = ts[traded].reset_index(drop=True)
    cc = pd.Series(c[traded]).reset_index(drop=True)
    gaps = t.diff().dt.days
    for i in gaps[gaps > 180].index:
        before, after = cc.iloc[max(0, i - 5):i].median(), cc.iloc[i:i + 5].median()
        if before and not np.isnan(after) and not (0.4 <= after / before <= 2.5):
            return fail(f"price seam: {before:,.2f} -> {after:,.2f} across "
                        f"{int(gaps.iloc[i])} days")

    ratio = cc.to_numpy()[1:] / np.where(cc.to_numpy()[:-1] == 0, np.nan, cc.to_numpy()[:-1])
    big = np.abs(ratio - 1) > 0.45
    near = np.zeros(len(ratio), dtype=bool)
    for k in SPLIT_RATIOS:
        near |= np.abs(ratio - k) < 0.03 * k
    if int(np.nansum(near & big)):
        return fail("suspected unadjusted split/bonus")
    if int(np.nansum(np.abs(ratio - 1) > 0.6)):
        return fail("unexplained single-day move over 60%")
    return out


def bucket_of(turnover: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= turnover < hi:
            return name
    return BUCKETS[-1][2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", action="store_true",
                    help="write data/candidates.txt: every ordinary equity we lack")
    ap.add_argument("--rank", action="store_true",
                    help="screen fetched daily files and write data/accepted.txt")
    ap.add_argument("--target", type=int, default=500,
                    help="how many stocks the universe should end up with")
    args = ap.parse_args()
    if not (args.candidates or args.rank):
        ap.error("choose --candidates or --rank")

    cfg = config.load()
    held = set(cfg.all_symbols) | set(config.EXCLUDED)
    eq, dump = ordinary_equities()
    print(f"\n  instruments dump: {dump.name}")
    print(f"  ordinary NSE equities: {len(eq):,}   already held: {len(held)}")

    if args.candidates:
        wanted = sorted(set(eq.tradingsymbol) - held)
        target = DATA / "candidates.txt"
        target.write_text(
            "# Ordinary NSE equities not yet in the universe.\n"
            f"# Written by scripts.screen_universe from {dump.name}.\n"
            "# Fetch DAILY ONLY first -- it is a fifth the cost of a full pull:\n"
            "#   python -m scripts.backfill --symbols-file data/candidates.txt "
            "--daily-only\n"
            + "\n".join(wanted) + "\n")
        mins = len(wanted) * 4 * 0.35 / 60
        print(f"\n  wrote {target} with {len(wanted):,} candidates")
        print(f"  daily-only backfill: ~{len(wanted)*4:,} requests, "
              f"~{mins:.0f} minutes of throttle plus network time\n")
        return

    # ---- rank -------------------------------------------------------------
    rows, missing = [], 0
    for s in sorted(set(eq.tradingsymbol) - held):
        a = assess(s)
        if a is None:
            missing += 1
        else:
            rows.append(a)
    print(f"  candidates with daily data fetched: {len(rows):,}"
          f"   (not yet fetched: {missing:,})")
    if not rows:
        raise SystemExit("\n  Nothing to rank. Fetch the candidates first:\n"
                         "    python -m scripts.backfill --symbols-file "
                         "data/candidates.txt --daily-only\n")

    passed = [r for r in rows if not r["reject"]]
    print(f"  passed every quality check:         {len(passed):,}\n")
    reasons: dict[str, int] = {}
    for r in rows:
        if r["reject"]:
            key = re.sub(r"[-\d,.]+", "N", r["reject"])[:52]
            reasons[key] = reasons.get(key, 0) + 1
    print("  rejected, by reason:")
    for k, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {n:5,}  {k}")

    need = max(0, args.target - len(cfg.all_symbols))
    by_bucket: dict[str, list] = {}
    for r in passed:
        by_bucket.setdefault(bucket_of(r["turnover"]), []).append(r)
    for v in by_bucket.values():
        v.sort(key=lambda r: -r["years"])      # history is the thing you cannot buy later

    print(f"\n  survivors by liquidity bucket (universe {len(cfg.all_symbols)} "
          f"-> target {args.target}, so {need} to add):")
    for _, _, name in BUCKETS:
        print(f"    {name:<20s} {len(by_bucket.get(name, [])):5,}")

    chosen, i = [], 0
    order = [n for _, _, n in BUCKETS if by_bucket.get(n)]
    while len(chosen) < need and order:
        progressed = False
        for name in order:
            pool = by_bucket[name]
            if i < len(pool) and len(chosen) < need:
                chosen.append(pool[i]); progressed = True
        i += 1
        if not progressed:
            break

    target = DATA / "accepted.txt"
    target.write_text(
        f"# {len(chosen)} stocks that passed every quality check, sampled evenly\n"
        f"# across liquidity buckets, longest history first within each.\n"
        "# Fetch them properly, then add them to [universe] holdout in "
        "config.local.toml:\n"
        "#   python -m scripts.backfill --symbols-file data/accepted.txt\n"
        + "\n".join(r["symbol"] for r in sorted(chosen, key=lambda r: r["symbol"])) + "\n")
    print(f"\n  wrote {target} with {len(chosen)} stocks")
    for _, _, name in BUCKETS:
        n = sum(1 for r in chosen if bucket_of(r["turnover"]) == name)
        if n:
            yrs = [r["years"] for r in chosen if bucket_of(r["turnover"]) == name]
            print(f"    {name:<20s} {n:4,}   median history "
                  f"{float(np.median(yrs)):.1f}y")
    print(f"\n  full 15-minute backfill for these: ~{len(chosen)*26:,} requests, "
          f"~{len(chosen)*26*0.35/60:.0f} minutes of throttle\n")


if __name__ == "__main__":
    main()

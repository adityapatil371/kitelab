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

    history        >= MIN_YEARS of daily bars SINCE THE LAST LISTING BREAK -- a gap
                   over frames.LISTING_BREAK_DAYS, a config.DEMERGERS ex-date or a
                   config.HISTORY_STARTS date -- because that is where
                   frames.sanitise() restarts the history on load (2026-09-07).
                   Until then it measured from the first bar, so STARHEALTH's 61
                   bars from 2016 bought it five years it did not have. The Q/M/W
                   stack needs 20 QUARTERLY bars to mean anything.
    liquidity      >= MIN_TURNOVER median daily traded value. Below that, fills are
                   fiction and the slippage model is guessing.
    padding        no run of invented bars in front of the first real trade -- the
                   JMFINANCIL defect (191 bars at Rs0.14 before Rs30.99).
    demergers      no close-to-close DROP over 30% on a day that was not market-wide.
                   Kite adjusts splits and bonuses at serve time (HAL 2:1 2023-07-28
                   1926.50 -> 1964.50; NESTLEIND 1:10 2024-01-05) but not demergers,
                   so the drop is the value that left the parent. Rejected until the
                   ex-date is entered in config.DEMERGERS, after which history restarts
                   there and the stock passes. This replaced the "unadjusted split"
                   gate on 2026-09-07: of its four verdicts, one was a demerger
                   (ORIENTPPR) and three were real moves or a reused symbol.
    integrity      no bar whose high/low fails to contain its own open/close, no
                   negative volume, no out-of-order timestamps, no two bars on one
                   SESSION that disagree (a session stored twice with identical
                   OHLCV is deduplicated, as sanitise does).

    seams          -- the VINEETLAB defect (Rs1,556 -> Rs41 across 350 days) -- are
                   no longer a rejection. The listing-break rule cuts the history at
                   the gap instead, the same repair every existing member gets.

STRATIFICATION. Survivors are bucketed by median traded value and sampled evenly
across the buckets, so the universe spans micro to large rather than drifting to
whatever segment happens to have the cleanest files. Within a bucket the longest
history wins, because history is the one thing that cannot be acquired later.
"""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd

from kitelab import config, frames
from kitelab.config import CLEAN, DATA

MIN_YEARS = 5.0            # 20 quarterly bars for the Q/M/W stack
MIN_BARS = 1_100          # ~4.4 trading years; belt and braces with MIN_YEARS
MIN_TURNOVER = 2_000_000  # Rs20 lakh median daily traded value
BUCKETS = [(0, 1e7, "micro  < Rs1cr"), (1e7, 5e7, "small  Rs1-5cr"),
           (5e7, 2.5e8, "mid    Rs5-25cr"), (2.5e8, float("inf"), "large  > Rs25cr")]
# A close-to-close drop this large on a day the market did not crash is a
# demerger until shown otherwise. Every entry in config.DEMERGERS is between
# -25% and -56% close-to-close; the smallest, VEDL, is -32.9%.
DEMERGER_DROP = 0.30


def market_wide_days() -> pd.DatetimeIndex:
    """Crash days, measured on the current universe's cleaned daily closes.

    A candidate halving on one of these (UNITECH on 2008-10-24, when half the
    universe fell over 8%) is not a corporate action. Read once per run.
    """
    closes = {}
    for symbol in config.load().merged:
        try:
            day = frames.daily(symbol)
        except SystemExit:
            continue
        closes[symbol] = day.set_index("ts")["close"]
    return frames.market_wide_days(closes)


def ordinary_equities() -> pd.DataFrame:
    """Every ordinary NSE equity in the newest instruments dump."""
    dumps = sorted(DATA.glob("instruments_NSE_*.parquet"))
    if not dumps:
        raise SystemExit("No instruments_NSE_*.parquet -- run a backfill first, "
                         "which caches the daily instrument dump.")
    d = pd.read_parquet(dumps[-1])
    eq = d[(d.segment == "NSE") & (d.instrument_type == "EQ")].copy()

    def ordinary(s: str) -> bool:
        # -GB: Sovereign Gold Bonds. Missing until 2026-09-04, when five of them
        # (SGBJUL28IV-GB and siblings) passed the quality gate as equities in the
        # 1004-stock widening -- a bond barely moves, so the risk-based sizer
        # computed a near-zero risk_taken against one and produced a 1164R trade
        # that alone pushed a bootstrap 5th-percentile CAGR into the hundreds of
        # millions of percent. See kitelab.config.EXCLUDED for the five names.
        if re.search(r"-SG$|-GS$|-GB$|^\d", s):        # government securities, bonds
            return False
        if re.search(r"-SM$|-ST$|-RE$|-BE$|-BZ$|-N\d$|-IV$|-PP$", s):
            return False                              # SME, rights, T2T, NCDs
        if "ETF" in s or s.endswith("BEES"):
            return False
        return True

    eq = eq[eq.tradingsymbol.map(ordinary) & (eq.lot_size == 1)]
    return eq.drop_duplicates("tradingsymbol").reset_index(drop=True), dumps[-1]


def assess(symbol: str, market_wide=None) -> dict | None:
    """Every quality check, on the daily file. None if there is no file yet.

    `market_wide` is the index of crash days (market_wide_days()); a drop on
    one of them is not read as a demerger. None means no such exemption.
    """
    # RAW, not CLEAN. A candidate has only ever been fetched -- clean_data
    # processes the WORKING SET, which by definition a candidate is not in
    # yet, so its cleaned copy does not exist. Screening is triage of raw
    # arrivals, the same category as data_audit, and reading CLEAN here would
    # have found nothing and rejected every candidate for want of history.
    # The two rules sanitise() applies to daily files -- one bar per session,
    # history from the last break -- are applied here by hand for the same
    # reason, so the candidate is measured as it would be traded.
    p = DATA / f"{symbol}_day.parquet"
    if not p.exists():
        return None
    try:
        d = pd.read_parquet(p, columns=["ts", "open", "high", "low", "close", "volume"])
    except Exception as exc:
        return {"symbol": symbol, "reject": f"unreadable ({type(exc).__name__})"}
    if d.empty:
        return {"symbol": symbol, "reject": "empty file"}

    out = {"symbol": symbol, "bars": len(d), "reject": ""}

    def fail(why: str) -> dict:
        out["reject"] = why
        return out

    ts = pd.to_datetime(d["ts"])
    if (ts.diff() < pd.Timedelta(0)).any():
        return fail("timestamps out of order")
    session = ts.dt.normalize()
    doubled = session.duplicated(keep="last")
    if doubled.any():
        # Identical twins are the fetch.py stamp defect and are dropped, as on
        # load. Twins that DISAGREE are two different bars claiming one date.
        conflicting = d.assign(_s=session).groupby("_s")["close"].nunique() > 1
        if conflicting.any():
            return fail("two bars on one session with different closes")
        d = d.loc[~doubled].reset_index(drop=True)
        session = session[~doubled].reset_index(drop=True)
    out["duplicate_sessions"] = int(doubled.sum())

    o, h, l, c = (d[k].to_numpy(float) for k in ["open", "high", "low", "close"])
    v = d["volume"].to_numpy(float)
    traded = v > 0
    if not traded.any():
        return fail("no traded bar at all")
    if (v < 0).any():
        return fail("negative volume")

    # History is what survives the last listing break, demerger ex-date or
    # configured start -- the same cut frames.sanitise() makes on load.
    found = frames.history_start(symbol, session)
    start = found[0] if found else session.iloc[0]
    out["history_from"] = str(start.date())
    out["restarted_by"] = found[1] if found else ""
    since = (session >= start).to_numpy()
    bars = int(since.sum())
    years = (session.iloc[-1] - start).days / 365.25
    out["years"] = round(years, 1)
    out["bars"] = bars
    turnover = float(np.median((c * v)[traded & since]))
    out["turnover"] = turnover

    if bars < MIN_BARS or years < MIN_YEARS:
        cut = f" since {start.date()} ({out['restarted_by']})" if found else ""
        return fail(f"history {years:.1f}y / {bars} bars{cut} < {MIN_YEARS}y")
    if turnover < MIN_TURNOVER:
        return fail(f"turnover Rs{turnover:,.0f} < Rs{MIN_TURNOVER:,}")

    pos = (h > 0) & (l > 0) & (o > 0) & (c > 0) & since
    if int((pos & ((h < l) | (h < np.maximum(o, c) - 1e-9)
                   | (l > np.minimum(o, c) + 1e-9))).sum()) > 5:
        return fail("high/low does not contain open/close, repeatedly")

    first = int((traded & since).argmax()) - int(since.argmax())
    if first > 20:
        return fail(f"{first} invented bars before the first real trade")

    keep = traded & since
    t = session[keep].reset_index(drop=True)
    cc = pd.Series(c[keep]).reset_index(drop=True)
    ratio = cc.to_numpy()[1:] / np.where(cc.to_numpy()[:-1] == 0, np.nan, cc.to_numpy()[:-1])
    drops = ratio < 1 - DEMERGER_DROP
    if market_wide is not None and len(market_wide):
        drops &= ~t.iloc[1:].isin(market_wide).to_numpy()
    if int(np.nansum(drops)):
        i = int(np.nanargmin(np.where(drops, ratio, np.nan)))
        return fail(f"suspected demerger: {ratio[i] - 1:+.1%} on {t.iloc[i + 1].date()}"
                    f" -- verify, then add the ex-date to config.DEMERGERS")
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
    held = set(cfg.merged) | set(config.EXCLUDED)
    eq, dump = ordinary_equities()
    print(f"\n  instruments dump: {dump.name}")
    print(f"  ordinary NSE equities: {len(eq):,}   already held: {len(held)}")

    if args.candidates:
        wanted = sorted(set(eq.tradingsymbol) - held)
        target = CLEAN / "candidates.txt"
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
    crashes = market_wide_days()
    print(f"  market-wide crash days on the current universe: {len(crashes)}")
    rows, missing = [], 0
    for s in sorted(set(eq.tradingsymbol) - held):
        a = assess(s, crashes)
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

    need = max(0, args.target - len(cfg.merged))
    by_bucket: dict[str, list] = {}
    for r in passed:
        by_bucket.setdefault(bucket_of(r["turnover"]), []).append(r)
    for v in by_bucket.values():
        v.sort(key=lambda r: -r["years"])      # history is the thing you cannot buy later

    print(f"\n  survivors by liquidity bucket (universe {len(cfg.merged)} "
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

    target = CLEAN / "accepted.txt"
    target.write_text(
        f"# {len(chosen)} stocks that passed every quality check, sampled evenly\n"
        f"# across liquidity buckets, longest history first within each.\n"
        "# Fetch them properly, then add them to [universe] unseen in "
        "config.local.toml (the batch list; the four lists merge into one universe):\n"
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

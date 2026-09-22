"""Buy-and-hold on today's Nifty 500 vs a list a 2018 trader could have built.

    python3 -m scripts.n500_hold                 # seconds; writes a dated CSV

WHAT THIS MEASURES, AND WHY IT COMES BEFORE ANY STRATEGY RUN. The second
report is about today's Nifty 500 constituents. NSE re-cuts that list every
March and September, so running it back to 2018 buys stocks that earned
inclusion by rising. That is membership bias, and the honest way to report it
is as a NUMBER, not a paragraph of disclaimer (see kitelab-fix-not-disclose).

The number is the gap between two equal-weight buy-and-hold portfolios over
the same calendar:

  TODAY   the 496 current constituents that have usable data
          (data/keep/nifty500.json "study"; see scripts.nifty500_list for the
          four names dropped and why)
  PIT     the top 500 of cfg.merged by median daily traded value using ONLY
          bars before the start year -- point-in-time, no look-ahead, the
          closest thing in this data to "the 500 biggest names as of then".
          A trader standing on 1 Jan 2018 could have written this list down.

TODAY minus PIT is the inclusion premium. It is charged to buy-and-hold here,
where no strategy is involved and nothing can be blamed on a rule.

A third row, `all` (every one of cfg.merged), is the board's own benchmark and
is the SELF-CHECK: it must reproduce validation_summary.hold_cagr_by_scenario
in the built dashboard.json to the digit, or this script is measuring
something other than the board's benchmark and says so instead of printing.

A fourth row is the real Nifty 500 TOTAL RETURN INDEX (dividends reinvested),
which is CAP-weighted -- big companies count more. Equal weight vs cap weight
is a second, separate gap, and the doc has to separate the two or the two
reports read as contradicting each other.

Reads:  data/keep/nifty500.json                    the study list
        /data/clean/kitelab/*_day.parquet          via kitelab.frames
        /data/clean/kitelab/dashboard.json         the self-check
        /data/raw/mf/cache/tri_nifty_500.parquet   READ-ONLY, the real index
Writes: output/measurements/n500_hold_<date>.csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config, frames, validation
from kitelab.config import CLEAN
from scripts.wf_daily import START_DEFAULT

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "measurements"
TRI = Path("/data/raw/mf/cache/tri_nifty_500.parquet")

# The point-in-time list is cut to the same size as the index it stands in for.
PIT_SIZE = 500


def turnover_before(members, asof):
    """Median daily traded value per symbol, using only bars before `asof`.

    scripts.wf_daily.buckets' arithmetic, kept as one number per symbol
    instead of four sets. Point-in-time by construction: a bar on or after the
    cut is never read, so nothing a 1 Jan trader could not know enters the
    ranking.
    """
    cut = pd.Timestamp(f"{asof}-01-01")
    turn = {}
    for sym in members:
        try:
            d = frames.daily(sym)
        except SystemExit:
            continue
        d = d[d["ts"] < cut]
        v = (d["close"] * d["volume"]).to_numpy(float)
        v = v[v > 0]
        if len(v):
            turn[sym] = float(np.median(v))
    return turn


def bars_in_span(sym, start_year):
    try:
        d = frames.daily(sym)
    except SystemExit:
        return 0
    return int((d["ts"] >= pd.Timestamp(f"{start_year}-01-01")).sum())


def tri_cagr(start_year, end_ts):
    """CAGR of the real Nifty 500 TRI over the same calendar, or None."""
    if not TRI.exists():
        return None, None, None
    d = pd.read_parquet(TRI)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"] >= pd.Timestamp(f"{start_year}-01-01")) & (d["date"] <= end_ts)]
    if len(d) < 2:
        return None, None, None
    yrs = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    cagr = 100 * ((d["tri"].iloc[-1] / d["tri"].iloc[0]) ** (1 / yrs) - 1)
    return cagr, d["date"].iloc[0], d["date"].iloc[-1]


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=START_DEFAULT,
                    help=f"start year (default {START_DEFAULT}, the board's)")
    args = ap.parse_args()
    t0 = time.time()
    year = args.start

    blob = json.loads((ROOT / "data" / "keep" / "nifty500.json").read_text())
    study = blob["study"]
    print(f"read data/keep/nifty500.json: {blob['n_constituents']} constituents, "
          f"{len(study)} in the study")
    print(f"  dropped: {blob['study_dropped']['no_price_file']} (no price file), "
          f"{sorted(blob['study_dropped']['data_defect'])} (data defect)")

    cfg = config.load()
    members = list(cfg.merged)
    print(f"board universe (cfg.merged): {len(members)} stocks")

    print(f"\nranking cfg.merged on turnover before {year}-01-01 "
          f"(point-in-time -- no bar on or after the cut is read)")
    turn = turnover_before(members, year)
    print(f"  {len(turn)} of {len(members)} had a positive-turnover bar before the cut "
          f"({len(members) - len(turn)} had not listed yet)")
    ranked = sorted(turn, key=turn.get, reverse=True)
    pit = ranked[:PIT_SIZE]
    print(f"  PIT list = top {len(pit)} by median daily traded value; "
          f"cut-off Rs{turn[pit[-1]]:,.0f}/day")

    overlap = len(set(pit) & set(study))
    print(f"  overlap with today's Nifty 500 study list: {overlap} names "
          f"({overlap/len(pit):.1%} of PIT, {overlap/len(study):.1%} of TODAY)")

    rows = []
    end_ts = None
    for name, mem in (("TODAY  (Nifty 500 as of 2026-09)", study),
                      (f"PIT    (top {PIT_SIZE} by pre-{year} turnover)", pit),
                      ("ALL    (cfg.merged, the board's benchmark)", members)):
        cagr = validation.buy_and_hold(mem, start_year=year)
        thin = [s for s in mem if bars_in_span(s, year) < validation.MIN_HOLD_BARS]
        rows.append({"basket": name, "n_named": len(mem),
                     "n_too_thin": len(thin), "n_held": len(mem) - len(thin),
                     "cagr": cagr,
                     "too_thin_names": ";".join(sorted(thin))})
        print(f"\n  {name}")
        print(f"    named {len(mem)}, held {len(mem)-len(thin)}, "
              f"skipped {len(thin)} with under {validation.MIN_HOLD_BARS} bars "
              f"since {year}")
        if thin:
            print(f"      skipped: {', '.join(sorted(thin))}")
        print(f"    equal-weight buy-and-hold CAGR from {year}: {cagr:.2f}%/yr")

    today, pit_c, allc = (r["cagr"] for r in rows)

    # ---- self-check: ALL must reproduce the built board's own benchmark ----
    dash = CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"no dashboard.json at {dash} -- cannot self-check")
    payload = json.loads(dash.read_bytes())
    want = payload["validation_summary"]["hold_cagr_by_scenario"].get(f"all|{year}")
    if want is None:
        raise SystemExit(f"the built board has no hold CAGR for all|{year}")
    if round(allc, 1) != round(want, 1):
        raise SystemExit(
            f"\n  SELF-CHECK FAILED: hold on cfg.merged from {year} is "
            f"{allc:.2f}% here and {want}% on the built board "
            f"({payload['built']}).\n  This script is not measuring the board's "
            f"benchmark. Nothing was written.\n")
    print(f"\n  SELF-CHECK PASSED: all|{year} hold = {allc:.2f}% here, "
          f"{want}% on the board built {payload['built']}")

    # ---- was it the recent listings? the question everyone asks first ----
    # The hold benchmark's convention is LATE MONEY, NOT FREE MONEY: a member
    # that has not listed yet holds Rs1 in cash earning nothing. So a basket
    # full of recent flotations could be dragged down by idle cash, and the
    # inclusion premium would be understated for a reason that has nothing to
    # do with membership. Measured here rather than argued about: rerun TODAY
    # on only the study names that were ALREADY TRADING before the cut.
    early = sorted(turnover_before(study, year))
    early_cagr = validation.buy_and_hold(early, start_year=year)
    thin_e = [s for s in early if bars_in_span(s, year) < validation.MIN_HOLD_BARS]
    rows.append({"basket": f"TODAY-EARLY (study names trading before {year})",
                 "n_named": len(early), "n_too_thin": len(thin_e),
                 "n_held": len(early) - len(thin_e), "cagr": early_cagr,
                 "too_thin_names": ";".join(sorted(thin_e))})
    print(f"\n  TODAY-EARLY (of {len(study)} study names, {len(early)} were "
          f"already trading before {year}; {len(study)-len(early)} listed later)")
    print(f"    equal-weight buy-and-hold CAGR from {year}: {early_cagr:.2f}%/yr")
    print(f"    dropping the later listings moves TODAY {today:.2f} -> "
          f"{early_cagr:.2f}, i.e. {early_cagr - today:+.2f} points")

    # ---- the real index, cap-weighted ----
    closes = frames.daily(members[0])
    end_ts = pd.Timestamp(closes["ts"].max())
    tri, t_from, t_to = tri_cagr(year, end_ts)
    if tri is not None:
        rows.append({"basket": "TRI    (real Nifty 500 Total Return Index)",
                     "n_named": np.nan, "n_too_thin": np.nan,
                     "n_held": np.nan, "cagr": tri, "too_thin_names": ""})
        print("\n  TRI    (real Nifty 500 Total Return Index, CAP-weighted)")
        print(f"    {t_from.date()} to {t_to.date()}: {tri:.2f}%/yr")

    print(f"\n{'='*70}\nTHE TWO GAPS THE DOC HAS TO EXPLAIN\n{'='*70}")
    print("  inclusion premium (membership bias, equal weight both sides)")
    print(f"    TODAY {today:6.2f}  -  PIT {pit_c:6.2f}  =  "
          f"{today - pit_c:+.2f} CAGR points a year")
    if tri is not None:
        print("  equal weight vs cap weight (same index, same calendar)")
        print(f"    TODAY {today:6.2f}  -  TRI {tri:6.2f}  =  "
              f"{today - tri:+.2f} CAGR points a year")

    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df["start_year"] = year
    df["inclusion_premium"] = round(today - pit_c, 4)
    # Provenance the report reads rather than retypes: which board this was
    # checked against, and what the check agreed on.
    df["pit_overlap_with_today"] = overlap
    df["n_study"] = len(study)
    df["selfcheck_board_hold"] = want
    df["board_built"] = payload["built"]
    df["min_hold_bars"] = validation.MIN_HOLD_BARS
    path = OUT / f"n500_hold_{year}_{time.strftime('%Y-%m-%d')}.csv"
    df.to_csv(path, index=False)
    print(f"\n  wrote {path}")
    print(f"  took {time.time()-t0:.1f}s")

    pit_path = OUT / f"n500_pit_list_{year}_{time.strftime('%Y-%m-%d')}.csv"
    pd.Series(pit).to_csv(pit_path, index=False, header=["symbol"])
    print(f"  wrote the PIT list -> {pit_path}")


if __name__ == "__main__":
    main()

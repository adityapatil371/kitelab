"""Read NSE's Nifty 500 constituent CSV and reconcile it against our universe.

    python3 -m scripts.nifty500_list                    # default search paths
    python3 -m scripts.nifty500_list --csv <path>

WHAT THIS IS FOR. The board trades `cfg.merged` -- 1,000 screened NSE equities
picked by liquidity, not by index membership. A Nifty 500 study needs the
actual membership list, and NSE is the only publisher of it. The container
firewall blocks nseindia.com, so the CSV is downloaded by hand and read here.

WHAT IT DELIBERATELY DOES NOT DO. It does not pretend the list is historical.
NSE rebalances the Nifty 500 every March and September; this file is TODAY's
membership. Anything backtested on it from 2018 is buying stocks that earned
their way into the index by going up, which is look-ahead bias and inflates
every number. This script therefore writes the list AND the facts needed to
size that bias -- it never writes the list alone.

Reads:  data/keep/ind_nifty500list.csv   (or --csv), the hand-downloaded file
        data/keep/universe.json          (cfg.merged, 1,000 symbols)
        /data/clean/kitelab/*_day.parquet via kitelab.frames, for coverage
Writes: data/keep/nifty500.json          the reconciled list + coverage report
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP = ROOT / "data" / "keep"
SEARCH = [KEEP / "ind_nifty500list.csv",
          Path("/data/raw/kitelab/ind_nifty500list.csv"),
          KEEP / "nifty500.csv"]


# ---------------------------------------------------------- study universe ----
# THE USER'S RULE, 2026-09-22: "remove these genuinely missing and bad data, do
# all the rest no matter the short period of data." So the Nifty 500 study
# universe drops exactly two things and nothing else:
#
#   1. a constituent with no price file at all, anywhere -- nothing to test;
#   2. a constituent whose price history carries a KNOWN DEFECT, named below.
#
# A short history is NOT a reason to drop a name. That is a deliberate
# departure from `config.EXCLUDED`, nine of whose thirteen Nifty 500 entries
# are the 5-year history gate: a stock that listed in 2024 contributes nothing
# to a 2018-start backtest but is not bad data, and excluding it would delete
# the very cohort the membership-bias measurement is about.
#
# PNB is KEPT. Its config.EXCLUDED note says in terms that it has no data
# defect -- the +46.2% on 2017-10-25 is the real PSU recapitalisation
# announcement, which the split gate misread -- and that putting it back is
# the owner's call. This is that call, for this study only; the board's own
# universe is untouched.
DATA_DEFECT = {
    "CGPOWER": "unexplained single-day move over 60% (config.EXCLUDED); an "
               "unadjusted corporate action would reprice every bar before it",
    "JMFINANCIL": "191 zero-volume padding bars at Rs0.14 before the first "
                  "real trade at Rs30.99; they seeded the 20-day EMA at 0.14 "
                  "and manufactured a buy on the first real session",
}


def find_csv(explicit):
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"no such file: {p}")
        return p
    for p in SEARCH:
        if p.exists():
            return p
    raise SystemExit(
        "Nifty 500 constituent CSV not found. Looked in:\n  "
        + "\n  ".join(str(p) for p in SEARCH)
        + "\n\nDownload it on the Mac (the container firewall blocks NSE):\n"
          "  https://www.nseindia.com/products-services/indices-nifty500-index\n"
          "  -> 'Index Constituent' -> ind_nifty500list.csv\n"
          f"then save it as {SEARCH[0]}")


def read_list(path):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
    if not rows:
        raise SystemExit(f"{path} has no data rows")
    cols = list(rows[0])
    print(f"  read {path}")
    print(f"    {len(rows)} rows x {len(cols)} columns: {cols}")
    for r in rows[:3]:
        print(f"    {r}")
    sym_col = next((c for c in cols if c.strip().lower() == "symbol"), None)
    if sym_col is None:
        raise SystemExit(
            f"no 'Symbol' column in {path}; columns are {cols}. "
            "This is probably not NSE's ind_nifty500list.csv.")
    isin_col = next((c for c in cols if "isin" in c.strip().lower()), None)
    ser_col = next((c for c in cols if c.strip().lower() == "series"), None)

    rows = [r for r in rows if r[sym_col].strip()]
    # NSE leaves demerger placeholders in the file: a row whose ISIN starts
    # with DUM is a dummy line, not a tradable company. Drop it by ISIN, not
    # by name, so a real company called DUMMY-something would survive.
    def placeholder(r):
        return bool(isin_col) and r[isin_col].strip().upper().startswith("DUM")
    dropped = [r for r in rows if placeholder(r)]
    rows = [r for r in rows if not placeholder(r)]
    print(f"    dropped {len(dropped)} ISIN-DUM placeholder row(s): "
          + (", ".join(f"{r[sym_col].strip()} ({r[isin_col].strip()})"
                       for r in dropped) or "none"))

    if ser_col:
        from collections import Counter
        ser = Counter(r[ser_col].strip().upper() for r in rows)
        print(f"    series: {dict(sorted(ser.items()))}")
        odd = sorted(r[sym_col].strip().upper() for r in rows
                     if r[ser_col].strip().upper() != "EQ")
        # BE = trade-for-trade settlement: no intraday netting, 100% margin.
        # Kept -- our board trades daily closes -- but named, because their
        # real-world fills are worse than the rest of the list.
        print(f"    non-EQ series ({len(odd)}), kept but flagged: "
              + (", ".join(odd) or "none"))
    else:
        odd = []

    syms = [r[sym_col].strip().upper() for r in rows]
    print(f"    symbols: {len(syms)} before dedupe", end="")
    syms = sorted(set(syms))
    print(f", {len(syms)} after")
    if not 400 <= len(syms) <= 520:
        raise SystemExit(
            f"expected ~500 symbols, got {len(syms)} -- wrong file?")
    name_col = next((c for c in cols if "company" in c.strip().lower()), None)
    ind_col = next((c for c in cols if "industry" in c.strip().lower()), None)
    meta = {r[sym_col].strip().upper(): {
                "name": r.get(name_col, "").strip() if name_col else "",
                "industry": r.get(ind_col, "").strip() if ind_col else "",
                "series": r.get(ser_col, "").strip() if ser_col else ""}
            for r in rows}
    return syms, meta, {"dropped": [r[sym_col].strip().upper() for r in dropped],
                        "non_eq": odd}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    print("Nifty 500 constituents, reconciled against the board's universe")
    syms, meta, flags = read_list(find_csv(args.csv))

    uni_path = KEEP / "universe.json"
    uni_blob = json.load(open(uni_path))
    universe = set(uni_blob["universe"])
    excluded = set(uni_blob.get("excluded", []))
    print(f"  read {uni_path}: universe {len(universe)}, excluded {len(excluded)}")

    from kitelab.config import CLEAN
    on_disk = {p.name[:-len("_day.parquet")]
               for p in CLEAN.glob("*_day.parquet")}
    print(f"  cleaned candle files on disk: {len(on_disk)}")

    tradable = sorted(s for s in syms if s in universe)
    missing_excluded = sorted(s for s in syms if s in excluded)
    missing_nodata = sorted(s for s in syms
                            if s not in universe and s not in excluded
                            and s not in on_disk)
    missing_unscreened = sorted(s for s in syms
                                if s not in universe and s not in excluded
                                and s in on_disk)
    print(f"  of {len(syms)} constituents:")
    print(f"    {len(tradable):4d} in the board universe  -> tradable")
    print(f"    {len(missing_unscreened):4d} have candles but failed the screen")
    print(f"    {len(missing_excluded):4d} on config.EXCLUDED")
    print(f"    {len(missing_nodata):4d} no candle file at all")
    assert (len(tradable) + len(missing_unscreened) + len(missing_excluded)
            + len(missing_nodata) == len(syms)), "the four groups must partition"

    cover = len(tradable) / len(syms)
    print(f"  overlap with the board's traded universe: {cover:.1%} "
          "(not the study's coverage -- see below)")

    # ---- the study universe: everything except no-data and bad-data ----
    no_data = sorted(s for s in syms if s not in on_disk)
    defect = sorted(s for s in syms if s in DATA_DEFECT)
    study = sorted(s for s in syms if s not in no_data and s not in defect)
    print("  study universe (the user's rule, 2026-09-22):")
    print(f"    {len(syms):4d} constituents")
    print(f"    {-len(no_data):4d} no price file anywhere: {', '.join(no_data) or 'none'}")
    print(f"    {-len(defect):4d} known data defect: {', '.join(defect) or 'none'}")
    print(f"    {len(study):4d} IN THE STUDY  ({len(study)/len(syms):.1%} of the index)")
    if len(study) / len(syms) < 0.80:
        print("    WARNING: under 80% coverage. A study on this subset is not "
              "a study of the Nifty 500 -- report the gap, do not paper over it.")
    assert len(study) + len(no_data) + len(defect) == len(syms)
    short = sorted(s for s in study if s not in universe)
    print(f"    of those, {len(short)} are NOT in the board's own 1,000-stock "
          f"universe -- kept anyway, mostly short history")

    out = {
        "written": datetime.now().isoformat(timespec="seconds"),
        "source": str(find_csv(args.csv)),
        "caveat": ("TODAY's membership, not point-in-time. NSE rebalances the "
                   "Nifty 500 each March and September. Backtesting this list "
                   "over a past period selects stocks that earned inclusion by "
                   "rising, which inflates returns. Quote the measured "
                   "inclusion premium alongside every number built on it."),
        "n_constituents": len(syms),
        "dropped_placeholders": flags["dropped"],
        "non_eq_series": flags["non_eq"],
        "constituents": syms,
        "tradable": tradable,
        "not_tradable": {"failed_screen": missing_unscreened,
                         "excluded": missing_excluded,
                         "no_candles": missing_nodata},
        "coverage": round(cover, 4),
        "study": study,
        "study_dropped": {"no_price_file": no_data,
                          "data_defect": {k: DATA_DEFECT[k] for k in defect}},
        "study_outside_board_universe": short,
        "meta": meta,
    }
    dest = KEEP / "nifty500.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"  wrote {dest} ({dest.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

"""Every data-quality check we know how to make, over every parquet file.

    python -m scripts.data_audit              # summary
    python -m scripts.data_audit --detail     # every offending symbol, listed

Checks the RAW files, before frames.sanitise() touches anything, because the point
is to see what Kite actually delivered. Where sanitise already repairs something the
report says so, so a loud row is not mistaken for a live problem.

Written 2026-09-01 after the fix pass found four separate families of bad data that
the code had been trading on: zero-volume bars carrying invented prices, padding in
front of a listing, price seams across long gaps, and histories too short to support
the indicators computed from them.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config
from kitelab.config import DATA

OHLC = ["open", "high", "low", "close"]
SESSION_OPEN = pd.Timedelta(hours=9, minutes=15)
SESSION_CLOSE = pd.Timedelta(hours=15, minutes=35)   # post-CAS close


class Findings:
    """Accumulates (check, symbol, interval, count, note) rows."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def add(self, check, symbol, interval, count, note="") -> None:
        if count:
            self.rows.append((check, symbol, interval, int(count), note))

    def by_check(self) -> dict[str, list[tuple]]:
        out: dict[str, list[tuple]] = defaultdict(list)
        for r in self.rows:
            out[r[0]].append(r)
        return out


def check_frame(f: Findings, symbol: str, interval: str, d: pd.DataFrame) -> None:
    n = len(d)
    if n == 0:
        f.add("empty file", symbol, interval, 1)
        return

    missing = [c for c in [*OHLC, "ts", "volume"] if c not in d.columns]
    if missing:
        f.add("missing columns", symbol, interval, len(missing), ",".join(missing))
        return

    ts = pd.to_datetime(d["ts"])
    o, h, l, c = (d[k].to_numpy(dtype=float) for k in OHLC)
    v = d["volume"].to_numpy(dtype=float)

    # ---- structure ----
    f.add("duplicate timestamps", symbol, interval, int(ts.duplicated().sum()))
    f.add("timestamps out of order", symbol, interval, int((ts.diff() < pd.Timedelta(0)).sum()))
    f.add("NaN in OHLC", symbol, interval, int(np.isnan(np.c_[o, h, l, c]).any(axis=1).sum()))
    f.add("NaN in volume", symbol, interval, int(np.isnan(v).sum()))

    # ---- OHLC integrity ----
    f.add("non-positive price (sanitise repairs)", symbol, interval,
          int((np.c_[o, h, l, c] <= 0).any(axis=1).sum()))
    pos = ~np.isnan(h) & ~np.isnan(l)
    f.add("high < low", symbol, interval, int((pos & (h < l)).sum()))
    f.add("high below open or close", symbol, interval,
          int((pos & (h < np.maximum(o, c) - 1e-9)).sum()))
    f.add("low above open or close", symbol, interval,
          int((pos & (l > np.minimum(o, c) + 1e-9)).sum()))
    f.add("negative volume", symbol, interval, int((v < 0).sum()))

    # ---- suspicious values ----
    frozen = (o == h) & (h == l) & (l == c) & (v > 0)
    f.add("frozen bar, traded (o=h=l=c, volume>0)", symbol, interval, int(frozen.sum()))
    zero_vol = v == 0
    f.add("zero-volume bar", symbol, interval, int(zero_vol.sum()))

    # zero-volume bars whose price is far off the traded tape -- the VINEETLAB shape
    if zero_vol.any() and (~zero_vol).any():
        ref = (pd.Series(c).where(~zero_vol)
               .rolling(41, center=True, min_periods=5).median().ffill().bfill().to_numpy())
        ok = ~np.isnan(ref) & (ref > 0)
        off = zero_vol & ok & ((c < 0.2 * ref) | (c > ref / 0.2))
        f.add("zero-volume bar, price far off the tape", symbol, interval, int(off.sum()))

    # leading / trailing padding: no trade ever happened on these bars
    if (v > 0).any():
        first = int((v > 0).argmax())
        last = n - 1 - int((v > 0)[::-1].argmax())
        f.add("padding before the first real trade", symbol, interval, first)
        f.add("padding after the last real trade", symbol, interval, n - 1 - last)
    else:
        f.add("no traded bar in the whole file", symbol, interval, 1)

    # extreme single-bar move between TRADED bars
    traded = v > 0
    tc = c[traded]
    if len(tc) > 2:
        r = np.abs(np.diff(tc) / np.where(tc[:-1] == 0, np.nan, tc[:-1]))
        limit = 0.5 if interval == "day" else 0.35
        f.add(f"single-bar move over {limit:.0%}", symbol, interval,
              int(np.nansum(r > limit)), f"max {np.nanmax(r):.0%}" if len(r) else "")

    # ---- calendar ----
    f.add("weekend bar", symbol, interval, int((ts.dt.dayofweek >= 5).sum()))
    f.add("future-dated bar", symbol, interval, int((ts > pd.Timestamp.now()).sum()))
    if interval != "day":
        tod = ts - ts.dt.normalize()
        f.add("bar outside 09:15-15:35", symbol, interval,
              int(((tod < SESSION_OPEN) | (tod > SESSION_CLOSE)).sum()))

    # long gaps between traded bars, and a price level shift across one
    if interval == "day" and traded.sum() > 50:
        t = ts[traded].reset_index(drop=True)
        cc = pd.Series(c[traded])
        gaps = t.diff().dt.days
        seams = 0
        for i in gaps[gaps > 180].index:
            before = cc.iloc[max(0, i - 5):i].median()
            after = cc.iloc[i:i + 5].median()
            if before and not np.isnan(after):
                ratio = after / before
                if not (0.4 <= ratio <= 2.5):
                    seams += 1
        f.add("gap over 180 days", symbol, interval, int((gaps > 180).sum()))
        f.add("PRICE SEAM across a long gap", symbol, interval, seams)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", action="store_true", help="list every offending symbol")
    ap.add_argument("--interval", default="all", choices=["all", "day", "15minute"])
    args = ap.parse_args()

    cfg = config.load()
    universe = set(cfg.all_symbols)
    excluded = set(config.EXCLUDED)
    f = Findings()

    files = sorted(DATA.glob("*.parquet"))
    print(f"\n  {len(files)} parquet files in {DATA}")

    # ---- coverage: universe vs files ----
    have_day = {p.stem[:-4] for p in files if p.stem.endswith("_day")}
    have_15 = {p.stem[:-9] for p in files if p.stem.endswith("_15minute")}
    for s in sorted(universe - have_day):
        f.add("universe symbol with NO daily file", s, "day", 1)
    for s in sorted(universe - have_15):
        f.add("universe symbol with NO 15-minute file", s, "15minute", 1)
    orphans = sorted((have_day - universe) - excluded)
    print(f"  universe {len(universe)} symbols · daily files {len(have_day)} · "
          f"15-minute files {len(have_15)}")
    if orphans:
        print(f"  {len(orphans)} daily files for symbols NOT in the universe "
              f"(and not excluded): {', '.join(orphans[:12])}"
              + (" ..." if len(orphans) > 12 else ""))

    scanned = 0
    for p in files:
        stem = p.stem
        if stem.endswith("_day"):
            symbol, interval = stem[:-4], "day"
        elif stem.endswith("_15minute"):
            symbol, interval = stem[:-9], "15minute"
        elif stem.endswith("_30minute"):
            symbol, interval = stem[:-9], "30minute"
        else:
            continue
        if args.interval != "all" and interval != args.interval:
            continue
        try:
            d = pd.read_parquet(p)
        except Exception as exc:
            f.add("UNREADABLE FILE", symbol, interval, 1, str(exc)[:60])
            continue
        scanned += 1
        check_frame(f, symbol, interval, d)

    print(f"  scanned {scanned} files\n")
    print("=" * 78)
    print(f"  {'check':<46s}{'symbols':>9s}{'bars':>11s}")
    print("=" * 78)
    grouped = f.by_check()
    order = sorted(grouped, key=lambda k: -sum(r[3] for r in grouped[k]))
    for check in order:
        rows = grouped[check]
        syms = len({r[1] for r in rows})
        total = sum(r[3] for r in rows)
        print(f"  {check:<46s}{syms:>9,}{total:>11,}")
        if args.detail:
            for _, sym, iv, cnt, note in sorted(rows, key=lambda r: -r[3])[:25]:
                print(f"      {sym:<14s}{iv:<10s}{cnt:>8,}  {note}")
    print("=" * 78)
    if not grouped:
        print("  no findings")


if __name__ == "__main__":
    main()

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

Widened 2026-09-07 after the audit found five more, none of which any check here
would have raised: a listing break traded straight through (ROTO, -53.5R), a last
bar that was a fraction of a session (every file, fetched before the close), a
session stored twice under two stamps (COALINDIA), a series with its moves
amplified several-fold for years (HINDPETRO 2013), and demergers read as crashes
(fourteen of them, now in config.DEMERGERS). Each is a check below. Two of them
need the whole store before they can say anything about one file -- a crash day
is defined by how many stocks fell, and a wild year by the stock's liquidity
bucket -- so those run as a second pass once every daily file has been read.

Universe members are listed before non-members, because a defect in a stock
the board trades on is a published number and a defect in a fetched-but-rejected
candidate is not.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np
import pandas as pd

from kitelab import config, frames
from kitelab.config import DATA
from scripts.screen_universe import bucket_of

OHLC = ["open", "high", "low", "close"]

# Instruments that legitimately break the equity assumptions, so their findings are
# not defects:
#   INDICES     have no volume at all -- every bar reads zero
#   ALWAYS_ON   trades weekends (crypto) or Saturdays (MCX commodity sessions)
INDICES = {"NIFTY 50", "NIFTY BANK"}
ALWAYS_ON = {"BITCOIN", "GOLD", "SILVER", "CRUDEOIL"}

# Checks whose hits are explained rather than wrong. Kept in the report -- a count
# that suddenly moves is still worth seeing -- but listed separately so a big number
# is not mistaken for a big problem.
BENIGN = {
    "zero-volume bar",
    "frozen bar, traded (o=h=l=c, volume>0)",
    "weekend bar",
    "bar outside 09:15-15:35",
    "non-positive price (sanitise repairs)",
    "zero-volume bar, price far off the tape (dropped)",
    "high/low did not contain open/close (repaired)",
    "duplicated session after normalising the stamp (deduped on load)",
    "listing break over 180 days (history restarts on load)",
    "price seam across a long gap (cut by the listing-break rule)",
    "demerger ex-date in config.DEMERGERS (history restarts on load)",
    "history before config.HISTORY_STARTS (dropped on load)",
}
SESSION_OPEN = pd.Timedelta(hours=9, minutes=15)
SESSION_CLOSE = pd.Timedelta(hours=15, minutes=35)   # post-CAS close

# A single-bar move over this between traded bars is listed. Was 50% on daily
# bars and 35% intraday until 2026-09-07; lowered to 25% because every demerger
# in config.DEMERGERS is a 25-56% drop and the old bar let VEDL (-32.9%) and
# SIEMENS (-25.1%) through unlisted.
MOVE_LIMIT = 0.25
# A last bar whose volume is under this fraction of its 60-session median was
# fetched before the session closed. Measured 2026-09-07: 463 of 1,011 files.
PARTIAL_VOLUME = 0.5
PARTIAL_WINDOW = 60
# A calendar year whose daily-return sigma is over this multiple of the median
# sigma for the stock's liquidity bucket. HINDPETRO 2013 is 3.4x, 2008 4.1x; the
# next hits are YESBANK 2020 (3.7x) and IDEA 2020 (3.4x), which were real.
SIGMA_MULTIPLE = 2.5
SIGMA_MIN_RETURNS = 100
# A close-to-close drop over this on a day that was NOT market-wide is a
# demerger until shown otherwise; the smallest in config.DEMERGERS is -32.9%.
DROP_LIMIT = 0.25


class Findings:
    """Accumulates (check, symbol, interval, count, note) rows, plus what the
    second pass needs: each daily file's closes and its pre-2018 turnover."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.closes: dict[str, pd.Series] = {}
        self.turnover: dict[str, float] = {}

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
    if interval == "day":
        session = ts.dt.normalize()
        doubled = int(session.duplicated().sum()) - int(ts.duplicated().sum())
        f.add("duplicated session after normalising the stamp (deduped on load)",
              symbol, interval, doubled,
              ", ".join(str(x.date()) for x in session[session.duplicated()].head(3)))
    f.add("timestamps out of order", symbol, interval, int((ts.diff() < pd.Timedelta(0)).sum()))
    f.add("NaN in OHLC", symbol, interval, int(np.isnan(np.c_[o, h, l, c]).any(axis=1).sum()))
    f.add("NaN in volume", symbol, interval, int(np.isnan(v).sum()))

    # ---- OHLC integrity ----
    f.add("non-positive price (sanitise repairs)", symbol, interval,
          int((np.c_[o, h, l, c] <= 0).any(axis=1).sum()))
    pos = ~np.isnan(h) & ~np.isnan(l)
    f.add("high/low did not contain open/close (repaired)", symbol, interval,
          int((pos & ((h < l) | (h < np.maximum(o, c) - 1e-9)
                      | (l > np.minimum(o, c) + 1e-9))).sum()))
    f.add("negative volume", symbol, interval, int((v < 0).sum()))

    # ---- suspicious values ----
    frozen = (o == h) & (h == l) & (l == c) & (v > 0)
    f.add("frozen bar, traded (o=h=l=c, volume>0)", symbol, interval, int(frozen.sum()))
    if symbol in INDICES:
        if interval == "day":
            f.closes[symbol] = pd.Series(c, index=ts.dt.normalize())
        return          # an index has no volume; every volume check below is moot
    zero_vol = v == 0
    f.add("zero-volume bar", symbol, interval, int(zero_vol.sum()))

    # zero-volume bars whose price is far off the traded tape -- the VINEETLAB shape
    if zero_vol.any() and (~zero_vol).any():
        ref = (pd.Series(c).where(~zero_vol)
               .rolling(41, center=True, min_periods=5).median().ffill().bfill().to_numpy())
        ok = ~np.isnan(ref) & (ref > 0)
        off = zero_vol & ok & ((c < 0.2 * ref) | (c > ref / 0.2))
        f.add("zero-volume bar, price far off the tape (dropped)", symbol,
              interval, int(off.sum()))

    traded = v > 0

    # leading / trailing padding: no trade ever happened on these bars
    if traded.any():
        first = int(traded.argmax())
        last = n - 1 - int(traded[::-1].argmax())
        f.add("padding before the first real trade", symbol, interval, first)
        f.add("padding after the last real trade", symbol, interval, n - 1 - last)
    else:
        f.add("no traded bar in the whole file", symbol, interval, 1)

    # The "SUSPECTED UNADJUSTED SPLIT / BONUS" check that used to sit here was
    # retired on 2026-09-07. Kite adjusts splits and bonuses at serve time (HAL
    # 2:1 2023-07-27/28 reads 1926.50 -> 1964.50; NESTLEIND 1:10 2024-01-05
    # 1355.8 -> 1333.2 with volume adjusted too), and of the four stocks the
    # check excluded, none was a split. What Kite does NOT adjust is demergers;
    # those are the second-pass DROP check below and config.DEMERGERS.

    # extreme single-bar move between TRADED bars
    tc = c[traded]
    if len(tc) > 2:
        r = np.abs(np.diff(tc) / np.where(tc[:-1] == 0, np.nan, tc[:-1]))
        f.add(f"single-bar move over {MOVE_LIMIT:.0%}", symbol, interval,
              int(np.nansum(r > MOVE_LIMIT)), f"max {np.nanmax(r):.0%}" if len(r) else "")

    # the last bar was captured before the session closed
    if interval == "day" and n > PARTIAL_WINDOW:
        median = float(np.median(v[-PARTIAL_WINDOW - 1:-1]))
        if median > 0 and v[-1] < PARTIAL_VOLUME * median:
            f.add("last bar looks PARTIAL (volume under 0.5x its 60-session median)",
                  symbol, interval, 1, f"{ts.iloc[-1].date()} at {v[-1] / median:.2f}x")

    # ---- calendar ----
    if symbol not in ALWAYS_ON:
        f.add("weekend bar", symbol, interval, int((ts.dt.dayofweek >= 5).sum()))
    # Bars are stamped in IST. Comparing them against a UTC clock would miss a
    # future-dated bar for five and a half hours after it appeared.
    f.add("future-dated bar", symbol, interval,
          int((ts > config.now_local().tz_localize(None)).sum()))
    if interval != "day" and symbol not in ALWAYS_ON:
        tod = ts - ts.dt.normalize()
        f.add("bar outside 09:15-15:35", symbol, interval,
              int(((tod < SESSION_OPEN) | (tod > SESSION_CLOSE)).sum()))

    # long gaps between traded bars, and a price level shift across one
    if interval == "day" and traded.sum() > 50:
        t = ts[traded].dt.normalize().reset_index(drop=True)
        cc = pd.Series(c[traded])
        gaps = t.diff().dt.days
        seams = 0
        breaks = gaps[gaps > frames.LISTING_BREAK_DAYS]
        for i in breaks.index:
            before = cc.iloc[max(0, i - 5):i].median()
            after = cc.iloc[i:i + 5].median()
            if before and not np.isnan(after):
                ratio = after / before
                if not (0.4 <= ratio <= 2.5):
                    seams += 1
        if len(breaks):
            i = breaks.index[-1]
            note = (f"last: {t.iloc[i - 1].date()} -> {t.iloc[i].date()} "
                    f"({int(breaks.iloc[-1]):,}d); {i} bars before it dropped")
        else:
            note = ""
        f.add("listing break over 180 days (history restarts on load)", symbol,
              interval, len(breaks), note)
        f.add("price seam across a long gap (cut by the listing-break rule)",
              symbol, interval, seams)

    if interval == "day":
        session = ts.dt.normalize()
        ex_dates = [pd.Timestamp(x) for x in config.DEMERGERS.get(symbol, [])]
        f.add("demerger ex-date in config.DEMERGERS (history restarts on load)",
              symbol, interval, sum(1 for x in ex_dates if (session >= x).any()),
              ", ".join(str(x.date()) for x in ex_dates))
        if symbol in config.HISTORY_STARTS:
            cut = pd.Timestamp(config.HISTORY_STARTS[symbol])
            f.add("history before config.HISTORY_STARTS (dropped on load)", symbol,
                  interval, int((session < cut).sum()), f"before {cut.date()}")
        # for the second pass: closes by session (last wins), and the pre-2018
        # turnover that puts the stock in a liquidity bucket
        closes = pd.Series(c, index=session)
        f.closes[symbol] = closes[~closes.index.duplicated(keep="last")]
        early = traded & (session < pd.Timestamp("2018-01-01")).to_numpy()
        f.turnover[symbol] = float(np.median((c * v)[early])) if early.any() else 0.0


def second_pass(f: Findings) -> pd.DatetimeIndex:
    """The two checks that need every daily file before they can judge one."""
    crashes = frames.market_wide_days(
        {s: c for s, c in f.closes.items() if s not in INDICES and s not in ALWAYS_ON})

    # (e) a drop over DROP_LIMIT on a day the market did not crash
    for symbol, closes in f.closes.items():
        if symbol in INDICES or symbol in ALWAYS_ON:
            continue
        r = closes.pct_change()
        known = {pd.Timestamp(x) for x in config.DEMERGERS.get(symbol, [])}
        hit = r[(r < -DROP_LIMIT) & ~r.index.isin(crashes) & ~r.index.isin(known)]
        f.add("DROP over 25% on a normal day (demerger? verify, then config.DEMERGERS)",
              symbol, "day", len(hit),
              ", ".join(f"{d.date()} {x:+.0%}" for d, x in hit.head(3).items()))

    # (d) a year whose sigma is far above the stock's liquidity bucket
    rows = []
    for symbol, closes in f.closes.items():
        if symbol in INDICES or symbol in ALWAYS_ON:
            continue
        r = closes.pct_change()
        turnover = f.turnover.get(symbol, 0.0)
        bucket = bucket_of(turnover) if turnover > 0 else "recent (no pre-2018 trade)"
        for year, g in r.groupby(r.index.year):
            if g.notna().sum() >= SIGMA_MIN_RETURNS:
                rows.append((symbol, bucket, int(year), float(g.std())))
    if rows:
        table = pd.DataFrame(rows, columns=["symbol", "bucket", "year", "sigma"])
        table["ratio"] = table["sigma"] / table.groupby("bucket")["sigma"].transform("median")
        wild = table[table["ratio"] > SIGMA_MULTIPLE]
        for symbol, g in wild.groupby("symbol"):
            f.add("per-year sigma over 2.5x its liquidity bucket's median (the HINDPETRO shape)",
                  symbol, "day", len(g),
                  ", ".join(f"{int(y)} {x:.1f}x" for y, x in
                            g.sort_values("ratio", ascending=False)[["year", "ratio"]].to_numpy()[:3]))
    return crashes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", action="store_true", help="list every offending symbol")
    ap.add_argument("--interval", default="all", choices=["all", "day", "15minute"])
    args = ap.parse_args()

    cfg = config.load()
    universe = set(cfg.merged)
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

    crashes = second_pass(f)
    print(f"  scanned {scanned} files; {len(crashes)} market-wide crash days "
          f"(over 25% of stocks down over 8%)\n")
    print("=" * 84)
    print(f"  {'check':<52s}{'symbols':>9s}{'in univ':>9s}{'bars':>11s}")
    print("=" * 84)
    grouped = f.by_check()

    def block(title, checks):
        if not checks:
            return
        print(f"\n  {title}")
        print("  " + "-" * 80)
        for check in sorted(checks, key=lambda k: -sum(r[3] for r in grouped[k])):
            rows = grouped[check]
            symbols = {r[1] for r in rows}
            print(f"  {check:<52s}{len(symbols):>9,}{len(symbols & universe):>9,}"
                  f"{sum(r[3] for r in rows):>11,}")
            if args.detail:
                # universe members first, then by size
                ordered = sorted(rows, key=lambda r: (r[1] not in universe, -r[3]))
                for _, sym, iv, cnt, note in ordered[:25]:
                    tag = "" if sym in universe else "  (not in universe)"
                    print(f"      {sym:<14s}{iv:<10s}{cnt:>8,}  {note}{tag}")

    block("NEEDS A DECISION -- not repaired anywhere",
          [k for k in grouped if k not in BENIGN])
    block("HANDLED -- repaired or dropped automatically on load",
          [k for k in grouped if k in BENIGN])
    print("=" * 84)
    if not grouped:
        print("  no findings")


if __name__ == "__main__":
    main()

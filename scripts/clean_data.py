"""Write a cleaned copy of every raw parquet file to the clean data directory.

    Reads   /data/raw/kitelab/*.parquet          READ-ONLY, never modified
    Writes  /data/clean/kitelab/<same filename>  one cleaned file per raw file
    Writes  /work/kitelab/output/clean_data_summary.csv   per-file counts

The cleaning rules are NOT reinvented here. Each candle frame is handed to
kitelab.frames.sanitise(), which is the definition of "clean" that every strategy
in this project already reads through in memory. Writing that same function's
output to disk means /data/clean holds exactly what the backtests have been
trading on -- no second, slightly different idea of clean.

This script's own job is to MEASURE. sanitise() repairs quietly; here every rule
is counted separately, per file, and the totals are printed and saved. The
stage-by-stage frame is cross-checked against sanitise()'s own output on every
file, so if the rules in frames.py ever change, this script fails loudly instead
of reporting counts that no longer describe what it wrote.

    python -m scripts.clean_data                # clean everything
    python -m scripts.clean_data --limit 5      # first 5 files, smoke test
    python -m scripts.clean_data --dry-run      # measure, write nothing
"""
from __future__ import annotations

import argparse
import io
import shutil
from contextlib import redirect_stdout

import pandas as pd

from kitelab import config, frames
from kitelab.config import CLEAN, DATA, ROOT

# Small outputs (tables, reports) belong with the code, not with the data.
OUTPUT_DIR = ROOT / "output"

REQUIRED = ["ts", "open", "high", "low", "close", "volume"]
OHLC = ["open", "high", "low", "close"]

# The instrument master dumps live alongside the candles and share their suffix,
# but they are symbol lists, not price series -- no ts, no OHLC. No cleaning rule
# applies to them. They are copied through verbatim so that the clean directory
# is self-sufficient for the code that globs for them (fetch, screen_universe).
PASSTHROUGH_PREFIX = "instruments_"

# The rules, in the order sanitise() applies them. Printed at the end of every
# run so the report always states what was done to the data.
RULES = [
    ("drop-dead-close",
     "Bar dropped when close <= 0. A bar with no usable close cannot be "
     "repaired towards anything."),
    ("repair-nonpositive",
     "open <= 0 -> close; high <= 0 -> max(open, close); low <= 0 -> "
     "min(open, close). Kite's 2015-2018 intraday history records missing "
     "fields as 0.00, and an intrabar rule reads low=0 as a stop gapped "
     "through."),
    ("widen-containment",
     "high = max(high, open, close) and low = min(low, open, close). A bar's "
     "high and low must contain its own open and close; the close is "
     "authoritative, so the high and low are widened, never the close moved."),
    ("drop-untraded-outlier",
     f"Bar dropped when volume == 0 AND close is off a centred "
     f"{frames.UNTRADED_REF_WINDOW}-bar rolling median of the TRADED closes by "
     f"more than {1 / frames.UNTRADED_OUTLIER_FACTOR:.0f}x in either "
     f"direction. No volume means no trade, so there is no price to repair "
     f"towards -- the bar did not happen."),
    ("passthrough-instruments",
     f"Files named {PASSTHROUGH_PREFIX}*.parquet are symbol master dumps, not "
     f"candles. Copied through unchanged; no rule applies."),
]


def check_frame(name: str, d: pd.DataFrame) -> None:
    """Stop with a clear error if the frame is not the shape we expect."""
    missing = [c for c in REQUIRED if c not in d.columns]
    if missing:
        raise SystemExit(
            f"{name}: missing required column(s) {', '.join(missing)}.\n"
            f"  columns present: {', '.join(map(str, d.columns))}"
        )
    if d.empty:
        raise SystemExit(f"{name}: file holds no rows at all.")
    nulls = d[REQUIRED].isna().sum()
    if nulls.any():
        detail = ", ".join(f"{c}={int(n)}" for c, n in nulls.items() if n)
        raise SystemExit(
            f"{name}: unexpected missing values in required columns ({detail}). "
            f"Raw candles are not supposed to contain nulls; refusing to guess "
            f"what they should have been."
        )


def clean_frame(symbol: str, interval: str, raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Apply sanitise()'s rules stage by stage, counting each one.

    Returns the cleaned frame and a count per rule. The composed result is
    checked against frames.sanitise() itself, so the counts cannot drift away
    from what was actually written.
    """
    counts = dict.fromkeys([r[0] for r in RULES], 0)
    d = raw

    # Rule 1 and 2: the non-positive branch. Same arithmetic as sanitise(), which
    # keeps it inline; the assert at the end of this function is what guarantees
    # the two stay in step.
    if (d[OHLC] <= 0).any(axis=1).any():
        dead = d["close"] <= 0
        counts["drop-dead-close"] = int(dead.sum())
        d = d.loc[~dead].copy()
        broken = (d[OHLC] <= 0).any(axis=1)
        counts["repair-nonpositive"] = int(broken.sum())
        d["open"] = d["open"].where(d["open"] > 0, d["close"])
        d["high"] = d["high"].where(d["high"] > 0, d[["open", "close"]].max(axis=1))
        d["low"] = d["low"].where(d["low"] > 0, d[["open", "close"]].min(axis=1))
        d = d.reset_index(drop=True)

    # Rule 3: containment.
    high = d[["high", "open", "close"]].max(axis=1)
    low = d[["low", "open", "close"]].min(axis=1)
    counts["widen-containment"] = int(((high != d["high"]) | (low != d["low"])).sum())

    # Rules 3 and 4 are applied by frames itself. sanitise() prints as it repairs;
    # this script prints its own per-file line instead, so that chatter is caught.
    before_untraded = len(d)
    noise = io.StringIO()
    with redirect_stdout(noise):
        d = frames.enforce_containment(d, symbol, interval)
        d = frames.drop_untraded_outliers(d, symbol, interval)
        reference = frames.sanitise(raw.copy(), symbol, interval)
    counts["drop-untraded-outlier"] = before_untraded - len(d)

    d = d.reset_index(drop=True)
    check = reference.reset_index(drop=True)
    if not d.equals(check):
        raise SystemExit(
            f"{symbol} {interval}: this script's stage-by-stage result no longer "
            f"matches kitelab.frames.sanitise(). The rules in frames.py have "
            f"changed; update RULES and clean_frame() in this script to match "
            f"before trusting any of these counts."
        )
    return d, counts


# The raw directory is the long-term store and will grow to ~500 companies. The
# clean directory is the WORKING set: the universe actually being traded, plus the
# six class assets the reports chart. Cleaning everything in raw would put ~300
# unused symbols back into the working set every time this ran, which is exactly
# what the 2026-09-01 tidy-up removed. Pass --all to override.
ASSETS = ["BITCOIN", "NIFTY 50", "NIFTY BANK", "GOLD", "SILVER", "CRUDEOIL"]


def write_if_changed(frame: pd.DataFrame, target) -> bool:
    """Write only when the result differs from what is already there.

    Rewriting an identical file is not free. kitelab.signals fingerprints every
    price file by name, SIZE AND MODIFICATION TIME, so a no-op re-clean moved all
    214 timestamps and every stamped cache -- and the dashboard itself -- reported
    "the PRICE DATA has changed since they were built". Nothing had changed but
    the clocks. Comparing the frame rather than the bytes because parquet output
    is not guaranteed byte-identical between writes.
    """
    if target.exists():
        try:
            if pd.read_parquet(target).equals(frame.reset_index(drop=True)):
                return False
        except Exception:
            pass                       # unreadable or a different shape: rewrite it
    frame.to_parquet(target, index=False)
    return True


def working_set() -> set[str]:
    """Every symbol the project needs a price file for.

    Four groups, and the last two are easy to forget: the tradeable universe,
    the six class assets, the five assigned stocks -- one of which (HYUNDAI) is
    NOT in the universe, because it failed the quality gate but is still charted;
    leaving it out deleted its cleaned files and broke the dashboard rebuild --
    and the holdout, which is not in all_symbols by design and would otherwise
    never be cleaned at all. Everything the analysis side reads comes from CLEAN,
    so a symbol missing here is a symbol no backtest can see.
    """
    cfg = config.load()
    return (set(cfg.all_symbols) | set(cfg.out_of_sample) | set(ASSETS)
            | set(config.CLASS_ASSIGNED))


def in_scope(path, keep: set[str]) -> bool:
    """Is this raw file part of the working set? Instrument dumps always are."""
    if path.name.startswith(PASSTHROUGH_PREFIX):
        return True
    stem = path.stem
    for suffix in ("_day", "_15minute", "_30minute"):
        if stem.endswith(suffix):
            return stem[:-len(suffix)] in keep
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0,
                    help="clean only the first N files (smoke test)")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report, but write nothing")
    ap.add_argument("--all", action="store_true",
                    help="clean every raw file, not just the symbols in use")
    args = ap.parse_args()

    files = sorted(DATA.glob("*.parquet"))
    if not args.all:
        keep = working_set()
        files = [p for p in files if in_scope(p, keep)]
        print(f"working set: {len(keep)} symbols "
              f"({len(keep) - len(ASSETS)} universe + {len(ASSETS)} assets). "
              "Use --all to clean every raw file.")
    if args.limit:
        files = files[:args.limit]
    if not files:
        raise SystemExit(f"No parquet files found in {DATA}")

    print(f"reading from  {DATA}   (read-only)")
    print(f"writing to    {CLEAN}{'   [DRY RUN, nothing written]' if args.dry_run else ''}")
    print(f"{len(files)} parquet files to process\n")

    if not args.dry_run:
        CLEAN.mkdir(parents=True, exist_ok=True)

    rows, totals, shown = [], dict.fromkeys([r[0] for r in RULES], 0), set()
    rows_in = rows_out = 0

    for path in files:
        if path.name.startswith(PASSTHROUGH_PREFIX):
            if not args.dry_run:
                target = CLEAN / path.name
                # Same reasoning as write_if_changed: an identical copy would
                # still move the mtime and invalidate every stamp.
                if not (target.exists() and target.stat().st_size == path.stat().st_size):
                    shutil.copy2(path, target)
            totals["passthrough-instruments"] += 1
            n = len(pd.read_parquet(path))
            print(f"{path.name:44} {n:>7,} -> {n:>7,}   copied unchanged (not candles)")
            rows.append(dict(file=path.name, symbol="", interval="", rows_before=n,
                             rows_after=n, **dict.fromkeys(totals, 0)))
            rows_in += n; rows_out += n
            continue

        symbol, _, interval = path.stem.rpartition("_")
        raw = pd.read_parquet(path)
        check_frame(path.name, raw)

        # CLAUDE.md asks every script to show what it loaded. One representative
        # file per interval, rather than 400 identical headers.
        if interval not in shown:
            shown.add(interval)
            print(f"  sample of the {interval} files -- {path.name}: "
                  f"{raw.shape[0]:,} rows x {raw.shape[1]} columns")
            print(raw.head(3).to_string(index=False), "\n")

        clean, counts = clean_frame(symbol, interval, raw)
        for k, v in counts.items():
            totals[k] += v

        before, after = len(raw), len(clean)
        rows_in += before; rows_out += after
        touched = ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "no rule fired"
        print(f"{path.name:44} {before:>7,} -> {after:>7,}   {touched}")

        if not args.dry_run:
            write_if_changed(clean, CLEAN / path.name)

        rows.append(dict(file=path.name, symbol=symbol, interval=interval,
                         rows_before=before, rows_after=after, **counts))

    summary = pd.DataFrame(rows)
    out_dir = OUTPUT_DIR
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out_dir / "clean_data_summary.csv", index=False)

    print("\n" + "=" * 78)
    print("RULES APPLIED")
    print("=" * 78)
    for name, description in RULES:
        print(f"\n  {name}  --  {totals[name]:,} "
              f"{'files' if name == 'passthrough-instruments' else 'bars'}")
        for line in _wrap(description):
            print(f"      {line}")

    print("\n" + "=" * 78)
    print("ROW COUNTS")
    print("=" * 78)
    print(f"  files processed      {len(files):>12,}")
    print(f"  rows before          {rows_in:>12,}")
    print(f"  rows after           {rows_out:>12,}")
    print(f"  rows dropped         {rows_in - rows_out:>12,} "
          f"({(rows_in - rows_out) / rows_in * 100:.4f}%)")
    repaired = totals["repair-nonpositive"] + totals["widen-containment"]
    print(f"  bars repaired        {repaired:>12,}   (kept, values corrected)")
    changed = summary[summary["rows_before"] != summary["rows_after"]]
    print(f"  files losing rows    {len(changed):>12,}")
    if not args.dry_run:
        print(f"\n  cleaned parquet -> {CLEAN}")
        print(f"  per-file counts -> {out_dir / 'clean_data_summary.csv'}")


def _wrap(text: str, width: int = 68) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


if __name__ == "__main__":
    main()

"""Backfill candles.

    python -m scripts.backfill                     # the hand-drawn universe
    python -m scripts.backfill --all               # the whole configured universe
    python -m scripts.backfill --symbols-file F    # an arbitrary list, one per line
    python -m scripts.backfill --daily-only        # skip 15-minute (a fifth the cost)

Safe to re-run: it resumes from the last bar already on disk.

The two-phase pattern for widening the universe (2026-09-01):

    1. python -m scripts.screen_universe --candidates    writes the candidate list
    2. python -m scripts.backfill --symbols-file data/candidates.txt --daily-only
    3. python -m scripts.screen_universe --rank          screens what arrived
    4. python -m scripts.backfill --symbols-file data/accepted.txt

Daily is ~4 requests per symbol and 15-minute ~22, so screening on daily first costs
a fifth of what fetching everything would, and only the survivors get the expensive
intraday pull.
"""
import argparse
from dataclasses import replace
from pathlib import Path

from kitelab import auth, config, fetch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true",
                        help="include the extended and holdout universes")
    parser.add_argument("--symbols-file", type=Path,
                        help="fetch this list instead (one trading symbol per line; "
                             "blank lines and # comments ignored)")
    parser.add_argument("--daily-only", action="store_true",
                        help="fetch daily candles only -- the cheap screening pass")
    parser.add_argument("--limit", type=int,
                        help="stop after this many symbols (for a trial run)")
    args = parser.parse_args()

    # The one job that needs an account: this is what pulls new data from Kite.
    cfg = config.require_secrets(config.load())
    if args.symbols_file:
        names = [ln.strip() for ln in args.symbols_file.read_text().splitlines()]
        names = [n for n in names if n and not n.startswith("#")]
        if not names:
            raise SystemExit(f"{args.symbols_file} has no symbols in it.")
        cfg = replace(cfg, symbols=names)
    elif args.all:
        cfg = replace(cfg, symbols=cfg.all_symbols)
    if args.limit:
        cfg = replace(cfg, symbols=cfg.symbols[:args.limit])

    intervals = ["day"] if args.daily_only else None
    per_symbol = 4 if args.daily_only else 26
    print(f"\n  {len(cfg.symbols)} symbols to backfill"
          f"{' (daily only)' if args.daily_only else ''}")
    print(f"  roughly {len(cfg.symbols) * per_symbol:,} requests at "
          f"{fetch.MIN_REQUEST_GAP}s apart "
          f"-- about {len(cfg.symbols) * per_symbol * fetch.MIN_REQUEST_GAP / 60:.0f} "
          f"minutes of throttle, plus network time\n")
    fetch.backfill(auth.client(cfg), cfg, intervals)


if __name__ == "__main__":
    main()

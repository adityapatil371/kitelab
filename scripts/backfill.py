"""Backfill candles.

    python -m scripts.backfill          # just the hand-drawn universe (config symbols)
    python -m scripts.backfill --all    # plus the extended list (Nifty Next 50)

Safe to re-run: it resumes from the last bar already on disk.
"""
import argparse
from dataclasses import replace

from kitelab import auth, config, fetch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="include the extended and holdout universes")
    args = parser.parse_args()

    cfg = config.load()
    if args.all:
        cfg = replace(cfg, symbols=cfg.all_symbols)
    print(f"\n  {len(cfg.symbols)} symbols to backfill")
    fetch.backfill(auth.client(cfg), cfg)


if __name__ == "__main__":
    main()

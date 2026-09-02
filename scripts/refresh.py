"""Bring the dashboard up to date, doing only the work that is actually needed.

    python -m scripts.refresh            # clean and rebuild whatever is stale
    python -m scripts.refresh --check    # say what is stale, change nothing
    python -m scripts.refresh --force    # rebuild even if everything looks current

This does NOT fetch anything and needs no Kite credentials. Getting new data is a
separate job with a separate program:

    python -m scripts.backfill           # raw downloads, needs an account

The split is deliberate. Fetching talks to Zerodha and can only run when the
market data service is up and you have logged in. Refreshing is pure local
computation over files already on disk, and should never be blocked by either.

WHAT IT CHECKS, in order:

    1. Is the CLEANED data behind the RAW data?
       Any raw file in the working set that is missing from the clean directory,
       or newer than its cleaned copy, means backfill has brought in something
       that has not been cleaned yet.  ->  runs scripts.clean_data

    2. Is the DASHBOARD behind the cleaned data?
       dashboard.json carries a stamp of the universe, the price files and the
       strategy code it was built from. Any of the three moving makes its
       numbers describe something other than what you are trading now.
       ->  runs scripts.dashboard_data

Each step is skipped when it is not needed, so re-running this on an unchanged
project costs a second and prints "already current". That matters because the
rebuild is a ~30 minute job and should never be started out of doubt.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

# This step COMPUTES, so it needs the project's dependencies. The check has to
# happen before the imports below -- scripts.clean_data pulls in pandas at module
# level, so a guard inside main() would never run: Python would already have
# raised a bare ImportError from the import line.
try:
    import pandas  # noqa: F401
except ImportError:                                          # pragma: no cover
    raise SystemExit(
        "\n  This needs the project's Python environment (pandas, numpy,\n"
        "  pyarrow). Run it with the venv:\n\n"
        "      ./.venv/bin/python -m scripts.refresh\n\n"
        "  If there is no .venv yet:\n"
        "      python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt\n\n"
        "  Viewing the dashboard needs none of this -- ./run_dashboard.sh runs\n"
        "  on any Python 3.11+.\n")

from kitelab import config, dashboard_server
from kitelab.config import CLEAN, DATA
from scripts.clean_data import in_scope, working_set


def needs_cleaning(keep: set[str]) -> tuple[list[str], list[str]]:
    """(missing, outdated) raw files whose cleaned copy is absent or older.

    Compared on modification time. clean_data writes a cleaned copy per raw file,
    so a raw file that is newer than its copy has been refetched since.
    """
    missing, outdated = [], []
    for raw in sorted(DATA.glob("*.parquet")):
        if not in_scope(raw, keep):
            continue
        clean = CLEAN / raw.name
        if not clean.exists():
            missing.append(raw.name)
        elif raw.stat().st_mtime > clean.stat().st_mtime:
            outdated.append(raw.name)
    return missing, outdated


def run(module: str, why: str) -> None:
    """Run one of the project's own programs, showing its output as it goes.

    A subprocess rather than an import: each program owns its argument parsing
    and prints its own row counts, and running it any other way would mean
    reproducing that here and letting the two drift apart.
    """
    print(f"\n{'=' * 78}\n  {module}  --  {why}\n{'=' * 78}\n", flush=True)
    started = time.time()
    result = subprocess.run([sys.executable, "-m", module])
    if result.returncode != 0:
        raise SystemExit(f"\n  {module} failed (exit {result.returncode}). "
                         "Nothing further was run.\n")
    print(f"\n  {module} finished in {(time.time() - started) / 60:.1f} min")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report what is stale and exit without changing anything")
    ap.add_argument("--force", action="store_true",
                    help="clean and rebuild even if nothing looks stale")
    args = ap.parse_args()

    keep = working_set()
    cfg = config.load()
    # Broken out since the holdout landed: it is most of the working set and
    # lumping it in with "class assets" made the line read as 400 assets.
    unseen = cfg.out_of_sample
    extras = len(keep) - len(cfg.all_symbols) - len(unseen)
    print(f"\n  universe   {len(cfg.all_symbols)} stocks in-sample "
          f"+ {len(unseen)} holdout (+{extras} class assets)")
    print(f"  raw        {DATA}")
    print(f"  clean      {CLEAN}")

    # ---- 1. is the cleaned data behind the raw data? ----------------------
    missing, outdated = needs_cleaning(keep)
    dirty = len(missing) + len(outdated)
    if dirty:
        print(f"\n  CLEANING NEEDED: {len(missing)} raw file(s) never cleaned, "
              f"{len(outdated)} refetched since cleaning")
        for name in (missing + outdated)[:8]:
            print(f"      {name}")
        if dirty > 8:
            print(f"      ... and {dirty - 8} more")
    else:
        print("\n  cleaned data is current with the raw downloads")

    # ---- 2. is the dashboard behind the cleaned data? ---------------------
    verdict = dashboard_server.status()
    if verdict.get("stale"):
        print(f"\n  DASHBOARD REBUILD NEEDED\n      {verdict.get('message', '')}")
    else:
        print(f"\n  dashboard is current (built {verdict.get('built')}, "
              f"{verdict.get('n_now')} stocks)")

    if args.check:
        print("\n  --check: nothing was changed.\n")
        raise SystemExit(0 if not (dirty or verdict.get("stale")) else 1)

    if not dirty and not verdict.get("stale") and not args.force:
        print("\n  Everything is already current. Nothing to do.\n")
        return

    started = time.time()
    if dirty or args.force:
        run("scripts.clean_data", f"{dirty} file(s) need cleaning" if dirty
            else "--force")
    if verdict.get("stale") or args.force:
        run("scripts.dashboard_data", verdict.get("message", "--force")[:60])

    print(f"\n  Refresh complete in {(time.time() - started) / 60:.1f} min.")
    after = dashboard_server.status()
    if after.get("stale"):
        print(f"  WARNING: the dashboard still reports itself stale -- "
              f"{after.get('message', '')}\n")
    else:
        print(f"  Dashboard is current: {after.get('n_now')} stocks, "
              f"built {after.get('built')}.\n")


if __name__ == "__main__":
    main()

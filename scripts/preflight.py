"""Run the whole dashboard build over a handful of symbols, into a temp dir.

    python -m scripts.preflight            # 3 symbols, ~40 seconds
    python -m scripts.preflight -n 8

WHY THIS EXISTS. A full rebuild takes six minutes, and for most of 2026-09-03 it
was serving as the test: change something, wait six minutes, discover a NameError
inside main() that no import could have caught. Five builds were spent that way,
three of them on faults a smoke run would have found in under a minute -- a
block-cut that silently deleted BREADTH_SIZES, a payload key the page could not
read, an assets view that rendered nothing.

The whole of main() runs here. Same code path, same payload keys, same JSON
serialisation, same numpy-leak check -- only the universe is small. If this
passes, the rebuild will too, and that is the entire claim.

THE REDIRECTION IS THE POINT, NOT AN OPTIMISATION. kitelab.signals.CACHE,
dashboard_server.STAMP_PATH and dashboard_data.OUT are module globals, so they
can be pointed at a temp directory. Without that, a smoke run over three symbols
writes caches named `_all` stamped for three symbols, destroying the 500-symbol
caches -- and forcing exactly the rebuild this exists to avoid. The assertions
below refuse to run if the redirection has not taken, because silently falling
back to the real paths is the one failure worse than not running at all.

WHAT IT DOES NOT CHECK. Numbers. A tiny universe produces real but meaningless
results, so this asserts SHAPE -- that every stage completes, that the payload
carries the keys web/dashboard.html reads, that nothing leaked as numpy. Row
counts and medians are deliberately not asserted: on three symbols the breadth
sweep has one basket size, and a check that cries wolf on every run gets ignored.

Run it with the other two, in this order:

    python -m unittest discover -s tests -t .   # unit tests, instant
    python -m pyflakes kitelab scripts tests    # undefined names, instant
    python -m scripts.preflight                 # the build path, ~40s
    node scripts/check_dashboard.js <payload>   # the page, instant
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile

import pandas as pd

from kitelab import config, dashboard_server, signals


def _page_keys() -> set[str]:
    """Every DATA.<key> the dashboard page reads.

    Parsed from the page rather than listed here: a hand-kept list is the same
    mistake as the hand-kept cache-stamp list that invalidated a day of Holy
    Grail numbers, and it fails the same way -- silently, later.
    """
    page = (pathlib.Path(__file__).resolve().parent.parent
            / "web" / "dashboard.html").read_text()
    # STRIP COMMENTS FIRST, rather than discarding keys that appear in one.
    # The first version of this walked lines and dropped any key seen in a
    # comment, which was wrong in both directions: it missed continuation lines
    # inside a /* */ block (so a key named only in prose about REMOVED code
    # counted as read), and it dropped pool_year, which appears in a comment AND
    # in the code beneath it. Removing the comments and then scanning what is
    # left has neither failure.
    code = re.sub(r"/\*.*?\*/", " ", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", " ", code, flags=re.M)
    keys = set(re.findall(r'DATA\.([A-Za-z_][A-Za-z0-9_]*)', code))
    keys |= set(re.findall(r'DATA\["([^"]+)"\]', code))
    return keys


def _attached_keys() -> set[str]:
    """Payload keys a LATER stage adds, which this build is not asked to emit.

    The payload the page reads is built in three stages: dashboard_data writes
    it, scripts.wf_attach computes the day-by-day tests beside it, and
    scripts.attach_diagnostics merges those in. Preflight runs the FIRST stage
    only -- deliberately, because the other two are cheap and the expensive
    thing to smoke-test is the build. So the keys the third stage adds are
    absent here by design, and blaming the build for them is a false alarm.

    It was a LOUD false alarm: `daily_excess`, `fill_timing` and `diagnostics`
    entered the page with the daily-excess gate on 2026-09-10, and from that day
    `python3 -m scripts.preflight` failed and `scripts.refresh` aborted at the
    gate before starting the rebuild it guards. Found 2026-09-11.

    Read out of the merging script the same way page keys are read out of the
    page, and for the same reason: a hand-kept list of exceptions is the very
    thing _page_keys exists not to be, and it fails the same way -- silently,
    later, when someone adds a fourth key. If the pattern ever stops matching
    this returns empty, the keys go back to counting as missing, and the FAIL
    comes back rather than the check quietly excusing everything.
    """
    src = (pathlib.Path(__file__).resolve().parent
           / "attach_diagnostics.py").read_text()
    return set(re.findall(r'payload\["([A-Za-z_][A-Za-z0-9_]*)"\]\s*=', src))


def _one_per_bucket(symbols, n: int, asof: int) -> list[str]:
    """The most liquid stock from each liquidity bucket, then the rest by
    turnover. Two of the three symbols the old `merged[:3]` picked (HAL, IRFC)
    had no pre-2018 bars, so every bucket subset in the smoke build was empty
    (audit E4). Same cut points and the same point-in-time rule as
    scripts.dashboard_data's buckets()."""
    import numpy as np
    from kitelab import frames
    cut = pd.Timestamp(f"{asof}-01-01")
    turn: dict[str, float] = {}
    for sym in symbols:
        try:
            d = frames.daily(sym)
        except SystemExit:
            continue
        v = (d.loc[d["ts"] < cut, "close"] * d.loc[d["ts"] < cut, "volume"]).to_numpy(float)
        v = v[v > 0]
        turn[sym] = float(np.median(v)) if len(v) else -1.0
    buckets = {"large": [s for s, t in turn.items() if t >= 25e7],
               "mid": [s for s, t in turn.items() if 5e7 <= t < 25e7],
               "small": [s for s, t in turn.items() if 0 <= t < 5e7],
               "recent": [s for s, t in turn.items() if t < 0]}
    chosen: list[str] = []
    for names in buckets.values():
        if names and len(chosen) < n:
            chosen.append(max(names, key=lambda s: turn[s]))
    for s in sorted(turn, key=lambda s: -turn[s]):
        if len(chosen) >= n:
            break
        if s not in chosen:
            chosen.append(s)
    return chosen


def _small_config(real, chosen):
    """A copy of the loaded config whose universe is `chosen`, whatever list
    fields the Config dataclass currently has."""
    fields = {f.name for f in dataclasses.fields(real)}
    over = {"symbols": chosen}
    for name in ("extended", "holdout", "unseen"):
        if name in fields:
            over[name] = []
    return dataclasses.replace(real, **over)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--symbols", type=int, default=3)
    ap.add_argument("--keep", action="store_true",
                    help="leave the temp payload on disk and print its path")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show the build's own progress output")
    args = ap.parse_args()

    from kitelab import validation
    from scripts import dashboard_data as dd

    real = config.load()
    if len(real.merged) < args.symbols:
        sys.exit(f"universe has only {len(real.merged)} symbols")
    chosen = _one_per_bucket(real.merged, args.symbols, dd.START_DEFAULT)
    small = _small_config(real, chosen)
    # UNDER MIN_TRADES EVERY VALIDATION FUNCTION RETURNS None/EMPTY, which is
    # what keeps a 3-symbol build green -- and what let a broken per-universe
    # branch pass preflight until 2026-09-07 (audit E4): on HAL/HINDZINC/IRFC
    # five variants fell under 30 trades and every bucket subset was empty, so
    # fixed_checks_by_universe and walk_forward_grid never ran their bodies.
    # Lowered here, in the smoke build only, so the branches execute.
    validation.MIN_TRADES = 5
    # Shape, not values: ten shuffles exercise the permutation branch; the
    # real build's PERMUTATION_ROUNDS over 3 symbols x 19 variants x 5
    # universes took the "40-second" smoke build past ten minutes.
    validation.PERMUTATION_ROUNDS = 10

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kitelab-preflight-"))
    saved = (signals.CACHE, dashboard_server.STAMP_PATH, dd.OUT, dd.GRID_CKPT,
             config.load)
    signals.CACHE = tmp / "signal_cache"
    dashboard_server.STAMP_PATH = tmp / "stamp.json"
    dd.OUT = tmp / "dashboard.json"
    # ADDED 2026-09-09 with the grid checkpoints, and it was needed: the first
    # preflight after they landed wrote 38 partitions into the real
    # CLEAN/grid_ckpt. Nothing was served wrong -- a partition's digest covers
    # the universes, so the 3-symbol files could only ever miss -- but they are
    # the same class of leak the assert below exists to catch.
    dd.GRID_CKPT = tmp / "grid_ckpt"
    config.load = lambda: small

    # REFUSE TO RUN unless every write now lands in the temp directory. If any
    # of these still points at CLEAN, the build below would overwrite the real
    # caches and the real payload.
    for name, path in (("signals.CACHE", signals.CACHE),
                       ("STAMP_PATH", dashboard_server.STAMP_PATH),
                       ("dashboard_data.OUT", dd.OUT),
                       ("dashboard_data.GRID_CKPT", dd.GRID_CKPT)):
        assert tmp in path.parents or path.parent == tmp, \
            f"{name} was not redirected -- refusing to run"

    print(f"  preflight: full build over {len(small.merged)} symbols -> {tmp}",
          flush=True)
    try:
        if args.verbose:
            dd.main()
        else:
            noise = io.StringIO()
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                dd.main()
    except Exception:
        print("\n  BUILD FAILED. The rebuild would fail the same way.\n", flush=True)
        raise
    finally:
        (signals.CACHE, dashboard_server.STAMP_PATH, dd.OUT, dd.GRID_CKPT,
         config.load) = saved

    payload = json.loads((tmp / "dashboard.json").read_text())
    wanted, got = _page_keys(), set(payload)
    attached = _attached_keys() & wanted
    missing = sorted(wanted - got - attached)
    dead = sorted(got - wanted)
    # THE BRANCHES THAT USED TO BE SKIPPED (audit E4): with MIN_TRADES lowered
    # and one symbol per bucket, every per-universe validation record and
    # every per-scenario benchmark must exist. Shape only, never values.
    problems: list[str] = []
    vs = payload.get("validation_summary") or {}
    holds = vs.get("hold_cagr_by_scenario") or {}
    for uni in payload.get("universes", {}):
        for year in payload.get("start_years", []):
            if f"{uni}|{year}" not in holds:
                problems.append(f"hold_cagr_by_scenario lacks {uni}|{year}")
    for key in ("alpha", "hurdle", "expected_best", "expected_by_chance", "tried"):
        if key not in vs:
            problems.append(f"validation_summary lacks {key}")
    equity = [u for u in payload.get("universes", {}) if u not in payload.get("single_name", [])]
    n_fcu = sum(1 for rec in payload.get("validation", {}).values()
                for u in equity if u in (rec.get("fixed_checks_by_universe") or {}))
    if payload.get("validation") and n_fcu == 0:
        problems.append("fixed_checks_by_universe is empty for every variant and universe")
    n_wf = sum(1 for rec in payload.get("validation", {}).values()
               for wf in (rec.get("walk_forward_by_scenario") or {}).values()
               if wf.get("windows"))
    if payload.get("validation") and n_wf == 0:
        problems.append("walk_forward_by_scenario has no windows anywhere")
    if problems:
        for msg in problems:
            print(f"  FAIL       {msg}")
        missing = missing + ["(validation shape)"]

    print(f"  payload:   {len(payload)} keys, {len(payload.get('grid', {})):,} grid cells")
    if attached:
        # Named, not silently excused -- the reader should see which keys this
        # build is not responsible for, and how they get there.
        print(f"  NOTE       added later by scripts.attach_diagnostics, not by "
              f"this build: {', '.join(sorted(attached))}")
    if dead:
        print(f"  NOTE       computed but never read by the page: {', '.join(dead)}")
    if missing:
        print(f"  FAIL       page reads keys the build does not emit: {', '.join(missing)}")

    out = tmp / "dashboard.json"
    if args.keep:
        print(f"\n  payload kept at {out}")
        print(f"  render it: node scripts/check_dashboard.js {out}\n")
    else:
        shutil.rmtree(tmp, ignore_errors=True)

    if missing:
        sys.exit(1)
    print("  OK         every stage ran and the page's keys are all present\n")


if __name__ == "__main__":
    main()

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--symbols", type=int, default=3)
    ap.add_argument("--keep", action="store_true",
                    help="leave the temp payload on disk and print its path")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show the build's own progress output")
    args = ap.parse_args()

    from scripts import dashboard_data as dd

    real = config.load()
    if len(real.merged) < args.symbols:
        sys.exit(f"universe has only {len(real.merged)} symbols")
    small = dataclasses.replace(real, symbols=real.merged[:args.symbols],
                                extended=[], holdout=[], unseen=[])

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kitelab-preflight-"))
    saved = (signals.CACHE, dashboard_server.STAMP_PATH, dd.OUT, config.load)
    signals.CACHE = tmp / "signal_cache"
    dashboard_server.STAMP_PATH = tmp / "stamp.json"
    dd.OUT = tmp / "dashboard.json"
    config.load = lambda: small

    # REFUSE TO RUN unless every write now lands in the temp directory. If any
    # of these still points at CLEAN, the build below would overwrite the real
    # caches and the real payload.
    for name, path in (("signals.CACHE", signals.CACHE),
                       ("STAMP_PATH", dashboard_server.STAMP_PATH),
                       ("dashboard_data.OUT", dd.OUT)):
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
        (signals.CACHE, dashboard_server.STAMP_PATH, dd.OUT, config.load) = saved

    payload = json.loads((tmp / "dashboard.json").read_text())
    wanted, got = _page_keys(), set(payload)
    missing = sorted(wanted - got)
    dead = sorted(got - wanted)

    print(f"  payload:   {len(payload)} keys, {len(payload.get('grid', {})):,} grid cells")
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

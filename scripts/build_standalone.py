"""Build ONE self-contained HTML file from web/article.html + dashboard.json.

    python3 -m scripts.build_standalone [-o output/stock-analysis.html]

WHY THIS EXISTS
---------------
The served reading page (/read) needs three things running: the Mac awake, the
dashboard server, and a cloudflared tunnel. Close the lid and the link dies
mid-sentence for everyone, and the URL is freshly minted on every restart, so
it is a "look at this now" link rather than something anyone can keep.

This makes the other kind of artefact: a single file, no server, no passphrase,
no network at all. Mail it, put it in a chat, drop it on a static host. It
opens from the filesystem on a laptop or a phone and reads identically.

WHAT IT PUTS IN, AND WHAT IT LEAVES OUT
---------------------------------------
The article reads exactly ten top-level keys of dashboard.json and exactly four
fields of each grid cell. This script copies those and nothing else -- not by
deleting what it does not want, but by copying only what it does, which is the
form of the rule that cannot be defeated by a new field appearing upstream.

So the file carries returns and summary statistics, and does NOT carry the
per-run equity curves (`curve_index`, and the separate curves file the server
serves by byte range), the day-by-day excess series, the fill-timing tables,
or the per-rule validation detail. It is about a fifth the size of the payload
the server sends, and everything left out is the part a reader of the article
never sees anyway.

There is one thing worth being straight about: a file is not a door. Whoever
has it can forward it, and there is no passphrase in front of it and no way to
take it back. That is the trade for it working with the laptop shut.

Verify with `node scripts/check_standalone.js`, which re-renders the article
from BOTH payloads -- the trimmed one and the full one -- and fails unless the
two produce byte-identical HTML.
"""
import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The ten keys web/article.html actually reads. Grep it for `DATA.` before
# changing this list; check_standalone.js fails loudly if one goes missing.
KEEP_TOP = [
    "built",
    "strategies",
    "universes",
    "risks",
    "capitals",
    "start_default",
    "priority_default",
    "validation_summary",
    "diagnostics",
    # "grid" is handled separately -- it is trimmed field by field.
]

# The four fields of a grid cell the page reads. The other eighteen (sharpe,
# ulcer, exposure, the skip counters...) are the dashboard's business.
KEEP_CELL = ["cagr", "wiped", "maxdd", "taken"]


def clean_dir():
    """Same resolution order as kitelab/config.py and run_dashboard.sh:57.

    The container and the Mac are one directory with two CLEAN paths; a
    script that hardcodes either works on exactly one side.
    """
    home = os.environ.get("HOME", "")
    for cand in (os.environ.get("KITELAB_CLEAN_DIR"),
                 os.path.join(home, "data/clean/kitelab") if home else None,
                 "/data/clean/kitelab",
                 str(ROOT / "data-clean")):
        if cand and os.path.isfile(os.path.join(cand, "dashboard.json")):
            return pathlib.Path(cand)
    sys.exit("No dashboard.json in any known CLEAN directory. "
             "Build one:  python -m scripts.refresh")


def trim(payload):
    """Copy the allowlist across. Never mutate, never delete -- build up."""
    missing = [k for k in KEEP_TOP + ["grid"] if k not in payload]
    if missing:
        sys.exit(f"dashboard.json is missing keys the article needs: {missing}")

    out = {k: payload[k] for k in KEEP_TOP}

    grid_in = payload["grid"]
    print(f"  grid cells in : {len(grid_in):,}")
    grid_out = {}
    dropped_fields = set()
    for key, cell in grid_in.items():
        if cell is None:
            grid_out[key] = None
            continue
        dropped_fields |= set(cell) - set(KEEP_CELL)
        # `wiped` is false for almost every cell; the page reads it as falsy,
        # so leaving it out when false is safe and saves a sixth of the grid.
        small = {f: cell[f] for f in KEEP_CELL if f in cell and not (f == "wiped" and not cell[f])}
        grid_out[key] = small
    out["grid"] = grid_out
    print(f"  grid cells out: {len(grid_out):,}  (unchanged -- no cell is dropped)")
    print(f"  fields kept per cell   : {', '.join(KEEP_CELL)}")
    print(f"  fields dropped per cell: {', '.join(sorted(dropped_fields))}")

    dropped_top = sorted(set(payload) - set(out))
    print(f"  top-level keys kept    : {', '.join(sorted(out))}")
    print(f"  top-level keys dropped : {', '.join(dropped_top)}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--out", default=str(ROOT / "output" / "stock-analysis.html"),
                    help="where to write the file (default output/stock-analysis.html)")
    ap.add_argument("-j", "--json", default=None, help="a dashboard.json to use instead")
    args = ap.parse_args()

    src_json = pathlib.Path(args.json) if args.json else clean_dir() / "dashboard.json"
    page = ROOT / "web" / "article.html"
    out_path = pathlib.Path(args.out)

    print(f"reading  {src_json}  ({src_json.stat().st_size:,} bytes)")
    payload = json.loads(src_json.read_text())
    print(f"  top-level keys: {len(payload)}")
    print(f"  built from prices to {payload.get('built')}")

    print("\ntrimming the payload to what the article reads")
    small = trim(payload)

    # separators= drops the space after every ':' and ',' -- about 8% of the
    # file for free, and JSON.parse does not care.
    blob = json.dumps(small, separators=(",", ":"))
    print(f"\n  payload json: {len(json.dumps(payload)):,} bytes -> {len(blob):,} bytes "
          f"({100 * len(blob) / len(json.dumps(payload)):.1f}%)")

    # Nothing in the payload may close the script block early.
    blob = blob.replace("</", "<\\/")

    print(f"\nreading  {page}  ({page.stat().st_size:,} bytes)")
    html = page.read_text()

    # Replace the marked bootstrap, and only that. A regex over the whole file
    # would be one refactor away from silently matching nothing.
    start, end = "/* BOOTSTRAP-START */", "/* BOOTSTRAP-END */"
    if html.count(start) != 1 or html.count(end) != 1:
        sys.exit(f"could not find exactly one {start} / {end} pair in {page} -- "
                 "the bootstrap markers are what this script splices on")
    head, rest = html.split(start, 1)
    _, tail = rest.split(end, 1)

    inline = (
        "/* This file is SELF-CONTAINED. The served page fetches its numbers\n"
        "   from /api/dashboard; here they are inlined by\n"
        "   scripts/build_standalone.py, trimmed to the ten payload keys and\n"
        "   four cell fields the article reads. No network, no server. */\n"
        f"boot({blob});"
    )
    built = head + inline + tail

    # A provenance line in the source, where it cannot be mistaken for prose.
    built = built.replace(
        "<title>Stock Analysis</title>",
        "<title>Stock Analysis</title>\n"
        f"<!-- self-contained build of web/article.html, prices to {payload.get('built')}.\n"
        "     Rebuild:  python3 -m scripts.build_standalone -->", 1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(built)
    print(f"\nwrote    {out_path}  ({len(built.encode()):,} bytes)")

    # Belt and braces: prove the thing we promised about what is NOT in here.
    for gone in ("curve_index", "daily_excess", "fill_timing", "calendars", "trade_stats"):
        if f'"{gone}"' in blob:
            sys.exit(f"the trimmed payload still contains {gone} -- the allowlist leaked")
    leaked = sorted(set(re.findall(r'"([A-Z][A-Z0-9&\-]{2,})"', blob)))
    if leaked:
        sys.exit(f"the trimmed payload contains symbol-shaped strings: {leaked[:10]}")
    print("     no curves, no daily series, no fill timing, no symbol-shaped strings")
    print("\nverify with:  node scripts/check_standalone.js")


if __name__ == "__main__":
    main()

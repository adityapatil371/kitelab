"""Copy the files that CANNOT be rebuilt into the repo, where git keeps them.

Almost everything here is reproducible. `python -m scripts.clean_data` rebuilds
CLEAN from the raw store, `scripts.refresh` rebuilds every cache and the
dashboard, and the raw store itself can be refetched from Kite. Four things are
not in that set, and each of them was one `rm` from gone:

  levels.json    hand-drawn support and resistance. Nothing regenerates it.
  accepted.txt   the 399 screened names. In principle screen_universe rewrites
                 it -- but against a NEWER instrument dump and a newer data
                 snapshot, so a rerun silently REDEFINES the holdout rather than
                 restoring it. A holdout you can accidentally redraw is not one.
  the universe   [universe] symbols / holdout / unseen in config.local.toml:
                 which 101 stocks are in-sample and which 399 are not. This is
                 the experiment's definition, and config.local.toml is gitignored
                 -- correctly, because the same file holds the Kite api_key and
                 api_secret. So the split shared a hiding place with the secrets
                 and inherited their absence from git.
  levels' shape  written alongside as a count, so a truncated restore is obvious.

Nothing secret is copied. The universe lists are read through config.load() and
written as plain JSON, so [kite] never leaves config.local.toml.

Reads:  <clean>/levels.json, <clean>/accepted.txt, config.local.toml
Writes: data/keep/levels.json, data/keep/accepted.txt, data/keep/universe.json,
        data/keep/MANIFEST.txt
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from kitelab import config

KEEP = config.ROOT / "data" / "keep"

# Copied verbatim. (filename, what it is, the smallest size that is plausibly
# whole -- a backup that shrinks is the failure this script exists to catch.)
COPIES = [
    ("levels.json", "hand-drawn levels", 500),
    ("accepted.txt", "the 399 screened names", 500),
]


def fail(message: str) -> None:
    print(f"\nSTOPPED: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="overwrite even when the new copy is smaller than the old")
    args = ap.parse_args()

    KEEP.mkdir(parents=True, exist_ok=True)
    print(f"backing up into {KEEP}")
    lines = [f"# written {config.now_local():%Y-%m-%d %H:%M} IST by "
             f"scripts.backup_inputs", ""]

    for name, what, floor in COPIES:
        src = config.CLEAN / name
        dst = KEEP / name
        if not src.exists():
            fail(f"{src} is missing. This script copies it; it cannot recreate it.")
        new = src.stat().st_size
        old = dst.stat().st_size if dst.exists() else 0
        if new < floor:
            fail(f"{src} is {new} bytes, below the {floor}-byte floor for {what}. "
                 f"Refusing to copy what looks like a truncated file.")
        # A backup is only worth having if it cannot be quietly emptied. Shrinking
        # is the one direction that is almost never intended.
        if old and new < old and not args.force:
            fail(f"{name} would shrink {old} -> {new} bytes. If that is deliberate, "
                 f"rerun with --force.")
        shutil.copy2(src, dst)
        print(f"  {name:<16} {old:>7} -> {new:>7} bytes   {what}")
        lines.append(f"{name:<16} {new:>8} bytes   {what}")

    # The split, read through config so the file format is the loader's problem
    # and no secret is ever in scope.
    cfg = config.load()
    in_sample = sorted(cfg.all_symbols)
    unseen = sorted(cfg.out_of_sample)
    if not in_sample:
        fail("config.load() returned an EMPTY in-sample universe. "
             "Check config.local.toml before letting this overwrite the backup.")
    overlap = sorted(set(in_sample) & set(unseen))
    if overlap:
        fail(f"{len(overlap)} symbols are in BOTH the in-sample set and the "
             f"holdout: {', '.join(overlap[:5])}... The split is broken; fix it "
             f"before backing it up.")

    dst = KEEP / "universe.json"
    was = {}
    if dst.exists():
        was = json.loads(dst.read_text())
    for label, now_, before in (("in_sample", in_sample, was.get("in_sample", [])),
                                ("unseen", unseen, was.get("unseen", []))):
        if before and len(now_) < len(before) and not args.force:
            fail(f"the {label} universe would shrink {len(before)} -> {len(now_)} "
                 f"symbols. If that is deliberate, rerun with --force.")
        print(f"  {label:<16} {len(before):>7} -> {len(now_):>7} symbols")
        lines.append(f"{label:<16} {len(now_):>8} symbols")

    dst.write_text(json.dumps(
        {"written": config.now_local().isoformat(timespec="seconds"),
         "note": "The in-sample/holdout split. Restore into [universe] in "
                 "config.local.toml. Never tune on `unseen`.",
         "in_sample": in_sample, "unseen": unseen,
         "excluded": {k: v for k, v in sorted(config.EXCLUDED.items())},
         "class_assigned": sorted(config.CLASS_ASSIGNED)},
        indent=1) + "\n")

    (KEEP / "MANIFEST.txt").write_text("\n".join(lines) + "\n")
    print(f"\n{len(COPIES) + 1} files backed up. Commit them:  "
          f"git add data/keep && git commit")


if __name__ == "__main__":
    main()

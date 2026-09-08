"""Copy the files that CANNOT be rebuilt into the repo, where git keeps them.

Almost everything here is reproducible. `python -m scripts.clean_data` rebuilds
CLEAN from the raw store, `scripts.refresh` rebuilds every cache and the
dashboard, and the raw store itself can be refetched from Kite. Four things are
not in that set, and each of them was one `rm` from gone:

  levels.json    hand-drawn support and resistance. Nothing regenerates it.
  accepted.txt   the list scripts.screen_universe last wrote -- batch 2, the
                 504 names it accepted on 2026-09-02, as its own header says.
                 In principle a rerun rewrites it, but against a NEWER
                 instrument dump and a newer data snapshot, so the rerun
                 redefines the batch rather than restoring it.
  the universe   [universe] symbols / extended / holdout / unseen in
                 config.local.toml, merged and with EXCLUDED applied: the ONE
                 list every published number is quoted on. config.local.toml
                 is gitignored -- correctly, because the same file holds the
                 Kite api_key and api_secret -- so the universe shared a
                 hiding place with the secrets and inherited their absence
                 from git.
  levels' shape  written alongside as a count, so a truncated restore is obvious.

Until 2026-09-07 this script described accepted.txt as "the 399 screened
names" and refused to run when "the split is broken" -- an in-sample/holdout
split that config.merged had replaced on 2026-09-03 and that was deleted
outright on 2026-09-07 (owner's decision 5; the 399 list survives at commit
2a3e4d2). The guard is now about the current universe: it must be non-empty
and must not shrink without --force.

Nothing secret is copied. The universe is read through config.load() and
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
    ("accepted.txt", "batch 2: the 504 names screen_universe accepted 2026-09-02", 500),
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

    # The universe, read through config so the file format is the loader's
    # problem and no secret is ever in scope.
    cfg = config.load()
    universe = sorted(cfg.merged)
    if not universe:
        fail("config.load() returned an EMPTY universe. Check config.local.toml "
             "before letting this overwrite the backup.")

    dst = KEEP / "universe.json"
    was = {}
    if dst.exists():
        was = json.loads(dst.read_text())
    # Backups written before 2026-09-07 hold the two halves of the old split;
    # together they are the same universe, so the shrink guard still applies.
    before = was.get("universe") or (was.get("in_sample", []) + was.get("unseen", []))
    if before and len(universe) < len(before) and not args.force:
        fail(f"the universe would shrink {len(before)} -> {len(universe)} symbols. "
             f"If that is deliberate, rerun with --force.")
    print(f"  {'universe':<16} {len(before):>7} -> {len(universe):>7} symbols")
    lines.append(f"{'universe':<16} {len(universe):>8} symbols   cfg.merged, EXCLUDED applied")
    lines.append(f"{'excluded':<16} {len(config.EXCLUDED):>8} symbols   config.EXCLUDED")

    dst.write_text(json.dumps(
        {"written": config.now_local().isoformat(timespec="seconds"),
         "note": "The universe every number is quoted on: the four [universe] "
                 "lists in config.local.toml merged, with config.EXCLUDED "
                 "applied. Restore into [universe] unseen (any list works; "
                 "they merge). The in-sample/holdout split was deleted "
                 "2026-09-07.",
         "universe": universe,
         "excluded": {k: v for k, v in sorted(config.EXCLUDED.items())},
         "demergers": {k: v for k, v in sorted(config.DEMERGERS.items())},
         "history_starts": dict(sorted(config.HISTORY_STARTS.items())),
         "class_assigned": sorted(config.CLASS_ASSIGNED)},
        indent=1) + "\n")

    (KEEP / "MANIFEST.txt").write_text("\n".join(lines) + "\n")
    print(f"\n{len(COPIES) + 1} files backed up. Commit them:  "
          f"git add data/keep && git commit")


if __name__ == "__main__":
    main()

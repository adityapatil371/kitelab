"""Signal caches that know what built them.

A cached signal list used to be trusted for one reason: the file existed.

    if path.exists():
        return pickle.loads(path.read_bytes())

It recorded nothing about the universe it was built from, the price files it read, or
the code that produced it. So changing the universe, refetching a symbol or fixing a
strategy left every cache silently wrong, and the only defence was remembering to
delete the right pickles by hand. That defence failed repeatedly on 2026-08-31: a
grid-key format change left factor_analysis dead for hours, and every engine fix in
that session needed a manual decision about which caches were now lies.

A cache now carries a STAMP and is refused when it does not match:

    universe   which symbols it was built over
    data       every price file those symbols read -- size and modification time
    code       the strategy modules' own mtimes

Legacy caches (no stamp) are still readable, because refusing them outright would
break every report until a full rebuild. They warn, loudly, every time.
"""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

from .config import CLEAN

# Caches are DERIVED data, so they belong in CLEAN. They used to be written to
# DATA, which was fine while DATA was the repo's own ./data, and became a hard
# crash the moment DATA started pointing at the shared read-only /data/raw:
#   OSError [Errno 30] Read-only file system: .../signal_cache/EMA_all.pkl
# The price files a stamp fingerprints are read from CLEAN too, because that is
# where frames.py reads them from.
CACHE = CLEAN / "signal_cache"
_WARNED: set[str] = set()

# Modules whose contents decide what a trade IS. A change here invalidates every
# cache, which is exactly what happened with each engine fix on 2026-08-31.
_CODE = ["backtest.py", "strategies.py", "darvas.py", "trailing.py", "frames.py",
         "sizing.py", "indicators.py", "levels.py"]


def _digest(parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def stamp(symbols) -> dict:
    """What this cache depends on: the universe, the price files, the strategy code."""
    symbols = sorted(symbols)
    data = []
    for s in symbols:
        for suffix in ("day", "15minute", "30minute"):
            p = CLEAN / f"{s}_{suffix}.parquet"
            if p.exists():
                st = p.stat()
                data.append((p.name, st.st_size, st.st_mtime_ns))
    here = Path(__file__).resolve().parent
    code = [(n, here.joinpath(n).stat().st_mtime_ns)
            for n in _CODE if here.joinpath(n).exists()]
    return {"universe": _digest(symbols), "n_symbols": len(symbols),
            "data": _digest(data), "n_files": len(data), "code": _digest(code)}


def _explain(want: dict, got: dict) -> str:
    if got.get("universe") != want["universe"]:
        return (f"built over a DIFFERENT UNIVERSE "
                f"({got.get('n_symbols', '?')} symbols, now {want['n_symbols']})")
    if got.get("data") != want["data"]:
        return (f"the PRICE FILES have changed since it was built "
                f"({got.get('n_files', '?')} files, now {want['n_files']})")
    if got.get("code") != want["code"]:
        return "the STRATEGY CODE has changed since it was built"
    return "its stamp does not match"


def save(name: str, trades: list[dict], symbols) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{name}.pkl").write_bytes(
        pickle.dumps({"stamp": stamp(symbols), "trades": trades}))


def load(name: str, symbols, allow_legacy: bool = True) -> list[dict] | None:
    """Cached trades, or None if there is no usable cache.

    None means "rebuild me". A legacy cache (written before stamping) is returned
    with a warning rather than refused, so existing reports keep working until the
    next full rebuild stamps everything.
    """
    path = CACHE / f"{name}.pkl"
    if not path.exists():
        return None
    blob = pickle.loads(path.read_bytes())

    if isinstance(blob, list):                      # pre-2026-09-01 format
        if not allow_legacy:
            return None
        if name not in _WARNED:
            print(f"[kitelab] {name}: UNSTAMPED cache -- cannot verify which universe, "
                  "price files or code built it. Rebuild to make it checkable.")
            _WARNED.add(name)
        return blob

    want, got = stamp(symbols), blob.get("stamp", {})
    if got != want:
        if name not in _WARNED:
            print(f"[kitelab] {name}: STALE cache -- {_explain(want, got)}. Rebuilding.")
            _WARNED.add(name)
        return None
    return blob["trades"]


def require(name: str, symbols) -> list[dict]:
    """Cached trades, or a clear stop. For reports, which cannot rebuild for you."""
    trades = load(name, symbols)
    if trades is None:
        raise SystemExit(
            f"\n  {name}: no usable signal cache.\n"
            "  It is missing, or it was built from a different universe, different\n"
            "  price files or different strategy code. Rebuild it with:\n"
            "      python -m scripts.dashboard_data\n")
    return trades


def status(symbols) -> list[tuple[str, str]]:
    """(name, verdict) for every cache on disk. Used by scripts.cache_status."""
    out = []
    want = stamp(symbols)
    for p in sorted(CACHE.glob("*.pkl")):
        blob = pickle.loads(p.read_bytes())
        if isinstance(blob, list):
            out.append((p.stem, f"UNSTAMPED ({len(blob):,} trades)"))
        elif blob.get("stamp") == want:
            out.append((p.stem, f"ok ({len(blob['trades']):,} trades)"))
        else:
            out.append((p.stem, f"STALE -- {_explain(want, blob.get('stamp', {}))}"))
    return out

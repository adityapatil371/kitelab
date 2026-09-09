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
    code       the strategy modules' own CONTENTS (sha256 of the bytes)

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
#
# EVERY producer must be listed, or the guarantee is worthless for the ones that
# are not. holygrail.py was missing until 2026-09-02, and it cost a whole set of
# published numbers: the rules were rewritten at 16:33 and the dashboard built at
# 15:32 kept serving trades from the superseded code, with refresh reporting
# "already current" because the digest it compared could not see the file. The
# three additions below are the rest of that hole -- slippage.py sets the fill
# price baked into every cached trade, and timeframes.py decides which bars a
# multi-timeframe rule is even looking at.
# SUPPORT modules: every producer reads through these, so a change to any of
# them changes every trade, and no Strategy names them. The PRODUCERS are not
# listed here -- kitelab.registry declares which module makes each strategy's
# trades and _code_files() derives them, so a rule that is on the board is in
# the stamp by construction. That is the whole fix for 2026-09-02: the hand-kept
# list is gone, and with it the way to forget an entry.
#
# registry.py ADDED 2026-09-05. It is not a support module by the definition
# above -- no producer reads it -- but it holds the PARAMETER VALUES baked into
# every cached trade: BANDS, SOLO_BAND, ATH_BAND, DARVAS_WINDOWS. Removing the
# EMA band that day changed what `pair`, `e1` and `eath` trade while their cache
# names (EMA_WD, EMA_daily_only, EMA_ath10) stayed identical and no producer file
# was touched, so the stamp would not have moved and refresh would have said
# "already current" over superseded trades. That is the 2026-09-02 failure again
# with a different file in the hole. The cost is that any edit to registry.py --
# a label typo included -- invalidates every cache, which is the safe direction.
_SUPPORT = ["frames.py", "sizing.py", "indicators.py", "slippage.py",
            "levels.py", "strategies.py", "trailing.py", "registry.py",
            "config.py", "screener.py"]

# ACCOUNT modules -- everything that turns a cached trade list into a number on
# the page, and that NO signal cache depends on. ADDED 2026-09-07. The audit
# that day touched portfolio.py, curves.py, validation.py, contracts.py,
# config.py and scripts/dashboard_data.py one at a time on an isolated copy and
# the dashboard digest stayed at 7d6c0c509c7698fc for every one of them; only
# backtest.py moved it. So an edit to the account engine (every CAGR, MAR and
# drawdown cell), the curve metrics (Sharpe, Sortino, exposure), the five
# validation gates, the futures lot table, or the grid axes themselves
# (RISKS, CAPITALS, START_YEARS, the bucket cuts) left `refresh` reporting
# "already current" over superseded numbers -- the 2026-09-02 holygrail.py
# failure again, one layer up.
#
# Two stamps, not one, because the two artefacts depend on different things. A
# signal cache is valid across a portfolio.py edit (it holds trades, not
# returns), and refusing it would force the slow half of every rebuild for
# nothing. dashboard.json is not, so it is stamped with `account=True` and
# refuses itself when any of these move. config.py is in _SUPPORT rather than
# here because EXCLUDED, DEMERGERS and HISTORY_STARTS change which trades exist;
# screener.py likewise, because timeframes reaches it (tests/test_stamps.py
# walks the build's import graph and fails on any module left out).
_ACCOUNT = ["portfolio.py", "curves.py", "validation.py", "contracts.py",
            "../scripts/dashboard_data.py"]


# ------------------------------------------------------ per-producer code ----
# WHY A CACHE SHOULD NOT CARE ABOUT EVERY OTHER STRATEGY'S CODE (2026-09-09).
#
# `code` was ONE digest over every producer plus _SUPPORT, so a one-line fix to
# holygrail.py invalidated all 76 caches and cost a ~103-minute rebuild to
# reproduce 18 strategies' trades byte for byte. That is the safe direction to
# err in, and it was the right first version -- but it is not free, and the
# cost was being paid on almost every engine edit.
#
# A cache may instead be stamped against the TRANSITIVE IMPORT CLOSURE of the
# module that built it, unioned with _SUPPORT. Two properties make that safe:
#
#   Derived, not hand-kept. The closure is read out of the import statements
#   with ast, so a producer that starts importing a new module is covered the
#   moment it does. This is the 2026-09-02 lesson (a hand-kept list served a
#   day of superseded numbers) applied one level down.
#
#   _SUPPORT STAYS GLOBAL, and that is not a detail. registry.py, strategies.py
#   and trailing.py are in _SUPPORT precisely because NO producer imports them
#   -- registry holds the parameter values baked into every cached trade. Under
#   a closure-only rule they would land in nobody's closure and an edit to them
#   would invalidate nothing, which is the 2026-09-05 hole reopened. Unioning
#   _SUPPORT in shuts it.
#
# What it buys, measured on the board of 2026-09-09:
#   edit holygrail.py     1 of 19 strategies rebuilt (was 19)
#   edit darvas.py        4 of 19
#   edit timeframes.py   11 of 19
#   edit backtest.py     19 of 19 -- every producer imports it, correctly
#   edit any _SUPPORT    19 of 19
#
# `producer=None` still means the old whole-board digest, and that is what the
# dashboard's account stamp uses: the page depends on every strategy, so it
# must refuse itself when any of them moves.


def _imports(path: Path) -> set[str]:
    """kitelab.<name> modules imported by one file, including relative forms.

    Lifted out of tests/test_stamps.py on 2026-09-09, which had walked the
    import graph to CHECK the stamp; the stamp now walks it to BUILD itself,
    and the test imports this so the two can never drift apart.
    """
    import ast
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("kitelab."):
                    out.add(a.name.split(".")[1])
        elif isinstance(node, ast.ImportFrom):
            if node.module == "kitelab":
                out |= {a.name for a in node.names}
            elif node.module and node.module.startswith("kitelab."):
                out.add(node.module.split(".")[1])
            elif node.level and node.module:            # from .x import y
                out.add(node.module.split(".")[0])
            elif node.level and not node.module:        # from . import x, y
                out |= {a.name for a in node.names}
    here = path.resolve().parent
    return {m for m in out if (here / f"{m}.py").exists()}


def reachable_from(script: Path) -> set[str]:
    """Every kitelab module `script` can reach, transitively. Module names."""
    pkg = Path(__file__).resolve().parent
    seen: set[str] = set()
    todo = list(_imports(script))
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(_imports(pkg / f"{m}.py") - seen)
    return seen


def _code_files(producer: str | None = None) -> list[str]:
    """The modules a cache's `code` digest covers.

    `producer=None`  every producer on the board, plus _SUPPORT. The whole-board
                     digest, unchanged since 2026-09-02.
    `producer=X.py`  X's own transitive import closure, plus _SUPPORT. See the
                     block above for why _SUPPORT is unioned in rather than
                     derived, and what the narrowing is worth.

    Imported inside the function, not at module scope: registry imports the
    strategy modules, several of which import this one, and a module-level
    import here would close that loop.
    """
    from . import registry
    if producer is None:
        return sorted(set(registry.modules()) | set(_SUPPORT))
    here = Path(__file__).resolve().parent
    path = here / producer
    if not path.exists():
        raise ValueError(f"no such producer module: {producer}")
    closure = {f"{m}.py" for m in reachable_from(path)} | {producer}
    return sorted(closure | set(_SUPPORT))


def producer_of(name: str) -> str | None:
    """Which module built the cache called `name`, or None if nothing claims it.

    Cache names are f"{strat.cache}_{suffix}" (suffix is the universe: all,
    hold, 101). LONGEST PREFIX WINS, so EMA_MD_all resolves to the strategy
    whose cache is EMA_MD and not to the one whose cache is EMA. An orphan --
    a pickle left behind by a strategy that has since been deleted -- matches
    nothing and gets None, which means the WHOLE-BOARD digest: an unclaimed
    cache is held to the strictest standard, not the loosest.
    """
    from . import registry
    best = None
    for s in registry.REGISTRY:
        if name == s.cache or name.startswith(s.cache + "_"):
            if best is None or len(s.cache) > len(best[0]):
                best = (s.cache, s.module)
    return best[1] if best else None


# ---------------------------------------------------------- code identity ----
# WHY THE CODE DIGEST READS BYTES AND NOT TIMESTAMPS (2026-09-09).
#
# This used to hash each module's st_mtime_ns. That is not a property of the
# CODE, it is a property of the FILESYSTEM, and the two part company routinely:
#
#   git switch, git checkout, git stash, git pull, and a fast-forward merge all
#   REWRITE working-tree files. Contents land exactly where they started and
#   every timestamp is new.
#
# It happened for real on 2026-09-09. Merging a finished branch into main moved
# through `git switch main` (28 files rewritten backwards) and then a
# fast-forward (the same 28 rewritten forwards). `git diff` between the commit
# that built the caches and the tree afterwards was two lines, both of them
# timestamps inside data/keep -- no code at all. All 19 live caches nevertheless
# went stale at once and `refresh --check` reported "the STRATEGY CODE has
# changed since they were built", which was simply false. The cost of believing
# it would have been a 103-minute rebuild that reproduced the numbers exactly.
#
# The old timestamps are recorded nowhere, so that damage could not be undone --
# only prevented. Hashing the bytes prevents it: a rewrite that restores the
# same content is now invisible, an edit of one character is not.
#
# The PRICE files deliberately stay on (size, mtime), just below. They are
# 593 MB of parquet, git never touches them, and they are replaced wholesale by
# a refetch rather than edited -- so timestamps tell the truth there and reading
# every byte to learn it would be an absurd price.
def _content(path: Path) -> str:
    """sha256 of a source file's bytes. Survives a checkout; catches an edit."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _digest(parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def stamp(symbols, account: bool = False, producer: str | None = None) -> dict:
    """What this artefact depends on: the universe, the price files, the code.

    `account=False` (signal caches): producer modules + _SUPPORT.
    `account=True` (dashboard.json): those plus _ACCOUNT, so the dashboard
    refuses itself when the account engine, the curve metrics, the validation
    gates or the grid axes move. See _ACCOUNT for the 2026-09-07 measurement.
    `producer="darvas.py"`: that module's import closure + _SUPPORT instead of
    every producer, so one engine's caches survive another engine's edit. The
    narrowing is RECORDED IN THE STAMP, so a narrow stamp can never be read as
    a broad one -- widening a cache later refuses it rather than serving it.

    The two are mutually exclusive: the dashboard depends on every strategy, so
    narrowing its stamp would let it certify itself over superseded trades.

    `symbols` should include every instrument whose price file the artefact
    read -- for the dashboard that means the assets (BITCOIN, GOLD) as well as
    the stock universe, which write_stamp omitted until 2026-09-07: a GOLD
    refetch left the page reporting current.
    """
    if account and producer is not None:
        raise ValueError("the account stamp covers the whole board and cannot be "
                         "narrowed to one producer -- dashboard.json depends on "
                         "every strategy in the grid.")
    symbols = sorted(set(symbols))
    data = []
    for s in symbols:
        for suffix in ("day", "15minute", "30minute"):
            p = CLEAN / f"{s}_{suffix}.parquet"
            if p.exists():
                st = p.stat()
                data.append((p.name, st.st_size, st.st_mtime_ns))
    here = Path(__file__).resolve().parent
    files = _code_files(producer) + (_ACCOUNT if account else [])
    code = [(n, _content(here.joinpath(n).resolve()))
            for n in files if here.joinpath(n).exists()]
    out = {"universe": _digest(symbols), "n_symbols": len(symbols),
           "data": _digest(data), "n_files": len(data), "code": _digest(code),
           "n_code": len(code)}
    if account:
        out["account"] = True
    if producer is not None:
        out["producer"] = producer
    return out


def stamped_files(account: bool = False, producer: str | None = None) -> list[str]:
    """The module paths a stamp covers, relative to kitelab/. For tests that
    assert every module on the numbers path is covered (tests/test_stamps.py)."""
    return sorted(set(_code_files(producer)) | (set(_ACCOUNT) if account else set()))


def _explain(want: dict, got: dict) -> str:
    if got.get("universe") != want["universe"]:
        return (f"built over a DIFFERENT UNIVERSE "
                f"({got.get('n_symbols', '?')} symbols, now {want['n_symbols']})")
    if got.get("data") != want["data"]:
        return (f"the PRICE FILES have changed since it was built "
                f"({got.get('n_files', '?')} files, now {want['n_files']})")
    if got.get("producer") != want.get("producer"):
        return (f"it was stamped against {got.get('producer') or 'the whole board'} "
                f"and is now checked against {want.get('producer') or 'the whole board'}")
    if got.get("code") != want["code"]:
        return (f"the STRATEGY CODE has changed since it was built "
                f"({want.get('n_code', '?')} modules cover it)")
    return "its stamp does not match"


def _narrow(name: str, account: bool) -> str | None:
    """The producer a cache called `name` is stamped against, or None for the
    whole board. RESOLVED FROM THE NAME, so no call site has to pass it and no
    call site can pass the wrong one. account=True is never narrowed."""
    return None if account else producer_of(name)


def save(name: str, trades: list[dict], symbols, account: bool = False) -> None:
    """`account=True` stamps against the wider _ACCOUNT set too -- for a
    cache whose contents depend on the account/validation modules (the
    dashboard's validation record), not just on which trades exist. Added
    2026-09-07: that record was stamped narrowly, so a validation.py edit
    left it served as current."""
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{name}.pkl").write_bytes(pickle.dumps(
        {"stamp": stamp(symbols, account=account, producer=_narrow(name, account)),
         "trades": trades}))


def load(name: str, symbols, allow_legacy: bool = True,
         account: bool = False) -> list[dict] | None:
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

    want = stamp(symbols, account=account, producer=_narrow(name, account))
    got = blob.get("stamp", {})
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
    for p in sorted(CACHE.glob("*.pkl")):
        # Per cache, not once: each is checked against the code ITS OWN producer
        # depends on. Checking every pickle against one whole-board digest would
        # report the other 18 strategies stale after a one-engine edit -- which
        # is what this file did until 2026-09-09, and what the caches themselves
        # then acted on.
        want = stamp(symbols, producer=_narrow(p.stem, account=False))
        blob = pickle.loads(p.read_bytes())
        if isinstance(blob, list):
            out.append((p.stem, f"UNSTAMPED ({len(blob):,} trades)"))
        elif blob.get("stamp") == want:
            out.append((p.stem, f"ok ({len(blob['trades']):,} trades)"))
        else:
            out.append((p.stem, f"STALE -- {_explain(want, blob.get('stamp', {}))}"))
    return out

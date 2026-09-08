"""The local dashboard server. Stdlib only -- no web framework in the project.

    GET  /              the dashboard page
    GET  /api/dashboard the precomputed results (dashboard.json)
    GET  /api/status    whether those results still describe the current universe

Run it with `python -m scripts.dashboard`, then open
http://localhost:8765. Rebuild the numbers it serves with
`python -m scripts.refresh`.

The page is a pure viewer: every control selects among precomputed
backtests, so the server only ever reads one JSON file from disk.

WHY /api/status EXISTS
----------------------
dashboard.json is a snapshot. Nothing in it forced it to describe the universe
you are trading now, and on 2026-09-01 it did not: the page served results built
over 192 stocks, 91 of which had already been excluded for untrustworthy price
data, under a heading that said only "built <date>". A stale dashboard that
looks current is worse than one that admits it is stale.

So dashboard_data writes a small STAMP file beside the big one, and this server
compares it against the live config on every request. The comparison is why the
stamp is a SEPARATE file: dashboard.json is ~87 MB, and re-parsing it per
request to read two fields would turn a 0.1s response into a slow one.
"""
from __future__ import annotations

import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, signals
from .config import CLEAN

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
DATA_PATH = CLEAN / "dashboard.json"
# One curve per line, addressed by the byte offsets in payload["curve_index"].
# The comparison table needs no curves at all, and a detail view needs exactly
# one, so shipping all 83,904 of them was 89% of a 303 MB payload spent on data
# the page would almost never look at.
CURVES_PATH = CLEAN / "dashboard.curves.jsonl"
STAMP_PATH = CLEAN / "dashboard.stamp.json"


def write_stamp(symbols, built: str, inputs: dict | None = None,
                partial: list | None = None, assets=()) -> None:
    """Record what a dashboard.json was built from. Called by
    scripts.dashboard_data after it writes the big file.

    Three things can make the numbers wrong, and all three are recorded: the
    UNIVERSE (which stocks), the PRICE FILES (their size and mtime) and the
    CODE. signals.stamp(account=True) computes that trio over the producer
    modules AND the account/validation modules -- see signals._ACCOUNT for why
    the dashboard needs the wider set (2026-09-07).

    `inputs` is the stamp taken BEFORE the build started. It is passed in
    rather than computed here because a stamp taken after a long build records
    the mtimes of whatever the code is NOW, not what produced the numbers: edit
    backtest.py ten minutes into a rebuild and the file was stamped current
    over stale trades (audit 2026-09-07, B2). scripts.dashboard_data takes the
    stamp first, checks it again at the end, and writes `inputs=None` if the
    two differ, which status() reports as stale.

    `assets` are the non-equity instruments whose price files the build read;
    they were left out of the stamp until 2026-09-07 (B3). `partial` lists
    stages the build skipped (["assets"] for --stocks-only) so status() can say
    "current, assets not built" instead of serving an empty tab as current.
    """
    STAMP_PATH.write_text(json.dumps({
        "built": built,
        "symbols": sorted(symbols),
        "assets": sorted(assets),
        "partial": list(partial or []),
        "inputs": inputs,
    }, indent=2))


_CURVES_CHECK: dict = {}


def _curves_match() -> bool:
    """Does dashboard.curves.jsonl carry the digest dashboard.json recorded?

    Memoised on (size, mtime) of both files so the check costs one read per
    build, not one per curve. A payload without `curves_sha256` (built before
    2026-09-07) is trusted, as before.
    """
    try:
        key = (DATA_PATH.stat().st_mtime_ns, CURVES_PATH.stat().st_mtime_ns,
               CURVES_PATH.stat().st_size)
    except OSError:
        return False
    if _CURVES_CHECK.get("key") != key:
        want = None
        with open(DATA_PATH, "rb") as fh:
            head = fh.read(4_000_000)      # the key sits near the end; fall back to a full read
        m = re.search(rb'"curves_sha256":\s*"([0-9a-f]{64})"', head)
        if m is None:
            m = re.search(rb'"curves_sha256":\s*"([0-9a-f]{64})"', DATA_PATH.read_bytes())
        if m is not None:
            want = m.group(1).decode()
        ok = True
        if want is not None:
            h = hashlib.sha256()
            with open(CURVES_PATH, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            ok = h.hexdigest() == want
        _CURVES_CHECK.update(key=key, ok=ok)
    return bool(_CURVES_CHECK["ok"])


def status() -> dict:
    """Does the dashboard on disk still describe the current universe?

    Returns a verdict the page can render. `stale` true means the numbers being
    served were computed over a different set of stocks than the one configured
    now, so every headline on the page is answering a question you stopped
    asking.
    """
    if not DATA_PATH.exists():
        return {"ok": False, "stale": False, "message": "No dashboard data yet."}
    try:
        now = sorted(config.load().merged)
    except SystemExit as exc:
        return {"ok": False, "stale": True,
                "message": f"Cannot check whether these numbers are current: {exc}"}

    if not STAMP_PATH.exists():
        return {"ok": True, "stale": True, "built": None,
                "message": ("This dashboard carries no record of the universe it "
                            "was built from, so it cannot be checked. Rebuild it "
                            "with: python -m scripts.refresh")}

    stamp = json.loads(STAMP_PATH.read_text())
    was = sorted(stamp.get("symbols", []))
    if was == now:
        # Same stocks. The price files or the strategy code can still have moved
        # underneath the snapshot, which changes every number without changing
        # the universe -- so check those too rather than declaring it current.
        want = signals.stamp(now + list(stamp.get("assets", [])), account=True)
        got = stamp.get("inputs")
        if got is None:
            return {"ok": True, "stale": True, "built": stamp.get("built"),
                    "n_built": len(was), "n_now": len(now), "reason": "unrecorded",
                    "message": ("The universe still matches, but this dashboard "
                                "carries no input stamp -- either it predates "
                                "input tracking, or the code moved while it was "
                                "being built and the build refused to certify "
                                "itself. Rebuild with: python -m scripts.refresh")}
        if not got.get("account"):
            return {"ok": True, "stale": True, "built": stamp.get("built"),
                    "n_built": len(was), "n_now": len(now), "reason": "narrow-stamp",
                    "message": ("This dashboard was stamped against the strategy "
                                "code only; since 2026-09-07 the account and "
                                "validation modules are tracked too, and this "
                                "build cannot be checked against them. Rebuild "
                                "with: python -m scripts.refresh")}
        if got != want:
            why = ("the PRICE DATA has changed" if got.get("data") != want["data"]
                   else "the STRATEGY CODE has changed" if got.get("code") != want["code"]
                   else "its inputs have changed")
            return {"ok": True, "stale": True, "built": stamp.get("built"),
                    "n_built": len(was), "n_now": len(now),
                    "reason": "inputs",
                    "message": (f"These numbers cover the right {len(now)} stocks, "
                                f"but {why} since they were built. Rebuild with: "
                                "python -m scripts.refresh")}
        return {"ok": True, "stale": False, "built": stamp.get("built"),
                "n_built": len(was), "n_now": len(now),
                "partial": list(stamp.get("partial", []))}

    dropped = [s for s in was if s not in set(now)]
    added = [s for s in now if s not in set(was)]
    bits = []
    if dropped:
        bits.append(f"{len(dropped)} stock{'s' if len(dropped) != 1 else ''} "
                    "shown here " + ("have" if len(dropped) != 1 else "has") +
                    " since been removed from your universe")
    if added:
        bits.append(f"{len(added)} stock{'s' if len(added) != 1 else ''} in your "
                    "universe " + ("are" if len(added) != 1 else "is") +
                    " missing from these results")
    return {"ok": True, "stale": True, "built": stamp.get("built"),
            "n_built": len(was), "n_now": len(now),
            "dropped": dropped[:200], "added": added[:200],
            "message": ("These numbers were built over " f"{len(was)} stocks; you "
                        f"now trade {len(now)}. " + "; ".join(bits).capitalize() +
                        ". Rebuild with: python -m scripts.refresh")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # keep the console quiet
        pass

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Never cache. Both files change under the browser -- the page whenever
        # the layout is edited, the data on every rebuild -- and the failure is
        # silent and confusing: a cached PAGE against fresh DATA (or the reverse)
        # renders blank, because the two are only ever in step by version. There
        # is no bandwidth argument for caching either; this serves localhost.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html", "/dashboard"):
                self._send((WEB_ROOT / "dashboard.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif route == "/api/curve":
                # ?at=<offset>&len=<bytes>, both from the index in the payload.
                # Offsets rather than a key lookup keeps the server from holding
                # an 84,000-entry index of its own in memory.
                q = parse_qs(urlparse(self.path).query)
                try:
                    at, length = int(q["at"][0]), int(q["len"][0])
                except (KeyError, ValueError, IndexError):
                    self._send(json.dumps({"error": "need at= and len="}).encode(),
                               "application/json", 400)
                    return
                if not CURVES_PATH.exists():
                    self._send(json.dumps({"error": "no curve file -- rebuild"}).encode(),
                               "application/json", 404)
                    return
                size = CURVES_PATH.stat().st_size
                if not _curves_match():
                    self._send(json.dumps({"error": "curve file is not this "
                                           "dashboard's -- rebuild"}).encode(),
                               "application/json", 409)
                    return
                if at < 0 or length < 0 or at + length > size:
                    self._send(json.dumps({"error": "out of range"}).encode(),
                               "application/json", 400)
                    return
                with open(CURVES_PATH, "rb") as fh:
                    fh.seek(at)
                    self._send(fh.read(length), "application/json")
            elif route == "/api/status":
                self._send(json.dumps(status()).encode(), "application/json")
            elif route == "/api/dashboard":
                data_file = DATA_PATH
                if data_file.exists():
                    self._send(data_file.read_bytes(), "application/json")
                else:
                    self._send(json.dumps({
                        "error": "no data yet -- run: python -m scripts.refresh"
                    }).encode(), "application/json", 404)
            else:
                self._send(json.dumps({"error": "not found"}).encode(),
                           "application/json", 404)
        except Exception as exc:
            self._send(json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode(),
                       "application/json", 500)


def serve(port: int = 8765) -> None:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        # Almost always "address already in use": the dashboard is running in
        # another window. A stack trace here tells the reader nothing useful.
        if exc.errno in (48, 98):            # EADDRINUSE on macOS / Linux
            raise SystemExit(
                f"\n  Port {port} is already in use.\n"
                f"  The dashboard may already be running: http://localhost:{port}\n"
                f"  Or start this one elsewhere:  python -m scripts.dashboard "
                f"--port {port + 1}\n")
        raise
    print(f"\n  dashboard running at http://127.0.0.1:{port}")
    print("  press Ctrl-C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")

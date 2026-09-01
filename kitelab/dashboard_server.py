"""The local dashboard server. Stdlib only -- no web framework in the project.

    GET  /              the dashboard page
    GET  /api/dashboard the precomputed results (dashboard.json)
    GET  /api/status    whether those results still describe the current universe

Run it with `python -m scripts.dashboard`, then open
http://localhost:8765. Rebuild the numbers it serves with
`python -m scripts.dashboard_data`.

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

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .config import CLEAN

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
DATA_PATH = CLEAN / "dashboard.json"
STAMP_PATH = CLEAN / "dashboard.stamp.json"


def write_stamp(symbols, built: str) -> None:
    """Record which universe a dashboard.json was built over. Called by
    scripts.dashboard_data straight after it writes the big file."""
    STAMP_PATH.write_text(json.dumps(
        {"built": built, "symbols": sorted(symbols)}, indent=2))


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
        now = sorted(config.load().all_symbols)
    except SystemExit as exc:
        return {"ok": False, "stale": True,
                "message": f"Cannot check whether these numbers are current: {exc}"}

    if not STAMP_PATH.exists():
        return {"ok": True, "stale": True, "built": None,
                "message": ("This dashboard carries no record of the universe it "
                            "was built from, so it cannot be checked. Rebuild it "
                            "with: python -m scripts.dashboard_data")}

    stamp = json.loads(STAMP_PATH.read_text())
    was = sorted(stamp.get("symbols", []))
    if was == now:
        return {"ok": True, "stale": False, "built": stamp.get("built"),
                "n_built": len(was), "n_now": len(now)}

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
                        ". Rebuild with: python -m scripts.dashboard_data")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # keep the console quiet
        pass

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html", "/dashboard"):
                self._send((WEB_ROOT / "dashboard.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif route == "/api/status":
                self._send(json.dumps(status()).encode(), "application/json")
            elif route == "/api/dashboard":
                data_file = DATA_PATH
                if data_file.exists():
                    self._send(data_file.read_bytes(), "application/json")
                else:
                    self._send(json.dumps({
                        "error": "no data yet -- run: python -m scripts.dashboard_data"
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

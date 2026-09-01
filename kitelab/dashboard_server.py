"""The local dashboard server. Stdlib only -- no web framework in the project.

    GET  /              the dashboard page
    GET  /api/dashboard the precomputed results (data/dashboard.json)

Run it with `python -m scripts.dashboard`, then open
http://localhost:8765. Rebuild the numbers it serves with
`python -m scripts.dashboard_data`.

The page is a pure viewer: every control selects among precomputed
backtests, so the server only ever reads one JSON file from disk.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import CLEAN

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


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
            elif route == "/api/dashboard":
                data_file = CLEAN / "dashboard.json"
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
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  dashboard running at http://127.0.0.1:{port}")
    print("  press Ctrl-C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")

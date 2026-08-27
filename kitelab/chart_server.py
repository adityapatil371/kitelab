"""A tiny local chart server. Stdlib only -- no web framework added to the project.

    GET  /                            the chart page
    GET  /api/symbols                 configured universe
    GET  /api/candles?symbol=&tf=     OHLCV for one symbol/timeframe
    GET  /api/levels?symbol=          saved levels, with touch count and valid_from
    POST /api/levels                  {symbol, price, kind, note}
    POST /api/levels/delete           {symbol, index}

Timestamps: Lightweight Charts renders intraday times in UTC. Our bars are naive IST,
so intraday times are sent as if that wall-clock reading were UTC. The chart then
displays 09:15 for the 09:15 bar instead of shifting it back to 03:45.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import backtest, config, frames, levels
from .config import DATA

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
EPOCH = datetime(1970, 1, 1)
DATE_TIMEFRAMES = {"1d", "1w", "1M"}


@lru_cache(maxsize=64)
def _candles(symbol: str, timeframe: str) -> str:
    frame = frames.load(symbol, timeframe)
    rows, volume = [], []
    for record in frame.itertuples(index=False):
        if timeframe in DATE_TIMEFRAMES:
            stamp = record.ts.strftime("%Y-%m-%d")
        else:
            stamp = int((record.ts.to_pydatetime() - EPOCH).total_seconds())
        rows.append({"time": stamp, "open": record.open, "high": record.high,
                     "low": record.low, "close": record.close})
        volume.append({"time": stamp, "value": int(record.volume),
                       "color": "#26a69a55" if record.close >= record.open else "#ef535055"})
    return json.dumps({"candles": rows, "volume": volume})


@lru_cache(maxsize=64)
def _ema_payload(symbol: str) -> str:
    """The three EMA lines exactly as the backtest computed them -- the weekly and
    monthly values are the FORMING-bar ones, so the chart shows what the strategy saw,
    not the completed-bar lines TradingView draws."""
    signal = backtest.ema_stack_signal(symbol)
    lines = {"daily_ema": [], "weekly_ema": [], "monthly_ema": []}
    for record in signal.itertuples(index=False):
        stamp = record.ts.strftime("%Y-%m-%d")
        for name in lines:
            value = getattr(record, name)
            if value == value:  # not NaN
                lines[name].append({"time": stamp, "value": round(float(value), 2)})
    return json.dumps(lines)


@lru_cache(maxsize=64)
def _trades_payload(symbol: str) -> str:
    trades = backtest.simulate(symbol)
    out = [{
        "entry": t["entry_ts"].strftime("%Y-%m-%d"),
        "exit": t["exit_ts"].strftime("%Y-%m-%d"),
        "entry_price": round(t["entry_price"], 2),
        "exit_price": round(t["exit_price"], 2),
        "stop": round(t["stop"], 2),
        "net": round(t["net_profit"], 2),
        "reason": t["exit_reason"],
        "shares": t["shares"],
    } for t in trades]
    return json.dumps({"trades": out})


class Handler(BaseHTTPRequestHandler):
    cfg = None

    def log_message(self, *args):  # keep the console quiet
        pass

    # -- helpers ----------------------------------------------------------
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self._send(body, "application/json", status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _levels_for(self, symbol: str):
        return levels.describe(symbol, frames.load(symbol, "1d"))

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path

        try:
            if route in ("/", "/index.html"):
                self._send((WEB_ROOT / "chart.html").read_bytes(), "text/html; charset=utf-8")
            elif route == "/dashboard":
                self._send((WEB_ROOT / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            elif route == "/api/dashboard":
                data_file = DATA / "dashboard.json"
                if data_file.exists():
                    self._send(data_file.read_bytes(), "application/json")
                else:
                    self._json({"error": "no data yet -- run: python -m scripts.dashboard_data"}, 404)
            elif route == "/api/symbols":
                self._json({"symbols": self.cfg.all_symbols})
            elif route == "/api/candles":
                symbol = query.get("symbol", [""])[0]
                timeframe = query.get("tf", ["1d"])[0]
                self._json(_candles(symbol, timeframe))
            elif route == "/api/levels":
                self._json({"levels": self._levels_for(query.get("symbol", [""])[0])})
            elif route == "/api/ema":
                self._json(_ema_payload(query.get("symbol", [""])[0]))
            elif route == "/api/trades":
                self._json(_trades_payload(query.get("symbol", [""])[0]))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        try:
            payload = self._body()
            symbol = payload.get("symbol", "")
            if self.path == "/api/levels":
                levels.add(symbol, float(payload["price"]), payload.get("kind", "support"),
                           payload.get("note", ""))
            elif self.path == "/api/levels/delete":
                levels.remove(symbol, int(payload["index"]))
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"levels": self._levels_for(symbol)})
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def serve(port: int = 8765) -> None:
    Handler.cfg = config.load()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  chart running at http://127.0.0.1:{port}")
    print("  click the chart to place a level, press Ctrl-C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")

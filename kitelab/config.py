"""Configuration. Secrets live in config.local.toml, which is gitignored."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.local.toml"


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    symbols: list[str]
    extended: list[str]
    holdout: list[str]
    exchange: str
    start: str
    intraday_start: str
    use_daily_source: bool

    @staticmethod
    def _dedupe(*groups) -> list[str]:
        seen, out = set(), []
        for group in groups:
            for symbol in group:
                if symbol not in seen:
                    seen.add(symbol); out.append(symbol)
        return out

    @property
    def in_sample(self) -> list[str]:
        """Where the parameters were chosen. Results here are not evidence."""
        return self._dedupe(self.symbols, self.extended)

    @property
    def out_of_sample(self) -> list[str]:
        """Stocks no parameter has ever seen."""
        return [s for s in self.holdout if s not in set(self.in_sample)]

    @property
    def all_symbols(self) -> list[str]:
        return self._dedupe(self.symbols, self.extended, self.holdout)

    @property
    def everything(self) -> list[str]:
        """Hand-drawn universe plus the wider list, de-duplicated, order preserved."""
        seen, out = set(), []
        for symbol in [*self.symbols, *self.extended]:
            if symbol not in seen:
                seen.add(symbol); out.append(symbol)
        return out


def load() -> Config:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            "Missing config.local.toml.\n"
            "  cp config.example.toml config.local.toml\n"
            "then edit it and fill in your Kite api_key / api_secret."
        )
    raw = tomllib.loads(CONFIG_PATH.read_text())
    kite = raw.get("kite", {})
    universe = raw.get("universe", {})
    backfill = raw.get("backfill", {})

    for field in ("api_key", "api_secret"):
        value = kite.get(field, "")
        if not value or value.startswith("your_"):
            raise SystemExit(f"config.local.toml: [kite] {field} is not filled in.")

    DATA.mkdir(exist_ok=True)
    return Config(
        api_key=kite["api_key"],
        api_secret=kite["api_secret"],
        symbols=universe.get("symbols", []),
        extended=universe.get("extended", []),
        holdout=universe.get("holdout", []),
        exchange=universe.get("exchange", "NSE"),
        start=backfill.get("start", "2015-01-01"),
        intraday_start=backfill.get("intraday_start",
                                    backfill.get("start", "2015-01-01")),
        use_daily_source=backfill.get("use_daily_source", True),
    )

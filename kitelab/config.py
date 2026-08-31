"""Configuration. Secrets live in config.local.toml, which is gitignored."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.local.toml"

# Stocks removed from EVERY universe on 2026-08-31 because their daily price
# history cannot be trusted. The raw lists live in config.local.toml, which is
# gitignored (it holds the Kite secrets), so the exclusions live HERE instead --
# in version control, each with the measurement that justifies it. Removing a
# stock changes every published number, so it must never be a silent edit.
#
# Two kinds of defect, both found 2026-08-31:
#
#   SEAM     a long trading gap with the price on a completely different level
#            either side. Either the company was restructured and Kite's history
#            is unadjusted, or the symbol was reused. Kite sells no corporate
#            actions, so it cannot be told which, and it cannot be repaired.
#
#   PADDING  zero-volume bars carrying a made-up price in front of the stock's
#            first real trade. frames.drop_untraded_outliers() removes the worst
#            of these, but a history that STARTS with hundreds of invented bars
#            has no usable early period at all.
EXCLUDED: dict[str, str] = {
    "VINEETLAB": "SEAM x2: Rs1,556 -> Rs41 across a 350-day gap (2018-05-23 -> "
                 "2019-05-08), then Rs1,547 -> Rs43 across 769 days. Also carried "
                 "6 zero-volume bars at Rs7.60 that cost the Q/M/W reference "
                 "account a manufactured -Rs43,200.",
    "LANCER":    "SEAM: Rs49.90 -> Rs10.42 across a 1,207-day gap "
                 "(2023-04-28 -> 2026-08-17). Only 249 daily bars in total.",
    "LAGNAM":    "SEAM: Rs99.98 -> Rs35.20 across a 945-day gap "
                 "(2016-02-16 -> 2018-09-18). Also the worst intraday file in the "
                 "universe after SHAHALLOYS: 431 of 40,165 bars non-positive.",
    "PVP":       "SEAM: Rs1.80 -> Rs5.25 across a 632-day gap "
                 "(2019-10-29 -> 2021-07-22).",
    "JMFINANCIL": "PADDING: 191 zero-volume bars at Rs0.14 before the first real "
                  "trade at Rs30.99 (2006-01-02 .. 2006-10-09). They seeded the "
                  "20-day EMA at 0.14 and manufactured a buy signal on the stock's "
                  "first trading day; Darvas took it with a stop of Rs0.14.",
    "TVSSRICHAK": "PADDING: 275 zero-volume bars at Rs39.90 before the first real "
                  "trade at Rs107.65 (2007-02-13). Produces a trade on that exact "
                  "day, worth -Rs5,256 on the EMA list.",
    "SUDARSCHEM": "PADDING: 88 zero-volume bars at Rs4.47 before the first real "
                  "trade at Rs20.27 (2006-05-16). Same first-day trade, "
                  "-Rs1,458 on the EMA list.",
}


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
        """De-duplicate, preserve order, and drop everything in EXCLUDED."""
        seen, out = set(), []
        for group in groups:
            for symbol in group:
                if symbol in EXCLUDED or symbol in seen:
                    continue
                seen.add(symbol); out.append(symbol)
        return out

    @property
    def excluded(self) -> dict[str, str]:
        """Symbols dropped from every universe, and why. See EXCLUDED."""
        listed = {*self.symbols, *self.extended, *self.holdout}
        return {s: why for s, why in EXCLUDED.items() if s in listed}

    @property
    def in_sample(self) -> list[str]:
        """Where the parameters were chosen. Results here are not evidence."""
        return self._dedupe(self.symbols, self.extended)

    @property
    def out_of_sample(self) -> list[str]:
        """Stocks no parameter has ever seen."""
        inside = set(self.in_sample)
        return [s for s in self.holdout if s not in EXCLUDED and s not in inside]

    @property
    def all_symbols(self) -> list[str]:
        return self._dedupe(self.symbols, self.extended, self.holdout)

    @property
    def everything(self) -> list[str]:
        """Hand-drawn universe plus the wider list, de-duplicated, order preserved."""
        return self._dedupe(self.symbols, self.extended)


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

"""Configuration. Secrets live in config.local.toml, which is gitignored."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def _resolve(env_var: str, kind: str, fallback: Path) -> Path:
    """Where the data lives, found rather than assumed.

    An environment variable always wins. Otherwise take the first candidate that
    actually exists: the container mounts /data/{raw,clean}/kitelab, the laptop
    keeps the same two under $HOME. Hard-coding the container path meant every
    direct entry point died on the laptop --

        OSError [Errno 30] Read-only file system: '/data'

    -- because CLEAN had no fallback at all and load() tries to create it. Only
    run_dashboard.sh worked, and only because it exports the variables first.
    """
    chosen = os.environ.get(env_var)
    if chosen:
        return Path(chosen)
    for candidate in (Path(f"/data/{kind}/kitelab"),
                      Path.home() / "data" / kind / "kitelab"):
        if candidate.is_dir():
            return candidate
    return fallback


# Where the RAW parquet files live. READ-ONLY: nothing in this project may write
# into it.
DATA = _resolve("KITELAB_DATA_DIR", "raw", ROOT / "data")

# Where CLEANED and derived data lives. This one IS writable, and it is the only
# directory the analysis side of this project ever touches. Overridable with
# KITELAB_CLEAN_DIR.
#
# THE RULE, decided 2026-09-01:
#
#   Everything that READS PRICES FOR ANALYSIS reads CLEAN, and nothing else.
#   frames.py, signals.py and screen_universe.py all resolve into it, so every
#   backtest, report and dashboard number comes from the cleaned copies.
#
#   Only four places still touch DATA, each on purpose:
#     fetch.py, fetch_assets.py   ACQUISITION -- they create the raw files
#     clean_data.py               reads raw, writes clean. That is its whole job.
#     data_audit.py               audits the raw downloads; auditing the cleaned
#                                 copies would be marking its own homework.
#     screen_universe.py          reads the instrument DUMP (metadata, not prices)
#
# Switching the readers over changed no results: frames.sanitise() already ran on
# every read, so CLEAN holds exactly what the backtests were trading on. Checked
# on 2026-09-01 across 107 symbols x 6 timeframes -- 632 frames, 0 differences.
#
# CLEAN is rebuildable from DATA with `python -m scripts.clean_data`, with ONE
# exception: levels.json is hand-drawn and cannot be regenerated. Back it up.
CLEAN = _resolve("KITELAB_CLEAN_DIR", "clean", ROOT / "data-clean")

CONFIG_PATH = ROOT / "config.local.toml"

# Everything user-facing is stamped in IST. The machine's own clock is not a safe
# default: this project is developed in a UTC container against a laptop on IST,
# so a dashboard built at 23:21 IST announced itself as "17:51" and the two
# disagreed by five and a half hours. NSE and MCX both trade on IST, so it is the
# right zone regardless of where the code runs.
TIMEZONE = "Asia/Kolkata"


def now_local():
    """The current time in the market's timezone, as a pandas Timestamp."""
    import pandas as pd
    return pd.Timestamp.now(tz=TIMEZONE)

# The five stocks of the class assignment, charted individually on the dashboard
# and compared timeframe by timeframe. This is NOT the tradeable universe: a
# stock can be assigned coursework and still fail the quality gate. HYUNDAI does
# exactly that -- listed October 2024, 1.8 years of history, excluded from every
# backtest below, but still one of the five the dashboard has to draw.
#
# It lives here because two very different programs need it: the dashboard,
# which charts them individually, and clean_data, which has to know that their
# price files belong in the working set. While it lived in the timeframe module
# clean_data did not know, so the 2026-09-01 tidy-up deleted HYUNDAI's cleaned
# files and the dashboard rebuild died on "No 15-minute data for HYUNDAI".
CLASS_ASSIGNED = ["HAL", "HINDZINC", "HYUNDAI", "IRFC", "INDHOTEL"]

# The cached Kite access token, overridable with KITELAB_TOKEN_PATH. The
# default stays at the original ./data location and deliberately does NOT
# follow DATA: DATA now often points at a shared, read-only directory, and this
# file has to be written on every login.
TOKEN_PATH = Path(
    os.environ.get("KITELAB_TOKEN_PATH")
    or (ROOT / "data" / ".access_token.json")
)

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

    # ---------------------------------------------------------------------
    # 2026-09-01: the quality gate applied to the EXISTING universe, not just to
    # new candidates. scripts/screen_universe.py states the bar -- five years of
    # history (the Q/M/W stack needs 20 quarterly bars), Rs20 lakh median daily
    # turnover, no seams, no padding, no suspected unadjusted split, structural
    # integrity -- and these 91 of the then-192 did not clear it.
    #
    # Holding half a universe to one standard and half to another is how most of
    # this project's wrong numbers happened. Every stock now passes the same
    # written test, and each line below records which rule it failed.
    #
    # Re-run `python -m scripts.screen_universe --rank` to reproduce these
    # verdicts. Deleting a line puts the stock back.
    "AASTHA": "GATE: history 0.1y / 37 bars < 5.0y",
    "ACL": "GATE: history 3.3y / 819 bars < 5.0y",
    "AHLADA": "GATE: turnover Rs1,261,730 < Rs2,000,000",
    "ALPA": "GATE: turnover Rs1,348,444 < Rs2,000,000",
    "ARCHIES": "GATE: turnover Rs1,427,287 < Rs2,000,000",
    "AYE": "GATE: history 0.5y / 129 bars < 5.0y",
    "BAJAJINDEF": "GATE: history 1.5y / 371 bars < 5.0y",
    "BALAJEE": "GATE: history 1.9y / 484 bars < 5.0y",
    "BATLIBOI": "GATE: history 0.3y / 89 bars < 5.0y",
    "BELLACASA": "GATE: history 1.1y / 273 bars < 5.0y",
    "BHARATCOAL": "GATE: history 0.6y / 149 bars < 5.0y",
    "BLUECLOUDS": "GATE: history 0.0y / 7 bars < 5.0y",
    "BOHRAIND": "GATE: history 3.8y / 786 bars < 5.0y",
    "BRNL": "GATE: turnover Rs1,578,005 < Rs2,000,000",
    "CGPOWER": "GATE: unexplained single-day move over 60%",
    "COCKERILL": "GATE: history 0.3y / 89 bars < 5.0y",
    "CPEDU": "GATE: history 1.0y / 240 bars < 5.0y",
    "DATAPATTNS": "GATE: history 4.7y / 1158 bars < 5.0y",
    "DCMSIL": "GATE: history 0.5y / 128 bars < 5.0y",
    "ELITECON": "GATE: history 0.3y / 89 bars < 5.0y",
    "ELPROINTL": "GATE: history 0.3y / 89 bars < 5.0y",
    "EMPOWER": "GATE: history 0.3y / 89 bars < 5.0y",
    "ENRIN": "GATE: history 1.2y / 294 bars < 5.0y",
    "ESSARSHPNG": "GATE: turnover Rs884,992 < Rs2,000,000",
    "FINKURVE": "GATE: history 0.9y / 222 bars < 5.0y",
    "FMNL": "GATE: turnover Rs415,988 < Rs2,000,000",
    "GANDHAR": "GATE: history 2.7y / 679 bars < 5.0y",
    "GOCOLORS": "GATE: history 4.7y / 1176 bars < 5.0y",
    "GODAVARIB": "GATE: history 1.8y / 451 bars < 5.0y",
    "GUJAPOLLO": "GATE: turnover Rs1,029,123 < Rs2,000,000",
    "HAMPTON": "GATE: history 0.0y / 7 bars < 5.0y",
    "HEALTHX": "GATE: turnover Rs1,500,439 < Rs2,000,000",
    "HEXT": "GATE: history 1.5y / 373 bars < 5.0y",
    "HMAAGRO": "GATE: history 3.1y / 781 bars < 5.0y",
    "HYUNDAI": "GATE: history 1.8y / 457 bars < 5.0y",
    "INDOTHAI": "GATE: turnover Rs307,328 < Rs2,000,000",
    "IPRINGLTD": "GATE: history 0.0y / 7 bars < 5.0y",
    "JASH": "GATE: suspected unadjusted split/bonus",
    "JAYAGROGN": "GATE: turnover Rs1,623,568 < Rs2,000,000",
    "JPOLYINVST": "GATE: turnover Rs512,836 < Rs2,000,000",
    "KAMAHOLD": "GATE: history 0.3y / 89 bars < 5.0y",
    "KAMANWALA": "GATE: history 0.0y / 7 bars < 5.0y",
    "LANDMARK": "GATE: history 3.7y / 910 bars < 5.0y",
    "LEENEE": "GATE: history 0.0y / 7 bars < 5.0y",
    "MAMATA": "GATE: history 1.7y / 412 bars < 5.0y",
    "MANAKALUCO": "GATE: turnover Rs365,630 < Rs2,000,000",
    "MANOMAY": "GATE: history 3.6y / 885 bars < 5.0y",
    "METROBRAND": "GATE: history 4.7y / 1160 bars < 5.0y",
    "METROGLOBL": "GATE: history 0.3y / 89 bars < 5.0y",
    "MSL": "GATE: history 0.0y / 6 bars < 5.0y",
    "NATCAPSUQ": "GATE: history 1.5y / 376 bars < 5.0y",
    "NBIFIN": "GATE: turnover Rs409,068 < Rs2,000,000",
    "NDGL": "GATE: turnover Rs277,024 < Rs2,000,000",
    "NEPHROPLUS": "GATE: history 0.7y / 170 bars < 5.0y",
    "NEXTMEDIA": "GATE: turnover Rs127,317 < Rs2,000,000",
    "NIRLON": "GATE: history 0.3y / 89 bars < 5.0y",
    "NYKAA": "GATE: history 4.8y / 1189 bars < 5.0y",
    "ORIENTALTL": "GATE: turnover Rs361,962 < Rs2,000,000",
    "ORIENTPPR": "GATE: suspected unadjusted split/bonus",
    "OSWALAGRO": "GATE: turnover Rs1,746,459 < Rs2,000,000",
    "PILITA": "GATE: turnover Rs938,110 < Rs2,000,000",
    "PNB": "GATE: suspected unadjusted split/bonus",
    "POLYSPIN": "GATE: history 0.0y / 7 bars < 5.0y",
    "PRABHA": "GATE: history 1.4y / 355 bars < 5.0y",
    "RAMBHAJO": "GATE: history 0.2y / 40 bars < 5.0y",
    "RANEHOLDIN": "GATE: turnover Rs1,857,838 < Rs2,000,000",
    "SAGARDEEP": "GATE: turnover Rs476,344 < Rs2,000,000",
    "SAPPHIRE": "GATE: history 4.8y / 1183 bars < 5.0y",
    "SBFC": "GATE: history 3.0y / 751 bars < 5.0y",
    "SHAHALLOYS": "GATE: turnover Rs223,701 < Rs2,000,000",
    "SMARTWORKS": "GATE: history 1.1y / 274 bars < 5.0y",
    "SUNLOC": "GATE: history 0.0y / 6 bars < 5.0y",
    "SURANAT&P": "GATE: turnover Rs302,982 < Rs2,000,000",
    "SWARAJ": "GATE: history 4.4y / 1044 bars < 5.0y",
    "SYNCOMF": "GATE: history 3.8y / 935 bars < 5.0y",
    "SYRMA": "GATE: history 4.0y / 991 bars < 5.0y",
    "TATACAP": "GATE: history 0.9y / 215 bars < 5.0y",
    "TCI": "GATE: unexplained single-day move over 60%",
    "TERASOFT": "GATE: turnover Rs1,329,218 < Rs2,000,000",
    "TGVSL": "GATE: history 0.0y / 7 bars < 5.0y",
    "TMCV": "GATE: history 0.8y / 195 bars < 5.0y",
    "TRANSCOR": "GATE: history 0.0y / 7 bars < 5.0y",
    "TURTLEMINT": "GATE: history 0.2y / 42 bars < 5.0y",
    "UNITECH": "GATE: suspected unadjusted split/bonus",
    "VHL": "GATE: turnover Rs426,748 < Rs2,000,000",
    "VINCOFE": "GATE: history 1.9y / 459 bars < 5.0y",
    "VOITHPAPR": "GATE: history 0.0y / 7 bars < 5.0y",
    "WAKEFIT": "GATE: history 0.7y / 172 bars < 5.0y",
    "WEALTH": "GATE: turnover Rs749,354 < Rs2,000,000",
    "XPROINDIA": "GATE: turnover Rs359,680 < Rs2,000,000",
    "XTGLOBAL": "GATE: history 1.9y / 482 bars < 5.0y",
}


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    symbols: list[str]
    extended: list[str]
    holdout: list[str]
    unseen: list[str]
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
        listed = {*self.symbols, *self.extended, *self.holdout, *self.unseen}
        return {s: why for s, why in EXCLUDED.items() if s in listed}

    @property
    def in_sample(self) -> list[str]:
        """Where the parameters were chosen. Results here are not evidence."""
        return self._dedupe(self.symbols, self.extended)

    # THE SPLIT, decided 2026-09-01, corrected 2026-09-02.
    #
    # THESE 101 ARE IN-SAMPLE. Every parameter in this project -- the 2% band,
    # 20/10 and 55/20, the weekly gate, the risk levels -- was chosen by looking
    # at them. A set that has been fitted on cannot serve as a holdout however it
    # is labelled, so no result measured here is evidence that the rules work.
    #
    # THE NEW NAMES ARE THE HOLDOUT. When the raw store grows past 3,000 symbols
    # and the universe widens, the added stocks are the only ones nothing has
    # ever been tuned on. That is where the rules get their first honest test.
    #
    # Two things must hold, or the split is worthless:
    #   - the new names go through scripts.screen_universe unchanged, so they
    #     face the same quality gate and are not filtered by hindsight;
    #   - nothing gets tuned on them. Once a parameter is chosen by looking at
    #     the holdout it stops being one, and there is no second.
    #
    # The dashboard's in-sample / holdout universes were removed on 2026-09-01
    # because a 44/57 split of stocks that had all been looked at measured
    # nothing. in_sample and out_of_sample below are what the real split should
    # be rebuilt from once the new names land.

    @property
    def out_of_sample(self) -> list[str]:
        """Stocks no parameter has ever seen.

        NOT `holdout`. That list was drawn on 2026-08-23 and folded into
        all_symbols the same day, so every parameter since has been chosen with
        it in view -- it is in-sample and has been all along. The name is kept
        because config.local.toml still uses it and renaming a key silently
        empties a universe.

        `unseen` is the real one: the 399 that passed screen_universe on
        2026-09-02 and have never been looked at. Anything already inside the
        in-sample set is dropped, so the two can never overlap however the
        config is edited.
        """
        inside = set(self.in_sample) | set(self.holdout)
        return [s for s in self.unseen if s not in EXCLUDED and s not in inside]

    @property
    def all_symbols(self) -> list[str]:
        return self._dedupe(self.symbols, self.extended, self.holdout)

    @property
    def everything(self) -> list[str]:
        """Hand-drawn universe plus the wider list, de-duplicated, order preserved."""
        return self._dedupe(self.symbols, self.extended)


def require_secrets(cfg: Config) -> Config:
    """Stop unless real Kite credentials are present.

    Called by the programs that actually reach the network -- login, backfill and
    fetch_assets -- and by nothing else. Fetching new data is the only thing in
    this project that needs an account.
    """
    for field, env_var in (("api_key", "KITE_API_KEY"),
                           ("api_secret", "KITE_API_SECRET")):
        value = getattr(cfg, field)
        if not value or value.startswith("your_"):
            raise SystemExit(
                f"\n  {field} is not set, and fetching data needs it.\n"
                f"  Either export {env_var}, or fill in [kite] {field} in "
                f"config.local.toml.\n"
                f"  Nothing else in this project needs it -- the dashboard, the\n"
                f"  reports and the cache checker all run without credentials.\n")
    return cfg


def load() -> Config:
    """The configuration.

    This does NOT require Kite credentials. Thirty-one scripts call it and only
    one file, kitelab/auth.py, ever reads api_key or api_secret -- everything
    else wants the universe, the exchange and the date range, none of which is
    a secret. Demanding a key here meant the dashboard, the reports and the
    cache checker all refused to run on a machine that simply had not sourced
    its secrets, and it caused a real failure: the dashboard's freshness check
    hit the missing-key exit and reported "not stale", announcing that it had
    checked when it had not looked at all.

    The programs that genuinely talk to Kite call require_secrets() below, so
    the demand sits where the need is.
    """
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

    # The environment wins; config.local.toml is only the fallback.
    api_key = os.environ.get("KITE_API_KEY") or kite.get("api_key", "")
    api_secret = os.environ.get("KITE_API_SECRET") or kite.get("api_secret", "")


    # DATA is read-only when it points at the shared raw directory, so only create
    # it when it is the repo's own ./data fallback and does not exist yet.
    if not DATA.is_dir():
        DATA.mkdir(parents=True, exist_ok=True)
    CLEAN.mkdir(parents=True, exist_ok=True)
    return Config(
        api_key=api_key,
        api_secret=api_secret,
        symbols=universe.get("symbols", []),
        extended=universe.get("extended", []),
        holdout=universe.get("holdout", []),
        unseen=universe.get("unseen", []),
        exchange=universe.get("exchange", "NSE"),
        start=backfill.get("start", "2015-01-01"),
        intraday_start=backfill.get("intraday_start",
                                    backfill.get("start", "2015-01-01")),
        use_daily_source=backfill.get("use_daily_source", True),
    )

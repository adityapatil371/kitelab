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
#            either side: a symbol reused, or a suspension that outlasted any
#            position. Since 2026-09-07 frames.sanitise() CUTS history at the
#            last gap over frames.LISTING_BREAK_DAYS instead of trading across
#            it, so a seam is no longer a reason to refuse a stock -- these
#            four entries predate that rule and stay only because removing an
#            exclusion changes the universe, which is the owner's call. (The
#            2026-08-31 text said "Kite sells no corporate actions, so it
#            cannot be told which". Half right: Kite DOES adjust splits and
#            bonuses at serve time -- HAL 2:1 on 2023-07-27/28 reads 1926.50
#            -> 1964.50, BPCL's 2024-06-21 bonus 288.95 -> 283.85, NESTLEIND
#            1:10 on 2024-01-05 1355.8 -> 1333.2 with volume adjusted too --
#            and does NOT adjust demergers, which is what DEMERGERS below is
#            for.)
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
    # turnover, no seams, no padding, no suspected unadjusted split (a rule
    # retired 2026-09-07 -- Kite adjusts splits; see DEMERGERS), structural
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
    # 2026-09-07: the four "suspected unadjusted split/bonus" verdicts re-read
    # against the files, now that Kite is known to adjust splits and bonuses.
    # Not one of them was a split. ORIENTPPR's was a demerger and has moved to
    # DEMERGERS; the other three keep their exclusion with the reason corrected.
    "JASH": "SYMBOL REUSED: 2015-01-01 .. 2017-04-25 carries a tape with a median "
            "19.3m shares and Rs1.15bn a day, which cannot be Jash Engineering "
            "(an SME that listed 2017-09-28; its own tape from 2017-10-11 runs "
            "24-48k shares a day). The -49.5% on 2017-10-11 is the seam between "
            "the two instruments across a 169-day gap -- under "
            "frames.LISTING_BREAK_DAYS, so the break rule does not cut it, and "
            "the post-seam turnover is far below the gate anyway.",
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
    "OSWALAGRO": "GATE: turnover Rs1,746,459 < Rs2,000,000",
    "PILITA": "GATE: turnover Rs938,110 < Rs2,000,000",
    "PNB": "NO DATA DEFECT (kept out pending re-screen): the +46.2% on 2017-10-25 "
           "(130.51 -> 190.81, 166.5m shares, 25.7x the 60-day median) is the "
           "PSU-bank recapitalisation announcement, a real move that the split "
           "gate misread as a 3:2 ratio. Volume and turnover stayed up for the "
           "month after, which a split cannot do. Passes every current gate; "
           "putting it back changes the universe, so that is the owner's call.",
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
    "UNITECH": "NO SPLIT (kept out pending re-screen): the -49.7% on 2008-10-24 "
               "(61.60 -> 31.00 on 6.3x volume, +38% the next session) is the "
               "market-wide crash day on which half the universe fell over 8%, "
               "not a corporate action. The stock is a Rs1-2 penny stock since "
               "2019, and the 2026-09-07 gate rejects it on its own: 2.10 -> 1.25 "
               "on 2019-02-21 (-40.5%) reads as a suspected demerger, and it is "
               "not one -- it is what a Rs2 stock does.",
    "VHL": "GATE: turnover Rs426,748 < Rs2,000,000",
    "VINCOFE": "GATE: history 1.9y / 459 bars < 5.0y",
    "VOITHPAPR": "GATE: history 0.0y / 7 bars < 5.0y",
    "WAKEFIT": "GATE: history 0.7y / 172 bars < 5.0y",
    "WEALTH": "GATE: turnover Rs749,354 < Rs2,000,000",
    "XPROINDIA": "GATE: turnover Rs359,680 < Rs2,000,000",
    "XTGLOBAL": "GATE: history 1.9y / 482 bars < 5.0y",

    # ---------------------------------------------------------------------
    # 2026-09-04: found while adding a bootstrap-derived "Edge (p05)" column
    # to the dashboard. scripts.screen_universe's ordinary() filter excludes
    # tickers ending -SG/-GS (government securities) but not -GB, so five
    # Sovereign Gold Bonds passed the quality gate as if they were equities
    # in the 2026-09-03 widening to 1004 stocks. A bond's price barely moves,
    # so the EMA stack's risk-based sizer computes a near-zero risk_taken
    # against it -- one SGB trade risked Rs14.40 and made Rs16,762, an R
    # multiple of 1164. Compounded through kitelab.validation's bootstrap,
    # that single trade alone pushed the 5th-percentile CAGR for EVERY EMA
    # band (not just the unfiltered one) into the hundreds of millions of
    # percent. Not a math bug -- fixed-fractional compounding of a genuine
    # 1164R trade does that -- the bug is that a bond was ever priced as a
    # stock. screen_universe.ordinary() now excludes -GB$ too.
    "SGBAUG28V-GB": "WRONG INSTRUMENT TYPE: Sovereign Gold Bond, not equity.",
    "SGBJUL28IV-GB": "WRONG INSTRUMENT TYPE: Sovereign Gold Bond, not equity.",
    "SGBMAY28-GB": "WRONG INSTRUMENT TYPE: Sovereign Gold Bond, not equity.",
    "SGBMAY29I-GB": "WRONG INSTRUMENT TYPE: Sovereign Gold Bond, not equity.",
    "SGBMR29XII-GB": "WRONG INSTRUMENT TYPE: Sovereign Gold Bond, not equity.",
}

# Demerger EX-DATES. Kite adjusts splits and bonuses at serve time (see the
# HAL / BPCL / NESTLEIND readings above) but NOT demergers: on the ex-date the
# parent's close drops by the value that left, and the daily file records it
# as a crash. frames.history_start() treats each date here as a listing break
# -- history restarts on that date -- because the price before it is the price
# of a different company.
#
# Found 2026-09-07 by scanning every daily file for a close-to-close drop over
# 30% that was not a market-wide day (frames.market_wide_days: 2008-01-21/22,
# October 2008, 2015-08-24, 2020-03-12/23, 2024-06-04 are the crash days, and
# on none of the dates below did more than 5% of the universe fall over 8%).
# Every date was read back against the file before it went in; the figures
# are previous close -> that day's open (the gap) and close.
#
# The rule for ADDING one: verify the drop in the file, verify the day is not
# market-wide, then record it here with the numbers. A date that is wrong
# silently deletes history, so this is not a list to guess at.
DEMERGERS: dict[str, list[str]] = {
    "SIEMENS":    ["2025-04-07"],  # 3754.25 -> 2450.00 (-34.7% gap), close 2812.45; Siemens Energy India
    "RAYMOND":    ["2024-07-11",   # 1912.50 -> 1161.10 (-39.3%), close 1219.20; Raymond Lifestyle
                   "2025-05-14"],  # 953.00 -> 525.00 (-44.9%), close 551.20; Raymond Realty
    "ABFRL":      ["2025-05-22"],  # 203.55 -> 98.00 (-51.9%), close 89.85, 14.2x vol; AB Lifestyle Brands
    "ABREL":      ["2019-10-11"],  # 882.20 -> 390.00 (-55.8%), close 393.85; cement business to UltraTech
    "ARVIND":     ["2018-11-28"],  # 248.85 -> 109.45 (-56.0%), close 108.60, 36x vol; Arvind Fashions + Anup
    "TATACHEM":   ["2020-03-04"],  # 719.65 -> 315.00 (-56.2%), close 314.95; consumer business to Tata Consumer
    "ADANIENT":   ["2015-06-03"],  # 183.20 -> close 106.40 (-41.9%; the 555.80 open is a pre-open print); Adani Ports / Power / Transmission
    "VEDL":       ["2026-04-30"],  # 404.90 -> 289.50 (-28.5%), close 271.55 (-32.9%); Vedanta demerger
    "TRIVENI":    ["2026-07-22"],  # 471.50 -> 289.95 (-38.5%), close 275.50 (-41.6%)
    "IIFL":       ["2019-05-30"],  # 291.05 -> 203.95 (-29.9%), close 193.80 (-33.4%), ordinary volume; IIFL Wealth + IIFL Securities
    "TATACOMM":   ["2019-09-17"],  # 426.60 -> 265.00 (-37.9%), close 278.25 (-34.8%), ordinary volume; surplus land to Hemisphere Properties
    "NIITLTD":    ["2023-06-08"],  # 173.95 -> 92.15 (-47.0%), close 96.75 (-44.4%); NIIT Learning Systems
    "THOMASCOOK": ["2019-12-05"],  # 116.70 -> 69.90 (-40.1%), close 66.45 (-43.1%); Quess Corp stake
    # Was EXCLUDED as "suspected unadjusted split/bonus" until 2026-09-07. A
    # split halves the price and doubles the share count; here the price
    # halved (104.45 -> 55.05, close 52.30) while median daily volume FELL
    # from 726k to 480k and turnover to a third -- value left the company.
    # Orient Electric was carved out of Orient Paper on this ex-date.
    "ORIENTPPR":  ["2018-01-11"],
}

# Series that are continuous but WRONG before a date, with no gap or ex-date
# to hang the cut on. The bars before the date are dropped on load.
#
# HINDPETRO, found 2026-09-07 (audit): its 2013 file has a high/low ratio of
# 14.6x against BPCL's 2.0x and 20 sessions with a move over 15% against
# BPCL's none, while its daily return correlation with BPCL stays 0.78 -- a
# series that TRACKS its peer with the moves amplified several-fold, at a
# price level (Rs2.25 in August 2013) the stock never traded at. The per-year
# sigma is 3.4x its liquidity bucket's median in 2013 and 4.1x in 2008; from
# 2015 it is within 1.2x every year. The audit brief quoted 10.9x and 23
# sessions from the raw file; the cleaned file gives 14.6x and 20. Same
# conclusion either way.
HISTORY_STARTS: dict[str, str] = {
    "HINDPETRO": "2015-01-01",
}


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    # The four [universe] lists in config.local.toml. They are the BATCHES the
    # store was fetched in, not a split: `symbols` the class-assigned five,
    # `extended` the 2026-08 additions, `holdout` the 150 drawn on 2026-08-23,
    # `unseen` the 399 screened on 2026-09-02 plus the 504 of batch 2. The key
    # names are historical -- renaming one silently empties a universe -- and
    # nothing reads them apart from merged() and excluded(). The
    # in_sample / out_of_sample properties that used to split them were
    # deleted on 2026-09-07 (owner's decision 5) along with
    # scripts.universe_bias, the only thing that read them; the 399 list
    # survives at commit 2a3e4d2.
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
    def merged(self) -> list[str]:
        """Every screened stock, one universe. THIS is what results are quoted on.

        Decided 2026-09-03, replacing the 101/399 in-sample/holdout split.

        WHY THE SPLIT WENT. Train/test is a tool for FITTED models -- it catches a
        model that memorised its training rows. These rules are not fitted that
        way. Their shapes were taught in class before this repo read a single
        candle: the 20 EMA, the M/W/D stack, ADX>25 into a pullback, Donchian
        20-10 and 55-20. Holding 399 stocks back from a pre-specified rule buys
        no validity, it only costs power -- and it cost a great deal, because
        every in-sample number was then measured on 101 stocks that turned out
        to be a list of winners. A wider deck fixes that directly.

        WHAT WAS ACTUALLY FITTED HERE, which is a shorter list than the old
        handover's "every parameter" and decides which control is needed: the 2%
        band, the weekly gate added to the Turtle on 2026-09-01, and -- the real
        one -- the choice of WHICH of 24 variants to headline. Everything else is
        class-given or swept as a grid axis.

        SO THE EXPOSURE IS MULTIPLE TESTING, NOT CONTAMINATION, and a holdout is
        a blunt instrument against it: it spends 80% of the data to control for
        something a bootstrap controls for on all of it. scripts.universe_bias
        (deleted 2026-09-07 with the split it measured) found the exposure
        directly -- 13 different variants won across 25 fresh draws. The
        replacements are the tests traders actually run: a trade bootstrap,
        regime splits, parameter plateaus, worst-draw baskets. Every one of
        them is stronger on 500 stocks than on 101.

        WHAT MERGING DOES NOT FIX, so that nothing here is read as a clean bill:
        the 101 are still winners, now ~20% of the deck instead of 100%, so that
        bias is diluted rather than gone -- on large caps it moves the median
        buy-and-hold from +15.3% (101) to roughly +11.2% against the 399's
        +9.7%. Survivorship is untouched and remains the largest known bias in
        every number this project produces (measured 2026-09-08 by scripts/wf_survivor.py
        at 0.6-2.1 pts/yr on hold -- a floor, since only survivors can be killed). Neither was a reason to
        keep the split, and neither is repaired by dropping it.
        """
        return self._dedupe(self.symbols, self.extended, self.holdout, self.unseen)

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

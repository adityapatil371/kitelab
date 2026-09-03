"""Contract specifications for instruments that are not cash equities.

A share is one share: you buy an integer number and pay the full price. A
futures contract is neither. It trades in LOTS of a fixed size and you post
MARGIN rather than its value, so an account that can hold a stock worth
Rs2,00,000 may not hold one lot of anything here -- and where it can, its real
exposure is many times its capital. Until 2026-09-03 this project modelled
neither, buying futures like shares in any integer quantity for full cash value.

TWO KINDS OF NUMBER LIVE HERE AND THEY ARE NOT EQUALLY TRUSTWORTHY.

  MULTIPLIERS are contract definitions. They change rarely, they are published,
  and the ones below were checked against MCX and NSE contract specifications on
  2026-09-03. `multiplier_verified` records that.

  MARGINS are not definitions at all. The exchange runs SPAN -- a scenario grid
  over price and volatility -- and charges the worst case, then layers exposure
  on top. A percentage is a fiction that is roughly stable in calm markets and
  stops being stable exactly when a trend rule needs it to be right. They are
  therefore a DATED SCHEDULE here, not a constant, and still an approximation.

WHY THE SCHEDULE EXISTS RATHER THAN ONE NUMBER. Crude oil's minimum initial
margin was around 9% of contract value in 2016 and 30-33% after the 2024
revision -- a factor of three. A backtest applying one figure across 2010-2026
does not merely mis-state cost: it lets the account hold positions from 2021
onward that the exchange would have refused, which is the direction that
flatters. Silver moved 11.5% -> 13% in 2024 and again in 2025. The same
mechanism as backtest.STT_SCHEDULE, and for the same reason.

STILL NOT MODELLED, and each would move results:
  * SPAN is recomputed daily. These are step functions between known revisions,
    so intra-period volatility spikes -- March 2020 above all -- are invisible.
  * Margin calls. A position whose margin requirement rises mid-trade is not
    topped up here; it simply continues.
  * Lot size keyed to (symbol, EXPIRY) rather than date. For MCX the units below
    appear to have held throughout, though no consolidated change log was found,
    so that is absence of evidence. For NSE index futures it is definitely
    wrong -- see INDEX_LOTS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class Contract:
    """One instrument's tradeable shape.

    multiplier   units of the underlying per lot, in the terms the PRICE is
                 quoted. MCX gold is quoted per 10 g and trades in 1 kg lots, so
                 one lot is 100 x the quoted price.
    margins      (effective_from, fraction of contract value) oldest first. The
                 first entry applies to everything before the second.
    launched     first date the contract existed. A price series that starts
                 earlier is synthesised from something else and should be
                 treated as a different instrument -- see `predates_launch`.
    """
    symbol: str
    multiplier: float
    margins: tuple[tuple[date, float], ...]
    multiplier_verified: bool
    note: str
    launched: date | None = None

    def lot_value(self, price: float) -> float:
        return price * self.multiplier

    def margin_at(self, when) -> float:
        """Margin fraction in force on `when`. Latest entry not after it."""
        stamp = pd.Timestamp(when).date()
        rate = self.margins[0][1]
        for effective, value in self.margins:
            if stamp >= effective:
                rate = value
        return rate

    def lot_margin(self, price: float, when) -> float:
        return self.lot_value(price) * self.margin_at(when)

    def predates_launch(self, when) -> bool:
        return (self.launched is not None
                and pd.Timestamp(when).date() < self.launched)


D = date

# Multipliers verified 2026-09-03 against MCX contract specifications (and NSE's
# parallel bullion listings, which carry identical units). Margins are dated
# from documented MCXCCL revisions; the pre-revision figures are the best
# available and the early ones are the weakest.
CONTRACTS: dict[str, Contract] = {
    "GOLD": Contract(
        "GOLD", 100.0,
        ((D(2010, 1, 1), 0.06), (D(2025, 10, 1), 0.09)),
        True, "1 kg trading unit quoted per 10 g. Tick Rs1/10 g. Margin ran "
              "6-9%; MCXCCL raised bullion minimums in Oct 2025."),
    "GOLDM": Contract(
        "GOLDM", 10.0,
        ((D(2010, 1, 1), 0.06), (D(2025, 10, 1), 0.09)),
        True, "100 g quoted per 10 g. THE RETAIL GOLD CONTRACT -- about "
              "Rs98,000 of margin at Rs162,920/10 g, inside a Rs2,00,000 account."),
    "GOLDTEN": Contract(
        "GOLDTEN", 1.0,
        ((D(2025, 4, 1), 0.06), (D(2025, 10, 1), 0.09)),
        True, "10 g quoted per 10 g, so one lot IS the quote. LAUNCHED "
              "2025-04-01: any longer series is synthesised.",
        launched=D(2025, 4, 1)),
    "GOLDGUINEA": Contract(
        "GOLDGUINEA", 1.0,
        ((D(2010, 1, 1), 0.06), (D(2025, 10, 1), 0.09)),
        True, "8 g quoted per 8 g -- multiplier 1, NOT 10. Tick Rs1/8 g."),
    "GOLDPETAL": Contract(
        "GOLDPETAL", 1.0,
        ((D(2010, 1, 1), 0.06), (D(2025, 10, 1), 0.09)),
        True, "1 g quoted per 1 g. Tick Rs1/g."),
    "SILVER": Contract(
        "SILVER", 30.0,
        ((D(2010, 1, 1), 0.115), (D(2024, 8, 1), 0.13), (D(2025, 10, 1), 0.15)),
        True, "30 kg quoted per kg. Tick Rs1/kg. MARGIN WAS PREVIOUSLY MODELLED "
              "AT 8% HERE, roughly half the real figure. Currently excluded for "
              "a 1000x stitching seam -- see ASSET_EXCLUDED."),
    "SILVERM": Contract(
        "SILVERM", 5.0,
        ((D(2010, 1, 1), 0.115), (D(2024, 8, 1), 0.13), (D(2025, 10, 1), 0.15)),
        True, "5 kg quoted per kg."),
    "SILVERMIC": Contract(
        "SILVERMIC", 1.0,
        ((D(2010, 1, 1), 0.115), (D(2024, 8, 1), 0.13), (D(2025, 10, 1), 0.15)),
        True, "1 kg quoted per kg. Not the source of the SILVER seam: 30 kg "
              "against 1 kg is 30x and the seam is ~1000x."),
    "CRUDEOIL": Contract(
        "CRUDEOIL", 100.0,
        ((D(2010, 1, 1), 0.09), (D(2020, 5, 1), 0.20), (D(2024, 8, 1), 0.33)),
        True, "100 barrels quoted per barrel. THE WIDEST MARGIN MOVE HERE: ~9% "
              "in 2016 against 33% after the Aug 2024 revision. The 2020 step is "
              "interpolated across the negative-settlement aftermath and is the "
              "least trustworthy entry in this file. Excluded anyway for the "
              "Rs1.00 settlement of 2020-04-20."),
    "CRUDEOILM": Contract(
        "CRUDEOILM", 10.0,
        ((D(2010, 1, 1), 0.09), (D(2020, 5, 1), 0.20), (D(2024, 8, 1), 0.33)),
        True, "10 barrels quoted per barrel. Launch date unconfirmed and "
              "certainly later than 2010 -- check first-trade date against MCX."),
    "NATURALGAS": Contract(
        "NATURALGAS", 1250.0,
        ((D(2010, 1, 1), 0.10), (D(2024, 8, 1), 0.25)),
        True, "1250 mmBtu quoted per 1 mmBtu. NOT per 100 mmBtu -- one widely "
              "read tutorial says so in its summary and contradicts itself in "
              "its own worked example. TICK IS DISPUTED between Rs0.10 and "
              "Rs0.25; at Rs0.10 every spread cost here is understated 2.5x if "
              "the true tick is Rs0.25. Read the contract note before trading."),
    "NATGASMINI": Contract(
        "NATGASMINI", 250.0,
        ((D(2010, 1, 1), 0.10), (D(2024, 8, 1), 0.25)),
        True, "250 mmBtu quoted per 1 mmBtu. Launch date unconfirmed, likely "
              "post-2019."),
}

# NOT ADDED: SILVER100 (launched 2026-06-01). Its quotation base could not be
# established -- one secondary source reports a Rs1/10 g tick, which would mean
# MCX switched this contract to a per-10-g base unlike every other silver
# contract that quotes per kg. The multiplier is 10 if quoted per 10 g and 0.1
# if per kg, a hundredfold difference, so guessing is not an option. Note also
# that SILVER100 and the older SILVER1000 will collide under loose symbol
# matching.

# NSE INDEX FUTURES -- recorded, deliberately NOT wired in.
#
# LOT SIZE IS A PROPERTY OF THE CONTRACT, NOT OF THE DATE, and this is the trap
# worth naming loudly. Every NSE revision leaves live contracts on their OLD lot
# until they expire: the Aug 2015 change took effect only from the November 2015
# expiry, and in 2024 weeklies and monthlies ran out at the old lot while
# quarterlies transitioned at end-December. So a backtester keying the
# multiplier off the BAR DATE gets roughly two months of wrong notionals around
# each of a dozen transitions -- a clean multiplicative distortion of P&L that
# throws no exception and looks like alpha or drawdown depending on the side.
#
# Wiring this in needs (symbol, expiry) keyed contracts and a real futures
# series; the project currently holds SPOT INDEX candles, which are not
# tradeable at all. Two BANKNIFTY transitions below are undated and both fall
# inside 2010-2026.
INDEX_LOTS = {
    "NIFTY 50": [(D(2010, 1, 1), 25), (D(2015, 11, 26), 75), (D(2021, 7, 29), 50),
                 (D(2024, 4, 26), 25), (D(2024, 11, 20), 75), (D(2026, 1, 1), 65)],
    "NIFTY BANK": [(D(2010, 1, 1), 25), (D(2015, 11, 26), 30),
                   # (????-??-??, 25) -- a reduction happened between 2015 and
                   # 2021; the Mar 2021 circular lists 25 as already current.
                   # (????-??-??, 15) -- and again before Apr 2024, which says
                   # BANKNIFTY is "unchanged at 15". Both dates UNKNOWN.
                   (D(2024, 11, 20), 30), (D(2025, 7, 1), 35), (D(2026, 1, 1), 30)],
}
INDEX_MARGIN = 0.125          # ~10-15% of contract value in calm conditions
INDEX_LOTS_INCOMPLETE = ["NIFTY BANK"]


def get(symbol: str) -> Contract | None:
    """The contract for `symbol`, or None if it is bought outright.

    None is the normal case: every NSE equity, and spot Bitcoin, which really is
    bought in fractions for its full price.
    """
    return CONTRACTS.get(symbol)


def unverified_multipliers() -> list[str]:
    return sorted(c.symbol for c in CONTRACTS.values() if not c.multiplier_verified)

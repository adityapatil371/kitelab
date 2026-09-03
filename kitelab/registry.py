"""Every strategy the lab compares, declared in one place.

WHY THIS EXISTS. Adding a strategy used to mean editing five separate places:
signal_lists in scripts.dashboard_data, STRATEGY_LABELS, PRIMARY, the page's
label map, and kitelab.signals._CODE. Four of those are cosmetic. The fifth is
not -- _CODE is the list of modules hashed into every cache stamp, and
holygrail.py was missing from it. The Holy Grail rules were rewritten at 16:33
on 2026-09-02; the dashboard built at 15:32 went on serving trades from the
superseded code, and refresh reported "already current" because the digest it
compared could not see the file. Every Holy Grail number published that day was
invalid, and nothing in the project could have said so.

So a Strategy declares THE MODULE THAT PRODUCES ITS TRADES, and signals.stamp()
derives the code digest from this list rather than from a hand-kept constant. A
strategy that is on the board is in the stamp by construction. The 2026-09-02
bug is now unrepresentable rather than merely fixed.

ADDING ONE. Append a Strategy (or call register()). It then appears in the
compare table, the breadth sweep and scripts.bootstrap without another edit,
and -- this is the point of testing new rules against old ones -- it is measured
on the SAME stocks, the SAME baskets and the SAME account as everything already
there, so the comparison is between rules rather than between setups.

    register(key="mine", variant="v1", label="My rule",
             cache="MyRule_v1", module="myrules.py",
             build=lambda symbol: myrules.simulate(symbol))

One caution that the machinery cannot enforce for you: every rule you add
raises the bar for all of them. scripts.bootstrap counts how many variants
clear a 95% test against the ~5% that clear it by chance, so a 25th strategy
makes "one of them looks good" slightly less impressive, not more. That is
arithmetic, not pessimism.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from . import backtest, darvas, holygrail, timeframes
from .timeframes import simulate_variant

# ---------------------------------------------------------------- shape ----


@dataclass(frozen=True)
class Strategy:
    """One row of the compare table, and everything needed to produce it.

    key      the family, e.g. "ema". Families share a label and a page row.
    variant  the discriminator inside the family -- a band (0.02), a channel
             pair ("20-10"), a stop reading ("swing"). Kept as the ORIGINAL
             type, not stringified: the grid key formatter (tag) renders 0.02
             as "0.02" and "20-10" as itself, and the page relies on both.
    cache    cache stem WITHOUT the universe suffix. Renaming it forces a
             rebuild, which is the correct move whenever a rule changes meaning.
    module   the file under kitelab/ that produces the trades. Feeds the cache
             stamp; getting it wrong is the 2026-09-02 bug.
    build    symbol -> list of trades. One symbol at a time, because that is
             what the cache builder iterates and what lets a bad symbol be
             skipped without losing the run.
    """
    key: str
    variant: object
    label: str
    cache: str
    module: str
    build: Callable[[str], list]
    family_label: str = ""


# --------------------------------------------------------- the strategies ----

BANDS = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
SOLO_BAND = 0.02
ATH_BAND = 0.10
DARVAS_WINDOWS = [(20, 10), (55, 20)]
DARVAS_GATED = [True, False]
HG_VARIANTS = [("candle", "signal_low"), ("swing", "pivot")]

FAMILY_LABELS = {
    "ema": "EMA · M/W/D", "qmw": "EMA · Q/M/W", "pair": "EMA · one higher TF",
    "e1": "EMA · daily only", "eath": "EMA · near the high",
    "dv": "Turtle channel", "hg": "Holy Grail · ADX",
}

# The variant each family shows wherever only one can be shown -- the per-stock
# page, and the breadth sweep. Not "the best": the DEFAULT, chosen once so that
# the breadth curves compare families rather than each family's luckiest setting.
PRIMARY = {"ema": 0.02, "qmw": 0.02, "pair": "WD", "e1": "daily",
           "eath": "near-high", "dv": "20-10", "hg": "swing"}


def _padded(key: str, band: float):
    """simulate_variant trades, plus the two fields portfolio.run needs.

    kitelab.timeframes emits neither same_session nor net_profit; every other
    producer does. Padding here rather than in the account keeps portfolio.run
    with one trade shape to reason about.
    """
    def build(symbol):
        out = []
        for t in simulate_variant(symbol, key, band=band):
            t = dict(t)
            t["same_session"] = (pd.Timestamp(t["entry_ts"]).date()
                                 == pd.Timestamp(t["exit_ts"]).date())
            t["net_profit"] = t["gross_profit"] - t["charges"]
            out.append(t)
        return out
    return build


def _build_registry() -> list[Strategy]:
    out: list[Strategy] = []
    for band in BANDS:
        # Band 2% keeps the historical cache names, so the one setting that has
        # been on the board longest does not force a needless rebuild.
        stem = "" if band == 0.02 else f"_b{band*100:g}"
        out.append(Strategy("ema", band, f"EMA · M/W/D · {band:.0%} band",
                            f"EMA{stem}", "backtest.py",
                            lambda s, b=band: backtest.simulate(s, band=b)))
        out.append(Strategy("qmw", band, f"EMA · Q/M/W · {band:.0%} band",
                            f"QMW{stem}", "timeframes.py", _padded("QMW", band)))
    # NOTE THE CACHE NAMES. Darvas gained a weekly gate on 2026-09-01, so a
    # trade list built before that is a different strategy under the same label.
    # The old caches are UNSTAMPED and load() would hand them back without
    # complaint; renaming is what forces the rebuild.
    for gated in DARVAS_GATED:
        for entry_len, exit_len in DARVAS_WINDOWS:
            variant = f"{entry_len}-{exit_len}" + ("" if gated else " 1TF")
            stem = (f"Turtle_w{darvas.WEEKLY_LEN}" if gated else "Turtle_1tf")
            out.append(Strategy(
                "dv", variant,
                f"Turtle {entry_len}-{exit_len}" + (" + weekly" if gated else " (1 TF)"),
                f"{stem}_{entry_len}_{exit_len}", "darvas.py",
                lambda s, a=entry_len, b=exit_len, g=gated:
                    darvas.simulate(s, a, b, weekly=g)))
    for pair_key, pair_label, _ in timeframes.PAIRS:
        out.append(Strategy("pair", pair_key, f"EMA · {pair_label}",
                            f"EMA_{pair_key}", "timeframes.py",
                            _padded(pair_key, SOLO_BAND)))
    out.append(Strategy("e1", "daily", "EMA · daily only", "EMA_daily_only",
                        "backtest.py",
                        lambda s: backtest.simulate(s, band=SOLO_BAND, stack="daily")))
    out.append(Strategy("eath", "near-high",
                        f"EMA · within {ATH_BAND:.0%} of the high",
                        f"EMA_ath{ATH_BAND*100:g}", "backtest.py",
                        lambda s: backtest.simulate(s, band=SOLO_BAND,
                                                    ath_band=ATH_BAND)))
    for hg_tag, hg_stop in HG_VARIANTS:
        out.append(Strategy("hg", hg_tag, f"Holy Grail · {hg_tag} stop",
                            f"HolyGrail_{hg_tag}", "holygrail.py",
                            lambda s, st=hg_stop: holygrail.simulate(s, stop=st)))
    return [Strategy(s.key, s.variant, s.label, s.cache, s.module, s.build,
                     FAMILY_LABELS.get(s.key, s.key)) for s in out]


REGISTRY: list[Strategy] = _build_registry()


def register(**kwargs) -> Strategy:
    """Add a strategy at runtime. Same fields as Strategy.

    Appending to REGISTRY is equivalent; this exists so a plugin file can add
    itself without importing the list and mutating it in place.
    """
    s = Strategy(family_label=kwargs.pop("family_label", ""), **kwargs)
    REGISTRY.append(s)
    return s


# ------------------------------------------------------------- accessors ----


def modules() -> list[str]:
    """Every module that produces trades on the board. FEEDS THE CACHE STAMP.

    Derived, never hand-kept -- see the module docstring for what a hand-kept
    list cost on 2026-09-02. Support modules that every producer reads through
    (frames, sizing, slippage, indicators) are added by signals itself, because
    no Strategy names them and a change to any of them changes every trade.
    """
    return sorted({s.module for s in REGISTRY})


def families() -> dict[str, str]:
    """family key -> label, in registry order."""
    out = {}
    for s in REGISTRY:
        out.setdefault(s.key, s.family_label or s.key)
    return out


def variants(key: str) -> list:
    return [s.variant for s in REGISTRY if s.key == key]


def primary(key: str):
    """The family's default variant, falling back to its first if unlisted --
    so a newly registered family works before anyone edits PRIMARY."""
    if key in PRIMARY:
        return PRIMARY[key]
    got = variants(key)
    return got[0] if got else None


def find(key: str, variant) -> Strategy | None:
    for s in REGISTRY:
        if s.key == key and s.variant == variant:
            return s
    return None

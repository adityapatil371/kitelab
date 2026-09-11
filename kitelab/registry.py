"""Every strategy the lab compares, declared in one place.

WHY THIS EXISTS. Adding a strategy used to mean editing five separate places:
signal_lists in scripts.dashboard_data, STRATEGY_LABELS, PRIMARY, the page's
label map, and a hand-kept module list in kitelab.signals (then called _CODE;
gone since 2026-09-03 -- signals._SUPPORT now lists only the support modules
every producer reads through, and the producers come from here). Four of
those were cosmetic. The fifth was not -- that list was what got hashed into
every cache stamp, and holygrail.py was missing from it. The Holy Grail rules
were rewritten at 16:33 on 2026-09-02; the dashboard built at 15:32 went on
serving trades from the superseded code, and refresh reported "already
current" because the digest it compared could not see the file. Every Holy
Grail number published that day was invalid, and nothing in the project could
have said so.

So a Strategy declares THE MODULE THAT PRODUCES ITS TRADES, and signals.stamp()
derives the code digest from this list rather than from a hand-kept constant. A
strategy that is on the board is in the stamp by construction. The 2026-09-02
bug is now unrepresentable rather than merely fixed.

ADDING ONE. Append a Strategy (or call register()). It then appears in the
compare table, kitelab.validation and scripts.bootstrap without another edit,
and -- this is the point of testing new rules against old ones -- it is measured
on the SAME stocks and the SAME account as everything already there, so the
comparison is between rules rather than between setups. (The breadth sweep and
the per-strategy baskets it drew went on 2026-09-03; see CLAUDE.md.)

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
    variant  the discriminator inside the family -- a band (0.0, the only one
             since BANDS lost its sweep on 2026-09-05), a stack ("WD"), a
             channel pair ("20-10"), a stop reading ("swing"). Kept as the
             ORIGINAL type, not stringified: the grid key formatter (tag)
             renders the float as "0.0" and "20-10" as itself, and the page
             relies on both.
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

# NO BAND, from 2026-09-05. Every EMA family on the board now trades the bare
# 20-EMA cross: in when the close clears the line, out when it falls back
# through it.
#
# WHAT `band` WAS. Hysteresis -- enter only when price is `band` ABOVE every EMA,
# exit only when it is `band` BELOW one of them, so the two lines bracket a dead
# zone where nothing happens. kitelab/backtest.py:61 records what it bought:
# without it entry and exit share one knife-edge, and price hovering around an
# EMA churned the account -- median holding period 3 sessions on a strategy
# filtered by MONTHLY EMAs, and 20% of trades re-entering the same stock the next
# day when first measured; re-measured at band 0 on 2026-09-07 it is 29-31% on
# M/W/D, W/D, Q/M/W and M/W, median hold 3-4 bars. That churn is now paid
# again, deliberately.
#
# WHAT ELSE GOES WITH IT. The 0-5% sweep was this project's only
# parameter-plateau control: "is 2% a plateau or a lucky spike?" was answered by
# six adjacent settings moving together, and with one setting there is nothing to
# compare. That question is no longer measured anywhere -- see CLAUDE.md, which
# no longer lists it. The offsetting gain is real though: the 2% band was one of
# only three things this project ever actually fitted (config.Config.merged), so
# this removes a fitted parameter rather than adding one.
#
# Kept as a list, so restoring the sweep is one edit here and a rebuild.
BANDS = [0.0]

# The band for the EMA families that never swept it -- one-higher-TF, daily-only
# and near-the-high. Was 0.02, moved with BANDS so every EMA rule on the board
# reads the same cross rather than three of them keeping a buffer the other two
# lost. Their cache names do not encode the band (EMA_WD, EMA_daily_only,
# EMA_ath10), so this line changes what they trade without changing what they are
# called -- which is why signals._SUPPORT now stamps this file.
SOLO_BAND = 0.0

# NOT an EMA band and not touched by the band removal: how far below its own
# all-time high a stock may be and still be bought. It is the whole definition
# of the `eath` family -- set it to 0 and that family becomes a duplicate of the
# stack underneath it.
ATH_BAND = 0.10

# WHICH STACKS THE ATH FILTER IS ASKED ON, widened 2026-09-05 from the single
# M/W/D row to five. The question the family exists to answer is "does refusing
# to buy a stock far below its own record improve the rule?", and one stack
# could not answer it -- a filter can easily help a stack that trades daily and
# do nothing for one that trades weekly, and the board had no way to see that.
# It matters now in particular because after the band came off, every
# daily-traded EMA rule on the board lost money and every weekly-traded one
# passed all five gates: this family spans both, so it can say whether the ATH
# filter rescues the daily side or merely rides the weekly one.
#
# The one remaining row is a timeframes.py pair and is named there (PAIRS).
# It has an unfiltered twin already on the board -- eath|MW -> pair|MW -- so it
# can be read against the same stack without the filter. That pairing is the
# point; do not add an ATH stack without its control. (Until 2026-09-11 the
# family also held MWD, whose twin was ema rather than a pair, because MWD is
# backtest.py's own stack.)
#
# And since 2026-09-07 the pairing is exact: every trade an ATH row takes is a
# trade its control takes (same symbol, same entry stamp), because the filter
# now only DECLINES crosses rather than manufacturing entries of its own when
# price climbs back inside the band. tests.test_ath_filter pins that subset
# property; backtest.ema_stack_signal carries the measurement that forced it.
# CUT 5 -> 2 ON 2026-09-11, on the re-rank of all 19 over the rebuilt
# momentum board (5,700 cells, every one carrying the daily-excess test).
# Removed: QM (rank 18 of 19 -- 1.0% of its 300 cells beat buy-and-hold, lowest
# own-exit contribution and lowest effect on the board), QD (rank 14, but
# correlating 0.93 and 0.92 with the two rows kept -- a third label on one
# cluster), and MWD (rank 10, correlating 0.968 with WD at a median gap of
# 0.00 -- indistinguishable, not merely similar, and WD ranks higher).
# Their unfiltered controls pair|QM and pair|QD went with them.
#
# CUT 2 -> 1 ON 2026-09-11 (second cut, same day), on the redundancy pass in
# scripts/redundancy.py rather than on rank: the board's 13 labels carried only
# 3-9 independent ideas and six of the thirteen were EMA rules chained at
# r >= 0.65. WD went because its own control pair|WD was one of those six
# (r = 0.895 raw / 0.817 demeaned with ema|0), and an ATH row cannot stay when
# the control it is read against leaves. eath|MW stays: at a nearest-twin
# r = 0.294 it is among the most DISTINCT rows on the board, which is the whole
# reason the family is worth a row at all.
ATH_STACKS = ["MW"]
# WHICH PAIRS ARE ON THE BOARD, narrowed 2026-09-11 from all four of
# timeframes.PAIRS to two. The table there says which stacks EXIST and is left
# whole, because ATH_STACK_LABEL reads it for display names; this list says
# which ones are REGISTERED. Dropped: QW (r = 0.963 with qmw|0 and 0.936 with
# pair|MW -- the tightest pair on the board, two labels on one rule) and WD
# (r = 0.895 with ema|0, and it was the last thing holding eath|WD on).
# Restoring one is one edit here plus a rebuild.
PAIR_STACKS = ["MD", "MW"]
DARVAS_WINDOWS = [(20, 10), (55, 20)]
DARVAS_GATED = [True, False]

# EMA_MWD_RETIRED IS GONE, 2026-09-05, and this is the uncomfortable half of
# removing the band. It held {0.0}: M/W/D at a 0% band was cut from the board
# EARLIER THE SAME DAY as consistently the worst variant tested -- median MAR
# -0.14 across all 50 priority x risk x start-year combinations, positive in only
# 7 of them, best case a losing 0.23. With BANDS = [0.0] that guard would delete
# the M/W/D family outright, so it is removed and the variant returns as the ONLY
# M/W/D row on the board. Nothing has re-measured it: the -0.14 stands until a
# rebuild says otherwise, and the Validated gate is what should be read on that
# row rather than its rank.

# THE ONE HOLY GRAIL ROW TRADES THE SIGNAL CANDLE'S LOW, from 2026-09-07.
#
# What it was. On 2026-09-05 the candle-stop reading was retired from the
# board and the row left trading stop="pivot" (the last confirmed 5-bar swing
# low, a median 13.4% below entry), on the grounds that under this board's
# validation candle was the second-worst variant tested (median MAR -0.06,
# positive in 8 of 50 combinations, best case 0.08). That left the project
# saying three different things: CLAUDE.md, that the stop is the entry
# candle's own low uniformly across every strategy; holygrail.py's own
# docstring, that the signal low is "the standing rule" and the board
# publishes both; and this line, trading the pivot alone.
#
# What it is. The row trades stop="signal_low", so the uniform convention is
# true of the whole board. The reason is holygrail.py's own measurement
# against all 39 stops marked in the class sheet: the signal candle's low
# misses them by a median 0.80%, the 5-bar pivot by 5.82% -- five times too
# wide, and positions a fifth of the size. Ranking better on the board is not
# a reason to trade a stop the rule does not describe. "pivot" stays a
# parameter of holygrail.simulate for anyone who wants the comparison.
#
# The variant key "swing" and the cache stem "HolyGrail_swing" are unchanged
# on purpose -- the page and the build key on them -- and the cache is
# invalidated anyway, because this file is in the stamp (signals._SUPPORT).
# THE FAMILY IS OFF THE BOARD, 2026-09-11, and the list is empty rather than
# deleted so the row above reads as history and the family can come back by
# re-adding one tuple. It was last of 19 on every view of the re-rank: 0.0% of
# its 300 cells beat buy-and-hold, and its LUCKIEST cell of 300 still lost 3.1
# CAGR points. A best-of-300 maximum is mostly luck and so cannot justify
# KEEPING a rule -- but "even the lucky tail loses" is sound grounds for
# dropping one. holygrail.py stays on disk, unregistered and untouched, with
# its stop measurement intact.
HG_VARIANTS: list[tuple[str, str]] = []

# THE qmw FAMILY (EMA Q/M/W, one row) IS GONE, 2026-09-11, on the redundancy
# pass and not on rank -- it ranked 3rd of 13 on the six-view table. It
# correlated 0.963 with pair|QW and 0.961 with pair|MW across all 300 shared
# scenarios, and 0.922 / 0.921 after each scenario's cross-strategy mean is
# subtracted, so the duplication is a fact about the rules and not about the
# scenarios they share. Three of the top four ranked rows were that one cluster;
# the board paid for three rows and learned from one. pair|MW was kept as the
# cluster's representative (best average rank, 4.67). Nothing re-measured qmw
# on its own merits: it was not beaten, it was duplicated.
# timeframes.py still implements "QMW" and _padded can still build it; this file
# simply no longer registers it.

FAMILY_LABELS = {
    "ema": "EMA · M/W/D", "pair": "EMA · one higher TF",
    # "qmw": "EMA · Q/M/W" -- family removed 2026-09-11, see the note above.
    # The percentage lives HERE, once, because it is the same on every row of
    # the family (five until the 2026-09-11 cut, two after it) and the Setting
    # column carries the stack instead. It was in Strategy.label
    # only, which the payload never publishes -- so until 2026-09-05 the page
    # never showed the 10% anywhere at all.
    "e1": "EMA · daily only", "eath": f"EMA · within {ATH_BAND:.0%} of the high",
    "dv": "Turtle channel",
    # "hg": "Holy Grail · ADX" -- family removed 2026-09-11, see HG_VARIANTS.
}

# The variant each family shows wherever only one can be shown -- the per-stock
# Detail view. Not "the best": the DEFAULT, chosen once so that a family is
# compared by one stated setting rather than by its luckiest one. (It also fed
# the breadth sweep until that went on 2026-09-03.)
# Stack -> display label, DERIVED from the timeframes tables rather than
# retyped, so a pair renamed there cannot leave a stale label here. MWD is in
# VARIANTS; the rest are in PAIRS.
ATH_STACK_LABEL = {k: label for k, label, _ in
                   (*timeframes.VARIANTS, *timeframes.PAIRS)}

# "eath" was "MWD" until 2026-09-11 and that stack is no longer registered. It
# became "WD" -- the same stack "pair" defaulted to -- so the filtered and
# unfiltered families are shown at the SAME setting and the Detail view compares
# like with like. BOTH BECAME "MW" LATER THE SAME DAY, when the redundancy cut
# took pair|WD and eath|WD off the board: a default has to name a row that is
# registered, and MW is the only stack the two families still share. Picked for
# that reason, not because MW ranked higher; a default chosen on rank is the
# luckiest variant by another name.
PRIMARY = {"ema": 0.0, "pair": "MW", "e1": "daily",
           "eath": "MW", "dv": "20-10"}


def _padded(key: str, band: float, ath_band: float | None = None):
    """simulate_variant trades, plus the two fields portfolio.run needs.

    kitelab.timeframes emits neither same_session nor net_profit; every other
    producer does. Padding here rather than in the account keeps portfolio.run
    with one trade shape to reason about.
    """
    def build(symbol):
        out = []
        for t in simulate_variant(symbol, key, band=band, ath_band=ath_band):
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
        # Band 2% keeps the historical cache names -- it is off the board as of
        # 2026-09-05 (see BANDS), so this branch is currently dead, but it is
        # what lets the old caches be reused unchanged if the sweep comes back.
        stem = "" if band == 0.02 else f"_b{band*100:g}"
        # "no band" rather than "0% band": the latter reads as a setting someone
        # chose among others, and since 2026-09-05 there are no others. The
        # variant itself stays a float, because tag() and the page depend on it.
        note = f"{band:.0%} band" if band else "no band"
        out.append(Strategy("ema", band, f"EMA · M/W/D · {note}",
                            f"EMA{stem}", "backtest.py",
                            lambda s, b=band: backtest.simulate(s, band=b)))
        # THE qmw FAMILY IS GONE, 2026-09-11 -- see QMW_RETIRED above.
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
        if pair_key not in PAIR_STACKS:
            continue
        out.append(Strategy("pair", pair_key, f"EMA · {pair_label}",
                            f"EMA_{pair_key}", "timeframes.py",
                            _padded(pair_key, SOLO_BAND)))
    out.append(Strategy("e1", "daily", "EMA · daily only", "EMA_daily_only",
                        "backtest.py",
                        lambda s: backtest.simulate(s, band=SOLO_BAND, stack="daily")))
    # The ATH family, one row per stack in ATH_STACKS. MWD goes through
    # backtest.py (its own M/W/D implementation); the rest are timeframes.py
    # pairs, where stack_signal grew an ath_band argument on 2026-09-05 that
    # applies the filter to the BASE frame's own closes -- so a weekly-traded
    # row compares a weekly close against the highest weekly close, matching
    # pine/ema_ath_band.pine rather than diverging from it.
    for stack in ATH_STACKS:
        pct = f"{ATH_BAND:.0%}"
        if stack == "MWD":
            out.append(Strategy("eath", stack, f"EMA · M/W/D · within {pct} of the high",
                                f"EMA_ath{ATH_BAND*100:g}_MWD", "backtest.py",
                                lambda s: backtest.simulate(s, band=SOLO_BAND,
                                                            ath_band=ATH_BAND)))
        else:
            out.append(Strategy("eath", stack,
                                f"EMA · {ATH_STACK_LABEL[stack]} · within {pct} of the high",
                                f"EMA_ath{ATH_BAND*100:g}_{stack}", "timeframes.py",
                                _padded(stack, SOLO_BAND, ath_band=ATH_BAND)))
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

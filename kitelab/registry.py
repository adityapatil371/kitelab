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

from . import backtest, darvas, entries, holygrail, timeframes
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
#
# NOTHING READS THIS FOR THE BOARD ANY MORE, from 2026-09-17. The `ema` family
# (M/W/D, the only one whose variant slot ever held a band) came off the board
# that day, so _build_registry no longer loops over BANDS at all; SOLO_BAND is
# what the surviving EMA families trade. The list stays because it is the
# declaration of what a band sweep WOULD be, and because scripts and docstrings
# still point at it for the 2026-09-05 reasoning.
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
# CUT 2 -> 1 ON 2026-09-17, on scripts/board_span.py and NOT on rank. MD was
# the second-most overlapping entry pair on the whole board: phi = 0.843 with
# ema|0.0, measured over the firing panel alone with no return ever read. MW
# stays because it is eath|MW's designed control, and an ATH row cannot be read
# without its unfiltered twin.
PAIR_STACKS = ["MW"]
# CUT 4 ROWS -> 1 ON 2026-09-17, same pass. dv|20-10 and dv|55-20 fire together
# at phi = 0.958 -- the single worst pair on the board, two labels on one rule --
# and the 1TF controls overlap each other at 0.309 while adding a second axis
# (the weekly gate) that no other family carries. 55-20 is kept because the
# returns-blind maximin slate picked it; 20-10 was the page default, which is a
# convention and not evidence. The lists stay lists so restoring a row is one
# edit here plus a rebuild.
DARVAS_WINDOWS = [(55, 20)]
DARVAS_GATED = [True]

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

# ---------------------------------------------------------- the stop axis ----
# ADDED 2026-09-17, and it is the finding that forced the rest of this file.
#
# THE STOP IS NOT A DETAIL, IT IS THE BOARD'S BINDING CONSTRAINT.
# scripts/board_span.py compared 24 exit rules x stop widths over 596,729
# pooled bars, returns-blind. Under this project's uniform convention -- the
# stop is the entry bar's own low -- the exit axis does not exist:
#
#     stop      Kaiser ideas   Li-Ji   median |rho|   median hold   ends day 1
#     own            1          3.00      0.907            1d          54.6%
#     atr2           2          5.00      0.419           11d           8.6%
#     atr3           2          6.00      0.269           17d           8.0%
#
# At `own`, 54.6% of trades are stopped out on their FIRST session and the
# median trade lives two. Nothing downstream of the entry gets a chance to
# differ, so every exit rule the lab can write collapses onto one behaviour
# (stop|own and t60|own correlate at 1.000 -- a 60-session limit and a stop are
# the same rule when the stop fires first 55% of the time). Widen the stop and
# the axis reappears: three times as many independent ideas out of the same 24
# labels.
#
# So the stop width becomes a board AXIS rather than a fixed convention. Two
# arms, every family carried on both:
#
#   own    the entry bar's own low. The inherited convention, kept so every
#          number published before today stays comparable.
#   atr3   close - 3 x ATR(14) at entry. The WIDE arm.
#
# WHY 3 AND NOT A SWEEP. A sweep over stop width is exactly the fitted
# parameter this project keeps removing (see BANDS above). Two arms answer
# "does stop width change the answer?"; a sweep answers "which width won?",
# which is a question about luck. 3 x ATR is the width scripts/xrank_account.py
# already used and is a standard outside this repo, so it is not a value this
# board chose for itself.
#
# THE STOP IS ALSO A SIZING INPUT, not only an exit line: portfolio.py sizes
# off entry_price - stop, so the wide arm takes SMALLER positions on the same
# signal. The two arms are therefore different strategies at the account level,
# not the same strategy exited differently, and that is why each gets its own
# row rather than a footnote.
#
# Cache stems get the arm appended (the `own` arm keeps the historical stem, so
# its caches are reused unchanged); signals.producer_of resolves by LONGEST
# prefix, so EMA_daily_only_atr3_all cannot be mistaken for EMA_daily_only.
STOPS = {"own": None, "atr3": 3.0}

STOP_LABEL = {"own": "stop at the entry bar's own low",
              "atr3": "stop 3 x ATR(14) below entry"}


# ------------------------------------------------- the six new entry rules ----
# ADDED 2026-09-17. Chosen by scripts/board_span.py's maximin (farthest-point)
# pass over 18 candidate entry rules, on the FIRING PANEL only: the selection
# read which stock-sessions each rule fires on and never read a return, a CAGR
# or a rank. That is the whole point. The previous board was assembled by
# ranking, and scripts/pbo.py measured what ranking buys here -- PBO 0.412,
# worse than a coin flip -- so a rule's past score is not evidence about which
# rules belong on a board together. Coverage of the design space is.
#
# What it bought, same slate size (k = 9), no returns read:
#
#     slate            Kaiser   90% var   Li-Ji   max phi   mean phi
#     the old board       4        6       8.0     0.958     0.127
#     maximin             4        8       9.0     0.103     0.027
#
# The worst-overlapping pair falls from 0.958 to 0.103. Six of nine incumbents
# were displaced; eath|MW, e1|daily and dv|55-20 survived on distance alone.
#
# These six deliberately include rules this project would never have ranked
# onto a board -- a mean-reversion entry, a calendar entry with no price
# content at all -- because a board that only holds trend-following variations
# cannot answer whether trend-following is the thing that matters.
ENTRY_FAMILIES = [
    ("mr", "20-day low", "Mean reversion"),
    ("vcon", "narrowest 7-day range", "Volatility contraction"),
    ("vol", "volume 3x its 50-day median", "Volume spike"),
    ("cal", "first session of the month", "Calendar"),
    ("xrank", "top decile 252-day return", "Cross-sectional momentum"),
    ("pull", "above the 200-day, below the 20-day", "Pullback in an uptrend"),
    # ---- EIGHT MORE, 2026-09-19 (NEXT_TESTS item 24) ----------------------
    # Same criterion, same machinery, a queue written down before it was
    # measured: scripts/pine_span.py ran the maximin pass over eighteen
    # candidates on firing panels alone and the user's answer to the table was
    # "all of these". Listed in that pass's own pick order. The Pine
    # contributes ONE row and not five -- the order collapses from 0.85 to 0.18
    # once any Pine variant is chosen, because its variants are near-subsets of
    # each other, and five labels holding one idea is the fault the 2026-09-11
    # cut removed. See kitelab/entries.py for the definitions and
    # kitelab/pine.py for the two conventions the Pine row carries.
    ("gapdn", "opens 3% below the previous close", "Gap down"),
    ("rsi30", "RSI(14) closes back above 30", "Oscillator"),
    ("gap", "opens 3% above the previous close", "Gap up"),
    ("mktrel", "up over 20 days while the market is down", "Relative strength"),
    ("dryup", "volume below half its 50-day median", "Volume dry-up"),
    ("low252", "252-day low", "One-year low"),
    ("inside", "inside the previous bar's range", "Inside bar"),
    ("pine", "Heikin-Ashi no-wick + monthly EMA + RSI", "Pine · weekly"),
]


# THE FAMILY LABEL NOW CARRIES THE SETTING, because the variant slot carries
# the STOP. Every family on the board has exactly one entry configuration and
# two stop arms, so "M/W" and "55-20" moved up into the family name where they
# are stated once, and the Setting column says which stop the row trades. That
# is the only thing that varies within a family now, and a Setting column that
# named something uniform across the family was unreadable -- the same fault the
# 2026-09-05 eath note describes.
FAMILY_LABELS = {
    # The three incumbents the returns-blind pass kept, plus one control.
    "e1": "EMA · daily only",
    # KEPT AS A CONTROL, NOT AS A PROPOSAL. It is the unfiltered twin of
    # eath|MW: same stack, same crosses, no all-time-high filter. CLAUDE.md --
    # "every ATH stack needs its unfiltered twin control or its score cannot be
    # read". It is on the board for that structural reason and not because it
    # scored or spanned; the maximin pass did not pick it.
    "pair": "EMA · M/W · no ATH filter (control)",
    "eath": f"EMA · M/W · within {ATH_BAND:.0%} of the high",
    "dv": "Turtle channel 55-20 + weekly",
    # "ema": "EMA · M/W/D" -- OFF THE BOARD 2026-09-17. It fired with pair|MD at
    # phi = 0.843 and with e1|daily at 0.591, the second and fourth worst pairs
    # measured; e1|daily is the same engine with no higher timeframe and was the
    # one the maximin pass kept. Nothing re-measured ema on its merits -- it was
    # duplicated, not beaten, the same verdict pass two gave qmw on 2026-09-11.
    # "qmw" -- family removed 2026-09-11.  "hg" -- family removed 2026-09-11.
    **{k: f"{name} · {what}" for k, what, name in ENTRY_FAMILIES},
}

# The variant each family shows wherever only one can be shown -- the per-stock
# Detail view. Not "the best": the DEFAULT, chosen once so that a family is
# compared by one stated setting rather than by its luckiest one.
#
# It is `own` for every family, and that is a CONVENTION rather than a result:
# the entry bar's own low is the stop this project has published since it
# started, so defaulting to it keeps the Detail view comparable with every
# number written down before 2026-09-17. It is emphatically not a claim that
# the tight arm is better -- board_span says the tight arm is the one that
# COLLAPSES the exit axis, and which arm makes more money is what the rebuild
# is being run to find out.
PRIMARY = {k: "own" for k in FAMILY_LABELS}

# Stack -> display label, DERIVED from the timeframes tables rather than
# retyped, so a pair renamed there cannot leave a stale label here. Still read
# by scripts/stop_sweep.py and by the eath row labels below.
ATH_STACK_LABEL = {k: label for k, label, _ in
                   (*timeframes.VARIANTS, *timeframes.PAIRS)}


def setting_label(key: str, variant) -> str:
    """What the page's Setting column shows for one row.

    SINGLE SOURCE OF TRUTH, from 2026-09-17. The page used to hold its own
    per-family chain of ternaries keyed on hardcoded family strings ("dv",
    "pair", "e1", "eath"), which meant a family added here rendered its variant
    raw and a family renamed here rendered the wrong text -- the same hand-kept
    duplication this module's docstring was written to kill, one layer out in
    the browser. scripts.dashboard_data publishes this map; the page reads it.
    """
    return STOP_LABEL.get(str(variant), str(variant))


def _padded(key: str, band: float, ath_band: float | None = None,
            stop_mult: float | None = None):
    """simulate_variant trades, plus the two fields portfolio.run needs.

    kitelab.timeframes emits neither same_session nor net_profit; every other
    producer does. Padding here rather than in the account keeps portfolio.run
    with one trade shape to reason about.
    """
    def build(symbol):
        out = []
        for t in simulate_variant(symbol, key, band=band, ath_band=ath_band,
                                  stop_mult=stop_mult):
            t = dict(t)
            t["same_session"] = (pd.Timestamp(t["entry_ts"]).date()
                                 == pd.Timestamp(t["exit_ts"]).date())
            t["net_profit"] = t["gross_profit"] - t["charges"]
            out.append(t)
        return out
    return build


def _stem(base: str, arm: str) -> str:
    """Cache stem for one stop arm.

    The `own` arm keeps the historical stem unchanged, so every cache built
    before the stop became an axis is reused rather than recomputed. The wide
    arm gets a suffix. signals.producer_of matches by LONGEST prefix, so
    "EMA_daily_only_atr3_all" resolves to the atr3 row and not to the own row
    it happens to start with.
    """
    return base if arm == "own" else f"{base}_{arm}"


def _build_registry() -> list[Strategy]:
    """Eighteen families x two stop arms = thirty-six rows.

    TEN FAMILIES UNTIL 2026-09-19, when the eight at the bottom of
    ENTRY_FAMILIES joined and the board went 20 rows -> 36, 6,000 cells ->
    10,800. That is not free for the rows already here: 10,800 cells is 10,800
    chances to be lucky, the ~276 cells expected to clear an uncorrected
    p <= 0.05 by chance alone becomes ~497, and the Benjamini-Hochberg bar
    tightens for every incumbent. diagnostics.median_mde_80 is a property of the
    BOARD and must be re-read after a rebuild, never carried across one.

    THE SHAPE CHANGED ON 2026-09-17 and the reason is worth stating once here
    rather than only in the notes above. Until today a family's variant slot
    held whatever that family happened to sweep -- a band for the EMA rows, a
    window pair for Turtle, a stack for the pairs, a stop reading for Holy
    Grail. Four families, four incomparable axes, and no question could be
    asked ACROSS them. Now every family carries the same axis (the stop width)
    and nothing else, so "does the stop change the answer?" is one column of
    the board instead of four separate studies.

    The entry configuration each family trades is fixed and named in
    FAMILY_LABELS. Restoring a swept axis means adding rows here and widening
    the variant tag, and the grid key would need a separator that is not "|".
    """
    out: list[Strategy] = []
    for arm, mult in STOPS.items():
        # ---- the fourteen returns-blind entries (kitelab/entries.py) -------
        # One module, fourteen rules, because they share an exit (a 60-session
        # limit plus the stop) and differ only in when they fire. That is the
        # point: holding the exit fixed is what makes the entries comparable,
        # and it is also why the Pine row here is NOT the rule its author
        # trades -- scripts/wf_pine.py measures that one, with its own ATR stop
        # and target, and rejected it on 2026-09-16. This row asks the narrower
        # question the board can actually answer: is its ENTRY worth anything?
        for key, what, _name in ENTRY_FAMILIES:
            out.append(Strategy(
                key, arm, f"{what} · {STOP_LABEL[arm]}",
                _stem(f"Entry_{key}", arm), "entries.py",
                lambda s, e=key, m=mult: entries.simulate(s, e, stop_mult=m)))
        # ---- the incumbents the span pass kept -----------------------------
        out.append(Strategy(
            "e1", arm, f"EMA · daily only · {STOP_LABEL[arm]}",
            _stem("EMA_daily_only", arm), "backtest.py",
            lambda s, m=mult: backtest.simulate(s, band=SOLO_BAND,
                                                stack="daily", stop_mult=m)))
        for entry_len, exit_len in DARVAS_WINDOWS:
            for gated in DARVAS_GATED:
                base = (f"Turtle_w{darvas.WEEKLY_LEN}" if gated else "Turtle_1tf")
                out.append(Strategy(
                    "dv", arm,
                    f"Turtle {entry_len}-{exit_len}"
                    + (" + weekly" if gated else " (1 TF)")
                    + f" · {STOP_LABEL[arm]}",
                    _stem(f"{base}_{entry_len}_{exit_len}", arm), "darvas.py",
                    lambda s, a=entry_len, b=exit_len, g=gated, m=mult:
                        darvas.simulate(s, a, b, weekly=g, stop_mult=m)))
        for stack in PAIR_STACKS:
            out.append(Strategy(
                "pair", arm,
                f"EMA · {ATH_STACK_LABEL[stack]} · {STOP_LABEL[arm]}",
                _stem(f"EMA_{stack}", arm), "timeframes.py",
                _padded(stack, SOLO_BAND, stop_mult=mult)))
        # The ATH family, one row per stack in ATH_STACKS. MWD would go through
        # backtest.py (its own M/W/D implementation); MW is a timeframes.py
        # pair, where stack_signal applies the filter to the BASE frame's own
        # closes -- so a weekly-traded row compares a weekly close against the
        # highest weekly close, matching pine/ema_ath_band.pine.
        for stack in ATH_STACKS:
            pct = f"{ATH_BAND:.0%}"
            if stack == "MWD":
                out.append(Strategy(
                    "eath", arm,
                    f"EMA · M/W/D · within {pct} of the high · {STOP_LABEL[arm]}",
                    _stem(f"EMA_ath{ATH_BAND*100:g}_MWD", arm), "backtest.py",
                    lambda s, m=mult: backtest.simulate(s, band=SOLO_BAND,
                                                        ath_band=ATH_BAND,
                                                        stop_mult=m)))
            else:
                out.append(Strategy(
                    "eath", arm,
                    f"EMA · {ATH_STACK_LABEL[stack]} · within {pct} of the high"
                    f" · {STOP_LABEL[arm]}",
                    _stem(f"EMA_ath{ATH_BAND*100:g}_{stack}", arm),
                    "timeframes.py",
                    _padded(stack, SOLO_BAND, ath_band=ATH_BAND,
                            stop_mult=mult)))
        # HOLY GRAIL IS OFF THE BOARD (HG_VARIANTS is empty) and is registered
        # on the `own` arm only, so that if a row ever comes back it does not
        # silently appear twice with identical trades: holygrail.simulate has no
        # stop_mult argument, so both arms would be the same backtest under two
        # labels -- which is exactly the duplication the 2026-09-17 span pass
        # cut four other rows for. Give it a stop_mult before giving it an arm.
        if arm == "own":
            for hg_tag, hg_stop in HG_VARIANTS:
                out.append(Strategy("hg", arm, f"Holy Grail · {hg_tag} stop",
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

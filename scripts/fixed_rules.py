"""The board's eighteen rules, repaired: every entry given a direction and
every exit derived from the entry's own premise.

WHY THIS EXISTS, 2026-09-23. `scripts/exit_audit.py` measured how the board's
trades actually end: across 244,000 trades on 150 symbols, 100% of them ended
either at a fixed stop or at the 60-session cap, and under `atr3` the median
holding period was EXACTLY 60 sessions for all fourteen entries.py families.
Fourteen entries wearing one holding-period rule is not fourteen strategies.
`kitelab/entries.py`'s own docstring admits the gap in writing -- "these six
entries have no natural exit of their own ... picking it on performance would
reintroduce the hindsight this whole slate exists to avoid" -- and resolved it
by picking one arbitrary number for everything. The user's instruction was
"not hindsight, not fixed": derive the exit from what the entry claims.

THE ADMISSIBILITY RULE. An exit may enter this file by exactly three routes,
and every rule below names which one it took:

  NEGATION    the condition that justified the entry stops being true
  RESOLUTION  the thing the entry predicted has happened
  SOURCED     a number fixed OUTSIDE this project, cited, never swept here

Inadmissible: any number chosen because it scored well, and any number picked
to be uniform across rules. Nothing in this file has seen a return.

THE SOURCES, and they are the reason this is not hindsight. Researched
2026-09-23 with citations; the parameters are the authors' own.

  Connors, L. & Alvarez, C. (2008) Short Term Trading Strategies That Work.
      Short-term mean reversion exits on a close back above the 5-day SMA,
      with a 10-trading-day time stop. His own pullback setup is "close above
      the 200-day SMA and below the 5-day SMA", which is `pull` with a
      different fast average.
  Jegadeesh, N. & Titman, S. (1993) J. Finance 48(1) 65-91. Cross-sectional
      momentum: rank on a J-month return, SKIP ONE MONTH, hold a FIXED K
      months. The sourced momentum exit is a time exit, not "drops out of the
      decile" -- which is what this project assumed before checking.
  Lakonishok, J. & Smidt, S. (1988) Rev. Fin. Studies 1(4) 403-425. The
      turn-of-month window is the last trading day of the month through the
      third of the next (day -1 to +3). Our `cal` entered on day +1 and so
      skipped the first day of the effect, then held sixty sessions past its
      end.
  Raschke, L. & Connors, L. (1996) Street Smarts. Quoted verbatim from the
      copy in this repo: "Take partial profits within two to six bars and
      trail a stop on the balance"; "if the market starts to move
      parabolically or has a range expansion move, take profits on the entire
      position"; "The stop should quickly be moved to breakeven"; "If you are
      not filled on day two, the trade is cancelled."
  Wilder, J.W. (1978) New Concepts in Technical Trading Systems. RSI 30/70.
  De Bondt, W. & Thaler, R. (1985) J. Finance 40(3) 793-808. Long-horizon
      reversal is a 3-to-5 YEAR effect and is explicitly NOT significant in
      the first year -- which is evidence against `low252` as it was written,
      not for it. See that rule's note.
  Wyckoff, R. (1920s-30s), via secondary accounts. A volume extreme's meaning
      is read from where in the trend it occurs; the spike bar's own direction
      is the cheapest honest reading of that on daily bars.

WHAT COULD NOT BE SOURCED, and is therefore NEGATION or RESOLUTION only.
Crabel specifies no exit for NR4/NR7 and his actual trade is an intraday
opening-range breakout, so `vcon` has nothing to borrow. No authored gap-fade
exit exists; the published gap-fill rates are blog-grade and disagree from 29%
to 80%, so `gapdn` uses the gap itself as the target (RESOLUTION, no free
parameter) rather than any of those numbers. `mktrel` and `pine` have no
literature at all.

THE DIRECTION REPAIR. Four rules -- `vcon`, `inside`, `dryup`, `vol` -- are
setups with no direction: a coil breaks either way, a volume spike fires on
crashes as loudly as on breakouts. The board traded all four long-only, which
is a malformed rule rather than a rule with a bad exit. Every source found
agrees on the repair and none suggests trading both sides blind: the direction
comes from the breakout (Bollinger's squeeze, Carter's TTM, the universal
inside-bar convention) or, for volume, from the bar's own direction (Wyckoff).
So those four now need a TRIGGER after the setup, live for two bars, which is
Raschke's own cancellation window.

WHAT IS NOT REPAIRED HERE. The four legacy producers -- `e1`, `pair`, `eath`
(EMA stacks) and `dv` (Turtle channel) -- already exit on their entry's own
inverse: the EMA cross-under, and the 10-day channel low. They were coherent
before this file existed and are left alone. `kitelab/darvas.py:68` calls
20/10 "the Donchian rule the Turtles traded"; the parameters are Dennis and
Eckhardt's, inspired by Donchian's channel rather than published by him. That
is a docstring fix costing a four-strategy rebuild, so it is recorded here
and not made.

THE DISASTER STOP STAYS. Raschke: "The exact timing to exit a trade is a
subjective matter. What is not subjective is the initial protective stop."
Every rule below keeps the board's stop axis untouched, so this file changes
the exit and nothing else -- which is what makes the comparison against the
current board readable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from kitelab import indicators

# ---------------------------------------------------------------------------
# Parameters. Every one is either copied from the rule it repairs (so the
# repair cannot be accused of retuning the entry) or cited above. None is swept.
# ---------------------------------------------------------------------------
TRIGGER_WINDOW = 2      # Raschke: "if you are not filled on day two, cancel"
CONNORS_FAST = 5        # Connors' exit average
CONNORS_TIME = 10       # Connors' time stop, trading days
JT_SKIP = 21            # Jegadeesh-Titman one-month skip, in sessions
JT_HOLD = 63            # their K = 3 months, in sessions
TOM_END = 3             # Lakonishok-Smidt: hold through day +3
RASCHKE_BARS = 6        # "partial profits within two to six bars" -- the far end
RSI_LEN, RSI_LOW, RSI_HIGH = 14, 30.0, 70.0      # Wilder
MOM_LOOKBACK = 252
REL_LOOKBACK = 20
VOL_MEDIAN_LEN = 50
DRYUP_MULT = 0.5
VOL_SPIKE_MULT = 3.0
VCON_LEN = 7            # the coil window; its mirror is the expansion window
LOW_LOOKBACK = 252
GAP_UP, GAP_DOWN = 1.03, 0.97


def _cols(bars: pd.DataFrame) -> dict:
    """The six series every rule reads, as plain float Series."""
    need = ["ts", "open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in bars.columns]
    if missing:
        raise ValueError(f"required columns missing: {missing}")
    out = {k: pd.Series(bars[k].to_numpy(float)) for k in need[1:]}
    if out["close"].isna().any():
        raise ValueError("unexpected nulls in close")
    out["ts"] = pd.DatetimeIndex(bars["ts"])
    return out


def breakout_after(setup: pd.Series, high: pd.Series, close: pd.Series,
                   window: int = TRIGGER_WINDOW) -> pd.Series:
    """Fire when price closes above a recent setup bar's high.

    This is the direction repair. `setup` marks bars that are only a SETUP --
    a coil, an inside bar, a volume dry-up -- which say that something is
    coming without saying which way. The trade fires on the break, so the
    market supplies the direction, and the setup expires after `window` bars
    unfilled (Raschke's cancellation rule) rather than waiting indefinitely.
    """
    n = len(close)
    fires = np.zeros(n, dtype=bool)
    pending: list[tuple[int, float]] = []       # (expires_at, level)
    s = setup.to_numpy(bool)
    h = high.to_numpy(float)
    c = close.to_numpy(float)
    for i in range(n):
        pending = [(exp, lvl) for exp, lvl in pending if exp >= i]
        if pending and any(c[i] > lvl for _, lvl in pending):
            fires[i] = True
            pending = []
        if s[i] and np.isfinite(h[i]):
            pending.append((i + window, float(h[i])))
    return pd.Series(fires)


# ---------------------------------------------------------------------------
# The rules. Each returns a dict:
#   fires       bool Series, True where the (repaired) entry fires
#   exit_state  bool Series, True where an OPEN trade should close at this bar
#   time_stop   int or None -- bars, counted from the entry bar
#   target      Series or None -- read AT THE ENTRY BAR to fix one price level;
#               the trade ends on the first close at or above it
#   invalidate  Series or None -- same, but the trade ends at or BELOW it. This
#               is not the disaster stop; it is the price that proves the entry
#               wrong, which the disaster stop knows nothing about.
#   route       NEGATION / RESOLUTION / SOURCED, per the admissibility rule
#   note        what changed against the board's version, in one line
# ---------------------------------------------------------------------------

def rule_mr(d, ctx=None):
    c = d["close"]
    fires = c.le(c.rolling(20, min_periods=20).min())
    return dict(fires=fires,
                exit_state=c.gt(indicators.sma(c, CONNORS_FAST)),
                time_stop=CONNORS_TIME, target=None, route="SOURCED",
                note="exit on a close back above the 5-day SMA (Connors), "
                     "10-day time stop; was: 60-session cap")


def rule_pull(d, ctx=None):
    c = d["close"]
    sma20 = c.rolling(20, min_periods=20).mean()
    sma200 = c.rolling(200, min_periods=200).mean()
    fires = c.gt(sma200) & c.lt(sma20)
    # RESOLUTION: back above the average the dip was measured against.
    # NEGATION: below the 200-day, so the uptrend that made it a "dip" is gone.
    return dict(fires=fires,
                exit_state=c.gt(sma20) | c.lt(sma200),
                time_stop=CONNORS_TIME, target=None, route="RESOLUTION+NEGATION",
                note="exit when the close reclaims the 20-day (paid) or loses "
                     "the 200-day (thesis dead); was: 60-session cap")


def rule_rsi30(d, ctx=None):
    c = d["close"]
    r = indicators.rsi(c, RSI_LEN)
    warm = pd.Series(np.arange(len(c)) >= RSI_LEN)
    fires = r.gt(RSI_LOW) & r.shift(1).le(RSI_LOW) & warm
    # RESOLUTION at 70 (Wilder's own upper band), NEGATION back under 30.
    return dict(fires=fires,
                exit_state=r.ge(RSI_HIGH) | r.lt(RSI_LOW),
                time_stop=CONNORS_TIME, target=None, route="SOURCED",
                note="exit at RSI 70 (Wilder) or back under 30; was: 60-session cap")


def rule_low252(d, ctx=None):
    c = d["close"]
    # The entry repair. De Bondt-Thaler say a one-year low's reversal is NOT
    # significant inside the first year, so buying the low itself has published
    # evidence against it. The cheapest honest repair is to stop catching the
    # falling knife and require the reversal to have BEGUN -- the same "wait for
    # the next bar to trade back through" discipline Raschke uses for Turtle
    # Soup. It does not rescue the premise; it stops us trading against it.
    at_low = c.le(c.rolling(LOW_LOOKBACK, min_periods=LOW_LOOKBACK).min())
    fires = breakout_after(at_low, d["high"], c)
    mean252 = c.rolling(LOW_LOOKBACK, min_periods=LOW_LOOKBACK).mean()
    return dict(fires=fires, exit_state=c.gt(mean252), time_stop=None,
                target=None, route="RESOLUTION",
                note="enter only once the low is rejected; exit on a close back "
                     "above the 252-day mean; was: buy the low, 60-session cap")


def rule_xrank(d, ctx=None):
    panel = (ctx or {}).get("xrank")
    fires = panel if panel is not None else pd.Series(False, index=range(len(d["close"])))
    return dict(fires=fires, exit_state=pd.Series(False, index=range(len(d["close"]))),
                time_stop=JT_HOLD, target=None, route="SOURCED",
                note="rank skips one month and the hold is a fixed 3 months "
                     "(Jegadeesh-Titman); was: rank to today, 60-session cap")


def rule_mktrel(d, ctx=None):
    c = d["close"]
    weak = (ctx or {}).get("weak")
    if weak is None:
        weak = pd.Series(False, index=range(len(c)))
    own_up = (c / c.shift(REL_LOOKBACK) - 1.0).gt(0.0)
    fires = own_up & weak
    # NEGATION of both legs: the stock stops outperforming, or the market stops
    # being weak, in which case "up while the market is down" discriminates
    # nothing any more.
    return dict(fires=fires, exit_state=(~own_up) | (~weak), time_stop=None,
                target=None, route="NEGATION",
                note="exit when either leg of the comparison ends; was: 60-session cap")


def rule_cal(d, ctx=None):
    ts = d["ts"]
    months = ts.to_period("M")
    is_last = np.concatenate([months[:-1] != months[1:], [True]])
    fires = pd.Series(is_last)          # day -1, the day the board skipped
    # RESOLUTION: the window closes on day +3. Counted in bars from the entry,
    # so the time stop carries it; no state exit.
    return dict(fires=fires, exit_state=pd.Series(False, index=range(len(ts))),
                time_stop=TOM_END, target=None, route="SOURCED",
                note="enter on the LAST session of the month and exit on day +3 "
                     "(Lakonishok-Smidt); was: enter day +1, 60-session cap")


def rule_gapdn(d, ctx=None):
    o, c = d["open"], d["close"]
    prev = c.shift(1)
    gapped = o.le(prev * GAP_DOWN)
    # Direction repair: only fade a gap the bar itself has started to absorb.
    fires = gapped & c.gt(o)
    # RESOLUTION with no free parameter at all: the gap IS the target. Every
    # published gap-fill number found in research was blog-grade and they
    # disagree from 29% to 80%, so none is used; the trade simply ends where
    # the premise says it should. The 6-bar cap is Raschke's stated far end
    # for a short-term trade, not a number measured here.
    return dict(fires=fires, exit_state=None,
                time_stop=RASCHKE_BARS, target=prev, route="RESOLUTION",
                note="target is the gap itself (the prior close), 6-bar time stop; "
                     "was: no target, 60-session cap")


def rule_gap(d, ctx=None):
    o, c = d["open"], d["close"]
    prev = c.shift(1)
    fires = o.gt(prev * GAP_UP) & c.gt(prev)      # the gap must still be held
    # NEGATION, at a level fixed on the entry bar rather than a rolling state:
    # the gap is a claim that yesterday's close is no longer a fair price. A
    # close back under it says the claim failed. Nothing here is a free
    # parameter -- the level is the gap the entry already measured.
    return dict(fires=fires, exit_state=None, invalidate=prev, time_stop=None,
                target=None, route="NEGATION",
                note="exit on a close back inside the gap (the entry bar's prior "
                     "close); was: 60-session cap")


def rule_vcon(d, ctx=None):
    h, lo, c = d["high"], d["low"], d["close"]
    rng = h - lo
    coil = rng.le(rng.rolling(VCON_LEN, min_periods=VCON_LEN).min())
    fires = breakout_after(coil, h, c)            # direction from the break
    # RESOLUTION, and it reuses the ENTRY's own window rather than inventing a
    # number: the entry is the narrowest range of 7, so the premise is spent at
    # the widest true range of 7. Raschke: "if the market ... has a range
    # expansion move, take profits on the entire position."
    tr = indicators.true_range(h, lo, c)
    expand = tr.ge(tr.rolling(VCON_LEN, min_periods=VCON_LEN).max())
    return dict(fires=fires, exit_state=expand, time_stop=None, target=None,
                route="RESOLUTION",
                note="enter on the break of the coil, exit on the first "
                     "range-expansion bar; was: long-only, no trigger, 60-session cap")


def rule_inside(d, ctx=None):
    h, lo, c = d["high"], d["low"], d["close"]
    setup = h.lt(h.shift(1)) & lo.gt(lo.shift(1))
    fires = breakout_after(setup, h, c)
    tr = indicators.true_range(h, lo, c)
    expand = tr.ge(tr.rolling(VCON_LEN, min_periods=VCON_LEN).max())
    return dict(fires=fires, exit_state=expand, time_stop=None, target=None,
                route="RESOLUTION",
                note="enter on the break of the inside bar's high, exit on range "
                     "expansion; was: long-only, no trigger, 60-session cap")


def rule_dryup(d, ctx=None):
    v, h, c = d["volume"], d["high"], d["close"]
    vmed = v.rolling(VOL_MEDIAN_LEN, min_periods=VOL_MEDIAN_LEN).median()
    setup = v.lt(DRYUP_MULT * vmed) & vmed.gt(0)
    fires = breakout_after(setup, h, c)
    # NEGATION: the dry-up is the premise, so its end is the exit.
    return dict(fires=fires, exit_state=v.gt(vmed), time_stop=None, target=None,
                route="NEGATION",
                note="enter on the break out of the dry-up, exit when volume "
                     "returns to its median; was: long-only, no trigger, 60-session cap")


def rule_vol(d, ctx=None):
    v, c = d["volume"], d["close"]
    vmed = v.rolling(VOL_MEDIAN_LEN, min_periods=VOL_MEDIAN_LEN).median()
    spike = v.gt(VOL_SPIKE_MULT * vmed) & vmed.gt(0)
    # Direction repair, Wyckoff: a volume extreme's meaning is read from the
    # move it accompanies. On daily bars the bar's own direction is the
    # cheapest honest reading -- a spike into a down close is not our trade.
    fires = spike & c.gt(c.shift(1))
    return dict(fires=fires, exit_state=v.lt(vmed), time_stop=None, target=None,
                route="NEGATION",
                note="the spike bar must close up; exit when volume normalises; "
                     "was: any spike, long-only, 60-session cap")


def rule_pine(d, ctx=None):
    fires = (ctx or {}).get("pine")
    n = len(d["close"])
    if fires is None:
        fires = pd.Series(False, index=range(n))
    # The script already CONTAINS its exit and the board ignored it:
    # kitelab/pine.py:150 computes `ha_exit_long`, a bearish Heikin-Ashi bar
    # with an upper wick -- the exact mirror of the `bull_nowick` bar that
    # opens the trade. Paired with the trend filter failing, that is the rule's
    # own inverse and nothing had to be invented. Note that the other two entry
    # legs (the no-wick candle, the RSI turn) are TRIGGERS, not states: a
    # trigger cannot be negated into an exit, which is why only the trend leg
    # appears here.
    off = (ctx or {}).get("pine_off")
    if off is None:
        off = pd.Series(False, index=range(n))
    return dict(fires=fires, exit_state=off, time_stop=None, target=None,
                route="NEGATION",
                note="exit on the Pine's own ha_exit_long bar or the weekly "
                     "trend filter failing; was: 60-session cap")


RULES = {
    "mr": rule_mr, "pull": rule_pull, "rsi30": rule_rsi30, "low252": rule_low252,
    "xrank": rule_xrank, "mktrel": rule_mktrel, "cal": rule_cal,
    "gapdn": rule_gapdn, "gap": rule_gap, "vcon": rule_vcon,
    "inside": rule_inside, "dryup": rule_dryup, "vol": rule_vol, "pine": rule_pine,
}

# Already coherent before this file existed; listed so "eighteen" stays honest.
LEGACY_NATIVE = {
    "e1": "EMA cross-under (backtest.py exit_ok)",
    "pair": "EMA cross-under (timeframes.py)",
    "eath": "EMA cross-under (timeframes.py)",
    "dv": "close at or below the 10-day channel low (darvas.py)",
}


def build(name: str, bars: pd.DataFrame, ctx: dict | None = None) -> dict:
    """The repaired rule `name`, evaluated on `bars`."""
    if name not in RULES:
        raise ValueError(f"unknown rule {name!r}; known: {sorted(RULES)}")
    spec = RULES[name](_cols(bars), ctx)
    n = len(bars)
    blank = pd.Series(False, index=range(n))
    if spec.get("exit_state") is None:
        spec["exit_state"] = blank
    spec.setdefault("invalidate", None)
    for key in ("fires", "exit_state"):
        spec[key] = spec[key].fillna(False).to_numpy(dtype=bool)
        if len(spec[key]) != n:
            raise ValueError(f"{name}: {key} is {len(spec[key])} long, bars are {n}")
    for key in ("target", "invalidate"):
        if spec[key] is not None:
            spec[key] = spec[key].to_numpy(float)
    spec["name"] = name
    return spec

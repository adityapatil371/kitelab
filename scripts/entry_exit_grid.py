"""Which layer earns the money -- the entry, or the exit?

Follows scripts/entry_zoo.py, which established (2026-09-17) that nine
candidate entries are all distinct from the board and from each other. That
answered "are these different bets". This asks what they are worth, and
crucially SEPARATES the two decisions a rule makes: when to buy, and when to
sell.

The motivating fact came out of entry_zoo itself: 10,766 of dv|55-20's 10,768
entries are also dv|20-10 entries -- 99.98%, two different rules on the board
sharing one entry and differing only in the exit. Their RETURNS diverge
(dv|55-20 loads 0.080 on the common factor, the lowest of the nine) while
their ENTRIES are the same rule. If a board rule's distinctness can live
entirely in its exit, then measuring entries alone is measuring half the
machine.

THE DESIGN IS A CROSSED GRID, and both axes carry a control:

              stop  t5  t20  t60  ma20  chan10  r2  | rnd   <- exit control
        mr      .    .    .    .    .      .    .   |  .
        pull    .    .    .    .    .      .    .   |  .
        ...
        rand    .    .    .    .    .      .    .   |  .    <- entry control
                                                       ^^ the null cell

Every entry meets every exit. The entry control (`rand`, a coin flip at a
matched firing rate) says what an exit earns with NO entry skill behind it;
the exit control (`rnd`, a random holding period) says what an entry earns
with no exit skill. The corner cell is both, and is the number every other
cell should be read against. Same discipline as the placebo bands in
scripts/band_scope.py and the two-ended calibration in scripts/entry_zoo.py:
a control validates the MEASUREMENT, and is not itself a candidate.

CONVENTIONS, taken from the project rather than invented here:
  * entry at the signal session's own close
  * THE STOP IS THE ENTRY CANDLE'S OWN LOW, uniformly, for every cell. It is
    not one of the seven exits -- it is always on, and the exit rule is the
    ADDITIONAL way out. That is what the engine does, and item 10 found the
    stop rather than the exit sets the holding period, so an exit axis that
    left the stop out would be measuring a rule the board does not trade.
  * a gap through the stop fills at the session's OPEN, not the stop level
  * stop and target in the same bar assume the STOP
  * a gap through the target fills at the target, not the better open
  * one position per symbol at a time
  * the spread is charged on both fills, using kitelab.slippage's own ladder
    (SPREAD_K bp at Rs1cr of ADV, falling as 1/sqrt(turnover), floored at half
    a tick, capped at 200bp), vectorised here rather than called per fill.
    Impact is NOT charged: it depends on order size and only the account layer
    knows that -- see the note in slippage.fill.

WHAT THIS IS NOT, and the list matters more than the numbers:
  * NOT an account. No cash constraint, no position sizing, no priority rule,
    no limit on concurrent positions across symbols. Every signal is taken.
    kitelab's repeated finding is that the account layer takes back most of a
    gross gain (item 10, finding 3), so a good cell here is a CANDIDATE for
    the grid, never a result from it.
  * `ann_pct` is mean-return-per-trade x trades-per-stock-year. It is an
    arithmetic scaling, NOT a CAGR, and it silently assumes capital is always
    free to redeploy. Compare cells to each other with it; do not compare it
    to a buy-and-hold CAGR.
  * NO REBUILD, nothing in signals._SUPPORT or _ACCOUNT touched.

Reads:  the universe from config.load().merged and daily bars through
        kitelab.frames.daily.
Writes: output/measurements/entry_exit_grid_<date>.csv       (every cell)
        output/measurements/entry_exit_attrib_<date>.csv     (the decomposition)
        output/figures/entry_exit_grid_<date>.png
Run:    PYTHONPATH=/work/kitelab python3 -m scripts.entry_exit_grid [--pilot]
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from kitelab import config, frames, slippage

OUT = Path(__file__).resolve().parent.parent / "output"
FIG = OUT / "figures"              # every PNG
FIG.mkdir(parents=True, exist_ok=True)

SEED = 20260917
MAXHOLD = 120          # sessions; the longest any cell may hold. Caps the
                       # sliding window, and `t60` is the longest real exit.
SESSIONS_PER_YEAR = 252.0
RND_MEAN_HOLD = 15.0   # the exit control's mean holding period, in sessions

ENTRIES = ["mr", "pull", "vcon", "vol", "xrank", "mktrel", "gap", "cal", "rand"]
EXITS = ["stop", "t5", "t20", "t60", "ma20", "chan10", "r2", "rnd"]
# Item 11 (2026-09-16, scripts/stop_sweep.py, 6,624 cells) swept six stop levels
# across the nine board rules and found the incumbent -- the entry candle's own
# low -- is on the TIGHT side of the optimum for essentially every rule, worth
# +1.14 (2xATR) to +1.55 (3xATR) CAGR points a year to widen, monotone, with
# tighter never better. The first version of this grid ran every cell on the
# incumbent alone, which risked crediting the exit axis for work a too-tight
# stop was doing: a stop that ends 60-93% of trades leaves an exit rule very
# little to act on. So the stop is a THIRD AXIS here, not a constant.
STOPS = ["own", "atr2", "atr3"]
ENTRY_CONTROL, EXIT_CONTROL = "rand", "rnd"
REQUIRED = ["ts", "open", "high", "low", "close", "volume"]


def panels(members, pilot=False):
    """Wide frames (sessions x symbols) of O/H/L/C/V plus trailing ADV.

    ADV is built here, per symbol, with exactly the recipe in slippage.profile
    -- a 60-session rolling median of close*volume, SHIFTED ONE SESSION so a
    trade on day D is priced with what was known by D-1. Recomputing it on the
    wide panel instead would let one stock's suspension days leak into
    another's window; computing it on the symbol's own series cannot.
    """
    if pilot:
        members = members[:150]
    keys = ("open", "high", "low", "close", "volume", "adv")
    cols = {k: {} for k in keys}
    skipped = []
    for i, symbol in enumerate(members, 1):
        try:
            bars = frames.daily(symbol)
        except SystemExit:
            skipped.append(symbol)
            continue
        if bars.empty:
            skipped.append(symbol)
            continue
        missing = [c for c in REQUIRED if c not in bars.columns]
        if missing:
            raise SystemExit(f"{symbol}: daily frame lacks {missing}")
        idx = pd.DatetimeIndex(bars["ts"]).normalize()
        close = bars["close"].astype(float)
        turn = close * bars["volume"].astype(float)
        adv = turn.rolling(slippage.ADV_WINDOW, min_periods=5).median().shift(1)
        adv = adv.fillna(turn.expanding(min_periods=1).median().shift(1))
        series = {k: bars[k].to_numpy(float) for k in REQUIRED[1:]}
        series["adv"] = adv.to_numpy(float)
        for k in keys:
            s = pd.Series(series[k], index=idx)
            cols[k][symbol] = s[~s.index.duplicated(keep="last")]
        if i % 250 == 0:
            print(f"    ... {i:,} of {len(members):,} symbols read")
    if skipped:
        print(f"  {len(skipped):,} symbols had no usable daily frame")
    out = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    n_rows, n_cols = out["close"].shape
    print(f"  panel: {n_rows:,} sessions x {n_cols:,} symbols, "
          f"{out['close'].index.min().date()} to {out['close'].index.max().date()}")
    print(f"  stock-sessions with a price: "
          f"{int(out['close'].notna().to_numpy().sum()):,}")
    return out


def build_signals(p):
    """The nine entry panels. Identical definitions to scripts/entry_zoo.py."""
    o, h, l, c, v = (p["open"], p["high"], p["low"], p["close"], p["volume"])
    listed = c.notna()
    sig = {}
    sig["mr"] = c.le(c.rolling(20, min_periods=20).min())
    sig["pull"] = c.gt(c.rolling(200, min_periods=200).mean()) & \
                  c.lt(c.rolling(20, min_periods=20).mean())
    rng = h - l
    sig["vcon"] = rng.le(rng.rolling(7, min_periods=7).min())
    vmed = v.rolling(50, min_periods=50).median()
    sig["vol"] = v.gt(3.0 * vmed) & vmed.gt(0)
    sig["xrank"] = (c / c.shift(252) - 1.0).rank(axis=1, pct=True).gt(0.90)
    proxy = (1.0 + c.pct_change(fill_method=None).mean(axis=1, skipna=True)
             .fillna(0.0)).cumprod()
    weak = (proxy / proxy.shift(20) - 1.0) < 0
    sig["mktrel"] = (c / c.shift(20) - 1.0).gt(0) & pd.DataFrame(
        np.repeat(weak.to_numpy()[:, None], c.shape[1], axis=1),
        index=c.index, columns=c.columns)
    sig["gap"] = o.gt(c.shift(1) * 1.03)
    first = pd.Series(c.index, index=c.index).groupby(
        [c.index.year, c.index.month]).transform("min") == pd.Series(
        c.index, index=c.index)
    sig["cal"] = pd.DataFrame(
        np.repeat(first.to_numpy()[:, None], c.shape[1], axis=1),
        index=c.index, columns=c.columns)
    rates = [float((sig[k] & listed).to_numpy().sum()) /
             float(listed.to_numpy().sum()) for k in sig]
    rate = float(np.median(rates))
    sig["rand"] = pd.DataFrame(
        np.random.default_rng(SEED).random(c.shape) < rate,
        index=c.index, columns=c.columns)
    print(f"  entry control `rand` fires at {100 * rate:.3f}% of stock-sessions")
    for k in sig:
        sig[k] = (sig[k].fillna(False) & listed)
    return sig


def half_spread_panel(p):
    """kitelab.slippage.half_spread, vectorised over the whole panel.

    Same three pieces, same order: a floor of half a tick, a 1/sqrt(ADV)
    ladder, a 200bp cap. Where ADV is missing or non-positive the scalar
    version returns the cap, and so does this. Checked against the scalar
    function on a sample before use -- see step 3 of main().
    """
    price = p["close"].to_numpy(float)
    adv = p["adv"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        floor = np.where(price > 0, (slippage.TICK / 2.0) / price, 0.0)
        ladder = (slippage.SPREAD_K / np.sqrt(adv / 1e7)) / 10_000.0
    hs = np.minimum(slippage.MAX_HALF_SPREAD, np.maximum(floor, ladder))
    bad = ~np.isfinite(adv) | (adv <= 0)
    hs[bad] = slippage.MAX_HALF_SPREAD
    return np.nan_to_num(hs, nan=slippage.MAX_HALF_SPREAD)


def exits_for_symbol(o, h, l, c, stop, rng):
    """For EVERY bar i, where does a position opened at close[i] get out?

    `stop` is the exit line for a position opened at bar i, one value per bar.

    Returns {exit_name: (exit_index, exit_price)}, both length n. The exit of
    a trade opened at bar i does not depend on WHICH entry rule fired at i, so
    this is computed once per symbol and reused by all nine entries -- that is
    what makes a 72-cell grid affordable.

    Windowed at MAXHOLD sessions forward. A position still open at the end of
    the window is closed there; so is one whose window runs off the end of the
    series, which is why the tail of `valid` is False.
    """
    n = len(c)
    pad = np.full(MAXHOLD, np.nan)
    ow, hw, lw, cw = (sliding_window_view(np.concatenate([x[1:], pad]),
                                          MAXHOLD) for x in (o, h, l, c))
    ow, hw, lw, cw = ow[:n], hw[:n], lw[:n], cw[:n]
    ks = np.arange(MAXHOLD)

    hit_stop = lw <= stop[:, None]

    def first(mask):
        """Index of the first True in each row, or MAXHOLD-1 if never."""
        any_ = mask.any(axis=1)
        return np.where(any_, mask.argmax(axis=1), MAXHOLD - 1), any_

    k_stop, _ = first(hit_stop)
    sma20 = pd.Series(c).rolling(20, min_periods=20).mean().to_numpy()
    sma20w = sliding_window_view(np.concatenate([sma20[1:], pad]), MAXHOLD)[:n]
    chan = pd.Series(l).rolling(10, min_periods=10).min().shift(1).to_numpy()
    chanw = sliding_window_view(np.concatenate([chan[1:], pad]), MAXHOLD)[:n]
    target = c + 2.0 * (c - stop)                # 2R, off the intended risk

    rules = {
        "stop":   np.zeros_like(hit_stop),
        "t5":     ks[None, :] >= 4,
        "t20":    ks[None, :] >= 19,
        "t60":    ks[None, :] >= 59,
        "ma20":   cw < sma20w,
        "chan10": cw < chanw,
        "r2":     hw >= target[:, None],
        "rnd":    ks[None, :] >= np.minimum(
                      rng.geometric(1.0 / RND_MEAN_HOLD, size=n), MAXHOLD
                  )[:, None] - 1,
    }

    out = {}
    for name, trig in rules.items():
        k_rule, _ = first(np.asarray(trig) & np.isfinite(cw))
        k = np.minimum(k_stop, k_rule)
        # Which one bound? On a tie the STOP binds -- the project's convention
        # for a bar holding both, and the conservative reading.
        by_stop = hit_stop[np.arange(n), k]
        opens = ow[np.arange(n), k]
        closes = cw[np.arange(n), k]
        # A gap through the stop fills at the open, never at the stop level.
        stop_px = np.minimum(opens, stop)
        # A gap through the target fills AT the target, never at the better open.
        if name == "r2":
            # target reached on the binding bar -> fill AT the target; otherwise
            # the window ran out and we leave at that bar's close.
            px = np.where(by_stop, stop_px,
                          np.where(hw[np.arange(n), k] >= target, target, closes))
        else:
            px = np.where(by_stop, stop_px, closes)
        out[name] = (k + 1, px, by_stop)         # k+1 sessions after entry
    return out


def walk(sig_idx, k_out, n):
    """One position at a time: take a signal, then skip to after its exit."""
    taken = []
    j = -1
    for i in sig_idx:
        if i <= j or i + k_out[i] >= n:
            continue
        taken.append(i)
        j = i + int(k_out[i])
    return np.asarray(taken, dtype=int)


def main():
    pilot = "--pilot" in sys.argv
    started = time.time()
    print(f"entry_exit_grid{'  [PILOT]' if pilot else ''} — which layer earns "
          f"it, the entry or the exit?\n")

    members = list(config.load().merged)
    print(f"1. universe: {len(members)} stocks from config.load().merged")
    p = panels(members, pilot)

    print("\n2. the nine entry panels")
    sig = build_signals(p)

    print("\n3. the spread panel, checked against slippage.half_spread itself")
    hs = half_spread_panel(p)
    slippage.ENABLED = True
    rng_chk = np.random.default_rng(1)
    cols, idx = list(p["close"].columns), p["close"].index
    checked = worst = 0
    for _ in range(300):
        ci = int(rng_chk.integers(len(cols)))
        ri = int(rng_chk.integers(len(idx)))
        px = p["close"].to_numpy()[ri, ci]
        if not np.isfinite(px) or px <= 0:
            continue
        ref = slippage.half_spread(cols[ci], idx[ri], float(px))
        worst = max(worst, abs(ref - hs[ri, ci]))
        checked += 1
    slippage.ENABLED = False
    print(f"  {checked} random cells vs the scalar function, "
          f"max abs difference {worst:.3e}")
    if worst > 1e-9:
        raise SystemExit("vectorised half-spread does not match slippage.py")

    print(f"\n4. the {len(ENTRIES)} x {len(EXITS)} grid "
          f"({len(ENTRIES) * len(EXITS)} cells), one pass per symbol")
    O, H, L, C = (p[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    sig_np = {k: sig[k].to_numpy() for k in ENTRIES}
    acc = {(e, x, st): {"ret": [], "hold": [], "R": [], "bystop": []}
           for e in ENTRIES for x in EXITS for st in STOPS}
    rng = np.random.default_rng(SEED + 1)
    n_sym = len(cols)
    for ci in range(n_sym):
        c = C[:, ci]
        ok = np.isfinite(c)
        if ok.sum() < 260:
            continue
        s, e = int(np.argmax(ok)), int(len(ok) - np.argmax(ok[::-1]))
        o, h, l, c = O[s:e, ci], H[s:e, ci], L[s:e, ci], C[s:e, ci]
        n = len(c)
        if n < 260 or not np.isfinite(c).all():
            c = np.nan_to_num(c, nan=0.0)
        # ATR(14), Wilder's true range, for the two wider stops.
        pc = np.concatenate([[np.nan], c[:-1]])
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
        stop_lines = {"own": l, "atr2": c - 2.0 * atr, "atr3": c - 3.0 * atr}
        spread = hs[s:e, ci]
        for sname in STOPS:
            stop_l = stop_lines[sname]
            risk = c - stop_l
            good = np.isfinite(risk) & (risk > 0)
            ex = exits_for_symbol(o, h, l, c, stop_l, rng)
            for ename in ENTRIES:
                fires = np.flatnonzero(sig_np[ename][s:e, ci] & good)
                if not len(fires):
                    continue
                for xname in EXITS:
                    k_out, px_out, by_stop = ex[xname]
                    idxs = walk(fires, k_out, n)
                    if not len(idxs):
                        continue
                    jdx = idxs + k_out[idxs]
                    entry_px = c[idxs] * (1.0 + spread[idxs])
                    exit_px = px_out[idxs] * (1.0 - spread[np.minimum(jdx, n - 1)])
                    fin = (np.isfinite(entry_px) & np.isfinite(exit_px)
                           & (entry_px > 0))
                    if not fin.any():
                        continue
                    a = acc[(ename, xname, sname)]
                    a["ret"].append((exit_px[fin] - entry_px[fin]) / entry_px[fin])
                    a["hold"].append(k_out[idxs][fin])
                    a["R"].append((exit_px[fin] - entry_px[fin]) / risk[idxs][fin])
                    a["bystop"].append(by_stop[idxs][fin])
        if (ci + 1) % 200 == 0:
            print(f"    ... {ci + 1:,} of {n_sym:,} symbols simulated "
                  f"({time.time() - started:.0f}s)")

    print("\n5. cells")
    stock_years = float(np.isfinite(C).sum()) / SESSIONS_PER_YEAR
    rows = []
    for (ename, xname, sname), a in acc.items():
        if not a["ret"]:
            continue
        ret = np.concatenate(a["ret"])
        hold = np.concatenate(a["hold"])
        R = np.concatenate(a["R"])
        bs = np.concatenate(a["bystop"])
        lo, hi = np.percentile(R, [1, 99])
        rows.append({
            "entry": ename, "exit": xname, "stop_rule": sname,
            "trades": len(ret),
            "ret_per_trade_pct": 100.0 * ret.mean(),
            "win_rate_pct": 100.0 * (ret > 0).mean(),
            "median_hold": float(np.median(hold)),
            "trades_per_stock_year": len(ret) / stock_years,
            "ann_pct": 100.0 * ret.mean() * len(ret) / stock_years,
            "exposure_pct": 100.0 * hold.sum() / (stock_years * SESSIONS_PER_YEAR),
            "ret_per_session_bp": 10_000.0 * ret.sum() / hold.sum(),
            "mean_R_w": float(np.clip(R, lo, hi).mean()),
            "pct_ended_by_stop": 100.0 * bs.mean(),
            "ret_when_stop_pct": 100.0 * ret[bs].mean() if bs.any() else float("nan"),
            "ret_when_rule_pct": 100.0 * ret[~bs].mean() if (~bs).any() else float("nan"),
        })
    grid = pd.DataFrame(rows)

    # Buy-and-hold on the same panel: the equal-weighted mean daily return of
    # every listed stock-session, which is what a cell's ret_per_session_bp has
    # to beat before any of it is skill rather than drift. No costs -- holding
    # crosses the spread twice in a lifetime, and charging it would flatter the
    # rules.
    dr = p["close"].pct_change(fill_method=None).to_numpy(float)
    dr = dr[np.isfinite(dr)]
    hold_bp = 10_000.0 * dr.mean()
    print(f"\n   BENCHMARK, same panel: simply owning the stock earned "
          f"{hold_bp:,.2f} bp per session,\n   equal-weighted, costs off. A cell "
          f"beats DRIFT only if its ret_per_session_bp\n   exceeds this; ann_pct "
          f"alone cannot tell you that.\n"
          f"   Deliberately NOT annualised: this is an ARITHMETIC mean of daily "
          f"returns and\n   compounding it would produce a number far above the "
          f"universe's real CAGR (the\n   arithmetic mean exceeds the geometric "
          f"by half the variance, which is large in\n   631 small caps). It is "
          f"quoted per session because every cell is measured the\n   same way, "
          f"so the DIFFERENCE is fair even though the level is not a return "
          f"anyone\n   earned.")
    grid["vs_hold_bp"] = grid["ret_per_session_bp"] - hold_bp
    def pv(metric, stop_rule):
        return grid[grid["stop_rule"] == stop_rule].pivot(
            index="entry", columns="exit", values=metric).reindex(
            index=ENTRIES, columns=EXITS)
    piv = pv("ann_pct", "own")
    print("\n   ann_pct = mean return per trade x trades per stock-year, spread "
          "charged.\n   An arithmetic scaling, NOT a CAGR and NOT an account. "
          "Read cells against\n   each other and against the controls, never "
          "against a buy-and-hold number.\n")
    print(piv.to_string(float_format=lambda v: f"{v:,.2f}"))

    psv = pv("vs_hold_bp", "own")
    print("\n   The same 72 cells per SESSION IN THE MARKET, minus the drift a "
          "holder got for\n   free over the same sessions (bp/session). This is "
          "the exposure-adjusted view:\n   it asks whether the rule picks better "
          "days, not whether it trades more often.\n")
    print(psv.to_string(float_format=lambda v: f"{v:,.2f}"))

    expo = pv("exposure_pct", "own")
    print("\n   ...and the exposure behind those numbers (% of all stock-sessions "
          "held):\n")
    print(expo.to_string(float_format=lambda v: f"{v:,.1f}"))

    print("\n5b. WHO ENDS THE TRADE. The stop is on in every cell, so an exit "
          "rule only\n    binds when it fires FIRST. Averaged over the eight "
          "entries:\n")
    who = grid[(grid["entry"] != ENTRY_CONTROL)
               & (grid["stop_rule"] == "own")].groupby("exit").agg(
        pct_ended_by_stop=("pct_ended_by_stop", "mean"),
        ret_when_stop_pct=("ret_when_stop_pct", "mean"),
        ret_when_rule_pct=("ret_when_rule_pct", "mean")).reindex(EXITS)
    who["rule_minus_stop"] = who["ret_when_rule_pct"] - who["ret_when_stop_pct"]
    print(who.to_string(float_format=lambda v: f"{v:,.2f}"))
    print("\n    If `ret_when_rule_pct` is POSITIVE the exit rule is cutting "
          "trades that were\n    in profit -- the stop had already taken the "
          "losers. That is the mechanism to\n    check before claiming the exit "
          "axis is about cutting winners short.")

    print("\n6. the controls, which is how the table above becomes readable")
    null = float(piv.loc[ENTRY_CONTROL, EXIT_CONTROL])
    print(f"   null cell (random entry, random exit): {null:,.2f}")
    print(f"   random entry, best real exit:          "
          f"{piv.loc[ENTRY_CONTROL, EXITS[:-1]].max():,.2f}"
          f"  ({piv.loc[ENTRY_CONTROL, EXITS[:-1]].idxmax()})")
    print(f"   best real entry, random exit:          "
          f"{piv.loc[ENTRIES[:-1], EXIT_CONTROL].max():,.2f}"
          f"  ({piv.loc[ENTRIES[:-1], EXIT_CONTROL].idxmax()})")

    print("\n7. attribution — how much of the spread across the 56 real cells "
          "is the\n   entry choice, how much the exit choice, how much their "
          "interaction")
    def decompose(table, label):
        real = table.loc[ENTRIES[:-1], EXITS[:-1]]
        gm = real.to_numpy().mean()
        row_eff = real.mean(axis=1) - gm
        col_eff = real.mean(axis=0) - gm
        inter = (real.to_numpy() - gm - row_eff.to_numpy()[:, None]
                 - col_eff.to_numpy()[None, :])
        ss = [float(len(EXITS[:-1]) * (row_eff ** 2).sum()),
              float(len(ENTRIES[:-1]) * (col_eff ** 2).sum()),
              float((inter ** 2).sum())]
        tot = sum(ss)
        return pd.DataFrame({
            "metric": label,
            "source": ["entry choice", "exit choice", "interaction"],
            "sum_of_squares": ss,
            "share_pct": [100 * v / tot for v in ss]}), row_eff, col_eff

    blocks, effs = [], {}
    for sname in STOPS:
        for metric, lab in (("ann_pct", "ann_pct"),
                            ("vs_hold_bp", "vs_hold_bp (exposure-adj)")):
            b, r_, c_ = decompose(pv(metric, sname), f"{lab} @ stop={sname}")
            blocks.append(b)
            effs[(metric, sname)] = (r_, c_)
    attrib = pd.concat(blocks, ignore_index=True)
    print(attrib.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print("\n   THE STOP AXIS IS THE CHECK ON FINDING 1. Item 11 measured the "
          "incumbent\n   stop to be too tight; if 'the exit dominates' were an "
          "artifact of that, the\n   exit share would collapse as the stop "
          "widens. Read the six rows above.")
    print("\n   Best cell at each stop, per session vs hold (bp):")
    for sname in STOPS:
        t = pv("vs_hold_bp", sname).loc[ENTRIES[:-1], EXITS[:-1]]
        i, j = np.unravel_index(np.nanargmax(t.to_numpy()), t.shape)
        n_beat = int((pv("vs_hold_bp", sname).to_numpy() > 0).sum())
        print(f"     stop={sname:<5} best {t.index[i]:>6} x {t.columns[j]:<7}"
              f" {t.to_numpy()[i, j]:+7.2f}   cells beating hold: "
              f"{n_beat:>2} of {len(ENTRIES) * len(EXITS)}")
    row_eff, col_eff = effs[("ann_pct", "own")]
    row2, col2 = effs[("vs_hold_bp", "own")]
    print("\n   Read the SECOND block. ann_pct rewards an exit for trading "
          "more often, so\n   part of its exit share is arithmetic rather "
          "than skill; vs_hold_bp divides\n   that out and asks the same "
          "question per session held.")
    print("\n   exposure-adjusted entry effects: " +
          ", ".join(f"{k} {v:+.2f}" for k, v in
                    row2.sort_values(ascending=False).items()))
    print("   exposure-adjusted exit  effects: " +
          ", ".join(f"{k} {v:+.2f}" for k, v in
                    col2.sort_values(ascending=False).items()))
    print("\n   entry effects (best to worst):")
    print("   " + row_eff.sort_values(ascending=False).round(2).to_string()
          .replace("\n", "\n   "))
    print("\n   exit effects (best to worst):")
    print("   " + col_eff.sort_values(ascending=False).round(2).to_string()
          .replace("\n", "\n   "))

    stamp = date.today().isoformat()
    meas = OUT / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    grid.to_csv(meas / f"entry_exit_grid_{stamp}.csv", index=False)
    attrib.to_csv(meas / f"entry_exit_attrib_{stamp}.csv", index=False)

    fig, ax = plt.subplots(figsize=(10, 6))
    d = piv.to_numpy(float)
    lim = np.nanmax(np.abs(d))
    im = ax.imshow(d, cmap="RdBu_r", vmin=-lim, vmax=lim, aspect="auto")
    ax.set_xticks(range(len(EXITS))); ax.set_xticklabels(EXITS)
    ax.set_yticks(range(len(ENTRIES))); ax.set_yticklabels(ENTRIES)
    ax.axvline(len(EXITS) - 1.5, color="k", lw=2)
    ax.axhline(len(ENTRIES) - 1.5, color="k", lw=2)
    for i in range(d.shape[0]):
        for j in range(d.shape[1]):
            ax.text(j, i, f"{d[i, j]:.1f}", ha="center", va="center", fontsize=7,
                    color="white" if abs(d[i, j]) > 0.6 * lim else "black")
    fig.colorbar(im, ax=ax, label="ann_pct (not a CAGR, no account)")
    ax.set_title("Entry x exit, spread charged, no account\n"
                 "last row = random entry, last column = random exit; "
                 "the corner is the null")
    fig.tight_layout()
    png = FIG / f"entry_exit_grid_{stamp}.png"
    fig.savefig(png, dpi=130); plt.close(fig)
    print(f"\nwrote {meas / f'entry_exit_grid_{stamp}.csv'}")
    print(f"wrote {meas / f'entry_exit_attrib_{stamp}.csv'}")
    print(f"wrote {png}")
    print(f"elapsed {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()

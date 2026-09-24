"""WHERE DOES THE MONEY DIE? A layer-by-layer decomposition.

THE CONTRADICTION THIS EXISTS TO RESOLVE. Two things are both measured and
both solid. `scripts/us_rules.py` shows the entry rules genuinely beat random
timing -- +35 to +100 bps a trade, in two markets that agree with each other.
The board shows the same rules losing to buy-and-hold by ~18 points a year,
0 of 36 rows beating it. Those cannot both be true unless something BETWEEN
the signal and the account is destroying the edge.

Nobody has measured the between. Every remaining idea in this project -- better
exits, fewer positions, different sizing -- is a bet on one particular layer
being the thief. This names the thief instead of guessing.

THE LADDER. Every rung is the same universe, same period, same rules, and is
reported in the SAME unit (account CAGR, %/yr) so the rungs can be subtracted
from each other. Each rung adds exactly one friction to the one above it:

  A  random timing, no costs, unlimited positions
  A0 random timing, no costs, NO STOP
  B  RULE timing, no costs, unlimited positions      B-A = the entry edge
  X  rule timing, no stop, fixed 60-day exit         X-B = what the stop costs
  C  rule timing + brokerage and slippage            C-B = what costs take
  D0 rule timing + costs + 20 positions max          D0-C = capital rationing
  D1 rule timing + costs + 10 positions max          D1-D0 = more rationing
  E  RANDOM timing + costs + 20 positions max        D0-E = the honest final
  F  buy and hold, equal weight, same universe       the benchmark

WHY RUNG E EXISTS. D0 and D1 carry two frictions the other rungs do not: the
20-slot limit and the 1% fill cap. The fill cap is not a cost -- it shrinks
the order in a thin name and leaves the money in cash, which is a LIQUIDITY
SCREEN wearing a friction's clothes. On NSE that screen is worth more than
every entry rule on the board: the capped rung beat buy-and-hold in 26 of 26
arms, including arms whose entry is known to have no edge. Rung E charges the
identical screen to random timing, so D0-E asks the only question that matters
-- does the ENTRY add anything once both sides own the same screen?

THE ACCOUNT MODEL. Equity is spread equally over whatever positions are open;
with nothing open it sits in cash earning zero. That is the same equal-weight
convention `scripts/vol_target.py` uses for its baskets, so rung F is directly
comparable. Round-trip cost is charged in full on the entry day. Under a
position limit, signals that arrive with no free slot are DROPPED, not queued
-- a dropped signal is what capital rationing actually feels like.

THE FILL CAP needs a capital figure to mean anything (an order is too big only
relative to the bar it must fill in), so --capital is an explicit assumption,
reported, and swept. It applies only on the rungs that have a position limit,
which is also the only place it is real.

READS:  CLEAN/US_<SYM>_day.parquet and CLEAN/<SYM>_day.parquet
WRITES: output/measurements/waterfall_<date>.csv
        output/figures/waterfall_<date>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitelab import entries, indicators                                # noqa: E402
from scripts.wf_intraday import FAMILIES, PANEL, STOPS, close_panel, \
    panel_masks, fires_for                                             # noqa: E402
from scripts.us_rules import load, us_universe, nse_universe, _exit_from, MIN_SESSIONS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output"
SESSIONS = 252
MAXHOLD = entries.MAXHOLD
COST_BPS = 23.0            # round trip, NSE delivery, the board's own figure
SLOTS = [20, 10]
CAP_FRAC = 0.01            # you may take 1% of a bar's traded value
MAX_DAY = 5.00             # see scripts.vol_target -- CRUDEOIL is not a price


def trades_for(sym, bars, family, stop_mult, masks, rng, mode):
    """Trade list as (entry_i, exit_i). mode: 'rule' | 'random' | 'nostop'.

    'nostop' keeps the rule's entries and replaces the exit with a flat
    60-session hold, which is the only way to price the stop separately.
    """
    c = bars["close"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    atr = indicators.atr(bars["high"], bars["low"], bars["close"],
                         entries.ATR_LEN).to_numpy(float)
    # A stop may be a NUMBER (multiples of ATR, the board's convention) or a
    # CALLABLE, in which case it is asked for a per-bar stop DISTANCE in price
    # units and the multiple becomes 1.0. That is the whole hook a tailored
    # stop needs: `_exit_from` already computes c[i] - mult * atr[i], so a
    # distance array slotted into `atr` with mult 1.0 is exactly right, and
    # the exit logic below stays byte-identical between the board's stops and
    # a tailored one. The callable never sees returns -- only bars.
    stop_arg = stop_mult          # the UNREWRITTEN stop, for the recursion below
    if callable(stop_mult):
        atr = stop_mult(bars, family)
        stop_mult = 1.0
    total = len(bars)
    out = []

    if mode in ("rule", "nostop"):      # NOT randomnostop -- that is a control
        fires = fires_for(sym, family, bars, masks)
        pos = 0
        while pos < total:
            if not (fires[pos] and pos > 0 and not fires[pos - 1]):
                pos += 1
                continue
            if mode in ("nostop",):
                exit_i = min(pos + MAXHOLD, total - 1)
                if exit_i <= pos:
                    break
                out.append((pos, exit_i))
                pos = exit_i + 1
                continue
            got = _exit_from(pos, c, lo, atr, stop_mult, total)
            if got is None:
                pos += 1
                continue
            out.append((pos, got[0]))
            pos = got[0] + 1
        return out

    # random arms: same count as the rule arm, random date. 'random' keeps the
    # board's exit; 'randomnostop' replaces it with the flat 60-session hold,
    # which gives the 2x2 -- {rule, random} x {board exit, no stop} -- that
    # separates what the ENTRY is worth from what the EXIT costs.
    flat = mode == "randomnostop"
    # match the count of the arm this is a control FOR: the flat-exit control
    # against the flat-exit rule arm, the stopped control against the stopped
    # rule arm. Matching both to `rule` gave the flat control 144 trades
    # against the flat rule arm's 128 -- a breadth edge, not a timing test.
    # stop_arg, NOT stop_mult: by here a callable stop has been rewritten to
    # 1.0, and passing that down would silently give the control a 1x-ATR stop
    # against a tailored rule arm -- a stop-width difference wearing a timing
    # test's clothes.
    n = len(trades_for(sym, bars, family, stop_arg, masks, rng,
                       "nostop" if flat else "rule"))
    if not n:
        return out
    lo_i, hi_i = entries.ATR_LEN + 1, total - 2
    if hi_i <= lo_i:
        return out
    # NON-OVERLAPPING, like the rule arm. Independent draws collide, and the
    # account refuses to pyramid into a symbol it already holds -- so naive
    # draws silently lost ~30% of the control's trades at the account layer
    # and the baseline stopped being matched.
    tries, busy = 0, []
    while len(out) < n and tries < n * 40:
        tries += 1
        j = int(rng.integers(lo_i, hi_i))
        if any(a <= j <= b for a, b in busy):
            continue
        if flat:
            ex = min(j + MAXHOLD, total - 1)
            got = None if ex <= j else (ex, c[ex] / c[j] - 1.0)
        else:
            got = _exit_from(j, c, lo, atr, stop_mult, total)
        if got is None:
            continue
        if any(j <= a <= got[0] for a, b in busy):
            continue
        out.append((j, got[0]))
        busy.append((j, got[0]))
    return sorted(out)


def account(book, dates, rets, turn, slots, cost_bps, capital):
    """Walk the calendar. `book` maps symbol -> list of (entry_i, exit_i) as
    positions in that symbol's OWN bar index, already translated to positions
    in `dates`. Returns the daily portfolio return series."""
    n = len(dates)
    opens = [[] for _ in range(n)]
    for sym, tl in book.items():
        for a, b in tl:
            # The trade enters at the CLOSE of bar a, so it earns nothing on
            # bar a itself -- it starts accruing on a+1 and ends on b. Booking
            # it from a credited the account with the entry day's move, which
            # quietly punished every dip-buying rule for the dip it bought.
            if 0 <= a + 1 < n and a + 1 <= b < n:
                opens[a + 1].append((sym, b))

    held: dict[str, int] = {}
    nopen_log = np.zeros(n, dtype=int)
    # How much of the book is actually in the market each day. The fill cap
    # shrinks a position's weight and the freed money earns zero, so a heavily
    # capped account is part cash without ever showing a cash DAY -- which
    # `pct_days_cash` cannot see. Without this the 20-slot rungs are unreadable.
    expo_log = np.zeros(n)
    port = np.zeros(n)
    eq, taken, dropped, capped = 1.0, 0, 0, 0
    for t in range(n):
        for sym, b in opens[t]:
            if slots is not None and len(held) >= slots:
                dropped += 1
                continue
            if sym in held:                    # already in it, no pyramiding
                continue
            held[sym] = b
            taken += 1
        nopen_log[t] = len(held)
        if held:
            w = 1.0 / len(held)
            day, wsum = 0.0, 0.0
            for sym in list(held):
                r = rets.get(sym)
                x = r[t] if r is not None and np.isfinite(r[t]) else 0.0
                weight = w
                if slots is not None and capital:
                    # The order you must place, against the bar it must fill in.
                    # Sized off a FIXED book, not eq * capital. With the equity
                    # multiplier in here the cap tightened as the account
                    # compounded: a book that grew 50x could place only 2% of
                    # its intended order, so the strategy quietly switched
                    # itself off after its good years. NSE's 20-slot rung then
                    # printed 23.1% CAGR at a 13.8% drawdown while sitting 78%
                    # in cash -- an early-years return annualised over the whole
                    # span, and compared against a hold that was 100% invested
                    # throughout. A fixed book is also what the board assumes
                    # (sizing.CAPITAL, the Rs 1cr paper book).
                    order = capital * w
                    tv = turn[sym][t] if turn.get(sym) is not None else np.inf
                    room = CAP_FRAC * tv if np.isfinite(tv) else np.inf
                    if order > room > 0:
                        weight = w * (room / order)
                        capped += 1
                day += weight * x
                wsum += weight
            expo_log[t] = wsum
            # round trip charged in full on the entry day
            fresh = sum(1 for sym, b in opens[t] if held.get(sym) == b)
            day -= fresh * w * cost_bps / 1e4
            port[t] = day
            eq *= (1.0 + day)
        for sym in [s for s, b in held.items() if b <= t]:
            del held[sym]
    return (pd.Series(port, index=dates), taken, dropped, capped,
            _open_profile(nopen_log) + (float(100.0 * expo_log.mean()),))


def _open_profile(n: np.ndarray):
    """How the account's exposure is distributed over the calendar.

    `crowding` is the share of all position-days that land on the busiest 10%
    of sessions. A rule whose signals all fire in the same week cannot
    diversify its losses against each other however many symbols it holds --
    that is concentration in TIME, which the per-trade average cannot see.
    """
    if not len(n) or n.sum() == 0:
        return 0.0, 100.0, 0.0
    k = max(1, int(round(0.10 * len(n))))
    top = np.sort(n)[-k:]
    return (float(np.median(n)), float(100.0 * (n == 0).mean()),
            float(100.0 * top.sum() / n.sum()))


def cagr_of(r: pd.Series) -> float:
    eq = float((1.0 + r).prod())
    yrs = len(r) / SESSIONS
    return (eq ** (1 / yrs) - 1.0) * 100.0 if eq > 0 and yrs > 0 else -100.0


def maxdd_of(r: pd.Series) -> float:
    e = (1.0 + r).cumprod()
    return float((e / e.cummax() - 1.0).min() * 100.0)


def run_universe(tag, syms, fams, capital, t0, start=None):
    print(f"\n=== {tag}: loading {len(syms)} symbols ===")
    bars, dropped_bad = {}, []
    for s in syms:
        b = load(s)
        if b is None:
            continue
        c = b["close"].to_numpy(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            mv = np.nanmax(np.abs(np.diff(c) / c[:-1])) if len(c) > 1 else 0.0
        if mv > MAX_DAY:                       # CRUDEOIL and friends
            dropped_bad.append((s, mv * 100))
            continue
        # Trimmed here, before anything reads the frame, so the rules, the
        # random controls and buy-and-hold all see the identical history.
        # Indicators warm up from the new start rather than carrying state
        # across the cut, which is what an investor starting that year has.
        if start is not None:
            b = b[pd.DatetimeIndex(b["ts"]) >= start]
            if len(b) < MIN_SESSIONS:
                continue
        bars[s] = b
    for s, mv in dropped_bad:
        print(f"  DROPPED {s}: largest single-day move {mv:,.0f}%")
    print(f"  loaded {len(bars)} of {len(syms)} "
          f"({len(syms) - len(bars) - len(dropped_bad)} too short, "
          f"{len(dropped_bad)} not a price series)")
    if not bars:
        return pd.DataFrame()

    # One shared calendar. Everything -- rules, controls, hold -- is indexed
    # to it, so no rung can win by trading on days another rung cannot see.
    dates = pd.DatetimeIndex(sorted(set().union(
        *(pd.DatetimeIndex(b["ts"]) for b in bars.values()))))
    print(f"  calendar {len(dates):,} sessions "
          f"({dates[0].date()} .. {dates[-1].date()})")

    rets, turn, pos_of = {}, {}, {}
    for s, b in bars.items():
        idx = pd.DatetimeIndex(b["ts"])
        pos_of[s] = idx.get_indexer(idx)       # placeholder, replaced below
        loc = dates.get_indexer(idx)
        pos_of[s] = loc
        r = np.full(len(dates), np.nan)
        c = b["close"].to_numpy(float)
        rr = np.empty(len(c)); rr[0] = np.nan; rr[1:] = c[1:] / c[:-1] - 1.0
        r[loc] = rr
        rets[s] = r
        tv = np.full(len(dates), np.nan)
        tv[loc] = c * b["volume"].to_numpy(float)
        turn[s] = tv

    # rung F: buy and hold, equal weight, daily rebalanced, same calendar
    wide = pd.DataFrame({s: rets[s] for s in bars}, index=dates)
    hold = wide.mean(axis=1, skipna=True).fillna(0.0)
    print(f"  hold: CAGR {cagr_of(hold):.2f}%/yr, max drawdown {maxdd_of(hold):.1f}%")

    first = dates[0]
    masks = {}
    if any(f in PANEL for f in fams):
        panel = close_panel(list(bars), "1d", first, dates[-1])
        masks = panel_masks(panel)

    rows = []
    for family in fams:
        for stop_name, stop_mult in STOPS.items():
            seed = abs(hash((tag, family, stop_name))) % (2**32)
            books = {}
            for mode in ("rule", "random", "nostop", "randomnostop"):
                rng = np.random.default_rng(seed)
                bk = {}
                for s, b in bars.items():
                    tl = trades_for(s, b, family, stop_mult, masks, rng, mode)
                    if tl:
                        loc = pos_of[s]
                        bk[s] = [(int(loc[a]), int(loc[e])) for a, e in tl]
                books[mode] = bk

            rungs = [
                ("A0 random, no cost, NO STOP",   "randomnostop", None, 0.0),
                ("A  random, no cost, unlimited", "random", None, 0.0),
                ("B  rule,   no cost, unlimited", "rule",   None, 0.0),
                ("C  rule,   + costs, unlimited", "rule",   None, COST_BPS),
            ] + [(f"D{i} rule,   + costs, {n:>2} slots", "rule", n, COST_BPS)
                 for i, n in enumerate(SLOTS)] + [
                # The SAME frictions applied to random timing. Without this
                # rung the position cap and the 1% fill cap are only ever
                # charged to the rule, so any lift they produce is credited to
                # the entry. They produce a large one: the fill cap is a
                # liquidity screen (it shrinks orders in thin names and leaves
                # the money in cash), and in NSE that screen is worth more than
                # every rule on the board. This rung is what separates the two.
                (f"E  random, + costs, {SLOTS[0]:>2} slots",
                 "random", SLOTS[0], COST_BPS),
                ("X  rule,   no cost, NO STOP",   "nostop", None, 0.0)]
            # printed order is deliberate: A0/A are the two controls, B/X the
            # two rule arms, then frictions. Do NOT let pandas sort these.

            for label, mode, slots, cost in rungs:
                r, taken, dropped, capped, nopen = account(
                    books[mode], dates, rets, turn, slots, cost, capital)
                rows.append({"universe": tag, "family": family, "stop": stop_name,
                             "rung": label, "trades": taken, "dropped": dropped,
                             "capped": capped, "cagr_pct": cagr_of(r),
                             "max_dd_pct": maxdd_of(r),
                             "mean_bps_day": float(r.mean() * 1e4),
                             "ann_vol_pct": float(r.std() * np.sqrt(SESSIONS) * 100),
                             "median_open": nopen[0], "pct_days_cash": nopen[1],
                             "crowding": nopen[2], "exposure_pct": nopen[3]})
            rows.append({"universe": tag, "family": family, "stop": stop_name,
                         "rung": "F  buy and hold", "trades": 0, "dropped": 0,
                         "capped": 0, "cagr_pct": cagr_of(hold),
                         "max_dd_pct": maxdd_of(hold),
                         "mean_bps_day": float(hold.mean() * 1e4),
                         "ann_vol_pct": float(hold.std() * np.sqrt(SESSIONS) * 100),
                         "median_open": len(bars), "pct_days_cash": 0.0,
                         "crowding": 0.0, "exposure_pct": 100.0})
        print(f"  [{(time.time()-t0)/60:5.1f} min] {family} done")
    return pd.DataFrame(rows)


def main() -> None:
    global CAP_FRAC
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", nargs="*", default=FAMILIES)
    ap.add_argument("--capital", type=float, default=1e7,
                    help="account size in rupees, for the fill cap only")
    ap.add_argument("--pilot", type=int, default=0, help="use only N symbols")
    ap.add_argument("--start", default=None,
                    help="drop every bar before this date (YYYY-MM-DD). The "
                         "India numbers are shaped by 2008; --start 2010-01-01 "
                         "asks whether anything here survives without it.")
    ap.add_argument("--capfrac", type=float, default=CAP_FRAC,
                    help="fraction of a bar's traded value one order may take. "
                         "Pass something huge (1e9) to switch the fill cap OFF "
                         "and fill every order in full -- that is the control "
                         "that says whether the cap, or merely holding cash, "
                         "is what the slot rungs are actually doing.")
    args = ap.parse_args()
    CAP_FRAC = args.capfrac
    t0 = time.time()

    us = us_universe()
    if not us:
        sys.exit("no US_*_day.parquet in CLEAN -- run scripts.us_fetch first")
    books = {"US": us, "NSE": nse_universe(len(us))}
    if args.pilot:
        books = {k: v[:args.pilot] for k, v in books.items()}

    start = pd.Timestamp(args.start) if args.start else None
    out = pd.concat([run_universe(t, s, args.families, args.capital, t0, start)
                     for t, s in books.items()], ignore_index=True)
    stamp = date.today().isoformat()
    if args.capfrac != 0.01:            # never clobber the shipped run
        stamp += f"_capfrac{args.capfrac:g}"
    if args.start:
        stamp += f"_from{args.start}"
    csv = OUT / "measurements" / f"waterfall_{stamp}.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(csv, index=False)
    print(f"\nwrote {csv}")
    print(f"  {out.shape[0]} rows x {out.shape[1]} columns")
    print(out.head(3).to_string(index=False))

    # THE DECOMPOSITION. Each step is computed WITHIN an arm and only then
    # summarised across arms. Taking the median of each rung and subtracting
    # those medians is a different (and wrong) quantity -- medians do not
    # difference, and doing it that way made two unrelated rungs print an
    # identical 14.37 in the pilot.
    piv = out.pivot_table(index=["universe", "family", "stop"],
                          columns="rung", values="cagr_pct")
    slots_hi, slots_lo = f"D0 rule,   + costs, {SLOTS[0]:>2} slots", \
        f"D1 rule,   + costs, {SLOTS[1]:>2} slots"
    steps = pd.DataFrame({
        "entry is worth (no stop on either side)":
            piv["X  rule,   no cost, NO STOP"] - piv["A0 random, no cost, NO STOP"],
        "entry is worth (board stop on both)":
            piv["B  rule,   no cost, unlimited"] - piv["A  random, no cost, unlimited"],
        "the board's stop costs the rule":
            piv["B  rule,   no cost, unlimited"] - piv["X  rule,   no cost, NO STOP"],
        "the board's stop costs random timing":
            piv["A  random, no cost, unlimited"] - piv["A0 random, no cost, NO STOP"],
        "brokerage and slippage cost":
            piv["C  rule,   + costs, unlimited"] - piv["B  rule,   no cost, unlimited"],
        f"capping at {SLOTS[0]} positions costs":
            piv[slots_hi] - piv["C  rule,   + costs, unlimited"],
        "the same cap+costs is worth this to RANDOM":
            piv[f"E  random, + costs, {SLOTS[0]:>2} slots"]
            - piv["A  random, no cost, unlimited"],
        "FINAL: realistic rule minus realistic RANDOM":
            piv[slots_hi] - piv[f"E  random, + costs, {SLOTS[0]:>2} slots"],
        f"tightening {SLOTS[0]} -> {SLOTS[1]} positions costs":
            piv[slots_lo] - piv[slots_hi],
        "FINAL: realistic rule minus buy and hold":
            piv[slots_hi] - piv["F  buy and hold"],
    }).reset_index()
    steps.to_csv(OUT / "measurements" / f"waterfall_steps_{stamp}.csv", index=False)

    for tag in books:
        s_ = steps[steps.universe == tag]
        print(f"\n=== {tag}: what each layer is worth, {len(s_)} arms, "
              f"points of CAGR per year (capital Rs{args.capital:,.0f}) ===")
        print(f"  {'layer':<46} {'median':>8} {'helps in':>10}")
        for col in steps.columns[3:]:
            v = s_[col].dropna()
            print(f"  {col:<46} {v.median():+8.2f} {f'{(v > 0).sum()} of {len(v)}':>10}")
        r = out[(out.universe == tag)]
        print(f"  reference: buy and hold "
              f"{r[r.rung.str.startswith('F')].cagr_pct.median():.2f}%/yr")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, tag in zip(axes, books):
        s = out[out.universe == tag]
        med = s.groupby("rung")["cagr_pct"].median().sort_index()
        ax.bar(range(len(med)), med.to_numpy(), color="steelblue")
        ax.set_xticks(range(len(med)))
        ax.set_xticklabels([r[:1] for r in med.index])
        ax.axhline(med.get("F buy and hold", np.nan), color="crimson", lw=1.5,
                   label="buy and hold")
        ax.set_title(f"{tag}: median account CAGR by rung")
        ax.grid(alpha=0.3, axis="y")
        ax.legend(fontsize=8)
    axes[0].set_ylabel("CAGR, %/yr")
    fig.tight_layout()
    png = OUT / "figures" / f"waterfall_{stamp}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}")
    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

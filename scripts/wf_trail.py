"""Does a TRAILING stop change the board's answer? Measured, out of band.

    python3 -m scripts.wf_trail --pilot 60     # 60 symbols, timed, for an estimate
    python3 -m scripts.wf_trail                # the full universe
    python3 -m scripts.wf_trail --width own    # the tight arm instead of atr3

WHY THIS EXISTS, 2026-09-17. Every stop on the twenty-row board is FIXED at
entry and never moves: entries.py sets `stop` once and checks `close[step] <=
stop` unchanged (entries.py:257-288), darvas.py the same, and backtest.py's
`current_stop` only moves when scale_out="half_be" is passed, which the
registry never does. kitelab/trailing.py implements the swing-low trail as
taught -- raise the stop to each newly CONFIRMED higher pivot low, never lower
it -- but only strategies.py and holygrail.py call it, and neither is on the
board (HG_VARIANTS is empty since 2026-09-11, strategies.py was never
registered). So the trail is written, tested, and measured on nothing the
dashboard shows. This script measures it.

WHAT IS VARIED, AND IT IS EXACTLY ONE THING. Same six entries, same universe,
same initial stop, same sizing -- sizing.position() reads the INITIAL stop, so
every arm buys the identical number of shares on the identical bar. The only
difference is whether that stop then moves up. Trade counts therefore cannot
drift the way they did in the first xrank placebo (kitelab-xrank-fails-the-gate:
2,776 real trades against 31,400 control, patient against frantic); here the
ENTRIES are identical by construction and only the exits differ.

    fixed    the board's convention. The stop never moves.
    atr      ratchet: stop = max(stop, close - k x ATR(14)), recomputed each
             bar. Same width as the atr3 arm, so "does ratcheting help?" is
             asked without also changing how wide the stop is.
    swing    the taught rule, via trailing.pivot_lows: raise to a 5-bar
             confirmed pivot low, and only to one that sits BELOW the current
             bar's low so it cannot fire on the bar that set it. A pivot is
             invisible until 5 bars after it forms; that lag is honoured.

Both trails are applied AFTER the bar's stop check, so no bar both sets and
triggers the same stop.

WHY atr3 IS THE DECISIVE LEG. Under `own` the stop ends 54.6% of trades on day
one and the median trade lasts 1-2 sessions -- a trail has no room to do
anything, and measuring it there would answer a question about the tight stop
rather than about trailing. Under atr3 the stop-only median is 77 sessions.
If a trail can matter anywhere on this board, it matters there. `own` is run
as a secondary arm to say whether trailing rescues the tight stop.

PRE-REGISTERED EXPECTATION, written before the first run, because PBO on this
project's own selection criterion is 0.412 and a result read against a
leaderboard is worth less than one read against a sentence:

    Trailing will make things WORSE, not better, and the mechanism is
    turnover. The board's stop already ends 60-93% of trades, and the exit
    work measured on 2026-09-17 cuts WINNERS rather than losers -- a trail is
    a machine for cutting winners earlier. Expect shorter holds, more round
    trips, more spread and charges paid, and a LOWER account CAGR in both
    trail arms than in the fixed arm, on the median family.

    If a trail arm WINS, that is the surprising result and it does not go on
    the board on this evidence. It would need the item-24 gate and a control.

THE THREE LEGS, added 2026-09-17 after the first two sweeps came back
"mildly negative" -- which is a description and not an answer.

    1. the sweep      trail minus fixed, on the median of 120 paired account
                      cells (5 universes x 2 risks x 2 capitals x 6 entries)
                      and the fraction of cells the trail wins. Run at both
                      stop widths.
    2. the controls   a trail exits sooner, so "it beat fixed" may be nothing
                      but exiting sooner. cap_* is a time stop at the trail's
                      own median hold; rnd_* draws each trade's holding period
                      from the trail arm's own realised hold distribution. Both
                      keep the initial stop and the identical entries. A trail
                      whose timing carries information must beat its own clock.
    3. the test       a Newey-West HAC t on the PAIRED DAILY excess of the two
                      accounts, the same machinery as the board's day-by-day
                      gate. Medians over 120 correlated cells cannot be read as
                      significance; this can.

WHAT IT FOUND, 2026-09-17 (both widths, 120 paired account cells each, costs
on, from 2018; logs in output/logs/wf_trail_legs_*.log).

**Trailing does not help.** Against the board's fixed stop the median cell
LOSES: atr3 width -0.60 pts/yr (atr) and -1.35 (swing), own width -0.95 and
-0.85, and on the day-by-day HAC test the median cell is -0.20/-0.50 and the
significant cells run the WRONG way -- 21 cells at t <= -1.96 against 1 at
t >= +1.96, where 6 of each were expected by chance. No entry family escapes:
the best median daily t any trail arm reaches is +0.57 (swing on `cal`), and
its best single cell is 1.26. The pre-registered expectation was right.

**But the trail's timing is not worthless, and that is the interesting part.**
Both trails beat their own hold-matched placebo -- a twin with the same initial
stop, the same entries and each trade's holding period DRAWN from the trail's
own realised hold distribution. Decomposed on the atr3 width:

    arm      net vs fixed   timing (vs rnd_*)   cost of exiting that early
    atr          -0.60           +0.90                    -1.25
    swing        -1.35           +2.35                    -3.80

So the trail buys about +1 to +2.4 CAGR points of genuine exit timing and pays
about -1.3 to -3.8 for the turnover that timing forces. The net is negative.
This is the same shape as kitelab-xrank-fails-the-gate: beats its own placebo,
still loses to the incumbent, and the two sentences must stay separate.

**Read the `own` control gaps with care.** There the median hold is 3 sessions
and the distribution has a long tail, so a time stop at the MEDIAN cuts the
MEAN hold from 13.6 sessions to 2.2 -- the control is crippled, not merely
matched, and the resulting +23.5/+28.1 pt gaps mostly measure what cutting
exposure by 5x does. The atr3 arms are matched within ~20% on mean hold
(atr 36.4 vs rnd_atr 29.9; swing 21.8 vs rnd_swing 18.3) and are the ones to
quote. Median-matching a skewed hold distribution is not matching.

WHAT IT DOES NOT TOUCH. Nothing under kitelab/ is edited and neither is
scripts/dashboard_data.py, so no signal cache and no grid checkpoint is
invalidated -- the same out-of-band discipline as scripts/wf_attach.py. The
ENTRY is not re-implemented either: entries.signal() is called directly, so
only the holding loop is replicated here, and SELF-CHECK proves that replica
reproduces entries.simulate() trade-for-trade with the trail switched off
before any number below is written.

Reads:  the cleaned parquet candles, via kitelab.frames
        /data/clean/kitelab/dashboard.json   (axes, hold CAGR, self-check)
Writes: output/measurements/wf_trail_<date>_<width>.csv        (per arm x cell)
        output/measurements/wf_trail_daily_<date>_<width>.csv  (per HAC test)
        output/wf_trail_<date>_<width>.json
        output/wf_trail_<date>_<width>.png
Cost:   run --pilot 60 first; it prints a measured estimate for the full run.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import (backtest, config, entries, frames, indicators, portfolio,
                     sizing, slippage, trailing)
from kitelab.backtest import charges
from kitelab.config import CLEAN
from scripts import dashboard_data as dd
from scripts.wf_daily import MIN_DAYS, hac_se, norm_sf, nw_lag

OUT = Path(__file__).resolve().parent.parent / "output"
ARMS = ("fixed", "atr", "swing")
TRAIL_K = 3.0          # the ratchet width, matching registry.STOPS["atr3"]

# The headline cell: the board's own defaults at the largest account, which is
# the cell wf_attach.py self-checks against. One cell, not 300 -- this asks
# whether trailing changes the answer, not which of 300 settings liked it best.
UNIVERSE = "all"
RISK = 1.0
CAPITAL = 10_000_000

# --cells widens the headline to the board's own account axes. One cell is one
# draw, and scripts/pbo.py measured what a single favourable draw is worth on
# this board: PBO 0.412 on our own selection criterion, worse than a coin flip.
# So the verdict is read off the MEDIAN of paired cells and the fraction of
# cells where the trail wins, never off the best one.
SWEEP_UNIS = ["all", "large", "mid", "small", "recent"]

# --- leg 2: the controls that make the comparison mean something ------------
# A trail's whole effect is that it exits sooner. "Trail beats fixed" therefore
# does not say the trail's TIMING has content -- a dumb clock that exits just as
# soon might do as well or better. Two controls per trail arm, both keeping the
# initial stop and the identical entries and share counts:
#
#   cap_*   a time stop at the trail's own MEDIAN hold. Deterministic, and it
#           cannot use price at all.
#   rnd_*   the placebo with teeth: each trade's holding period is DRAWN from
#           the trail arm's own realised hold distribution (seeded), so the
#           whole distribution is matched, not just its middle. This is the
#           shape the corrected xrank control had to take -- match the
#           behaviour, not the count (kitelab-xrank-fails-the-gate).
#
# A trail that cannot beat its own clock is a trail whose timing is worth
# nothing; the CAGR gap against `fixed` would then be turnover arithmetic.
CONTROLS = {"atr": ("cap_atr", "rnd_atr"), "swing": ("cap_swing", "rnd_swing")}
SEED = 20260917


def base_specs() -> list[dict]:
    """The three arms the self-check and the headline table use."""
    return [{"name": a, "kind": a, "maxhold": entries.MAXHOLD, "holds": None}
            for a in ARMS]


def replica(symbol: str, entry: str, stop_mult: float | None,
            day: pd.DataFrame | None = None,
            specs: list[dict] | None = None) -> dict[str, list[dict]]:
    """entries.simulate's holding loop, run several ways on one pass of the data.

    Returns {arm: trades}. The entry, the initial stop and the share count are
    computed ONCE and shared, so the arms differ only in their exits.

    A spec is {name, kind, maxhold, holds}: `kind` is "fixed", "atr" or "swing"
    (whether and how the stop ratchets), `maxhold` the session limit, and
    `holds` an array of holding periods to DRAW from per trade (the rnd_*
    controls) or None for the plain limit.
    """
    specs = base_specs() if specs is None else specs
    if day is None:
        day = frames.daily(symbol).reset_index(drop=True)
    if day.empty or len(day) < 2:
        return {a: [] for a in ARMS}
    fires = entries.signal(symbol, entry, day)
    open_, high, low, close = (day[c].to_numpy(float)
                               for c in ("open", "high", "low", "close"))
    atr = indicators.atr(day["high"], day["low"], day["close"],
                         entries.ATR_LEN).to_numpy(float)
    stamps = pd.DatetimeIndex(day["ts"]).tolist()
    total = len(day)
    pivots = trailing.pivot_lows(day)

    out: dict[str, list[dict]] = {sp["name"]: [] for sp in specs}
    # Each arm walks independently, because an arm that exits earlier is free
    # to take the NEXT signal earlier. Sharing one cursor would let the fixed
    # arm's exits dictate the trail arm's entries.
    for sp in specs:
        arm, kind, cap, holds = sp["name"], sp["kind"], sp["maxhold"], sp["holds"]
        # seeded per (symbol, arm) so the placebo is reproducible and no two
        # arms share a draw sequence
        rng = (np.random.default_rng([SEED, zlib.crc32(symbol.encode()),
                                      zlib.crc32(arm.encode())])
               if holds is not None and len(holds) else None)
        position = 0
        while position < total:
            fresh = fires[position] and position > 0 and not fires[position - 1]
            if not fresh:
                position += 1
                continue
            if stop_mult is None:
                stop0 = float(low[position])
            else:
                if not np.isfinite(atr[position]):
                    position += 1
                    continue
                stop0 = float(close[position]) - stop_mult * float(atr[position])
            if backtest.NEXT_OPEN_FILLS:
                if position + 1 >= total:
                    break
                entry_index = position + 1
                entry_price = float(open_[entry_index])
            else:
                entry_index = position
                entry_price = float(close[position])
            risk = entry_price - stop0
            if risk <= 0:
                position += 1
                continue

            stop = stop0
            limit = cap if rng is None else min(cap, max(1, int(rng.choice(holds))))
            cursor = bisect.bisect_left(pivots, 0)
            exit_at = None
            start = entry_index if backtest.NEXT_OPEN_FILLS else position + 1
            for step in range(start, total):
                if close[step] <= stop:
                    exit_at = (step, float(close[step]), "stop (close)")
                    break
                if step - entry_index >= limit:
                    exit_at = (step, float(close[step]),
                               f"{limit}-session limit")
                    break
                # --- the trail, applied only AFTER this bar's checks ---------
                if kind == "atr" and np.isfinite(atr[step]):
                    stop = max(stop, float(close[step]) - TRAIL_K * float(atr[step]))
                elif kind == "swing":
                    while (cursor < len(pivots)
                           and pivots[cursor] + trailing.PIVOT_SPAN <= step):
                        cand = float(low[pivots[cursor]])
                        if stop < cand < float(low[step]):
                            stop = cand
                        cursor += 1
            if exit_at is None:
                break

            exit_index, exit_price, reason = exit_at
            if backtest.NEXT_OPEN_FILLS:
                if exit_index + 1 >= total:
                    break
                exit_index += 1
                exit_price = float(open_[exit_index])
            # SIZED OFF THE INITIAL STOP in every arm -- that is what keeps the
            # share count identical and the comparison about exits alone.
            shares, risk_taken, capped = sizing.position(entry_price, stop0)
            if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
                position = exit_index + 1
                continue

            quoted_entry, quoted_exit = entry_price, exit_price
            fill_in = slippage.fill(symbol, stamps[entry_index], quoted_entry, +1)
            fill_out = slippage.fill(symbol, stamps[exit_index], quoted_exit, -1)
            buy_value, sell_value = fill_in * shares, fill_out * shares
            gross = (fill_out - fill_in) * shares
            same_session = stamps[entry_index].date() == stamps[exit_index].date()
            cost = charges(buy_value, sell_value, intraday=same_session)
            out[arm].append({
                "symbol": symbol,
                "entry_ts": stamps[entry_index], "exit_ts": stamps[exit_index],
                "same_session": same_session, "entry_time": "EOD",
                "level": None, "level_kind": entries.ENTRIES[entry],
                "max_risk_capital": sizing.risk_budget(),
                "risk_taken": risk_taken, "capital_capped": capped,
                "range": risk, "target": None, "final_stop": stop,
                "bars_held": exit_index - entry_index, "charges_best": cost,
                "net_profit_best": gross - cost,
                "entry_date": stamps[entry_index], "entry_price": fill_in,
                "quoted_entry": quoted_entry, "quoted_exit": quoted_exit,
                "spread_cost": ((quoted_exit - fill_out)
                                + (fill_in - quoted_entry)) * shares,
                "stop": stop0, "risk_per_share": risk,
                "exit_date": stamps[exit_index], "exit_price": fill_out,
                "exit_reason": reason,
                "days_held": (stamps[exit_index] - stamps[entry_index]).days,
                "sessions_held": exit_index - entry_index, "shares": shares,
                "cost_of_entry": buy_value, "gross_profit": gross,
                "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
                "charges": cost, "net_profit": gross - cost,
            })
            position = exit_index + 1
    return out


def self_check(symbols: list[str], width: float | None) -> None:
    """The `fixed` arm must BE entries.simulate, not merely resemble it."""
    print("\nSELF-CHECK: replica(fixed) against entries.simulate")
    checked = 0
    for entry in entries.ENTRIES:
        for symbol in symbols:
            want = entries.simulate(symbol, entry, stop_mult=width)
            got = replica(symbol, entry, width)["fixed"]
            if len(want) != len(got):
                raise SystemExit(
                    f"\n  SELF-CHECK FAILED on {entry}/{symbol}: "
                    f"{len(want)} trades in the engine, {len(got)} here. "
                    f"This harness is not the board's rule. Nothing written.\n")
            for a, b in zip(want, got):
                bad = [k for k in ("entry_ts", "exit_ts", "shares", "stop",
                                   "exit_price", "net_profit", "exit_reason")
                       if a[k] != b[k]]
                if bad:
                    raise SystemExit(
                        f"\n  SELF-CHECK FAILED on {entry}/{symbol}: {bad} "
                        f"disagree.\n  engine {[a[k] for k in bad]}\n  here   "
                        f"{[b[k] for k in bad]}\n  Nothing written.\n")
            checked += len(want)
    print(f"  PASSED: {checked:,} trades reproduced exactly across "
          f"{len(entries.ENTRIES)} entries x {len(symbols)} symbols")


def account(trades: list[dict], start: int, members=None,
            risk: float = RISK, capital: int = CAPITAL) -> tuple[dict, list]:
    """One board cell: the same portfolio.run the grid calls.

    Returns (payload, curve). The curve is the daily mark-to-market equity the
    account traced, and leg 3 tests arms against each other on it.
    """
    if members is not None:
        trades = [t for t in trades if t["symbol"] in members]
    ordered = sorted(trades, key=lambda t: t["entry_ts"])
    stamps = [pd.Timestamp(t["entry_ts"]) for t in ordered]
    window = ordered[bisect.bisect_left(stamps, pd.Timestamp(f"{start}-01-01")):]
    if not window:
        return ({"cagr": float("nan"), "final": 0, "maxdd": float("nan"),
                 "taken": 0, "sharpe": float("nan")}, [])
    r = portfolio.run(window, int(capital), risk / 100, dd.PRIORITY_DEFAULT)
    return dd.run_payload(r), r["curve"]


def daily_t(curve_a, curve_b) -> tuple[float | None, int]:
    """Newey-West HAC t of (arm A minus arm B) on their DAILY returns.

    Leg 3. A median over 120 correlated cells is a description; this is a test.
    The two accounts are marked to market on the same calendar, so the paired
    daily difference removes the market and what is left is the exit rule. Same
    machinery as the board's own day-by-day gate (scripts/wf_daily.py): lag
    max(4(n/100)^(2/9), 21) so overlapping positions and volatility clustering
    are not counted as independent evidence, and a cell with under a year of
    curve is not tested at all.
    """
    if not curve_a or not curve_b:
        return None, 0
    a = pd.Series({pd.Timestamp(d): float(v) for d, v in curve_a})
    b = pd.Series({pd.Timestamp(d): float(v) for d, v in curve_b})
    both = pd.concat([a, b], axis=1, join="inner").sort_index()
    if len(both) < MIN_DAYS + 1:
        return None, len(both)
    ra = both.iloc[:, 0].to_numpy(float)
    rb = both.iloc[:, 1].to_numpy(float)
    x = (ra[1:] / ra[:-1]) - (rb[1:] / rb[:-1])   # paired daily excess return
    x = x[np.isfinite(x)]
    if len(x) < MIN_DAYS:
        return None, len(x)
    se = hac_se(x, nw_lag(len(x)))
    if not se:
        return None, len(x)
    return float(x.mean() / se), len(x)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pilot", type=int, metavar="N",
                    help="use only the first N symbols and print an estimate")
    ap.add_argument("--width", choices=["atr3", "own"], default="atr3",
                    help="which stop width to trail from (default atr3)")
    ap.add_argument("--cells", action="store_true",
                    help="sweep the board's account axes (5 universes x 2 risks "
                         "x 2 capitals) instead of reporting one cell")
    args = ap.parse_args()
    width = None if args.width == "own" else 3.0

    dash = CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"No dashboard.json at {dash}. "
                         f"Build it: python3 -m scripts.refresh")
    payload = json.loads(dash.read_bytes())
    print(f"dashboard.json: built {payload['built']}, "
          f"{len(payload['grid']):,} grid cells")

    cfg = config.load()
    members = list(cfg.merged)
    print(f"universe: {len(members)} stocks; stop width {args.width}; "
          f"arms {', '.join(ARMS)}")

    # COSTS ON, exactly as the board builds them. Never measure an exit rule
    # gross: turnover is the mechanism under test and it is paid in the spread.
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = dd.REALISTIC_PARTICIPATION
    print(f"  COSTS ON: slippage.ENABLED={slippage.ENABLED}, "
          f"MAX_PARTICIPATION={slippage.MAX_PARTICIPATION}")

    self_check(members[:2], width)

    if args.pilot:
        members = members[:args.pilot]
        print(f"\nPILOT: {len(members)} of {len(cfg.merged)} symbols")

    unis = {"all": None}
    if args.cells:
        from scripts.wf_attach import stock_universes
        unis = stock_universes(list(cfg.merged))
        unis = {k: unis[k] for k in SWEEP_UNIS}
    cells = [(u, unis[u], r, c) for u in unis
             for r in (dd.RISKS if args.cells else [RISK])
             for c in (dd.CAPITALS if args.cells else [CAPITAL])]
    print(f"  account cells per (entry, arm): {len(cells)}")

    t0 = time.time()
    rows, tests = [], []

    def sweep(specs, tag):
        """One pass over every symbol, pooling trades for each spec."""
        pooled = {sp["name"]: [] for sp in specs}
        skipped = 0
        for symbol in members:
            try:
                got = replica(symbol, entry, width, specs=specs)
            except Exception as exc:             # a bad symbol, not a bad run
                skipped += 1
                if skipped <= 3:
                    print(f"    skipped {symbol}: {exc}")
                continue
            for name in pooled:
                pooled[name].extend(got[name])
        print(f"    {tag}: {len(members) - skipped} symbols, {skipped} skipped "
              f"({time.time() - e0:.0f}s)")
        return pooled

    for entry in entries.ENTRIES:
        e0 = time.time()
        print(f"\n{entry}:")
        pooled = sweep(base_specs(), "arms")

        # The controls are built FROM the trail arms just measured: same median
        # hold for cap_*, same hold distribution for rnd_*. They cannot be built
        # before this pass exists, which is why there are two.
        ctrl_specs = []
        for trail, (cap_name, rnd_name) in CONTROLS.items():
            holds = np.array([t["sessions_held"] for t in pooled[trail]], dtype=int)
            med = int(np.median(holds)) if len(holds) else entries.MAXHOLD
            ctrl_specs.append({"name": cap_name, "kind": "fixed",
                               "maxhold": max(1, med), "holds": None})
            ctrl_specs.append({"name": rnd_name, "kind": "fixed",
                               "maxhold": entries.MAXHOLD,
                               "holds": holds if len(holds) else None})
        pooled.update(sweep(ctrl_specs, "controls"))

        stats = {}
        for a, tr in pooled.items():
            held = [t["sessions_held"] for t in tr]
            stopped = sum(1 for t in tr if t["exit_reason"].startswith("stop"))
            stats[a] = {
                "entry": entry, "arm": a, "trades": len(tr),
                "median_held": float(np.median(held)) if held else float("nan"),
                "mean_held": round(float(np.mean(held)), 2) if held else float("nan"),
                "pct_stopped": round(100.0 * stopped / len(tr), 1) if tr else float("nan"),
                "mean_r": round(float(np.mean([t["r_multiple"] for t in tr])), 4) if tr else float("nan"),
                "spread_paid": round(sum(t["spread_cost"] for t in tr), 0),
                "charges": round(sum(t["charges"] for t in tr), 0),
            }
        print(f"    {'arm':<10}{'trades':>9}{'med hold':>10}{'mean hold':>11}"
              f"{'% stopped':>11}{'mean R':>9}")
        for a in pooled:
            st = stats[a]
            print(f"    {a:<10}{st['trades']:>9,}{st['median_held']:>10.0f}"
                  f"{st['mean_held']:>11.2f}{st['pct_stopped']:>11.1f}"
                  f"{st['mean_r']:>9.4f}")

        for ukey, mem, risk, capital in cells:
            curves = {}
            for a, tr in pooled.items():
                acct, curve = account(tr, dd.START_DEFAULT, mem, risk, capital)
                curves[a] = curve
                rows.append({**stats[a], "universe": ukey, "risk": risk,
                             "capital": capital, "cagr": acct["cagr"],
                             "maxdd": acct["maxdd"], "taken": acct["taken"],
                             "sharpe": acct["sharpe"]})
            for trail, (cap_name, rnd_name) in CONTROLS.items():
                for against in ("fixed", cap_name, rnd_name):
                    t, n = daily_t(curves[trail], curves[against])
                    tests.append({"entry": entry, "universe": ukey, "risk": risk,
                                  "capital": capital, "arm": trail,
                                  "against": against, "t": t, "days": n})
    took = time.time() - t0

    if args.pilot:
        share = len(members) / len(cfg.merged)
        print(f"\n  PILOT took {took/60:.1f} min for {len(members)} symbols.")
        print(f"  Estimated full run: {took/60/share:.0f} min (scaled by symbol "
              f"count; the universe's later symbols have shorter histories, so "
              f"treat this as an order of magnitude, not a promise).")
        return

    frame = pd.DataFrame(rows)
    print(f"\nresult frame: {len(frame)} rows x {len(frame.columns)} cols")
    print(frame.head(3).to_string(index=False))

    key = ["entry", "universe", "risk", "capital"]
    wide = frame.pivot_table(index=key, columns="arm", values="cagr")
    before = len(wide)
    wide = wide.dropna()
    print(f"\npaired cells: {before} built, {len(wide)} complete "
          f"({before - len(wide)} dropped for a missing arm)")

    print(f"\n{'='*70}\nTRAIL MINUS FIXED, CAGR points a year, from "
          f"{dd.START_DEFAULT}\n{'='*70}")
    print(f"  {'entry':<8}{'cells':>7}{'fixed':>9}{'atr-fix':>10}{'swing-fix':>11}"
          f"{'atr wins':>10}{'swing wins':>12}")
    for entry in entries.ENTRIES:
        sub = wide.xs(entry, level="entry")
        da, ds = sub["atr"] - sub["fixed"], sub["swing"] - sub["fixed"]
        print(f"  {entry:<8}{len(sub):>7}{sub['fixed'].median():>9.2f}"
              f"{da.median():>10.2f}{ds.median():>11.2f}"
              f"{f'{(da > 0).sum()}/{len(sub)}':>10}"
              f"{f'{(ds > 0).sum()}/{len(sub)}':>12}")
    summary = {}
    for a in ("atr", "swing"):
        for against in ("fixed",) + CONTROLS[a]:
            d = wide[a] - wide[against]
            summary[f"{a}-{against}"] = {
                "median": round(float(d.median()), 2),
                "wins": int((d > 0).sum()), "cells": int(len(d)),
                "p25": round(float(d.quantile(0.25)), 2),
                "p75": round(float(d.quantile(0.75)), 2)}
            print(f"\n  {a:<6} minus {against:<10} median {d.median():+.2f} pts/yr "
                  f"over {len(d)} paired cells, IQR [{d.quantile(0.25):+.2f}, "
                  f"{d.quantile(0.75):+.2f}], ahead in {int((d > 0).sum())} "
                  f"({100*(d > 0).mean():.0f}%)")

    # ---- leg 2: is the trail better than a clock that exits as soon? --------
    print(f"\n{'='*70}\nAGAINST THE HOLD-MATCHED CONTROLS, median CAGR pts/yr"
          f"\n{'='*70}")
    print(f"  {'entry':<8}{'atr-cap':>9}{'atr-rnd':>9}{'swing-cap':>11}"
          f"{'swing-rnd':>11}")
    for entry in entries.ENTRIES:
        sub_ = wide.xs(entry, level="entry")
        print(f"  {entry:<8}"
              f"{(sub_['atr'] - sub_['cap_atr']).median():>9.2f}"
              f"{(sub_['atr'] - sub_['rnd_atr']).median():>9.2f}"
              f"{(sub_['swing'] - sub_['cap_swing']).median():>11.2f}"
              f"{(sub_['swing'] - sub_['rnd_swing']).median():>11.2f}")
    print("\n  how well matched? median / mean sessions held, pooled over cells:")
    print(f"  {'entry':<8}" + "".join(f"{a:>11}" for a in wide.columns))
    for entry in entries.ENTRIES:
        f_ = frame[frame.entry == entry].drop_duplicates("arm").set_index("arm")
        print(f"  {entry:<8}" + "".join(
            f"{f_.loc[a, 'median_held']:>5.0f}/{f_.loc[a, 'mean_held']:<5.1f}"
            for a in wide.columns))

    # ---- leg 3: a test, not a description -----------------------------------
    tframe = pd.DataFrame(tests)
    tested = tframe.dropna(subset=["t"])
    print(f"\n{'='*70}\nDAY BY DAY (Newey-West HAC t of the paired daily "
          f"excess)\n{'='*70}")
    print(f"  {len(tested)} of {len(tframe)} comparisons had >= {MIN_DAYS} "
          f"paired sessions and a usable HAC se")
    print(f"  {'arm':<6}{'against':<11}{'cells':>7}{'median t':>10}{'max t':>8}"
          f"{'t>+1.96':>9}{'t<-1.96':>9}{'p<=0.05 by chance':>19}")
    daily = {}
    for a in ("atr", "swing"):
        for against in ("fixed",) + CONTROLS[a]:
            g = tested[(tested.arm == a) & (tested.against == against)]
            if g.empty:
                continue
            up = int((g.t > 1.96).sum())
            dn = int((g.t < -1.96).sum())
            daily[f"{a}-{against}"] = {
                "cells": int(len(g)), "median_t": round(float(g.t.median()), 2),
                "max_t": round(float(g.t.max()), 2), "up": up, "down": dn,
                "expected": round(0.05 * len(g), 1)}
            print(f"  {a:<6}{against:<11}{len(g):>7}{g.t.median():>10.2f}"
                  f"{g.t.max():>8.2f}{up:>9}{dn:>9}{0.05 * len(g):>19.1f}")
    print("\n  A two-sided p for the median cell, read off the normal tail "
          "(one cell, not a family):")
    for k, v in daily.items():
        print(f"    {k:<18} median t {v['median_t']:+.2f}  ->  p "
              f"{2 * norm_sf(abs(v['median_t'])):.3f}")

    hold = payload["validation_summary"].get("hold_cagr_by_scenario", {})
    print("\n  buy-and-hold for scale, same start year:")
    for u in sorted({r['universe'] for r in rows}):
        print(f"    {u:<8}{hold.get(f'{u}|{dd.START_DEFAULT}', 'n/a')}")

    stamp = f'{time.strftime("%Y-%m-%d")}_{args.width}'
    (OUT / "measurements").mkdir(parents=True, exist_ok=True)
    csv = OUT / "measurements" / f"wf_trail_{stamp}.csv"
    frame.to_csv(csv, index=False)
    tcsv = OUT / "measurements" / f"wf_trail_daily_{stamp}.csv"
    tframe.to_csv(tcsv, index=False)
    js = OUT / f"wf_trail_{stamp}.json"
    js.write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "built": payload["built"], "width": args.width, "trail_k": TRAIL_K,
        "cell": {"start": dd.START_DEFAULT, "priority": dd.PRIORITY_DEFAULT},
        "summary": summary, "daily": daily, "rows": rows, "tests": tests,
    }, default=str))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4.5))
        x = np.arange(len(entries.ENTRIES))
        shown = list(wide.columns)
        w = 0.8 / len(shown)
        for i, a in enumerate(shown):
            vals = [wide.xs(e, level="entry")[a].median() for e in entries.ENTRIES]
            ax.bar(x + (i - (len(shown) - 1) / 2) * w, vals, w, label=a)
        ax.set_xticks(x); ax.set_xticklabels(list(entries.ENTRIES))
        ax.axhline(0, color="black", lw=0.8)
        ax.set_ylabel("median account CAGR across cells, %/yr")
        ax.legend(ncol=4, fontsize=8)
        ax.set_title(f"Fixed vs trailing stop, {args.width} width, "
                     f"from {dd.START_DEFAULT} (costs on)")
        fig.tight_layout()
        png = OUT / f"wf_trail_{stamp}.png"
        fig.savefig(png, dpi=130)
        print(f"  wrote {png}")
    except ImportError:
        print("  no matplotlib; skipped the figure")

    print(f"  wrote {csv}\n  wrote {tcsv}\n  wrote {js}\n"
          f"  took {took/60:.1f} min\n")


if __name__ == "__main__":
    main()

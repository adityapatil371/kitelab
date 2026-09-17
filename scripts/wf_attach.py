"""Two board-wide diagnostics the dashboard was missing, computed per grid cell.

    python3 -m scripts.wf_attach              # both arms, the whole grid
    python3 -m scripts.wf_attach --pilot 1    # one strategy, timed, for an estimate
    python3 -m scripts.wf_attach --arm a      # daily-excess only
    python3 -m scripts.wf_attach --arm b      # next-open fills only

WHY THIS EXISTS. Two measurements were made in earlier sessions, written up,
and then left in `scripts/` where the page could not see them:

  A. THE DAILY EXCESS TEST (scripts.wf_daily, 2026-09-08). The walk-forward
     gate squashes each three-year window into one win/lose bit and judges a
     rule on SEVEN numbers. scripts.wf_power measured what that costs: at the
     board's own noise it passes 49.8% of rules with NO edge, while an honest
     5% test on the same seven numbers needs a 20 pts/yr edge for 80% power.
     Seven observations cannot support a test. This replaces them with the
     ~5,000 DAILY rule-minus-hold differences already implicit in the same
     account curve, tested with a Newey-West HAC standard error because
     consecutive days are correlated. It cut the detectable edge to ~12 pts/yr.

  B. THE FILL-TIMING ARM (scripts.wf_lookahead, 2026-09-09). The board fills at
     the CLOSE of the session that produced the signal, which no end-of-day
     trader can do -- the close is gone by the time it is known. Arm B refills
     every rule at the NEXT session's open. The effect is engine-dependent and
     opposite-signed across families, so the board-wide median (-0.29) hides
     it; `dv` (darvas.py) is flattered by ~3.3 pts/yr and supplies most of the
     rows currently closest to passing.

WHAT IT DOES NOT TOUCH, and why that matters. Nothing under kitelab/ is edited
and neither is scripts/dashboard_data.py. signals.stamp() digests the contents
of kitelab/*.py, and _grid_digest additionally digests
`signals._ACCOUNT` = portfolio, curves, validation, contracts AND
../scripts/dashboard_data.py. Editing any of those invalidates every grid
checkpoint and the validation summary, turning a one-hour job into a full
~150-minute rebuild. So both diagnostics are computed HERE, out of band, and
merged into the payload by scripts.attach_diagnostics.

THE SELF-CHECK. Arm A must reproduce a real board cell to the digit before any
number here is written. It loads the same signal caches, applies the same
spread, sets the same slippage globals and calls the same portfolio.run, so if
it disagrees with the built grid this harness is measuring something else and
says so instead of printing a number.

Reads:  /data/clean/kitelab/dashboard.json          (axes, stamp, self-check)
        /data/clean/kitelab/signal_cache/*_all.pkl  (arm A trades)
        the cleaned parquet candles, via kitelab.frames
Writes: output/wf_attach_<built date>.json          (the merged result)
        output/wf_attach_ckpt_<board key>/*.pkl     (one per strategy per arm)
        output/wf_attach_hold_<board key>.pkl       (daily hold curves)
        <board key> is board_key() -- the built DATE alone is not enough.
Cost:   run --pilot 1 first; it prints a measured estimate for the full grid.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

from kitelab import backtest, config, frames, portfolio, registry, slippage
from kitelab.config import CLEAN
from scripts import dashboard_data as dd
from scripts.wf_daily import (MIN_DAYS, buckets, closes_matrix, daily_returns,
                              hac_se, hold_curve, nw_lag, norm_sf)

OUT = Path(__file__).resolve().parent.parent / "output"
CACHE = CLEAN / "signal_cache"

# The board cell arm A must reproduce, read off the built grid rather than
# constructed here: the key format is dashboard_data's business.
#
# EVERY SLOT IS NOW READ FROM THE FILE THAT DEFINES IT, and this is the second
# time that lesson has been paid for at the end of a finished grid.
#   2026-09-11: the key ended "...|2018|liquidity"; PRIORITIES was cut to three
#     and that cell stopped being built, so this leg died after a 116-minute
#     grid. The priority was moved to dd.PRIORITY_DEFAULT.
#   2026-09-17: the STRATEGY slot died the same way. The board's variant axis
#     became the stop width, the EMA row was renamed "ema|0" -> "e1|own", and
#     this leg died after a 100-minute grid on "self-check cell ema|0|... is
#     not in the grid".
# A self-check must fail when the NUMBERS stop reproducing, never because it
# names an axis value the board retired. So the family is named here (it is a
# deliberate choice -- see below) and everything else is looked up: the variant
# from registry.variants, the risk and capital from dashboard_data's axis
# lists, the start year and priority from its defaults.
#
# The family is `e1` -- EMA, daily only -- because it is the one engine whose
# next-open branch is independently verified (ARM_B_VERIFIED below), and it is
# the same account this check has reproduced since it was written; only its
# label changed on 2026-09-17.
SELF_CHECK_FAMILY = "e1"
SELF_CHECK_KEY = (
    f"{SELF_CHECK_FAMILY}|{dd.tag(registry.variants(SELF_CHECK_FAMILY)[0])}"
    f"|all|{max(dd.RISKS):g}|{max(dd.CAPITALS)}|1"
    f"|{dd.START_DEFAULT}|{dd.PRIORITY_DEFAULT}")

# Engines with a next-open path. Keyed on Strategy.module so an engine that
# loses its path raises here rather than being silently measured as zero.
#
# `entries.py` was ADDED 2026-09-17, when the returns-blind board put six
# entry families (twelve of twenty rows) on that engine. It honours
# backtest.NEXT_OPEN_FILLS in three places (entries.py:265, 283, 297) and its
# module docstring says so, so leaving it out would have skipped arm B on the
# majority of the board -- silently, which is the exact failure this set was
# written to prevent. It is NOT in ARM_B_VERIFIED below, so its numbers reach
# the page marked provisional, which is the designed treatment.
NEXT_OPEN_ENGINES = {"backtest.py", "timeframes.py", "darvas.py",
                     "holygrail.py", "entries.py"}

# Arm B is only VERIFIED for backtest.py: scripts.wf_lookahead's self-check
# reproduced a board cell for ema|0 and nothing else. The other three engines
# got their next-open branch in a2040da and it has never been independently
# checked, so their arm-B numbers travel to the page marked provisional.
ARM_B_VERIFIED = {"backtest.py"}


def sid(strat) -> str:
    """The dashboard's id for a strategy: "skey|tag(variant)"."""
    return f"{strat.key}|{dd.tag(strat.variant)}"


def grid_key(strat, ukey, risk, capital, year, prio) -> str:
    """Exactly scripts.dashboard_data's cell key, so the page can join on it."""
    return (f"{strat.key}|{dd.tag(strat.variant)}|{ukey}|{risk:g}"
            f"|{capital}|{dd.GRID_FILLS[0]}|{year}|{prio}")


# ----------------------------------------------------------- universes ----
def stock_universes(members):
    """{ukey: members or None}, and the counts asserted against the payload."""
    small, mid, large, recent = buckets(members)
    total = len(small) + len(mid) + len(large) + len(recent)
    if total != len(members):
        raise SystemExit(f"liquidity buckets must partition the universe "
                         f"({total} != {len(members)})")
    print(f"  buckets: large {len(large)}, mid {len(mid)}, small {len(small)}, "
          f"recent {len(recent)}")
    return {"all": None, "large": large, "mid": mid, "small": small, "recent": recent}


def assert_buckets_match_payload(unis, payload):
    """The page prints "631 small caps". If our buckets disagree with the built
    payload the trade subsets are not the ones the grid was made from, and every
    number below would be attached to the wrong cell."""
    labels = payload["universes"]
    for ukey, mem in unis.items():
        if mem is None:
            continue
        n = len(mem)
        if str(n) not in labels.get(ukey, ""):
            raise SystemExit(
                f"bucket {ukey} has {n} members but the payload says "
                f"'{labels.get(ukey)}'. The universe moved since the build -- "
                f"rebuild the dashboard before attaching diagnostics.")
    print("  buckets agree with the built payload")


def single_name_hold(symbol):
    """Daily buy-and-hold wealth for one instrument, starting at 1.0."""
    d = frames.daily(symbol)
    s = pd.Series(d["close"].to_numpy(float),
                  index=pd.DatetimeIndex(pd.to_datetime(d["ts"])).normalize())
    s = s[~s.index.duplicated(keep="last")]
    return s / s.iloc[0]


def board_key(payload):
    """Which board a checkpoint was computed against.

    Checkpoints were keyed on the built DATE alone until 2026-09-11, when two
    different boards were built on one day -- 19 strategies at 14:45 IST, then
    13 at 17:50 after the cut -- and the second run reused all 26 partitions
    the first had written, finishing a ~50-minute leg in 0.4 minutes. Nothing
    checked that the reuse was sound: `if path.exists()` was the whole test.
    The numbers did survive it (verified by recomputing two strategies from
    scratch: 1,200 cells, byte-identical pickles), because a strategy's cells
    depend only on its own trades and the cut changed no surviving rule -- but
    that was luck, not a guarantee the code offered.

    Key on the FULL built timestamp instead. attach_diagnostics already treats
    that string as the identity of a board when it refuses a stale handoff
    (see its `blob["built"] != payload["built"]` check), so both layers now
    answer "which board is this?" the same way.

    Hold curves do not strictly depend on the strategy set, so keying them
    this way rebuilds them once per board even when prices have not moved.
    That is the safe direction to be wrong in, and it is the cheap part of
    this leg -- the two arms are the hour.

    THE DIAGNOSTIC CODE IS IN THE KEY TOO (2026-09-11, second pass). The board
    identity alone still cannot see that THIS script's arithmetic moved. Fixing
    the `recent` hold curve in wf_daily.hold_curve the same afternoon left a
    stale `wf_attach_hold_*.pkl` on disk that the board key had no way to
    reject -- the same bug one layer down. Both files are hashed whole rather
    than per-function: naming the exact functions whose output lands in a
    checkpoint means keeping that list right forever, and a list that silently
    goes stale is what this key exists to prevent.

    THE COST: a comment in either file invalidates ~75 minutes of diagnostics.
    That is the same trade scripts/dashboard_data.py makes by sitting in
    signals._ACCOUNT, made deliberately and in the safe direction. If a change
    is genuinely inert, prove it the way the 2026-09-11 reuse was proved --
    recompute two strategies and diff the pickles -- and carry the rest over by
    hand. Do not widen the key to make an edit cheap.
    """
    built = payload["built"]
    code = hashlib.sha1()
    here = Path(__file__).resolve().parent
    for name in ("wf_attach.py", "wf_daily.py"):
        code.update((here / name).read_bytes())
    return (f"{built.split()[0]}_{hashlib.sha1(built.encode()).hexdigest()[:6]}"
            f"_{code.hexdigest()[:6]}")


def hold_curves(members, unis, assets, board):
    """One daily equal-weight hold curve per universe, checkpointed.

    The stock curves are scripts.wf_daily's, which are asserted against
    validation.buy_and_hold there. The single-name ones are the instrument.
    """
    ckpt = OUT / f"wf_attach_hold_{board}.pkl"
    if ckpt.exists():
        print(f"  hold curves: reusing {ckpt.name}")
        return pickle.loads(ckpt.read_bytes())
    print("  building daily hold curves")
    closes = closes_matrix(members)
    out = {}
    for ukey, mem in unis.items():
        mem = set(closes.columns) if mem is None else mem
        c = hold_curve(closes, mem)
        if c is None:
            raise SystemExit(f"no hold curve for {ukey}")
        out[ukey] = c
    for sym in assets:
        out[sym] = single_name_hold(sym)
    for ukey, c in out.items():
        yrs = (c.index[-1] - c.index[0]).days / 365.25
        print(f"    {ukey:<8}{len(c):>6} days  {c.index[0].date()} to "
              f"{c.index[-1].date()}  CAGR {100*((c.iloc[-1]/c.iloc[0])**(1/yrs)-1):>7.2f}%")
    OUT.mkdir(exist_ok=True)
    ckpt.write_bytes(pickle.dumps(out))
    return out


# --------------------------------------------------------------- trades ----
def spread_of(trades):
    """The board's own execution: costs on, one order <= 1% of daily turnover.

    dashboard_data applies the half-spread to the CACHED trade list and leaves
    the cap to portfolio.run, because nothing in the simulation depends on the
    fill price. Same two lines here, same order.
    """
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = dd.REALISTIC_PARTICIPATION
    slippage.reset()
    return [slippage.apply_spread(t) for t in trades]


def arm_a_trades(strat):
    """The board's own trades: the signal cache it was built from."""
    p = CACHE / f"{strat.cache}_all.pkl"
    if not p.exists():
        return None
    blob = pickle.loads(p.read_bytes())
    return blob["trades"] if isinstance(blob, dict) else blob


def arm_b_trades(strat, universe):
    """The same rule refilled at the next session's open. Built from bars.

    NEXT_OPEN_FILLS is a run-time global, set the way dashboard_data.execution()
    sets the slippage globals. Nothing under kitelab/ is written, so no signal
    cache is invalidated. Built UNSPREAD, because the board builds its caches
    before execution() runs; the spread is charged afterwards by spread_of.
    """
    backtest.NEXT_OPEN_FILLS = True
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    trades = []
    try:
        for symbol in universe:
            try:
                trades.extend(strat.build(symbol))
            except SystemExit:
                pass
    finally:
        backtest.NEXT_OPEN_FILLS = False      # leave the module as we found it
    return trades


# ---------------------------------------------------------------- stats ----
def excess_stats(curve, hold):
    """Daily rule-minus-hold excess for one cell, HAC-tested against zero.

    H0: mean daily excess = 0, one-sided H1 > 0. An iid standard error would be
    far too small here -- daily excess returns are serially correlated through
    overlapping positions and a shared market factor -- and would manufacture
    significance, the same mistake validation.t_iid was corrected for on the
    trade side. Lag is Newey-West's max(4*(n/100)^(2/9), 21), floored at one
    trading month so the interval errs wide.
    """
    if len(curve) < MIN_DAYS:
        return None
    idx = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d, _ in curve])
    eq = pd.Series([float(v) for _, v in curve], index=idx)
    eq = eq[~eq.index.duplicated(keep="last")]
    shared = eq.index.intersection(hold.index)
    if len(shared) < MIN_DAYS:
        return None
    ex = daily_returns(eq.loc[shared].to_numpy()) - daily_returns(hold.loc[shared].to_numpy())
    lag = nw_lag(len(ex))
    se = hac_se(ex, lag)
    if not se:
        return None
    yrs = (shared[-1] - shared[0]).days / 365.25
    g_r = eq.loc[shared].iloc[-1] / eq.loc[shared].iloc[0]
    g_h = hold.loc[shared].iloc[-1] / hold.loc[shared].iloc[0]
    rule = 100 * (g_r ** (1 / yrs) - 1) if g_r > 0 else None
    held = 100 * (g_h ** (1 / yrs) - 1)
    t = ex.mean() / se
    return {"days": int(len(ex)), "nw_lag": int(lag),
            "t_hac": round(float(t), 3),
            "p": round(float(norm_sf(t)), 6),
            "excess_pts": None if rule is None else round(rule - held, 2),
            # smallest true edge an 80%-power one-sided 5% test catches here
            "mde_80": round(float(2.486 * se * 252 * 100), 2)}


# ----------------------------------------------------------- the grid ----
def cells_for(strat, trades, unis, assets_trades, holds, arm, hold_cagr):
    """Every grid cell for one strategy, in scripts.dashboard_data's own order.

    The loop is that file's: sort once by entry_ts, bisect each start year off
    the sorted list, and collapse the priority axis to one entry on a
    single-instrument universe, where all five orderings are byte-identical
    because the account never has two candidates at once.
    """
    daily, timing = {}, {}
    pool = dict(unis)
    for sym in assets_trades:
        pool[sym] = {sym}
    for ukey, members in pool.items():
        src = assets_trades.get(ukey, trades)
        subset = (src if members is None
                  else [t for t in src if t["symbol"] in members])
        if not subset:
            continue
        subset = sorted(subset, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        hold = holds.get(ukey)
        single = members is not None and len(members) == 1
        # Same duplicate-start-year collapse the grid applies, from the same
        # function, so the two loops cannot drift apart: a cell the grid wrote
        # as None must not come back carrying a daily-excess test.
        live_years = dd.gridded_years(stamps)
        for year in dd.START_YEARS:
            cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
            window = subset[cut:] if year in live_years else []
            if not window:
                continue
            for prio in ([dd.PRIORITY_DEFAULT] if single else dd.PRIORITIES):
                for risk in dd.RISKS:
                    for capital in dd.CAPITALS:
                        r = portfolio.run(window, capital, risk / 100, prio)
                        key = grid_key(strat, ukey, risk, capital, year, prio)
                        if arm == "a":
                            st = excess_stats(r.get("curve") or [], hold)
                            if st:
                                daily[key] = st
                        else:
                            pay = dd.run_payload(r)
                            held = hold_cagr.get(f"{ukey}|{year}")
                            timing[key] = {
                                "cagr": pay["cagr"], "taken": pay["taken"],
                                "wiped": bool(pay["wiped"]),
                                "beats_hold": (None if (held is None or pay["wiped"]
                                                        or pay["cagr"] is None)
                                               else bool(pay["cagr"] > held))}
    return daily, timing


def run_arm(arm, strategies, unis, members, assets, holds, hold_cagr, board):
    """One fill convention over every strategy, checkpointed per strategy."""
    ck = OUT / f"wf_attach_ckpt_{board}"
    ck.mkdir(parents=True, exist_ok=True)
    daily, timing = {}, {}
    t0 = time.time()
    for i, strat in enumerate(strategies, 1):
        path = ck / f"{arm}__{strat.cache}.pkl"
        if path.exists():
            d, t = pickle.loads(path.read_bytes())
            daily.update(d), timing.update(t)
            print(f"  [{i:2}/{len(strategies)}] {sid(strat):<16} reused "
                  f"{len(d) + len(t):>4} cells", flush=True)
            continue
        if arm == "b" and strat.module not in NEXT_OPEN_ENGINES:
            print(f"  [{i:2}/{len(strategies)}] {sid(strat):<16} skipped -- "
                  f"{strat.module} has no next-open path", flush=True)
            continue
        s0 = time.time()
        if arm == "a":
            raw = arm_a_trades(strat)
            if raw is None:
                print(f"  [{i:2}/{len(strategies)}] {sid(strat):<16} skipped -- "
                      f"no signal cache on disk", flush=True)
                continue
            assets_raw = {sym: build_one(strat, sym, next_open=False) for sym in assets}
        else:
            raw = arm_b_trades(strat, members)
            assets_raw = {sym: build_one(strat, sym, next_open=True) for sym in assets}
        trades = spread_of(raw)
        assets_trades = {sym: spread_of(v) for sym, v in assets_raw.items() if v}
        d, t = cells_for(strat, trades, unis, assets_trades, holds, arm, hold_cagr)
        path.write_bytes(pickle.dumps((d, t)))
        daily.update(d), timing.update(t)
        print(f"  [{i:2}/{len(strategies)}] {sid(strat):<16} {len(raw):>7,} trades "
              f"-> {len(d) + len(t):>4} cells  {time.time()-s0:>6.0f}s", flush=True)
    print(f"  arm {arm.upper()}: {len(daily) + len(timing)} cells in "
          f"{(time.time()-t0)/60:.1f} min")
    return daily, timing


def build_one(strat, symbol, next_open):
    """One instrument's trades under one fill convention."""
    backtest.NEXT_OPEN_FILLS = next_open
    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()
    try:
        return strat.build(symbol)
    except SystemExit:
        return []
    finally:
        backtest.NEXT_OPEN_FILLS = False


def self_check(payload, strategies, unis, holds):
    """Arm A must reproduce a built board cell to the digit, or nothing here
    describes the grid it is about to be merged into."""
    want = payload["grid"].get(SELF_CHECK_KEY)
    if want is None:
        raise SystemExit(f"self-check cell {SELF_CHECK_KEY} is not in the grid")
    skey, band, ukey, risk, capital, _f, year, prio = SELF_CHECK_KEY.split("|")
    strat = next((s for s in strategies if sid(s) == f"{skey}|{band}"), None)
    if strat is None:
        raise SystemExit(f"self-check strategy {skey}|{band} is not in the registry")
    raw = arm_a_trades(strat)
    if raw is None:
        raise SystemExit(f"self-check needs {strat.cache}_all.pkl and it is missing")
    trades = spread_of(raw)
    members = unis[ukey]
    subset = [t for t in trades if members is None or t["symbol"] in members]
    subset = sorted(subset, key=lambda t: t["entry_ts"])
    stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
    window = subset[bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01")):]
    got = dd.run_payload(portfolio.run(window, int(capital), float(risk) / 100, prio))
    bad = [f for f in ("cagr", "final", "maxdd", "taken", "sharpe")
           if got[f] != want[f]]
    if bad:
        raise SystemExit(
            f"\n  SELF-CHECK FAILED on {SELF_CHECK_KEY}: {bad} disagree with the "
            f"built grid.\n  board {[want[f] for f in bad]} vs here "
            f"{[got[f] for f in bad]}\n  This harness is not measuring the board's "
            f"account. Nothing was written.\n")
    print(f"  SELF-CHECK PASSED: {SELF_CHECK_KEY} reproduces the board "
          f"(cagr {got['cagr']}, final {got['final']:,}, taken {got['taken']})")


# ----------------------------------------------------------------- main ----
def main():
    # LINE-BUFFERED, ALWAYS. Python block-buffers stdout when it is a pipe, so
    # `python3 -m scripts.wf_attach | tee run.log` shows nothing at all for the
    # first 8 KB -- which on an hour-long job reads exactly like a hang. Fixed
    # here rather than by remembering `-u` at the call site, because
    # scripts.refresh invokes this as a subprocess and would have to remember
    # it too.
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", choices=["a", "b", "both"], default="both",
                    help="a = daily excess, b = next-open fills (default both)")
    ap.add_argument("--pilot", type=int, metavar="N",
                    help="run only the first N strategies and print a measured "
                         "estimate for the full grid, then stop")
    args = ap.parse_args()

    dash = CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"No dashboard.json at {dash}. "
                         f"Build it: python3 -m scripts.refresh")
    payload = json.loads(dash.read_bytes())
    stamp = payload["built"].split()[0]   # output JSON name: attach_diagnostics resolves it
    board = board_key(payload)            # checkpoint name: see board_key()
    print(f"dashboard.json: built {payload['built']}, "
          f"{len(payload['grid']):,} grid cells")

    cfg = config.load()
    members = list(cfg.merged)
    assets = list(payload.get("single_name") or [])
    print(f"universe: {len(members)} stocks, {len(assets)} single-name "
          f"({', '.join(assets)})")

    unis = stock_universes(members)
    assert_buckets_match_payload(unis, payload)
    holds = hold_curves(members, unis, assets, board)
    hold_cagr = payload["validation_summary"]["hold_cagr_by_scenario"]

    strategies = registry._build_registry()
    self_check(payload, strategies, unis, holds)
    if args.pilot:
        strategies = strategies[:args.pilot]
        print(f"\nPILOT: {args.pilot} of {len(registry._build_registry())} "
              f"strategies")

    t0 = time.time()
    daily, timing = {}, {}
    for arm in (["a", "b"] if args.arm == "both" else [args.arm]):
        print(f"\n{'='*70}\nARM {arm.upper()} -- "
              f"{'daily excess vs hold' if arm == 'a' else 'next-open fills'}\n{'='*70}")
        d, t = run_arm(arm, strategies, unis, members, assets, holds, hold_cagr, board)
        daily.update(d), timing.update(t)
    took = time.time() - t0

    if args.pilot:
        share = args.pilot / len(registry._build_registry())
        print(f"\n  PILOT took {took/60:.1f} min for {args.pilot} strategies.")
        print(f"  Estimated full run: {took/60/share:.0f} min "
              f"(scaled by strategy count; trade lists differ in size, so treat "
              f"this as an order of magnitude, not a promise).")
        print("  Checkpoints are kept -- the full run will reuse this work.\n")
        return

    out = {
        "built": payload["built"],
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "daily_excess": daily,
        "fill_timing": timing,
        "arm_b_provisional": sorted(
            sid(s) for s in registry._build_registry()
            if s.module in NEXT_OPEN_ENGINES and s.module not in ARM_B_VERIFIED),
        # Engine NAMES here, rule ids above: the page says "verified for
        # backtest.py" (a property of the engine) but flags provisional rows
        # one at a time (a property of the row).
        "arm_b_verified": sorted(ARM_B_VERIFIED),
    }
    OUT.mkdir(exist_ok=True)
    path = OUT / f"wf_attach_{stamp}.json"
    path.write_text(json.dumps(out))
    print(f"\n  daily_excess {len(daily):,} cells, fill_timing {len(timing):,} cells")
    print(f"  wrote {path} in {took/60:.1f} min")
    print("\n  Merge it into the payload:  python3 -m scripts.attach_diagnostics\n")


if __name__ == "__main__":
    main()

"""The all-time-high band rule, backtested on its own -- NOT on the dashboard.

    python3 -m scripts.ath_band                 # 999 stocks, three bands, ~minutes
    python3 -m scripts.ath_band -n 60           # a quick look over 60 stocks
    python3 -m scripts.ath_band --start 2012

WHY THIS IS A SCRIPT AND NOT A REGISTRY ENTRY (owner's decision, 2026-09-07).
Every rule added to kitelab.registry raises the luck hurdle for every rule
already there, multiplies the grid, and invalidates every cache. This one is
being DESIGNED, not ranked: it is the owner's own earlier ATH rule, rebuilt
deliberately after the 2026-09-07 audit found an accidental version of it
inside the `eath` family (a pullback re-entry that fired with no EMA cross and
earned most of that family's profit on the M/W and Q/M stacks). It stays here,
measured through the SAME account engine, the same costs and the same honest
gates as the board, until it has earned a row -- and it writes only to output/.

THE RULE, as the owner described it ("ath 10% band buy, sell when candles fall
below 20 ema or previous swing, we use trailing stoploss, low timeframe is
daily"), made precise:

    bars     daily; decisions at the close, fills at the close (board convention)
    entry    the close is within BAND of the highest close so far (today
             included) and yesterday's close was not -- the rising edge. So a
             fresh all-time high always qualifies, and a stock already sitting
             in the band does not re-signal until it has left it and returned.
    stop     the entry candle's own low (the project's uniform convention)
    trail    each newly CONFIRMED 5-bar swing low that sits above the current
             stop and below today's low becomes the stop; never lowered
             (kitelab.trailing, unchanged)
    exit     whichever comes first: a close below the daily 20-EMA, or the
             stop. The stop is checked first (same-bar ambiguity resolves to
             the stop); a gap through it fills at the open.
    one position per stock; no re-entry until the band condition has gone
             false and come true again.

Two things the description left open, resolved the STRICTER way and flagged
here so the owner can overrule them:
    * no higher-timeframe trend filter ("low timeframe is daily" may imply a
      weekly/monthly EMA condition above it; none is applied)
    * "candles fall below 20 EMA" is read as ONE daily close below the EMA,
      not the whole candle -- the earlier exit of the two.

CONTROLS. Three bands (5%, 10%, 15%), which is the parameter-plateau check the
board lost with the EMA band; a random-entry null with the same exits (built
into kitelab.validation.bootstrap_one as drift_r); and the equal-weight hold of
the same stocks from the same start year. The honest reading is the drift-
adjusted, cluster-robust t against a family-wise bar -- and that bar must
count these three bands on top of the board's variants, which the summary
does approximately (n_eff + 3).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import time
from statistics import NormalDist

import numpy as np
import pandas as pd

from kitelab import (backtest, config, frames, indicators, portfolio, sizing,
                     slippage, strategies, trailing, validation)

BANDS = [0.05, 0.10, 0.15]
EMA_LENGTH = 20
PIVOT_SPAN = trailing.PIVOT_SPAN
CAPITALS = [200_000, 10_000_000]
RISK = 0.01
PRIORITY = "liquidity"
PARTICIPATION = 0.01            # the board's "Realistic fills" size cap
OUT_DIR = pathlib.Path("output")


# ------------------------------------------------------------- the rule ----
def build(symbol: str, band: float) -> list[dict]:
    """Closed trades for one stock, oldest first. Fills at the close, no
    spread (applied afterwards exactly as the board does, slippage.apply_spread)."""
    try:
        daily = frames.daily(symbol).reset_index(drop=True)
    except SystemExit:
        return []
    if len(daily) < 250:
        return []
    close = daily["close"].to_numpy(float)
    low = daily["low"].to_numpy(float)
    peak = np.maximum.accumulate(close)
    in_band = close >= peak * (1.0 - band)
    fresh = in_band.copy()
    fresh[1:] &= ~in_band[:-1]
    fresh[0] = False
    ema = indicators.ema(daily["close"], EMA_LENGTH).to_numpy(float)
    below_ema = close < ema
    pivots = trailing.pivot_lows(daily, PIVOT_SPAN)
    make_step = trailing.daily_trail(daily, pivots, daily["ts"], PIVOT_SPAN)

    trades: list[dict] = []
    position = 0
    total = len(daily)
    while position < total - 1:
        if not fresh[position]:
            position += 1
            continue
        entry_price, stop = float(close[position]), float(low[position])
        risk = entry_price - stop
        if risk <= 0:
            position += 1
            continue
        shares, risk_taken, _capped = sizing.position(entry_price, stop)
        if shares < 1:
            position += 1
            continue
        # The walk starts on the bar AFTER entry: with a close fill and the
        # entry bar's own low as the stop, checking the entry bar would stop
        # every trade out on the day it opened.
        exit_pos, exit_price, reason, _final_stop, _bf, _bp = trailing.resolve(
            daily, position + 1, stop, make_step(), entry_price, None,
            below_ema, f"below {EMA_LENGTH} EMA")
        if reason == trailing.OPEN_MARKER:
            break                                   # still open when data ends
        entry_ts, exit_ts = daily.iloc[position]["ts"], daily.iloc[exit_pos]["ts"]
        gross = (exit_price - entry_price) * shares
        same_session = entry_ts.date() == exit_ts.date()
        cost = backtest.charges(entry_price * shares, exit_price * shares,
                                intraday=same_session)
        trades.append({
            "symbol": symbol, "band": band,
            "entry_ts": entry_ts, "entry_date": entry_ts.date(),
            "exit_ts": exit_ts, "exit_date": exit_ts.date(),
            "entry_price": entry_price, "exit_price": exit_price,
            "quoted_entry": entry_price, "quoted_exit": exit_price,
            "stop": stop, "risk_per_share": risk, "risk_taken": risk_taken,
            "shares": shares, "bars_held": exit_pos - position,
            "gross_profit": gross, "charges": cost, "net_profit": gross - cost,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "exit_reason": reason, "same_session": same_session,
        })
        position = exit_pos + 1
    return trades


# ------------------------------------------------------------ the checks ----
def _execution(on: bool) -> None:
    slippage.ENABLED = on
    slippage.MAX_PARTICIPATION = PARTICIPATION if on else None
    slippage.reset()


def _account(trades, capital, start_year):
    cut = pd.Timestamp(f"{start_year}-01-01")
    window = [t for t in trades if pd.Timestamp(t["entry_ts"]) >= cut]
    if not window:
        return None
    return portfolio.run(window, capital, RISK, priority=PRIORITY)


def _fmt(v, places=1, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, float) and not np.isfinite(v):
        return "inf"
    return f"{v:.{places}f}{suffix}"


def summarise(band: float, trades: list[dict], members: list[str], start_year: int,
              hold_by_start: dict, hold_windows: dict) -> dict:
    """One band's row: trade-level checks on every signal, account-level checks
    on what the board's default account would have taken."""
    held = strategies.drop_overlaps(trades)
    cred = validation.bootstrap_one(held) if len(held) >= validation.MIN_TRADES else None
    breakeven = validation.breakeven_cost(held) if held else None
    row = {"band": band, "signals": len(trades),
           "mean_r": None if cred is None else cred["mean_r"],
           "drift_r": None if cred is None else cred.get("drift_r"),
           "t_iid": None if cred is None else cred.get("t_iid"),
           "t_stat": None if cred is None else cred.get("t_stat"),
           "n_clusters": None if cred is None else cred.get("n_clusters"),
           "breakeven_bp": breakeven,
           "median_hold_bars": (int(np.median([t["bars_held"] for t in held]))
                                if held else None),
           "win_rate": (round(float(np.mean([t["net_profit"] > 0 for t in held])), 3)
                        if held else None)}
    wf = validation.walk_forward_grid(trades, {"all": None}, [PRIORITY], [CAPITALS[0]],
                                      {"all": hold_windows})
    wf = wf.get(validation.scenario_key("all", PRIORITY, CAPITALS[0]), {})
    row["wf_wins"] = wf.get("wins")
    row["wf_total"] = wf.get("total_windows")
    for capital in CAPITALS:
        r = _account(trades, capital, start_year)
        tag = "2L" if capital == 200_000 else "1cr"
        if r is None:
            row.update({f"cagr_{tag}": None, f"maxdd_{tag}": None, f"mar_{tag}": None,
                        f"taken_{tag}": 0, f"t_taken_{tag}": None})
            continue
        tt = validation.clustered_t(r["taken"]) if r["taken"] else None
        row.update({f"cagr_{tag}": r["cagr_pct"], f"maxdd_{tag}": r["max_drawdown_pct"],
                    f"mar_{tag}": r.get("mar"), f"taken_{tag}": len(r["taken"]),
                    f"skipped_cash_{tag}": r["skipped_cash"],
                    f"t_taken_{tag}": None if tt is None else tt["t_stat"]})
    row["hold_cagr"] = hold_by_start.get(start_year)
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--symbols", type=int, default=None,
                    help="only the first N symbols of the universe (quick look)")
    ap.add_argument("--start", type=int, default=2018, help="account start year")
    ap.add_argument("--bands", type=float, nargs="+", default=BANDS)
    args = ap.parse_args()

    cfg = config.load()
    members = list(cfg.merged)[: args.symbols] if args.symbols else list(cfg.merged)
    OUT_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    print(f"  ATH band rule over {len(members)} stocks, bands {args.bands}, "
          f"account from {args.start}", flush=True)

    t0 = time.time()
    hold_by_start = {args.start: validation.buy_and_hold(members, start_year=args.start)}
    hold_windows = validation.hold_by_window(members)
    print(f"  equal-weight hold from {args.start}: {_fmt(hold_by_start[args.start])}% a year "
          f"({time.time() - t0:.0f}s)", flush=True)

    rows = []
    for band in args.bands:
        t1 = time.time()
        _execution(False)
        trades: list[dict] = []
        for sym in members:
            trades.extend(build(sym, band))
        # Same treatment as the board's "Realistic fills": spread charged onto
        # the finished trades, impact and the 1% size cap live in the account.
        _execution(True)
        trades = [slippage.apply_spread(t) for t in trades]
        row = summarise(band, trades, members, args.start, hold_by_start, hold_windows)
        _execution(False)
        rows.append(row)
        print(f"    band {band:.0%}: {len(trades):,} signals in {time.time() - t1:.0f}s",
              flush=True)
        with open(OUT_DIR / f"ath_band_{band:.2f}_trades_{stamp}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(trades[0].keys()) if trades else ["symbol"])
            w.writeheader()
            for t in trades:
                w.writerow({k: (v.date() if isinstance(v, pd.Timestamp) else v)
                            for k, v in t.items()})

    # The bar. The board's own n_eff is not known here without its payload, so
    # the family-wise 95% hurdle is quoted for two readings: these three bands
    # alone, and these three on top of the board's 19 (n_eff ~6.2 measured
    # 2026-09-05). Both are approximations; the exact number is what a
    # registry entry would get.
    nd = NormalDist()
    hurdle_alone = nd.inv_cdf(1 - 0.05 / len(args.bands))
    hurdle_board = nd.inv_cdf(1 - 0.05 / (6.2 + len(args.bands)))

    print()
    print(f"  {'band':>5} {'signals':>8} {'meanR':>6} {'drift':>6} {'t_iid':>6} {'t':>6} "
          f"{'hold%':>6} {'2L cagr':>8} {'2L mar':>7} {'2L t':>6} {'1cr cagr':>9} "
          f"{'1cr mar':>8} {'wf':>5} {'be bp':>6}")
    for r in rows:
        wf = "—" if r["wf_total"] is None else f"{r['wf_wins']}/{r['wf_total']}"
        print(f"  {r['band']:>5.0%} {r['signals']:>8,} {_fmt(r['mean_r'], 2):>6} "
              f"{_fmt(r['drift_r'], 2):>6} {_fmt(r['t_iid'], 1):>6} {_fmt(r['t_stat'], 2):>6} "
              f"{_fmt(r['hold_cagr']):>6} {_fmt(r['cagr_2L']):>8} {_fmt(r['mar_2L'], 2):>7} "
              f"{_fmt(r['t_taken_2L'], 2):>6} {_fmt(r['cagr_1cr']):>9} "
              f"{_fmt(r['mar_1cr'], 2):>8} {wf:>5} {_fmt(r['breakeven_bp']):>6}")
    print()
    print("  t is the drift-adjusted, quarter-clustered credibility statistic on every "
          "signal; '2L t' the same on the trades the ₹2L account took.")
    print(f"  family-wise 95% bar: {hurdle_alone:.2f} for these {len(args.bands)} bands "
          f"alone, {hurdle_board:.2f} counted on top of the board's variants.")
    print(f"  hold% is the equal-weight buy-and-hold of the same {len(members)} stocks "
          f"from {args.start}; wf = windows beating their own hold / windows counted.")

    with open(OUT_DIR / f"ath_band_summary_{stamp}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n  written: {OUT_DIR}/ath_band_summary_{stamp}.csv and the per-band trade lists "
          f"({time.time() - t0:.0f}s total)\n")


if __name__ == "__main__":
    main()

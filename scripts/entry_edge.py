"""Does the entry predict anything, and is the exit rule throwing it away?

    python -m scripts.entry_edge

TWO QUESTIONS THE PER-CELL BOARD CANNOT ANSWER, FOR ONE REASON: POWER. Each
grid cell carries ~2,125 daily observations and a median detectable edge of
16.35 CAGR points a year, so "is this rule better than holding" is unanswerable
at this sample size by anyone. But the board holds 1.27 MILLION individual
trades. Pool them and the error bars collapse -- as long as the pooling is done
honestly, which is most of the work below.

  1. EVENT STUDY. From each entry, what did the stock do over the next
     5 / 20 / 60 / 250 sessions, against two controls?
       * the equal-weight buy-and-hold index over the SAME dates (removes the
         market regime -- 2009 was kind to every entry rule ever written);
       * the same stock's own average forward return from a random session
         (removes the fact that some rules only ever trade rising stocks).
     Flat at zero      -> the signal is empty; stops and exits never mattered.
     Positive, yet the real trades lose -> the machinery destroys a real edge.

  2. HOLDING-PERIOD COUNTERFACTUAL. Same entries, exit rule DISCARDED, hold a
     fixed 60 or 250 sessions. If that beats what the rule's own exits earned,
     the exit is the problem rather than the entry.

  3. TIME-NORMALISED RATES, AND THE EXPOSURE RECONCILIATION (added 2026-09-10,
     because section 2 read backwards without them). The raw counterfactual
     table says a 250-session hold trounces the rule's own exit -- but the
     daily families hold for a MEDIAN OF 3 SESSIONS, so that table compares
     calendar, not skill. Annualise per session actually in the market and the
     conclusion INVERTS: the rules' own exits beat both fixed holds for almost
     every rule. The exit is not the leak.
     The leak is exposure. Held stock-sessions over all stock-sessions the
     universe offers runs from 5.9% (Turtle 20-10 + weekly) to 49.9%
     (EMA . Q/M); scale the in-market rate by that, with idle cash at 0, and
     the median rule earns 5.2%/yr against 14.09% for holding -- a deficit of
     8.9 points, landing within two points of the board's independently
     measured median -10.88, by a route sharing almost no code with it.

WHY THE ERROR BARS ARE NOT sqrt(n). The 1.27M trades are nowhere near
independent: on any given day hundreds of stocks fire the same signal because
the market moved, the same stock is held by every rule at once, and a
250-session horizon overlaps the next 249 days of entries. Treating them as
independent would divide the standard error by ~1,128 and manufacture
significance out of nothing. So every test here:
  * collapses each calendar date to ONE mean excess, which absorbs the
    cross-sectional correlation exactly, then
  * applies a Newey-West HAC error to that date series at lag = the horizon,
    which is the standard correction for overlapping forward returns.
The effective sample is ~5,000 dates, not 1.27M trades.

Reads:  every registered rule's *_all.pkl signal cache and the daily
        parquet candles. The rule count comes from registry.REGISTRY.
Writes: output/measurements/entry_edge_<N>strat_<date>.csv
        output/measurements/holding_counterfactual_<N>strat_<date>.csv
        output/measurements/exposure_<N>strat_<date>.csv
        output/entry_edge_curve.png
"""
from __future__ import annotations

import csv
import datetime
import math
import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")            # no display in the container; PNG only
import matplotlib.pyplot as plt  # noqa: E402

from kitelab import config, registry, signals, validation  # noqa: E402

HORIZONS = (5, 20, 60, 250)
# Anchored on the repo, not the shell's cwd -- see the CLAUDE.md "output/ is
# flat" trap: every measurement script here resolves output/ for itself.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
CURVE = os.path.join(OUT, "entry_edge_curve.png")
N_RULES = len(registry.REGISTRY)
# The board has been cut twice (19 -> 13 -> 9) while these filenames stayed
# fixed, so a CSV named for a DATE said nothing about which board it holds.
STAMP = f"{N_RULES}strat_{datetime.date.today():%Y-%m-%d}"
POOLED_LABEL = f"ALL {N_RULES} POOLED"
EVENT_CSV = os.path.join(OUT, "measurements", f"entry_edge_{STAMP}.csv")
HOLD_CSV = os.path.join(OUT, "measurements", f"holding_counterfactual_{STAMP}.csv")
EXPO_CSV = os.path.join(OUT, "measurements", f"exposure_{STAMP}.csv")
TOLL = 0.00222   # round-trip statutory cost, 0.222% of position value
YEAR = 250.0     # trading sessions in a year, this project's convention
NW_FLOOR = 21


# ------------------------------------------------------------- price data ----
def close_matrix(universe):
    """Sessions x symbols closes, plus the masks that keep dead stocks honest.

    Returns (index, symbols, filled, traded, last_row). `filled` is forward-filled so a
    halted stock carries its last price; `traded` marks the sessions on which
    the stock ACTUALLY printed, and `last_row` its final one. Both exist so a
    forward return is never read off a price the stock no longer had -- a
    delisted stock's horizon is TRUNCATED at its last real close rather than
    dropped, because dropping it would quietly rebuild survivorship bias.
    """
    from kitelab import frames
    series = {}
    for sym in universe:
        try:
            d = frames.daily(sym)
        except SystemExit:
            continue
        if d is None or len(d) < validation.MIN_HOLD_BARS:
            continue
        s = pd.Series(d["close"].to_numpy(float),
                      index=pd.DatetimeIndex(pd.to_datetime(d["ts"])).normalize())
        series[sym] = s[~s.index.duplicated(keep="last")]
    if not series:
        raise SystemExit("no usable price series")

    raw = pd.DataFrame(series).sort_index()
    print(f"  price matrix: {raw.shape[0]} sessions x {raw.shape[1]} symbols "
          f"({len(universe) - raw.shape[1]} skipped: missing, or under "
          f"{validation.MIN_HOLD_BARS} bars)")
    print(f"  {raw.index[0].date()} .. {raw.index[-1].date()}")
    print("  first 3 rows, first 4 symbols:")
    print(raw.iloc[:3, :4].to_string().replace("\n", "\n    "))

    traded = raw.notna().to_numpy()
    filled = raw.ffill().to_numpy(float)
    if np.isnan(filled[traded]).any():
        raise SystemExit("NaN survived the forward fill on a traded session")
    last_row = np.where(traded.any(axis=0), traded.shape[0] - 1
                        - np.argmax(traded[::-1], axis=0), -1)
    return raw.index, list(raw.columns), filled, traded, last_row


def hold_index(filled, traded):
    """Equal-weight buy-and-hold wealth, validation._hold's convention.

    Rs1 into each stock at its first close and held; a stock that has not
    listed yet holds its Rs1 in cash and earns nothing -- "late money, not free
    money". The mean over stocks, so the curve starts at 1.0. This is the same
    benchmark the board's "beats hold" gate uses, which is why it is the
    benchmark here rather than a cross-sectional average of returns.
    """
    first = np.argmax(traded, axis=0)
    base = filled[first, np.arange(filled.shape[1])]
    wealth = filled / base
    alive = np.arange(filled.shape[0])[:, None] >= first[None, :]
    wealth = np.where(alive, wealth, 1.0)      # pre-listing -> Rs1 in cash
    return wealth.mean(axis=1)


def forward(filled, last_row, rows, cols, h):
    """Forward return over `h` sessions for the given (row, col) entries."""
    end = np.minimum(rows + h, last_row[cols])
    return filled[end, cols] / filled[rows, cols] - 1.0


def drift_by_stock(filled, traded, last_row, h):
    """Each stock's mean forward h-return from a RANDOM one of its sessions."""
    n_cols = filled.shape[1]
    out = np.full(n_cols, np.nan)
    for j in range(n_cols):
        live = np.flatnonzero(traded[:, j])
        if live.size == 0:
            continue
        end = np.minimum(live + h, last_row[j])
        r = filled[end, j] / filled[live, j] - 1.0
        out[j] = float(np.mean(r))
    return out


# ------------------------------------------------------------------ stats ----
def hac_se(x, lag):
    """Newey-West HAC standard error of the mean of `x`. wf_daily's arithmetic."""
    n = len(x)
    if n < 3:
        return None
    d = x - x.mean()
    s = float(d @ d) / n
    for l in range(1, min(lag, n - 1) + 1):
        g = float(d[l:] @ d[:-l]) / n
        s += 2.0 * (1.0 - l / (lag + 1)) * g
    if s <= 0:
        return None
    return math.sqrt(s / n)


def by_date(rows, values):
    """Collapse to one mean per entry session -- absorbs same-day correlation."""
    order = np.argsort(rows, kind="stable")
    r, v = rows[order], values[order]
    edges = np.flatnonzero(np.diff(r)) + 1
    sums = np.add.reduceat(v, np.r_[0, edges])
    counts = np.diff(np.r_[0, edges, len(v)])
    return sums / counts


def tstat(rows, values, lag):
    """Mean, HAC standard error and t, on the date-collapsed series."""
    ok = np.isfinite(values)
    if ok.sum() < 100:
        return None
    series = by_date(rows[ok], values[ok])
    se = hac_se(series, lag)
    mean = float(series.mean())
    if se is None or se == 0:
        return {"mean": mean, "se": None, "t": None, "dates": len(series)}
    return {"mean": mean, "se": se, "t": mean / se, "dates": len(series)}


# ------------------------------------------------------- rates and exposure ---
def rate_from_log(logret, sessions):
    """Annualised compound return WHILE INVESTED.

    From the mean log return per trade divided by the mean sessions per trade,
    so a 3-session rule and a 200-session rule are put on one scale. "While
    invested" is the whole point and also the whole caveat: it assumes the next
    trade starts the day this one ends, which no rule here does. It is an upper
    bound, applied identically to the rule column and both hold columns, so the
    COMPARISON survives even though no single number is achievable.
    """
    if sessions <= 0:
        return float("nan")
    return math.expm1(YEAR * float(np.mean(logret)) / sessions)


def per_trade_sessions(rows, exits, gross):
    """(mean sessions held, total sessions held, mean log return) for closed trades.

    Trades whose exit date is not a session in the matrix are dropped here --
    they cannot contribute a holding period. Returns are clipped at -99.9%
    before the log so a total wipeout does not become -inf.
    """
    ok = exits >= 0
    sess = np.maximum(exits[ok] - rows[ok], 1)
    lg = np.log1p(np.clip(gross[ok], -0.999, None))
    return float(np.mean(sess)), float(np.sum(sess)), lg


# ------------------------------------------------------------------- main ----
def load_entries(universe, index, col_of):
    """(rule -> arrays of entry row/col plus the trade's own outcome)."""
    sessions = {ts: i for i, ts in enumerate(index)}
    out = {}
    total_cached = total_kept = 0
    for strat in registry.REGISTRY:
        trades = signals.load(f"{strat.cache}_all", universe)
        if not trades:
            print(f"  {strat.label}: no cache -- run scripts.refresh first")
            continue
        rows, cols, gross, held = [], [], [], []
        for t in trades:
            j = col_of.get(t["symbol"])
            i = sessions.get(pd.Timestamp(t["entry_ts"]).normalize())
            if j is None or i is None:
                continue
            e = sessions.get(pd.Timestamp(t["exit_ts"]).normalize())
            rows.append(i); cols.append(j)
            gross.append(float(t["exit_price"]) / float(t["entry_price"]) - 1.0)
            held.append(-1 if e is None else e)
        total_cached += len(trades)
        total_kept += len(rows)
        print(f"  {strat.label:<38} {len(trades):>8,} cached -> "
              f"{len(rows):>8,} matched to a session "
              f"({100.0 * len(rows) / len(trades):.1f}%)")
        out[strat] = (np.array(rows), np.array(cols),
                      np.array(gross), np.array(held))
    print(f"\n  {total_cached:,} cached trades -> {total_kept:,} usable "
          f"({100.0 * total_kept / total_cached:.2f}%). An entry is dropped only "
          "when its stamp is not a session in the matrix.")
    if total_kept < 0.9 * total_cached:
        raise SystemExit("more than 10% of entries failed to match a session -- "
                         "the entry stamp is not the decision date, stop here")
    return out


def main() -> None:
    cfg = config.load()
    universe = cfg.merged
    print(f"\n  {len(universe)} stocks, {len(registry.REGISTRY)} rules.\n")

    index, symbols, filled, traded, last_row = close_matrix(universe)
    col_of = {sym: j for j, sym in enumerate(symbols)}

    print()
    entries = load_entries(universe, index, col_of)
    hold = hold_index(filled, traded)

    # ---------------------------------------------------------- event study --
    print("\n  EVENT STUDY -- mean forward return from the entry, per session\n")
    head = (f"  {'rule':<34}{'h':>5}{'raw %':>9}{'vs hold %':>11}{'t':>8}"
            f"{'vs own drift %':>16}{'t':>8}")
    print(head); print("  " + "-" * (len(head) - 2))

    rows_out, pooled = [], {h: {"rows": [], "exc": [], "drift": []} for h in HORIZONS}
    for h in HORIZONS:
        end = np.minimum(np.arange(len(index)) + h, len(index) - 1)
        mkt = hold[end] / hold - 1.0
        drift = drift_by_stock(filled, traded, last_row, h)
        for strat, (r, c, _g, _e) in entries.items():
            if r.size == 0:
                continue
            sig = forward(filled, last_row, r, c, h)
            exc = sig - mkt[r]
            dex = sig - drift[c]
            a = tstat(r, exc, h)
            b = tstat(r, dex, h)
            if a is None or b is None:
                print(f"  {strat.label:<34}{h:>5}   too few entries to test")
                continue
            print(f"  {strat.label:<34}{h:>5}{100 * np.nanmean(sig):>9.2f}"
                  f"{100 * a['mean']:>11.2f}{(a['t'] or 0):>8.2f}"
                  f"{100 * b['mean']:>16.2f}{(b['t'] or 0):>8.2f}")
            rows_out.append({"rule": strat.label, "horizon": h, "trades": int(r.size),
                             "raw_pct": round(100 * float(np.nanmean(sig)), 4),
                             "excess_vs_hold_pct": round(100 * a["mean"], 4),
                             "t_vs_hold": round(a["t"], 3) if a["t"] is not None else None,
                             "excess_vs_drift_pct": round(100 * b["mean"], 4),
                             "t_vs_drift": round(b["t"], 3) if b["t"] is not None else None,
                             "dates": a["dates"]})
            pooled[h]["rows"].append(r)
            pooled[h]["exc"].append(exc)
            pooled[h]["drift"].append(dex)

    print("  " + "-" * (len(head) - 2))
    curve = []
    for h in HORIZONS:
        r = np.concatenate(pooled[h]["rows"])
        e = np.concatenate(pooled[h]["exc"])
        d = np.concatenate(pooled[h]["drift"])
        a, b = tstat(r, e, h), tstat(r, d, h)
        if a is None or b is None or a["se"] is None:
            raise SystemExit(f"pooled horizon {h}: HAC error undefined -- stop")
        print(f"  {POOLED_LABEL:<34}{h:>5}{'':>9}"
              f"{100 * a['mean']:>11.2f}{a['t']:>8.2f}"
              f"{100 * b['mean']:>16.2f}{b['t']:>8.2f}")
        curve.append((h, 100 * a["mean"], 100 * a["se"], 100 * b["mean"]))
        rows_out.append({"rule": POOLED_LABEL, "horizon": h, "trades": int(r.size),
                         "raw_pct": None,
                         "excess_vs_hold_pct": round(100 * a["mean"], 4),
                         "t_vs_hold": round(a["t"], 3),
                         "excess_vs_drift_pct": round(100 * b["mean"], 4),
                         "t_vs_drift": round(b["t"], 3), "dates": a["dates"]})

    # ------------------------------------------- holding-period counterfactual
    print("\n  HOLDING-PERIOD COUNTERFACTUAL -- same entries, exit rule discarded\n")
    h2 = (f"  {'rule':<34}{'trades':>9}{'rule exit %':>13}"
          f"{'hold 60 %':>11}{'hold 250 %':>12}{'median days':>13}")
    print(h2); print("  " + "-" * (len(h2) - 2))
    hold_rows = []
    for strat, (r, c, g, e) in entries.items():
        if r.size == 0:
            continue
        f60 = forward(filled, last_row, r, c, 60)
        f250 = forward(filled, last_row, r, c, 250)
        days = np.where(e >= 0, e - r, np.nan)
        print(f"  {strat.label:<34}{r.size:>9,}{100 * float(np.nanmean(g)):>13.2f}"
              f"{100 * float(np.nanmean(f60)):>11.2f}"
              f"{100 * float(np.nanmean(f250)):>12.2f}"
              f"{np.nanmedian(days):>13.0f}")
        hold_rows.append({"rule": strat.label, "trades": int(r.size),
                          "rule_exit_pct": round(100 * float(np.nanmean(g)), 4),
                          "hold_60_pct": round(100 * float(np.nanmean(f60)), 4),
                          "hold_250_pct": round(100 * float(np.nanmean(f250)), 4),
                          "median_sessions_held": float(np.nanmedian(days))})
    print("\n  Gross of costs on both sides, and NOT annualised: a 250-session")
    print("  hold has more calendar in it than a 20-session trade, so read the")
    print("  columns against the hold benchmark above, never against each other.\n")

    # ------------------------------------- time-normalised rates, and exposure
    # Section 2's table is gross and un-annualised, which reads BACKWARDS: the
    # daily families hold 3 sessions and a 250-session hold obviously earns
    # more over 250 sessions. Everything below is per session in the market.
    yrs = len(index) / YEAR
    mkt_ann = (hold[-1] / hold[0]) ** (1 / yrs) - 1
    print(f"  equal-weight hold: {100 * mkt_ann:.2f}% a year over {yrs:.1f} years\n")

    h3 = (f"  {'rule':<34}{'mean sess':>10}{'rule/yr %':>11}{'toll/yr %':>11}"
          f"{'net/yr %':>10}{'hold60/yr %':>13}{'hold250/yr %':>14}")
    print(h3); print("  " + "-" * (len(h3) - 2))
    rate_rows = []
    for strat, (r, c, g, e) in entries.items():
        if r.size == 0:
            continue
        s_mean, _s_tot, lg = per_trade_sessions(r, e, g)
        f60 = forward(filled, last_row, r, c, 60)
        f250 = forward(filled, last_row, r, c, 250)
        rule_yr = rate_from_log(lg, s_mean)
        toll_yr = math.expm1(-(YEAR / s_mean) * TOLL)
        net_yr = math.expm1(YEAR * (float(np.mean(lg)) - TOLL) / s_mean)
        h60 = rate_from_log(np.log1p(np.clip(f60, -0.999, None)), 60.0)
        h250 = rate_from_log(np.log1p(np.clip(f250, -0.999, None)), 250.0)
        print(f"  {strat.label:<34}{s_mean:>10.1f}{100 * rule_yr:>11.1f}"
              f"{100 * toll_yr:>11.1f}{100 * net_yr:>10.1f}"
              f"{100 * h60:>13.1f}{100 * h250:>14.1f}")
        rate_rows.append({"rule": strat.label,
                          "mean_sessions_held": round(s_mean, 2),
                          "rule_pct_yr": round(100 * rule_yr, 3),
                          "toll_pct_yr": round(100 * toll_yr, 3),
                          "net_pct_yr": round(100 * net_yr, 3),
                          "hold60_pct_yr": round(100 * h60, 3),
                          "hold250_pct_yr": round(100 * h250, 3)})
    print("\n  Rates are WHILE INVESTED: they assume the next trade starts the day")
    print("  this one ends. Real exposure is lower, so these are upper bounds on all")
    print("  columns alike. Toll is the 0.222% round trip only -- no slippage, no")
    print("  1% fill cap. The rule column beating both hold columns is the finding:")
    print("  discarding the exit rule earns LESS per session in the market.\n")

    # Which raises the obvious question: if the entries beat the market at 60
    # and 250 sessions and the exits beat fixed holds per session, where does
    # the board's ~11-point annual deficit come from? Exposure. The denominator
    # below is every stock-session the universe offers, because that is what an
    # equal-weight hold -- which owns everything, always -- actually collects.
    print("  EXPOSURE RECONCILIATION -- the in-market rate, scaled by time in market\n")
    first = np.argmax(traded, axis=0)
    live = float(np.sum(last_row - first + 1))
    print(f"  universe offers {live:,.0f} stock-sessions.\n")
    h4 = (f"  {'rule':<34}{'exposure %':>12}{'net/yr in mkt %':>17}"
          f"{'effective/yr %':>16}{'vs hold pts':>13}")
    print(h4); print("  " + "-" * (len(h4) - 2))
    expo_rows, effs = [], []
    for strat, (r, c, g, e) in entries.items():
        if r.size == 0:
            continue
        s_mean, s_tot, lg = per_trade_sessions(r, e, g)
        expo = s_tot / live
        net_in = YEAR * (float(np.mean(lg)) - TOLL) / s_mean   # log rate/yr in mkt
        eff = math.expm1(net_in * expo)                        # idle cash earns 0
        print(f"  {strat.label:<34}{100 * expo:>12.2f}"
              f"{100 * math.expm1(net_in):>17.1f}{100 * eff:>16.1f}"
              f"{100 * (eff - mkt_ann):>13.1f}")
        expo_rows.append({"rule": strat.label,
                          "exposure_pct": round(100 * expo, 3),
                          "net_pct_yr_in_market": round(100 * math.expm1(net_in), 3),
                          "effective_pct_yr": round(100 * eff, 3),
                          "vs_hold_pts": round(100 * (eff - mkt_ann), 3)})
        effs.append(eff)
    med = float(np.median(effs))
    print(f"\n  median effective {100 * med:.1f}%/yr vs {100 * mkt_ann:.2f}% for "
          f"holding = {100 * (med - mkt_ann):.1f} points.")
    print("  Exposure is held stock-sessions over all stock-sessions available, the")
    print("  right denominator against an equal-weight hold that owns everything")
    print("  always. Idle capital earns 0 here; a real account would earn something.")
    print("  Compare the board's independently measured median deficit, -10.88.\n")

    # ------------------------------------------------------------- outputs ---
    with open(EVENT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)
    with open(HOLD_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(hold_rows[0].keys()))
        w.writeheader(); w.writerows(hold_rows)
    merged = [{**a, **{k: v for k, v in b.items() if k != "rule"}}
              for a, b in zip(rate_rows, expo_rows)]
    with open(EXPO_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(merged[0].keys()))
        w.writeheader(); w.writerows(merged)

    hs = [c[0] for c in curve]
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.axhline(0, color="#888", lw=0.8)
    ax.errorbar(hs, [c[1] for c in curve], yerr=[1.96 * c[2] for c in curve],
                marker="o", capsize=4, label="excess over equal-weight hold")
    ax.plot(hs, [c[3] for c in curve], marker="s", ls="--",
            label="excess over the same stock's own drift")
    ax.set_xlabel("sessions after the entry")
    ax.set_ylabel("mean excess return, %")
    ax.set_title(f"Pooled event study, {N_RULES} rules, all entries\n"
                 "bars are 95% Newey-West, on ~5,000 date means")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CURVE, dpi=140)
    print(f"  wrote {EVENT_CSV}, {HOLD_CSV}, {EXPO_CSV}, {CURVE}\n")


if __name__ == "__main__":
    main()

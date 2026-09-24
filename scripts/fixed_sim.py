"""Run the repaired rules of `scripts/fixed_rules.py`, one exit arm at a time.

WHAT THIS IS FOR. The board's fourteen entries.py rows all end their trades
the same two ways -- a fixed stop, or a 60-session cap -- so the exit axis
does not exist on it. `scripts/fixed_rules.py` gives each rule an exit derived
from its own premise. This runs them side by side and writes one CSV. It reads
price frames through `kitelab.frames` (the read-only CLEAN parquet) and writes
nothing except that CSV under output/measurements/.

NOTHING IN HERE IS FITTED. No parameter is swept, no arm is selected, and the
broken baseline is kept deliberately so the comparison has a control. The
arms are reported together; picking the winner afterwards would be exactly the
hindsight this whole exercise exists to avoid.

THE DECOMPOSITION. Three of the arms answer one question each:

    board    the original entry and the original exit -- entries.simulate,
             untouched, the true control
    current  the REPAIRED entry with the ORIGINAL exit (fixed stop, 60-session
             cap). board -> current is the entry repair on its own.
    native   the repaired entry with the rule's OWN exit.
             current -> native is the exit repair on its own.

and two more are outside exits applied to every rule, both with a published
parameter that this project did not choose:

    chandelier  highest high since entry - 3 x ATR(14). LeBeau & Lucas (1992),
                Computer Analysis of the Futures Markets; the multiple is theirs.
    sar         Wilder (1978) parabolic SAR, AF 0.02 step 0.02 cap 0.20 --
                his own three numbers, unchanged.
    raschke     Street Smarts, the three numeric rules she states: out on a
                range-expansion bar, out by six bars, and the stop to breakeven
                as soon as the trade is profitable. Her partial scale-out is
                NOT modelled -- this engine holds whole positions -- so this arm
                is her timing without her position management, and that is a
                real difference, not a rounding.

CONVENTIONS ARE COPIED FROM entries.simulate SO THE ARMS ARE COMPARABLE.
Rising edge only, the entry fills at the signal bar's close, the stop is checked
before anything else so a bar that breaks two rules is charged the worse one,
sizing and charges come from the same functions, and the exit scan starts the
session AFTER the entry. The only thing that differs between arms is the exit.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

from kitelab import config, entries, frames, indicators, sizing, slippage
from kitelab.backtest import charges

sys.path.insert(0, "scripts") if "scripts" not in sys.path else None
import fixed_rules                                       # noqa: E402

MEAS = "/work/kitelab/output/measurements"
ARMS = ("current", "native", "chandelier", "sar", "raschke")
CHANDELIER_K = 3.0          # LeBeau & Lucas
SAR_STEP, SAR_CAP = 0.02, 0.20      # Wilder
RASCHKE_BARS = fixed_rules.RASCHKE_BARS
BOARD_MAXHOLD = entries.MAXHOLD


# ---------------------------------------------------------------------------
# Cross-sectional context. Two rules are not functions of one symbol's bars.
# ---------------------------------------------------------------------------
def build_panels(symbols: list[str]) -> dict:
    """Momentum ranks (with Jegadeesh-Titman's skip) and the weak-market flag.

    Both are computed over the SAMPLE, not the universe, so a 50-symbol pilot
    ranks within its fifty. That makes `xrank` scores from a pilot and from a
    full run incomparable -- which is why the sample size is written into the
    output filename.
    """
    closes = {}
    for s in symbols:
        d = frames.daily(s)
        if d.empty:
            continue
        closes[s] = pd.Series(d["close"].to_numpy(float),
                              index=pd.DatetimeIndex(d["ts"]).normalize())
    if not closes:
        raise ValueError("no symbol in the sample has any daily bars")
    wide = pd.DataFrame(closes).sort_index()
    print(f"panel: {wide.shape[0]} sessions x {wide.shape[1]} symbols")

    # Jegadeesh-Titman: rank on the 252-session return ENDING 21 sessions ago.
    # The board ranked to today, which is the one thing their paper says not to
    # do -- the skip is there to step over short-term reversal.
    lag = wide.shift(fixed_rules.JT_SKIP)
    mom = lag / lag.shift(fixed_rules.MOM_LOOKBACK) - 1.0
    pct = mom.rank(axis=1, pct=True, na_option="keep")
    top = pct.ge(0.90)

    # The market proxy, copied in behaviour from entries._build_panels
    # (kitelab/entries.py:252): an equal-weighted index of RETURNS, compounded.
    #
    # It used to be `wide.mean(axis=1)` -- the average of raw closing PRICES --
    # with a comment claiming that was the same shape. It is not, and the
    # difference was measurable: on this 150-symbol panel the two definitions
    # disagreed about whether the market was weak on 551 of 5,121 sessions
    # (10.8%), which silently changed `mktrel`'s entry even though `mktrel` is
    # documented as needing no entry repair. A price-level average is dominated
    # by high-priced names and steps whenever a symbol enters or leaves the
    # panel; a return index does neither. Fixed 2026-09-24.
    proxy = (1.0 + wide.pct_change(fill_method=None)
             .mean(axis=1, skipna=True).fillna(0.0)).cumprod()
    weak = (proxy / proxy.shift(fixed_rules.REL_LOOKBACK) - 1.0).lt(0.0)
    print(f"panel: top-decile cells {int(top.to_numpy().sum())}, "
          f"weak-market sessions {int(weak.sum())} of {len(weak)}")
    return {"top": top, "weak": weak}


def symbol_ctx(symbol: str, day: pd.DataFrame, panels: dict) -> dict:
    """The panel columns and the Pine signals, aligned to this symbol's bars."""
    idx = pd.DatetimeIndex(day["ts"]).normalize()
    top = panels["top"]
    col = (top[symbol].reindex(idx).fillna(False).to_numpy(bool)
           if symbol in top.columns else np.zeros(len(idx), bool))
    weak = panels["weak"].reindex(idx).fillna(False).to_numpy(bool)

    fires = entries._pine_fires(symbol, day)
    off = pd.Series(False, index=range(len(day)))
    try:
        from kitelab import pine as pine_mod
        sig = pine_mod.signals(symbol, entries.PINE_TF)
        if not sig.empty:
            stamp = sig["end_ts"] if "end_ts" in sig.columns else sig["ts"]
            bad = sig["ha_exit_long"].to_numpy(bool) | ~sig["trend_up"].to_numpy(bool)
            when = pd.DatetimeIndex(stamp[bad]).normalize()
            at = idx.get_indexer(when)
            at = at[at >= 0] + entries.PINE_SHIFT
            off.iloc[at[at < len(day)]] = True
    except Exception:
        pass            # pine is optional; a symbol without it simply never fires
    return {"xrank": pd.Series(col), "weak": pd.Series(weak),
            "pine": fires, "pine_off": off}


# ---------------------------------------------------------------------------
# The engine. One function, one arm, conventions copied from entries.simulate.
# ---------------------------------------------------------------------------
def _sar_series(high, low, entry_i, total):
    """Wilder's parabolic SAR for a long started at `entry_i`, his parameters."""
    sar = np.full(total, np.nan)
    af, ep, s = SAR_STEP, float(high[entry_i]), float(low[entry_i])
    for i in range(entry_i + 1, total):
        s = s + af * (ep - s)
        s = min(s, float(low[i - 1]), float(low[max(i - 2, entry_i)]))
        sar[i] = s
        if high[i] > ep:
            ep = float(high[i])
            af = min(af + SAR_STEP, SAR_CAP)
    return sar


def simulate(symbol: str, rule: str, stop_mult: float | None, arm: str,
             panels: dict) -> list[dict]:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; known: {ARMS}")
    day = frames.daily(symbol).reset_index(drop=True)
    if day.empty or len(day) < 2:
        return []
    spec = fixed_rules.build(rule, day, symbol_ctx(symbol, day, panels))
    fires = spec["fires"]
    state = spec["exit_state"]
    target, invalid = spec["target"], spec["invalidate"]
    tstop = spec["time_stop"]

    open_, high, low, close = (day[c].to_numpy(float)
                               for c in ("open", "high", "low", "close"))
    atr = indicators.atr(day["high"], day["low"], day["close"],
                         entries.ATR_LEN).to_numpy(float)
    tr = indicators.true_range(day["high"], day["low"], day["close"])
    expand = tr.ge(tr.rolling(fixed_rules.VCON_LEN,
                              min_periods=fixed_rules.VCON_LEN).max()).to_numpy(bool)
    stamps = pd.DatetimeIndex(day["ts"]).tolist()
    total = len(day)

    trades: list[dict] = []
    position = 0
    while position < total:
        fresh = fires[position] and position > 0 and not fires[position - 1]
        if not fresh:
            position += 1
            continue
        if stop_mult is None:
            stop = float(low[position])
        else:
            if not np.isfinite(atr[position]):
                position += 1
                continue
            stop = float(close[position]) - stop_mult * float(atr[position])
        entry_index = position
        entry_price = float(close[position])
        risk = entry_price - stop
        if risk <= 0:
            position += 1
            continue

        tgt = float(target[entry_index]) if target is not None else np.nan
        inv = float(invalid[entry_index]) if invalid is not None else np.nan
        sar = _sar_series(high, low, entry_index, total) if arm == "sar" else None
        hh = entry_price
        live_stop = stop

        exit_at = None
        for step in range(position + 1, total):
            # The disaster stop, always first. Raschke: "the exact timing to
            # exit is subjective; what is not subjective is the initial
            # protective stop."
            if close[step] <= live_stop:
                exit_at = (step, float(close[step]), "stop (close)")
                break
            hh = max(hh, float(high[step]))
            held = step - entry_index

            if arm == "current":
                if held >= BOARD_MAXHOLD:
                    exit_at = (step, float(close[step]), f"{BOARD_MAXHOLD}-session limit")
                    break
            elif arm == "native":
                if np.isfinite(tgt) and close[step] >= tgt:
                    exit_at = (step, float(close[step]), "target (premise resolved)")
                    break
                if np.isfinite(inv) and close[step] <= inv:
                    exit_at = (step, float(close[step]), "invalidated")
                    break
                if state[step]:
                    exit_at = (step, float(close[step]), "premise ended")
                    break
                if tstop is not None and held >= tstop:
                    exit_at = (step, float(close[step]), f"{tstop}-session rule limit")
                    break
            elif arm == "chandelier":
                if np.isfinite(atr[step]):
                    live_stop = max(live_stop, hh - CHANDELIER_K * float(atr[step]))
            elif arm == "sar":
                if np.isfinite(sar[step]):
                    live_stop = max(live_stop, float(sar[step]))
            elif arm == "raschke":
                if close[step] > entry_price:
                    live_stop = max(live_stop, entry_price)   # breakeven ratchet
                if expand[step]:
                    exit_at = (step, float(close[step]), "range expansion")
                    break
                if held >= RASCHKE_BARS:
                    exit_at = (step, float(close[step]), f"{RASCHKE_BARS}-bar limit")
                    break
        if exit_at is None:
            break                       # still open at the end of the data

        exit_index, exit_price, reason = exit_at
        shares, risk_taken, capped = sizing.position(entry_price, stop)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue
        quoted_entry, quoted_exit = entry_price, exit_price
        fill_in = slippage.fill(symbol, stamps[entry_index], quoted_entry, +1)
        fill_out = slippage.fill(symbol, stamps[exit_index], quoted_exit, -1)
        gross = (fill_out - fill_in) * shares
        same = stamps[entry_index].date() == stamps[exit_index].date()
        cost = charges(fill_in * shares, fill_out * shares, intraday=same)
        trades.append({
            "symbol": symbol, "rule": rule, "arm": arm,
            "entry_date": stamps[entry_index], "exit_date": stamps[exit_index],
            "entry_price": fill_in, "exit_price": fill_out, "stop": stop,
            "shares": shares, "sessions_held": exit_index - entry_index,
            "exit_reason": reason, "gross_profit": gross, "charges": cost,
            "net_profit": gross - cost,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "capital_capped": capped,
        })
        position = exit_index + 1
    return trades


def board_trades(symbol: str, rule: str, stop_mult: float | None) -> list[dict]:
    """The untouched control: the board's own entry and the board's own exit."""
    out = []
    for t in entries.simulate(symbol, rule, stop_mult):
        out.append({"symbol": symbol, "rule": rule, "arm": "board",
                    "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                    "entry_price": t["entry_price"], "exit_price": t["exit_price"],
                    "stop": t["stop"], "shares": t["shares"],
                    "sessions_held": t["sessions_held"],
                    "exit_reason": t["exit_reason"],
                    "gross_profit": t["gross_profit"], "charges": t["charges"],
                    "net_profit": t["net_profit"], "r_multiple": t["r_multiple"],
                    "capital_capped": t["capital_capped"]})
    return out


def _se_naive(r: pd.Series) -> float:
    """Textbook standard error of a mean: assumes the trades are independent."""
    n = len(r)
    return float("nan") if n < 2 else float(r.std(ddof=1) / np.sqrt(n))


def _se_cluster(frame: pd.DataFrame, key: str) -> float:
    """Cluster-robust standard error of the mean R, clustering on `key`.

    The trades in a cell are NOT independent draws. Two trades on the same
    symbol share that company; two trades opened the same year share that
    market. Either correlation makes the naive standard error too small and the
    t-statistic built on it too large. This is the usual cluster-robust
    sandwich for the special case of a sample mean:

        SE = sqrt( G/(G-1) * sum_g ( sum_{i in g} (r_i - rbar) )^2 ) / n

    with G the number of clusters. It is not a fix for the dependence, it is an
    honest accounting of it, and it is only trustworthy when G is reasonably
    large -- which is why n_symbols and n_years are written out beside it.
    """
    r = frame["r_multiple"].to_numpy(dtype=float)
    n = len(r)
    if n < 2:
        return float("nan")
    dev = r - r.mean()
    sums = pd.Series(dev).groupby(frame[key].to_numpy()).sum().to_numpy()
    grp = len(sums)
    if grp < 2:
        return float("nan")
    return float(np.sqrt(grp / (grp - 1) * np.square(sums).sum()) / n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", type=int, default=20)
    ap.add_argument("--rules", default="", help="comma list; default all 14")
    ap.add_argument("--arms", default=",".join(("board",) + ARMS))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cfg = config.load()
    syms = sorted(cfg.merged)[:args.symbols]
    rules = ([r.strip() for r in args.rules.split(",") if r.strip()]
             or list(fixed_rules.RULES))
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [r for r in rules if r not in fixed_rules.RULES]
    if unknown:
        raise SystemExit(f"unknown rules: {unknown}")
    print(f"symbols {len(syms)}  rules {len(rules)}  arms {len(arms)}  "
          f"stops {list(entries.STOPS)}")

    panels = build_panels(syms)
    rows, t0 = [], time.time()
    with entries.panel_scope(syms):
        for rule in rules:
            for stop_name, mult in entries.STOPS.items():
                for arm in arms:
                    got, errs = [], 0
                    for s in syms:
                        try:
                            got += (board_trades(s, rule, mult) if arm == "board"
                                    else simulate(s, rule, mult, arm, panels))
                        except Exception as exc:
                            errs += 1
                            if errs == 1:
                                print(f"  {rule}|{stop_name}|{arm} {s}: {exc}")
                    for t in got:
                        t["stop_name"] = stop_name
                    rows += got
                    print(f"{rule:>7}|{stop_name:<4}|{arm:<10} "
                          f"trades {len(got):>6}  errors {errs}")
    print(f"simulated in {time.time() - t0:.1f}s")

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no trades produced -- nothing to write")
    print(f"trades frame: {df.shape[0]} rows x {df.shape[1]} columns")
    print(df.head(3).to_string())
    need = ["rule", "arm", "stop_name", "r_multiple", "sessions_held",
            "net_profit", "symbol", "entry_date"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SystemExit(f"required columns missing from the trades frame: {missing}")
    nulls = df[need].isna().sum()
    if nulls.any():
        raise SystemExit(f"unexpected nulls: {nulls[nulls > 0].to_dict()}")

    before = len(df)
    df = df[np.isfinite(df["r_multiple"])]
    print(f"filter finite r_multiple: {before} -> {len(df)} rows")

    df["entry_year"] = pd.DatetimeIndex(df["entry_date"]).year
    g = df.groupby(["rule", "stop_name", "arm"], sort=False)
    summary = pd.DataFrame({
        "trades": g.size(),
        "median_sessions_held": g["sessions_held"].median(),
        "mean_r": g["r_multiple"].mean(),
        "median_r": g["r_multiple"].median(),
        "std_r": g["r_multiple"].std(ddof=1),
        "win_rate": g["r_multiple"].apply(lambda s: float((s > 0).mean())),
        "total_net_rs": g["net_profit"].sum(),
        "n_symbols": g["symbol"].nunique(),
        "n_years": g["entry_year"].nunique(),
        "se_naive": g["r_multiple"].apply(_se_naive),
        "se_symbol": g.apply(lambda f: _se_cluster(f, "symbol"),
                             include_groups=False),
        "se_year": g.apply(lambda f: _se_cluster(f, "entry_year"),
                           include_groups=False),
    }).reset_index()
    top = g["exit_reason"].agg(lambda s: s.value_counts(normalize=True).idxmax())
    share = g["exit_reason"].agg(lambda s: float(s.value_counts(normalize=True).max()))
    summary["top_exit_reason"] = top.to_numpy()
    summary["top_exit_share"] = share.to_numpy()
    print(f"summary: {summary.shape[0]} rows x {summary.shape[1]} columns")
    print(summary.head(3).to_string())

    out = args.out or f"{MEAS}/fixed_rules_{len(syms)}sym_2026-09-23.csv"
    summary.to_csv(out, index=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

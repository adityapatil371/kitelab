"""Nine entry rules that are NOT what the board already does, and how distinct
they actually are.

THE QUESTION, set by the user 2026-09-17: "variance -- all the different ways
we can enter, then we can narrow down the entry and exit rules which are legit
useful". The board is nine labels carrying two ideas (five moving-average
trend rules, four channel breakouts) and n_eff 4.0. Every one of them waits
for price to already be rising and then buys. This script asks what ELSE an
entry could key on, and measures whether each candidate is a genuinely
different bet or the same bet in new clothes.

DISTINCTNESS FIRST, PERFORMANCE SECOND, and deliberately so. The user's stated
criterion for the Pine rule was distinctness rather than returns
(NEXT_TESTS item 6), and [[kitelab-nine-are-worse-than-hold]] has already
settled that none of the incumbents beats buy-and-hold. A candidate that
correlates 0.9 with an incumbent buys nothing whatever its CAGR.

WHAT IS COMPARED. Not equity curves -- SIGNALS. For every rule this builds the
boolean panel "did this rule want to be long this stock at the close of this
session", over 1,012 stocks x ~5,100 sessions. The board's own panels are
recovered from the entry timestamps in its signal caches, which hold every
trade each rule ever generated, unconstrained by the account
(scripts/entry_edge.py relies on the same fact). Two rules that fire on the
same stock-days ARE the same rule regardless of how their accounts diverge
downstream.

    phi     the correlation of the two boolean panels. The headline number,
            and directly comparable to the 0.646 worst-surviving-pair and
            0.616 Pine figure already in NEXT_TESTS.
    jaccard |A and B| / |A or B|. Reported alongside because phi is bounded
            by the two firing rates: a rule that fires on 0.5% of stock-days
            and one that fires on 20% cannot reach a high phi however nested
            they are. Read them together or read neither.

THE RANDOM RULE IS NOT A CANDIDATE, IT IS THE PLACEBO. `rand` fires with a
fixed probability matched to the median firing rate of the other eight. It
must come back at phi ~ 0 against everything, and if it does not, the metric
is broken rather than the board being interesting. Same discipline as the
placebo bands in scripts/band_scope.py, where the control validated the
detector and not the hypothesis.

THE NINE, and what each keys on that the board does not:

  mr      MEAN REVERSION. A new 20-session low. Buys weakness -- the opposite
          sign to every rule on the board, so decorrelation is structural
          rather than hoped for.
  pull    PULLBACK. Long-term uptrend intact (close > SMA200) but price has
          dipped below its own short average (close < SMA20). A hybrid: trend
          filter, reversion trigger.
  vcon    VOLATILITY CONTRACTION. Today's high-low range is the narrowest of
          the last 7 sessions. Fires on RANGE, carrying no directional view
          at all.
  vol     VOLUME ANOMALY. Volume above 3x its own 50-session median. Fires on
          PARTICIPATION rather than price.
  xrank   CROSS-SECTIONAL RANK. In the top decile of 252-session return across
          the whole universe that day. A RELATIVE statement: a stock can
          qualify while falling, if everything else fell harder. The board
          uses a momentum ranking only to decide which signal to fund when
          cash is short (priority mom_hi), never to generate one.
  mktrel  MARKET-RELATIVE. The stock is up over 20 sessions while the market
          proxy is down over 20. Strength against a weak tape; needs two
          series, which nothing on the board does.
  gap     GAP. Opens more than 3% above the previous close. Keys on overnight
          information only.
  cal     CALENDAR. The first session of each month. Contains NO PRICE
          INPUT, so its overlap with any price rule is near zero by
          construction -- the cheapest possible source of variance.
  rand    RANDOM. The placebo described above.

THE MARKET PROXY. There is no index price history in /data/clean/kitelab --
the instrument dump lists NIFTY 50 but carries no candles. The proxy is
therefore built from the universe itself: the equal-weighted cross-sectional
mean of daily returns. It is a proxy for THIS universe, which is 631 small
caps of 1,000, and is not NIFTY. Say so wherever it is quoted.

Reads:  the universe from config.load().merged, daily bars through
        kitelab.frames.daily, and the board's entry stamps from
        /data/clean/kitelab/signal_cache/<cache>_all.pkl.
Writes: output/measurements/entry_zoo_signals_<date>.csv   (firing rates)
        output/measurements/entry_zoo_phi_<date>.csv       (the matrix)
        output/figures/entry_zoo_<date>.png
Rebuilds nothing. Edits no stamped module. Run:

        PYTHONPATH=/work/kitelab python3 -m scripts.entry_zoo [--pilot]
"""
from __future__ import annotations

import pickle
import sys
import time
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from kitelab import config, frames, registry
from kitelab.config import CLEAN

OUT = Path(__file__).resolve().parent.parent / "output"
FIG = OUT / "figures"              # every PNG
FIG.mkdir(parents=True, exist_ok=True)
CACHE = CLEAN / "signal_cache"

SEED = 20260917          # the random placebo, pinned so the run reproduces
GAP_PCT = 3.0            # gap: open this far above the previous close
VOL_MULT = 3.0           # volume anomaly: this many times the 50-session median
XRANK_TOP = 0.10         # cross-sectional rank: top decile
REQUIRED = ["ts", "open", "high", "low", "close", "volume"]

CANDIDATES = ["mr", "pull", "vcon", "vol", "xrank", "mktrel", "gap", "cal",
              "rand"]


def panels(members, pilot=False):
    """Five wide frames (sessions x symbols) of O/H/L/C/V, on one date index.

    Read through frames.daily so these are the bars the engine trades on --
    same sanitise(), same listing-break cut, same corporate-action rescaling.
    A wide panel is what makes the cross-sectional rules possible at all: a
    per-symbol loop cannot see what the other 1,011 stocks did today.
    """
    if pilot:
        members = members[:150]
    cols = {k: {} for k in ("open", "high", "low", "close", "volume")}
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
        for key in cols:
            s = pd.Series(bars[key].to_numpy(float), index=idx)
            cols[key][symbol] = s[~s.index.duplicated(keep="last")]
        if i % 250 == 0:
            print(f"    ... {i:,} of {len(members):,} symbols read")
    if skipped:
        print(f"  {len(skipped):,} symbols had no usable daily frame "
              f"(first few: {skipped[:5]})")
    out = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    n_rows, n_cols = out["close"].shape
    print(f"  panel: {n_rows:,} sessions x {n_cols:,} symbols, "
          f"{out['close'].index.min().date()} to {out['close'].index.max().date()}")
    listed = out["close"].notna()
    print(f"  stock-sessions with a price: {int(listed.to_numpy().sum()):,} "
          f"of {n_rows * n_cols:,} cells in the rectangle")
    return out


def build_signals(p):
    """The nine boolean panels. True = this rule wants to be long at this close.

    Every rule is computed on the panel and then masked to sessions where the
    stock actually has a price, so a rule can never claim a signal on a stock
    that was not listed. Rolling windows use min_periods equal to the window,
    so a rule cannot fire before it has enough history to be defined -- the
    alternative silently gives short-history stocks (the 187 'recent' names)
    signals the long-history ones could not have had.
    """
    o, h, l, c, v = (p["open"], p["high"], p["low"], p["close"], p["volume"])
    listed = c.notna()
    sig = {}

    sig["mr"] = c.le(c.rolling(20, min_periods=20).min())

    sma200 = c.rolling(200, min_periods=200).mean()
    sma20 = c.rolling(20, min_periods=20).mean()
    sig["pull"] = c.gt(sma200) & c.lt(sma20)

    rng = h - l
    sig["vcon"] = rng.le(rng.rolling(7, min_periods=7).min())

    vmed = v.rolling(50, min_periods=50).median()
    sig["vol"] = v.gt(VOL_MULT * vmed) & vmed.gt(0)

    # Cross-sectional: rank WITHIN each session, so the comparison is against
    # what every other stock did on that same day. pct=True gives the
    # proportion of the day's listed stocks at or below this one.
    ret252 = c / c.shift(252) - 1.0
    sig["xrank"] = ret252.rank(axis=1, pct=True).gt(1.0 - XRANK_TOP)

    # The market proxy: the equal-weighted cross-sectional mean daily return of
    # the universe. NOT an index -- there is no index history in the project.
    daily_ret = c.pct_change(fill_method=None)
    proxy_ret = daily_ret.mean(axis=1, skipna=True)
    proxy = (1.0 + proxy_ret.fillna(0.0)).cumprod()
    proxy20 = proxy / proxy.shift(20) - 1.0
    stock20 = c / c.shift(20) - 1.0
    sig["mktrel"] = stock20.gt(0) & pd.DataFrame(
        np.repeat((proxy20 < 0).to_numpy()[:, None], c.shape[1], axis=1),
        index=c.index, columns=c.columns)

    sig["gap"] = o.gt(c.shift(1) * (1.0 + GAP_PCT / 100.0))

    month_start = pd.Series(c.index, index=c.index).groupby(
        [c.index.year, c.index.month]).transform("min") == pd.Series(
        c.index, index=c.index)
    sig["cal"] = pd.DataFrame(
        np.repeat(month_start.to_numpy()[:, None], c.shape[1], axis=1),
        index=c.index, columns=c.columns)

    rates = [float((sig[k] & listed).to_numpy().sum()) / float(listed.to_numpy().sum())
             for k in sig]
    rate = float(np.median(rates))
    rng_gen = np.random.default_rng(SEED)
    sig["rand"] = pd.DataFrame(
        rng_gen.random(c.shape) < rate, index=c.index, columns=c.columns)
    print(f"  placebo `rand` fires at {100 * rate:.3f}% of stock-sessions, "
          f"the median of the other eight")

    for key in sig:
        sig[key] = (sig[key].fillna(False) & listed)
    return sig, listed


def board_signals(index, columns, pilot=False):
    """The board's own entry panels, recovered from its signal caches.

    The caches hold every trade the rule generated per symbol, with no account
    in front of them, so an entry stamp IS a signal. Two caveats stated rather
    than implied: a rule cannot re-enter a stock it is already holding, so its
    panel understates how often the rule WOULD have fired; and aggregated-bar
    rules (pair|MW and friends) stamp an entry on the session the fill
    happened, which is the right date here.
    """
    out = {}
    strats = registry.REGISTRY[:2] if pilot else registry.REGISTRY
    col_pos = {s: i for i, s in enumerate(columns)}
    row_pos = {t: i for i, t in enumerate(index)}
    for strat in strats:
        path = CACHE / f"{strat.cache}_all.pkl"
        if not path.exists():
            raise SystemExit(f"missing signal cache: {path}")
        blob = pickle.loads(path.read_bytes())
        trades = blob["trades"] if isinstance(blob, dict) else blob
        mat = np.zeros((len(index), len(columns)), dtype=bool)
        hit = 0
        for t in trades:
            r = row_pos.get(pd.Timestamp(t["entry_ts"]).normalize())
            cidx = col_pos.get(t["symbol"])
            if r is not None and cidx is not None:
                mat[r, cidx] = True
                hit += 1
        label = f"{strat.key}|{strat.variant}"
        out[label] = pd.DataFrame(mat, index=index, columns=columns)
        print(f"  {label:<14} {len(trades):>8,} trades, {hit:>8,} placed on "
              f"the panel ({100.0 * hit / max(len(trades), 1):.1f}%)")
    return out


def phi_jaccard(a, b, listed):
    """(phi, jaccard) between two boolean panels, over LISTED cells only.

    Restricting to listed cells matters: the rectangle is ~40% empty, and
    counting those as "neither rule fired" inflates phi towards agreement for
    two rules that merely trade the same era.
    """
    m = listed.to_numpy()
    x = a.to_numpy()[m]
    y = b.to_numpy()[m]
    both = float(np.count_nonzero(x & y))
    either = float(np.count_nonzero(x | y))
    jac = both / either if either else float("nan")
    xf, yf = x.astype(np.float32), y.astype(np.float32)
    sx, sy = xf.std(), yf.std()
    if sx == 0 or sy == 0:
        return float("nan"), jac
    phi = float(((xf - xf.mean()) * (yf - yf.mean())).mean() / (sx * sy))
    return phi, jac


def main():
    pilot = "--pilot" in sys.argv
    started = time.time()
    print(f"entry_zoo{'  [PILOT]' if pilot else ''} — nine entry rules that are "
          f"not what the board does\n")

    members = list(config.load().merged)
    print(f"1. universe: {len(members)} stocks from config.load().merged")
    p = panels(members, pilot)

    print("\n2. the nine candidate signal panels")
    sig, listed = build_signals(p)
    n_listed = int(listed.to_numpy().sum())
    rows = []
    for key in CANDIDATES:
        fired = int(sig[key].to_numpy().sum())
        per_stock = sig[key].sum(axis=0)
        rows.append({"rule": key, "kind": "candidate",
                     "signals": fired,
                     "pct_of_stock_sessions": 100.0 * fired / n_listed,
                     "stocks_ever_firing": int((per_stock > 0).sum()),
                     "median_per_stock": float(per_stock.median())})
    print("\n3. the board's own entry panels, from its caches")
    board = board_signals(p["close"].index, p["close"].columns, pilot)
    for label, panel in board.items():
        fired = int(panel.to_numpy().sum())
        per_stock = panel.sum(axis=0)
        rows.append({"rule": label, "kind": "board",
                     "signals": fired,
                     "pct_of_stock_sessions": 100.0 * fired / n_listed,
                     "stocks_ever_firing": int((per_stock > 0).sum()),
                     "median_per_stock": float(per_stock.median())})
    table = pd.DataFrame(rows)
    print("\n4. firing rates. A rule that fires on 20% of stock-sessions and one "
          "that fires\n   on 0.5% cannot show a high phi however nested they "
          "are — read this table\n   beside the matrix, never without it.")
    print(table.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    print("\n5. phi between every candidate and every board rule")
    all_panels = {k: sig[k] for k in CANDIDATES}
    all_panels.update(board)
    names = list(all_panels)
    phi = pd.DataFrame(np.nan, index=names, columns=names, dtype=float)
    jac = pd.DataFrame(np.nan, index=names, columns=names, dtype=float)
    for i, a in enumerate(names):
        for b in names[i:]:
            pv, jv = phi_jaccard(all_panels[a], all_panels[b], listed)
            phi.loc[a, b] = phi.loc[b, a] = pv
            jac.loc[a, b] = jac.loc[b, a] = jv
    board_names = list(board)
    cross = phi.loc[CANDIDATES, board_names]
    print(cross.to_string(float_format=lambda v: f"{v:,.3f}"))

    print("\n6. the verdict column: each candidate's WORST case against the "
          "board")
    summary = pd.DataFrame({
        "nearest_board_rule": cross.idxmax(axis=1),
        "max_phi": cross.max(axis=1),
        "mean_phi": cross.mean(axis=1),
        "max_jaccard": jac.loc[CANDIDATES, board_names].max(axis=1),
    })
    summary["verdict"] = np.where(
        summary["max_phi"] < 0.20, "distinct",
        np.where(summary["max_phi"] < 0.40, "partly distinct", "overlapping"))
    print(summary.to_string(float_format=lambda v: f"{v:,.3f}"))
    print("\n   For scale: the worst pair ALREADY on the board is 0.646 and the "
          "Pine\n   rule's nearest twin is 0.616 — both measured on returns, "
          "not signals, so\n   they are indicative rather than directly "
          "comparable. The placebo `rand`\n   is the number that says whether "
          "this metric works at all.")
    rand_max = float(summary.loc["rand", "max_phi"])
    print(f"   placebo `rand` max phi against the board: {rand_max:.4f}"
          f"  {'-- METRIC OK' if abs(rand_max) < 0.02 else '-- INVESTIGATE'}")

    print("\n7. and against each other, because two new rules that duplicate "
          "each other\n   are worth one slot, not two")
    print(phi.loc[CANDIDATES, CANDIDATES].to_string(
        float_format=lambda v: f"{v:,.3f}"))

    stamp = date.today().isoformat()
    meas = OUT / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    table.to_csv(meas / f"entry_zoo_signals_{stamp}.csv", index=False)
    phi.to_csv(meas / f"entry_zoo_phi_{stamp}.csv")

    fig, ax = plt.subplots(figsize=(11, 6))
    data = cross.to_numpy(dtype=float)
    im = ax.imshow(data, cmap="RdBu_r", vmin=-0.5, vmax=0.5, aspect="auto")
    ax.set_xticks(range(len(board_names)))
    ax.set_xticklabels(board_names, rotation=45, ha="right")
    ax.set_yticks(range(len(CANDIDATES)))
    ax.set_yticklabels(CANDIDATES)
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center",
                    fontsize=7,
                    color="white" if abs(data[i, j]) > 0.3 else "black")
    fig.colorbar(im, ax=ax, label="phi (signal-level correlation)")
    ax.set_title("Candidate entry rules vs the board's own entries\n"
                 "phi over listed stock-sessions; `rand` is the placebo and "
                 "must read ~0")
    fig.tight_layout()
    png = FIG / f"entry_zoo_{stamp}.png"
    fig.savefig(png, dpi=130)
    plt.close(fig)

    print(f"\nwrote {meas / f'entry_zoo_signals_{stamp}.csv'}")
    print(f"wrote {meas / f'entry_zoo_phi_{stamp}.csv'}")
    print(f"wrote {png}")
    print(f"elapsed {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()

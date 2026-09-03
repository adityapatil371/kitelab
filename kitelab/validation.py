"""Is the edge real, or is it a pattern found in noise? The math, not the CLI.

Every function here was `scripts/validate.py` or `scripts/bootstrap.py` until
2026-09-04, when the dashboard needed to show the same numbers those two
scripts print. Both scripts import `positions`, `signal_lists`, `tag` and
`STRATEGY_LABELS` from `scripts.dashboard_data`, so `dashboard_data` importing
their compute functions back would be circular -- these functions have no
dependency on either script or on argparse, so they live here instead.
`scripts/validate.py` and `scripts/bootstrap.py` are now thin CLI wrappers
around this module; their printed output is unchanged.

DIFFERENT FROM tests/. Those check that the CODE does what it was told, which
is necessary and says nothing about whether a strategy makes money. These are
the checks a trader runs on a strategy before believing it.
"""
from __future__ import annotations

import math
import statistics
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import frames, portfolio, signals, strategies

CAPITAL = 200_000
RISK = 0.01
WINDOW_YEARS = 3
TOP_N = (1, 5, 10, 25)

DRAWS = 5_000
RISK_PCT = 1.0
CHUNK = 250          # iterations per block, so a 20k-trade rule stays in memory
# Trades per contiguous block. Around a year of signals for the busier rules,
# which is the scale on which market regimes -- and therefore runs of winners
# -- actually persist. Not tuned: tuning it on the answer would be the very
# thing this module exists to detect.
BLOCK = 20

# Below this many trades a bootstrap or benchmark answer is noise about noise.
# Also the floor scripts.preflight's 3-symbol smoke build sits under, so every
# caller here must degrade to None/empty rather than raise.
MIN_TRADES = 30


def cagr_of(trades, capital=CAPITAL, risk=RISK):
    if not trades:
        return None
    r = portfolio.run(trades, capital, risk)
    return r.get("cagr_pct")


def load(strat, universe):
    return signals.load(f"{strat.cache}_all", universe)


# ------------------------------------------------------------ benchmark ----
def buy_and_hold(universe) -> float | None:
    """Equal-weight buy and hold of the same stocks over the same span.

    Equal weight, not cap weight: the strategies size by risk and hold
    whatever signals, so an index's concentration would be comparing two
    different things.
    """
    rates = []
    for sym in universe:
        try:
            day = frames.daily(sym)
        except SystemExit:
            continue
        if len(day) < 250:
            continue
        first, last = float(day["close"].iloc[0]), float(day["close"].iloc[-1])
        years = (day["ts"].iloc[-1] - day["ts"].iloc[0]).days / 365.25
        if first > 0 and years > 1:
            rates.append(((last / first) ** (1 / years) - 1) * 100)
    return statistics.median(rates) if rates else None


# --------------------------------------------------------- walk-forward ----
def walk_forward(trades):
    """CAGR in each DISJOINT window, and how many were positive."""
    if not trades:
        return []
    stamps = sorted(pd.Timestamp(t["entry_ts"]) for t in trades)
    start, end = stamps[0], stamps[-1]
    out = []
    left = start
    while left < end:
        right = left + pd.DateOffset(years=WINDOW_YEARS)
        window = [t for t in trades if left <= pd.Timestamp(t["entry_ts"]) < right]
        if len(window) >= 20:
            out.append((left.year, right.year, cagr_of(window)))
        left = right
    return out


# --------------------------------------------------------------- top-N ----
def without_best(trades, n):
    """The account with the n most profitable trades deleted."""
    if n >= len(trades):
        return None
    ordered = sorted(trades, key=lambda t: t.get("net_profit", 0.0), reverse=True)
    return cagr_of(ordered[n:])


# ----------------------------------------------------- breakeven friction ----
def _charged(trades, bps):
    """Every trade charged an extra `bps` per side on its turnover."""
    out = []
    for t in trades:
        cost = (bps / 10_000.0) * (t["entry_price"] + t["exit_price"]) * t["shares"]
        u = dict(t)
        u["exit_price"] = t["exit_price"] - cost / max(t["shares"], 1e-9)
        out.append(u)
    return out


def breakeven_cost(trades, hi=200.0):
    """Basis points per side at which the edge reaches zero. None if already
    negative, or if it survives even `hi`."""
    if cagr_of(trades) is None or (cagr_of(trades) or 0) <= 0:
        return None
    if (cagr_of(_charged(trades, hi)) or -1) > 0:
        return float("inf")
    lo = 0.0
    for _ in range(12):                       # 12 bisections is ~0.05bp resolution
        mid = (lo + hi) / 2
        got = cagr_of(_charged(trades, mid))
        if got is not None and got > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 1)


# ---------------------------------------------------------- correlation ----
def monthly_returns(trades):
    """Realised profit by calendar month -- the series to correlate on."""
    by_month: dict = {}
    for t in trades:
        key = pd.Timestamp(t["exit_ts"]).to_period("M")
        by_month[key] = by_month.get(key, 0.0) + t.get("net_profit", 0.0)
    return by_month


def correlate(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 12:
        return None
    x = np.array([a[k] for k in keys], dtype=float)
    y = np.array([b[k] for k in keys], dtype=float)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


# ---------------------------------------------------------- permutation ----
def permutation_test(strat, universe, rounds, rng, sample=60):
    """Observed CAGR against the same rule run on shuffled price paths."""
    pool = sorted(universe)[:sample]
    real = load(strat, universe)
    if not real:
        return None, []
    observed = cagr_of([t for t in real if t["symbol"] in set(pool)])
    got = []
    saved = frames.daily
    try:
        for _ in range(rounds):
            cache = {}

            def fake(sym, *a, **k):
                if sym not in cache:
                    cache[sym] = permuted_daily(sym, rng, saved)
                return cache[sym]

            frames.daily = fake
            trades = []
            for sym in pool:
                try:
                    trades.extend(strat.build(sym))
                except (SystemExit, FileNotFoundError, ValueError, IndexError):
                    continue
            frames.daily = saved
            got.append(cagr_of(trades))
    finally:
        frames.daily = saved
    return observed, [g for g in got if g is not None]


def permuted_daily(symbol, rng, real_daily):
    """The same daily returns in a different order.

    Preserves each stock's return DISTRIBUTION -- drift, volatility, fat
    tails -- and destroys only the sequence. A trend rule has nothing left
    to find, so whatever it still earns there is fitted noise.

    `real_daily` is passed in rather than read from the module, because the
    caller has patched frames.daily to serve these very frames: reading it
    from scope would call this function from inside itself.
    """
    day = real_daily(symbol).reset_index(drop=True)
    close = day["close"].to_numpy(float)
    if len(close) < 60:
        return day
    rets = np.diff(close) / close[:-1]
    rng.shuffle(rets)
    walk = np.concatenate([[close[0]], close[0] * np.cumprod(1 + rets)])
    scale = walk / close
    out = day.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = day[col].to_numpy(float) * scale
    return out


# ------------------------------------------------------- bootstrap ----
def r_multiples(trades: list[dict]) -> np.ndarray:
    """Net R per trade: what the trade returned as a multiple of what it risked.

    net_profit, not gross, so brokerage, STT and the modelled spread are
    already inside every number here. Trades that risked nothing measurable
    (risk_taken of zero -- a stop at the entry price) carry no R and are
    dropped rather than counted as flat, which would dilute the distribution
    toward zero.
    """
    return np.array([t["net_profit"] / t["risk_taken"] for t in trades
                     if t.get("risk_taken")], dtype=float)


def span_years(trades: list[dict]) -> float:
    """Calendar years the trade list actually covers.

    entry_ts / exit_ts, NOT entry_date -- only some producers set the _date
    pair, and the _ts fields are the ones every producer sets.
    """
    first = min(t["entry_ts"] for t in trades)
    last = max(t["exit_ts"] for t in trades)
    return max((last - first).days / 365.25, 1e-9)


def paths(r: np.ndarray, fraction: float, years: float) -> tuple:
    """(CAGR %, max drawdown %, MAR) for each row of an (n, trades) R matrix.

    Compounding is multiplicative on equity, which is what fixed-fractional
    risk means: a rupee risked is a percentage of the account at the time,
    not a constant. The floor on the per-trade factor stops a pathological
    draw from taking equity negative.
    """
    step = np.maximum(1.0 + fraction * r, 1e-6)
    curve = np.cumprod(step, axis=1)
    cagr = (curve[:, -1] ** (1.0 / years) - 1.0) * 100.0
    peak = np.maximum.accumulate(curve, axis=1)
    dd = (curve / peak - 1.0).min(axis=1) * 100.0
    mar = np.divide(cagr, np.abs(dd), out=np.full_like(cagr, np.nan), where=dd < 0)
    return cagr, dd, mar


def _indices(rng, n_rows: int, n_trades: int, block: int) -> np.ndarray:
    """Row indices for one chunk of resamples.

    block <= 1 is the naive i.i.d. draw. Otherwise this is a MOVING BLOCK
    bootstrap: pick random start points and take `block` consecutive trades
    from each, wrapping at the end so every trade is equally likely to
    appear.
    """
    if block <= 1:
        return rng.integers(0, n_trades, size=(n_rows, n_trades))
    n_blocks = -(-n_trades // block)                  # ceiling division
    starts = rng.integers(0, n_trades, size=(n_rows, n_blocks, 1))
    offsets = np.arange(block).reshape(1, 1, block)
    idx = (starts + offsets) % n_trades
    return idx.reshape(n_rows, -1)[:, :n_trades]


def bootstrap(r: np.ndarray, years: float, fraction: float, draws: int, rng,
              block: int = BLOCK):
    """Resample in blocks, in chunks, keeping only the statistics."""
    cagrs, dds, mars = [], [], []
    done = 0
    while done < draws:
        n = min(CHUNK, draws - done)
        idx = _indices(rng, n, len(r), block)
        c, d, m = paths(r[idx], fraction, years)
        cagrs.append(c); dds.append(d); mars.append(m)
        done += n
    return (np.concatenate(cagrs), np.concatenate(dds), np.concatenate(mars))


def expected_best_of(n_trials: int, spread: float) -> float:
    """What the BEST of `n_trials` edgeless rules scores by luck alone.

    The order statistic behind the deflated Sharpe ratio (Bailey & Lopez de
    Prado): the expected maximum of n independent draws from a zero-mean
    normal of the given spread.
    """
    if n_trials < 2 or spread <= 0:
        return 0.0
    nd = NormalDist()
    gamma = 0.5772156649015329                       # Euler-Mascheroni
    a = nd.inv_cdf(1 - 1.0 / n_trials)
    b = nd.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return spread * ((1 - gamma) * a + gamma * b)


# --------------------------------------------- dashboard summary builder ----
#
# Everything above is scripts/validate.py and scripts/bootstrap.py's own
# math, moved rather than rewritten so the page and the CLI tools can never
# disagree. What follows is new: it packages that math into the shapes
# scripts/dashboard_data.py puts on the page.

def _bootstrap_one(trades, risk_pct=RISK_PCT, draws=DRAWS, seed=20260903,
                    block=BLOCK):
    """One rule's bootstrap row, or None below MIN_TRADES.

    Mirrors scripts/bootstrap.py's main() loop for a single rule -- same
    inputs (fixed-fractional risk, block resampling), so `p05` here and the
    'Edge (p05)' column on the page always match what `python -m
    scripts.bootstrap` prints for the same rule.
    """
    r = r_multiples(trades)
    if len(r) < MIN_TRADES:
        return None
    years = span_years(trades)
    fraction = risk_pct / 100.0
    rng = np.random.default_rng(seed)
    obs_cagr, obs_dd, obs_mar = (v[0] for v in paths(r[None, :], fraction, years))
    cagr, dd, mar = bootstrap(r, years, fraction, draws, rng, block)
    return {
        "n": len(r),
        "obs_cagr": round(float(obs_cagr), 1),
        "obs_mar": round(float(obs_mar), 2) if np.isfinite(obs_mar) else None,
        "p05": round(float(np.percentile(cagr, 5)), 1),
        "p50": round(float(np.percentile(cagr, 50)), 1),
        "p95": round(float(np.percentile(cagr, 95)), 1),
        "dd05": round(float(np.percentile(dd, 5)), 1),
        "p_neg": round(float((cagr <= 0).mean()), 3),
        "cleared_95": bool(np.percentile(cagr, 5) > 0),
    }


def _breakeven_payload(trades):
    """JSON-safe wrapper around breakeven_cost().

    breakeven_cost() returns float('inf') for "survives 200bp+" and None for
    "already negative" -- opposite verdicts that both need `json.dumps` to
    survive. Python's json module writes float('inf') as the bare token
    `Infinity`, which is not valid JSON and makes the browser's JSON.parse
    throw on the whole payload. `status` carries the verdict; `bp` is a
    plain finite number only in the ordinary case.
    """
    bp = breakeven_cost(trades)
    if bp is None:
        return {"bp": None, "status": "negative"}
    if bp == float("inf"):
        return {"bp": None, "status": "robust"}
    return {"bp": bp, "status": "finite"}


def validation_summary(strat, trades, universe, rng, permutation_rounds=10,
                        hold_cagr=None):
    """One strategy variant's full validation record, or None below MIN_TRADES.

    `trades` is the strategy's own cached "_all" list (what
    scripts.dashboard_data already holds in `base`) -- one position at a
    time per symbol is enforced here via strategies.drop_overlaps before
    anything that would otherwise double-count an overlapping signal, the
    same fix `positions()` in scripts/dashboard_data.py already applies to
    trade-level stats.

    Computed ONCE over the full universe and full history -- not crossed
    with the account grid's capital/risk/fill/year/priority axes. These
    questions ("is the rule real") do not depend on which account scenario
    is on screen; they answer a question the grid cannot ask.

    `hold_cagr` -- buy_and_hold(universe) does not depend on the strategy,
    only on the universe, so a caller looping over every registered strategy
    (scripts.dashboard_data) should compute it once and pass it in rather
    than pay a full scan of every stock's daily bars 24 times over. Computed
    here when omitted, so this function stays usable on its own.
    """
    held = strategies.drop_overlaps(trades)
    if len(held) < MIN_TRADES:
        return None

    bh = buy_and_hold(universe) if hold_cagr is None else hold_cagr
    observed_cagr = cagr_of(held)
    wf = walk_forward(held)
    positive_windows = sum(1 for _, _, c in wf if c is not None and c > 0)

    return {
        "n": len(held),
        "cagr": round(observed_cagr, 1) if observed_cagr is not None else None,
        "vs_hold": (round(observed_cagr - bh, 1)
                    if observed_cagr is not None and bh is not None else None),
        "hold_cagr": round(bh, 1) if bh is not None else None,
        "walk_forward": [[y0, y1, round(c, 1) if c is not None else None]
                          for y0, y1, c in wf],
        "positive_windows": positive_windows,
        "total_windows": len(wf),
        "top_n": {str(n): (round(c, 1) if c is not None else None)
                  for n in TOP_N for c in [without_best(held, n)]},
        "breakeven": _breakeven_payload(held),
        "bootstrap": _bootstrap_one(held),
        "permutation": _permutation_summary(strat, universe, rng, permutation_rounds),
        "monthly": monthly_returns(held),         # consumed by correlation_summary, stripped after
    }


def _permutation_summary(strat, universe, rng, rounds):
    observed, shuffled = permutation_test(strat, universe, rounds, rng)
    if observed is None or not shuffled:
        return None
    med = float(np.median(shuffled))
    # scripts/validate.py's own flag, not a re-derived one: NOT DISTINGUISHABLE
    # when a QUARTER OR MORE of the shuffled rounds matched or beat the real
    # result. A flat point gap (e.g. "beats by <2%") flags the wrong rows --
    # verified against the printed table, where a 4.7pp beat was flagged and a
    # 5.5pp beat was not, because it is the shuffled DISTRIBUTION that decides,
    # not the gap to its median.
    worse = sum(1 for g in shuffled if g >= observed)
    return {"observed": round(observed, 1), "shuffled_median": round(med, 1),
            "beat_by": round(observed - med, 1),
            "distinguishable": worse * 4 <= len(shuffled)}


def correlation_summary(monthly_by_key: dict) -> dict:
    """For every key, the single most-correlated other key -- not the full
    matrix. 24 numbers a reader will act on beat a 24x24 grid nobody reads."""
    out = {}
    keys = list(monthly_by_key)
    for k in keys:
        best_other, best_r = None, None
        for other in keys:
            if other == k:
                continue
            r = correlate(monthly_by_key[k], monthly_by_key[other])
            if r is not None and (best_r is None or abs(r) > abs(best_r)):
                best_other, best_r = other, r
        out[k] = ({"key": best_other, "r": round(best_r, 2)}
                   if best_other is not None else None)
    return out


def multiple_testing_summary(bootstrap_rows: list[dict]) -> dict | None:
    """The paragraph scripts/bootstrap.py prints last, structured.

    `bootstrap_rows` is a list of {"key": label, "obs_cagr": .., "p05": ..}
    for every strategy that cleared MIN_TRADES -- the reason this exists at
    all: counting how many rules "look good" means nothing without knowing
    how many an edgeless menu of the same size would produce by chance.
    """
    rows = [r for r in bootstrap_rows if r.get("p05") is not None]
    if not rows:
        return None
    tried = len(rows)
    cleared = [r for r in rows if r["p05"] > 0]
    expected = tried * 0.05
    spread = float(np.median([r["p95"] - r["p05"] for r in rows
                              if r.get("p95") is not None])) / 3.29
    hurdle = expected_best_of(tried, spread)
    best = max(rows, key=lambda r: r["obs_cagr"])
    return {
        "tried": tried, "cleared": len(cleared), "expected_by_chance": round(expected, 1),
        "hurdle": round(hurdle, 1), "best_cagr": round(best["obs_cagr"], 1),
        "best_key": best["key"], "clears_hurdle": best["obs_cagr"] > hurdle,
        "cleared_keys": [r["key"] for r in cleared],
    }

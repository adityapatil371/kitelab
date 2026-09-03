"""Is the edge real, or is it a pattern found in noise?

    python -m scripts.validate                    # every test, primary variants
    python -m scripts.validate --only walkforward top-n
    python -m scripts.validate --all-variants     # all 24, not one per family
    python -m scripts.validate --permutations 20

DIFFERENT FROM tests/. Those check that the CODE does what it was told, which is
necessary and says nothing about whether a strategy makes money. These are the
checks a trader runs on a strategy before believing it. A rule can pass every
unit test in the project and still be a curve fit.

Six tests, and each answers a question the compare table cannot:

  BENCHMARK      Did the rule beat simply owning the same stocks? The only
                 comparison that matters, and the one missing from the headline
                 table -- a rule can rank first among 24 and still lose to doing
                 nothing.
  WALK-FORWARD   Does it hold in DISJOINT periods? The dashboard's start-year
                 axis is cumulative -- every window ends today, so one strong
                 stretch flatters them all. These windows do not overlap.
  TOP-N          Delete the best few trades. If the edge dies, it was a lottery
                 ticket, not a rule. Aimed squarely at the Turtle, whose return
                 is already known to live in a thin tail.
  BREAKEVEN COST How much friction does the edge survive? A rule that dies at
                 0.3% a side is untradeable whatever its backtest says.
  CORRELATION    Are 24 variants 24 bets, or three? If they take the same
                 trades, the multiple-testing count is overstated and
                 "diversifying across rules" is an illusion.
  PERMUTATION    Run the rule on SHUFFLED returns -- same distribution, no
                 structure. Anything it earns there is fitted noise. The
                 sharpest test here, and the slowest.

Reads the cached trades the dashboard uses, so it describes the rules as
published rather than a re-simulation that might differ.
"""
from __future__ import annotations

import argparse
import statistics

import numpy as np
import pandas as pd

from kitelab import config, frames, portfolio, registry, signals

CAPITAL = 200_000
RISK = 0.01
WINDOW_YEARS = 3
TOP_N = (1, 5, 10, 25)


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

    Equal weight, not cap weight: the strategies size by risk and hold whatever
    signals, so an index's concentration would be comparing two different things.
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

    Preserves each stock's return DISTRIBUTION -- drift, volatility, fat tails --
    and destroys only the sequence. A trend rule has nothing left to find, so
    whatever it still earns there is fitted noise.

    `real_daily` is passed in rather than read from the module, because the
    caller has patched frames.daily to serve these very frames: reading it from
    scope would call this function from inside itself.
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


# ----------------------------------------------------------------- main ----
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None,
                    choices=["benchmark", "walkforward", "top-n", "cost",
                             "correlation", "permutation"])
    ap.add_argument("--all-variants", action="store_true")
    ap.add_argument("--permutations", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20260903)
    args = ap.parse_args()
    want = set(args.only) if args.only else {"benchmark", "walkforward", "top-n",
                                             "cost", "correlation", "permutation"}

    cfg = config.load()
    universe = cfg.merged
    rng = np.random.default_rng(args.seed)

    picked = (registry.REGISTRY if args.all_variants else
              [registry.find(f, registry.primary(f)) for f in registry.families()])
    picked = [s for s in picked if s is not None]

    loaded = {}
    for s in picked:
        t = load(s, universe)
        if t:
            loaded[s.label] = (s, t)
    if not loaded:
        raise SystemExit("\n  No cached trades. Run: python -m scripts.refresh\n")

    print(f"\n  {len(universe)} stocks, Rs{CAPITAL:,} at {RISK:.0%} risk. "
          f"{len(loaded)} rules.\n")

    # ---- benchmark -------------------------------------------------------
    if "benchmark" in want:
        bh = buy_and_hold(universe)
        print("  DID IT BEAT SIMPLY OWNING THE STOCKS?")
        print(f"    equal-weight buy and hold, median stock: {bh:.1f}% a year\n")
        print(f"    {'rule':<34}{'CAGR':>9}{'vs hold':>10}")
        print("    " + "-" * 51)
        beat = 0
        for label, (s, t) in loaded.items():
            got = cagr_of(t)
            if got is None:
                continue
            beat += got > bh
            print(f"    {label:<34}{got:>8.1f}%{got - bh:>9.1f}")
        print(f"\n    {beat} of {len(loaded)} beat doing nothing.\n")

    # ---- walk-forward ----------------------------------------------------
    if "walkforward" in want:
        print(f"  DOES IT HOLD IN DISJOINT {WINDOW_YEARS}-YEAR WINDOWS?")
        print("    (the dashboard's start-year axis is cumulative; these do not overlap)\n")
        for label, (s, t) in loaded.items():
            rows = walk_forward(t)
            if not rows:
                continue
            vals = [c for _, _, c in rows if c is not None]
            pos = sum(1 for c in vals if c > 0)
            spans = "  ".join(f"{a}-{b}:{c:>6.1f}" if c is not None else f"{a}-{b}:    --"
                              for a, b, c in rows)
            print(f"    {label}")
            print(f"      {spans}")
            print(f"      positive in {pos}/{len(vals)} windows\n")

    # ---- top-N -----------------------------------------------------------
    if "top-n" in want:
        print("  HOW MUCH RIDES ON THE BEST FEW TRADES?")
        head = f"    {'rule':<34}{'all':>8}" + "".join(f"{'-' + str(n):>8}" for n in TOP_N)
        print(head); print("    " + "-" * (len(head) - 4))
        for label, (s, t) in loaded.items():
            base = cagr_of(t)
            cells = "".join(
                f"{(without_best(t, n) or float('nan')):>8.1f}" for n in TOP_N)
            print(f"    {label:<34}{(base or float('nan')):>8.1f}{cells}")
        print("    columns: CAGR with the best 1, 5, 10, 25 trades deleted\n")

    # ---- breakeven cost --------------------------------------------------
    if "cost" in want:
        print("  HOW MUCH FRICTION DOES THE EDGE SURVIVE?")
        print(f"    {'rule':<34}{'breakeven':>12}")
        print("    " + "-" * 46)
        for label, (s, t) in loaded.items():
            got = breakeven_cost(t)
            text = ("already negative" if got is None else
                    "survives 200bp+" if got == float("inf") else f"{got:.1f} bp/side")
            print(f"    {label:<34}{text:>12}")
        print("    Zerodha delivery plus a modelled spread is roughly 15-40 bp a side.\n")

    # ---- correlation -----------------------------------------------------
    if "correlation" in want and len(loaded) > 1:
        print("  ARE THESE DIFFERENT BETS, OR THE SAME ONE?")
        labels = list(loaded)
        months = {k: monthly_returns(v[1]) for k, v in loaded.items()}
        print(f"    {'':<20}" + "".join(f"{l.split(chr(183))[0][:7]:>8}" for l in labels))
        for a in labels:
            cells = ""
            for b in labels:
                c = 1.0 if a == b else correlate(months[a], months[b])
                cells += f"{c:>8.2f}" if c is not None else f"{'--':>8}"
            print(f"    {a[:20]:<20}{cells}")
        pairs = [correlate(months[a], months[b])
                 for i, a in enumerate(labels) for b in labels[i + 1:]]
        pairs = [p for p in pairs if p is not None]
        if pairs:
            print(f"\n    median pairwise correlation {statistics.median(pairs):.2f} "
                  f"-- above ~0.7 these are one bet wearing several names.\n")

    # ---- permutation -----------------------------------------------------
    if "permutation" in want:
        print("  DOES IT STILL WORK WHEN THE STRUCTURE IS REMOVED?")
        print("    Each stock's daily returns shuffled -- same distribution, no")
        print(f"    sequence. {args.permutations} rounds on a 60-stock sample.\n")
        print(f"    {'rule':<34}{'real':>8}{'shuffled median':>17}{'beat by':>9}")
        print("    " + "-" * 68)
        for label, (s, t) in loaded.items():
            observed, got = permutation_test(s, universe, args.permutations, rng)
            if observed is None or not got:
                print(f"    {label:<34}{'--':>8}")
                continue
            med = statistics.median(got)
            worse = sum(1 for g in got if g >= observed)
            flag = "" if worse * 4 <= len(got) else "   <-- NOT DISTINGUISHABLE"
            print(f"    {label:<34}{observed:>7.1f}%{med:>16.1f}%"
                  f"{observed - med:>8.1f}{flag}")
        print("\n    A rule that earns as much on shuffled prices as on real ones")
        print("    has found no structure -- it is fitting noise.\n")


if __name__ == "__main__":
    main()

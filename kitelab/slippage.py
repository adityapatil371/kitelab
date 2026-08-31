"""What a fill really costs, per stock, per day.

Every result in this lab before 2026-08-31 assumed a perfect fill: you always got
the exact historical price, instantly, at any size. That is the single largest
unmodelled factor in the project (the tornado study put a flat 0.2%/side at 4.8
CAGR points, more than brokerage). This module replaces the flat guess.

Three separate costs, and it matters which are MEASURED and which are ASSUMED:

  1. half-spread   ASSUMED.  We have no quote data -- Kite's historical API serves
                   candles, not the order book -- so the bid-ask cannot be measured
                   from what we hold. It is modelled as a ladder that falls with
                   liquidity and is floored by the tick size (below).
  2. market impact MEASURED inputs, assumed shape. Your own order pushes the price.
                   Order value and the stock's traded value are both in our data;
                   the square-root shape and the coefficient are the assumption.
  3. fill timing   MEASURED. Deciding at a close you cannot trade at is a real cost
                   and our data prices it exactly -- see backtest.NEXT_OPEN_FILLS.
                   Not handled here; it is a fill convention, not a spread.

Why not Corwin-Schultz. The standard high-low spread estimator was tried first and
rejected on 2026-08-31: across our 164 stocks with usable history it returned a
median half-spread of 54.7 bps and put HAL -- Rs146 crore of daily turnover -- at
35.1 bps, roughly fifteen times any plausible quoted spread for a stock that size.
It also separated the most and least liquid names by only 3x when reality is nearer
50x, and 36% of its day-pairs came out negative. The estimator is dominated by
volatility, not spread, on this data. Reproduce with scripts/slippage_report.py
--audit.

Everything here is OFF by default. With ENABLED = False the fill price is returned
untouched, so every number produced before this module still reproduces exactly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import frames

# Master switch. False = perfect fills, i.e. every pre-2026-08-31 result.
ENABLED = False

# NSE tick for most equities. A marketable order can never do better than crossing
# half a tick, whatever the liquidity, so this is the floor on the half-spread and
# it is the one part of the spread model that is not a guess. Cheap stocks live on
# this floor: half a tick on a Rs15 share is 17 bps, on a Rs4,900 share it is 0.05.
TICK = 0.05

# Half-spread ladder: SPREAD_K basis points at Rs1 crore of daily traded value,
# falling as 1/sqrt(turnover). Calibrated so that a Rs100+ crore/day large cap lands
# near 2-3 bps, which is the order of magnitude quoted on liquid NSE names. It is a
# calibration, not a measurement -- scripts/slippage_report.py sweeps it so you can
# see how much the answer depends on it.
SPREAD_K = 30.0
MAX_HALF_SPREAD = 0.02      # 200 bps; a hard stop on the ladder for near-dead stocks

# Market impact, Almgren-style square root law: C * daily_vol * sqrt(order / ADV).
# Small orders barely move a liquid stock; the same order is violent in a thin one.
IMPACT_C = 0.5

# The most of a stock's daily traded value one account may take in a single order.
# None = no limit, which is what every earlier result assumed and which let the
# reference account put 37x a stock's ENTIRE daily turnover into one position.
MAX_PARTICIPATION: float | None = None

ADV_WINDOW = 60             # sessions of trailing turnover behind the liquidity read
VOL_WINDOW = 60             # sessions behind the volatility read
DEFAULT_VOL = 0.02          # a stock's first sessions, before any return exists

_cache: dict[str, dict] = {}


def reset() -> None:
    """Drop the per-symbol profiles (call after changing TICK/SPREAD_K/etc)."""
    _cache.clear()
    _liquidity_memo.clear()


def profile(symbol: str) -> dict:
    """Trailing liquidity and volatility for one stock, strictly point-in-time.

    Both series are shifted one session, so a trade on day D is priced using only
    what was known by the close of D-1. Without the shift the model would quietly
    read the future to decide what the past cost.
    """
    hit = _cache.get(symbol)
    if hit is not None:
        return hit
    day = frames.daily(symbol)
    close = day["close"].astype(float)
    turnover = close * day["volume"].astype(float)
    adv = turnover.rolling(ADV_WINDOW, min_periods=5).median().shift(1)
    adv = adv.fillna(turnover.expanding(min_periods=1).median().shift(1))
    returns = close.pct_change()
    vol = returns.rolling(VOL_WINDOW, min_periods=5).std().shift(1)
    # Point-in-time fallback for the first sessions, mirroring the ADV line above:
    # the standard deviation of what had happened SO FAR, shifted one session.
    # This used to be vol.median() over the ENTIRE series, so a stock's first weeks
    # were priced with knowledge of its whole future volatility -- lookahead, in
    # the one module whose job is honesty about what trading costs.
    vol = vol.fillna(returns.expanding(min_periods=2).std().shift(1))
    vol = vol.fillna(DEFAULT_VOL)      # nothing has happened yet at all
    built = {
        "ts": day["ts"].to_numpy().astype("datetime64[ns]"),
        "adv": adv.to_numpy(dtype=float),
        "vol": vol.to_numpy(dtype=float),
    }
    _cache[symbol] = built
    return built


def _row(symbol: str, stamp) -> tuple[float, float]:
    """(ADV in rupees, daily volatility) as known on the session of `stamp`."""
    p = profile(symbol)
    i = int(np.searchsorted(p["ts"], np.datetime64(pd.Timestamp(stamp).normalize(), "ns"),
                            side="right")) - 1
    if i < 0:
        return float("nan"), float("nan")
    return float(p["adv"][i]), float(p["vol"][i])


def half_spread(symbol: str, stamp, price: float) -> float:
    """Half the bid-ask, as a fraction of price. ASSUMED -- see the module note."""
    adv, _ = _row(symbol, stamp)
    floor = (TICK / 2.0) / price if price > 0 else 0.0
    if not np.isfinite(adv) or adv <= 0:
        return min(MAX_HALF_SPREAD, max(floor, MAX_HALF_SPREAD))
    ladder = (SPREAD_K / np.sqrt(adv / 1e7)) / 10_000.0
    return float(min(MAX_HALF_SPREAD, max(floor, ladder)))


def impact(symbol: str, stamp, order_value: float) -> float:
    """Your own footprint, as a fraction of price. Square-root law."""
    adv, vol = _row(symbol, stamp)
    if not np.isfinite(adv) or adv <= 0 or order_value <= 0:
        return 0.0
    if not np.isfinite(vol) or vol <= 0:
        vol = 0.02
    return float(IMPACT_C * vol * np.sqrt(order_value / adv))


def fill(symbol: str, stamp, price: float, side: int) -> float:
    """The price you get after crossing the spread. side=+1 buying, -1 selling.

    SPREAD ONLY. Impact is deliberately not applied here: crossing the spread costs
    the same whatever you trade, but impact depends on how big your order is, and at
    this level the order has been sized off a fixed Rs1,00,000 book. The account
    engine is the only place that knows the real position size, so it owns impact --
    see portfolio.run. Applying it here would price a Rs36,000 order as if it were
    the Rs2,000 one the trade-level sizer happened to produce.

    With ENABLED = False this returns `price` unchanged, byte for byte, which is
    what keeps every earlier result reproducible.
    """
    if not ENABLED:
        return price
    return price * (1.0 + side * half_spread(symbol, stamp, price))


_liquidity_memo: dict = {}


def liquidity_at(symbol: str, stamp) -> float:
    """Trailing traded value for a stock on a given session, memoised.

    Used to break ties between signals that arrive on the same day. It reads the
    same shifted ADV series as the cost model, so it knows only what was on the
    tape by the previous close -- ranking today's candidates by today's turnover
    would be lookahead.

    Returns 0.0 where there is no usable history, which sorts such a name last.
    """
    key = (symbol, stamp)
    hit = _liquidity_memo.get(key)
    if hit is None:
        adv, _vol = _row(symbol, stamp)
        hit = float(adv) if np.isfinite(adv) else 0.0
        _liquidity_memo[key] = hit
    return hit


def apply_spread(trade: dict) -> dict:
    """Charge the spread on an ALREADY-simulated trade, without re-simulating it.

    This is exact, not an approximation, and the reason is worth stating: nothing
    in the simulation depends on the fill price. Position size comes from the
    QUOTED price (you place the order before you know the fill), and every exit
    decision -- stop, EMA break, target -- is read off the bars. So the spread
    changes what a trade EARNED but never which trades happen or when. Verified
    on 2026-08-31 against re-simulation: 1,806 trades over 25 symbols, zero
    field mismatches.

    That is what makes a slippage toggle affordable in the dashboard: the cached
    signal lists can be reused instead of rebuilding every strategy from bars.

    Requires ENABLED = True to do anything, like every other entry point here.
    """
    from .backtest import charges          # deferred: backtest imports this module

    out = dict(trade)
    quoted_entry, quoted_exit = trade["entry_price"], trade["exit_price"]
    shares = trade["shares"]

    # This function re-prices a trade as ONE buy and ONE sell. A scale-out has two
    # sells -- half banked at nR, the rest at the exit -- and the banked leg is not
    # in the trade record at all, so (exit - entry) x shares is simply the wrong
    # gross and the charges would be billed on the wrong sell value. Nothing calls
    # it that way today; refuse rather than let a future caller get silent nonsense.
    # The test is structural, not a name check: every honest producer sets
    # gross_profit to exactly (exit - entry) x shares (verified: 363,574 trades
    # across all 44 cached lists, worst relative gap 0.0).
    own_gross = trade.get("gross_profit")
    naive_gross = (quoted_exit - quoted_entry) * shares
    banked = "banked" in str(trade.get("exit_reason", ""))
    mismatch = (own_gross is not None
                and abs(naive_gross - own_gross) > 1e-9 * max(1.0, abs(own_gross)))
    if banked or mismatch:
        raise ValueError(
            f"apply_spread cannot price {trade.get('symbol')} {trade.get('entry_ts')}: "
            f"exit_reason {trade.get('exit_reason')!r}"
            + (f", and its gross_profit ({own_gross:,.2f}) is not (exit - entry) x "
               f"shares ({naive_gross:,.2f})" if mismatch else "")
            + ". This is a multi-leg trade and this function prices one buy against "
              "one sell. Scale-out lists must be spread-adjusted at simulation time, "
              "not here.")

    entry = fill(trade["symbol"], trade["entry_ts"], quoted_entry, +1)
    exit_ = fill(trade["symbol"], trade["exit_ts"], quoted_exit, -1)
    gross = (exit_ - entry) * shares
    # One fee convention (see backtest.simulate): same-session trades are billed
    # intraday. This used to re-bill an already-intraday W/D/H trade at DELIVERY
    # rates, so applying the spread quietly added Rs291,639 of pure convention
    # change to the realistic W/D/H trade list on top of the actual spread cost.
    cost = cost_best = charges(entry * shares, exit_ * shares,
                               trade.get("same_session", False))
    risk_taken = trade.get("risk_taken") or 0.0
    out.update(
        quoted_entry=quoted_entry, quoted_exit=quoted_exit,
        entry_price=entry, exit_price=exit_,
        cost_of_entry=entry * shares, gross_profit=gross,
        spread_cost=(quoted_exit - exit_) * shares + (entry - quoted_entry) * shares,
        charges=cost, charges_best=cost_best,
        net_profit=gross - cost, net_profit_best=gross - cost_best,
        r_multiple=(gross / risk_taken) if risk_taken else 0.0,
    )
    return out


def capped_shares(symbol: str, stamp, price: float, shares: float) -> float:
    """Trim an order down to what the stock can actually absorb in one session."""
    if not ENABLED or MAX_PARTICIPATION is None or price <= 0:
        return shares
    adv, _ = _row(symbol, stamp)
    if not np.isfinite(adv) or adv <= 0:
        return shares
    return min(shares, (MAX_PARTICIPATION * adv) / price)

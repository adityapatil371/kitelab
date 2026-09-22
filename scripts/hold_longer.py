"""Does holding the SAME entries longer survive real fills? The one lead left.

    python3 -m scripts.hold_longer

scripts/net_in_market.py (2026-09-16) charged the real half-spread against every
board rule's own exit and found the table ordered by HOLDING PERIOD, not by
signal quality: gross return while invested is flat (17-29%/yr) across all nine
rules, but every rule holding 19+ sessions survived the spread and every rule
holding ~8 sessions died. It could not say whether that is causal, because the
slow rules and the fast rules are different rules.

This holds one variable still. Take each rule's OWN entry stamps, throw away its
exit, and sell at a fixed horizon instead. Same entries, same stocks, same dates
-- only the holding period moves. If the ordering in net_in_market was really
about turnover, every rule's curve should rise with the horizon; if it was about
signal quality, the fast rules should stay bad however long they are held.

WHAT A FIXED HORIZON DELETES, and it is not small: THE STOP. Every rule here
exits on its own stop (the entry candle's low, uniformly -- see CLAUDE.md), and
a fixed-horizon exit ignores it. So these columns are not a strategy anyone could
run; they are a probe of one variable. A real "hold it longer" rule would keep a
stop and the stop would end some trades early, which is the NEXT question, not
this one.

THE OTHER STANDING CAVEAT, inherited from entry_edge and applied to every column
alike: every rate here is PER SESSION IN THE MARKET, which assumes the next trade
starts the day this one ends. No rule does that, and a 250-session hold could not
-- one pot of money cannot hold 250-session positions at the rate a 3-session
rule opens them. Capital binds harder the longer you hold, and that cost lands on
BREADTH, which this script does not measure. Read the SHAPE and the CROSSOVER,
never a level, and never the argmax of 90 numbers.

The cost ladder is net_in_market's, unchanged, and computed here the vectorised
way: the spread model's inputs (trailing ADV, trailing volatility) are per-symbol
series, so they are reindexed onto the shared session grid ONCE and the ladder
becomes arithmetic. That is what makes ten horizons cost about what one did. The
arithmetic is asserted identical to slippage.half_spread / impact / capped_shares
on a random sample before anything is reported.

Reads:  every registered rule's *_all.pkl signal cache, the daily parquet
        candles, and output/measurements/net_in_market_<N>strat_*.csv (the
        self-check reference -- the rule's own exit must reproduce it).
Writes: output/measurements/hold_longer_<N>strat_<date>.csv
        output/figures/hold_longer_curve.png
"""
from __future__ import annotations

import csv
import glob
import math
import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")            # no display in the container; PNG only
import matplotlib.pyplot as plt  # noqa: E402

from kitelab import registry, signals, slippage, sizing, config  # noqa: E402
from scripts.entry_edge import (close_matrix, hold_index,  # noqa: E402
                                N_RULES, STAMP, TOLL, YEAR)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M = os.path.join(REPO, "output", "measurements")
CSV_PATH = os.path.join(M, f"hold_longer_{STAMP}.csv")
FIG = os.path.join(REPO, "output", "figures")   # every PNG
os.makedirs(FIG, exist_ok=True)
CURVE = os.path.join(FIG, "hold_longer_curve.png")

HOLDS = (3, 5, 10, 20, 40, 60, 90, 120, 180, 250)
BOOKS = (200_000, 10_000_000)    # dashboard_data.CAPITALS -- the board's own two
SELF_CHECK_TOL = 0.5             # CAGR points, same tolerance net_in_market used
HOLD_PCT_YR = 14.09              # entry_edge's equal-weight hold, printed to confirm

# The cost model is switched on for this whole run, exactly as wf_attach does it.
slippage.ENABLED = True
slippage.MAX_PARTICIPATION = 0.01


def newest(prefix):
    """Newest CSV for THIS board size.

    Anchored on "<N>strat_", never the bare prefix: on 2026-09-16 a plain
    `exposure_*` sort picked up a brand-new exposure_reconcile_*.csv and a
    sibling script silently self-checked against the wrong table.
    """
    pat = os.path.join(M, f"{prefix}{N_RULES}strat_*.csv")
    hits = sorted(glob.glob(pat))
    if not hits:
        raise SystemExit(f"no {os.path.basename(pat)} in {M} -- run "
                         "scripts.net_in_market to write one for this board")
    print(f"  self-check reference: {os.path.basename(hits[-1])}")
    return hits[-1]


# ------------------------------------------------------- the cost matrices ----
def cost_matrices(index, symbols):
    """(adv, vol) on the shared session grid, one row per session, one col per stock.

    slippage._row looks up a symbol's own shifted ADV/vol series with
    searchsorted(side="right") - 1, i.e. the last value at or before the stamp.
    Reindexing that series onto the master index with method="ffill" is the same
    operation done once for every session instead of once per trade. Sessions
    before the stock's first bar stay NaN, which is what _row returns there too.
    """
    adv = np.full((len(index), len(symbols)), np.nan)
    vol = np.full((len(index), len(symbols)), np.nan)
    for j, sym in enumerate(symbols):
        p = slippage.profile(sym)
        ts = pd.DatetimeIndex(p["ts"]).normalize()
        a = pd.Series(p["adv"], index=ts)
        v = pd.Series(p["vol"], index=ts)
        a = a[~a.index.duplicated(keep="last")].reindex(index, method="ffill")
        v = v[~v.index.duplicated(keep="last")].reindex(index, method="ffill")
        adv[:, j] = a.to_numpy(float)
        vol[:, j] = v.to_numpy(float)
    finite = np.isfinite(adv)
    print(f"  cost matrices: {adv.shape[0]} sessions x {adv.shape[1]} symbols, "
          f"{100.0 * finite.mean():.1f}% of cells carry a trailing ADV")
    return adv, vol


def half_spread_v(adv, price):
    """slippage.half_spread, vectorised. Fraction of price, per element."""
    floor = np.where(price > 0, (slippage.TICK / 2.0) / np.maximum(price, 1e-12), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ladder = (slippage.SPREAD_K / np.sqrt(adv / 1e7)) / 10_000.0
    hs = np.minimum(slippage.MAX_HALF_SPREAD, np.maximum(floor, ladder))
    # No usable ADV -> the model's own worst case, matching half_spread's branch.
    return np.where(np.isfinite(adv) & (adv > 0), hs, slippage.MAX_HALF_SPREAD)


def capped_v(adv, price, shares):
    """slippage.capped_shares, vectorised."""
    room = np.where(np.isfinite(adv) & (adv > 0) & (price > 0),
                    (slippage.MAX_PARTICIPATION * adv) / np.maximum(price, 1e-12),
                    np.inf)
    return np.minimum(shares, room)


def impact_v(adv, vol, order_value):
    """slippage.impact, vectorised. Fraction of price, per element."""
    v = np.where(np.isfinite(vol) & (vol > 0), vol, 0.02)
    with np.errstate(divide="ignore", invalid="ignore"):
        imp = slippage.IMPACT_C * v * np.sqrt(order_value / adv)
    ok = np.isfinite(adv) & (adv > 0) & (order_value > 0)
    return np.where(ok, np.nan_to_num(imp), 0.0)


def assert_matches_module(index, symbols, filled, adv, vol, n=400, seed=7):
    """The vectorised ladder must equal slippage's own, element for element.

    This is the whole licence for the matrices above. Checked on a random sample
    of live (session, stock) cells rather than asserted in a comment.
    """
    rng = np.random.default_rng(seed)
    live = np.flatnonzero(np.isfinite(adv).ravel())
    pick = rng.choice(live, size=min(n, live.size), replace=False)
    r, c = np.unravel_index(pick, adv.shape)
    price = filled[r, c]
    ok = np.isfinite(price) & (price > 0)
    r, c, price = r[ok], c[ok], price[ok]
    mine_hs = half_spread_v(adv[r, c], price)
    mine_cap = capped_v(adv[r, c], price, np.full(price.shape, 1e6))
    mine_imp = impact_v(adv[r, c], vol[r, c], price * 100.0)
    worst = [0.0, 0.0, 0.0]
    for k in range(len(r)):
        sym, stamp, p = symbols[c[k]], index[r[k]], float(price[k])
        worst[0] = max(worst[0], abs(slippage.half_spread(sym, stamp, p) - mine_hs[k]))
        worst[1] = max(worst[1], abs(slippage.capped_shares(sym, stamp, p, 1e6)
                                     - mine_cap[k]))
        worst[2] = max(worst[2], abs(slippage.impact(sym, stamp, p * 100.0)
                                     - mine_imp[k]))
    print(f"  vectorised vs slippage.py on {len(r)} random cells: "
          f"half_spread {worst[0]:.2e}, capped_shares {worst[1]:.2e}, "
          f"impact {worst[2]:.2e}")
    if max(worst) > 1e-9:
        raise SystemExit("  the vectorised cost ladder does NOT match slippage.py "
                         "-- stop; every number below would be wrong")


# ------------------------------------------------------------------ loader ----
def load(universe, index, col_of):
    """(rule -> entry row, col, exit row, shares) for every cached trade.

    entry_edge.load_entries is not reused because it drops `shares`, and the
    impact leg needs the order size the producer actually sized. Sizing is
    ENTRY-side (risk per trade off the entry candle's low), so the share count is
    the same whatever exit is imposed -- which is exactly why a fixed horizon can
    be repriced from the cache without re-simulating anything.
    """
    sessions = {ts: i for i, ts in enumerate(index)}
    out = {}
    cached = kept = 0
    for strat in registry.REGISTRY:
        trades = signals.load(f"{strat.cache}_all", universe)
        if not trades:
            print(f"  {strat.label}: no cache -- run scripts.refresh first")
            continue
        r, c, e, sh = [], [], [], []
        for t in trades:
            j = col_of.get(t["symbol"])
            i = sessions.get(pd.Timestamp(t["entry_ts"]).normalize())
            if j is None or i is None:
                continue
            x = sessions.get(pd.Timestamp(t["exit_ts"]).normalize())
            r.append(i); c.append(j); e.append(-1 if x is None else x)
            sh.append(float(t["shares"]))
        cached += len(trades); kept += len(r)
        print(f"  {strat.label:<38} {len(trades):>8,} cached -> {len(r):>8,} "
              f"matched to a session ({100.0 * len(r) / len(trades):.1f}%)")
        out[strat] = (np.array(r), np.array(c), np.array(e), np.array(sh))
    print(f"\n  {cached:,} cached trades -> {kept:,} usable ({100.0 * kept / cached:.2f}%)")
    if kept < 0.9 * cached:
        raise SystemExit("more than 10% of entries failed to match a session -- "
                         "the entry stamp is not the decision date, stop here")
    return out


def rate(logret, sessions):
    """Annualised compound return WHILE INVESTED. entry_edge.rate_from_log."""
    if sessions <= 0 or logret.size == 0:
        return float("nan")
    return 100.0 * math.expm1(YEAR * float(np.mean(logret)) / sessions)


def leg_prices(filled, adv, vol, r, c, end, shares, book):
    """Entry and exit prices after the spread, and after impact at `book`.

    Returns (buy_spread, sell_spread, buy_impacted, sell_impacted). The cap is
    applied to the share count first, at the ENTRY session, exactly as
    portfolio.run does -- a position that could not be bought cannot be sold.
    """
    pe, px = filled[r, c], filled[end, c]
    ae, ax = adv[r, c], adv[end, c]
    buy = pe * (1.0 + half_spread_v(ae, pe))
    sell = px * (1.0 - half_spread_v(ax, px))
    if book is None:
        return buy, sell, buy, sell
    want = shares * (book / sizing.CAPITAL)
    got = capped_v(ae, pe, want)
    ie = impact_v(ae, vol[r, c], pe * got)
    ix = impact_v(ax, vol[end, c], px * got)
    return buy, sell, buy * (1.0 + ie), sell * (1.0 - ix)


def main() -> None:
    cfg = config.load()
    universe = cfg.merged
    print(f"\n  {len(universe)} stocks. Spread ON, 1% participation cap ON.\n")

    index, symbols, filled, traded, last_row = close_matrix(universe)
    col_of = {s: j for j, s in enumerate(symbols)}
    print()
    adv, vol = cost_matrices(index, symbols)
    assert_matches_module(index, symbols, filled, adv, vol)
    print()
    entries = load(universe, index, col_of)

    hold = hold_index(filled, traded)
    yrs = len(index) / YEAR
    mkt = 100.0 * ((hold[-1] / hold[0]) ** (1 / yrs) - 1)
    print(f"\n  equal-weight buy-and-hold: {mkt:.2f}% a year over {yrs:.1f} years "
          f"(entry_edge published {HOLD_PCT_YR:.2f})\n")

    ref = {}
    with open(newest("net_in_market_")) as fh:
        for row in csv.DictReader(fh):
            ref[row["rule"]] = float(row["spread_pct_yr"])

    rows, curves, owns, bad = [], {}, {}, []   # curves: label -> (spread, per-book, own, own per-book)
    for strat, (r, c, e, sh) in entries.items():
        if r.size == 0:
            continue
        line = {"rule": strat.label, "trades": int(r.size)}

        # --- the rule's own exit, charged the same way: the self-check ---------
        # Computed at EVERY cost level, because the fixed-horizon columns are.
        # An earlier draft priced `own` spread-only and reprinted it beside the
        # impacted columns, which silently compared a cheap exit against dear
        # ones and left the "beats hold" count at 3 in all three tables.
        ok = e >= 0
        s_own = float(np.mean(np.maximum(e[ok] - r[ok], 1)))
        b, s, _, _ = leg_prices(filled, adv, vol, r[ok], c[ok], e[ok], sh[ok], None)
        own = rate(np.log1p(np.clip(s / b * (1.0 - TOLL) - 1.0, -0.999, None)), s_own)
        owns[strat.label] = own
        own_book = {}
        for bk in BOOKS:
            _, _, bi, si = leg_prices(filled, adv, vol, r[ok], c[ok], e[ok],
                                      sh[ok], bk)
            own_book[bk] = rate(
                np.log1p(np.clip(si / bi * (1.0 - TOLL) - 1.0, -0.999, None)), s_own)
            line[f"own_exit_book_{bk}_pct_yr"] = round(own_book[bk], 3)
        want = ref.get(strat.label)
        if want is None:
            bad.append(f"{strat.label}: absent from the reference CSV")
        elif abs(own - want) > SELF_CHECK_TOL:
            bad.append(f"{strat.label}: own exit {own:.2f} vs net_in_market {want:.2f}")
        line["mean_sessions_own"] = round(s_own, 2)
        line["own_exit_spread_pct_yr"] = round(own, 3)

        # --- the same entries, sold at a fixed horizon -------------------------
        vals, per_book = [], {bk: [] for bk in BOOKS}
        for h in HOLDS:
            end = np.minimum(r + h, last_row[c])
            sess = np.maximum(end - r, 1)
            s_mean = float(np.mean(sess))
            b, s, _, _ = leg_prices(filled, adv, vol, r, c, end, sh, None)
            g = np.log1p(np.clip(s / b * (1.0 - TOLL) - 1.0, -0.999, None))
            v = rate(g, s_mean)
            vals.append(v)
            line[f"hold_{h}_sessions"] = round(s_mean, 2)
            line[f"hold_{h}_spread_pct_yr"] = round(v, 3)
            for bk in BOOKS:
                _, _, bi, si = leg_prices(filled, adv, vol, r, c, end, sh, bk)
                gi = np.log1p(np.clip(si / bi * (1.0 - TOLL) - 1.0, -0.999, None))
                vi = rate(gi, s_mean)
                per_book[bk].append(vi)
                line[f"hold_{h}_book_{bk}_pct_yr"] = round(vi, 3)
        curves[strat.label] = (vals, per_book, own, own_book)
        rows.append(line)

    if bad:
        raise SystemExit("  SELF-CHECK FAILED -- the own-exit column does not "
                         "reproduce net_in_market:\n    " + "\n    ".join(bad))
    print(f"  SELF-CHECK PASS: all {len(rows)} own-exit rates reproduce "
          f"net_in_market within {SELF_CHECK_TOL} CAGR points "
          f"(worst {max(abs(owns[k] - ref[k]) for k in owns):.3f})\n")

    order = sorted(rows, key=lambda x: x["mean_sessions_own"])

    def table(title, pick, own_at, note):
        """`pick` selects the horizon columns, `own_at` the own-exit column --
        both at the SAME cost level, so the row compares like with like."""
        hdr = ("  {:<34}{:>6}{:>7}".format("rule", "sess", "own")
               + "".join(f"{h:>7}" for h in HOLDS))
        print(f"\n  {title}\n  {note}\n")
        print(hdr); print("  " + "-" * (len(hdr) - 2))
        grid, own_col = [], []
        for line in order:
            cv = curves[line["rule"]]
            v, o = pick(cv), own_at(cv)
            grid.append(v); own_col.append(o)
            print(f"  {line['rule']:<34}{line['mean_sessions_own']:>6.1f}{o:>7.1f}"
                  + "".join(f"{x:>7.1f}" for x in v))
        grid, own_col = np.array(grid), np.array(own_col)
        med = np.median(grid, axis=0)
        print("  " + "-" * (len(hdr) - 2))
        print(f"  {'MEDIAN OF THE ' + str(len(grid)) + ' RULES':<34}{'':>6}"
              f"{np.median(own_col):>7.1f}"
              + "".join(f"{x:>7.1f}" for x in med))
        print(f"  {'beats the ' + f'{mkt:.1f}' + ' hold, of ' + str(len(grid)):<34}"
              f"{'':>6}{int((own_col > mkt).sum()):>7}"
              + "".join(f"{int((grid[:, k] > mkt).sum()):>7}" for k in range(len(HOLDS))))
        return grid, med

    g_sp, m_sp = table(
        "PER SESSION IN THE MARKET, %/yr, net of the toll AND the half-spread",
        lambda cv: cv[0], lambda cv: cv[2],
        "Rows sorted by how long the rule holds. 'own' = the rule's own exit.")

    for bk in BOOKS:
        table(f"THE SAME, with market impact at a Rs {bk:,} book "
              f"(1% participation cap applied)",
              lambda cv, bk=bk: cv[1][bk], lambda cv, bk=bk: cv[3][bk],
              "Every column including 'own' is charged at this book. The cap trims "
              "the order first; what it refuses earns nothing, and that cost lands "
              "on breadth, not here.")

    # ------------------------------------------------------------- the read ---
    print("\n  WHAT THE SHAPE SAYS\n")
    rising = 0
    for line in order:
        v = np.array(curves[line["rule"]][0])
        k = int(np.argmax(v))
        better = v.max() - line["own_exit_spread_pct_yr"]
        if better > 0:
            rising += 1
        print(f"  {line['rule']:<34} own {line['own_exit_spread_pct_yr']:>6.1f} at "
              f"{line['mean_sessions_own']:>5.1f} sess  ->  best fixed hold "
              f"{v.max():>6.1f} at {HOLDS[k]:>3} sess  ({better:+.1f})")
    print(f"\n  A fixed horizon beats the rule's own exit for {rising} of "
          f"{len(order)} rules, spread charged.")
    k = int(np.argmax(m_sp))
    print(f"  Best horizon by the MEDIAN rule: {HOLDS[k]} sessions "
          f"({m_sp[k]:.1f}%/yr vs the {mkt:.1f} hold).")
    print("  Read the shape, not the argmax: the best of "
          f"{len(order) * len(HOLDS)} numbers is a lucky number. And every column "
          "here\n  has thrown the stop away -- see the module docstring.\n")

    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    fig, ax = plt.subplots(figsize=(7.8, 4.6))
    for label in curves:
        ax.plot(HOLDS, curves[label][0], color="#ccc", lw=0.8)
    ax.plot(HOLDS, m_sp, marker="o", color="#1f77b4", lw=2,
            label=f"median of the {len(order)} rules, spread charged")
    ax.plot(HOLDS, np.median(np.array([curves[k2][1][10_000_000] for k2 in curves]),
                             axis=0),
            marker="s", color="#9467bd", lw=2,
            label="median, + impact at the Rs 1cr book")
    ax.axhline(np.median([l["own_exit_spread_pct_yr"] for l in order]),
               color="#d62728", ls="--", lw=1.5, label="median rule's OWN exit")
    ax.axhline(mkt, color="#2ca02c", ls=":", lw=1.5, label="equal-weight buy & hold")
    ax.set_xlabel("sessions held before selling (the rule's own entries, its exit discarded)")
    ax.set_ylabel("% a year, per session in the market")
    ax.set_title("Holding the same entries longer, with the real fills charged")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CURVE, dpi=140)
    print(f"  wrote {CSV_PATH}, {CURVE}\n")


if __name__ == "__main__":
    main()

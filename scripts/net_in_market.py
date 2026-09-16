"""What the rules earn per session IN THE MARKET, once the fills are real.

    python -m scripts.net_in_market

WHY THIS EXISTS. `scripts/entry_edge.py` reports a `net_pct_yr_in_market` of
15.5-21.2% against 14.09% for an equal-weight buy-and-hold, which reads as
"the rules beat hold while invested and simply own too little". That column
carries the 0.222% statutory round trip and NOTHING ELSE -- its own output
says so: "Toll is the 0.222% round trip only -- no slippage, no 1% fill cap."
The board is net of both. The 2026-09-16 reconciliation
(`scripts/exposure_reconcile.py`) put the median gap between that column and
the board's own CAGR at +13.44 points, which is the size of the entire puzzle,
so the comparison to hold was never admissible. This script closes that hole.

FOUR COST LEVELS, each adding one thing to the one above:

  1. gross            exit/entry, no costs at all.
  2. + statutory      the 0.222% round trip. THIS REPRODUCES entry_edge's
                      published column, and the self-check below refuses to
                      continue if it does not.
  3. + half-spread    kitelab.slippage.fill on both legs. EXACT and
                      account-free: the spread changes what a trade earned but
                      never which trades happen (see slippage.apply_spread),
                      and it depends only on the stock's turnover, not on
                      order size.
  4. + impact + cap   kitelab.slippage.impact on both legs, on an order first
                      trimmed by slippage.capped_shares at MAX_PARTICIPATION =
                      1%. Both need a position size, so level 4 is reported
                      ACROSS BOOK SIZES rather than as one number.

WHY LEVEL 4 IS A CURVE AND NOT A NUMBER. Impact is C * vol * sqrt(order/ADV):
it is a fact about how big you are, not about the rule. The cached lists are
sized off `sizing.CAPITAL` (Rs1 crore) at 1% risk, so a book of B rupees is
modelled by scaling every order by B/CAPITAL -- exact, because risk-based
sizing is linear in the book. A compounded account is simply a bigger book,
which is why the sweep runs past the board's Rs1 crore.

THE CAP CUTS THE OTHER WAY, AND THE TABLE SAYS SO. Trimming an order to 1% of
turnover makes the remaining shares CHEAPER per rupee, so on a rate basis the
cap flatters the rule -- CLAUDE.md: "MAX_PARTICIPATION is a sizing rule, not a
cost -- on its own it beats perfect fills". Its real price is that the money
never gets deployed, which lands on breadth and not on this rate. The
`cap binds` and `size cut` columns are that cost, and they must be read
alongside the rate or level 4 will be misread as good news.

THE BENCHMARK IS NOT COST-ADJUSTED, ON PURPOSE. Buy-and-hold turns over once in
~20 years: one round trip is 0.222% spread over two decades, about 0.01 points
a year. The rules turn over every 8-48 sessions. That asymmetry IS the finding,
so charging hold a rounding error would only obscure it.

Reads:  every registered rule's *_all.pkl signal cache (no price matrix, no
        account, no rebuild); output/measurements/exposure_*.csv (newest) for
        the published level-2 figures the self-check is run against.
Writes: output/measurements/net_in_market_<N>strat_<date>.csv
        output/measurements/net_in_market_<N>strat_<date>.png
"""
from __future__ import annotations

import csv
import datetime
import glob
import math
import os
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")            # no display in the container; PNG only
import matplotlib.pyplot as plt  # noqa: E402

from kitelab import config, registry, signals, sizing, slippage  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output")
M = os.path.join(OUT, "measurements")

N_RULES = len(registry.REGISTRY)
STAMP = f"{N_RULES}strat_{datetime.date.today():%Y-%m-%d}"
CSV_PATH = os.path.join(M, f"net_in_market_{STAMP}.csv")
PNG_PATH = os.path.join(M, f"net_in_market_{STAMP}.png")

TOLL = 0.00222        # statutory round trip, same constant entry_edge uses
YEAR = 250.0          # trading sessions in a year, this project's convention

# Equal-weight buy-and-hold over the same price matrix, from the entry_edge run
# that wrote the exposure CSV this script self-checks against. Hardcoded rather
# than recomputed because rebuilding the matrix means ~1,000 parquet reads for
# one number that is already published beside the column being corrected.
HOLD_PCT_YR = 14.09

# Books to price level 4 at. CAPITALS on the board are Rs2 lakh and Rs1 crore;
# the sweep runs past both because an account that compounds is a bigger book
# placing bigger orders against the same turnover.
BOOKS = [200_000, 10_000_000, 50_000_000, 250_000_000,
         1_000_000_000, 5_000_000_000]
SELF_CHECK_TOL = 0.5  # CAGR points; see the note in self_check()


def crore(x):
    return f"{x / 1e7:g}cr" if x >= 1e7 else f"{x / 1e5:g}L"


def newest(prefix):
    """Newest CSV for THIS board size.

    The glob is anchored on `<N>strat_` rather than on the bare prefix: a plain
    `exposure_*` sort put `exposure_reconcile_9strat_...` last on 2026-09-16 and
    this script silently self-checked against the wrong file. Pinning the rule
    count also refuses a table written for a board that has since been cut.
    """
    pat = os.path.join(M, f"{prefix}{N_RULES}strat_*.csv")
    hits = sorted(glob.glob(pat))
    if not hits:
        raise SystemExit(f"no {os.path.basename(pat)} in {M} -- run "
                         "scripts.entry_edge to write one for this board")
    print(f"  self-check reference: {os.path.basename(hits[-1])}")
    return hits[-1]


def sessions_between(symbol, entry_ts, exit_ts):
    """Daily sessions the position was actually open, from the stock's own tape.

    NOT `sessions_held` off the trade: only some producers set it (EMA . M/D
    leaves it None on all 164,918 of its trades), and NOT `bars_held`, which is
    counted in the SIGNAL's timeframe -- 1 there means one day on a daily rule
    and one week on a weekly one. entry_edge sidesteps both by looking up
    positions in a sessions x symbols price matrix; this reads the same dates
    out of `slippage.profile`, which is already built and cached for every
    symbol by the spread model, so the calendar costs nothing extra.

    Per-symbol rather than a market-wide calendar, so a halted stock is credited
    with the sessions it actually traded.
    """
    ts = slippage.profile(symbol)["ts"]
    i = int(np.searchsorted(ts, np.datetime64(entry_ts, "ns"), side="left"))
    j = int(np.searchsorted(ts, np.datetime64(exit_ts, "ns"), side="left"))
    return max(j - i, 1)


def rate(logret, sessions):
    """Annualised compound return while invested, entry_edge's convention."""
    if sessions <= 0 or not len(logret):
        return float("nan")
    return 100.0 * math.expm1(YEAR * float(np.mean(logret)) / sessions)


def self_check(published, measured):
    """Level 2 must reproduce entry_edge's published column, or stop.

    Not exact by construction: entry_edge takes holding periods from positions
    in its price matrix and drops any trade whose exit stamp is not a session
    in it, while this script reads `sessions_held` off the trade and keeps
    every trade. Measured 2026-09-16 that is worth 0.03 sessions and ~0.07
    CAGR points on the largest rule. A tolerance, therefore -- but a tight one,
    because anything larger means the two harnesses are not measuring the same
    trades and every level below inherits the discrepancy.
    """
    bad = []
    for rule, want in published.items():
        got = measured.get(rule)
        if got is None:
            bad.append(f"{rule}: absent from this run")
        elif abs(got - want) > SELF_CHECK_TOL:
            bad.append(f"{rule}: {got:.2f} vs published {want:.2f}")
    if bad:
        raise SystemExit("  SELF-CHECK FAILED -- level 2 does not reproduce "
                         "entry_edge:\n    " + "\n    ".join(bad))
    worst = max(abs(measured[r] - w) for r, w in published.items())
    return f"PASS -- level 2 matches entry_edge on all {len(published)} rules "\
           f"(worst gap {worst:.3f} pts, tolerance {SELF_CHECK_TOL})"


def main() -> None:
    t_start = time.time()
    slippage.ENABLED = True
    slippage.MAX_PARTICIPATION = 0.01
    slippage.reset()
    print(f"\n  slippage ENABLED={slippage.ENABLED}, "
          f"MAX_PARTICIPATION={slippage.MAX_PARTICIPATION}, "
          f"SPREAD_K={slippage.SPREAD_K}, IMPACT_C={slippage.IMPACT_C}")
    print(f"  cached lists are sized off sizing.CAPITAL = Rs{crore(sizing.CAPITAL)} "
          f"at {100 * sizing.RISK_PCT:g}% risk\n")

    published = {}
    with open(newest("exposure_")) as fh:
        for r in csv.DictReader(fh):
            if not r["rule"].startswith("ALL "):
                published[r["rule"]] = float(r["net_pct_yr_in_market"])

    uni = config.load().merged
    print(f"  universe: {len(uni)} symbols\n")

    rows, total = [], 0
    for strat in registry.REGISTRY:
        trades = signals.load(f"{strat.cache}_all", uni)
        if not trades:
            print(f"  {strat.label}: no cache -- run scripts.refresh first")
            continue

        # ---- drop what cannot carry a holding period or a return -----------
        n_cached = len(trades)
        trades = [t for t in trades
                  if float(t.get("entry_price") or 0) > 0
                  and float(t.get("exit_price") or 0) > 0
                  and t.get("entry_ts") is not None
                  and t.get("exit_ts") is not None]
        n = len(trades)
        if n < 0.9 * n_cached:
            raise SystemExit(f"  {strat.label}: only {n:,} of {n_cached:,} "
                             "trades are usable -- stop and look at the cache")
        total += n

        sess = np.array([sessions_between(t["symbol"], t["entry_ts"], t["exit_ts"])
                         for t in trades], dtype=float)
        s_mean = float(np.mean(sess))
        qe = np.array([t["entry_price"] for t in trades], dtype=float)
        qx = np.array([t["exit_price"] for t in trades], dtype=float)
        shares = np.array([t["shares"] for t in trades], dtype=float)

        def lg(entry, exit_, toll):
            g = np.clip(exit_ / entry - 1.0, -0.999, None)
            return np.log1p(g) - toll

        # ---- level 1 and 2 --------------------------------------------------
        l1 = rate(lg(qe, qx, 0.0), s_mean)
        l2 = rate(lg(qe, qx, TOLL), s_mean)

        # ---- level 3: half-spread, exact and account-free -------------------
        se = np.array([slippage.fill(t["symbol"], t["entry_ts"],
                                     t["entry_price"], +1) for t in trades])
        sx = np.array([slippage.fill(t["symbol"], t["exit_ts"],
                                     t["exit_price"], -1) for t in trades])
        l3 = rate(lg(se, sx, TOLL), s_mean)

        # ---- level 4: + impact, on an order trimmed by the 1% cap -----------
        # capped_shares is independent of the book only through `shares`, which
        # scales linearly with it, so the cap must be recomputed per book -- it
        # binds on big orders and not on small ones, which is the whole point.
        row = {"rule": strat.label, "trades": n,
               "mean_sessions_held": round(s_mean, 2),
               "gross_pct_yr": round(l1, 3),
               "statutory_pct_yr": round(l2, 3),
               "spread_pct_yr": round(l3, 3)}
        for book in BOOKS:
            scale = book / sizing.CAPITAL
            want = shares * scale
            got = np.array([slippage.capped_shares(t["symbol"], t["entry_ts"],
                                                   t["entry_price"], w)
                            for t, w in zip(trades, want)])
            ie = np.array([slippage.impact(t["symbol"], t["entry_ts"],
                                           t["entry_price"] * g)
                           for t, g in zip(trades, got)])
            ix = np.array([slippage.impact(t["symbol"], t["exit_ts"],
                                           t["exit_price"] * g)
                           for t, g in zip(trades, got)])
            l4 = rate(lg(se * (1.0 + ie), sx * (1.0 - ix), TOLL), s_mean)
            row[f"net_pct_yr_book_{book}"] = round(l4, 3)
            row[f"cap_binds_pct_book_{book}"] = round(
                100.0 * float(np.mean(got < want - 1e-9)), 2)
            row[f"size_cut_pct_book_{book}"] = round(
                100.0 * (1.0 - float(got.sum() / want.sum())), 2)
        rows.append(row)
        print(f"  {strat.label:<38}{n:>9,} trades  "
              f"({time.time() - t_start:>5.0f}s)")

    if not rows:
        raise SystemExit("  no cached trade lists -- run scripts.refresh first")
    print(f"\n  {total:,} trades priced across {len(rows)} rules "
          f"in {time.time() - t_start:.0f}s")

    print("\n  " + self_check(published,
                              {r["rule"]: r["statutory_pct_yr"] for r in rows}))

    rows.sort(key=lambda r: -r["spread_pct_yr"])

    # ------------------------------------------------- 1. the cost ladder ---
    print("\n\n  1. THE COST LADDER -- annual % earned per session IN THE MARKET\n")
    h = (f"  {'rule':<38}{'sess':>6}{'gross':>8}{'+stat':>8}{'+spread':>9}"
         f"{'vs hold':>9}")
    print(h); print("  " + "-" * (len(h) - 2))
    for r in rows:
        print(f"  {r['rule']:<38}{r['mean_sessions_held']:>6.1f}"
              f"{r['gross_pct_yr']:>8.1f}{r['statutory_pct_yr']:>8.1f}"
              f"{r['spread_pct_yr']:>9.1f}"
              f"{r['spread_pct_yr'] - HOLD_PCT_YR:>9.1f}")
    print("  " + "-" * (len(h) - 2))
    med3 = float(np.median([r["spread_pct_yr"] for r in rows]))
    beat = sum(1 for r in rows if r["spread_pct_yr"] > HOLD_PCT_YR)
    print(f"  buy-and-hold, same units: {HOLD_PCT_YR:.2f}%/yr (turns over once "
          f"in ~20 years,\n  so its own costs are ~0.01 pts/yr -- not adjusted)")
    print(f"  median after the spread: {med3:.2f}%/yr. Rules still above hold: "
          f"{beat} of {len(rows)}.")
    print("  '+spread' is account-free and exact. It is NOT a number any account")
    print("  earns: it still assumes the next trade starts the day this one ends.\n")

    # ------------------------------------------ 2. impact, by account size --
    print("\n  2. THE SAME RATE ONCE YOUR OWN ORDERS MOVE THE PRICE\n")
    h2 = f"  {'rule':<38}" + "".join(f"{crore(b):>11}" for b in BOOKS)
    print(h2); print("  " + "-" * (len(h2) - 2))
    for r in rows:
        print(f"  {r['rule']:<38}"
              + "".join(f"{r[f'net_pct_yr_book_{b}']:>11.1f}" for b in BOOKS))
    print("  " + "-" * (len(h2) - 2))
    print(f"  {'MEDIAN':<38}"
          + "".join(f"{np.median([r[f'net_pct_yr_book_{b}'] for r in rows]):>11.1f}"
                    for b in BOOKS))
    print(f"  {'rules still above hold (of ' + str(len(rows)) + ')':<38}"
          + "".join(f"{sum(1 for r in rows if r[f'net_pct_yr_book_{b}'] > HOLD_PCT_YR):>11d}"
                    for b in BOOKS))
    print("  " + "-" * (len(h2) - 2))
    print(f"  Columns are book size. Hold is {HOLD_PCT_YR:.2f} at every one of them:")
    print("  an index does not pay impact to itself. The board's own accounts are")
    print(f"  {crore(200_000)} and {crore(10_000_000)}, the left two columns.\n")

    print("  AND WHAT THE 1% CAP REMOVED TO GET THOSE RATES\n")
    h3 = f"  {'rule':<38}" + "".join(f"{crore(b):>11}" for b in BOOKS)
    print(h3); print("  " + "-" * (len(h3) - 2))
    for r in rows:
        print(f"  {r['rule']:<38}"
              + "".join(f"{r[f'size_cut_pct_book_{b}']:>10.1f}%" for b in BOOKS))
    print("  " + "-" * (len(h3) - 2))
    print("  % of the intended position the cap refused to let you buy. The cap")
    print("  makes the rate ABOVE look BETTER -- a trimmed order pays less impact")
    print("  per rupee -- while the money it turned away earns nothing. Read the")
    print("  two tables together or level 4 reads as good news.\n")

    # ------------------------------------- 3. does it meet the board? ------
    # The payoff. entry_edge's gross column missed the board's own CAGR by
    # +13.44 points (scripts/exposure_reconcile.py). If charging the real fills
    # closes that gap, the costs were the whole discrepancy and nothing exotic
    # is left to explain.
    #
    # NOT an independent confirmation, and the difference matters: both routes
    # price the SAME cached trades. What differs is everything downstream --
    # this script equal-weights every trade over the full universe and all
    # dates with no cash limit; the board capital-weights a cash-constrained
    # account over 5 sub-universes, 5 start years and a mom_hi selection that
    # takes 5-36% of signals. Agreement means the aggregation is not where the
    # money went. It does not re-verify the trades.
    import json
    try:
        d = json.load(open("/data/clean/kitelab/dashboard.json"))
    except (OSError, ValueError) as exc:
        print(f"  (skipping the board reconciliation: {exc})\n")
        d = None
    if d:
        key = {}
        for strat in registry.REGISTRY:
            v = strat.variant
            v = f"{v:g}" if isinstance(v, float) else str(v)
            key[strat.label] = f"{strat.key}|{v}"
        board = {}
        for k, cell in d["grid"].items():
            if not cell or cell.get("cagr") is None:
                continue                      # 216 nulls, all in `recent`
            board.setdefault("|".join(k.split("|")[:2]), []).append(cell["cagr"])

        print(f"\n  3. AGAINST THE BOARD -- built {d['built']}\n")
        h4 = (f"  {'rule':<38}{'this, 1cr':>11}{'BOARD cagr':>12}{'gap':>8}"
              f"{'was (gross)':>13}")
        print(h4); print("  " + "-" * (len(h4) - 2))
        gaps, olds = [], []
        for r in rows:
            b = board.get(key[r["rule"]])
            if not b:
                continue
            bm = float(np.median(b))
            mine = r[f"net_pct_yr_book_{10_000_000}"]
            gaps.append(mine - bm)
            olds.append(r["statutory_pct_yr"] - bm)
            r["board_median_cagr"] = round(bm, 2)
            r["gap_to_board_pts"] = round(mine - bm, 2)
            print(f"  {r['rule']:<38}{mine:>11.1f}{bm:>12.2f}"
                  f"{mine - bm:>8.2f}{r['statutory_pct_yr'] - bm:>13.2f}")
        print("  " + "-" * (len(h4) - 2))
        print(f"  median gap, costs charged: {np.median(gaps):+.2f} points")
        print(f"  median gap, gross (entry_edge's column): "
              f"{np.median(olds):+.2f} points")
        print(f"  the real fills account for "
              f"{np.median(olds) - np.median(gaps):.2f} of the "
              f"{np.median(olds):.2f}-point discrepancy.\n")

    # ----------------------------------------------------------- write ------
    os.makedirs(M, exist_ok=True)
    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
    x = np.arange(len(BOOKS))
    for r in rows:
        ax[0].plot(x, [r[f"net_pct_yr_book_{b}"] for b in BOOKS],
                   marker="o", lw=1.2, label=r["rule"])
        ax[1].plot(x, [r[f"size_cut_pct_book_{b}"] for b in BOOKS],
                   marker="o", lw=1.2)
    ax[0].axhline(HOLD_PCT_YR, color="black", ls="--", lw=1.6,
                  label=f"buy-and-hold {HOLD_PCT_YR:.2f}%")
    ax[0].set_ylabel("% a year while invested, net of all costs")
    ax[0].set_title("The in-market rate, once fills are real")
    ax[1].set_ylabel("% of the intended position refused")
    ax[1].set_title("What the 1% turnover cap removed")
    for a in ax:
        a.set_xticks(x); a.set_xticklabels([crore(b) for b in BOOKS])
        a.set_xlabel("account size"); a.grid(alpha=0.3)
    ax[0].legend(fontsize=6.5, loc="best")
    fig.suptitle(f"Net in-market rate vs account size ({N_RULES} rules, "
                 f"{total:,} trades)")
    fig.tight_layout(); fig.savefig(PNG_PATH, dpi=120); plt.close(fig)
    print(f"  wrote {CSV_PATH}\n  wrote {PNG_PATH}\n")


if __name__ == "__main__":
    main()

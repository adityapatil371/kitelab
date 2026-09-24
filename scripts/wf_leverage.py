"""Leverage and ruin on the daily board -- the two things leverage does NOT
scale linearly.

WHY THIS EXISTS. Return on capital under leverage L is L*(gross - cost), so L
multiplies profit and cost alike and can never flip the sign of the bracket.
That much is algebra and needs no data. Two things are NOT linear in L and are
therefore worth measuring:

  1. RUIN. Equity compounds, so a single session at r <= -1/L ends the account
     permanently. The board's own `wiped` flag is the L=1 version of this.
  2. VOLATILITY DRAG. Growth is E[log(1 + L*r)], not L*E[log(1 + r)]. The log
     is concave, so leverage costs roughly L^2*sigma^2/2 and the growth-
     maximising L is FINITE even for a positive-edge rule.

THE ACTUAL QUESTION. A rule that loses to buy-and-hold on return can still beat
it at matched RISK if it is the quieter account: lever it to hold's volatility
and the extra return might clear the gap. That is the only way leverage
produces a surprise win here, and it is a testable claim --

    k = sigma_hold / sigma_rule

k > 1 means the rule is quieter than hold and levering it UP is the fair
comparison; k < 1 means the rule is the noisier account and matching risk
DE-levers it, which can only widen the gap. `scripts/wf_risk.py` measured
k < 1 on three rules of the OLD board (2026-09-08). This asks all 36 rows of
the current one.

WHAT IT READS   CLEAN/dashboard.json (the board), the cached signal trades via
                wf_attach.arm_a_trades (costs on, spread applied, 1% cap), and
                output/wf_attach_hold_<board key>.pkl (the hold curves).
WHAT IT WRITES  output/measurements/wf_leverage_<date>.csv  (one row per cell
                per leverage) and output/figures/wf_leverage_<date>.png.
                Nothing it writes is in any cache stamp.

MODELLING CHOICES, each of which costs the rule rather than flattering it:
  * Leverage is applied to the ACCOUNT's daily return. That is the right
    abstraction for "borrow and scale every position by L"; it is not the same
    as raising the per-trade risk percentage, which would change which signals
    the account can afford and is a different experiment.
  * Financing: MTF money is charged at FIN_RATE on the borrowed fraction
    (L-1), daily, 252-day year. Run with --fin 0 for the frictionless algebra.
  * Ruin is absorbing. Once equity <= 0 the path stays at 0; it does not
    recover on paper the way a naive cumprod would. A margin call would in
    practice arrive EARLIER than equity = 0, so RUIN here is a floor on how
    often a levered account dies, never an over-statement.
  * Hold is levered by the same L when compared at the same L, so the
    comparison never lets the rule borrow for free.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import wf_attach as W
from kitelab import config, registry, portfolio

OUT = Path(__file__).resolve().parent.parent / "output"
FIG = OUT / "figures"
MEAS = OUT / "measurements"

LEVERAGES = [1.0, 1.5, 2.0, 3.0, 5.0]
FIN_RATE = 0.12          # MTF, ~12%/yr on the borrowed part. --fin to change.
TRADING_DAYS = 252
WIPEOUT_FRAC = 0.20      # "practically dead": 80% of starting capital gone
MIN_DAYS = 250

UNIVERSE = "all"
RISK = 1.0
CAPITAL = 10_000_000
PRIORITY = "mom_hi"


# ----------------------------------------------------------- the model ----
def levered_path(r: np.ndarray, lev: float, fin: float) -> dict:
    """Walk a levered account day by day, with an absorbing barrier at zero.

    Vectorised cumprod is wrong here: it happily carries a negative equity
    forward and lets a dead account recover. The loop is the point.
    """
    carry = (lev - 1.0) * fin / TRADING_DAYS
    eq = 1.0
    peak = 1.0
    maxdd = 0.0
    ruined = False
    wiped = False
    ruin_day = None
    for i, x in enumerate(r):
        eq *= (1.0 + lev * x)
        eq -= carry * eq if eq > 0 else 0.0
        if eq <= 0.0:
            eq = 0.0
            ruined = True
            ruin_day = i
            break
        if eq < WIPEOUT_FRAC:
            wiped = True
        peak = max(peak, eq)
        maxdd = min(maxdd, eq / peak - 1.0)
    return {"final": eq, "maxdd": 100 * maxdd, "ruined": ruined,
            "wiped": wiped or ruined, "ruin_day": ruin_day}


def cagr_of(final: float, years: float) -> float | None:
    if final <= 0 or years <= 0:
        return None
    return 100 * (final ** (1 / years) - 1)


def daily_returns(eq: np.ndarray) -> np.ndarray:
    return eq[1:] / eq[:-1] - 1.0


# ------------------------------------------------------------- the run ----
def cell_curve(trades, year):
    """One board cell's daily equity curve, start year applied to the trades."""
    cut = pd.Timestamp(f"{year}-01-01")
    before = len(trades)
    window = [t for t in trades if pd.Timestamp(t["entry_ts"]) >= cut]
    after = len(window)
    if not window:
        return None, before, after
    r = portfolio.run(sorted(window, key=lambda t: t["entry_ts"]),
                      CAPITAL, RISK / 100, PRIORITY)
    return (r.get("curve") or []), before, after


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fin", type=float, default=FIN_RATE,
                    help=f"financing rate on borrowed money (default {FIN_RATE})")
    ap.add_argument("--years", type=int, nargs="+", default=[2006, 2018],
                    help="start years to run (default 2006 2018)")
    ap.add_argument("--pilot", type=int, metavar="N",
                    help="only the first N strategies")
    args = ap.parse_args()

    # LINE-BUFFERED, ALWAYS -- the same trap wf_attach.main() documents:
    # redirected stdout block-buffers, so a multi-minute run writes an empty
    # log file and reads exactly like a hang.
    sys.stdout.reconfigure(line_buffering=True)

    t0 = time.time()
    dash = W.CLEAN / "dashboard.json"
    if not dash.exists():
        raise SystemExit(f"No dashboard.json at {dash}. Build it: python3 -m scripts.refresh")
    payload = json.loads(dash.read_bytes())
    stamp = payload["built"].split()[0]
    print(f"dashboard.json: built {payload['built']}, {len(payload['grid']):,} grid cells")

    cfg = config.load()
    members = list(cfg.merged)
    unis = W.stock_universes(members)
    W.assert_buckets_match_payload(unis, payload)
    holds = W.hold_curves(members, unis, [], W.board_key(payload))
    if UNIVERSE not in holds:
        raise SystemExit(f"no hold curve for universe {UNIVERSE!r}")
    hold = holds[UNIVERSE]
    print(f"hold curve '{UNIVERSE}': {len(hold):,} days, "
          f"{hold.index[0].date()} to {hold.index[-1].date()}")

    strategies = registry._build_registry()
    if args.pilot:
        strategies = strategies[:args.pilot]
    print(f"strategies: {len(strategies)}   leverages: {LEVERAGES}   "
          f"financing: {100*args.fin:.0f}%/yr\n")

    rows = []
    checked = 0
    for n, strat in enumerate(strategies, 1):
        raw = W.arm_a_trades(strat)
        if raw is None:
            print(f"  [{n:2d}/{len(strategies)}] {W.sid(strat):<16} SKIP -- no signal cache")
            continue
        # THE BOARD'S OWN EXECUTION. arm_a_trades only LOADS the cache;
        # run_arm applies spread_of() to it, and that call is what sets
        # slippage.ENABLED and MAX_PARTICIPATION. Leaving it out does not
        # merely drop the half-spread -- it leaves the 1% participation cap
        # OFF, so the account sizes positions it could never fill and
        # mr|own|all|1|10000000|1|2006|mom_hi reads 35.2% CAGR against the
        # board's 8.7%. Found the hard way on 2026-09-23; the self_check
        # below now makes it impossible to miss again.
        trades = W.spread_of(raw)
        for year in args.years:
            curve, before, after = cell_curve(trades, year)
            print(f"  [{n:2d}/{len(strategies)}] {W.sid(strat):<16} {year}  "
                  f"trades {before:>7,} -> {after:>7,}", end="")
            if not curve:
                print("   SKIP (no trades in window)")
                continue

            idx = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d, _ in curve])
            eq = pd.Series([float(v) for _, v in curve], index=idx)
            eq = eq[~eq.index.duplicated(keep="last")]
            shared = eq.index.intersection(hold.index)
            if len(shared) < MIN_DAYS:
                print(f"   SKIP (only {len(shared)} shared days)")
                continue
            re = daily_returns(eq.loc[shared].to_numpy())
            rh = daily_returns(hold.loc[shared].to_numpy())
            years = (shared[-1] - shared[0]).days / 365.25

            sd_r = float(np.std(re, ddof=1))
            sd_h = float(np.std(rh, ddof=1))
            k = sd_h / sd_r if sd_r > 0 else np.nan

            # hold at L=1 is the yardstick every row is measured against
            h1 = levered_path(rh, 1.0, args.fin)
            hold_cagr = cagr_of(h1["final"], years)

            # the rule at its risk-matched leverage -- the surprise-win test
            km = levered_path(re, float(k), args.fin) if np.isfinite(k) else None
            matched_cagr = cagr_of(km["final"], years) if km else None

            # growth-optimal leverage, searched not assumed
            best_l, best_g = None, -np.inf
            for lv in np.arange(0.1, 5.01, 0.1):
                p = levered_path(re, float(lv), args.fin)
                g = cagr_of(p["final"], years)
                if g is not None and g > best_g:
                    best_g, best_l = g, float(lv)

            # SELF-CHECK. Our L=1 rule CAGR must reproduce the board cell it
            # claims to be. Same spirit as wf_attach.SELF_CHECK_KEY: a
            # standalone harness that silently stops matching the thing it
            # re-simulates is worse than no harness.
            gkey = W.grid_key(strat, UNIVERSE, RISK, CAPITAL, year, PRIORITY)
            board_cell = payload["grid"].get(gkey)
            if board_cell and board_cell.get("cagr") is not None:
                ours = cagr_of(levered_path(re, 1.0, 0.0)["final"], years)
                if ours is not None and abs(ours - board_cell["cagr"]) > 0.15:
                    raise SystemExit(
                        f"SELF-CHECK FAILED on {gkey}: this harness says "
                        f"{ours:.2f}% CAGR, dashboard.json says "
                        f"{board_cell['cagr']:.2f}%. Do not trust anything "
                        f"downstream until they agree.")
                checked += 1

            for lev in LEVERAGES:
                pr = levered_path(re, lev, args.fin)
                ph = levered_path(rh, lev, args.fin)
                rows.append({
                    "row": W.sid(strat), "start_year": year, "days": len(re),
                    "years": round(years, 2), "leverage": lev,
                    "rule_cagr": None if (c := cagr_of(pr["final"], years)) is None else round(c, 2),
                    "hold_cagr": None if (c := cagr_of(ph["final"], years)) is None else round(c, 2),
                    "rule_maxdd": round(pr["maxdd"], 1),
                    "hold_maxdd": round(ph["maxdd"], 1),
                    "rule_ruined": pr["ruined"], "hold_ruined": ph["ruined"],
                    "rule_wiped": pr["wiped"], "hold_wiped": ph["wiped"],
                    "sd_rule_bp": round(1e4 * sd_r, 1), "sd_hold_bp": round(1e4 * sd_h, 1),
                    "k_match": round(float(k), 3),
                    "matched_cagr": None if matched_cagr is None else round(matched_cagr, 2),
                    "hold_cagr_l1": None if hold_cagr is None else round(hold_cagr, 2),
                    "opt_leverage": best_l,
                    "opt_cagr": None if best_g == -np.inf else round(best_g, 2),
                })
            print(f"   k {k:5.2f}  matched {matched_cagr if matched_cagr is None else round(matched_cagr,1):>7}"
                  f"  hold {round(hold_cagr,1) if hold_cagr else None:>7}  optL {best_l}")

    if not rows:
        raise SystemExit("no cells produced -- nothing to report")

    print(f"\nself-check: {checked} cells reproduced the published board CAGR "
          f"to within 0.15 pts")
    df = pd.DataFrame(rows)
    for col in ("row", "leverage", "rule_cagr", "hold_cagr", "k_match", "matched_cagr"):
        if col not in df.columns:
            raise SystemExit(f"required column missing from result: {col}")
    print(f"\nresult frame: {df.shape[0]} rows x {df.shape[1]} columns")
    print(df.head(3).to_string(index=False))

    MEAS.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    csv = MEAS / f"wf_leverage_{stamp}.csv"
    df.to_csv(csv, index=False)

    # ------------------------------------------------------- the verdict ----
    print("\n" + "=" * 78)
    print("THE SURPRISE-WIN TEST: does any row beat hold at MATCHED RISK?")
    print("=" * 78)
    one = df[df.leverage == 1.0].copy()
    print(f"cells before filter: {len(df)}   at leverage 1.0: {len(one)}")
    one = one[one.matched_cagr.notna() & one.hold_cagr_l1.notna()]
    print(f"after dropping cells with no finite CAGR: {len(one)}")
    one["matched_edge"] = one.matched_cagr - one.hold_cagr_l1
    for year in sorted(one.start_year.unique()):
        s = one[one.start_year == year]
        win = s[s.matched_edge > 0]
        print(f"\n  start {year}: {len(s)} rows, k>1 (quieter than hold) in "
              f"{(s.k_match > 1).sum()}, matched-risk WINS in {len(win)}")
        print(f"    k          min {s.k_match.min():.2f}  median {s.k_match.median():.2f}  max {s.k_match.max():.2f}")
        print(f"    matched edge vs hold  median {s.matched_edge.median():+.2f}  best {s.matched_edge.max():+.2f} pts/yr")
        top = s.nlargest(5, "matched_edge")[["row", "k_match", "matched_cagr", "hold_cagr_l1", "matched_edge", "opt_leverage"]]
        print(top.to_string(index=False))

    print("\n" + "=" * 78)
    print("RUIN: how often the levered account dies")
    print("=" * 78)
    rn = df.groupby("leverage").agg(
        cells=("row", "size"),
        rule_ruined=("rule_ruined", "sum"), rule_wiped=("rule_wiped", "sum"),
        hold_ruined=("hold_ruined", "sum"), hold_wiped=("hold_wiped", "sum"),
        rule_med_cagr=("rule_cagr", "median"), hold_med_cagr=("hold_cagr", "median"),
        rule_med_dd=("rule_maxdd", "median"), hold_med_dd=("hold_maxdd", "median"))
    print(rn.to_string())

    print("\n" + "=" * 78)
    print("VOLATILITY DRAG: the growth-maximising leverage, searched 0.1..5.0")
    print("=" * 78)
    o = df[df.leverage == 1.0]
    print(f"  opt L   min {o.opt_leverage.min():.1f}  median {o.opt_leverage.median():.1f}  max {o.opt_leverage.max():.1f}")
    print(f"  rows whose optimum is BELOW 1.0 (leverage hurts at once): "
          f"{(o.opt_leverage < 1.0).sum()} of {len(o)}")

    # ------------------------------------------------------------ figure ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
    base = df[df.start_year == min(args.years)]
    for rowname, g in base.groupby("row"):
        g = g.sort_values("leverage")
        ax[0].plot(g.leverage, g.rule_cagr, color="#888", lw=0.8, alpha=0.6)
    hh = base.groupby("leverage").hold_cagr.median()
    ax[0].plot(hh.index, hh.values, color="crimson", lw=2.5, label="buy & hold")
    ax[0].set_xlabel("leverage L"); ax[0].set_ylabel("CAGR %/yr")
    ax[0].set_title(f"Every board row under leverage (start {min(args.years)})")
    ax[0].legend(); ax[0].grid(alpha=0.3)

    o2 = base[base.leverage == 1.0]
    ax[1].scatter(o2.k_match, o2.matched_cagr - o2.hold_cagr_l1, s=30, color="#2b6cb0")
    ax[1].axhline(0, color="crimson", lw=1.5)
    ax[1].axvline(1, color="#888", lw=1, ls="--")
    ax[1].set_xlabel("k = sigma_hold / sigma_rule   (>1 = rule is quieter)")
    ax[1].set_ylabel("matched-risk CAGR minus hold, pts/yr")
    ax[1].set_title("The surprise-win test")
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    png = FIG / f"wf_leverage_{stamp}.png"
    fig.savefig(png, dpi=130)

    print(f"\nwrote {csv}")
    print(f"wrote {png}")
    print(f"took {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()

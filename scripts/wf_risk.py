"""Risk-adjusted comparison: rules vs equal-weight hold. RAN 2026-09-08 (11.6s).

RESULT: THE RISK DEFENCE FAILS. The rules do not buy safety with their lost
return -- they are the noisier account AND the losing one.

  account            CAGR   maxDD  under water  >20% down  worst 12m  Calmar  matched   k
  hold              13.97   -55.3       74.5mo      25.5%      -51.6    0.25    13.97 1.00
  Turtle_1tf_55_20  11.82   -53.0       36.3mo      37.5%      -50.5    0.22     8.98 0.73
  Turtle_w20_55_20   8.81   -54.8       77.0mo      56.8%      -32.4    0.16     6.68 0.71
  EMA_b0            -4.43   -87.8      223.8mo      91.9%      -72.3   -0.05    -2.24 0.64

k < 1 for every rule: hold's volatility is only ~0.7x the rule's, so matching
risk means de-levering the rule, not levering it up. The Turtle is MORE
volatile than a 1,000-name equal-weight book -- unsurprising for 13-17
concentrated positions, but it is the opposite of the premise this file was
written to test. Matched to hold's risk the Turtle compounds at 8.98%/yr
against hold's 13.97%: it loses on return AND on risk. Every ratio agrees
(Calmar 0.22 vs 0.25, Sortino 0.72 vs 1.05) and the two caveats below would
only flatter the rule further, so they do not rescue it.

THE 2008 CRASH-PROTECTION CLAIM WAS AN ARTEFACT OF FREE FILLS. The note this
file was built on said the Turtle fell 23.5% in 2008 while hold fell 55.3%.
With fills priced, calendar 2008 is hold -50.4% vs Turtle -50.2%, and
peak-to-trough hold -55.3% vs Turtle -53.0% -- a tie. The protection was the
same unpriced illiquidity that produced the fake edge.

Nor is it a shallower ride overall. Drawdowns past -20%, priced fills:
  hold    4 episodes  (-55.3 2008, -48.2 2018-20, -21.7 2022, -25.0 2024-)
  Turtle  7 episodes  (-25.1 2006, -53.0 2008, -34.4 2011, -30.8 2015,
                       -42.3 2018, -46.5 2022, -33.5 2024- unrecovered)
The Turtle's one genuine win is RECOVERY SPEED, not depth: 36.3 months under
water at worst against hold's 74.5. It pays for that by sitting more than 20%
below its own peak on 37.5% of all sessions versus hold's 25.5%.

So "0 of 950 cells beat hold" may now be quoted as a verdict on return AND
risk. The return-only caveat is discharged.

Reproduce: python3 -m scripts.wf_risk    (output/wf_risk_2026-09-08.csv|.png|.log)
Drawdown-episode and calendar-year detail came from a throwaway diagnostic that
re-ran the same curve; the episode table above is its output.

--- original rationale, kept because it is why the file exists -------------

WHY THIS EXISTS. Every verdict on the board so far -- including the
scripts.wf_daily --capped grid that killed all 950 cells -- ranks on
compounded return alone. The user stopped the session to point out that this
is only a valid ranking if both accounts carry the SAME RISK, and they
plainly do not:

  * equal-weight hold is 100% invested in ~1,000 names every session;
  * Turtle_1tf_55_20 holds ~13-17 positions and sat 22.3% in cash through
    the 2008 crash, falling 23.5% while hold fell 55.3%.

A rule can lose on CAGR and still be the better account. Until this runs,
"zero of 950 survive" is a statement about RETURN ONLY and must be quoted
that way. It is NOT yet a claim that the rules are worthless.

Partial evidence already in hand (from the bear-market pass, frictionless):
  hold      -55.3% in 2008, 17 months to the bottom; also -48% (2020), -25% (2025)
  Turtle    -23.5% in 2008, then +1,044% off the low -- but LAGGED every
            other recovery (2015 +14 vs +19, 2018 +70 vs +94, 2022 +18 vs +28)
  EMA_b0    -85.2% max drawdown full period; fell HARDER than hold in six of
            seven bear markets. EMA needs no risk adjustment to be rejected.
So the Turtle is the only strategy where this question is live.

WHAT IT MEASURES, on the same daily curves, friction ON:
  max drawdown, longest time under water, share of days >20% below a peak,
  Calmar, Sharpe, Sortino, worst rolling 12 months.

Then the version that avoids arguing over which ratio is the right ratio:
scale the rule's daily returns until its volatility MATCHES hold's, and
compare CAGR at equal risk. If the Turtle is genuinely the quieter engine,
levering it to hold's risk should beat hold; if it still loses there, it is
not a better trade-off, just a smaller position.

TWO CAVEATS TO REPORT ALONGSIDE THE MATCHED-RISK NUMBER, not after it:
  1. The leverage is a thought experiment. Real leverage costs financing and
     gets margin-called at precisely the worst moment.
  2. A rule that sits in cash has low volatility BY CONSTRUCTION -- cash days
     are zero-variance -- which flatters every ratio with an SD in the
     denominator. Calmar and Sortino are less gameable that way, which is why
     all of them are reported rather than one.

MTIME TRAP -- DO NOT EDIT ANY kitelab/*.py FILE. signals.stamp() digests file
mtimes, not contents (kitelab/signals.py:19,138), so touching one invalidates
every signal cache and forces a ~103-minute rebuild that returns byte-identical
numbers. Both friction switches below are module-level globals READ AT RUN TIME
inside portfolio.run (kitelab/portfolio.py:525 capped_shares, :544 impact), so
setting them from a script costs nothing. scripts/*.py is safe to edit --
nothing imports it.

The half-spread is applied earlier, at signal-generation time
(kitelab/backtest.py:386), and is baked into the caches. So friction-on here is
a LOWER BOUND on true trading cost, same as in scripts.wf_daily --capped.

Reads:  /data/clean/kitelab/signal_cache/{strategy}_all.pkl
        output/wf_tail_hold.pkl   (the daily hold curve, already checkpointed)
Writes: output/wf_risk_<today>.csv
        output/figures/wf_risk_<today>.png   (drawdown chart; skipped if no matplotlib)

Cost: 3 portfolio.run calls. scripts.wf_tail did 36 of them, so this should be
well inside that -- but the last estimate given to the user (3 min for the
capped grid) was wrong by 4x (it took 11), so TIME IT, do not predict it.
See the kitelab-verify-estimates-before-stating memory.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

import kitelab.config as C
from kitelab import portfolio, slippage
from kitelab.config import CLEAN

import scripts.wf_daily as wd

OUT = Path(__file__).resolve().parent.parent / "output"
FIG = OUT / "figures"              # every PNG
FIG.mkdir(parents=True, exist_ok=True)
CACHE = CLEAN / "signal_cache"

CAPITAL = 200_000.0
RISK = 0.01
PRIORITY = "liquidity"          # most-traded name first: the defensible fill
TRADING_DAYS = 252


# ---------------------------------------------------------------- loading

def load_trades(cache: str, members: set) -> list[dict]:
    p = CACHE / f"{cache}_all.pkl"
    if not p.exists():
        raise SystemExit(f"no signal cache at {p}")
    blob = pickle.loads(p.read_bytes())
    trades = blob["trades"] if isinstance(blob, dict) else blob
    keep = [t for t in trades if t["symbol"] in members]
    print(f"  {cache}: {len(trades):,} trades in cache, "
          f"{len(keep):,} after keeping universe members")
    return keep


def curve_to_series(curve) -> pd.Series:
    """portfolio.run returns `curve` as (timestamp, account value) pairs."""
    s = pd.Series({pd.Timestamp(ts): float(v) for ts, v in curve}).sort_index()
    if s.empty:
        raise SystemExit("portfolio.run returned an empty curve")
    if (s <= 0).any():
        raise SystemExit(f"curve has {(s <= 0).sum()} non-positive values; "
                         "returns are undefined there")
    return s


# ---------------------------------------------------------------- metrics

def drawdown_stats(curve: pd.Series) -> dict:
    """Peak-to-trough depth, and how long before it got back to even."""
    peak = curve.cummax()
    dd = curve / peak - 1.0
    under = dd < 0
    longest, start = 0, None
    for ts, below in under.items():
        if below and start is None:
            start = ts
        elif not below and start is not None:
            longest = max(longest, (ts - start).days)
            start = None
    if start is not None:                       # never recovered
        longest = max(longest, (under.index[-1] - start).days)
    return {"max_dd_pct": float(dd.min() * 100),
            "months_under_water": longest / 30.44,
            "pct_days_below_20": float((dd < -0.20).mean() * 100)}


def cagr_of(curve: pd.Series) -> float:
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    return float((curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1) * 100


def ratios(curve: pd.Series) -> dict:
    r = curve.pct_change().dropna()
    ann_ret = cagr_of(curve)
    ann_vol = float(r.std() * np.sqrt(TRADING_DAYS) * 100)
    down = r[r < 0]
    ann_down = float(down.std() * np.sqrt(TRADING_DAYS) * 100) if len(down) else float("nan")
    mdd = abs(drawdown_stats(curve)["max_dd_pct"])
    # No risk-free rate: this is return per unit of risk, not an excess-return
    # Sharpe. Both sides are treated identically so the COMPARISON is valid.
    return {"ann_vol_pct": ann_vol,
            "sharpe_naive": ann_ret / ann_vol if ann_vol else float("nan"),
            "sortino_naive": ann_ret / ann_down if ann_down else float("nan"),
            "calmar": ann_ret / mdd if mdd else float("nan")}


def worst_12m(curve: pd.Series) -> float:
    """Worst 365-calendar-day return anywhere in the record."""
    roll = curve / curve.reindex(curve.index - pd.Timedelta(days=365),
                                 method="ffill").values - 1.0
    return float(np.nanmin(roll.values)) * 100


def matched_risk_cagr(rule: pd.Series, hold: pd.Series) -> tuple[float, float]:
    """Scale the rule's daily returns to hold's volatility, then compound.

    k > 1 means the rule was the quieter account and we are levering it UP.
    A THOUGHT EXPERIMENT: real leverage costs financing and gets called."""
    r = rule.pct_change().dropna()
    h = hold.pct_change().dropna()
    common = r.index.intersection(h.index)
    if len(common) < 250:
        raise SystemExit(f"only {len(common)} shared sessions; too few to match risk")
    r, h = r.loc[common], h.loc[common]
    k = float(h.std() / r.std())
    levered = (1 + k * r).cumprod()
    yrs = (common[-1] - common[0]).days / 365.25
    return float(levered.iloc[-1] ** (1 / yrs) - 1) * 100, k


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies",
                    default="Turtle_1tf_55_20,Turtle_w20_55_20,EMA_b0")
    ap.add_argument("--no-friction", action="store_true",
                    help="run with the illiquidity UNPRICED (flatters the rule)")
    args = ap.parse_args()

    if not args.no_friction:
        slippage.MAX_PARTICIPATION = 0.01
        slippage.ENABLED = True
        print("friction ON: order <= 1% of daily traded value, impact charged")
    else:
        print("friction OFF -- illiquid fills are free, results will flatter the rule")

    cfg = C.load()
    members = set(cfg.merged)
    print(f"universe: {len(members)} symbols")

    ckpt = OUT / "wf_tail_hold.pkl"
    if ckpt.exists():
        hold = pickle.loads(ckpt.read_bytes())
        print(f"hold curve: loaded checkpoint {ckpt.name} ({len(hold)} sessions)")
    else:
        closes = wd.closes_matrix(members)
        hold = wd.hold_curve(closes, set(closes.columns))
        ckpt.write_bytes(pickle.dumps(hold))
        print(f"hold curve: built and checkpointed to {ckpt.name}")
    print(f"hold curve: {len(hold)} rows, {hold.index[0].date()} -> "
          f"{hold.index[-1].date()}\n{hold.head(3)}")

    stamp = dt.date.today().isoformat()
    path = OUT / f"wf_risk_{stamp}.csv"
    fields = ["account", "cagr_pct", "max_dd_pct", "months_under_water",
              "pct_days_below_20", "worst_12m_pct", "ann_vol_pct",
              "sharpe_naive", "sortino_naive", "calmar",
              "matched_risk_cagr", "leverage_k"]
    rows, curves = [], {"hold": hold}

    def record(name: str, curve: pd.Series, is_hold: bool) -> None:
        row = {"account": name, "cagr_pct": cagr_of(curve)}
        row.update(drawdown_stats(curve))
        row["worst_12m_pct"] = worst_12m(curve)
        row.update(ratios(curve))
        if is_hold:
            row["matched_risk_cagr"], row["leverage_k"] = row["cagr_pct"], 1.0
        else:
            row["matched_risk_cagr"], row["leverage_k"] = matched_risk_cagr(curve, hold)
        rows.append({k: (round(v, 3) if isinstance(v, float) else v)
                     for k, v in row.items()})

    record("hold", hold, True)

    for name in args.strategies.split(","):
        name = name.strip()
        print(f"\n{'='*70}\n{name}")
        pool = load_trades(name, members)
        res = portfolio.run(pool, CAPITAL, RISK, priority=PRIORITY)
        curve = curve_to_series(res["curve"])
        curves[name] = curve
        print(f"  filled {len(res['taken']):,} trades, "
              f"CAGR {res['cagr_pct']:.2f}%/yr, final Rs {res['final']:,.0f}")
        record(name, curve, False)

    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    hdr = (f"\n{'account':<22}{'CAGR':>8}{'maxDD':>9}{'under water':>13}"
           f"{'>20% down':>11}{'worst 12m':>11}{'Calmar':>8}{'Sortino':>9}"
           f"{'matched':>9}{'x':>6}")
    print(hdr + "\n" + "-" * len(hdr.strip()))
    for r in rows:
        print(f"{r['account']:<22}{r['cagr_pct']:>8.2f}{r['max_dd_pct']:>9.1f}"
              f"{r['months_under_water']:>11.1f}mo{r['pct_days_below_20']:>10.1f}%"
              f"{r['worst_12m_pct']:>11.1f}{r['calmar']:>8.2f}"
              f"{r['sortino_naive']:>9.2f}{r['matched_risk_cagr']:>9.2f}"
              f"{r['leverage_k']:>6.2f}")

    print("\nREAD THE MATCHED COLUMN WITH BOTH CAVEATS: the leverage is a thought\n"
          "experiment (financing cost, margin calls), and cash days are\n"
          "zero-variance so any SD-denominated ratio flatters a rule that sits out.")

    # ---- drawdown chart (optional; CSV is the deliverable) --------------
    try:
        import matplotlib
        matplotlib.use("Agg")               # never open a window
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed -- CSV written, chart skipped")
        print(f"\nwrote {path}")
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    for name, c in curves.items():
        dd = (c / c.cummax() - 1.0) * 100
        ax.plot(dd.index, dd.values, linewidth=1.0, label=name)
    ax.axhline(-20, color="grey", linewidth=0.6, linestyle="--")
    ax.set_ylabel("drawdown from previous peak (%)")
    ax.set_title("How far below its own high-water mark each account sat, "
                 "friction priced")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    png = FIG / f"wf_risk_{stamp}.png"
    fig.savefig(png, dpi=130)
    plt.close(fig)
    print(f"wrote {png}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

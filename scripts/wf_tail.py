"""Is the edge one lucky tail? (off-board diagnostic, 2026-09-08)

WHY. scripts.wf_daily found Turtle_1tf_55_20 ahead of equal-weight hold on
`all` even with liquid-first fills (+14.0 pts/yr, HAC t = 2.53). But its bear-
market profile is carried by ONE episode: it fell 23% in the 2008 crash
against hold's 55%, held 22% cash through it, then made +1,044% off the low
while hold made +124%. In every OTHER recovery it LAGGED (2015 +14 vs +19,
2018 +70 vs +94, 2022 +18 vs +28). That is the shape of a single event, not a
repeatable edge -- the "is the return one thin tail?" question the
kitelab-validation skill lists as a holdout stand-in.

TWO WAYS OF ASKING, because they answer different things:

  CONCENTRATION -- what share of total profit comes from the best N filled
    trades. Descriptive, no re-run. Tells you how lopsided the record is.

  DELETION -- remove those N trades from the CANDIDATE pool and re-run the
    account. The money that would have gone into them is then free for other
    signals, which is what would really have happened. This is the honest
    version and it is always kinder than simply subtracting their profit.

  ERA -- restrict both the rule and the benchmark to 2010 onward, so the 2008
    crash and its rebound are out of the sample entirely. If the edge is the
    2008 recovery, this is where it disappears.

Friction is ON by default (order <= 1% of that day's traded value, market
impact charged), because with it OFF the illiquid fills dominate everything --
see the --capped note in scripts.wf_daily. kitelab/*.py is untouched: both
switches are globals read at run time inside portfolio.run.

Reads:  /data/clean/kitelab/signal_cache/*_all.pkl, the price frames
Writes: output/wf_tail_<today>.csv
Cost:   about a minute. No rebuild, no network.
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
CACHE = CLEAN / "signal_cache"

CAPITAL = 200_000.0
RISK = 0.01
PRIORITY = "liquidity"          # buy the MOST traded name first: the defensible fill
DROPS = [0, 1, 3, 5, 10, 25]
ERA_FROM = pd.Timestamp("2010-01-01")


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


def profit_of(t: dict) -> float:
    """Rupee profit of one FILLED trade AT THIS ACCOUNT'S SIZING.

    portfolio.run adds `net` to each taken trade. The cache's own `net_profit`
    is a different number -- the producer sizes every signal against a Rs1cr
    paper book (sizing.CAPITAL) so that no signal the largest account could
    take is dropped, so `net_profit` describes no account on the board. Using
    it here would rank trades by the wrong money."""
    if "net" not in t or t["net"] is None:
        raise SystemExit("portfolio.run did not stamp `net` on a taken trade; "
                         f"fields are {sorted(t)}")
    return float(t["net"])


def key_of(t: dict) -> tuple:
    return (t["symbol"], pd.Timestamp(t["entry_ts"]))


def cagr_from_curve(curve, since=None) -> tuple[float, int]:
    s = pd.Series({ts: v for ts, v in curve}).sort_index()
    if since is not None:
        s = s.loc[s.index >= since]
    if len(s) < 2 or s.iloc[0] <= 0 or s.iloc[-1] <= 0:
        return float("nan"), len(s)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100, len(s)


def hold_cagr(hold: pd.Series, since=None) -> float:
    s = hold if since is None else hold.loc[hold.index >= since]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="Turtle_1tf_55_20,EMA_b0,Turtle_w20_55_20")
    ap.add_argument("--no-friction", action="store_true",
                    help="run with the illiquidity UNPRICED (the old default)")
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

    stamp = dt.date.today().isoformat()
    path = OUT / f"wf_tail_{stamp}.csv"
    fields = ["strategy", "era", "dropped", "trades_filled", "rule_cagr",
              "hold_cagr", "excess_pts", "top_share_of_profit", "final_rupees"]
    rows = []

    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()

        for name in args.strategies.split(","):
            name = name.strip()
            print(f"\n{'='*70}\n{name}")
            pool = load_trades(name, members)

            base = portfolio.run(pool, CAPITAL, RISK, priority=PRIORITY)
            taken = base["taken"]
            profits = np.array([profit_of(t) for t in taken])
            order = np.argsort(-profits)
            total = profits.sum()
            print(f"  filled {len(taken):,} trades, total profit "
                  f"Rs {total:,.0f}, CAGR {base['cagr_pct']:.2f}%/yr")
            if total <= 0:
                print("  total profit is not positive -- concentration shares are "
                      "not meaningful, deletion results still are")

            # ---- CONCENTRATION (descriptive) -----------------------------
            print(f"\n  {'best N trades':<16}{'their profit':>16}{'share of total':>16}")
            share = {}
            for n in DROPS[1:]:
                if n > len(taken):
                    continue
                got = profits[order[:n]].sum()
                share[n] = got / total if total > 0 else float("nan")
                print(f"  {'top ' + str(n):<16}{got:>16,.0f}{share[n]*100:>15.1f}%")

            # ---- DELETION (re-run without them) --------------------------
            print(f"\n  {'dropped':<10}{'filled':>9}{'rule CAGR':>12}"
                  f"{'hold CAGR':>12}{'excess':>10}{'final Rs':>16}")
            for era_name, since in (("full", None), ("2010+", ERA_FROM)):
                hc = hold_cagr(hold, since)
                for n in DROPS:
                    banned = {key_of(taken[i]) for i in order[:n]} if n else set()
                    sub = [t for t in pool if key_of(t) not in banned] if n else pool
                    if since is not None:
                        sub = [t for t in sub if pd.Timestamp(t["entry_ts"]) >= since]
                    r = portfolio.run(sub, CAPITAL, RISK, priority=PRIORITY)
                    rc, _ = cagr_from_curve(r["curve"], since)
                    rows.append({
                        "strategy": name, "era": era_name, "dropped": n,
                        "trades_filled": len(r["taken"]),
                        "rule_cagr": round(rc, 2), "hold_cagr": round(hc, 2),
                        "excess_pts": round(rc - hc, 2),
                        "top_share_of_profit": (round(share.get(n, float("nan")), 4)
                                                if n else 0.0),
                        "final_rupees": round(r["final"], 0)})
                    w.writerow(rows[-1])
                    if era_name == "full" and n == 0:
                        print(f"  {'-- full history --':<10}")
                    if era_name == "2010+" and n == 0:
                        print(f"  {'-- 2010 onward --':<10}")
                    print(f"  {n:<10}{len(r['taken']):>9,}{rc:>12.2f}"
                          f"{hc:>12.2f}{rc-hc:>10.2f}{r['final']:>16,.0f}")

    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

"""How much of the walk-forward shortfall is the net-vs-gross cost bug?
(off-board diagnostic, 2026-09-08)

THE BUG. The five checks compare a cell's CAGR *net* of costs against
equal-weight buy-and-hold computed *gross* of them (kitelab-validation skill,
check 1; validation._hold sums last/first with no fees). The rule pays the
Zerodha schedule on every round trip; the benchmark pays nothing, ever. Every
margin on the board is therefore biased against the rule by some amount, and
no write-up of "these rules lose to hold" is honest until that amount is on
the table.

WHY THIS NEEDS NO PRICES. _hold buys each member once and sells it once, so
the fee is a single multiplicative haircut on that member's last/first --
identical for every member and independent of the price path. Net wealth is
gross wealth times a constant k, so

    CAGR_net = ((1 + CAGR_gross/100) * k**(1/years) - 1) * 100

is exact given a proportional cost model, and the built dashboard already
carries CAGR_gross and the dates for every window. No parquet, no rebuild.

WHAT IS AND IS NOT MODELLED. Proportional delivery charges only: STT,
exchange transaction, SEBI, stamp duty, GST on the fee components -- read
live from kitelab.backtest so this cannot drift from the engine. Brokerage is
0.0 for delivery. The Rs15.34 per-scrip demat fee is deliberately EXCLUDED:
_hold is scale-free (Rs1 per member), so a fixed rupee fee has no meaning in
it, and pretending otherwise would let an arbitrary account size drive the
answer. Spread is off by default in the engine (slippage.ENABLED = False), so
the default board is symmetric on spread; the sweep below prices it anyway,
because the `fill` grid axis can turn it on.

Reads:  /data/clean/kitelab/dashboard.json, output/wf_excess_<built date>.csv
Writes: output/wf_cost_gap_<built date>.csv
Runs in seconds. No prices, no rebuild, no network.
"""
import csv
import json
import statistics
from pathlib import Path

import pandas as pd

from kitelab import backtest
from kitelab.config import CLEAN

OUT = Path(__file__).resolve().parent.parent / "output"

# Extra ROUND-TRIP spread charged on top of the fee schedule, as a fraction of
# position value. 0 is the default board (slippage.ENABLED = False). 0.006 is
# two crossings of a 30bp half-spread -- slippage.SPREAD_K at Rs1cr of daily
# traded value, i.e. a liquid name. 0.02 is a deliberately punitive stress.
SPREAD_SWEEP = [0.0, 0.006, 0.02]


def fee_rates() -> tuple[float, float]:
    """(buy-side, sell-side) proportional cost, read from the live schedule.

    backtest.charges() takes rupee values, so price a Rs1 buy and a Rs1 sell
    separately and subtract the fixed demat fee, which this model excludes.
    """
    dp = backtest.DP_PER_SELL          # charges() adds it on every delivery call
    buy = backtest.charges(1.0, 0.0, intraday=False) - dp
    sell = backtest.charges(0.0, 1.0, intraday=False) - dp
    if not (0 < buy < 0.01 and 0 < sell < 0.01):
        raise SystemExit(f"implausible fee rates: buy {buy}, sell {sell} -- "
                         f"has the schedule in kitelab/backtest.py changed shape?")
    return buy, sell


def retention(buy: float, sell: float, spread: float) -> float:
    """Fraction of gross wealth left after one round trip."""
    half = spread / 2.0
    return (1.0 - sell - half) / (1.0 + buy + half)


def net_cagr(gross: float, years: float, k: float) -> float:
    return ((1.0 + gross / 100.0) * k ** (1.0 / years) - 1.0) * 100.0


def load_windows(d: dict) -> list[dict]:
    """Every comparable (universe, window) with its hold CAGR and span.

    Hold depends only on the universe and the window, so it repeats across all
    190 cells; take it once per (universe, window) and assert it agrees.
    """
    seen, rows = {}, []
    for strat, rec in d["validation"].items():
        for scen, wf in (rec.get("walk_forward_by_scenario") or {}).items():
            uni = scen.split("|")[0]
            for w in wf.get("windows") or []:
                if w.get("hold") is None or w.get("partial"):
                    continue
                key = (uni, w["from"])
                if key in seen:
                    if abs(seen[key]["hold"] - w["hold"]) > 1e-9:
                        raise SystemExit(f"hold disagrees at {key}: "
                                         f"{seen[key]['hold']} vs {w['hold']}")
                    continue
                years = (pd.Timestamp(w["to"]) - pd.Timestamp(w["from"])).days / 365.25
                if years <= 0:
                    raise SystemExit(f"non-positive span at {key}")
                seen[key] = {"universe": uni, "window": w["from"], "hold": w["hold"],
                             "years": years}
    rows = sorted(seen.values(), key=lambda r: (r["universe"], r["window"]))
    print(f"comparable (universe, window) pairs: {len(rows)}, {len(rows[0])} cols")
    for r in rows[:3]:
        print(f"  {r['universe']:<7}{r['window'][:10]}  hold {r['hold']:>7.2f}%  "
              f"{r['years']:.2f} yr")
    return rows


def load_margins(stamp: str) -> list[float]:
    path = OUT / f"wf_excess_{stamp}.csv"
    if not path.exists():
        raise SystemExit(f"No {path.name}. Run: python3 -m scripts.wf_excess")
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    vals = [float(r["mean_excess"]) for r in rows if r["mean_excess"]]
    print(f"\nwf_excess CSV: {len(rows)} cells, {len(vals)} with a mean margin")
    return vals


def main() -> None:
    path = CLEAN / "dashboard.json"
    if not path.exists():
        raise SystemExit(f"No dashboard.json at {path}. Build it first: python3 -m scripts.refresh")
    d = json.loads(path.read_bytes())
    stamp = d["built"].split()[0]
    print(f"dashboard.json: built {d['built']}")

    buy, sell = fee_rates()
    print(f"\nZerodha DELIVERY schedule, live from kitelab.backtest:")
    print(f"  buy-side  {buy*100:.4f}% of value")
    print(f"  sell-side {sell*100:.4f}% of value (demat Rs{backtest.DP_PER_SELL} excluded: "
          f"the benchmark is scale-free)")
    print(f"  brokerage {backtest.BROKERAGE} (delivery), slippage.ENABLED False by default")

    windows = load_windows(d)
    margins = load_margins(stamp)
    med_margin = statistics.median(margins)

    print(f"\n{'round trip':>12}{'retained':>10}{'median hold':>13}{'median hold':>13}"
          f"{'benchmark':>12}")
    print(f"{'spread':>12}{'of wealth':>10}{'GROSS %/yr':>13}{'NET %/yr':>13}"
          f"{'overstated by':>12}")
    rows = []
    for spread in SPREAD_SWEEP:
        k = retention(buy, sell, spread)
        deltas = [net_cagr(w["hold"], w["years"], k) - w["hold"] for w in windows]
        gross = statistics.median(w["hold"] for w in windows)
        med_delta = statistics.median(deltas)
        worst = min(deltas)
        print(f"{spread*100:>11.1f}%{k*100:>9.3f}%{gross:>13.2f}"
              f"{gross + med_delta:>13.2f}{med_delta:>12.3f}")
        rows.append({"round_trip_spread_pct": round(spread * 100, 2),
                     "retention": round(k, 6),
                     "median_hold_gross": round(gross, 3),
                     "median_hold_net": round(gross + med_delta, 3),
                     "median_overstatement_pts": round(med_delta, 4),
                     "worst_window_overstatement_pts": round(worst, 4),
                     "median_cell_margin_before": round(med_margin, 3),
                     "median_cell_margin_after": round(med_margin - med_delta, 3),
                     "share_of_gap_explained_pct": round(100 * abs(med_delta) / abs(med_margin), 3)})

    print(f"\nEvery margin on the board is biased against the rule by the last column.")
    print(f"Median cell margin as published: {med_margin:.2f} pts/yr")
    for r in rows:
        print(f"  at {r['round_trip_spread_pct']:.1f}% round-trip spread -> "
              f"{r['median_cell_margin_after']:.2f} pts/yr "
              f"({r['share_of_gap_explained_pct']:.2f}% of the gap explained)")

    OUT.mkdir(exist_ok=True)
    out = OUT / f"wf_cost_gap_{stamp}.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()

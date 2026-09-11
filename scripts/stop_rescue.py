"""How many of the board's winners are alive only because stops check the CLOSE?

    python -m scripts.stop_rescue

scripts/stops.py (run board-wide 2026-09-10) showed that the 90th-percentile
winner dips 1.23R -- PAST its own stop -- and lives. It lives because
backtest.simulate checks stops at the closing price (stop_on_close=True): a
stock that traded down through the stop at midday but closed above it is never
sold. A real account leaves a RESTING STOP ORDER with the broker, which fires
the moment price touches the level. Those trades would have been losses.

This script puts a number on that, per rule:

  * how many winners had MAE > 1.00R, i.e. would have been stopped out intraday;
  * what share of the rule's total net profit those trades carry;
  * a FIRST-ORDER estimate of the damage: each such winner becomes a -1R loss
    instead, so the ledger swings by (its net profit + its risk taken).

THE ESTIMATE IS A LEDGER ARITHMETIC, NOT A RE-SIMULATION. Stopping a trade out
in March frees its capital, which would have changed which later trades were
taken and how the account compounded. The real effect needs a rebuild with
stop_on_close=False. What this bounds is the size of the question.

Also reports how losers left, and that column KILLED A CLAIM I made on
2026-09-10 before measuring it. I said the losers' median MAE of 1.00R was
definitional -- a loser being, I assumed, a trade that hit its stop. It is not.
Only 32.8% of losers exit on a stop board-wide; the rest leave on the rule's
own signal (eath|MWD: 22,332 'ema break' against 9,154 'stop (close)'). Nor
does the distribution pile on the stop: exactly 1.000R holds 2.1% of eath|MWD
losers and 1.3% of pair|MW's, and the medians straddle it (1.0310, 0.9220).
So the winner-vs-loser MAE gap -- 0.38R against 1.00R -- is a real measurement,
not an identity, and the winners genuinely never approach their stops.

Reads:  the 19 *_all.pkl signal caches and the daily parquet candles.
Writes: output/measurements/stop_rescue_2026-09-10.csv
"""
from __future__ import annotations

import csv
import os

from kitelab import config, excursion, frames, registry, signals

# Anchored on the repo, not the shell's cwd -- every other measurement script
# here resolves output/ the same way (see the CLAUDE.md "output/ is flat" trap).
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "output", "measurements", "stop_rescue_2026-09-10.csv")
REQUIRED = ("symbol", "entry_price", "stop", "entry_ts", "exit_ts",
            "net_profit", "risk_taken", "exit_reason")


def check(trades: list[dict], label: str) -> None:
    """Stop with a clear error if the cache is not shaped the way we assume."""
    missing = [k for k in REQUIRED if k not in trades[0]]
    if missing:
        raise SystemExit(f"{label}: cached trades lack {missing} -- "
                         "the cache format moved, do not trust this run")
    bad = [k for k in REQUIRED for t in trades[:2000] if t.get(k) is None]
    if bad:
        raise SystemExit(f"{label}: None in {sorted(set(bad))} -- unexpected")


def measure(trades: list[dict]) -> list[dict]:
    """One row per measurable trade: its excursion plus what it earned."""
    out = []
    by_symbol: dict[str, list[dict]] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    for symbol, group in by_symbol.items():
        try:
            daily = frames.daily(symbol)
        except SystemExit:
            continue
        for t in group:
            got = excursion.excursions(t, daily)
            if got is None:
                continue
            got["net"] = float(t["net_profit"])
            got["risk"] = float(t["risk_taken"])
            got["won"] = got["net"] > 0
            got["exit_reason"] = t["exit_reason"]
            out.append(got)
    return out


def main() -> None:
    cfg = config.load()
    universe = cfg.merged
    print(f"\n  {len(universe)} stocks, {len(registry.REGISTRY)} rules.")
    print("  MAE is in R -- multiples of the trade's own risk. A winner with")
    print("  MAE > 1.00R traded below its stop and survived on the close check.\n")

    head = (f"  {'rule':<34}{'winners':>9}{'rescued':>9}{'%':>7}"
            f"{'their net Rs':>15}{'rule net Rs':>15}{'% of net':>10}"
            f"{'swing Rs':>15}")
    print(head)
    print("  " + "-" * (len(head) - 2))

    rows, totals = [], {"win": 0, "resc": 0, "resc_net": 0.0,
                        "net": 0.0, "swing": 0.0, "trades": 0,
                        "lost": 0, "lost_stop": 0}

    for strat in registry.REGISTRY:
        trades = signals.load(f"{strat.cache}_all", universe)
        if not trades:
            print(f"  {strat.label:<34}  no cache -- run scripts.refresh first")
            continue
        check(trades, strat.label)
        n_cached = len(trades)
        got = measure(trades)
        if not got:
            continue

        winners = [r for r in got if r["won"]]
        losers = [r for r in got if not r["won"]]
        rescued = [r for r in winners if r["mae_r"] > 1.0]
        lost_stop = [r for r in losers if "stop" in str(r["exit_reason"])]

        resc_net = sum(r["net"] for r in rescued)
        rule_net = sum(r["net"] for r in got)
        # Each rescued winner becomes a -1R loss: the ledger moves by what it
        # earned plus the loss it would have taken instead.
        swing = sum(r["net"] + r["risk"] for r in rescued)

        pct = 100.0 * len(rescued) / len(winners) if winners else 0.0
        share = 100.0 * resc_net / rule_net if rule_net else float("nan")
        print(f"  {strat.label:<34}{len(winners):>9}{len(rescued):>9}{pct:>7.1f}"
              f"{resc_net:>15,.0f}{rule_net:>15,.0f}{share:>10.1f}"
              f"{-swing:>15,.0f}")

        rows.append({"rule": strat.label, "key": strat.key,
                     "variant": strat.variant,
                     "cached_trades": n_cached, "measured": len(got),
                     "winners": len(winners), "losers": len(losers),
                     "losers_exited_on_stop": len(lost_stop),
                     "rescued_winners": len(rescued),
                     "rescued_pct_of_winners": round(pct, 2),
                     "rescued_net_rs": round(resc_net, 0),
                     "rule_net_rs": round(rule_net, 0),
                     "rescued_share_of_net_pct": round(share, 2),
                     "first_order_swing_rs": round(-swing, 0)})

        totals["trades"] += len(got)
        totals["win"] += len(winners)
        totals["resc"] += len(rescued)
        totals["resc_net"] += resc_net
        totals["net"] += rule_net
        totals["swing"] += swing
        totals["lost"] += len(losers)
        totals["lost_stop"] += len(lost_stop)

    print("  " + "-" * (len(head) - 2))
    pct = 100.0 * totals["resc"] / totals["win"] if totals["win"] else 0.0
    share = 100.0 * totals["resc_net"] / totals["net"] if totals["net"] else 0.0
    print(f"  {'ALL 19 POOLED':<34}{totals['win']:>9}{totals['resc']:>9}{pct:>7.1f}"
          f"{totals['resc_net']:>15,.0f}{totals['net']:>15,.0f}{share:>10.1f}"
          f"{-totals['swing']:>15,.0f}")

    ls = 100.0 * totals["lost_stop"] / totals["lost"] if totals["lost"] else 0.0
    print(f"\n  {totals['trades']:,} trades measured. "
          f"{totals['lost']:,} losers, of which {ls:.1f}% left on the stop "
          "rather than a signal.")
    print("  The swing column is ledger arithmetic on a fixed trade list, NOT a")
    print("  re-simulation: freeing that capital earlier would change which later")
    print("  trades were taken. It sizes the question, it does not answer it.\n")

    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {OUT} ({len(rows)} rows)\n")


if __name__ == "__main__":
    main()

"""RASCHKE AT THE ACCOUNT LAYER: the last step her setups have not survived.

`scripts/raschke_rules.py` scored the ten Street Smarts setups per TRADE and
found 10 of 80 rows clearing a computed Bonferroni, all positive. That is not
a result yet. Project memory (kitelab-account-layer-reverses-stop-finding) says
a trade-level number never survives the account, and `scripts/waterfall.py`
showed exactly why: our own entries score in the SAME +35 to +100 bps band and
still lose to buy-and-hold by ten points a year once an account has to hold
them -- because the edge was never the entry, it was the stop.

So this runs her setups through the identical nine-rung waterfall the board's
own rules go through, at MAX HOLD 6, which is the horizon her book actually
trades. The four setups that cleared Bonferroni at hold 6 are the ones on
trial: NSE anti (both stops), US tsoup1 (atr3), US whiplash (own). The other
six run alongside so nothing is selected by score.

The number that matters is "rule minus buy and hold". Everything before it is
diagnosis.

READS:  CLEAN/US_<SYM>_day.parquet and CLEAN/<SYM>_day.parquet
WRITES: output/measurements/raschke_account_<date>.csv
        output/figures/raschke_account_<date>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib                                              # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                # noqa: E402

from scripts import raschke_rules, us_rules                    # noqa: E402
from scripts import waterfall as wf                            # noqa: E402
from scripts.us_rules import us_universe, nse_universe         # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output"
HOLD = 6                 # her horizon, not ours


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setups", nargs="*", default=sorted(raschke_rules.RULES))
    ap.add_argument("--capital", type=float, default=1e7)
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--hold", type=int, default=HOLD)
    # Her setups are scored through the waterfall's account, so they inherit its
    # 1% fill cap -- and that cap shipped reading the fill bar's OWN turnover.
    # Every NSE number she posts rests on it, so it has to be switchable here
    # too or the re-test is not a re-test.
    ap.add_argument("--caplag", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()

    bad = [s for s in args.setups if s not in raschke_rules.RULES]
    if bad:
        sys.exit(f"not Street Smarts setups: {bad}")

    # The three patches, and only these. Her names resolve through her own
    # dispatcher, which raises on anything else, so a typo cannot quietly score
    # one of the board's rules under her name. MAXHOLD is a module global in
    # both engines and _exit_from reads the us_rules one.
    wf.fires_for = raschke_rules.fires_for
    wf.MAXHOLD = args.hold
    us_rules.MAXHOLD = args.hold
    wf.CAP_LAG = args.caplag
    wf.PANEL = []                      # none of her setups is cross-sectional
    print(f"setups: {args.setups}")
    print(f"max hold: {args.hold} sessions (her horizon); "
          f"stops: {list(wf.STOPS)}")

    books = {"US": us_universe(), "NSE": nse_universe(144)}
    if args.pilot:
        books = {k: v[:args.pilot] for k, v in books.items()}
    out = pd.concat([wf.run_universe(t, s, args.setups, args.capital, t0)
                     for t, s in books.items()], ignore_index=True)
    print(f"\nrows: {len(out)}")

    stamp = date.today().isoformat()
    if args.caplag:
        stamp += f"_caplag{args.caplag}"
    (OUT / "measurements").mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "measurements" / f"raschke_account_{stamp}.csv", index=False)

    key = ["universe", "family", "stop"]
    piv = out.pivot_table(index=key, columns="rung", values="cagr_pct")
    slots_hi = f"D0 rule,   + costs, {wf.SLOTS[0]:>2} slots"
    rand_hi = f"E  random, + costs, {wf.SLOTS[0]:>2} slots"
    res = pd.DataFrame({
        "vs_hold": piv[slots_hi] - piv["F  buy and hold"],
        "vs_matched_random": piv[slots_hi] - piv[rand_hi],
        "entry_worth_no_stop": (piv["X  rule,   no cost, NO STOP"]
                                - piv["A0 random, no cost, NO STOP"]),
        "cagr": piv[slots_hi],
        "hold": piv["F  buy and hold"],
    }).reset_index()
    res.to_csv(OUT / "measurements" / f"raschke_account_steps_{stamp}.csv",
               index=False)

    # The four rows on trial, named before the run so the reader can find them.
    ON_TRIAL = {("NSE", "anti", "own"), ("NSE", "anti", "atr3"),
                ("US", "tsoup1", "atr3"), ("US", "whiplash", "own")}
    res["on_trial"] = [tuple(r) in ON_TRIAL
                       for r in res[key].itertuples(index=False)]

    for tag in books:
        s_ = res[res.universe == tag].sort_values("vs_hold", ascending=False)
        print(f"\n=== {tag}: hold {args.hold}, account layer "
              f"(buy and hold {s_.hold.median():.2f}%/yr) ===")
        print(f"  {'setup':<10} {'stop':<5} {'CAGR':>7} {'vs hold':>9} "
              f"{'vs random':>10} {'entry, no stop':>15}  trial")
        for r in s_.itertuples(index=False):
            print(f"  {r.family:<10} {r.stop:<5} {r.cagr:>7.2f} "
                  f"{r.vs_hold:>+9.2f} {r.vs_matched_random:>+10.2f} "
                  f"{r.entry_worth_no_stop:>+15.2f}  "
                  f"{'<-- ON TRIAL' if r.on_trial else ''}")
        n = len(s_)
        print(f"  beat buy and hold: {(s_.vs_hold > 0).sum()} of {n}")
        t = s_[s_.on_trial]
        if len(t):
            print(f"  of the {len(t)} on trial: "
                  f"{(t.vs_hold > 0).sum()} beat hold, median {t.vs_hold.median():+.2f}")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax, tag in zip(axes, books):
        s_ = res[res.universe == tag].sort_values("vs_hold")
        lab = [f"{r.family}|{r.stop}" for r in s_.itertuples(index=False)]
        col = ["crimson" if o else "steelblue" for o in s_.on_trial]
        ax.barh(range(len(s_)), s_.vs_hold.to_numpy(), color=col)
        ax.set_yticks(range(len(s_)))
        ax.set_yticklabels(lab, fontsize=7)
        ax.axvline(0, color="black", lw=1)
        ax.set_title(f"{tag}: Raschke minus buy and hold, hold {args.hold}")
        ax.set_xlabel("points of CAGR per year")
        ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    png = OUT / "figures" / f"raschke_account_{stamp}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}   (red = named on trial before the run)")
    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()

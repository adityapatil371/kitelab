"""Can the walk-forward gate detect a rule that is genuinely good?
(off-board diagnostic, 2026-09-08)

Every argument about the gate's threshold so far has been intuition. This
measures it. A gate is an instrument; an instrument is judged by two numbers:

  FALSE POSITIVE  how often it passes a rule with NO edge at all;
  POWER           how often it passes a rule with a real edge of a given size.

The current gate is "wins * 2 > total_windows", i.e. at least 4 of 7 windows
beat equal-weight hold. As a hypothesis test that is a sign test whose
critical value sits at the median of its own null: under a coin-flip null,
P(X >= 4) = 64/128 = exactly 0.5. So it is expected to pass HALF of all
edgeless rules. That is not a 5% test, and this script confirms it by
simulation rather than asserting it.

Method. For a true edge of `delta` percentage points of CAGR per year, draw
7 independent window margins from Normal(delta, sigma^2) and apply each test.
`sigma` is not invented: it is the median within-cell standard deviation of
the real margins measured by scripts.wf_excess, so the simulated noise matches
the board's own window-to-window variability.

Three designs are compared on identical draws:
  GATE       wins * 2 > n              (the current rule)
  SIGNFLIP   exact sign-flip test, one-sided, alpha 0.05
  TTEST      one-sided paired t-test, alpha 0.05

Independence across windows is an assumption, and an optimistic one -- real
windows share a market factor, so true power is somewhat worse than reported.

Reads:  output/wf_excess_<built date>.csv, /data/clean/kitelab/dashboard.json
Writes: output/wf_power_<built date>.csv
Runs in seconds. No prices, no rebuild, no network.
"""
import csv
import itertools
import json
import math
import random
import statistics
from pathlib import Path

from kitelab.config import CLEAN

OUT = Path(__file__).resolve().parent.parent / "output"
ROUNDS = 20_000              # draws per (delta, design); 20k gives ~+-0.7% on a rate
WINDOWS = 7                  # the gate's calendar, for universes with a full history
ALPHA = 0.05
DELTAS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30]
SEED = 20260908


def built_stamp() -> str:
    path = CLEAN / "dashboard.json"
    if not path.exists():
        raise SystemExit(f"No dashboard.json at {path}. Build it first: python3 -m scripts.refresh")
    return json.loads(path.read_bytes())["built"].split()[0]


def sigma_from_board(stamp: str) -> float:
    """Median within-cell SD of the real per-window margins."""
    path = OUT / f"wf_excess_{stamp}.csv"
    if not path.exists():
        raise SystemExit(f"No {path.name}. Run: python3 -m scripts.wf_excess")
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    print(f"wf_excess CSV: {len(rows)} rows, {len(rows[0])} cols")
    for r in rows[:3]:
        print(f"  {r['universe']:<7}{r['strategy']:<16}sd={r['sd_excess']:<7}"
              f"mean={r['mean_excess']}")
    full = [float(r["sd_excess"]) for r in rows
            if r["sd_excess"] and int(r["windows"]) == WINDOWS]
    if not full:
        raise SystemExit(f"no cells with {WINDOWS} comparable windows -- cannot set sigma")
    print(f"\ncells with a full {WINDOWS}-window history: {len(full)} of {len(rows)}")
    s = sorted(full)
    med = statistics.median(s)
    print(f"within-cell SD of margin: min {s[0]:.1f}, median {med:.1f}, max {s[-1]:.1f} pts/yr")
    return med


# ---- the three designs, each returning True for "passes" -------------------

def gate(draw: list[float]) -> bool:
    """The board's rule: a strict majority of windows beat hold."""
    return sum(1 for x in draw if x > 0) * 2 > len(draw)


_PATTERNS = list(itertools.product((-1, 1), repeat=WINDOWS))


def signflip(draw: list[float]) -> bool:
    """Exact one-sided randomization test at ALPHA, H1: mean margin > 0."""
    mags = [abs(x) for x in draw]
    obs = sum(draw)
    atleast = sum(1 for signs in _PATTERNS
                  if sum(s * m for s, m in zip(signs, mags)) >= obs - 1e-12)
    return atleast / len(_PATTERNS) <= ALPHA


# One-sided t critical values, df = n - 1, alpha = 0.05.
T_CRIT = {6: 1.943, 5: 2.015, 4: 2.132, 3: 2.353, 2: 2.920}


def ttest(draw: list[float]) -> bool:
    n = len(draw)
    sd = statistics.stdev(draw)
    if sd == 0:
        return statistics.fmean(draw) > 0
    t = statistics.fmean(draw) / (sd / math.sqrt(n))
    return t >= T_CRIT[n - 1]


DESIGNS = [("GATE", gate), ("SIGNFLIP", signflip), ("TTEST", ttest)]


def main() -> None:
    stamp = built_stamp()
    sigma = sigma_from_board(stamp)
    rng = random.Random(SEED)

    print(f"\nsimulating {ROUNDS:,} rules per edge size, {WINDOWS} windows each, "
          f"sigma = {sigma:.1f} pts/yr")
    print(f"\n{'true edge':>10}  {'GATE':>8}  {'SIGNFLIP':>9}  {'TTEST':>7}")
    print(f"{'(pts/yr)':>10}  {'pass %':>8}  {'pass %':>9}  {'pass %':>7}")
    rows = []
    for delta in DELTAS:
        hits = {name: 0 for name, _ in DESIGNS}
        for _ in range(ROUNDS):
            draw = [rng.gauss(delta, sigma) for _ in range(WINDOWS)]
            for name, fn in DESIGNS:
                if fn(draw):
                    hits[name] += 1
        rate = {k: 100 * v / ROUNDS for k, v in hits.items()}
        tag = "  <- no edge at all" if delta == 0 else ""
        print(f"{delta:>10}  {rate['GATE']:>7.1f}%  {rate['SIGNFLIP']:>8.1f}%  "
              f"{rate['TTEST']:>6.1f}%{tag}")
        rows.append({"edge_pts_per_year": delta, "sigma": round(sigma, 2),
                     "windows": WINDOWS, "rounds": ROUNDS,
                     "gate_pass_pct": round(rate["GATE"], 2),
                     "signflip_pass_pct": round(rate["SIGNFLIP"], 2),
                     "ttest_pass_pct": round(rate["TTEST"], 2)})

    print("\nreading the table:")
    zero = rows[0]
    print(f"  at zero edge the GATE still passes {zero['gate_pass_pct']:.0f}% of rules "
          f"-- it is a sign test whose cutoff sits at its own null median.")
    for name, key in [("GATE", "gate_pass_pct"), ("SIGNFLIP", "signflip_pass_pct"),
                      ("TTEST", "ttest_pass_pct")]:
        need = next((r["edge_pts_per_year"] for r in rows if r[key] >= 80), None)
        print(f"  {name:<9} reaches 80% power at an edge of "
              + (f"{need} pts/yr" if need else f"more than {DELTAS[-1]} pts/yr"))

    path = OUT / f"wf_power_{stamp}.csv"
    OUT.mkdir(exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

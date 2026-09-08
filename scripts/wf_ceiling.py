"""Is the walk-forward gate passable at all? (off-board diagnostic, 2026-09-08)

Reads the built dashboard.json and asks, per universe, three questions:

  1. THE BAR      -- what equal-weight hold returned in each of the 7 fixed
                     windows. This is what a rule must beat to win one.
  2. ACHIEVED     -- the best win-count any real cell managed (190 per
                     universe: 19 strategies x 5 priorities x 2 capitals).
  3. THE CEILING  -- a hindsight-perfect SELECTOR: in each window
                     independently, take the best CAGR any of the 190 cells
                     achieved, and score that. No real strategy can do
                     better, because no real strategy gets to know which of
                     the 190 will win each window before the window starts.

If the ceiling cannot win a strict majority (4 of 7), the gate is not
measuring skill -- it is unpassable by construction, and a walk-forward
failure says nothing about a strategy.

Reads:  /data/clean/kitelab/dashboard.json (via config._resolve)
Writes: output/wf_ceiling_<built date>.csv
Runs in seconds. No prices, no rebuild, no network.
"""
import csv
import json
import statistics
from pathlib import Path

from kitelab.config import CLEAN

# A window counts only when BOTH sides are known and it is not partial --
# walk_forward()'s own rule. `recent` has 3 comparable windows, not 7, because
# its stocks did not exist before 2018, so the majority it must clear is 2.
def majority_of(n: int) -> int:
    """Smallest wins with wins * 2 > n -- the gate's strict majority."""
    return n // 2 + 1


def _round1(x):
    return None if x is None else round(x, 1)


def _win(cagr, hold):
    """The gate's own comparison: one-decimal figures, a tie is not a win."""
    if cagr is None or hold is None:
        return None
    return _round1(cagr) > _round1(hold)


def load_dashboard() -> dict:
    path = CLEAN / "dashboard.json"
    if not path.exists():
        raise SystemExit(f"No dashboard.json at {path}. Build it first: python3 -m scripts.refresh")
    d = json.loads(path.read_bytes())
    for key in ("validation", "grid", "universes", "built"):
        if key not in d:
            raise SystemExit(f"dashboard.json is missing required key {key!r}")
    print(f"dashboard.json: built {d['built']}, "
          f"{len(d['validation'])} strategies, {len(d['grid'])} grid cells")
    return d


def cells_for(d: dict, universe: str) -> list[dict]:
    """Every (strategy, priority, capital) walk-forward record in `universe`."""
    out = []
    for strat, rec in d["validation"].items():
        for scen, wf in (rec.get("walk_forward_by_scenario") or {}).items():
            if scen.split("|")[0] != universe:
                continue
            out.append({"strategy": strat, "scenario": scen,
                        "wins": wf.get("wins"), "total": wf.get("total_windows"),
                        "windows": wf.get("windows") or []})
    return out


def window_labels(cells: list[dict]) -> list[str]:
    """The shared calendar, taken from the data rather than reconstructed."""
    labels = [w["from"] for w in cells[0]["windows"]]
    for c in cells:
        got = [w["from"] for w in c["windows"]]
        if got != labels:
            raise SystemExit(f"{c['strategy']} {c['scenario']} has a different calendar: {got}")
    return labels


def hold_bar(cells: list[dict], labels: list[str]) -> dict:
    """Hold CAGR per window. Asserted identical across every cell in the
    universe -- it depends only on the universe's members, so a disagreement
    would mean the benchmark is not what this diagnostic assumes."""
    bar, partial = {}, {}
    for c in cells:
        for w in c["windows"]:
            seen = bar.setdefault(w["from"], w["hold"])
            if seen != w["hold"]:
                raise SystemExit(f"hold disagrees in {w['from']}: {seen} vs {w['hold']} "
                                 f"({c['strategy']} {c['scenario']})")
            partial[w["from"]] = w["partial"]
    return {lab: (bar[lab], partial[lab]) for lab in labels}


def ceiling(cells: list[dict], labels: list[str]) -> dict:
    """Per window: the best CAGR across all cells, and who scored it."""
    best = {}
    for lab in labels:
        top, who, beat = None, None, 0
        for c in cells:
            w = next(x for x in c["windows"] if x["from"] == lab)
            if w["cagr"] is None:
                continue
            if _win(w["cagr"], w["hold"]):
                beat += 1
            if top is None or w["cagr"] > top:
                top, who = w["cagr"], f"{c['strategy']} {c['scenario']}"
        best[lab] = {"cagr": top, "who": who, "cells_beating_hold": beat}
    return best


def exposure_rows(d: dict, universe: str, start: int = 2018) -> list[dict]:
    """Grid cells for this universe at `start`, for the cash-drag context."""
    out = []
    for key, cell in d["grid"].items():
        parts = key.split("|")
        if len(parts) != 8:
            continue
        if parts[2] != universe or parts[6] != str(start):
            continue
        if cell.get("exposure") is None:
            continue
        out.append(cell)
    return out


def report(d: dict, universe: str, writer) -> None:
    cells = cells_for(d, universe)
    print(f"\n{'='*72}\nUNIVERSE {universe!r}: {len(cells)} scenario-cells")
    if not cells:
        print("  no walk-forward records -- skipped")
        return
    labels = window_labels(cells)
    bar = hold_bar(cells, labels)
    top = ceiling(cells, labels)

    # Comparable = a benchmark exists and the window is not partial. A window
    # with no hold (no member of this universe traded then) is not a loss, it
    # is not a window at all -- counting it as one is what made an earlier
    # version of this script call `recent` unpassable while 22 cells passed it.
    counted = [lab for lab in labels if not bar[lab][1] and bar[lab][0] is not None]
    need = majority_of(len(counted))
    print(f"  {len(labels)} windows, {len(counted)} comparable "
          f"(no benchmark = not a window; partial = shown, not counted)")

    print(f"\n  {'window':<12}{'hold %':>9}{'best cell %':>13}{'oracle win':>12}"
          f"{'cells beating hold':>21}")
    oracle_wins = 0
    for lab in labels:
        h, is_partial = bar[lab]
        t = top[lab]
        win = _win(t["cagr"], h)
        if win and lab in counted:
            oracle_wins += 1
        print(f"  {lab:<12}{_fmt(h):>9}{_fmt(t['cagr']):>13}"
              f"{('YES' if win else 'no') if win is not None else '-':>12}"
              f"{t['cells_beating_hold']:>21}")
        writer.writerow({"universe": universe, "window": lab, "partial": is_partial,
                         "hold_cagr": h, "ceiling_cagr": t["cagr"],
                         "ceiling_source": t["who"], "ceiling_wins": win,
                         "cells_beating_hold": t["cells_beating_hold"],
                         "cells_total": len(cells)})

    best_real = max((c["wins"] or 0) for c in cells)
    winners = [c for c in cells if (c["wins"] or 0) * 2 > (c["total"] or 0)]
    easy = [lab for lab in counted if (bar[lab][0] or 0) < 15]

    print(f"\n  THE BAR      : {len(easy)} of {len(counted)} comparable windows have a hold bar "
          f"under 15%/yr ({', '.join(easy) or 'none'})")
    print(f"  ACHIEVED     : best real cell won {best_real} of {len(counted)}; "
          f"{len(winners)} of {len(cells)} cells pass the strict majority")
    print(f"  THE CEILING  : the hindsight-perfect selector wins {oracle_wins} of {len(counted)} "
          f"-- needs {need} to pass")
    # A window no cell on the board ever won is unwinnable in practice: the
    # most any single rule can score is the number of winnable windows, and
    # the slack against the majority is what a passing rule may drop.
    winnable = [lab for lab in counted if top[lab]["cells_beating_hold"] > 0]
    slack = len(winnable) - need
    verdict = ("PASSABLE (the ceiling clears the majority)" if oracle_wins >= need
               else "UNPASSABLE BY CONSTRUCTION (even the ceiling cannot reach a majority)")
    print(f"  SLACK        : {len(winnable)} of {len(counted)} windows were won by at least one "
          f"cell; a rule needs {need}, so it may drop {slack} of them")
    print(f"  VERDICT      : {verdict}")

    ex = exposure_rows(d, universe)
    if ex:
        med_exp = statistics.median(c["exposure"] for c in ex)
        cash = [c["median_cash"] for c in ex if c.get("median_cash") is not None]
        med_cash = statistics.median(cash) if cash else None
        # Cash drag was the suspected cause (a partly-invested rule cannot beat
        # an always-invested benchmark). Measured 2026-09-08, it is not: these
        # accounts sit in cash ~0% of the time. The shortfall is which stocks
        # are held and when, not money left idle.
        print(f"\n  Cash drag (start 2018, {len(ex)} grid cells): median exposure "
              f"{med_exp:.1f}% of days invested"
              + (f", median {med_cash:.1f}% of the account in cash" if med_cash is not None else ""))
        if med_cash is not None:
            print("  -> cash drag " + ("RULED OUT as the cause: the account is effectively "
                                       "fully invested." if med_cash < 5 else
                                       "is material and worth modelling."))


def _fmt(v):
    return "—" if v is None else f"{v:.1f}"


def main() -> None:
    d = load_dashboard()
    out = Path(__file__).resolve().parent.parent / "output"
    out.mkdir(exist_ok=True)
    stamp = d["built"].split()[0]
    path = out / f"wf_ceiling_{stamp}.csv"
    fields = ["universe", "window", "partial", "hold_cagr", "ceiling_cagr",
              "ceiling_source", "ceiling_wins", "cells_beating_hold", "cells_total"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for universe in d["universes"]:
            report(d, universe, writer)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()

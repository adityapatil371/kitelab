---
name: kitelab-validation
description: kitelab validation deep reference — the five Validated checks, credibility t/drift_r/luck hurdle, holdout stand-ins, adding a strategy. Load before changing validation.py or quoting significance.
---

# kitelab validation — deep reference

Relocated from CLAUDE.md on 2026-09-08. The map (where the math lives, gate
vs score) stays in CLAUDE.md; this is the detail. Rewritten 2026-09-07 after
the audit; every changed rule's docstring carries what it was, what it is, and
the measurement.

## The five checks, in full

1. **beats buy & hold** — the on-screen cell's CAGR (net of costs) against an
   **equal-weight portfolio** of the stocks in the on-screen universe from the
   on-screen start year (`hold_cagr_by_scenario["all|2018"]`; net of the
   proportional delivery charges since 2026-09-09, `_hold_retention`, worth
   0.09 pts/yr — it was gross before).
   Until 2026-09-07 this was one whole-history, all-universe, *median-stock*
   number (11.9) reused for every cell; the equal-weight portfolio from 2018 is
   ~17.0 (right-skewed stock returns, Jensen). Owner's decision: gate on raw
   CAGR, show exposure-matched hold in Detail as context only.
2. **distinguishable from shuffled prices** — 100 rounds over a seeded random
   60-stock pool, dates permuted *jointly* so the null keeps a market factor,
   `p = (worse + 1) / (rounds + 1) ≤ 0.05`. Was 10 rounds over the first 60
   stocks alphabetically with pass at p ≤ 0.2, which flipped with the seed.
3. **wins a strict majority of fixed 3-year calendar windows** (2006-09,
   2009-12, …) **against each window's own equal-weight hold**. Was "at least
   half the windows positive", a bar a bull market clears for anything. A
   trailing window under 2 years is shown, marked partial, and not counted.
4. **survives a cost margin** (breakeven bp, unchanged).
5. **clears the luck hurdle** — the drift-adjusted, cluster-robust t
   (`credibility.t_stat`) above `Φ⁻¹(1 − 0.05 / n_eff)`, a family-wise 95% bar
   (≈2.41 at n_eff 6.2). Was `E[max]` of the null (1.32), which an edgeless
   board's best rule clears 46% of the time; 19 of 19 cleared it.

## The credibility statistic

`t_iid` (mean R over std/√n) treated 40k–240k overlapping trades as
independent: pair|MW scored 20.3. `t_cluster` clusters the standard error by
entry quarter (Liang–Zeger): 4.1. `t_stat`, the gate, subtracts `drift_r` —
the mean R that *random* entries on the same stocks with the same stop
fraction, holding length and spread would earn — because these survivors drift
up and random timing earns that drift too. Read the sign of `drift_r`: rules
with tight daily stops get a *negative* drift (random timing with a 1% stop
loses to noise) so their t rises; rules with weekly stops get a positive one.
`t_taken` on every grid row is the same clustered t on the trades *that*
account actually took (~1.3 for the old headline rows at ₹2L). The percentiles
come from a calendar-quarter block bootstrap, not 20-trade blocks.

## Scope of each check, and small samples

Checks 1 and 3 recompute for the on-screen Universe × Priority × Capital (1
for Universe × start year); 2, 4 and 5 recompute for Universe only. Checks 2
and 4 **do** run `portfolio.run`, at ₹2L / 1% / most-liquid-first whatever is
on screen — the docstrings and tooltips say so. Below `MIN_TRADES = 30` every
function returns `None`/empty rather than raising; `preflight` lowers it to 5
and the permutation rounds to 10 so its 3-symbol build exercises every branch.

`validation_summary` carries the multiple-testing arithmetic (`tried`,
`n_eff`, `avg_correlation`, `alpha`, `hurdle`, `expected_best`,
`expected_by_chance`, `cleared`). Quote that spread, never a single headline
number. The hurdle guards the t-statistic only; the MAR the top row is sorted
by is the maximum over 540 cells per variant with no correction, on an axis
(priority) the project itself calls noise.

## Tests that stand in for the holdout

Practitioner controls, not train/test:

| question | where |
|---|---|
| is this edge distinguishable from luck on its own trades? | `scripts.bootstrap`; `t_stat` / `t_taken` on the page |
| is it just the survivors' drift? | `drift_r`, the random-entry null inside `bootstrap_one` |
| did 19 variants buy me a winner by chance? | the luck hurdle in `validation_summary` |
| did it beat simply owning the same stocks, from the same year? | `hold_cagr_by_scenario`, the vs-Hold column |
| does it hold in **disjoint** periods, against hold? | walk-forward, `scripts.validate` |
| does it hold across regimes? | `START_YEARS` axis on the grid |
| is a parameter a plateau or a lucky spike? | **nothing on the board**; `scripts.ath_band` runs three bands for its own rule |
| is the return one thin tail? | top-N deletion, `scripts.validate` |
| how much friction does it survive? | breakeven cost, `scripts.validate` |
| does the ordering rule matter? | the **Signal priority** axis on Compare |

## Adding a strategy

Append a `Strategy` to `kitelab/registry.py` (or call `register()`). Nothing
else needs editing: it appears in the compare grid, in `kitelab.validation`
and in `scripts.bootstrap` automatically, measured on the same stocks and the
same account as everything already there.

Each `Strategy` names **the module that produces its trades**, and
`signals.stamp()` derives the cache-code digest from that list. A rule that is
on the board is therefore in the stamp by construction. This is not
decoration: `holygrail.py` was missing from the old hand-kept `_CODE` list, so
a rewritten strategy went on serving superseded trades while `refresh` said
"already current", and a day of published numbers was invalid.

One thing the machinery cannot enforce: **every rule you add raises the bar
for all of them.** The luck hurdle counts how many variants clear a 95% test
against the ~5% that clear it by chance, so a 20th strategy makes "one of them
looks good" less impressive, not more. It also multiplies the grid — 540 new
cells per variant at the current axes.

**ATH stacks: `pair|QM` and `pair|QD` are controls, not strategies.** Every
ATH row needs its own unfiltered twin or its score cannot be read — credit is
unattributable between the filter and the stack under it. Do not add an ATH
stack without one. `tests/test_ath_filter.py` asserts the property that makes
the twin readable: every `eath` trade is a trade its control also takes.

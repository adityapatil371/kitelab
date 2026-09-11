# START HERE — kitelab open state

Last updated **2026-09-11, third session** (post-rebuild). Supersedes the 2026-09-09 note
that memory still points at (that file never existed on disk; this one does).

## The board as it stands

**Built 2026-09-11 20:44 IST from 9 strategies.** Code and payload agree;
`refresh --check` is quiet. The two cuts below are done, run and measured.

    9 strategies x 300 scenarios = 2,700 cells
      (5 universes x 5 start years x 3 priorities x 2 risks x 2 capitals)
    2,484 of them carry a daily-excess test; 216 are null BY DESIGN --
    see "The 216 nulls" below.

    ema|0          EMA · M/W/D              pair|MD     EMA · M/D
    dv|20-10       Turtle 20-10 + weekly    pair|MW     EMA · M/W
    dv|55-20       Turtle 55-20 + weekly    e1|daily    EMA · daily only
    dv|20-10 1TF   Turtle 20-10 (1 TF)      eath|MW     EMA · M/W within 10% of high
    dv|55-20 1TF   Turtle 55-20 (1 TF)

**Health:** 443 tests pass, pyflakes clean but for the known `refresh.py:69`,
`node scripts/check_dashboard.js` exits 0. The ATH control invariant holds
(`eath|MW`→`pair|MW`) and both `SELF_CHECK_KEY`s still name `ema|0`, which
survived the cut on purpose.

**Headline numbers — the 9-board, measured, quotable:**

    BH FDR 5% [THE GATE]              0 of 2,484
    beats hold, one-sided HAC p<=0.05 0        (~124 expected by chance alone)
    Bonferroni across 2,484 cells     0        (p <= 2.01e-05)
    next-open fills beat hold         85
    median detectable edge (80% power)  16.29 CAGR pts/yr
    seven-window gate would need        20.0  CAGR pts/yr
    cells with excess > 0             388 of 2,484
    smallest p                        0.0506   pair|MW|all|1|10000000|1|2018|mom_hi
    p <= 0.10                         12
    hold to beat  scenario-dependent: all|2006 14.0, all|2018 17.0,
                  median 15.7 over 25 universe x start-year scenarios
    validation_summary  tried 9, n_eff 4.0, hurdle 2.24, avg_correlation 0.48,
                        best dv|55-20 t=4.15, clears_hurdle True, cleared 6,
                        expected_best 1.06, expected_by_chance 0.5
    SELF-CHECK PASSED   ema|0|all|1|10000000|1|2018|mom_hi
                        cagr 0.4, final 10,337,864, taken 3497

The board is empty at the gate **by design**. That is the finding, not a bug.
See the memory note `kitelab-daily-excess-gate-2026-09-10`.

**The 13-board's 12 nominally-significant cells are all gone, and WHICH cells
they were is a deduction worth writing down.** Nothing in the daily-excess path
changed for a surviving rule — `excess_stats`, `portfolio.run` and
`validation.py` were untouched, the fix only SKIPS cells — so a surviving
cell's p-value is the same number it was on the 13-board. The 9-board's
smallest p is 0.0506, above 0.05. Therefore **all 12 cells that reached p≤0.05
belonged to the four rules cut for duplication** (`qmw|0`, `pair|QW`,
`pair|WD`, `eath|WD`), and three of those four were near-copies of each other
(`qmw|0`/`pair|QW` r = 0.963). The 12 "hits" were substantially one idea
counted several times. State the premise when quoting this: the 13-board
payload was overwritten, so it cannot be re-checked, only deduced.

**`n_eff` is 4.0 against `tried` 9.** The board is still less than half
independent after both cuts. `redundancy.py` said removal bottoms out near 10
strategies; this is the same fact from the validation side.

### The 216 nulls

A start year earlier than a rule's first trade produces the **identical**
backtest to the next year up — same trades, same curve, a duplicate under a
different label. 216 of the 2,700 cells (8%) were exactly that, all in
`recent` (whose members have no pre-2018 turnover, so 2006 and 2012 cut
nothing). They inflated the BH denominator — chances to be lucky — without
being independent tries. `dashboard_data.gridded_years` now writes them null;
`wf_attach` calls the same helper so the two loops cannot drift. The page says
*"same as 2018"* rather than *"no trades"*, which are different facts.

Nested windows give three invariants, and the checker enforces the third:
an earlier start can only ever see MORE trades, so a live cell with a null
ABOVE it is arithmetically impossible, and **null years must form a prefix**.
`recent` carries 324 daily-excess cells against 540 for every other universe
(540 − 216 = 324), which is the arithmetic the fix predicted before it ran.

## 19 → 13 → 9, two cuts with DIFFERENT criteria

Keep them apart. **The first removed rules that LOST; the second removed rules
that DUPLICATED.** Conflating them invents a quality story the data denies.

*Pass one (on rank)* removed six: `hg|swing`, `eath|QM`, `eath|QD`, their
controls `pair|QM` and `pair|QD`, and `eath|MWD`.

*Pass two (on redundancy)* removed four: **`qmw|0`, `pair|QW`, `pair|WD`** and
**`eath|WD`** — the last because its unfiltered control `pair|WD` went, and an
ATH row with nothing to control it against cannot be read. Measured by
`scripts/redundancy.py` (commit `ab58817`):

- The 13 labels carried **3–9 independent ideas**. Kaiser (λ>1) says 3, 90% of
  variance 7, Li-Ji 9. **Quote the range, never one number** — both matrices
  give Li-Ji exactly 9.00 and that is arithmetic coincidence, not corroboration.
- The duplication is **real, not a shared-scenario artifact**. All 13 rules are
  scored on the same 300 scenarios and the cross-strategy mean excess ranges
  −19.08 to +3.59 pts/yr, so some correlation is just "this scenario was hard
  for everything". Subtracting each scenario's mean barely dents it:
  `qmw|0`/`pair|QW` 0.963 → **0.922**, `pair|MW`/`qmw|0` 0.961 → **0.921**.
- Row-centring has a **floor**: it forces mean off-diagonal r ≈ −1/(k−1) =
  −0.083 and the observed mean is −0.081. Demeaned negatives are arithmetic,
  not diversification. Only r well ABOVE that floor is evidence.
- **The cut carries no quality signal.** Spearman(average rank, nearest-twin r)
  = −0.18, p = 0.56. "The duplicated rules are the good ones" was tested and
  falsified; trimming duplicates biases the board neither way.
- Worst surviving pair: `ema|0`/`pair|MD` at r = 0.646. **Redundancy removal
  bottoms out around 10 strategies** (the worst pair at that depth is 0.576), so
  do not expect another cut of this kind to buy much.
- `dv|55-20` is the find: rank **2nd** of 13 AND the **lowest** common-factor
  loading (0.080 vs 0.25–0.35 for every EMA rule) — the only top-ranked rule
  that is not a near-copy.

**This does not touch the gate.** BH is valid under positive dependence, and
correlation moves the variance of the "cells at p≤0.05 vs expected by chance"
count, not its expectation. (On the 13-board that read 12 vs ~195; on the
9-board it is 0 vs ~124.) What a duplicate-free board changes is the
evidential weight of the leaderboard's top rows, not the verdict.

## Open items, in priority order

1. ~~**THE REBUILD**~~ — **DONE 2026-09-11, and the projection held.**
   `python3 -m scripts.refresh`, 14:33:55 → 15:32:02 UTC, exit 0, **58.1 min**
   against the ~63 min pro-rata projection. Stages: preflight 0.6, grid
   (`dashboard_data`) 39.7, `wf_attach` 17.8 (arm A 8.5, arm B 9.1),
   `attach_diagnostics` 0.0. Log: `output/logs/refresh_2026-09-11_9strat.log`
   (gitignored). Pro-rata off one host measurement was the honest estimate and
   it came in 8% under — but see the host-bound trap below before reusing it.

   **`scripts/preflight.py` had been failing on EVERY run since 2026-09-10 and
   `refresh` aborts at it**, so no rebuild could be started through `refresh`
   at all. It reported *"page reads keys the build does not emit: daily_excess,
   diagnostics, fill_timing"* — which is true and harmless: preflight builds
   stage ONE of a three-stage payload, and those three keys are attached by
   `attach_diagnostics` in stage three. Proved pre-existing by extracting
   `DATA.<key>` reads from `dashboard.html` at four commits (absent at
   `9c06def`, present from `17f7156`; symmetric difference HEAD vs HEAD~1 is
   empty). Fixed in `784112a` with `_attached_keys()`, which greps
   `attach_diagnostics.py` for `payload["…"] =` rather than keeping a
   hand-written excuse list — a fourth instance of the same shape as the traps
   below, where "the build" named one stage of three.

2. **`exit_sweep` and `exposure` CSVs still describe 19 rules.** The ranking
   harness reads them and says so on its second line. Harmless (superset) but
   they should be regenerated against the 9-board before the ranks are quoted
   anywhere that matters. Unchanged from the last session.

3. **`scripts/wf_lookahead.py:105` is already broken and fails SILENTLY.** Its
   `SELF_CHECK_KEY` still names `liquidity`, the priority retired 2026-09-11 —
   the same bug as `wf_attach`'s, found and fixed there the same day but never
   swept for elsewhere. Unlike `wf_attach`, which aborts, this one returns
   `"SKIPPED -- ... is not on the built board"` (line 230), so the script keeps
   running with its self-check disabled and says so only in passing. One-line
   fix; `wf_lookahead.py` is in neither stamp tier, so it costs no rebuild.

4. **`PERMUTATION_WORKERS` is the bigger lever on rebuild time than the board
   size, and it is untested.** The box has 10 cores and 7 GB; the cap is 4
   because each worker needs ~0.5 GB, so **6 cores sit idle through the ~60-min
   validation stage**. Raising it risks OOM mid-rebuild and `validation.py` is
   in `signals._ACCOUNT`, so getting it wrong costs a grid rebuild. Measure
   actual per-worker RSS on a pilot before touching it — do not reason from the
   comment.

## Traps that cost time in these sessions

- **A stage finishing two orders of magnitude faster than its measured estimate
  is a defect report until proven otherwise.** Nothing errored, the checker
  passed, and `attach_diagnostics` said "all present in the grid". The ONLY
  signal was a duration.
- **Read the checker's full output, not its tail.** `grep -n FAIL`.
- **Three bugs on 2026-09-11 had one shape: an identifier that names less than
  it needs to.** `SELF_CHECK_KEY` named a retired priority; checkpoint keys
  named a DATE, not a board (two boards built that day, the second silently
  reused all 26 of the first's partitions — a ~50-min leg in 0.4 min, fixed with
  `wf_attach.board_key()`); `board_key` named a board, not its arithmetic.
  Preflight's model of "the build" named ONE stage of three (open item 1) was a
  fourth, now fixed. Open item 3 above is a fifth, still unfixed.
- **Editing tiers.** `signals._SUPPORT` (10 files, incl. `registry.py`)
  invalidates all signal caches; `signals._ACCOUNT` (5 files) invalidates the
  grid only. `scripts/wf_attach.py`, `scripts/wf_daily.py`, `web/dashboard.html`,
  `scripts/check_dashboard.js` and `tests/*` are in NEITHER and are free to
  edit. Verify with
  `python3 -c "from kitelab import signals; print(signals._SUPPORT)"`.
- **Where rebuild time goes.** On the 9-board (2026-09-11, measured end to end):
  preflight 0.6 min, `dashboard_data` **39.7 min**, `wf_attach` **17.8 min**
  (arm A 8.5, arm B 9.1), `attach_diagnostics` 0.0 — **58.1 min** total. The
  13-board split `dashboard_data`'s 90.6 min as signal caches 2m48s, validation
  (permutation + bootstrap) ~60 min, grid 3,900 cells 24m47s (stages sum to
  87.6; the missing 3.0 is setup and serialisation). The permutation test is the cost,
  not the signal caches, and **it loops per strategy**
  (`dashboard_data.py:763`) — **6.97 min per strategy across all stages**, which
  is the whole reason cutting the board cuts the clock. The grid scales per
  strategy too; the signal caches are noise either way.
- **Rebuild timing is host-bound**, 103–150 min for identical work on the same
  code on the 19-board. The 58.1 min above is ONE run on ONE host; it is a
  measurement, not a constant. Time the real run every time rather than
  quoting this line back.

## Saved analysis

    scripts/redundancy.py                 TRACKED, commit ab58817. The pass-two
                                          harness. python3 -m scripts.redundancy
                                          [board.json]; costs no rebuild.
    output/measurements/  (all 2026-09-11, gitignored — output/ is not in git)
    rank19_2026-09-11.py                  ranking harness; takes a board path arg
                                          run: PYTHONPATH=/work/kitelab python3 <it> <board.json>
    rank13_board_13strat_2026-09-11.log   ranks + redundancy, the 13-board
    rank19_board_19strat_2026-09-11.log   same, pre-cut 19-board (for comparison)
    redundancy_2026-09-11.log             pass-two tables
    entry_edge_2026-09-11.py              differs from scripts/entry_edge.py
    stop_rescue_2026-09-11.py             differs from scripts/stop_rescue.py
    recheck_mom_2026-09-11.py             momentum-priority recheck
    analyse19_2026-09-11.py               per-strategy board analysis

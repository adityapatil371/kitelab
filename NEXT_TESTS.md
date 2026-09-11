# START HERE — kitelab open state

Last updated **2026-09-11, second session**. Supersedes the 2026-09-09 note
that memory still points at (that file never existed on disk; this one does).

## The board as it stands

**THE REGISTRY SAYS 9 STRATEGIES; `dashboard.json` ON DISK STILL HOLDS 13.**
The cut below is committed in code but **not yet rebuilt** — `refresh --check`
reports "the STRATEGY CODE has changed since they were built". Every number in
the Headline block is from the 13-board and will move. Rebuild before quoting
anything.

    9 strategies x 300 scenarios = 2,700 cells
      (5 universes x 5 start years x 3 priorities x 2 risks x 2 capitals)

    ema|0          EMA · M/W/D              pair|MD     EMA · M/D
    dv|20-10       Turtle 20-10 + weekly    pair|MW     EMA · M/W
    dv|55-20       Turtle 55-20 + weekly    e1|daily    EMA · daily only
    dv|20-10 1TF   Turtle 20-10 (1 TF)      eath|MW     EMA · M/W within 10% of high
    dv|55-20 1TF   Turtle 55-20 (1 TF)

**Health after the cut:** 431 tests pass, pyflakes clean but for the known
`refresh.py:69`. The ATH control invariant holds (`eath|MW`→`pair|MW`) and
both `SELF_CHECK_KEY`s still name `ema|0`, which survived the cut on purpose.

**Headline numbers — FROM THE 13-BOARD, now stale:**

    BH FDR 5% [THE GATE]      0 of 3,900   (12 reach p<=0.05; ~195 by chance)
    next-open fills beat hold 156
    median detectable edge    16.3 CAGR pts/yr
    hold to beat              14.09 %/yr
    SELF-CHECK PASSED         ema|0|all|1|10000000|1|2018|mom_hi

The board is empty at the gate **by design**. That is the finding, not a bug.
See the memory note `kitelab-daily-excess-gate-2026-09-10`. Nothing about the
9-cut should change that: it removes duplicates, not evidence.

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
correlation moves the variance of the "12 cells at p≤0.05 vs ~195 by chance"
count, not its expectation. What a duplicate-free board changes is the
evidential weight of the leaderboard's top rows, not the verdict.

## Open items, in priority order

1. **THE REBUILD HAS NOT RUN.** `registry.py` is in `signals._SUPPORT`, so the
   9-strategy edit invalidated all signal caches and the grid. Projected
   **~63 min**, which is nothing cleverer than 90.6 x 9/13 — all three stages
   scale per strategy, so pro-rata is the honest estimate and the only one on
   offer until a real run is timed. Command: `python3 -m scripts.refresh`.
   Afterwards run
   `node scripts/check_dashboard.js` and re-read the Headline block above.

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
  `wf_attach.board_key()`); `board_key` named a board, not its arithmetic. Open
  item 3 above is a fourth instance of the same shape, still unfixed.
- **Editing tiers.** `signals._SUPPORT` (10 files, incl. `registry.py`)
  invalidates all signal caches; `signals._ACCOUNT` (5 files) invalidates the
  grid only. `scripts/wf_attach.py`, `scripts/wf_daily.py`, `web/dashboard.html`,
  `scripts/check_dashboard.js` and `tests/*` are in NEITHER and are free to
  edit. Verify with
  `python3 -c "from kitelab import signals; print(signals._SUPPORT)"`.
- **Where rebuild time goes** (measured on the 13-board, not estimated): signal
  caches **2m48s**; validation (permutation + bootstrap) **~60 min**; grid 3,900
  cells **24m47s**; total `dashboard_data` **90.6 min** (the stages sum to 87.6;
  the missing 3.0 is setup and serialisation). The permutation test is the cost,
  not the signal caches, and **it loops per strategy**
  (`dashboard_data.py:763`) — **6.97 min per strategy across all stages**, which
  is the whole reason cutting the board cuts the clock. The grid scales per
  strategy too; the signal caches are noise either way.
- **Rebuild timing is host-bound**, 103–150 min for identical work on the same
  code. Treat the ~63 min as a pro-rata projection off one measurement on one
  host, not a promise — time the real run and write the number down here.

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

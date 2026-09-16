# START HERE — kitelab open state

Last updated **2026-09-11, fourth session** (the Pine timeframe ladder). Supersedes the
2026-09-09 note that memory still points at (that file never existed on disk; this one does).

**Nothing on the board or the dashboard changed this session.** `dashboard.json` is still
the 20:44 IST 9-strategy build and `refresh --check` is quiet; the only commits touch
`scripts/wf_pine.py` and this file, neither in a stamp tier, so nothing was invalidated.
`main` is at `b34e8c5`, **11 ahead of origin/main as measured** — `origin/main` is a stale
local ref in the container (no fetch credentials), so measure it with
`git rev-list --count origin/main..HEAD` rather than trusting any number written down.

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

## The user's Pine strategy, measured on three timeframes (2026-09-11, session four)

The user supplied a TradingView Pine v5 strategy — `HA + RSI Entry / HTF EMA Trend
Filter` — and asked for it to be tested thoroughly, then on weekly bars, then on
monthly. All three are DONE. The harness is `scripts/wf_pine.py` (commits
`6244e81`, `ddf2965`, `eee7367`), a standalone out-of-band script on the
`wf_attach` pattern: it loads the signal caches and the built `dashboard.json`,
applies `slippage.apply_spread`, and calls the same `portfolio.run`, so its cells
are comparable with the board's. **It is in neither stamp tier — running or
editing it costs no rebuild.** That is why it exists as a script rather than a
registered strategy.

The rule: enter long when a Heikin Ashi candle closes bullish with a lower wick
under 8% of its range, RSI(14) is above 50 and rising, and price is above a
higher-timeframe EMA(20); stop 1.5xATR, target 3xATR, exit on an HA colour flip.

### The command

    python3 -m scripts.wf_pine --tf D|W|M            # the full run, ~6-10 min
    python3 -m scripts.wf_pine --verify RELIANCE --tf W   # hand-checks, instant
    python3 -m scripts.wf_pine --pilot 40 --tf M    # time it first, ~3 s
    python3 -m scripts.wf_pine --ablate --tf W      # which entry leg earns its place

`--tf` is the "one step up" ladder: D = daily bars / weekly EMA(20) filter (the
Pine as written), W = weekly / monthly, M = monthly / quarterly. **W is not a
weekly filter on a weekly chart** — that would compare a close against an EMA of
itself; the one-step-up reading matches `timeframes.py`'s own `"MW"`. Outputs and
the checkpoint dir carry the timeframe, so the three runs cannot overwrite each
other.

### The result, and it is the same verdict three times

| | D | W | M |
|---|---|---|---|
| trades | 195,482 | 42,787 | 9,468 |
| win rate | 35.2% | 39.3% | 46.6% |
| expectancy AFTER costs | -0.020 R | +0.105 R | +0.351 R |
| charges | Rs 743M | Rs 85M | Rs 11M |
| median hold | 5 days | 22 days | 91 days |
| median cell CAGR | -25.35 | +4.00 | +6.40 |
| median excess vs hold | -38.86 | -8.97 | -5.93 |
| cells beating hold | 0.0% | 0.7% | 15.2% |
| max drawdown | -99.2% | -52.7% | -51.2% |
| **cells passing mde_80** | **0 of 1,380** | **0 of 1,380** | **0 of 1,320** |

**The entry was never the problem; the turnover was.** The daily gross edge
(+0.0965 R/trade over 195,482 trades) is real and was spent entirely on spread
and brokerage — together ~1.6x the edge. Trading the same three conditions 20x
less keeps the edge and cuts the bill 67x. Every column above improves
monotonically with the timeframe.

**But it converges on buy-and-hold from below and never crosses.** The M default
cell `atrfill-open` posts t_hac = **-0.005** — indistinguishable from hold, not
better. Best cell anywhere (M, `atr-close`, small, 2018, 1% risk, Rs 2L,
`nearhigh_hi`): CAGR 21.3 vs hold 11.53, excess **+9.15** against an mde_80 of
**9.67**. It misses by half a point, as the max over 1,320 tries. Across all
three ladders, **0 of 4,080 cells clear the bar.**

Note the daily verdict is stronger than the board's nine rules get: this rule
loses *detectably* (t_hac to -9.9), where the board's nine are merely
"can't tell". Those are different findings.

### Things a next session must not re-derive

- **A hypothesis of mine was FALSIFIED and the wrong version reached the user
  before I caught it.** The W ablation showed dropping the RSI leg RAISED account
  CAGR (8.4 vs 6.1) while LOWERING per-trade expectancy, and I explained it as
  cash drag — idle money. It is not. `curves.exposure_pct` on the default cell is
  **99.2-100% at all three timeframes** (`output/logs/wf_pine_exposure_2026-09-11.log`);
  the account is always fully invested, so extra signals cannot be filling idle
  cash. **The cause is still unexplained.** Do not repeat the cash-drag story
  without measuring it.
- **Monthly costs five years of history, structurally.** The filter is EMA(20) on
  QUARTERLY bars, so no entry can exist before 20 closed quarters. 35 symbols
  dropped as too short (6 on W, 3 on D) and the 12 `recent|2018` cells vanish
  outright — which is why M has 1,320 cells and D/W have 1,380. Monthly is tilted
  towards old listings; read its universe counts before its CAGRs.
- **The short side is negative everywhere** (-0.157 R on W, -0.249 on M) and is
  TRADE LEVEL ONLY: `portfolio.py` is long-only and NSE cash equity cannot be held
  short overnight — that needs futures, ~200 of the 1,000 names.
- **`slippage.apply_spread` is long-only by construction** (its guard at
  `slippage.py:218` refuses any trade whose `gross_profit != (exit-entry)*shares`),
  and `slippage.py` is in `signals._SUPPORT` — editing it costs a ~58-min rebuild.
  `wf_pine.spread_of_shorts()` mirrors each short's two legs and their timestamps
  instead; the residual STT approximation is in its docstring.
- **`sizing.position(entry, stop)` assumes long** and returns 0 shares when the
  stop is above the entry. An early version passed shorts their real stop and
  **silently dropped every short trade** (0 of 0 across five variants, which looked
  like a data problem). Both sides are now sized as the equivalent long. The long
  numbers were never affected.
- **The `end_ts` stamping convention is the whole difficulty of the W and M
  ports.** `frames.NAMED_AGG` sets `ts=first session, end_ts=last`, so a Mon-Fri
  weekly bar carries a MONDAY stamp and DECIDES on Friday. Two consequences, both
  handled and both silent if got wrong: (a) the higher-timeframe EMA must be looked
  up by `end_ts` or a week straddling a month boundary reads an EMA one month stale
  (measured on ABB: 144 of 1,078 weekly bars straddle = 13.4%, median error 1.44%,
  max 7.89%); (b) THE STAMP MOVES WITH THE FILL — close-convention and intrabar
  exits stamp `end_ts`, next-open fills stamp the fill bar's own `ts`, or
  `portfolio.run` commits cash four sessions before the price existed. See
  `timeframes.py:141` and `timeframes.py:195-218`.
- **`indicators.rsi` and `indicators.atr` use `ewm(adjust=False)` with no
  `min_periods`, so both emit a value from bar one.** An unguarded port filters its
  earliest trades with a "20-week EMA" seeded two weeks earlier. `wf_pine` sets
  `WARMUP = max(RSI_LEN, ATR_LEN) + 1`, requires `closed >= EMA_LEN - 1` for the
  HTF EMA, and needs 150 daily bars minimum.
- **Two Pine settings were deliberately not ported**: `default_qty_value=100
  percent_of_equity` (the board sizes on risk, not equity fraction) and the flat
  0.05% commission (the board uses real `backtest.charges`).

### Outputs (all gitignored — `output/` is not in git)

    output/wf_pine_{,W_,M_}2026-09-11.json          summaries + all cells
    output/measurements/wf_pine_{,W_,M_}2026-09-11.csv   1380 / 1380 / 1320 rows x 17
    output/logs/wf_pine_2026-09-11.log              the D run
    output/logs/wf_pine_shorts_2026-09-11.log       D shorts, after the sizing fix
    output/logs/wf_pine_ablate_2026-09-11.log       D ablation
    output/logs/wf_pine_W_2026-09-11.log            the W run
    output/logs/wf_pine_W_ablate_2026-09-11.log     W ablation
    output/logs/wf_pine_M_2026-09-11.log            the M run
    output/logs/wf_pine_exposure_2026-09-11.log     the falsification above
    output/wf_pine_ckpt_{,W_,M_}<board key>/        cell checkpoints (NOT trades)

**The checkpoints hold finished cells, not trade lists** — a `(cells, stats)`
tuple. Anything needing the trades themselves must rebuild them (~1 min per
timeframe per variant for 1,000 symbols).


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

2. ~~**`exit_sweep` and `exposure` CSVs still describe 19 rules.**~~ —
   **DONE 2026-09-16, commit `17e0e9e`.** The registry was already down to 9,
   so both scripts were producing 9-rule numbers into files named for a 19-rule
   board. Regenerated: `entry_edge` **13s**, `exit_sweep` **12s** (measured; my
   pre-run guess of "a few minutes each" was wrong by an order of magnitude).
   `rank19` now reports `exit_sweep: 9 rules; exposure: 9 rules`.

   The naming was the real defect, not the staleness. Both wrote to hardcoded
   `*_2026-09-10.csv`, so re-running would have **overwritten the 19-rule record
   while keeping the 09-10 name** — the checkpoint-key collision again, an
   identifier naming a DATE rather than its contents. Output names now derive
   `N_RULES` from `registry.REGISTRY`: `entry_edge_9strat_2026-09-16.csv` and
   siblings. Every hardcoded 19 (table footer, pooled row label, plot legend and
   title) now reads `N_RULES`. The 09-10 CSVs survive untouched, and
   `output/measurements/rank19_2026-09-11.py` globs for the newest rather than
   naming a date.

   **`rank19_2026-09-11.py` lives in gitignored `output/` and is therefore NOT
   under version control** — the same exposure that lost the original
   `NEXT_TESTS.md`. Moving it to `scripts/` was not done on my own initiative;
   raise it with the user.

3. ~~**`scripts/wf_lookahead.py:105` fails SILENTLY.**~~ — **DONE 2026-09-16,
   commit `b2b1a73`, and it was NOT a one-line fix.** The name was only half of
   it: `self_check`'s verdict is only *printed* (line 313), never acted on, so
   even a `FAIL` would not have stopped the run. Both changed — `liquidity` →
   `mom_hi` (verified on the live board, `cagr 0.4`), and the absent-key branch
   now returns **FAIL** rather than SKIPPED, since over the full universe it can
   only mean the key names something the board does not build. Genuine SKIPs (a
   pilot run, no `dashboard.json`) are unchanged.

   **THE SWEEP IS DONE AND ITS RESULT IS NEGATIVE — do not redo it.** Four other
   scripts still set `PRIORITY = "liquidity"`: `wf_risk.py:128`,
   `ath_band.py:71`, `wf_tail.py:57`, `wf_survivor.py:78`. **They are correct as
   they stand.** `portfolio.py:231` keeps that ordering functional on purpose
   ("retired 2026-09-11, kept so earlier results and the control scripts still
   reproduce"), and `wf_risk.py`'s docstring embeds a dated result (RAN
   2026-09-08) produced with it. I changed `wf_risk.py` and reverted it:
   repointing those at `mom_hi` would silently invalidate recorded measurements,
   not fix a bug. Only `wf_lookahead` checks itself against the **live** board,
   so only `wf_lookahead` was broken.

   Note also that `liquidity` does **not** raise from `portfolio._order` — the
   guard at line 237 tests membership of the `keys` dict, which still contains
   it, not of `PRIORITIES`, which does not. Passing a retired priority runs
   silently.

4. ~~**Why does dropping the RSI leg RAISE account CAGR on weekly?**~~ —
   **ANSWERED 2026-09-16: the premise was a measurement artifact, and the
   corrected answer is much more interesting. See section 12 below.** The
   paragraph that follows is kept only to show what the wrong numbers were;
   do not re-derive from it.

   Original wording: 8.4 vs 6.1,
   while per-trade expectancy FALLS (0.057 vs 0.105). The obvious explanation —
   cash drag — is measured false, though **not by the number this item used to
   cite**: "exposure 99.2-100%" is `curves.exposure_pct`, the share of days
   holding *anything at all*, which says nothing about capital. The right
   columns are `median_cash` (0.0% on six of nine rules) and `full_pct`
   (40-91% of days under 5% cash), and they agree — there is no idle cash.
   Candidate explanations not yet tested: more candidates give `mom_hi`
   priority a better pool to choose from, or the extra positions simply
   diversify. Cheap to test: `wf_pine` already has the ablation harness, and it
   costs no rebuild. This is the only genuinely open question the Pine work left.

   **The breadth version of that second explanation is already dead** — see
   item 7. Do not re-derive it.

7. ~~**Does owning more of the market close the gap?**~~ — **ANSWERED NO,
   2026-09-16, `scripts/exposure_reconcile.py`, commit `f7f669e`. Costs
   nothing to re-run (under 1s, no rebuild).**

   The 2026-09-16 regenerated `exposure_*.csv` showed all 9 rules beating hold
   on the rate earned *while invested* (15.5-21.2%/yr vs 14.09) while owning
   5.9-49.5% of the market's stock-sessions, and breadth correlating **+0.986**
   with that table's "vs hold" column. It read as the project's first real lead:
   the rules are fine, they just own too little.

   It was circular. That table's `effective_pct_yr` is
   `expm1(net_in_market × breadth)` with the comment "idle cash earns 0", so its
   `vs_hold_pts` is a monotone function of breadth **by construction** — the
   +0.986 is arithmetic, not evidence. Two further faults, either one fatal:

   - **There is no idle cash to charge.** `entry_edge` has no account behind it;
     it counts every signal the rule ever made, unconstrained. The board's
     account is cash-constrained and ~fully deployed into few names (take rate
     5.2-36.4% of signals). `effective_pct_yr` prices a fund nobody simulated.
   - **The in-market rate is gross, the board is net.** That column carries the
     0.222% round trip only — no slippage, no 1% fill cap, which
     [the friction memo] measured as the thing that kills the edge. Median gap
     to the board: **+13.44 points**, the size of the whole puzzle.

   Re-run against the board's own numbers, which carry no circularity:

   | comparison | pearson | spearman |
   |---|---:|---:|
   | breadth vs MODELLED vs-hold (circular) | **+0.986** | +0.933 |
   | breadth vs BOARD median excess | **−0.444** | −0.367 |
   | breadth vs BOARD median cagr | −0.410 | −0.350 |
   | in-market rate vs BOARD median excess | +0.361 | +0.183 |

   The sign flips. With n = 9 and `n_eff` 4.0 nothing here is significant either
   way, so the honest read is **breadth does not predict the board** — not that
   owning more names hurts. "Hold more stocks" is not a lead.

   Two things this leaves standing: the concentration finding from 2026-09-10
   is *reinforced*, not overturned; and the net-of-costs in-market rate, which
   was the last open piece, is now measured — item 8.

8. ~~**What do the rules earn per session in the market, net of real fills?**~~
   — **MEASURED 2026-09-16, `scripts/net_in_market.py`, commit `2668118`.
   70s, no rebuild. Self-check reproduces `entry_edge`'s published column on
   all 9 rules (worst gap 0.089 pts).**

   `entry_edge`'s `net_pct_yr_in_market` carries the 0.222% statutory round
   trip and nothing else — its own output says "no slippage, no 1% fill cap".
   Charged properly, in four levels (hold = 14.09%/yr):

   | rule | sess | gross | +stat | **+spread** | vs hold |
   |---|---:|---:|---:|---:|---:|
   | Turtle 55-20 (1 TF) | 34.9 | 23.2 | 21.3 | **15.6** | +1.5 |
   | EMA · M/W | 43.1 | 21.4 | 19.9 | **15.4** | +1.3 |
   | Turtle 55-20 + weekly | 30.5 | 22.9 | 20.7 | **15.2** | +1.1 |
   | EMA · M/W · within 10% | 47.3 | 17.0 | 15.6 | **12.2** | −1.8 |
   | Turtle 20-10 (1 TF) | 21.3 | 23.1 | 19.9 | **10.8** | −3.3 |
   | Turtle 20-10 + weekly | 19.0 | 19.0 | 15.5 | **7.3** | −6.8 |
   | EMA · M/W/D · no band | 8.4 | 24.9 | 16.9 | **−2.2** | −16.3 |
   | EMA · M/D | 8.2 | 24.2 | 16.1 | **−3.2** | −17.3 |
   | EMA · daily only | 7.9 | 28.7 | 20.0 | **−5.3** | −19.4 |

   **The half-spread alone** takes the median from 16.8 to 10.8 and cuts
   "beats hold" from 9 of 9 to 3 of 9 — and those three lead by ~1.3 points,
   inside any noise band. The three fast EMA families go **negative**. Note
   what orders this table: **holding period, not signal quality.** Every rule
   holding 19+ sessions survives the spread; every rule holding ~8 dies.

   Add market impact at the board's own ₹1cr book and **0 of 9 beat hold**
   (median 7.8). At ₹2 lakh, 2 of 9 survive at 14.6–14.7 vs 14.09. Level 4 is
   a curve, not a number — impact is a fact about order size, and the cached
   lists scale linearly with the book, so the script sweeps ₹2L → ₹500cr.

   **Read the cap table beside it or level 4 reads as good news.** The 1% cap
   refuses 82–90% of the intended position at ₹1cr (41–49% at ₹2L), which makes
   the surviving shares cheaper per rupee and *flatters* the rate. It is a
   sizing rule, not a cost; its real price lands on breadth.

   **The gap to the board closes.** Median gap between this rate and the
   board's own median CAGR: **+13.53 gross → +1.57 with the fills charged.**
   Real costs account for 11.96 of the 13.53 points. Not independent
   confirmation — both routes price the same cached trades — but it locates the
   money: **the aggregation was never the problem, the fills were.** The three
   fast EMA rules now *undershoot* the board by 8–10 points, which is the cash
   constraint working in their favour: the account can only afford 5–7% of
   their signals and `mom_hi` picks them.

   **Where this points.** The only rules that survive real fills are the slow
   ones, and the surviving margin is ~1 point at a book small enough not to
   move the price. Turnover is the tax. A next session's cheapest lead is the
   one this table hands over: the same rules held **longer** — the ladder from
   7.9 to 47.3 sessions is monotone in survival, and nobody has yet tested an
   exit that deliberately lengthens it.

   **ANSWERED — see item 9. The lead was half right, which is the useful half.**

9. ~~**Does holding the same entries longer survive real fills?**~~ —
   **RAN 2026-09-16, `scripts/hold_longer.py`, ~4 min, no rebuild. SPLIT
   VERDICT, and the split is the finding.** Commit: see `git log` for
   `hold_longer`. Self-check PASS: the own-exit column reproduces
   `net_in_market`'s `spread_pct_yr` for all nine rules (worst gap 0.068 pts),
   and at the Rs 1cr book its median lands at 7.7 against that script's
   separately-computed 7.8 with the same **0 of 9** beating hold.

   **The design.** Take each rule's own entry stamps, DISCARD its exit, sell at
   a fixed horizon instead. Same entries, same stocks, same dates — only the
   holding period moves, so the comparison is causal in a way
   `net_in_market`'s cross-rule ordering could not be (there the slow rules and
   the fast rules were *different rules*).

   **What it deletes, and it is not small: THE STOP.** A fixed-horizon exit
   ignores the entry candle's low. These columns are a probe of one variable,
   not a strategy anyone could run. Keeping the stop and lengthening everything
   else is the NEXT question, not this one.

   **Per session in the market, %/yr, toll + half-spread charged, hold = 14.09:**

       rule                          sess    own     20     40     60     90    120    180    250
       EMA . daily only               8.0   -5.3    0.8    5.0    5.7    8.4    8.5    9.6    9.9
       EMA . M/D                      8.2   -3.2    3.9    8.2    9.3   11.0   10.9   10.6    9.6
       EMA . M/W/D . no band          8.4   -2.2    4.7    9.2   10.8   12.3   12.2   11.8   10.5
       Turtle 20-10 + weekly         19.1    7.3    5.3   15.4   18.3   19.8   21.3   20.1   16.9
       Turtle 20-10 (1 TF)           21.4   10.8    4.6    5.6    7.1    9.7   10.2   11.2   11.5
       Turtle 55-20 + weekly         30.6   15.2    3.8   14.0   17.1   18.8   20.3   19.4   16.8
       Turtle 55-20 (1 TF)           35.0   15.5    3.7    8.4   11.1   11.9   13.5   14.2   14.0
       EMA . M/W                     43.2   15.3   -3.5    1.5    4.2    6.9    7.5    8.1    8.8
       EMA . M/W . within 10%        47.6   12.2    4.8    4.9    4.4    5.4    6.7    7.3    5.8
       MEDIAN OF THE 9              ....   10.8    3.9    8.2    9.3   11.0   10.9   11.2   10.5
       beats the 14.1 hold, of 9        3      0      1      2      2      2      3      2

   **1. For the FAST rules, turnover was the whole tax.** The three EMA
   families holding ~8 sessions were LOSING money after the spread (-5.3, -3.2,
   -2.2). Held 90-250 sessions on the same entries they turn positive: +9.9,
   +11.0, +12.3 — swings of **+14 to +15 points** from nothing but the calendar.
   `Turtle 20-10 + weekly` gains +14.1 the same way. This is the causal
   confirmation item 8 could not supply.

   **2. For the SLOW rules, their own exits beat EVERY fixed horizon.**
   `Turtle 55-20 (1 TF)` own 15.5 vs best fixed 14.2; `EMA . M/W . within 10%`
   12.2 vs 7.3; `EMA . M/W` **15.3 vs 8.8, a 6.5-point gap**. Those exits are
   not merely "holding a long time" — they carry information about *when* to
   leave, and a calendar throws it away. **Do not read this table as "longer is
   better".** A fixed horizon beats the rule's own exit for 6 of 9; the 3 it
   loses to are 3 of the 4 slowest.

   **3. And it still does not cross buy-and-hold.** The median rule's best
   horizon is **11.2%/yr against the 14.09% hold**. The curve climbs steeply to
   ~90 sessions, flattens, and rolls over by 250. It converges on hold FROM
   BELOW and never crosses — the identical shape the user's own Pine rule traced
   up the daily -> weekly -> monthly ladder (item: the Pine section above). Two
   unrelated routes, one shape.

   **4. At the board's own Rs 1cr book, a long hold does beat 0.** Own exits:
   **0 of 9** over hold (median 7.7). Fixed 60-250 sessions: **2 of 9** —
   `Turtle 20-10 + weekly` (20.8 at 120) and `Turtle 55-20 + weekly` (19.8 at
   120), both well clear of 14.09. Small, but the first time anything in this
   project has moved a count off zero at a realistic book size.

   **Why 4 is not yet a finding.** Three reasons, all unmeasured here:
   (a) every rate is PER SESSION IN THE MARKET and assumes the next trade starts
   the day this one ends — a 120-session hold cannot be redeployed at the rate
   these rules signal, so the true account rate is lower and the cost lands on
   BREADTH, which this script does not measure; (b) 9 rules x 10 horizons = 90
   numbers and the best of 90 is a lucky number, the same multiple-testing
   problem the board's BH bar exists for; (c) the stop is gone, so per-trade
   drawdown is uncapped.

   **Where this points.** The one testable thing left standing is narrow and
   concrete: **the two `+ weekly` Turtles, held long, with the stop kept.**
   That is a strategy, not a probe — it can go through `portfolio.run` and get a
   real account curve with breadth and cash priced in, which is exactly what
   (a) and (c) above are missing. It needs no rebuild if run through a
   standalone harness in `scripts/` the way `wf_attach` does it.

   Outputs (gitignored): `output/measurements/hold_longer_9strat_2026-09-16.csv`
   (every rule x horizon x cost level), `output/hold_longer_curve.png`,
   `output/logs/hold_longer_run.log`.

   **A fault caught and fixed mid-run, worth knowing:** the first draft printed
   the `own` column spread-only beside the impact-charged horizon columns in all
   three tables, so a cheap exit was being compared against dear ones and the
   "beats hold" count sat at 3 in every table. The impacted own column is 0 of 9.
   Same bug shape as the glob fault in item 8 — **a label that names less than it
   needs to.** Third instance in this project.

5. **`PERMUTATION_WORKERS` is the bigger lever on rebuild time than the board
   size, and it is untested.** The box has 10 cores and 7 GB; the cap is 4
   because each worker needs ~0.5 GB, so **6 cores sit idle through the ~60-min
   validation stage**. Raising it risks OOM mid-rebuild and `validation.py` is
   in `signals._ACCOUNT`, so getting it wrong costs a grid rebuild. Measure
   actual per-worker RSS on a pilot before touching it — do not reason from the
   comment.

6. **Should the Pine rule go on the board?** My reading is no, and the next
   session should not do it on its own initiative: it is a user-supplied idea that
   failed at every timeframe, registering it costs a full rebuild (`registry.py` is
   in `signals._SUPPORT`), and `wf_pine.py` already measures it for free. Raise it
   with the user rather than deciding.

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

---

## 10. The two "+ weekly" Turtles held long WITH THE STOP KEPT — RAN 2026-09-16

`scripts/turtle_hold.py`, 3.8 min, no rebuild. This is item 9's own next step:
hold_longer showed both `+ weekly` Turtles reaching ~20%/yr at a 120-session
horizon against a 14.09% buy-and-hold, but it had **deleted the stop** and
priced no breadth. This puts the stop back and runs every variant through
`portfolio.run` on the board's own 300 scenarios.

**Verdict: the ~20%/yr does not survive. Not one variant beats buy-and-hold on
its median cell, at either account size.** Best is −2.97 points/yr.

**Design.** `simulate_variant()` mirrors `darvas.simulate` rather than editing
it (editing `darvas.py` rebuilds 4 strategies). Entry and stop untouched; only
the trailing channel is replaced, four ways: `base` (the board's own rule),
`t120`/`t250` (channel dropped, sell at the stop or after N sessions),
`slow` (channel kept but widened 3×: 10→30, 20→60). Horizons taken from item
9's table, not re-searched.

**Two self-checks, both passing.** The mirror reproduces `darvas.simulate`
field for field — 876 trades over 40 symbols × 2 rules, 0 mismatches. `base`
then reproduces `dashboard.json`'s own cells — 276 of 276 for each rule, to
1e-9.

### Finding 1 — the stop, not the exit, sets the holding period

|            | stop fires | channel | time limit | median sessions |
|---|---:|---:|---:|---:|
| `dv\|20-10` base | 57.8% | 42.2% | — | 11 |
| `dv\|55-20` base | 66.0% | 34.0% | — | 12 |
| `t120` | 77.4% | — | 22.6% | **12** |
| `t250` | 81.8% | — | 18.2% | **12** |

Raising the limit from 120 to 250 sessions moves the median holding period by
**zero sessions**. Four trades in five are already dead at the entry candle's
low. **"Hold longer with the stop kept" is close to a contradiction**: item 9's
long holds existed *because* it had thrown the stop away. The 22.6% that do
survive to the time limit carry the whole effect.

### Finding 2 — without the channel, the two rules are ONE rule

Shared (symbol, entry date) pairs between the 20-10 and 55-20 variants:
`base` 91.9%, `t120` **100.0%**, `t250` **100.0%**. Every 55-candle high is
also a 20-candle high, and once a position skips forward past a long hold both
rules land on the same bar. The `t120` and `t250` rows below are duplicates,
not two pieces of evidence — count them once.

### Finding 3 — the account layer takes back most of the gain

Median excess over the on-screen equal-weight buy-and-hold, CAGR points/yr,
and cells above it out of 138:

| variant | ₹2 lakh | >hold | ₹1 crore | >hold |
|---|---:|---:|---:|---:|
| `dv\|20-10` base | −7.30 | 16 | −9.73 | 3 |
| `dv\|20-10 t120` | −5.92 | 33 | −7.73 | 20 |
| `dv\|20-10 t250` | −2.97 | 58 | −3.65 | 35 |
| `dv\|20-10 slow` | −1.26 | 56 | −4.87 | 20 |
| `dv\|55-20` base | −3.06 | 44 | −4.67 | 42 |
| `dv\|55-20 slow` | −5.04 | 16 | −8.42 | 4 |

Holding longer **is** the right direction — `t250` improves on `dv|20-10`'s own
exit by **+4.3 points** at ₹2 lakh and **+6.1** at ₹1 crore, and lifts cells
beating hold from 3 to 35. That is item 9's effect, real and surviving the
stop. It is simply not worth 14 points, which is what it would need.

### Finding 4 — widening the channel helps the fast rule and hurts the slow one

`slow` is the only variant here a person could actually trade (a trailing rule,
not a calendar). Tripling it takes `dv|20-10` from −7.30 to **−1.26** at ₹2
lakh — the best number in the table — and takes `dv|55-20` from −3.06 to
**−5.04**. A 60-candle channel on a 55-candle breakout is past the point where
trailing wider still helps. The `slow` result also decays badly with account
size (−1.26 → −4.87), which the `base` rules do not, so it is buying its gain
in names the ₹1 crore book cannot fill.

### The bug this run found: `wf_attach.spread_of` leaves slippage ON

`spread_of` sets `slippage.ENABLED = True` and does not restore it. The board's
producers all build with it off and charge the spread once, afterwards — so any
harness that **builds a second trade list after calling it** re-charges the
spread inside `slippage.fill`, which moves `entry_price`, which moves `risk`,
which makes `sizing.position` return a different share count. A different
trade, not a rounding difference.

In the first full run `dv|20-10 base` was built before any `spread_of` and
matched the board 276/276; every variant after it was silently double-charged,
and `dv|55-20 base` missed 275 of its 276 cells by up to 2.6 CAGR points. The
board self-check is the only reason this was caught — the trade lists were
individually plausible and the totals looked fine.

Fixed in `turtle_hold.build_all`, which now forces the spread off for the
duration and restores the caller's setting in a `finally`. **`wf_attach.py` and
`wf_pine.py` were NOT touched** — `board_key` hashes `wf_attach.py` whole, so a
one-line fix there invalidates ~75 min of diagnostics. Any future harness that
builds trades must own this invariant itself.

Also noted, not fixed: **`darvas.py:210` hardcodes `"10-candle low"` whatever
`exit_len` is**, so every `dv|55-20` channel exit on the board is labelled for a
channel it did not use. Cosmetic — nothing reads the string back — and fixing
it costs a 4-strategy rebuild. Fourth instance of this project's recurring bug
shape: *an identifier that names less than it needs to.*

### Where this points

Item 9's lead is now closed. The honest summary of items 8–10 together:
**turnover was a real and large tax on the fast rules, removing it is worth
+4 to +6 CAGR points, and that is not enough to reach buy-and-hold.** The
remaining gap is not an exit problem.

Outputs: `output/turtle_hold_2026-09-16.json`,
`output/measurements/turtle_hold_2026-09-16.csv` (2,208 rows),
`output/logs/turtle_hold_run.log`,
`output/turtle_hold_ckpt_2026-09-11_8cbfd3_dd707a/` (8 pickles, resumable).

## 11. Eight stop-losses across all nine board rules — RAN 2026-09-16

`scripts/stop_sweep.py`, 6,624 cells in 30.0 min, no rebuild incurred.
Same rules, same entries, only the stop moved. Six valid settings: the
incumbent (`own`, the entry candle's low), 1/2/3×ATR(14), and flat 5%/10%.

**Finding: the incumbent stop is on the wrong side of the optimum for
essentially every rule, and widening it is worth about +1 to +1.6 CAGR
points a year.** Median effect across the nine rules, vs that rule's own
`own` row, and the count of rules it helped:

    ~3.8% (1xATR, tighter)  -0.39   4 of 9
    5% flat                 -0.07   4 of 9
    ~7.5% (2xATR)           +1.14   7 of 9
    10% flat                +1.12   8 of 9
    ~11.3% (3xATR, widest)  +1.55   7 of 9

Tighter is never better and the direction is monotone. It is a Turtle
finding, not an EMA finding: on the Turtles the incumbent stop ends 50-66%
of all trades and widening to 3xATR cuts that to 16% while tripling the
hold (12 -> 36 sessions); the fast EMA rules exit on their own signal in a
median 3 sessions whichever stop is set, so the stop was never binding
there. **Nothing crosses zero** — best combination anywhere is
`dv|55-20` + weekly at 3xATR, -2.02 pts/yr vs hold, up from -3.77.

Verification: every rule's `own` variant reproduced the board's cached
trade count exactly and matched **92 of 92** account cells in
`dashboard.json`, 0 disagreements, nine times over; plus 27,726 trades
compared field-by-field over 40 symbols x 9 rules, 0 mismatches.

### The bug this run found: `portfolio.py:564` re-sizes off `trade["stop"]`

Two of the eight columns are **dead at the account level and must not be
quoted**. `portfolio.run` computes `per_share_risk = entry_price -
trade["stop"]`, so the `stop` field is a SIZING INPUT to the account, not
merely a record of the exit line.

- `none` (no stop) writes `-inf`, so per-share risk is infinite, so the
  account buys **0 shares — 0 trades taken in all 828 cells**. That is why
  all nine rules printed an identical `0.00 / -12.32`; identical account
  results from nine different rules is the tell.
- `atr2_sizefix` was built to separate the exit effect from the sizing
  effect. It cannot: the account re-sizes it off the ATR stop exactly as it
  does `atr2`, so the separation never happens above the trade level.

The six real columns are unaffected — for those, exit stop and sizing stop
are the same number, so producer and account agree. The fix is to carry the
exit line and the sizing line as separate fields; it needs the full 30-min
re-run because the checkpoints are per-rule and hold all eight variants.
**User declined the re-run 2026-09-16** ("no need for that"). Still open:
does removing the stop entirely survive the cash constraint? At trade level
it looks excellent (`dv|55-20` expectancy 1.61 -> 3.17 R, win rate 26% ->
44%) and that is currently unmeasured at account level.

Outputs: `output/stop_sweep_2026-09-16.json`,
`output/measurements/stop_sweep_2026-09-16.csv` (6,624 rows x 14 cols),
`output/logs/stop_sweep_run.log`,
`output/stop_sweep_ckpt_2026-09-11_8cbfd3_dd707a/` (9 pickles, resumable).

## 12. The Pine ablation was mischarged, and RSI is the leg that hurts — RAN 2026-09-16

`scripts/pine_ablate_fix.py`, 3.0 min, no rebuild. **This supersedes item 4,
whose premise was false.**

### The bug (third instance of the same shape)

`wf_attach.spread_of` sets `slippage.ENABLED = True` and never restores it,
and `wf_pine.trades_for:349` calls `slippage.fill` while BUILDING a trade.
`wf_pine.ablate` calls `spread_of` after each of seven legs. So **leg 1 is
built clean and charged the half-spread once; legs 2-7 are built through
`slippage.fill` and charged again afterwards.** Proved three ways: the flag
printed at each leg's top on the full universe (leg 1 `False`, legs 2-7
`True`), the same leg rebuilt both ways giving different entry prices, and
the replay arm reproducing all seven of the 2026-09-11 CAGRs exactly.

Item 4's "expectancy FALLS 0.105 -> 0.057 while CAGR RISES" compared leg 1
against leg 4 — one backpack against two. **Charged alike the expectancies
are 0.105 and 0.101. There was never a paradox.** The three legs that all
reported exactly 3.2 become 6.3 / 5.8 / 6.1 — the same bug compressing
three genuinely close numbers into one decimal place, not a second fault.

**The same leak is in `wf_pine`'s MAIN path** (`build_all` + `spread_of`
per variant), so every variant after the first in the three-timeframe study
was double-charged. That study's verdict ("loses at every timeframe") is
safe in direction — overcharging only makes things look worse — but its
numbers are wrong and must not be quoted.

### The finding, charged alike (weekly, all|2018, 1% risk, Rs1cr, mom_hi)

    leg                  trades   expct R   taken     sigs   CAGR   vs hold
    full rule            42,787     0.105   2,435   24,347    6.1     -5.87
    no RSI condition     51,088     0.101   2,624   29,177   11.1     -0.87
    no wick condition    64,420     0.091   2,876   36,265    6.3     -5.67
    no HTF EMA filter    52,834     0.102   2,481   28,719    6.0     -5.97
    HTF filter alone     86,925     0.073   3,223   49,435    5.8     -6.17
    wick alone           73,507     0.089   2,662   40,340    8.7     -3.27
    RSI alone            79,663     0.086   2,947   42,652    6.1     -5.87

**Every combination containing RSI lands near 6.0 (6.1, 6.0, 6.3, 6.1);
both RSI-free combinations are higher (11.1, 8.7).** Four independent
askings, one answer — stronger than the single comparison item 4 rested on.
Max drawdown improves too, -50.9% -> -47.5%.

**The mechanism is selection, not volume.** Trades funded rose only 8%
(2,435 -> 2,624) while CAGR rose 82%. What grew is the CANDIDATE POOL,
24,347 -> 29,177 signals offered; `mom_hi` ranks that pool and buys the best
it can afford, so a bigger pool yields a better top slice. This is the
"more candidates give mom_hi a better pool" hypothesis item 4 listed and
never tested — now SUPPORTED, but not separated from plain diversification.
Do that separation before believing the mechanism.

### NEXT SESSION STARTS HERE (user's instruction, 2026-09-16) -- ANSWERED, see item 13

> "we will test the rule thoroughly first thing in the next session to see
> if we can add it to our set of rules"

The candidate is **the Pine rule with the RSI leg removed** (HTF EMA filter
+ wick condition, weekly bars / monthly filter). What "thoroughly" has to
mean here, because -0.87 is ONE cell:

1. **Run it over the board's 300 scenarios**, not one. `wf_pine.cells_for`
   already does this; the single cell is `all|2018|1%|1cr|mom_hi`, and this
   project's history is that single cells flatter badly (0 of 2,484 board
   cells have ever cleared the gate).
2. **Charge it correctly** — use the `build()` guard in
   `scripts/pine_ablate_fix.py`, never `wf_pine.ablate`'s ordering.
3. **Put it through the daily-excess test and the BH FDR bar**, which is
   what "beats hold" has to mean here. -0.87 is still NEGATIVE.
4. **Check redundancy against the nine** before adding anything: the
   2026-09-11 pass found the board already holds only 3-9 independent ideas
   in 9 labels. A tenth label that correlates 0.9 with `pair|MW` costs a
   full rebuild and teaches nothing.
5. Only then is registering it worth discussing — `registry.py` is in
   `signals._SUPPORT`, so adding a rule costs a full ~103-150 min rebuild.

Outputs: `output/measurements/pine_ablate_fix_2026-09-16.csv` (14 rows),
`output/logs/pine_ablate_fix_run.log`.

## 13. The RSI-free Pine over all 300 scenarios — RAN 2026-09-16. VERDICT: NO

`scripts/pine_candidate.py`, 3.8 min, no rebuild. **This answers the question
item 12 handed forward and CLOSES it.** The candidate does not join the board.

    python3 -m scripts.pine_candidate        # W only; checkpointed per label

Self-check PASSED both ways: the grid's
`pineW-full|atr-open|all|1|10000000|1|2018|mom_hi` is 6.1 and the `norsi` twin
is 11.1, reproducing item 12's charged-alike table exactly. Build counts match
too (42,787 and 51,088 trades). So this grid and that table are the same
arithmetic, extended from 1 cell to 276.

### Finding 1 — the -0.87 was a 92nd-percentile cell, not a typical one

    label                  cells  med CAGR  med hold  med excess  beat hold  own BH
    pineW-full|atr-open      276      4.00     12.32       -8.97       0.7%       0
    pineW-full|low-close     276      7.45     12.32       -5.32      22.1%       0
    pineW-norsi|atr-open     276      5.30     12.32       -7.50       5.8%       0
    pineW-norsi|low-close    276      6.30     12.32       -6.87      19.2%       0

The one cell item 12 quoted sits at the **92nd percentile** of its own label's
276 cells; the median cell loses by 7.50 CAGR points a year, not 0.87. The full
rule's quoted cell was the 84th percentile. This is the project's oldest lesson
arriving on schedule — a single cell is a maximum over choices nobody made in
advance, and it flatters by ~6.6 points here.

### Finding 2 — "RSI is the leg that hurts" is CONVENTION-DEPENDENT, and flips

Paired scenario by scenario, dropping the RSI leg is worth:

    the Pine's own convention (atr stop, next-open fill)  median +1.60 pts, helps 223/276 (81%)
    the board's own convention (prev-low stop, close fill) median -1.05 pts, helps 109/276 (39%)

Item 12 measured only the first and read it as a fact about RSI. It is a fact
about **RSI under one execution convention**. Under the convention the nine
board rules actually use, removing RSI makes the rule WORSE in three scenarios
out of five. Item 12's four-independent-askings argument was four askings of
one convention, so it did not catch this. The mechanism it proposed (a bigger
candidate pool for `mom_hi` to rank) stays unseparated from diversification and
is now moot for the board question.

### Finding 3 — zero cells at the gate, under the most generous bar available

    label                  cells  smallest p  p<=0.05  by chance  own BH  pooled BH  bonf
    pineW-full|atr-open      276      0.5027        0       13.8       0          0     0
    pineW-full|low-close     276      0.0068        8       13.8       0          0     0
    pineW-norsi|atr-open     276      0.0919        0       13.8       0          0     0
    pineW-norsi|low-close    276      0.0248        1       13.8       0          0     0

"own BH" runs Benjamini-Hochberg on the candidate's 276 p-values ALONE — a bar
no board rule is given, since the board's gate pools all 2,484. Nothing clears
even that. The uncorrected count is **below** the ~13.8 expected by chance in
every row. Median MDE is 12.2-17.1 CAGR pts/yr, so state the power alongside:
this data could not have detected a true edge smaller than that. The finding is
not "the edge is zero", it is "no edge large enough for this test to see, and
the point estimate is negative by 5-9 points".

### Finding 4 — it is NOT redundant. It is distinct, and it loses

    candidate             nearest board rule   raw r  demeaned r  mean raw r
    pineW-full|atr-open   dv|55-20 1TF         0.387       0.129       0.062
    pineW-full|low-close  dv|55-20 1TF         0.616       0.355       0.302
    pineW-norsi|atr-open  dv|55-20 1TF         0.329       0.105       0.040
    pineW-norsi|low-close dv|55-20 1TF         0.495       0.188       0.264

Item 12's step 4 guessed a tenth label correlating 0.9 with `pair|MW`. Wrong:
the nearest twin is `dv|55-20 1TF` at 0.616 raw / 0.355 demeaned, well below
the 0.646 worst surviving pair already on the board. **The Pine is the most
independent idea tested here since `dv|55-20`.** That is exactly why the
negative result is worth recording rather than quietly dropping: it is a
genuinely different rule that still loses to buy-and-hold on 4 cells in 5.

### Where this leaves the board

Nine strategies, unchanged. No rebuild was run and none is warranted — adding
a rule costs ~103-150 min through `registry.py` and would buy a tenth label
whose median cell loses by 6.9 points. **Do not re-open this without a NEW
reason**, and if one appears, the convention split in Finding 2 is the thing to
design around, not the RSI leg.

Two things this run did NOT do, stated rather than implied: it tested `W` only
(the D and M rungs from 2026-09-11 are still mischarged and their numbers must
not be quoted), and it used the cached hold curves, so it inherits whatever the
2026-09-11 board's universe was.

Outputs: `output/pine_candidate_2026-09-16.json`,
`output/measurements/pine_candidate_2026-09-16.csv` (1,104 rows x 18 cols),
`output/logs/pine_candidate_run.log`,
`output/pine_candidate_ckpt_2026-09-11_8cbfd3_dd707a/` (4 pickles, resumable).

### Finding 5 — where it ranks among the nine (asked 2026-09-16, same session)

All 13 rules on the 276 scenarios every one of them carries, ranked on median
daily-excess vs hold. `p<=0.05` is the uncorrected count out of 276.

    rule                    med excess  beats hold  avg rank  best-in-scen  p<=0.05
    pair|MW                      -2.94      27.5%      4.61        75            0
    dv|55-20                     -4.02      31.2%      4.74        39            0
    pineW-full|low-close         -5.29      22.8%      5.68        35            8
    eath|MW                      -5.54      23.9%      4.72        25            0
    dv|55-20 1TF                 -5.74      27.2%      5.68        56            0
    pineW-norsi|low-close        -6.47      17.8%      6.34        14            1
    dv|20-10 1TF                 -7.02      12.7%      6.07        13            0
    pineW-norsi|atr-open         -7.32       5.8%      6.77         5            0
    dv|20-10                     -8.40       6.9%      7.77         5            0
    pineW-full|atr-open          -8.85       0.7%      8.35         0            0
    ema|0                       -13.84       2.5%      9.87         1            0
    pair|MD                     -14.36       4.7%      9.98         5            0
    e1|daily                    -15.01       4.0%     10.41         3            0

**The Pine is mid-pack, not an outlier, and the verdict is unchanged: every
row loses.** Third of thirteen is third-least-bad in a field where the leader
still trails buy-and-hold by 2.94 points a year. Ranking it higher would not
have made it addable; ranking it last would not have made it more wrong.

Three things to take from the table rather than the headline:

- **The RSI leg is worth KEEPING under the board's convention.** `full` beats
  `norsi` 3rd vs 6th, -5.29 vs -6.47. This is Finding 2 again from the ranking
  side, and it is the opposite of item 12's reading. The convention, not the
  leg, was doing the talking.
- **`pineW-full|low-close` is the only rule on this board with any nominally
  significant cells at all — 8, where all nine board rules have 0.** Do not
  read that as an edge: 8 of 276 is BELOW the ~13.8 expected by chance. Read
  the nine's zero instead. If the nine had no edge, ~124 of their 2,484 cells
  would clear 0.05 by luck; 0 do. **The nine are not "no edge", they are
  reliably WORSE than hold**, and that is a stronger statement than the board's
  empty-gate line makes. Worth its own test.
- `avg rank` and `median excess` disagree slightly (`eath|MW` ranks 4th on
  average but 4th-worst-but-one on median), so quote both or quote neither.

Fair comparison check: the board cells and these come through the same path —
`wa.spread_of` then `portfolio.run`, which is what `wf_attach`'s SELF_CHECK
pins to a board cell. The Pine is a weekly rule against mostly daily ones;
that is a difference in the rules, not in the charging.

## 14. The nine are not unproven — they are reliably WORSE than hold — RAN 2026-09-16

`scripts/hold_dominance.py`, seconds, no rebuild. Reads the built payload's
`daily_excess` block and turns the existing test around: `excess_stats` stores
`t_hac`, so the left tail is `norm_sf(-t)` and nothing is recomputed.

### The finding

    direction                  p<=0.05   expected by chance     BH   Bonferroni
    rule BEATS hold                  0                  124      0            0
    rule is WORSE than hold        872                  124    474           63

**The board's "0 of 2,484 clear the gate" has been under-read.** A board of
rules with no edge either way would put ~124 cells past 0.05 in BOTH
directions by luck. It puts 0 one way and 872 the other. That is not an
absence of evidence; it is evidence of absence of edge, with a sign.

**63 cells clear BONFERRONI, and that is the number correlation cannot touch.**
Bonferroni is valid under arbitrary dependence, so however tangled these cells
are — 9 rules over overlapping universes and start years, the rules themselves
at n_eff 4.0 — a cell clearing it is worse than hold at a 5% bar taken across
all 2,484 at once. The BH count (474) assumes positive dependence, which this
project already relies on in the other direction.

### Per rule, which is the largest honest sample size here

    rule            med t   med excess   t<0    worse p<=.05   beats p<=.05
    dv|55-20        -0.42        -4.02   65%              38              0
    pair|MW         -0.65        -2.95   71%              54              0
    dv|55-20 1TF    -0.74        -5.73   70%              75              0
    dv|20-10        -0.97        -8.39   92%              86              0
    eath|MW         -1.00        -5.54   79%              80              0
    dv|20-10 1TF    -1.15        -7.02   86%              62              0
    ema|0           -1.88       -13.84   97%             156              0
    e1|daily        -1.92       -15.01   93%             159              0
    pair|MD         -2.04       -14.36   93%             162              0

**9 of 9 negative.** As a sign test that is p = 0.002 if the rules were
independent and **p = 0.0625 at the measured n_eff of 4.0** — quote the second.
Nine labels leaning one way is nearer four coin flips than nine, and on its own
that line does NOT clear 5%. The cell-level Bonferroni count is the load-bearing
evidence; the sign test is corroboration, not proof.

`dv|55-20` is least bad on this reading too (median t -0.42), which is the
third independent time it has come out best. The three worst are the EMA-family
rules, at -13.8 to -15.0 CAGR points a year.

### It is everywhere, not one bad corner

Median t is negative in every universe (-0.62 to -1.61), every start year
(-0.69 to -1.93) and every priority (-1.01 to -1.26). Worst: `large` and `mid`
caps (median excess -9.66 and -10.27), and start year 2012. Best (least bad):
`recent` and start 2018. **No slice of the grid is positive.**

### The limit on the claim

Median MDE is 16.28 pts/yr against a median measured shortfall of -8.34, so
most individual cells cannot separate "worse" from "no different" — only **453
of 2,484** have a shortfall exceeding their own detectable edge. The claim that
survives is about the BOARD, not about every cell: the distribution is shifted
left, decisively, and in 63 cells the shortfall is large enough to clear a
dependence-free bar on its own.

### What this changes

Nothing on the dashboard, and no rebuild — but it changes the sentence the
project should be saying. "No rule has been shown to beat buy-and-hold" is
true and too kind. **These nine rules, as configured, lose to buy-and-hold, and
the data can prove it in a way it could never prove the reverse.** That is a
finding, not a failure: it is the strongest result this board has produced.

Outputs: `output/measurements/hold_dominance_2026-09-16.csv` (2,484 rows x 12
cols), `output/logs/hold_dominance_run.log`.

## 15. Does "the nine lose to hold" generalise? Mostly yes -- RAN 2026-09-16

`scripts/oos_generalise.py`, 16.1 min, no rebuild. All 19 rules' cached trades
(the 9 on the board + the 10 cut on 2026-09-11, still on disk with matching
universe and price hashes) re-run through the board's own 276-cell grid, with
the identical HAC daily-excess test applied three times per cell: whole span,
and each side of a **pre-registered** 2017-01-01 split.

**SELF-CHECK PASSED: 2,484 board cells, 0 disagree with `dashboard.json`'s
stored `t_hac`.** This is the same arithmetic item 14 was made on.

### Leg 2 first -- the genuine holdout, and it replicates

The four rules cut for DUPLICATION carry no quality signal by measurement
(Spearman(rank, twin r) = -0.18, p = 0.56) and took no part in establishing
item 14. They behave like the nine:

    cohort     rules  cells  med exc   neg     worse u/BH/bonf   BEATS u/bonf
    board          9  2,484    -8.34   9/9          872/474/63            0/0
    dup-cut        4  1,104    -8.96   4/4          392/213/21            8/0
    rank-cut       6  1,656   -10.95   6/6          878/570/40            0/0

**4 of 4 negative, median shortfall -8.96 against the board's -8.34, and 21
cells clear Bonferroni as worse.** The finding is not about the nine.
(`rank-cut` was selected ON performance and can only agree; shown so the
exclusion is visible, excluded from the headline.)

**MY PREDICTION WAS TOO STRONG AND IS PARTLY FALSIFIED.** I wrote that the
holdout would clear *zero* cells in the favourable direction. It cleared **8**
uncorrected (0 at BH, 0 at Bonferroni). Against ~55 expected by chance that is
still seven times FEWER than luck would give, so the direction is untouched --
but "zero" was the board's number, not a property of rules of this kind, and I
should not have predicted the board's exact extremity for held-out rules. The
board's 0/2,484 is very slightly more extreme than an unselected sample gives.

### Leg 1 -- the shortfall is in both halves, but it is SHRINKING

864 of 2,484 cells have >= 250 days each side of 2017 (the 2006 and 2012 start
years). Every rule is negative in both halves -- prediction held, 9 of 9 -- but
the two halves are not the same size of finding:

    half    cells   worse u/BH/bonf   better u   expected by chance (each way)
    EARLY     864       434/315/82           2                              43
    LATE      864       274/101/ 1           1                              43

    rule           med t E   med t L   exc E    exc L
    e1|daily         -3.33     -1.54  -20.88   -11.20
    pair|MD          -2.74     -1.93  -19.20   -12.55
    ema|0            -2.55     -1.58  -17.70   -12.69
    dv|20-10         -1.99     -1.28  -14.06    -8.58
    dv|20-10 1TF     -1.19     -0.90   -8.18    -4.90
    pair|MW          -0.88     -0.09   -6.06    -0.77
    dv|55-20         -0.80     -0.74   -5.94    -5.06
    dv|55-20 1TF     -0.41     -0.22   -2.73    -1.17
    eath|MW          -0.55     -1.16   -3.20    -5.04

**8 of 9 shortfalls shrank, several by half, and `pair|MW` has all but closed
(-6.06 -> -0.77).** Only `eath|MW` got worse. The Bonferroni count collapses
from 82 to 1. **This is NOT mainly a power artefact**: the halves are
comparable in length (2,706 vs 2,396 days on the 2006 cells), so the driver is
a genuinely smaller effect, not a shorter window.

Cell-level agreement between halves is also weaker than the medians suggest --
`pair|MW` and `dv|55-20 1TF` are negative in both halves in only 44% and 48% of
cells.

### What survives, and what has to be narrowed

SURVIVES: the claim is about rules of this kind, not the nine labels. The
holdout cohort, the rank-cut cohort and the out-of-project Pine rule (item 13,
0 of 1,104 favourable) all lose by the same margin.

NARROWED: item 14's strength is period-dependent. The honest sentence is now
**"these rules lost to buy-and-hold over 2006-2026, decisively so before 2017
and more weakly after"** -- not "they lose, full stop". Whether the narrowing
is regime (a decade in which holding Indian equities was very hard to beat),
decay, or noise is NOT answered here and is the obvious next question.

STILL UNTESTED, and no leg here touches it: one market, one country, one asset
class, one cost model, one realised price path. Nothing was out of sample in
the only sense that would settle it -- data that did not exist when the rules
were written.

Outputs: `output/measurements/oos_generalise_2026-09-16.csv` (5,244 rows x 17
cols), `output/oos_generalise_2026-09-16.json`, `output/logs/oos_generalise_run.log`.

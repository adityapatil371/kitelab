---
name: kitelab-history
description: Dated change record for kitelab — EMA band removal, ATH filter repair, 2026-09-07 audit, universe/listing-break forensics, stamp fixes. Load when a number or note seems to contradict an older one.
---

# kitelab change history

Relocated from CLAUDE.md on 2026-09-08; the code's docstrings remain the
primary record. Newest-relevant first.

## The 2026-09-07 audit and repair

A second adversarial audit (six reviewers, one per layer) and the repair that
followed the owner's five decisions. The findings and the fix plan are the
owner's private artifacts (`kitelab-audit-2026-09-07`, `kitelab-fix-plan-2026-09-07`
in the claude.ai artifact gallery); the code carries the measurements. The short
form, so nobody re-derives it:

- The benchmark, the credibility t, the luck hurdle, the shuffle test and the
  walk-forward rule were each lenient in the same direction (see the
  kitelab-validation skill).
- The ATH "filter" was a second entry rule (below).
- Listing breaks, demergers and phantom pre-listing bars were traded (below).
- The stamp did not cover the numbers path (below).
- Bitcoin ₹2L cells refused sub-coin positions (`portfolio.run` read the global
  fractional flag inside its impact loop); the GOLD curve kept a second set of
  books with no margin model. There is one ledger now: `run()` records it and
  `daily_curve` only marks it to market; `test_account.OneBook` pins curve end
  == final to the rupee for every trade shape.
- Buckets did not partition the universe (below).
- The page printed "—" for Sharpe and Sortino (`r.r.sharpe`), hardcoded "the 24"
  in the banner, and called two different fields "Invested".

Expect the board to look worse than it did on 2026-09-05. That is the repair
working. The owner's decisions: gate on raw CAGR vs raw hold; alpha 0.05;
walk-forward wins mean "beats hold"; the accidental ATH re-entry rule removed
and the deliberate one designed off-board (`scripts.ath_band`); the holdout
script deleted.

## The EMA band is gone (2026-09-05)

Every EMA family now trades the bare 20-EMA cross. `BANDS = [0.0]`,
`SOLO_BAND = 0.0`, and `backtest.BAND` / `timeframes.BAND` moved to `0.0` with
them so a bare `simulate("HAL")` matches the board. `ATH_BAND = 0.10` is
untouched — it is the distance-from-all-time-high filter that *defines* the
`eath` family, not an EMA buffer.

Three consequences worth carrying into any reading of the board:

- **The churn the band existed to prevent is back.** `backtest.py` records
  what it bought: without hysteresis, entry and exit share one knife-edge. Median
  hold is 3–4 sessions on rules filtered by *monthly* EMAs, and 29–31% of trades
  re-enter the same stock the next bar (re-measured 2026-09-07; the original
  note said 20%). Expect trade counts up and cost drag up.
- **The parameter-plateau control is gone with it.** Six adjacent band settings
  moving together were the only answer this project had to "is 2% a plateau or a
  lucky spike?". One setting cannot answer it. Nothing replaced it on the board;
  `scripts.ath_band` runs three bands for its own rule, off the board.
- **`EMA_MWD_RETIRED` had to go**, and it is the uncomfortable part. It held
  `{0.0}` because M/W/D at a 0% band was cut *earlier the same day* as the worst
  variant on the board — median MAR −0.14 across all 50 priority × risk × start
  combinations, positive in 7 of them. With `BANDS = [0.0]` that guard would have
  deleted the family, so the variant is back as the **only** M/W/D row. Read its
  Validated gate, not its rank.

`registry.py` is in the cache stamp (`signals._SUPPORT`). It holds parameter
values baked into every trade, and this change proved the gap: `pair`, `e1` and
`eath` changed what they trade while their cache names (`EMA_WD`,
`EMA_daily_only`, `EMA_ath10`) stayed identical. The cost is that any registry
edit — a label typo included — invalidates every cache.

## The ATH filter, asked on five stacks (2026-09-05), made a filter (2026-09-07)

`eath` was one row — the M/W/D stack plus "only buy within 10% of the running
all-time high". It is five, one per entry in `registry.ATH_STACKS`
(M/W/D, M/W, Q/M, Q/D, W/D), because one stack could not answer the family's own
question. Two things were built to make that possible:

- **Q/M and Q/D did not exist.** Added to `timeframes._stack_frames` and to
  `PAIRS`. Q/M trades on **monthly** bars — the coarsest base on the board, ~100
  of them, and the stop is the entry month's low, so risk-per-share is large and
  trade counts are tiny. Read its count before its return.
- **`timeframes.stack_signal` had no ATH support**; the filter lived only in
  `backtest.py`, which is why the family had one row. It takes `ath_band` now,
  entries only. The running high is over the **base frame's own closes**, so a
  weekly row compares a weekly close to the highest weekly close — matching
  `pine/ema_ath_band.pine` rather than diverging from it.

**What the 2026-09-07 audit found, and what changed.** The band was ANDed into
`entry_ok` *before* the rising-edge test, so when the stack was already up and
price merely climbed back within 10% of its high the walk recorded a "fresh"
signal with no EMA cross. On a 250-stock sample from 2018 those cross-less
entries were 59% of the M/W row's trades and 71% of its net, and 83% / 73% on
Q/M. The +12.3 / +11.6 / +4.3 / +4.2 / −11.7pp attributions published on
2026-09-05 compared two different entry rules and are void. Since 2026-09-07 the
rising edge is computed on the bare stack and the band only *declines* a cross
(`backtest.simulate`, `timeframes.simulate_variant`); on HAL the M/W/D ATH row
went from 46 trades (7 not in its control) to 39 (0 not in its control). The
accidental pullback-recovery rule was removed, not renamed: the owner's own ATH
rule is being designed deliberately in `scripts/ath_band.py`, off the board.
Re-measure the filter's effect from the rebuilt payload before quoting one.

## Grid history

- **The scanning-pool axis and the Breadth sweep are gone** (2026-09-03). They
  were one measurement wearing two names, and what they found — a small account
  cannot fund what a wide scan offers — is now on every row as `skipped_cash`,
  `skipped_tiny_cash` / `skipped_tiny_risk` and `exposure`, which say it without
  a sweep. Read all three: on W/D at ₹2L "unaffordable" alone was 43% of signals
  and the cash scraps refused under the fee floor another 55%, so the true
  turned-away-for-cash figure is ~98%. `_draw_baskets` no longer exists; do not
  reintroduce a per-strategy draw, which would stop the table comparing rules
  and start it reporting who drew the better stocks.
- **Buckets** (2026-09-07): until then the 170 stocks with no positive-turnover
  bar before the 2018 cut were in `all` and in no bucket, so 73 + 116 + 640 =
  829 and "small caps" silently excluded every post-2017 listing — the cohort
  most exposed to survivorship. They are the `recent` bucket now and the five
  buckets partition the universe. Still unfixed one level up:
  `screen_universe`'s *admission* gate measures turnover over full history, so
  these are stocks that turned out to be liquid (499 of 999 would have failed it
  on pre-2018 bars alone; only 390 have bars from January 2006, so the 2006
  start runs on a universe that grows as listings land).

## Universe forensics

- **1,000 stocks since 2026-09-07** (ORIENTPPR came back: its −47% day was a
  demerger, not the unadjusted split it was excluded as). The docstrings still
  argue the case in terms of "500" and "the 399", which were the sizes on
  2026-09-03.
- **Listing breaks** (`frames.LISTING_BREAK_DAYS = 180`, `config.DEMERGERS`,
  `config.HISTORY_STARTS`, 2026-09-07). Kite serves bars under reused tokens
  before a stock listed (STARHEALTH had 58 bars from 2016, listed 2021-12-10), a
  suspension is a four-year gap the old rules let a position span (ROTO
  2018→2022, one cached trade at −53.5 R), and Kite adjusts splits and bonuses
  at serve time but **not demergers** (SIEMENS 2025-04-07 −35%, TATACHEM
  2020-03-04 −56%: 14 names, 440 cached trades net −₹25.5L booked as real
  losses). Bars before the last break are dropped; the cut is severe by design
  (SIEMENS keeps 343 bars). HINDPETRO before 2015 is a peer-tracking series with
  amplified moves and starts there. `data_audit` now checks for all of it.
- **The 101/399 in-sample/holdout split was dropped on 2026-09-03** and its last
  traces (`cfg.in_sample`, `cfg.out_of_sample`, `scripts/universe_bias.py`)
  deleted on 2026-09-07. Train/test catches a *fitted* model that memorised its
  training rows. These rule shapes were taught in class before the repo read a
  candle — 20 EMA, the M/W/D stack, ADX>25 pullbacks, Donchian 20-10 and 55-20 —
  so there was nothing to memorise, and holding back stocks cost power without
  buying validity. What the split measured, for the record: the old 101 were
  winners (large-cap median buy-and-hold ≈ +11.2% merged against +9.7% for the
  399 alone). The 399 list survives only at commit `2a3e4d2`.

## Stamp history

The two-stamp scheme (2026-09-07) replaced a hand-kept `_CODE` list that
omitted `holygrail.py`: a rewritten strategy went on serving superseded trades
while `refresh` said "already current", and a day of published numbers was
invalid. The audit also touched each account-path module (`portfolio.py`,
`curves.py`, `validation.py`, `contracts.py`, `scripts/dashboard_data.py`) on
an isolated copy and the old digest did not move — which is why the dashboard
stamp now includes `signals._ACCOUNT`. The validation record's cache
(`_validation_summary_v9`) carried the narrow stamp until 2026-09-07, so a
`validation.py` edit left it served as current. `refresh` also used to decide
whether to rebuild first, then clean, then skip the rebuild the clean had made
necessary; it cleans before it decides now.

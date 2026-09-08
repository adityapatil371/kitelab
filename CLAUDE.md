# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

kitelab — a local backtesting workbench for rules-based swing trading on NSE
equities, built on Kite Connect. Pure Python + stdlib HTTP; no framework, no
database, no test runner.

**Where the record lives.** `HANDOVER.md` was retired on 2026-09-03 (commit
`3ab77d3`) because later sections kept superseding it in place. The running
record is now the docstrings: every rule, exclusion and retired axis carries the
date and the measurement that moved it (`kitelab/config.py`,
`scripts/dashboard_data.py`, `kitelab/validation.py` are the dense ones).
`AUDIT_REPORT.md` and `FIX_REPORT.md` are the 2026-08-31 adversarial audit and
the 17-commit response to it — historical, and the numbers in them predate the
universe merge. Do not add a new standing handover; put the why next to the code.

## Commands

Which Python depends on where you are:

- **On the host (macOS)** — `./.venv/bin/python`. The venv is 3.14 and was built
  by Homebrew.
- **In the dev container (Linux)** — that venv is a dead symlink
  (`pyvenv.cfg` points at `/opt/homebrew/...`). Use plain `python3`: it is 3.12
  with pandas 3.0.5 and pyflakes installed, and everything below runs under it.

```bash
# view the dashboard — no keys, no venv (stdlib only). Run on the HOST, not in the container.
./run_dashboard.sh                       # port 8765; ./run_dashboard.sh 9000 for another

# rebuild whatever is stale — no keys, needs pandas
python3 -m scripts.refresh
python3 -m scripts.refresh --check       # report staleness, change nothing
python3 -m scripts.refresh --force
python3 -m scripts.refresh --stocks-only # skip the Bitcoin/GOLD stage

# BEFORE a rebuild -- the whole build path over 3 symbols (one per bucket) into a temp
# dir. Catches what import cannot: a NameError inside main(), a payload key the
# page reads and the build stopped emitting, a stage that silently produces
# nothing. Five of the six rebuilds on 2026-09-03 were spent finding faults this
# would have caught in under a minute.
python3 -m unittest discover -s tests -t .          # 350 tests, ~1s
python3 -m unittest tests.test_validation -v        # one module
python3 -m unittest tests.test_validation.BootstrapOneShape       # one case
python3 -m pyflakes kitelab scripts tests           # undefined names, instant
python3 -m scripts.preflight                        # the build path, one symbol per bucket, ~2 min

# does the page still render the file just built? (needs node, not pandas)
node scripts/check_dashboard.js

# fetch new data — the only job needing a Zerodha account
set -a; source ~/.secrets/all.env; set +a
python3 -m scripts.login                 # once a day; tokens die ~06:00 IST
python3 -m scripts.backfill --symbols-file <list> --daily-only

# health / audits
python3 -m scripts.cache_status
python3 -m scripts.data_audit
python3 -m scripts.validate              # the six trader's checks, printed
python3 -m scripts.bootstrap             # edge vs luck + the multiple-testing summary
python3 -m scripts.ath_band              # the owner's ATH band rule, OFF the board, writes output/
python3 -m scripts.backup_inputs         # refresh runs this as its last step
```

`scripts/check_dashboard.js` is the only JS test; `unittest` is the only Python
one. There is no `pytest`.

**Data lives outside the repo.** `config._resolve()` finds it rather than
assuming: `$KITELAB_DATA_DIR` / `$KITELAB_CLEAN_DIR` win, else `/data/{raw,clean}/kitelab`
(the container mount), else `$HOME/data/{raw,clean}/kitelab`, else `./data`.
RAW is read-only; CLEAN is the only directory the analysis side writes, and the
only one it reads prices from. `data/` in the repo holds just `data/keep/`.

## Adding a strategy

Append a `Strategy` to `kitelab/registry.py` (or call `register()`). Nothing else
needs editing: it appears in the compare grid, in `kitelab.validation` and in
`scripts.bootstrap` automatically, measured on the same stocks and the same
account as everything already there.

Each `Strategy` names **the module that produces its trades**, and
`signals.stamp()` derives the cache-code digest from that list. A rule that is
on the board is therefore in the stamp by construction. This is not decoration:
`holygrail.py` was missing from the old hand-kept `_CODE` list, so a rewritten
strategy went on serving superseded trades while `refresh` said "already
current", and a day of published numbers was invalid.

One thing the machinery cannot enforce: **every rule you add raises the bar for
all of them.** The luck hurdle counts how many variants clear a 95% test against
the ~5% that clear it by chance, so a 20th strategy makes "one of them looks
good" less impressive, not more. It also multiplies the grid — 540 new cells per
variant at the current axes.

## The grid, and what its axes mean

**10,260 cells** (19 variants x 540: five equity universes x 5 start years x 5
priorities x 2 risks x 2 capitals = 500, plus the two single-name asset
universes at one priority = 40), keyed
`strategy|variant|universe|risk|capital|fill|start|priority`. Take the live
count from `refresh --check` or the payload; the arithmetic is what to trust.

| axis | values | source |
|---|---|---|
| strategy·variant | 19 (7 families) | `registry.py` |
| universe | `all` / `large` / `mid` / `small` / `recent`, + BITCOIN / GOLD | liquidity buckets, below |
| risk | 0.5%, 1% | `RISKS` — the one lever known to reorder the table |
| capital | ₹2L, ₹1cr | `CAPITALS` — ₹1cr added to test the size cap |
| fill | Realistic only | `GRID_FILLS = ["1"]` |
| start year | 2006, 2012, 2018, 2022, 2024 | `START_YEARS`, default 2018 |
| priority | 5 orderings (1 on single-name universes) | `portfolio.PRIORITIES` |

Every one of those lists carries, in a comment above it, the measurement that set
it — read those before widening one. Each is a multiplier on every other.

Two axes carry findings rather than settings:

- **Universe** is *liquidity buckets, point-in-time*, and since 2026-09-07 they
  **partition** the universe. Membership is median daily traded value using only
  bars before `START_DEFAULT` (2018); stocks with no positive-turnover bar before
  the cut are the `recent` bucket ("listed after 2017"). Until 2026-09-07 those
  170 were in `all` and in no bucket, so 73 + 116 + 640 = 829 and "small caps"
  silently excluded every post-2017 listing — the cohort most exposed to
  survivorship. One classification, not one per start year: two cells labelled
  "large" must mean the same stocks. Still unfixed one level up:
  `screen_universe`'s *admission* gate measures turnover over full history, so
  these are stocks that turned out to be liquid (499 of 999 would have failed it
  on pre-2018 bars alone; only 390 have bars from January 2006, so the 2006
  start runs on a universe that grows as listings land).
- **Signal priority** — which signal wins the cash when you cannot fund them
  all. Was a hardcoded constant; it swings CAGR by up to **20 points**, more than
  the gap between strategies. No ordering wins consistently and Q/M/W's best is
  M/W/D's worst, which is what noise looks like — do not pick the winner from
  that table without running `scripts.bootstrap` over it. Single-instrument
  universes are dealt only the default: with one position per symbol there is
  never a second candidate, so all five orderings are byte-identical there.

**The scanning-pool axis and the Breadth sweep are gone** (2026-09-03). They were
one measurement wearing two names, and what they found — a small account cannot
fund what a wide scan offers — is now on every row as `skipped_cash`,
`skipped_tiny_cash` / `skipped_tiny_risk` and `exposure`, which say it without a
sweep. Read all three: on W/D at ₹2L "unaffordable" alone was 43% of signals and
the cash scraps refused under the fee floor another 55%, so the true
turned-away-for-cash figure is ~98%. `_draw_baskets` no longer exists; do not
reintroduce a per-strategy draw, which would stop the table comparing rules and
start it reporting who drew the better stocks.

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

## The ATH filter, asked on five stacks (2026-09-05), and made a filter (2026-09-07)

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

**`pair|QM` and `pair|QD` are controls, not strategies.** Every ATH row needs its
own unfiltered twin or its score cannot be read — credit is unattributable
between the filter and the stack under it. Do not add an ATH stack without one.
`tests/test_ath_filter.py` asserts the property that makes the twin readable:
every `eath` trade is a trade its control also takes.

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

## Validation — the "is this real" layer

`kitelab/validation.py` holds the math; `scripts/validate.py` and
`scripts/bootstrap.py` are thin CLI wrappers around it, and
`scripts/dashboard_data.py` calls the same functions so the page and the CLIs
cannot disagree. It lives in `kitelab/` rather than `scripts/` because both
scripts import helpers *from* `dashboard_data`, so the reverse import would be
circular. Rewritten on 2026-09-07 after the audit; every changed rule carries
what it was, what it is, and the measurement.

**Different from `tests/`.** Those check the code does what it was told, which
says nothing about whether a strategy makes money. These are the checks a trader
runs before believing a rule.

The Compare table's **Validated** column is a *gate, not a score*: a row failing
any one of five checks ranks below every row that passes all five, and MAR breaks
ties within each group. The five, as they are now:

1. **beats buy & hold** — the on-screen cell's CAGR (net of costs) against an
   **equal-weight portfolio** of the stocks in the on-screen universe from the
   on-screen start year (gross of costs; `hold_cagr_by_scenario["all|2018"]`).
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

**The credibility statistic.** `t_iid` (mean R over std/√n) treated 40k–240k
overlapping trades as independent: pair|MW scored 20.3. `t_cluster` clusters the
standard error by entry quarter (Liang–Zeger): 4.1. `t_stat`, the gate,
subtracts `drift_r` — the mean R that *random* entries on the same stocks with
the same stop fraction, holding length and spread would earn — because these
survivors drift up and random timing earns that drift too. Read the sign of
`drift_r`: rules with tight daily stops get a *negative* drift (random timing
with a 1% stop loses to noise) so their t rises; rules with weekly stops get a
positive one. `t_taken` on every grid row is the same clustered t on the trades
*that* account actually took (~1.3 for the old headline rows at ₹2L). The
percentiles come from a calendar-quarter block bootstrap, not 20-trade blocks.

Checks 1 and 3 recompute for the on-screen Universe × Priority × Capital (1 for
Universe × start year); 2, 4 and 5 recompute for Universe only. Checks 2 and 4
**do** run `portfolio.run`, at ₹2L / 1% / most-liquid-first whatever is on
screen — the docstrings and tooltips now say so. Below `MIN_TRADES = 30` every
function returns `None`/empty rather than raising; `preflight` lowers it to 5
and the permutation rounds to 10 so its 3-symbol build exercises every branch.

`validation_summary` carries the multiple-testing arithmetic (`tried`, `n_eff`,
`avg_correlation`, `alpha`, `hurdle`, `expected_best`, `expected_by_chance`,
`cleared`). Quote that spread, never a single headline number. The hurdle
guards the t-statistic only; the MAR the top row is sorted by is the maximum
over 540 cells per variant with no correction, on an axis (priority) the project
itself calls noise.

## The 2026-09-07 audit and repair

A second adversarial audit (six reviewers, one per layer) and the repair that
followed the owner's five decisions. The findings and the fix plan are the
owner's private artifacts (`kitelab-audit-2026-09-07`, `kitelab-fix-plan-2026-09-07`
in the claude.ai artifact gallery); the code carries the measurements. The short
form, so nobody re-derives it:

- The benchmark, the credibility t, the luck hurdle, the shuffle test and the
  walk-forward rule were each lenient in the same direction (Validation, above).
- The ATH "filter" was a second entry rule (ATH section, above).
- Listing breaks, demergers and phantom pre-listing bars were traded (Universe).
- The stamp did not cover the numbers path (Stamps).
- Bitcoin ₹2L cells refused sub-coin positions (`portfolio.run` read the global
  fractional flag inside its impact loop); the GOLD curve kept a second set of
  books with no margin model. There is one ledger now: `run()` records it and
  `daily_curve` only marks it to market; `test_account.OneBook` pins curve end
  == final to the rupee for every trade shape.
- Buckets did not partition the universe (Grid).
- The page printed "—" for Sharpe and Sortino (`r.r.sharpe`), hardcoded "the 24"
  in the banner, and called two different fields "Invested".

Expect the board to look worse than it did on 2026-09-05. That is the repair
working. The owner's decisions: gate on raw CAGR vs raw hold; alpha 0.05;
walk-forward wins mean "beats hold"; the accidental ATH re-entry rule removed
and the deliberate one designed off-board (`scripts.ath_band`); the holdout
script deleted.

## Layout

- `kitelab/` — the library. Strategy and data logic lives here, never in scripts.
  - `config.py` — universe, exclusions with the measurement that justifies each,
    `DEMERGERS` / `HISTORY_STARTS`, `merged`, and `_resolve()`.
  - `frames.py` / `fetch.py` / `timeframes.py` — storage and resampling.
  - `backtest.py`, `darvas.py`, `holygrail.py`, `strategies.py`, `timeframes.py` —
    the strategies. `trailing.py`, `sizing.py`, `slippage.py`, `levels.py`,
    `indicators.py`, `excursion.py`, `curves.py` support them.
  - `portfolio.py` — one pot of money, cash-constrained; the account-level truth.
  - `validation.py` — edge-vs-luck math (above). `screener.py`, `contracts.py`
    (hand-entered lot sizes and margins — `unverified_multipliers()` warns).
  - `signals.py` — stamped signal caches (see below). `registry.py` — the board.
  - `dashboard_server.py` — stdlib HTTP; `/`, `/api/dashboard`, `/api/status`, `/api/curve`.
- `scripts/` — thin entry points, all run as `python -m scripts.<name>`.
  `dashboard_data.py` precomputes the whole grid into `dashboard.json`.
- `tests/` — 350 hermetic tests, no price files or network (`support.py`
  patches `_daily_closes`, `liquidity_at`, `ema_stack_signal` and `frames.daily`).
  Stdlib `unittest`; no runtime test dependency. Three kinds worth knowing:
  **property** tests (the books balance, a later bar cannot change an earlier
  trade), a **golden** test that fails when a refactor silently moves numbers,
  and an **oracle** test running the same bars through `backtesting.py` — the
  only test that could catch a mistake made consistently in both the code and
  its own tests. It skips if that dev extra is absent.
  `scripts/preflight.py` is the integration test, `scripts/check_dashboard.js`
  the page one.
- `web/dashboard.html` — the entire UI, one file, **two views: Compare and
  Detail** (`VIEWS` at the bottom). Nothing is simulated in the browser: every
  control selects among precomputed results, and the page only derives vs Hold,
  the Validated verdict and the walk fraction from precomputed parts. The Compare table
  exports to CSV in one click — CSV not `.xlsx` because the viewer is stdlib
  only, and Excel opens CSV anyway.
- **Everything in the payload is displayed.** Sections nothing rendered were
  removed on 2026-09-03 (`scaleout`, `scaleout_r`, `tradestats`, `timeframes`,
  `nifty`); the scale-out sweep alone cost ~64s of every build to produce a key
  the page never read. If you add a payload key, add the view with it. Two keys
  currently serialize *empty* rather than absent: `waterfall` needs all four fill
  modes and only `"1"` is gridded. `assets`/`single_name` are populated again as
  of the 2026-09-05 rebuild — they are empty only when a build is run
  `--stocks-only`, which is what had produced the previous payload.
- `pine/` — TradingView mirrors of the strategies (4 scripts).
- `data/` is gitignored except `data/keep/`, which holds the four things nothing
  can rebuild (`levels.json`, `accepted.txt`, the in-sample/holdout split, the manifest).
- `README.md` is stale — it describes "Stage 1 only" over four symbols with a
  ~30 minute rebuild. Its **Timeframes**, **2026-08-03 closing-auction session
  change** and **Known limits** sections are still accurate and are the only
  place that reasoning is written down.
- Secrets live only in `config.local.toml` (gitignored). `config.load()` never
  reads them; only `auth.py` does, via `config.require_secrets()`.

## Three programs, deliberately separate

| | Zerodha account | pandas |
|---|:---:|:---:|
| `scripts.backfill` — fetch | yes | yes |
| `scripts.refresh` — clean + rebuild | no | yes |
| `./run_dashboard.sh` — view | no | no |

Keep that separation. Fetching can only run when Zerodha is up; refreshing is
pure local computation and must never be blocked by it; viewing must work with
neither.

## The universe, and what may be quoted

- **One universe** (`cfg.merged`), **1,000 stocks** since 2026-09-07 (ORIENTPPR
  came back: its −47% day was a demerger, not the unadjusted split it was
  excluded as). Five buckets partition it by the 2018 liquidity cut; take the
  counts from `scripts.refresh --check`. The docstrings still argue the case in
  terms of "500" and "the 399", which were the sizes on 2026-09-03.
- **History restarts at listing breaks** (`frames.LISTING_BREAK_DAYS = 180`,
  `config.DEMERGERS`, `config.HISTORY_STARTS`, 2026-09-07). Kite serves bars under
  reused tokens before a stock listed (STARHEALTH had 58 bars from 2016, listed
  2021-12-10), a suspension is a four-year gap the old rules let a position span
  (ROTO 2018→2022, one cached trade at −53.5 R), and Kite adjusts splits and
  bonuses at serve time but **not demergers** (SIEMENS 2025-04-07 −35%,
  TATACHEM 2020-03-04 −56%: 14 names, 440 cached trades net −₹25.5L booked as
  real losses). Bars before the last break are dropped; the cut is severe by
  design (SIEMENS keeps 343 bars). HINDPETRO before 2015 is a peer-tracking series
  with amplified moves and starts there. `data_audit` now checks for all of it.
- **The 101/399 in-sample/holdout split was dropped on 2026-09-03** and its last
  traces (`cfg.in_sample`, `cfg.out_of_sample`, `scripts/universe_bias.py`)
  deleted on 2026-09-07. Train/test catches a *fitted* model that memorised its
  training rows. These rule shapes were taught in class before the repo read a
  candle — 20 EMA, the M/W/D stack, ADX>25 pullbacks, Donchian 20-10 and 55-20 —
  so there was nothing to memorise, and holding back stocks cost power without
  buying validity. What the split measured, for the record: the old 101 were
  winners (large-cap median buy-and-hold ≈ +11.2% merged against +9.7% for the
  399 alone). The 399 list survives only at commit `2a3e4d2`.
- **So the live exposure is multiple testing, not contamination.** 19 variants
  ranked and a winner reported. The hurdle is a family-wise 95% bar at `n_eff`
  (≈2.41 at 6.2 effective trials, average correlation 0.42); before 2026-09-07 it
  was 1.32 and every variant cleared it. `kitelab.validation` measures this
  directly and the page shows it above the table — quote that, not a single
  headline number.
- **Survivorship is untouched and remains the largest known bias here**
  (~4.9pp/yr). The universe comes from the current instrument list. What changed
  on 2026-09-07 is that the credibility gate tests each rule against random
  entries on the same survivors, so the drift they carry is no longer credited to
  the rule. No number in this project is a forecast.
- The class spreadsheets (`*.xlsx`) are hand-picked examples, not backtests.
  Recover the intent behind a rule from them; never try to match their numbers.

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

## Stamps and caches

Signal caches and `dashboard.json` record the universe, the price files and the
code they were built from, and refuse themselves when any of the three moves.
Since 2026-09-07 there are **two stamps** (`signals.stamp(symbols, account=)`):

- **Signal caches** depend on the producer modules (derived from
  `kitelab.registry`, so a registered strategy is in the stamp by construction)
  plus `signals._SUPPORT`. This replaced the hand-kept `_CODE` list that omitted
  `holygrail.py` and invalidated a day of numbers.
- **The dashboard** depends on those *and* `signals._ACCOUNT`: `portfolio.py`,
  `curves.py`, `validation.py`, `contracts.py` and `scripts/dashboard_data.py`.
  The audit touched each of those on an isolated copy and the old digest did not
  move, so an edit to the account engine or the gates left `refresh` saying
  "already current". `tests/test_stamps.py` walks the build's import graph and
  fails on any reachable module left out of either list.
- **The validation record's cache (`_validation_summary_v9`) is stamped with
  `account=True` too** (`signals.save(..., account=True)`); until 2026-09-07 it
  carried the narrow stamp, so a `validation.py` edit left it served as current.
- **The stamp is taken before the build and checked after.** If the code or the
  price files moved meanwhile the file is written with `inputs: null` and the
  page reports it stale rather than certifying stale numbers. The assets'
  price files are in it; a `--stocks-only` build records `partial: ["assets"]`.
- **`refresh` cleans before it decides** whether to rebuild (it used to decide
  first, then clean, then skip the rebuild the clean had made necessary).
- **The code stamp uses file MTIMES, not contents.** Editing a docstring — or a
  `git checkout`, a fresh clone, copying the tree — invalidates every cache and
  forces a full rebuild. Safe direction to be wrong in; budget for it. The last
  full rebuild took 103 min on 2026-09-07 (`output/rebuild_2026-09-07d.log`); the permutation test
  (100 rounds × 60 stocks × 19 variants × 5 universes) is the long stage. Its
  rounds are forked across `validation.PERMUTATION_WORKERS` processes (8 on a
  10-core box, 1 in tests); the shuffled multiset, and so p, is identical at
  any worker count because every round carries its own seed.

## Conventions the code holds to

- **Point-in-time correctness everywhere.** Higher-timeframe values are those of
  the *forming* bar; pivots are only usable once confirmed by bars on both
  sides; a hand-drawn level is only valid from its second touch; universe
  membership uses only bars before the start year. Anything that reads a value
  before it could have existed is a bug, not an optimisation.
- **Conventions cost the strategy rather than flatter it** — stop and target in
  one bar assumes the stop, gaps fill at the open, one position per symbol.
- **The stop is the entry candle's own low**, uniformly across every strategy
  (true again since 2026-09-07: the Holy Grail board row had been trading a
  pivot-low stop a median 13% below entry under the label "swing stop").
- Costs are modelled, not assumed away: `slippage.py` separates what is measured
  from what is assumed and says which is which. Note the size cap
  (`MAX_PARTICIPATION`, one order ≤ 1% of the stock's daily turnover) is a
  *sizing rule*, not a cost — on its own it beats perfect fills.
- Docstrings here carry the *why*, including dates and rejected alternatives.
  Match that: when changing a rule, record what it was, what it is, and the
  measurement that moved it.

## Traps

- **Compiling is not working.** Most bugs in this project passed every static
  check — a missing `parse_qs` import, MAR computed but never put in the
  payload, numpy scalars that will not serialise. Dry-run the page against the
  built file (`node scripts/check_dashboard.js`) after every rebuild.
- `dashboard_server.serve()` binds `127.0.0.1` with no host parameter, so a
  server started inside the dev container is unreachable from the host browser
  and `-p 8765:8765` does not help. `curl` from the same container proves only
  that the process is alive.
- `pkill -f "scripts.dashboard"` matches its own shell and kills the session.
  Use PIDs; `pgrep -a -f` has the same problem.
- `/tmp` is cleared between sessions. Long-running logs go in `output/`.
- Small samples mislead — the all-time-high band looked neutral on 30 stocks and
  clearly harmful on 101.
- Deleting a block can take a shared import with it, 20 minutes into a rebuild.
- `scripts/check_dashboard.js` asserts VALUES against the payload since
  2026-09-07 (it used to pass with every CAGR zeroed). It FAILS against a payload
  built before that date, which is correct: rebuild.
- The producer's paper book is ₹1cr (`sizing.CAPITAL`, 2026-09-07) so no signal
  the largest account could take is dropped from the cache; every rupee figure
  in `trade_stats` is on that book and describes no account on the page. Read
  the R-multiple fields.
- `pyflakes` is currently clean except one known warning
  (`scripts/refresh.py:52`, pandas imported but unused). Anything else is yours.

# CLAUDE.md

kitelab — a local backtesting workbench for rules-based swing trading on NSE
equities, built on Kite Connect. Pure Python + stdlib HTTP; no framework, no
database, no test runner beyond stdlib `unittest`.

**Where the record lives.** The running record is the docstrings: every rule,
exclusion and retired axis carries the date and the measurement that moved it
(`kitelab/config.py`, `scripts/dashboard_data.py`, `kitelab/validation.py` are
the dense ones). Do not add a standing handover; put the why next to the code.
Dated change history (EMA band, ATH repair, the 2026-09-07 audit, universe
forensics) lives in the **kitelab-history** skill. `AUDIT_REPORT.md` /
`FIX_REPORT.md` are historical; their numbers predate the universe merge.

## Commands

Which Python: on the host (macOS), `./.venv/bin/python` (3.14, Homebrew). In
the dev container (Linux) that venv is a dead symlink — use plain `python3`
(3.12, pandas + pyflakes installed); everything below runs under it.

```bash
# view the dashboard — stdlib only, no keys. Run on the HOST, not in the container.
./run_dashboard.sh                       # port 8765

# rebuild whatever is stale — no keys, needs pandas
python3 -m scripts.refresh               # --check | --force | --stocks-only

# BEFORE a rebuild — catches what import cannot (NameError in main(), a payload
# key the build stopped emitting). Five of six rebuilds on 2026-09-03 were spent
# finding faults these would have caught in minutes.
python3 -m unittest discover -s tests -t .   # 350 tests, ~1s
python3 -m pyflakes kitelab scripts tests    # undefined names, instant
python3 -m scripts.preflight                 # build path, one symbol per bucket, ~2 min

# does the page still render the file just built? (needs node, not pandas)
node scripts/check_dashboard.js

# fetch new data — the only job needing a Zerodha account
set -a; source ~/.secrets/all.env; set +a
python3 -m scripts.login                 # once a day; tokens die ~06:00 IST
python3 -m scripts.backfill --symbols-file <list> --daily-only

# health / audits
python3 -m scripts.cache_status | data_audit | validate | bootstrap | ath_band
```

`scripts/check_dashboard.js` is the only JS test; `unittest` the only Python
one; no `pytest`. **Data lives outside the repo**: `config._resolve()` finds it
(`$KITELAB_DATA_DIR`/`$KITELAB_CLEAN_DIR`, else `/data/{raw,clean}/kitelab`,
else `$HOME/data/...`, else `./data`). RAW is read-only; CLEAN is the only dir
the analysis side writes or reads prices from.

## The grid

**10,260 cells** (19 variants × 540: 5 equity universes × 5 start years × 5
priorities × 2 risks × 2 capitals, plus BITCOIN/GOLD at one priority), keyed
`strategy|variant|universe|risk|capital|fill|start|priority`. Take live counts
from `refresh --check`; the arithmetic is what to trust. Axis values live in
`registry.py`, `RISKS`, `CAPITALS`, `START_YEARS` (default 2018),
`portfolio.PRIORITIES` — each list carries the measurement that set it in a
comment above it; read that before widening one. Each is a multiplier on all
the others.

- **Universe buckets are liquidity buckets, point-in-time, and they partition**
  (since 2026-09-07): membership is median daily traded value on bars before
  `START_DEFAULT` only; no positive-turnover bar before the cut → `recent`.
  One classification, not one per start year.
- **Signal priority swings CAGR by up to 20 points** — more than the gap
  between strategies — and no ordering wins consistently: that is what noise
  looks like. Never pick a winner off that table without `scripts.bootstrap`.
- All EMA families trade the bare 20-EMA cross (band removed 2026-09-05;
  ~30% next-bar re-entry churn is back — see kitelab-history).
- ATH rows: the band only *declines* a cross (since 2026-09-07), and **every
  ATH stack needs its unfiltered twin control** (`pair|QM`, `pair|QD` are
  controls, not strategies) or its score cannot be read.

## Validation — the "is this real" layer

`kitelab/validation.py` holds the math; `scripts/validate.py` and
`scripts/bootstrap.py` are thin CLI wrappers; `scripts/dashboard_data.py` calls
the same functions so page and CLIs cannot disagree. (It lives in `kitelab/`
because both scripts import helpers *from* `dashboard_data` — the reverse
import would be circular.) Different from `tests/`: those check the code does
what it was told; these are the checks a trader runs before believing a rule.

The Compare table's **Validated** column is a *gate, not a score*: failing any
one of five checks ranks a row below every row that passes all five; MAR
breaks ties within each group. The five, in one line each: beats the on-screen
equal-weight buy-&-hold; distinguishable from jointly-shuffled prices
(p ≤ 0.05, 100 rounds); wins a strict majority of fixed 3-year windows against
each window's own hold; survives a cost margin (breakeven bp); clears the
family-wise luck hurdle (drift-adjusted, cluster-robust t ≥ ~2.41).

Quote the `validation_summary` spread (`tried`, `n_eff`, `hurdle`, `cleared`…),
never a single headline number: the hurdle guards the t only, while the MAR the
top row sorts by is an uncorrected max over 540 cells on an axis (priority) the
project itself calls noise. Full detail — the five checks' history, the
credibility statistic, drift_r, the holdout stand-ins table, how to add a
strategy — is in the **kitelab-validation** skill. The 2026-09-07 audit made
every gate stricter; the board looking worse than 2026-09-05 is the repair
working.

## Layout

- `kitelab/` — the library. Strategy and data logic lives here, never in scripts.
  - `config.py` — universe, exclusions (each with its measurement),
    `DEMERGERS`/`HISTORY_STARTS`, `merged`, `_resolve()`.
  - `frames.py`/`fetch.py`/`timeframes.py` — storage and resampling.
  - `backtest.py`, `darvas.py`, `holygrail.py`, `strategies.py` — strategies;
    `trailing.py`, `sizing.py`, `slippage.py`, `levels.py`, `indicators.py`,
    `excursion.py`, `curves.py` support them.
  - `portfolio.py` — one pot of money, cash-constrained; account-level truth.
  - `validation.py` — edge-vs-luck math. `screener.py`, `contracts.py`
    (hand-entered lot sizes/margins — `unverified_multipliers()` warns).
  - `signals.py` — stamped signal caches. `registry.py` — the board.
  - `dashboard_server.py` — stdlib HTTP; `/`, `/api/dashboard`, `/api/status`, `/api/curve`.
- `scripts/` — thin entry points, all `python -m scripts.<name>`.
  `dashboard_data.py` precomputes the whole grid into `dashboard.json`.
- `tests/` — 350 hermetic tests, no price files or network (`support.py`
  patches the data boundary). Property tests, a golden test, and an oracle test
  against `backtesting.py` (skips if that dev extra is absent).
  `scripts/preflight.py` is the integration test, `check_dashboard.js` the page one.
- `web/dashboard.html` — the entire UI, one file, two views (Compare, Detail).
  Nothing is simulated in the browser: every control selects precomputed
  results. **Everything in the payload is displayed** — if you add a payload
  key, add the view with it (unrendered sections were removed 2026-09-03; the
  scale-out sweep alone cost ~64s/build for a key the page never read).
  `waterfall` serializes empty (only fill "1" is gridded); `assets`/
  `single_name` are empty only after a `--stocks-only` build.
- `pine/` — TradingView mirrors (4 scripts).
- `data/` is gitignored except `data/keep/` (the four things nothing can rebuild).
- `README.md` is stale overall, but its **Timeframes**, **2026-08-03
  closing-auction session change** and **Known limits** sections are accurate
  and are the only place that reasoning is written down.
- Secrets live only in `config.local.toml` (gitignored); `config.load()` never
  reads them, only `auth.py` does.

## Three programs, deliberately separate

| | Zerodha account | pandas |
|---|:---:|:---:|
| `scripts.backfill` — fetch | yes | yes |
| `scripts.refresh` — clean + rebuild | no | yes |
| `./run_dashboard.sh` — view | no | no |

Fetching can only run when Zerodha is up; refreshing is pure local computation
and must never be blocked by it; viewing must work with neither.

## The universe, and what may be quoted

- **One universe** (`cfg.merged`), **1,000 stocks** since 2026-09-07; five
  liquidity buckets partition it on the 2018 cut. Take counts from
  `refresh --check`. (Docstrings still argue in terms of "500"/"the 399" —
  sizes from 2026-09-03; the 101/399 holdout split is gone. History: skill.)
- **History restarts at listing breaks** (`frames.LISTING_BREAK_DAYS`,
  `config.DEMERGERS`, `config.HISTORY_STARTS`): Kite serves phantom pre-listing
  bars and does NOT adjust demergers, so bars before the last break are
  dropped — severe by design. `data_audit` checks for all of it.
- **The live exposure is multiple testing, not contamination**: 19 variants
  ranked and a winner reported. The luck hurdle is a family-wise 95% bar; quote
  the `validation_summary` spread the page shows above the table.
- **Survivorship is untouched and remains the largest known bias here**
  (~4.9pp/yr); the credibility gate now tests each rule against random entries
  on the same survivors, so their drift is no longer credited to the rule. No
  number in this project is a forecast.
- The class spreadsheets (`*.xlsx`) are hand-picked examples, not backtests.
  Recover the intent behind a rule; never try to match their numbers.

## Stamps and caches

Signal caches and `dashboard.json` record the universe, the price files and
the code they were built from, and refuse themselves when any of the three
moves. Two stamps since 2026-09-07 (`signals.stamp(symbols, account=)`):
signal caches depend on the producer modules (derived from `kitelab.registry`,
so a registered strategy is in the stamp by construction) plus
`signals._SUPPORT`; the dashboard additionally on `signals._ACCOUNT`
(`portfolio.py`, `curves.py`, `validation.py`, `contracts.py`,
`dashboard_data.py`). `tests/test_stamps.py` walks the build's import graph
and fails on any reachable module left out. The stamp is taken before the
build and checked after; if inputs moved meanwhile the file is written with
`inputs: null` and the page reports it stale rather than certifying stale
numbers.

**The code stamp uses file MTIMES, not contents.** Editing a docstring, a
`git checkout`, a fresh clone — any of it invalidates the caches that depend
on the touched file. Safe direction to be wrong in; budget for it: the last
full rebuild took ~103 min (2026-09-07), the permutation test being the long
stage (forked across `validation.PERMUTATION_WORKERS`; p is identical at any
worker count because every round carries its own seed).

**A rebuild is cut into parts (2026-09-09), so "invalidates the caches" is no
longer "invalidates all of them".** Two independent tiers:

- *Signal caches* are stamped against the touched producer's **transitive
  import closure** (`signals.reachable_from`, read out of the import
  statements with `ast`) unioned with `_SUPPORT`, and `signals.producer_of`
  resolves a cache name to its producer by longest prefix so no call site
  passes it. Measured on the 19-variant board: editing `holygrail.py` rebuilds
  1 strategy, `darvas.py` 4, `timeframes.py` 11, `backtest.py` all 19 (every
  engine imports it), any `_SUPPORT` module all 19. `_SUPPORT` stays global on
  purpose — `registry.py`, `strategies.py` and `trailing.py` are in nobody's
  closure, and a closure-only rule would let a `registry.py` edit invalidate
  nothing. An unrecognised cache name gets the whole-board digest, not the
  loosest one.
- *The grid* is one checkpoint per (fill, strategy, variant) under
  `CLEAN/grid_ckpt`, written atomically as each partition finishes, so an
  interrupted rebuild resumes instead of restarting. The digest covers the
  trades themselves (pickled and hashed — exact, 0.21s for the largest list),
  the universes, the axes and `_ACCOUNT`'s contents. **The producer modules are
  deliberately excluded**: their effect is already in the trades hash, so
  hashing them too would let a `darvas.py` docstring reprice Holy Grail.
  `--no-grid-cache` forces a full recompute.

Verified 2026-09-09 on the 3-symbol preflight board: building twice into one
temp dir gave 0/19 partitions reused then 19/19, 78.1s then 0.8s, and all
5,700 cells identical.

**The code digest reads bytes, not timestamps (2026-09-09).** It used to hash
each module's `st_mtime_ns`, which is a fact about the filesystem and not about
the code. Merging a finished branch into `main` went through `git switch` (28
files rewritten backwards) and then a fast-forward (the same 28 rewritten
forwards); `git diff` between the commit that built the caches and the tree
afterwards was two lines, both timestamps inside `data/keep`, no code at all.
All 19 live caches went stale at once and `refresh --check` announced *"the
STRATEGY CODE has changed since they were built"*, which was false — a
103-minute rebuild to reproduce identical numbers. `git checkout`, `stash` and
`pull` all do the same thing. `signals._content` now hashes the file's bytes,
so a rewrite that restores the same content is invisible and a one-character
edit is not; proved both ways on the real board. **Price files deliberately stay
on (size, mtime)**: 593 MB of parquet that git never touches and a refetch
replaces wholesale, so timestamps tell the truth there.

The old timestamps were recorded nowhere, so that incident could not be undone,
only prevented — the 19 caches were re-stamped by hand after git proved not one
file they depend on had changed. If it ever happens again, check that first:
adopt only when every part of the stamp except `code` already matches.

## Conventions the code holds to

- **Point-in-time correctness everywhere.** Higher-timeframe values are those
  of the *forming* bar; pivots only count once confirmed on both sides; a
  hand-drawn level is valid from its second touch; universe membership uses
  only bars before the start year. Reading a value before it could exist is a
  bug, not an optimisation.
- **Conventions cost the strategy rather than flatter it** — stop and target
  in one bar assumes the stop, gaps fill at the open, one position per symbol.
- **The stop is the entry candle's own low**, uniformly across every strategy.
- Costs are modelled, not assumed away: `slippage.py` says what is measured vs
  assumed. `MAX_PARTICIPATION` (one order ≤ 1% of daily turnover) is a *sizing
  rule*, not a cost — on its own it beats perfect fills.
- Docstrings carry the *why*, with dates and rejected alternatives. Match
  that: when changing a rule, record what it was, what it is, and the
  measurement that moved it.

## Verifying dashboard changes

Verify by **running** `node scripts/check_dashboard.js` and reading its
verdict (`all checks passed` / `N FAILURE(S)`, exit 0/1). Never verify by
reading `dashboard.html` or the checker's source to infer whether it worked.

## Traps

- **Compiling is not working.** Most bugs here passed every static check — a
  missing import, MAR computed but never put in the payload, numpy scalars
  that will not serialise. Dry-run the page against the built file
  (`node scripts/check_dashboard.js`) after every rebuild.
- `dashboard_server.serve()` binds `127.0.0.1` with no host parameter: a
  server inside the dev container is unreachable from the host browser and
  `-p 8765:8765` does not help. `curl` in-container proves only liveness.
- `pkill -f "scripts.dashboard"` matches its own shell and kills the session.
  Use PIDs; `pgrep -a -f` has the same problem.
- `/tmp` is cleared between sessions. Long-running logs go in `output/`.
- Small samples mislead — the ATH band looked neutral on 30 stocks and clearly
  harmful on 101.
- Deleting a block can take a shared import with it, 20 minutes into a rebuild.
- `check_dashboard.js` asserts VALUES against the payload since 2026-09-07 and
  correctly FAILS against a payload built before that date: rebuild.
- The producer's paper book is ₹1cr (`sizing.CAPITAL`) so no signal the
  largest account could take is dropped from the cache; rupee figures in
  `trade_stats` describe no account on the page — read the R-multiple fields.
- `pyflakes` is clean except one known warning (`scripts/refresh.py:52`,
  pandas imported but unused). Anything else is yours.

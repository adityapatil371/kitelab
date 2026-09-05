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

# BEFORE a rebuild -- the whole build path over 3 symbols, ~48s, into a temp
# dir. Catches what import cannot: a NameError inside main(), a payload key the
# page reads and the build stopped emitting, a stage that silently produces
# nothing. Five of the six rebuilds on 2026-09-03 were spent finding faults this
# would have caught in under a minute.
python3 -m unittest discover -s tests -t .          # 259 tests, ~0.5s
python3 -m unittest tests.test_validation -v        # one module
python3 -m unittest tests.test_validation.BootstrapOneShape       # one case
python3 -m pyflakes kitelab scripts tests           # undefined names, instant
python3 -m scripts.preflight                        # the build path, ~48s

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
python3 -m scripts.universe_bias         # what the retired 101/399 split measured
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
the ~5% that clear it by chance, so a 14th strategy makes "one of them looks
good" less impressive, not more. It also multiplies the grid — 400 new cells per
variant at the current axes.

## The grid, and what its axes mean

**5,720 cells** (was 8,800 before the EMA band went), keyed
`strategy|variant|universe|risk|capital|fill|start|priority`:

| axis | values | source |
|---|---|---|
| strategy·variant | 13 (7 families) | `registry.py` |
| universe | `all` / `large` / `mid` / `small`, + BITCOIN / GOLD | liquidity buckets, below |
| risk | 0.5%, 1% | `RISKS` — the one lever known to reorder the table |
| capital | ₹2L, ₹1cr | `CAPITALS` — ₹1cr added to test the size cap |
| fill | Realistic only | `GRID_FILLS = ["1"]` |
| start year | 2006, 2012, 2018, 2022, 2024 | `START_YEARS`, default 2018 |
| priority | 5 orderings (1 on single-name universes) | `portfolio.PRIORITIES` |

Every one of those lists carries, in a comment above it, the measurement that set
it — read those before widening one. Each is a multiplier on every other.

Two axes carry findings rather than settings:

- **Universe** is *liquidity buckets, point-in-time*. Membership is median daily
  traded value using only bars before `START_DEFAULT` (2018). Until 2026-09-03 it
  used whole history, so a stock that became liquid in 2024 was filed as a large
  cap for a 2006 backtest — the universe definition reading the future while
  every indicator was scrupulous about not doing so. Still unfixed one level up:
  `screen_universe`'s *admission* gate measures over full history, so these are
  stocks that turned out to be liquid.
- **Signal priority** — which signal wins the cash when you cannot fund them
  all. Was a hardcoded constant; it swings CAGR by up to **20 points**, more than
  the gap between strategies. No ordering wins consistently and Q/M/W's best is
  M/W/D's worst, which is what noise looks like — do not pick the winner from
  that table without running `scripts.bootstrap` over it. Single-instrument
  universes are dealt only the default: with one position per symbol there is
  never a second candidate, so all five orderings are byte-identical there.

**The scanning-pool axis and the Breadth sweep are gone** (2026-09-03). They were
one measurement wearing two names, and what they found — a small account cannot
fund what a wide scan offers — is now on every row as `skipped_cash` and
`exposure`, which say it without a sweep. `_draw_baskets` no longer exists; do
not reintroduce a per-strategy draw, which would stop the table comparing rules
and start it reporting who drew the better stocks.

## The EMA band is gone (2026-09-05)

Every EMA family now trades the bare 20-EMA cross. `BANDS = [0.0]`,
`SOLO_BAND = 0.0`, and `backtest.BAND` / `timeframes.BAND` moved to `0.0` with
them so a bare `simulate("HAL")` matches the board. `ATH_BAND = 0.10` is
untouched — it is the distance-from-all-time-high filter that *defines* the
`eath` family, not an EMA buffer.

Three consequences worth carrying into any reading of the board:

- **The churn the band existed to prevent is back.** `backtest.py:61` records
  what it bought: without hysteresis, entry and exit share one knife-edge, and
  the median holding period was 3 sessions on a strategy filtered by *monthly*
  EMAs, with 20% of trades re-entering the same stock the next day. Expect trade
  counts up and cost drag up.
- **The parameter-plateau control is gone with it.** Six adjacent band settings
  moving together were the only answer this project had to "is 2% a plateau or a
  lucky spike?". One setting cannot answer it. Nothing replaced it.
- **`EMA_MWD_RETIRED` had to go**, and it is the uncomfortable part. It held
  `{0.0}` because M/W/D at a 0% band was cut *earlier the same day* as the worst
  variant on the board — median MAR −0.14 across all 50 priority × risk × start
  combinations, positive in 7 of them. With `BANDS = [0.0]` that guard would have
  deleted the family, so the variant is back as the **only** M/W/D row. Nothing
  has re-measured it. Read its Validated gate, not its rank.

`registry.py` is now in the cache stamp (`signals._SUPPORT`). It holds parameter
values baked into every trade, and this change proved the gap: `pair`, `e1` and
`eath` changed what they trade while their cache names (`EMA_WD`,
`EMA_daily_only`, `EMA_ath10`) stayed identical. The cost is that any registry
edit — a label typo included — now invalidates every cache.

`scripts/universe_bias.py` derives its `NAMES` from the registry instead of a
hand-kept list of stems, for the same reason. It still reads caches by path,
bypassing the staleness check, so run it only after a full rebuild.

## Validation — the "is this real" layer

`kitelab/validation.py` (~775 lines) holds the math; `scripts/validate.py` and
`scripts/bootstrap.py` are thin CLI wrappers around it, and
`scripts/dashboard_data.py` calls the same functions so the page and the CLIs
cannot disagree. It lives in `kitelab/` rather than `scripts/` because both
scripts import helpers *from* `dashboard_data`, so the reverse import would be
circular.

**Different from `tests/`.** Those check the code does what it was told, which
says nothing about whether a strategy makes money. These are the checks a trader
runs before believing a rule.

The Compare table's **Validated** column is a *gate, not a score*: a row failing
any one of five checks ranks below every row that passes all five, and MAR breaks
ties within each group. The five:

1. beats an equal-weight buy & hold of the same stocks over the same span
2. distinguishable from shuffled prices (permutation)
3. wins a majority of **disjoint** 3-year walk-forward windows
4. survives a cost margin (breakeven bp)
5. clears the correlation-adjusted luck hurdle

Checks 1 and 3 recompute for the on-screen Universe × Priority × Capital;
2, 4 and 5 recompute for Universe only, because they run on the trade list
before any account simulation — priority and capital cannot move them. Below
`MIN_TRADES = 30` every function returns `None`/empty rather than raising, so
`preflight`'s 3-symbol build stays green.

`validation_summary` carries the multiple-testing arithmetic (`tried`, `n_eff`,
`avg_correlation`, `expected_by_chance`, `hurdle`). Quote that spread, never a
single headline number.

## Layout

- `kitelab/` — the library. Strategy and data logic lives here, never in scripts.
  - `config.py` — universe, exclusions with the measurement that justifies each,
    `in_sample` / `out_of_sample` / `merged`, and `_resolve()`.
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
  `dashboard_data.py` (~1,100 lines) precomputes the whole grid into `dashboard.json`.
- `tests/` — 259 hermetic tests, no price files or network (`support.py`
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
  Detail** (`VIEWS` at the bottom). A pure viewer: every control selects among
  precomputed results, nothing is simulated in the browser. The Compare table
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

- **One universe** (`cfg.merged`), currently **999 stocks** — 73 large / 116 mid
  / 640 small by the 2018 liquidity cut. It has grown: the docstrings still argue
  the case in terms of "500" and "the 399", which were the sizes on 2026-09-03.
  Read the reasoning, take the count from `scripts.refresh --check`.
- **The 101/399 in-sample/holdout split was dropped on 2026-09-03.**
  Train/test catches a *fitted* model that memorised its training rows. These
  rule shapes were taught in class before the repo read a candle — 20 EMA, the
  M/W/D stack, ADX>25 pullbacks, Donchian 20-10 and 55-20 — so there was nothing
  to memorise, and holding back stocks cost power without buying validity. What
  *was* chosen here is now shorter still: the Turtle's weekly gate, and which
  variant to headline. The 2% band was the third, and it is gone (below).
- **So the live exposure is multiple testing, not contamination.** 13 variants
  ranked and a winner reported; at a 95% bar chance alone clears ~0.65 of them.
  Removing the EMA band cut the board from 22, which lowers the raw hurdle but
  should also raise `n_eff` — the six bands were the most correlated cluster on
  the board, and the Nyholt correction was discounting them heavily.
  `kitelab.validation` measures this directly and the page shows the hurdle above
  the table — quote that, not a single headline number.
- **Two biases survive the merge and neither is fixed by it.** The old 101 are
  still winners, now a small share of the deck rather than all of it (large-cap
  median buy-and-hold ≈ +11.2% merged, against +9.7% for the 399 alone).
  Survivorship is untouched and remains the largest known bias here (~4.9pp/yr).
  No number in this project is a forecast.
- `cfg.in_sample` / `cfg.out_of_sample` still exist so `scripts.universe_bias`
  can keep asking what the old split measured. Nothing else should use them.
- The class spreadsheets (`*.xlsx`) are hand-picked examples, not backtests.
  Recover the intent behind a rule from them; never try to match their numbers.

## Tests that stand in for the holdout

Practitioner controls, not train/test:

| question | where |
|---|---|
| is this edge distinguishable from luck on its own trades? | `scripts.bootstrap` |
| did 13 variants buy me a winner by chance? | the luck hurdle in `validation_summary` |
| did it beat simply owning the same stocks? | `scripts.validate`, the vs-Hold column |
| does it hold in **disjoint** periods? | walk-forward, `scripts.validate` |
| does it hold across regimes? | `START_YEARS` axis on the grid |
| is a parameter a plateau or a lucky spike? | **nothing measures this now** — see below |
| is the return one thin tail? | top-N deletion, `scripts.validate` |
| how much friction does it survive? | breakeven cost, `scripts.validate` |
| does the ordering rule matter? | the **Signal priority** axis on Compare |
| what did the old split measure? | `scripts.universe_bias` |

## Stamps and caches

Signal caches and `dashboard.json` record the universe, the price files and the
strategy code they were built from, and refuse themselves when any of the three
moves. Two consequences:

- **`signals._CODE` is gone.** Producer modules derive from `kitelab.registry`,
  so a registered strategy is in the stamp by construction. This replaced the
  hand-kept list that omitted `holygrail.py` and invalidated a day of numbers.
- **The code stamp uses file MTIMES, not contents.** Editing a docstring — or a
  `git checkout`, a fresh clone, copying the tree — invalidates every cache and
  forces a full rebuild. Safe direction to be wrong in; budget for it. (The
  "~16 min" figure in older notes predates a 3.3x wider grid and has not been
  re-timed. Time it before quoting it.)

## Conventions the code holds to

- **Point-in-time correctness everywhere.** Higher-timeframe values are those of
  the *forming* bar; pivots are only usable once confirmed by bars on both
  sides; a hand-drawn level is only valid from its second touch; universe
  membership uses only bars before the start year. Anything that reads a value
  before it could have existed is a bug, not an optimisation.
- **Conventions cost the strategy rather than flatter it** — stop and target in
  one bar assumes the stop, gaps fill at the open, one position per symbol.
- **The stop is the entry candle's own low**, uniformly across every strategy.
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
- `pyflakes` is currently clean except one known warning
  (`scripts/refresh.py:52`, pandas imported but unused). Anything else is yours.

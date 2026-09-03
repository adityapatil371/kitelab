# CLAUDE.md

kitelab — a local backtesting workbench for rules-based swing trading on NSE
equities, built on Kite Connect. Pure Python + stdlib HTTP; no framework, no
database, no test runner. Read `HANDOVER.md` before quoting any result: it is
the running record of what is measured, what was wrong, and what is open.

## Commands

```bash
# view the dashboard — no keys, no venv (stdlib only). Run on the HOST, not in the container.
./run_dashboard.sh                       # port 8765; ./run_dashboard.sh 9000 for another

# rebuild whatever is stale — no keys, needs the venv (~16 min for a full rebuild)
./.venv/bin/python -m scripts.refresh
./.venv/bin/python -m scripts.refresh --check    # report staleness, change nothing
./.venv/bin/python -m scripts.refresh --force

# BEFORE a rebuild -- the whole build path over 3 symbols, ~40s, into a temp
# dir. Catches what import cannot: a NameError inside main(), a payload key the
# page reads and the build stopped emitting, a stage that silently produces
# nothing. Five of the six rebuilds on 2026-09-03 were spent finding faults this
# would have caught in under a minute.
./.venv/bin/python -m unittest discover -s tests -t .   # 235 tests, instant
./.venv/bin/python -m pyflakes kitelab scripts tests    # undefined names, instant
./.venv/bin/python -m scripts.preflight                 # the build path, ~40s

# does the page still render the file just built? (needs node, not the venv)
node scripts/check_dashboard.js

# fetch new data — the only job needing a Zerodha account
set -a; source ~/.secrets/all.env; set +a
./.venv/bin/python -m scripts.login      # once a day; tokens die ~06:00 IST
./.venv/bin/python -m scripts.backfill --symbols-file <list> --daily-only

# health / audits
./.venv/bin/python -m scripts.cache_status
./.venv/bin/python -m scripts.data_audit
./.venv/bin/python -m scripts.bootstrap          # edge vs luck + the multiple-testing summary
./.venv/bin/python -m scripts.universe_bias      # what the retired 101/399 split measured
./.venv/bin/python -m scripts.backup_inputs      # refresh runs this as its last step
```

Always use `./.venv/bin/python` (3.14) for anything that imports pandas. There
is no `pytest`; `scripts/check_dashboard.js` is the only test in the project.

## Adding a strategy

Append a `Strategy` to `kitelab/registry.py` (or call `register()`). Nothing else
needs editing: it appears in the compare grid, the breadth sweep, the non-equity
table and `scripts.bootstrap` automatically, measured on the same stocks, the
same baskets and the same account as everything already there.

Each `Strategy` names **the module that produces its trades**, and
`signals.stamp()` derives the cache-code digest from that list. A rule that is
on the board is therefore in the stamp by construction. This is not decoration:
`holygrail.py` was missing from the old hand-kept `_CODE` list, so a rewritten
strategy went on serving superseded trades while `refresh` said "already
current", and a day of published numbers was invalid.

One thing the machinery cannot enforce: **every rule you add raises the bar for
all of them.** `bootstrap` counts how many variants clear a 95% test against the
~5% that clear it by chance, so a 25th strategy makes "one of them looks good"
less impressive, not more.

## The grid, and what its axes mean

2,688 cells, ~7 min (was 23,040 / 15.5 min). Lean because the handover's own
measurements said so — capital "barely moves the result", and three of the four
fill modes only ever fed the cost waterfall.

Two axes carry findings rather than settings:

- **Scanning pool** — how many names are scanned, not held (the account never
  holds more than ~11). Over 500 stocks at ₹2L the class EMA stack skips **56%**
  of its own signals for want of cash and its median return falls to 2.5%,
  against 15.7% at 30 names; the Turtle skips 49% and climbs to 15.1%. So the
  default all-500 view is partly reporting how each rule's signal frequency fits
  one account size, not which rule is better.
- **Signal priority** — which signal wins the cash when you cannot fund them
  all. Was a hardcoded constant; it swings CAGR by up to **20 points**, more than
  the gap between strategies. No ordering wins consistently and Q/M/W's best is
  M/W/D's worst, which is what noise looks like — do not pick the winner from
  that table without running `bootstrap` over it.

Every strategy is dealt the **same baskets** at each pool size (`_draw_baskets`,
one seed shared with the breadth sweep). Draw per strategy and the table stops
comparing rules and starts reporting who drew the better stocks.

## Layout

- `kitelab/` — the library. Strategy and data logic lives here, never in scripts.
  - `config.py` — universe, exclusions with the measurement that justifies each,
    `in_sample` / `out_of_sample` / `all_symbols`. Also `_resolve()`, which finds
    the raw and clean data directories rather than assuming them.
  - `frames.py` / `fetch.py` / `timeframes.py` — storage and resampling.
  - `backtest.py`, `darvas.py`, `holygrail.py`, `strategies.py`, `timeframes.py` —
    the strategies. `trailing.py`, `sizing.py`, `slippage.py`, `levels.py` support them.
  - `portfolio.py` — one pot of money, cash-constrained; the account-level truth.
  - `signals.py` — stamped signal caches (see below).
  - `dashboard_server.py` — stdlib HTTP server; `/`, `/api/dashboard`, `/api/status`, `/api/curve`.
- `scripts/` — thin entry points, all run as `python -m scripts.<name>`.
  `dashboard_data.py` (~1000 lines) precomputes the whole grid into `dashboard.json`.
- `tests/` — 235 hermetic tests, no price files or network (`support.py`
  patches `_daily_closes`, `liquidity_at`, `ema_stack_signal` and `frames.daily`).
  Stdlib `unittest`; no runtime test dependency. Three kinds worth knowing:
  **property** tests (the books balance, a later bar cannot change an earlier
  trade), a **golden** test that fails when a refactor silently moves numbers,
  and an **oracle** test running the same bars through `backtesting.py` — the
  only test that could catch a mistake made consistently in both the code and
  its own tests. It skips if that dev extra is absent.
  `scripts/preflight.py` is the integration test, `scripts/check_dashboard.js`
  the page one.
- `web/dashboard.html` — the entire UI, one file. A pure viewer: every control
  selects among precomputed results, nothing is simulated in the browser. Views:
  Compare, Detail, Breadth, Assets (non-equity), Stocks. The Compare table
  exports to CSV in one click — CSV not `.xlsx` because the viewer is stdlib
  only, and Excel opens CSV anyway.
- **Everything in the payload is displayed.** Sections nothing rendered were
  removed on 2026-09-03 (`scaleout`, `scaleout_r`, `tradestats`, `timeframes`,
  `nifty`); the scale-out sweep alone cost ~64s of every build to produce a key
  the page never read. If you add a payload key, add the view with it.
- `pine/` — TradingView mirrors of the strategies.
- `data/` is gitignored except `data/keep/`, which holds the four things nothing
  can rebuild (`levels.json`, `accepted.txt`, the in-sample/holdout split, the manifest).
- Secrets live only in `config.local.toml` (gitignored). `config.load()` never
  reads them; only `auth.py` does, via `config.require_secrets()`.

## Three programs, deliberately separate

| | Zerodha account | venv |
|---|:---:|:---:|
| `scripts.backfill` — fetch | yes | yes |
| `scripts.refresh` — clean + rebuild | no | yes |
| `./run_dashboard.sh` — view | no | no |

Keep that separation. Fetching can only run when Zerodha is up; refreshing is
pure local computation and must never be blocked by it; viewing must work with
neither.

## The universe, and what may be quoted

- **One universe of 500 stocks** (`cfg.merged`). The 101/399 in-sample/holdout
  split was dropped on 2026-09-03; `config.Config.merged` carries the full
  argument and `scripts/bootstrap.py` is what replaced it.
- **Why the split went.** Train/test catches a *fitted* model that memorised its
  training rows. These rule shapes were taught in class before the repo read a
  candle — 20 EMA, the M/W/D stack, ADX>25 pullbacks, Donchian 20-10 and 55-20 —
  so there was nothing to memorise, and holding back 399 stocks cost power
  without buying validity. What *was* chosen here is short: the 2% band, the
  Turtle's weekly gate, and which of 24 variants to headline.
- **So the live exposure is multiple testing, not contamination.** 24 variants
  ranked and a winner reported. At a 95% bar, chance alone clears ~1.2 of 24, so
  one winner is evidence of nothing. `scripts.bootstrap` measures this directly;
  quote its percentile spread, not a single headline number.
- **Two biases survive the merge and neither is fixed by it.** The old 101 are
  still winners, now ~20% of the deck instead of 100% (large-cap median
  buy-and-hold ≈ +11.2% merged, against +9.7% for the 399 alone). Survivorship is
  untouched and remains the largest known bias here (~4.9pp/yr). No number in
  this project is a forecast.
- `cfg.in_sample` / `cfg.out_of_sample` still exist so `scripts.universe_bias`
  can keep asking what the old split measured. Nothing else should use them.
- The class spreadsheets (`*.xlsx`) are hand-picked examples, not backtests.
  Recover the intent behind a rule from them; never try to match their numbers.

## Tests that stand in for the holdout

Practitioner controls, not train/test. `scripts.bootstrap` is the one that
replaced the split; the others were already here and are worth reaching for:

| question | where |
|---|---|
| is this edge distinguishable from luck on its own trades? | `scripts.bootstrap` |
| did 24 variants buy me a winner by chance? | `scripts.bootstrap`, closing summary |
| does it hold across regimes? | `START_YEARS` axis on the grid |
| is a parameter a plateau or a lucky spike? | the band sweep (`BANDS`) |
| does it survive me not picking the right stocks? | the Breadth view |
| what did the old split measure? | `scripts.universe_bias` |
| does the ordering rule matter? | the **Signal priority** axis on Compare |
| does it work off the equity market? | the **Assets** view |

## Stamps and caches

Signal caches and `dashboard.json` record the universe, the price files and the
strategy code they were built from, and refuse themselves when any of the three
moves. Two consequences:

- **`signals._CODE` is gone.** Producer modules derive from `kitelab.registry`,
  so a registered strategy is in the stamp by construction. This replaced the
  hand-kept list that omitted `holygrail.py` and invalidated a day of numbers.
- **The code stamp uses file MTIMES, not contents.** Editing a docstring — or a
  `git checkout`, a fresh clone, copying the tree — invalidates every cache and
  forces the full 16-minute rebuild. Safe direction to be wrong in; budget for it.

## Conventions the code holds to

- **Point-in-time correctness everywhere.** Higher-timeframe values are those of
  the *forming* bar; pivots are only usable once confirmed by bars on both
  sides; a hand-drawn level is only valid from its second touch. Anything that
  reads a value before it could have existed is a bug, not an optimisation.
- **Conventions cost the strategy rather than flatter it** — stop and target in
  one bar assumes the stop, gaps fill at the open, one position per symbol.
- **The stop is the entry candle's own low**, uniformly across every strategy.
- Costs are modelled, not assumed away: `slippage.py` separates what is measured
  from what is assumed and says which is which.
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

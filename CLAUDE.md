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
# Safari enforces HTTPS-Only on this Mac, so ./run_dashboard.sh (plain http)
# fails with "navigation failed because request was an http url with https
# enabled". The server is FINE when that happens — do not debug the project.
# Serve over TLS instead and open https://localhost:8765 (with the s):
python3 ~/.kitelab-dev-tls/serve_https.py     # port 8765, Ctrl-C to stop

./run_dashboard.sh                       # port 8765, http — blocked in Safari

# rebuild whatever is stale — no keys, needs pandas
python3 -m scripts.refresh               # --check | --force | --stocks-only
                                         # also runs the two diagnostics below;
                                         # --skip-diagnostics stops after the grid

# the two out-of-band diagnostics, if run by hand (refresh does both for you)
python3 -m scripts.wf_attach             # --pilot N to time one strategy first
python3 -m scripts.attach_diagnostics    # --check | --strip

# BEFORE a rebuild — catches what import cannot (NameError in main(), a payload
# key the build stopped emitting). Five of six rebuilds on 2026-09-03 were spent
# finding faults these would have caught in minutes.
python3 -m unittest discover -s tests -t .   # ~400 tests, ~2s
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

# one-off measurements, off the board; each writes a dated CSV/PNG to output/
python3 -m scripts.wf_<name>             # ceiling cost_gap daily excess lookahead
                                         # power risk survivor tail — see each docstring
```

`scripts/check_dashboard.js` is the only JS test; `unittest` the only Python
one; no `pytest`. **Data lives outside the repo**: `config._resolve()` finds it
(`$KITELAB_DATA_DIR`/`$KITELAB_CLEAN_DIR`, else `/data/{raw,clean}/kitelab`,
else `$HOME/data/...`, else `./data`). RAW is read-only; CLEAN is the only dir
the analysis side writes or reads prices from.

## The grid

**Cut 19 → 13 → 9 on 2026-09-11, in two passes with different criteria.**
Keep the two apart when reading the board: the first cut removed rules that
LOST, the second removed rules that DUPLICATED.

*Pass one, on rank* (a re-rank of all 19 over the rebuilt momentum board, six
views averaged). Removed: `hg|swing` (last of 19 — 0.0% of its 300 cells beat
buy-and-hold and its luckiest cell still lost 3.1 points); `eath|QM` (rank 18)
and `eath|QD` (correlating 0.93/0.92 with the ATH rows kept) with their
unfiltered controls `pair|QM` and `pair|QD`, which existed only to control
them; and `eath|MWD` (correlating **0.968 at a median gap of 0.00** with the
better-ranked `eath|WD` — indistinguishable, not merely similar).

*Pass two, on redundancy* (`scripts/redundancy.py`, run the same evening). The
13-board's labels carried only **3–9 independent ideas**: Kaiser says 3, 90% of
variance 7, Li-Ji 9 — quote the range, never one number. Six of the thirteen
were EMA rules chained at r ≥ 0.65, and the duplication is REAL rather than an
artifact of the 300 shared scenarios (`qmw|0`/`pair|QW` 0.963 falls only to
0.922 once each scenario's cross-strategy mean is subtracted). Removed
`qmw|0`, `pair|QW`, `pair|WD` and — because its control went with them —
`eath|WD`. **None of the four was beaten; each was duplicated**, so the cut
carries no quality signal in either direction: Spearman(average rank,
nearest-twin r) = −0.18, p = 0.56. `pair|MW` was kept as the cluster's
representative. Worst surviving pair: `ema|0`/`pair|MD` at r = 0.646.
Every surviving ATH row still has its control: `eath|MW`→`pair|MW`.

Two things neither cut changed, both worth knowing before reading the board:
**not one** of the rules beats buy-and-hold on its median cell (best is −2.51
CAGR points/yr), and `dv|55-20` is the find — it ranks 2nd of 13 AND has the
**lowest** loading on the common factor (0.080 against 0.25–0.35 for every EMA
rule), the one top-ranked rule that is not a near-copy of another.


**2,700 cells** (9 variants × 300: 5 equity universes × 5 start years × 3
priorities × 2 risks × 2 capitals), keyed
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
- **Signal priority is the smallest of the three axes, and three of its five
  settings were measured to be worse than chance** (2026-09-11,
  `scripts/priority_control.py`: 20 seeded shuffles per cell, costs on, all 19
  strategies × 2 account sizes). Best-minus-worst CAGR across priority is
  median 6.7 points, against 24.35 across strategies and 14.75 across start
  year (all three measured on the 19-strategy board, before the 2026-09-11 cut). `liquidity` — the default from 2026-09-03 —
  beat the shuffle mean in 13 of 38 cells; `wide` 16, `illiquid` 19. The axis
  was cut to `mom_hi` (+3.84 pts, 35 of 38), `nearhigh_hi` (+2.37, 29) and
  `tight` (+1.06, 29), and `mom_hi` is the new default. **An earlier version of
  this line said priority was "noise worth up to 20 points" and both halves
  were wrong** — see the docstring of `scripts/deployment.py` for where each
  number came from. Never pick a winner off that table without
  `scripts.bootstrap`; the coin-flip evidence is not in `dashboard.json` at
  all, only in `output/measurements/priority_control_2026-09-11.csv`.
- **BITCOIN and GOLD came off the board 2026-09-11** (user's call: "they arent
  necessary"). `dashboard_data.ASSETS` is empty, so `assets` and `single_name`
  serialise empty — a valid payload, the same one `--stocks-only` builds, and
  `check_dashboard.js` passes on it. The fetch side and `ASSET_EXCLUDED` are
  untouched.
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
(p ≤ 0.05, 100 rounds); **beats the hold day by day** (see below); survives a
cost margin (breakeven bp); clears the family-wise luck hurdle (`t_gate`, the
smaller of the cluster-robust t and its drift-adjusted twin, ≥ ~2.4 — both,
since 2026-09-09).

**The fourth gate changed on 2026-09-10.** It used to be "wins a strict
majority of fixed 3-year windows". Seven windows is seven bits of evidence, and
`scripts/wf_power.py` measured what that buys: the smallest edge the majority
rule spots 80% of the time is **20 CAGR points a year**, and a rule with *no*
edge passes it **49.8%** of the time. A gate a coin flip clears half the time
describes rather than tests. The replacement asks the same question of ~5,000
daily rule-minus-hold returns instead of 7 win/lose bits, with a Newey-West HAC
standard error — lag `max(4·(n/100)^(2/9), 21)` — so overlapping positions and
volatility clustering are not counted as independent evidence. Detectable edge
falls to **~11.8 pts/yr**. Walk-forward keeps its column and its Detail table:
it answers *when* the edge was there, which a whole-sample test cannot.

The bar is **not** a nominal 0.05. 2,700 cells tested at once is 2,700 chances
to be lucky (~135 would clear 0.05 with no edge at all), so the gate is a
**Benjamini-Hochberg** false-discovery-rate threshold across every tested cell;
the uncorrected and Bonferroni counts are shown beside it, never gated on. BH
rather than Bonferroni because these cells are heavily correlated — 9 rules
re-run over overlapping universes and start years, and the 2026-09-11
redundancy pass showed the rules themselves are not independent either — and
Bonferroni on correlated tests is far stricter than its own nominal level.
**The redundancy cut does not move this gate.** BH is valid under positive
dependence, and correlation changes the VARIANCE of the "how many cells clear
0.05" count, not its expectation. What a duplicate-free board changes is the
evidential weight of the leaderboard's top rows, not the verdict.

Quote the `validation_summary` spread (`tried`, `n_eff`, `hurdle`, `cleared`…),
never a single headline number: the hurdle guards the t only, while the MAR the
top row sorts by is an uncorrected max over 300 cells, three of whose axes
(priority, start year, universe) are choices no trader made in advance.
Full detail — the five checks' history, the
credibility statistic, drift_r, the holdout stand-ins table, how to add a
strategy — is in the **kitelab-validation** skill. The 2026-09-07 audit made
every gate stricter; the board looking worse than 2026-09-05 is the repair
working.

### The two out-of-band diagnostics

Both are computed **outside** `scripts/dashboard_data.py`, and that is a
deliberate cost decision, not laziness. Every file in `kitelab.signals._ACCOUNT`
— which includes `'../scripts/dashboard_data.py'` — is content-hashed into
`_grid_digest`, so adding thirty lines to the build script invalidates all 38
grid partitions *and* the validation summary, costing a ~140-minute rebuild to
compute something that needs none of it. The standalone harness loads the same
signal caches, applies `slippage.apply_spread` under
`ENABLED=True, MAX_PARTICIPATION=0.01`, and calls `portfolio.run` — verified to
reproduce a board cell exactly (`SELF_CHECK_KEY` in `wf_attach.py`, which exits
non-zero if it ever stops matching).

| Script | What it adds | Gated? |
|---|---|---|
| `scripts/wf_attach.py` | arm A: the daily-excess test. arm B: the same accounts refilled at the **next session's open** instead of the signal's own close | A yes, B no |
| `scripts/attach_diagnostics.py` | merges `output/wf_attach_<date>.json` into `dashboard.json` as `daily_excess`, `fill_timing`, `diagnostics`; computes the BH bar | — |

`scripts/refresh.py` runs both after the grid (`--skip-diagnostics` to stop
short). A payload without the block is **not broken**: the page falls back to
the seven-window gate and says so in a banner, and `check_dashboard.js` reports
the fallback as `ok`.

**Arm B is shown, never gated**, and the reason is written on the page rather
than buried: `NEXT_OPEN_FILLS` (`kitelab/backtest.py:77`, default `False`) has
per-producer semantics, and its next-open branch is independently verified for
**`backtest.py` only** — one engine of the nine rules on the board. Gating a
verdict on a computation whose own code has not been checked is worse than
showing the number with a provisional flag. Promote engines into
`ARM_B_VERIFIED` as they are checked; the page's wording follows the constant.

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
- `tests/` — ~400 hermetic tests, no price files or network (`support.py`
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
- `experiments/` — ad-hoc hand comparisons (HAL, Manappuram, the DI stop) and
  the 2026-09-08 gate report; not part of any pipeline, nothing imports them.
  Run from the repo root: `python3 -m experiments.<name>`.
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
- **The live exposure is multiple testing, not contamination**: 9 variants
  ranked and a winner reported — and 9 LABELS is not 9 independent tries, since
  the surviving rules still correlate up to 0.646. The luck hurdle is a
  family-wise 95% bar; quote the `validation_summary` spread the page shows
  above the table.
- **Survivorship is untouched and remains the largest known bias here.** The
  one measurement so far (`scripts/wf_survivor.py`, 2026-09-08: delistings
  applied to rule and hold alike) moves hold by 0.6–2.1 pts/yr and the
  rule-vs-hold gap by under 0.2, and it is a floor — dead companies have no
  price history to kill. The credibility gate now tests each rule against random entries
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

**Editing a stamped module invalidates what depends on it, and that is the safe
direction to be wrong in; budget for it.** The last two full rebuilds of the same
code took 103 min (2026-09-07) and 146 min (2026-09-09) — the difference was the
host, not the code (run the 0.44 s benchmark in the kitelab-history skill before
quoting a duration). The permutation test is the long stage (forked across
`validation.PERMUTATION_WORKERS`; p is identical at any worker count because
every round carries its own seed).

**A rebuild is cut into parts (2026-09-09), so "invalidates the caches" is no
longer "invalidates all of them".** Two independent tiers:

- *Signal caches* are stamped against the touched producer's **transitive
  import closure** (`signals.reachable_from`, read out of the import
  statements with `ast`) unioned with `_SUPPORT`, and `signals.producer_of`
  resolves a cache name to its producer by longest prefix so no call site
  passes it. Measured on the 9-variant board: editing `darvas.py` rebuilds 4
  strategies, `timeframes.py` 3, `backtest.py` all 9 (every engine imports
  it), any `_SUPPORT` module all 9. `holygrail.py` now rebuilds **0** — it is
  on disk but off the board since 2026-09-11, so editing it cannot invalidate
  a cached trade; `tests/test_stamps.py` pins that in both directions. `_SUPPORT` stays global on
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
cells identical. Left as measured; that board had 19 partitions and five
priorities, where today's has 9 and three.

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

- **A diagnostic that never reaches the page is not done.** The day-by-day
  test existed as `scripts/wf_daily.py` for two days before anything on the
  dashboard read it, because reaching the page depended on someone remembering
  to run it. Anything meant to change a verdict goes into `refresh.py`,
  `dashboard.html` and `check_dashboard.js` in the same change as the maths.
- **Never edit `scripts/dashboard_data.py` to add a measurement.** It is in
  `signals._ACCOUNT`, so a comment in it costs a ~140-minute rebuild. Read the
  finished payload from a new script instead — see the two above.
- **Compiling is not working.** Most bugs here passed every static check — a
  missing import, MAR computed but never put in the payload, numpy scalars
  that will not serialise. Dry-run the page against the built file
  (`node scripts/check_dashboard.js`) after every rebuild.
- `dashboard_server.serve()` binds `127.0.0.1` with no host parameter: a
  server inside the dev container is unreachable from the host browser and
  `-p 8765:8765` does not help. `curl` in-container proves only liveness.
- **The Mac views the dashboard over HTTPS, not `./run_dashboard.sh`.** Safari
  enforces HTTPS-Only there and refuses a plain-http navigation outright. The
  fix is a trusted self-signed cert, NOT an http allowlist — so the browser is
  satisfied rather than bypassed. `~/.kitelab-dev-tls/serve_https.py` imports
  `Handler` from `dashboard_server` and wraps the socket in TLS; same page,
  same data. It lives OUTSIDE the repo and is not in git, so a machine rebuild
  loses it. To recreate (cert good 825 days, covers both names):

      mkdir -p ~/.kitelab-dev-tls && openssl req -x509 -newkey rsa:2048 -nodes \
        -days 825 -keyout ~/.kitelab-dev-tls/key.pem \
        -out ~/.kitelab-dev-tls/cert.pem -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

  then trust `cert.pem` in Keychain. The wrapper skips `run_dashboard.sh`'s
  data-dir discovery, so if the page says "No data yet", prefix the command
  with `KITELAB_CLEAN_DIR="$HOME/data/clean/kitelab"`.
- `pkill -f "scripts.dashboard"` matches its own shell and kills the session.
  Use PIDs; `pgrep -a -f` has the same problem.
- `/tmp` is cleared between sessions. Long-running logs go in `output/`.
- **`output/` is gitignored, so a delete there is permanent, and every script
  hardcodes it FLAT** — `wf_attach.py:69`, `attach_diagnostics.py:44`,
  `wf_power.py:45`, `wf_survivor.py:72`, `wf_risk.py:123`, `wf_excess.py:35` all
  resolve `OUT = <repo>/output` and join a bare filename, never searching
  subdirectories. Tidied into folders 2026-09-10 (`showcase/`, `classwork/`,
  `measurements/`, `logs/`); the files left at the top level are the live
  checkpoints scripts read back, and filing one away silently costs its re-run
  (`wf_attach_<built date>.json` = the 106.9-min diagnostics run;
  `wf_attach_ckpt_<board key>/` and `wf_attach_hold_<board key>.pkl` = resume
  instead of restart). **The two checkpoints are keyed on `wf_attach.board_key`,
  not the date** — they were date-keyed until 2026-09-11, when the 19-strategy
  board (14:45 IST) and the 13-strategy board that replaced it (17:50) collided
  and the second run reused all 26 of the first's partitions in 0.4 min.
  `if path.exists()` was the whole test. The numbers survived it — two
  strategies recomputed from scratch gave byte-identical pickles over 1,200
  cells — but by luck, not by construction. The output JSON keeps its date name
  because `attach_diagnostics` resolves it by date and then guards the handoff
  on the full `built` string. **`board_key` also hashes `wf_attach.py` and
  `wf_daily.py` whole**, added the same day after fixing the `recent` hold curve
  left a stale `wf_attach_hold_*.pkl` that the board key had no way to reject —
  the board had not changed, only the code that filled the pickle had. The cost
  is that a comment in either file invalidates ~75 min of diagnostics; that is
  the same trade `dashboard_data.py` makes by sitting in `signals._ACCOUNT`, and
  it is deliberate. If an edit is genuinely inert, **prove it** (recompute two
  strategies, diff the pickles) and carry the rest over by hand — do not widen
  the key to make an edit cheap. `output/README.md`
  carries the full map — and is itself gitignored, so this entry is the durable copy.
- Small samples mislead — the ATH band looked neutral on 30 stocks and clearly
  harmful on 101.
- Deleting a block can take a shared import with it, 20 minutes into a rebuild.
- `check_dashboard.js` asserts VALUES against the payload since 2026-09-07 and
  correctly FAILS against a payload built before that date: rebuild.
- The producer's paper book is ₹1cr (`sizing.CAPITAL`) so no signal the
  largest account could take is dropped from the cache; rupee figures in
  `trade_stats` describe no account on the page — read the R-multiple fields.
- `pyflakes` is clean except one known warning (`scripts/refresh.py:69`,
  pandas imported but unused). Anything else is yours.

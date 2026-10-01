# Code review — 2026-10-01

Scope: read-only review of commit `1f980d1` after cleanup `c5df6f1`. **VERIFIED** unless labeled **SUSPECTED**. No cache or research rebuild was run. Commands shown are reproducible read-only checks. Inventory below includes every tracked Python, JavaScript, shell, and HTML file; `none found` means no literal import/reference in the scanned source and README/NEXT_TESTS, not proof that a command line tool has no user.

## Baseline

**VERIFIED** `git -c safe.directory=/srv/rw/kitelab ls-files | wc -l` → **124 tracked files**; `... ls-files | xargs -r wc -l | tail -1` → **42,626 tracked lines**. `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider` → **471 passed, 4 skipped, 25 subtests passed, 0 failed; exit 0**. `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/srv/rw/kitelab PYTHONDONTWRITEBYTECODE=1 ./check_all.sh` → **475 unittest cases, 4 skipped, page checks passed, exit 0**. Its saved payload is dated **2026-09-19 13:32 IST**, has **10,800 cells**, and reports **0/36 rows beating hold**. The shell script's exit code remains unreliable on a failing suite (known audit item).

### Caller map and inventory

**VERIFIED static scan**: `git ls-files` plus a Python text inventory over tracked `.py/.js/.sh/.html`, with literal module/path references in source, README, and NEXT_TESTS. **108 files, 34,871 lines**. Long caller lists show the first five paths and a remaining count. Package `__init__` files are import plumbing; tests are discovered by pytest/unittest. This is a static caller map, so `getattr`, `Strategy.module`, registry dispatch, module aliases, and user CLI invocation need separate confirmation before deletion.

<!-- INVENTORY_START -->
| File | Lines | Static callers / entry |
|---|---:|---|
| `check_all.sh` | 141 | `NEXT_TESTS.md`, CLI |
| `kitelab/__init__.py` | 1 | package import |
| `kitelab/access.py` | 152 | `tests/test_dashboard_door.py` |
| `kitelab/auth.py` | 99 | `scripts/backfill.py`, `scripts/fetch_assets.py`, `scripts/login.py`, `README.md` |
| `kitelab/backtest.py` | 541 | `scripts/build_report.py`, `scripts/dashboard_data.py`, `scripts/fixed_sim.py`, `scripts/n500_grid.py`, `scripts/wf_attach.py`, +13 more |
| `kitelab/config.py` | 503 | `scripts/attach_diagnostics.py`, `scripts/backfill.py`, `scripts/backup_inputs.py`, `scripts/build_fixed_report.py`, `scripts/build_report.py`, +25 more |
| `kitelab/contracts.py` | 209 | `kitelab/portfolio.py`, `scripts/dashboard_data.py`, `scripts/fetch_assets.py`, `tests/test_account.py` |
| `kitelab/curves.py` | 177 | `scripts/dashboard_data.py`, `tests/test_metrics.py`, `tests/test_plumbing.py` |
| `kitelab/darvas.py` | 294 | `tests/test_next_open.py`, `tests/test_signals_gen.py`, `registry/relative import` |
| `kitelab/dashboard_server.py` | 498 | `scripts/dashboard.py`, `scripts/dashboard_data.py`, `scripts/preflight.py`, `scripts/refresh.py`, `tests/test_dashboard_door.py`, +2 more |
| `kitelab/entries.py` | 521 | `kitelab/indicators.py`, `scripts/build_report.py`, `scripts/fixed_sim.py`, `scripts/us_rules.py`, `scripts/waterfall.py`, +4 more |
| `kitelab/excursion.py` | 89 | `tests/test_excursion.py`, `NEXT_TESTS.md` |
| `kitelab/fetch.py` | 302 | `scripts/backfill.py`, `scripts/fetch_assets.py`, `tests/test_fetch_dtypes.py`, `tests/test_frames_clean.py`, `README.md` |
| `kitelab/frames.py` | 653 | `scripts/build_fixed_report.py`, `scripts/clean_data.py`, `scripts/dashboard_data.py`, `scripts/diagnose.py`, `scripts/entry_exit_grid.py`, +19 more |
| `kitelab/holygrail.py` | 445 | `tests/test_next_open.py`, `tests/test_pointintime.py`, `tests/test_signals_gen.py`, `registry/relative import` |
| `kitelab/indicators.py` | 111 | `scripts/fixed_rules.py`, `scripts/fixed_sim.py`, `scripts/us_rules.py`, `scripts/waterfall.py`, `scripts/wf_intraday.py`, +2 more |
| `kitelab/levels.py` | 154 | `tests/test_frames_clean.py`, `tests/test_levels_trailing.py`, `tests/test_pointintime.py`, `tests/test_remaining.py`, `registry/relative import` |
| `kitelab/pine.py` | 172 | `scripts/fixed_sim.py`, `registry/relative import` |
| `kitelab/portfolio.py` | 788 | `scripts/n500_grid.py`, `scripts/pbo.py`, `scripts/survivorship_test.py`, `scripts/wf_attach.py`, `scripts/wf_daily.py`, +7 more |
| `kitelab/progress.py` | 98 | `scripts/dashboard_data.py` |
| `kitelab/registry.py` | 592 | `kitelab/signals.py`, `kitelab/strategies.py`, `scripts/build_report.py`, `scripts/dashboard_data.py`, `scripts/n500_grid.py`, +6 more |
| `kitelab/screener.py` | 231 | `tests/test_remaining.py`, `registry/relative import` |
| `kitelab/signals.py` | 431 | `kitelab/registry.py`, `scripts/clean_data.py`, `scripts/dashboard_data.py`, `scripts/preflight.py`, `scripts/survivorship_test.py`, +5 more |
| `kitelab/sizing.py` | 61 | `scripts/build_report.py`, `scripts/fixed_sim.py`, `scripts/wf_intraday.py`, `tests/support.py`, `tests/test_levels_trailing.py`, +1 more |
| `kitelab/slippage.py` | 268 | `scripts/build_report.py`, `scripts/dashboard_data.py`, `scripts/entry_exit_grid.py`, `scripts/fixed_sim.py`, `scripts/n500_grid.py`, +11 more |
| `kitelab/strategies.py` | 456 | `tests/test_plumbing.py`, `registry/relative import` |
| `kitelab/timeframes.py` | 355 | `kitelab/registry.py`, `tests/test_ath_filter.py`, `tests/test_next_open.py`, `tests/test_remaining.py`, `registry/relative import` |
| `kitelab/trailing.py` | 134 | `tests/test_levels_trailing.py`, `registry/relative import` |
| `kitelab/validation.py` | 1471 | `kitelab/config.py`, `kitelab/registry.py`, `scripts/build_report.py`, `scripts/dashboard_data.py`, `scripts/n500_hold.py`, +5 more |
| `run_dashboard.sh` | 142 | check_all.sh, `kitelab/config.py`, `scripts/build_standalone.py`, `scripts/check_article.js`, `scripts/dashboard_data.py`, +4 more |
| `scripts/__init__.py` | 0 | package import |
| `scripts/attach_diagnostics.py` | 188 | `scripts/check_dashboard.js`, `scripts/preflight.py`, `scripts/refresh.py`, `scripts/wf_attach.py`, `tests/test_diagnostics.py`, +1 more |
| `scripts/backfill.py` | 67 | `kitelab/frames.py`, `scripts/refresh.py`, `scripts/screen_universe.py`, `README.md` |
| `scripts/backup_inputs.py` | 133 | `kitelab/levels.py`, `scripts/refresh.py` |
| `scripts/build_fixed_report.py` | 564 | open report CLI |
| `scripts/build_n500_report.py` | 944 | report CLI |
| `scripts/build_report.py` | 1185 | `scripts/build_n500_report.py`, report CLI |
| `scripts/build_standalone.py` | 272 | check_all.sh, `scripts/check_standalone.js` |
| `scripts/check_article.js` | 363 | check_all.sh, `web/article.html` |
| `scripts/check_dashboard.js` | 1021 | check_all.sh, `kitelab/validation.py`, `scripts/check_article.js`, `scripts/dashboard_data.py`, `scripts/preflight.py`, +2 more |
| `scripts/check_standalone.js` | 233 | check_all.sh, `scripts/build_standalone.py` |
| `scripts/clean_data.py` | 367 | `kitelab/config.py`, `kitelab/frames.py`, `scripts/backup_inputs.py`, `scripts/refresh.py` |
| `scripts/dashboard.py` | 40 | `kitelab/dashboard_server.py`, run_dashboard.sh |
| `scripts/dashboard_data.py` | 1394 | `kitelab/dashboard_server.py`, `kitelab/progress.py`, `kitelab/registry.py`, `kitelab/signals.py`, `kitelab/validation.py`, +14 more |
| `scripts/diagnose.py` | 132 | `README.md`, `NEXT_TESTS.md`, README CLI |
| `scripts/entry_exit_grid.py` | 576 | research CLI |
| `scripts/fetch_assets.py` | 187 | `scripts/dashboard_data.py` |
| `scripts/fixed_rules.py` | 404 | `scripts/build_fixed_report.py`, `NEXT_TESTS.md` |
| `scripts/fixed_sim.py` | 420 | `scripts/build_fixed_report.py` |
| `scripts/login.py` | 17 | `kitelab/auth.py`, `README.md` |
| `scripts/n500_figures.py` | 327 | `scripts/build_n500_report.py` |
| `scripts/n500_grid.py` | 391 | `scripts/build_n500_report.py` |
| `scripts/n500_hold.py` | 244 | `scripts/build_n500_report.py`, `scripts/n500_grid.py`, research CLI |
| `scripts/n500_profile.py` | 293 | `scripts/build_n500_report.py`, research CLI |
| `scripts/n500_text.py` | 261 | `scripts/build_n500_report.py` |
| `scripts/nifty500_list.py` | 230 | `scripts/n500_hold.py` |
| `scripts/pbo.py` | 464 | research CLI |
| `scripts/preflight.py` | 288 | `kitelab/validation.py`, `scripts/dashboard_data.py`, `scripts/refresh.py`, `tests/test_preflight.py`, `tests/test_validation.py` |
| `scripts/prerender.js` | 77 | `scripts/build_standalone.py` |
| `scripts/refresh.py` | 237 | check_all.sh, `kitelab/dashboard_server.py`, run_dashboard.sh, `scripts/backup_inputs.py`, `scripts/build_standalone.py`, +10 more |
| `scripts/report_figures.py` | 168 | `scripts/build_report.py` |
| `scripts/report_text.py` | 382 | `scripts/build_report.py` |
| `scripts/screen_universe.py` | 331 | `kitelab/config.py`, `scripts/backfill.py`, `scripts/backup_inputs.py`, `scripts/dashboard_data.py`, `scripts/wf_daily.py` |
| `scripts/show.py` | 116 | `scripts/diagnose.py`, `README.md`, `NEXT_TESTS.md`, README CLI |
| `scripts/survivorship_test.py` | 106 | `scripts/wf_survivor.py`, research CLI |
| `scripts/us_fetch.py` | 154 | `scripts/us_rules.py`, `scripts/waterfall.py` |
| `scripts/us_rules.py` | 322 | `scripts/waterfall.py` |
| `scripts/waterfall.py` | 534 | `NEXT_TESTS.md`, research CLI |
| `scripts/wf_attach.py` | 562 | `scripts/attach_diagnostics.py`, `scripts/check_dashboard.js`, `scripts/pbo.py`, `scripts/preflight.py`, `scripts/refresh.py`, +2 more |
| `scripts/wf_daily.py` | 414 | `scripts/n500_hold.py`, `scripts/n500_profile.py`, `scripts/pbo.py`, `scripts/wf_attach.py`, `scripts/wf_survivor.py` |
| `scripts/wf_intraday.py` | 424 | `scripts/us_rules.py`, `scripts/waterfall.py`, `NEXT_TESTS.md` |
| `scripts/wf_lookahead.py` | 375 | `scripts/wf_attach.py` |
| `scripts/wf_survivor.py` | 286 | research CLI |
| `tests/__init__.py` | 0 | `pytest/unittest discovery`, package import |
| `tests/support.py` | 132 | `tests/test_account.py`, `tests/test_ath_filter.py`, `tests/test_costs.py`, `tests/test_data_hygiene.py`, `tests/test_frames_clean.py`, +6 more |
| `tests/test_account.py` | 547 | `pytest/unittest discovery` |
| `tests/test_account_metrics.py` | 86 | `pytest/unittest discovery` |
| `tests/test_ath_filter.py` | 186 | `kitelab/registry.py`, `tests/test_signals_gen.py`, `pytest/unittest discovery` |
| `tests/test_costs.py` | 50 | `pytest/unittest discovery` |
| `tests/test_dashboard_door.py` | 220 | `pytest/unittest discovery` |
| `tests/test_data_hygiene.py` | 103 | `pytest/unittest discovery` |
| `tests/test_diagnostics.py` | 283 | `pytest/unittest discovery` |
| `tests/test_entries.py` | 152 | `pytest/unittest discovery` |
| `tests/test_excursion.py` | 76 | `NEXT_TESTS.md`, `pytest/unittest discovery` |
| `tests/test_fetch_dtypes.py` | 85 | `pytest/unittest discovery` |
| `tests/test_frames_clean.py` | 391 | `pytest/unittest discovery` |
| `tests/test_grid_checkpoints.py` | 157 | `pytest/unittest discovery` |
| `tests/test_indicators.py` | 173 | `pytest/unittest discovery` |
| `tests/test_invariants.py` | 212 | `pytest/unittest discovery` |
| `tests/test_levels_trailing.py` | 125 | `pytest/unittest discovery` |
| `tests/test_metrics.py` | 77 | `pytest/unittest discovery` |
| `tests/test_next_open.py` | 302 | `pytest/unittest discovery` |
| `tests/test_oracle.py` | 155 | `pytest/unittest discovery` |
| `tests/test_plumbing.py` | 166 | `pytest/unittest discovery` |
| `tests/test_pointintime.py` | 98 | `pytest/unittest discovery` |
| `tests/test_preflight.py` | 73 | `pytest/unittest discovery` |
| `tests/test_remaining.py` | 151 | `pytest/unittest discovery` |
| `tests/test_resample.py` | 96 | `pytest/unittest discovery` |
| `tests/test_signals_gen.py` | 124 | `pytest/unittest discovery` |
| `tests/test_simulate.py` | 169 | `pytest/unittest discovery` |
| `tests/test_slippage.py` | 157 | `pytest/unittest discovery` |
| `tests/test_staleness.py` | 117 | `pytest/unittest discovery` |
| `tests/test_stamps.py` | 237 | `pytest/unittest discovery` |
| `tests/test_start_years.py` | 102 | `pytest/unittest discovery` |
| `tests/test_validation.py` | 858 | `pytest/unittest discovery` |
| `web/article.html` | 1201 | check_all.sh, `kitelab/dashboard_server.py`, `scripts/build_report.py`, `scripts/build_standalone.py`, `scripts/check_article.js`, +2 more |
| `web/dashboard.html` | 2168 | check_all.sh, `kitelab/dashboard_server.py`, `kitelab/validation.py`, `scripts/check_dashboard.js`, `scripts/dashboard_data.py`, +4 more |
| `web/login.html` | 60 | `kitelab/dashboard_server.py` |
<!-- INVENTORY_END -->

Entry chain, **VERIFIED** `rg -n 'scripts.dashboard|scripts.refresh|dashboard_data|dashboard_server|check_dashboard|check_article' run_dashboard.sh check_all.sh scripts/refresh.py scripts/dashboard.py kitelab/dashboard_server.py`: `run_dashboard.sh` → `scripts.dashboard` → `kitelab.dashboard_server` → `web/{dashboard,article,login}.html`; `scripts.refresh` → `scripts.dashboard_data` → `dashboard.json`; `check_all.sh` reads that payload, runs both JS page checks, unittest and pyflakes. `scripts.build_standalone` plus `scripts.prerender.js` build the optional single-file page. `scripts.show` and `scripts.diagnose` are live README instructions at lines 129–142 and are excluded from deletion.

## Delete list

| Path/definition | Lines | Evidence and risk | Signal cache / published number / test |
|---|---:|---|---|
| `kitelab/excursion.py` | 89 | **VERIFIED** `rg -n 'excursion|excursions|worst_survivable' kitelab scripts tests README.md NEXT_TESTS.md`: only `tests/test_excursion.py` calls it; old `scripts/stops.py` and `stop_rescue.py` were deleted. Risk: external, undocumented imports. | In neither `_SUPPORT` nor producer closure: **no cache invalidation**, no published board number; its 8 tests are the only coverage. |
| `tests/test_excursion.py` | 76 | **VERIFIED** exclusively tests the above orphan. Risk: loses isolated MAE/MFE checks. Delete together after owner accepts retired study. | No cache/number; test count drops by 8. |
| `kitelab/strategies.py:support_bounce_trades` | ~48 | **VERIFIED** `rg -n 'support_bounce_trades' kitelab scripts tests README.md NEXT_TESTS.md` shows only definition. **SUSPECTED** delete after checking external users and registry; this file is otherwise live. | `_SUPPORT` hashes entire `strategies.py`: editing it invalidates **all signal caches** and dashboard; no expected number change; no direct test. |
| `kitelab/timeframes.py:window_start` | ~5 | **VERIFIED** same `rg` pattern: only definition. **SUSPECTED** removal; confirm no external caller. | `timeframes.py` is a producer/import: affected producer caches and dashboard invalidated; no expected number change; no direct test. |
| `scripts/build_fixed_report.py:md_table`; `scripts/n500_figures.py:fig_hold_ladder,fig_overlap` | ~51 combined | **VERIFIED** `rg -n 'md_table|fig_hold_ladder|fig_overlap' kitelab scripts tests README.md NEXT_TESTS.md` finds definitions only. Files themselves remain live research/report assets. Risk: manual report callers. | Neither script is stamped; no signal cache/board change; report visuals could change only if an undocumented caller exists; no tests. |

**Proposed whole-file deletion: 2 files, 165 lines.** Definition removals are separately estimated at ~104 lines and are not counted as file deletions. Do not delete registry-selected engines just because literal module-name search says `NONE`.

## Merge list

| Proposal | Copies and agreement | Lines saved, risk, cache cost |
|---|---|---|
| Extract shared EMA trade walker into `kitelab/backtest.py` and call it from `kitelab/timeframes.py` | **VERIFIED** `backtest.py:292–486` and `timeframes.py:172–322` duplicate rising-edge entry, stop selection, next-open branch, exit search, spread and sizing. Their base-bar timestamp handling intentionally differs (`end_ts` in timeframes), so the copies do **not** agree for aggregated bars by design. `darvas.py:123–294` and `holygrail.py:244–445` have rule-specific walkers; reuse a small fill/sizing primitive only after differential tests. | **SUSPECTED** ~60–100 net lines saved. High regression risk: trade timestamps, stop ties, and open fills. Editing `backtest.py` / `timeframes.py` invalidates affected producer stamps and the dashboard; numbers should remain identical only after trade-list equality tests. Existing simulation/next-open tests cover behavior but not full differential parity. |

Other repeated CAGR/drawdown and hold/loading code appears in `waterfall.py:271–280`, `wf_survivor.py:169–174`, `wf_daily.py:132–166`, `n500_hold.py:123–151`, and `validation.py:175–245`. `wf_survivor.py:164–166` repeats `wf_daily.py:160–161` first-close normalization, but a shared helper saves only ~2 lines and adds cross-script coupling, so no merge is proposed; neither file is stamped, and no number should change. **VERIFIED** broader formulas differ: waterfall annualizes `len(returns)/252` and rebalances daily; validation annualizes elapsed days/365.25 and holds fixed shares. Merging them before resolving those conventions would change numbers. Standalone research CLIs also repeat argument and data loading setup, but share too little to justify a common command framework.

## Logic findings, ranked by severity

1. **Changes a number — VERIFIED: false “buy and hold” rung.** `scripts/waterfall.py:340–344` computes `wide.mean(axis=1).fillna(0)`, an equal-weight **daily rebalanced** portfolio, while `validation.py:175–245` and `wf_daily.py:132–166` use fixed-share buy and hold. Probe `python3 - <<'PY' ...` with A `[100,200,100]` and B `[100,100,100]`: rebalanced terminal **1.125**, static terminal **1.000**. Affects waterfall rung F and every rule-minus-hold comparison, not the saved dashboard's 0/36 comparison. Size/direction in published waterfall is **unmeasured**; requires a dedicated waterfall rerun, which this job forbids. No test asserts the two definitions match. Fixing `scripts/waterfall.py` does not invalidate signal caches (`scripts/` is outside their producer stamp); it changes waterfall research numbers.
2. **Cosmetic/retired study — VERIFIED: even-sample median wrong.** `kitelab/excursion.py:76–82` takes sorted element `xs[len(xs)//2]`. For `[1,3]` it reports **3**, while median is **2** (`python3` probe); affects MAE/MFE summaries with even sample sizes. No published dashboard impact, and the proposed orphan deletion supersedes a fix. `tests/test_excursion.py` does not cover an even sample median. Deleting the file has no signal stamp cost.

**Checked for additional new errors — no new finding established.** **VERIFIED grep/code review** `rg -n 'shift\(-|center=True|bfill|dropna|merge\(|join\(|fillna|\.std\(|hash\(|default_rng|rolling\(' kitelab scripts`: the centered/bfill cleaner, full-panel random-rate fit, Python-hash seed, full-series symbol filter and daily cap resizing are already in the 09-30 audit. `slippage.py:92–102` lags ADV/volatility; `entry_exit_grid.py:172` uses current-date cross-sectional ranks, not future rows; `frames.py:641` drops only aggregation rows without `open`; `fetch.py:184` inner merge and `us_rules.py:182` paired-date `dropna` need row-count probes before claiming material loss. **VERIFIED** `config.example.toml` has 8 keys, all read by `config.py` (`tomllib` plus `rg` key scan); no unused sample-config key. No new pandas 3 copy-on-write/string dtype/groupby-observed/fillna downcast or integer-division error was demonstrated by the full suite. Population `ddof=0` in `indicators.py:97` is the specified Bollinger convention; sample `ddof=1` in research volatility/SE code is explicit or pandas default. **SUSPECTED** any unexecuted research path may still contain leakage or silent row loss; no broad negative proof is claimed.

## Exact cache and publication cost

**VERIFIED** `sed -n '34,88p;174,215p;263,322p' kitelab/signals.py`: `stamp()` hashes the sorted **set of symbol names**; for existing `{symbol}_{day,15minute,30minute}.parquet` files it digests **name, byte size, nanosecond mtime** (not price contents); and it digests SHA-256 **source bytes** (16 hex chars per file) for `_SUPPORT` = `frames,sizing,indicators,slippage,levels,strategies,trailing,registry,config,screener` plus each registered producer's transitive `kitelab` import closure. Per-producer signal caches narrow the closure; `account=True` uses **all registered producers** plus `portfolio,curves,validation,contracts,../scripts/dashboard_data.py`. It records producer/account markers. Thus editing `scripts/waterfall.py`, `wf_daily.py`, `wf_survivor.py`, or the report-only scripts costs no signal rebuild; editing `strategies.py` costs all signal caches; editing a producer or `backtest.py` costs its dependent caches. Tests in `tests/test_stamps.py` guard the registered import closure. Cache invalidation is separate from published-number change: a code-only refactor still changes its source digest.

## Audit items still open

One line each; **VERIFIED** via current code grep and baseline unless otherwise marked. These are status, not new findings.

- Present-day Nifty/US memberships and screened “PIT” comparator: **OPEN** (`n500_hold.py:123–145`, `us_fetch.py:43–68`).
- Same-close default: **OPEN**, with next-open effects **already measured** for the 36-row board in `output/wf_attach_2026-09-19.json`; owner has ended this work (`backtest.py:77`, `wf_attach.py:277–294`).
- Waterfall daily free cap resizing: **OPEN** (`waterfall.py:224–244`).
- Entry/exit grid random rate fitted on full panel: **OPEN** (`entry_exit_grid.py:165–194`).
- Waterfall random seed from `hash()`: **OPEN** (`waterfall.py:355–358`).
- Future-sensitive zero-volume cleaning and waterfall max-move exclusion: **OPEN** (`frames.py:95–139`, `waterfall.py:280–307`); earlier audit measured zero cleaner row loss in current data.
- Corrected gate narrower than total research search; no untouched stock holdout: **OPEN methodological limits** (`validation.py:1350–1468`, audit history).
- `check_all.sh` exits 0 on failed unittest/JS check: **OPEN** (`check_all.sh:104–126`, `NEXT_TESTS.md:28,35`).

## Proposed order of work

1. **Delete job:** remove `excursion.py` and its 8-test file, then optionally the five unreferenced definitions after external API review. Run tests. Cost: zero signal rebuild for the file pair; support/producer definition edits invalidate stamps despite expected identical numbers.
2. **Fix job:** correct `waterfall.py` hold rung to fixed-share buy and hold, add one small two-stock parity test, and recompute waterfall figures when research reruns are authorized. Cache cost: none; published waterfall comparisons change. Fix `check_all.sh` exit propagation separately; no cache cost or intended number change, test by injecting a failing unittest command in an isolated environment.
3. **Merge job:** only after parity fixtures, unify the EMA walkers. Estimated 60–100 lines saved; producer caches and dashboard stamp change, so revalidation is expensive.

**Totals:** whole-file deletion **2 files / 165 lines**; **1 proposed merge / ~60–100 net lines saved (SUSPECTED estimate)**; **2 new logic findings: 0 headline, 1 changes a research number, 1 cosmetic/retired-study**.

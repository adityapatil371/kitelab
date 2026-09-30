# Repository cleanup plan

## Breakage and removal gate

**Working tree already differs from HEAD:** `DI+_- VS 20 EMA - HAL.csv` and `poster.png` are tracked but absent; `.ai-runs/` and `AUDIT_REPORT_2026-09-30.md` are untracked. Inventory and line counts use HEAD. No existing file was changed.

**KEEP-PIPELINE/test imports of proposed RESEARCH-DONE or DEAD files:** none found by Python AST import scan, literal `-m scripts.*` calls, and shell/Node entry-point tracing. Research scripts do import one another; those are removed together. `scripts/build_n500_report.py` imports `scripts/build_report.py`, which imports `scripts/report_figures.py` and `scripts/report_text.py`; all four are kept. `scripts/wf_attach.py` imports `scripts/wf_daily.py`; both are kept.

**Other breakage if removing more later:** `tests/test_excursion.py` tests `kitelab.excursion` exclusively, and `scripts/stops.py`/`scripts/stop_rescue.py` are its only production callers. The test and core module remain because this plan excludes tests/ and kitelab/. `web/article.html` mentions `poster.png` as prose; no HTML `src`, `href` or CSS URL loads it, so removal leaves a stale sentence. `scripts/fixed_sim.py` and `scripts/build_fixed_report.py` consume `scripts/fixed_rules.py`; all three are retained for the pending migration.

**Nifty report import check:** `scripts.build_n500_report` stops at `ModuleNotFoundError: No module named 'reportlab'` in this Python environment. The source imports `reportlab` at module load, while `requirements.txt` and `pyproject.toml` do not list it. The report builder therefore cannot be exercised here; this is an existing dependency gap, not a deletion-induced missing script. Other nine sampled live script imports succeeded.

## Summary

| Bucket | Files | Lines |
|---|---:|---:|
| KEEP-PIPELINE | 49 | 21,811 |
| RESEARCH-DONE | 51 | 17,417 |
| RESEARCH-OPEN | 3 | 1,388 |
| DEAD | 31 | 4,819 |
| DOC-HISTORICAL | 6 | 2,141 |
| **Total** | **140** | **47,576** |

Scope: every `git ls-files` path outside `kitelab/` and `tests/`; binary `poster.png` counts as 0 source lines (915,038 bytes at HEAD). One bucket per path. `CLEANUP_PLAN.md` is a new output of this inventory and excluded from HEAD counts. Classification follows executed imports/calls from README fetch commands, refresh, the Nifty report builder, web serving/checks, `check_all.sh`, `run_dashboard.sh`, and test imports. Documentation mentions alone do not make a script live.

## KEEP-PIPELINE

| File | Lines | Reason | Evidence |
|---|---:|---|---|
| `.gitignore` | 33 | Dependency of a requested live workflow | Git excludes output/, secrets and runtime state. |
| `NEXT_TESTS.md` | 218 | Dependency of a requested live workflow | Pending jobs and completed conclusions; requested research ledger. |
| `README.md` | 162 | Dependency of a requested live workflow | Current setup and the fetch → refresh → serve commands. |
| `SHARING.md` | 258 | Dependency of a requested live workflow | Current sharing procedure for the web pages and standalone page. |
| `check_all.sh` | 141 | Dependency of a requested live workflow | Requested check entry point; executes both Node page checks and unittest. |
| `config.example.toml` | 27 | Dependency of a requested live workflow | README setup copies this configuration template. |
| `data/keep/MANIFEST.txt` | 6 | Dependency of a requested live workflow | scripts.backup_inputs writes the backup inventory. |
| `data/keep/accepted.txt` | 510 | Dependency of a requested live workflow | scripts.backup_inputs preserves screened universe input. |
| `data/keep/ind_nifty500list.SOURCE.txt` | 31 | Dependency of a requested live workflow | Provenance for the Nifty constituent source CSV. |
| `data/keep/ind_nifty500list.csv` | 502 | Dependency of a requested live workflow | scripts.nifty500_list.SEARCH reads it. |
| `data/keep/levels.json` | 238 | Dependency of a requested live workflow | kitelab.levels recovery source; scripts.backup_inputs writes it. |
| `data/keep/nifty500.json` | 4,147 | Dependency of a requested live workflow | n500_profile, n500_hold, n500_grid and build_n500_report read it. |
| `data/keep/universe.json` | 1,165 | Dependency of a requested live workflow | scripts.nifty500_list reads it; backup_inputs writes it. |
| `pyproject.toml` | 29 | Dependency of a requested live workflow | Editable package and runtime/test dependency metadata. |
| `requirements.txt` | 3 | Dependency of a requested live workflow | README environment install input. |
| `run_dashboard.sh` | 142 | Dependency of a requested live workflow | Requested server entry point; executes scripts.dashboard. |
| `scripts/__init__.py` | 0 | Dependency of a requested live workflow | Package marker for python -m scripts.* and tests imports. |
| `scripts/attach_diagnostics.py` | 188 | Dependency of a requested live workflow | refresh.run invokes it after wf_attach. |
| `scripts/backfill.py` | 67 | Dependency of a requested live workflow | README fetch command; calls kitelab.fetch.backfill. |
| `scripts/backup_inputs.py` | 133 | Dependency of a requested live workflow | refresh.run invokes it after a rebuild. |
| `scripts/build_n500_report.py` | 944 | Dependency of a requested live workflow | Requested Nifty PDF builder; imports figures, text and build_report styles. |
| `scripts/build_report.py` | 1,185 | Dependency of a requested live workflow | build_n500_report imports its palette, styles and table helpers. |
| `scripts/build_standalone.py` | 272 | Dependency of a requested live workflow | Builds the standalone article and invokes prerender.js. |
| `scripts/check_article.js` | 363 | Dependency of a requested live workflow | check_all.sh executes it. |
| `scripts/check_dashboard.js` | 1,021 | Dependency of a requested live workflow | check_all.sh executes it. |
| `scripts/check_standalone.js` | 233 | Dependency of a requested live workflow | check_all.sh executes it when a standalone file exists. |
| `scripts/clean_data.py` | 367 | Dependency of a requested live workflow | refresh imports working_set/in_scope and invokes its CLI. |
| `scripts/dashboard.py` | 40 | Dependency of a requested live workflow | run_dashboard.sh executes it; calls dashboard_server.serve. |
| `scripts/dashboard_data.py` | 1,394 | Dependency of a requested live workflow | refresh.run and preflight execute it to build dashboard.json. |
| `scripts/fetch_assets.py` | 187 | Dependency of a requested live workflow | Asset download CLI; calls kitelab.fetch/auth. |
| `scripts/login.py` | 17 | Dependency of a requested live workflow | README fetch login command. |
| `scripts/n500_figures.py` | 327 | Dependency of a requested live workflow | build_n500_report imports it. |
| `scripts/n500_grid.py` | 391 | Dependency of a requested live workflow | Produces the report’s grid measurement CSV and check JSON. |
| `scripts/n500_hold.py` | 244 | Dependency of a requested live workflow | Produces the report’s basket measurement CSV. |
| `scripts/n500_profile.py` | 293 | Dependency of a requested live workflow | Produces the report’s profile CSV and JSON. |
| `scripts/n500_text.py` | 261 | Dependency of a requested live workflow | build_n500_report imports it. |
| `scripts/nifty500_list.py` | 230 | Dependency of a requested live workflow | Builds the constituent JSON from the source CSV and universe. |
| `scripts/preflight.py` | 288 | Dependency of a requested live workflow | refresh.run invokes the smoke build; tests/test_preflight.py imports it. |
| `scripts/prerender.js` | 77 | Dependency of a requested live workflow | build_standalone.py invokes it with Node. |
| `scripts/refresh.py` | 237 | Dependency of a requested live workflow | README dashboard build entry point; calls all six build stages. |
| `scripts/report_figures.py` | 168 | Dependency of a requested live workflow | build_report imports it; required transitively by Nifty report. |
| `scripts/report_text.py` | 382 | Dependency of a requested live workflow | build_report imports it; required transitively by Nifty report. |
| `scripts/screen_universe.py` | 331 | Dependency of a requested live workflow | Fetch workflow screens raw data and writes accepted.txt. |
| `scripts/us_fetch.py` | 154 | Dependency of a requested live workflow | US daily fetch CLI; only program that fetches that market’s raw bars. |
| `scripts/wf_attach.py` | 562 | Dependency of a requested live workflow | refresh.run invokes it; tests/test_diagnostics.py imports its keys. |
| `scripts/wf_daily.py` | 414 | Dependency of a requested live workflow | wf_attach imports its daily account/hold helpers. |
| `web/article.html` | 1,201 | Dependency of a requested live workflow | dashboard_server serves /read; build_standalone consumes it. |
| `web/dashboard.html` | 2,168 | Dependency of a requested live workflow | dashboard_server serves the dashboard; Node checker renders it. |
| `web/login.html` | 60 | Dependency of a requested live workflow | dashboard_server serves authentication page. |

## RESEARCH-DONE

| File | Lines | Reason | Evidence |
|---|---:|---|---|
| `scripts/alpha_eval.py` | 480 | One-off measurement with a recorded conclusion | `9fe92c5`: “NEXT_TESTS item 18: Alpha101 as cash priority, both directions -- none beats mom_hi” |
| `scripts/alpha_priority.py` | 521 | One-off measurement with a recorded conclusion | `9fe92c5`: “NEXT_TESTS item 18: Alpha101 as cash priority, both directions -- none beats mom_hi” |
| `scripts/ath_band.py` | 287 | One-off measurement with a recorded conclusion | `450c11f`: “17.0% for equal-weight hold; 0 of 7 windows against hold at every band.” |
| `scripts/band_scope.py` | 501 | One-off measurement with a recorded conclusion | `1b03c62`: “band_scope: circuit bands cannot move the board -- item 16 closes for free” |
| `scripts/board_span.py` | 390 | One-off measurement with a recorded conclusion | `876d5e1`: “six of its nine step-2 candidates are board rows, so a re-run compares” |
| `scripts/bootstrap.py` | 180 | One-off measurement with a recorded conclusion | `68c3b69`: “The credibility t clusters its standard error by entry quarter (pair\|MW: 20.3” |
| `scripts/data/alpha101.json` | 103 | One-off measurement with a recorded conclusion | `9fe92c5`: “NEXT_TESTS item 18: Alpha101 as cash priority, both directions -- none beats mom_hi” |
| `scripts/data/alpha101_raw.txt` | 203 | One-off measurement with a recorded conclusion | `9fe92c5`: “NEXT_TESTS item 18: Alpha101 as cash priority, both directions -- none beats mom_hi” |
| `scripts/data_audit.py` | 375 | One-off measurement with a recorded conclusion | `428460d`: “History restarts at listing breaks and demergers, and the account keeps one book” |
| `scripts/entry_edge.py` | 480 | One-off measurement with a recorded conclusion | `67f03f3`: “NEXT_TESTS: close open items 2 and 3, and record the negative sweep” |
| `scripts/entry_exit_grid.py` | 576 | One-off measurement with a recorded conclusion | `668852d`: “entry_exit_grid: add a stop axis; the exit's share rises, the entry's collapses” |
| `scripts/entry_zoo.py` | 396 | One-off measurement with a recorded conclusion | `30c7964`: “All nine candidates are distinct from the whole board and from each other.” |
| `scripts/exit_audit.py` | 65 | One-off measurement with a recorded conclusion | `8d1f839`: “exit_audit measured the defect -- 100% of trades on all fourteen families end” |
| `scripts/exit_sweep.py` | 153 | One-off measurement with a recorded conclusion | `67f03f3`: “NEXT_TESTS: close open items 2 and 3, and record the negative sweep” |
| `scripts/exposure_reconcile.py` | 210 | One-off measurement with a recorded conclusion | `f7f669e`: “exposure_reconcile: the breadth correlation was circular” |
| `scripts/hold_dominance.py` | 198 | One-off measurement with a recorded conclusion | `cfa0bef`: “hold_dominance: the nine are not unproven against hold, they are reliably worse” |
| `scripts/hold_longer.py` | 418 | One-off measurement with a recorded conclusion | `68fcdc9`: “hold_longer: the fast rules were taxed by turnover, the slow ones were not” |
| `scripts/liquidity_screen.py` | 313 | One-off measurement with a recorded conclusion | `d146e1e`: “**The liquidity screen** — Finding 2b above.” |
| `scripts/net_in_market.py` | 414 | One-off measurement with a recorded conclusion | `2668118`: “net_in_market: charge the real fills, and the gap to the board closes” |
| `scripts/oos_generalise.py` | 430 | One-off measurement with a recorded conclusion | `f603e1c`: “oos_generalise: the hold-dominance finding replicates out of rule, and weakens after 2017” |
| `scripts/pbo.py` | 464 | One-off measurement with a recorded conclusion | `8661766`: “PBO: the 19 -> 9 rank cut was close to noise (median 0.412)” |
| `scripts/pine_ablate_fix.py` | 190 | One-off measurement with a recorded conclusion | `49bb026`: “stop sweep across 9 rules, and the Pine ablation was mischarged” |
| `scripts/pine_candidate.py` | 347 | One-off measurement with a recorded conclusion | `28ef0d7`: “pine_candidate: the RSI-free Pine loses on 276 of 276 scenarios, and is not redundant” |
| `scripts/pine_span.py` | 415 | One-off measurement with a recorded conclusion | `876d5e1`: “scripts/pine_span.py (new) -- the returns-blind scan that produced the eight” |
| `scripts/raschke_account.py` | 157 | One-off measurement with a recorded conclusion | `d146e1e`: “**RAN 2026-09-24 — the thread is CLOSED** (`scripts/raschke_account.py`).” |
| `scripts/raschke_rules.py` | 339 | One-off measurement with a recorded conclusion | `d146e1e`: “account layer does not pay for. Nothing left to test here.” |
| `scripts/redundancy.py` | 272 | One-off measurement with a recorded conclusion | `3e4f83c`: “Cut the board 13 -> 9 on redundancy, not on rank” |
| `scripts/short_rules.py` | 329 | One-off measurement with a recorded conclusion | `d146e1e`: “NEXT: nothing, until someone prices borrow. Do not build on this.” |
| `scripts/stop_sweep.py` | 878 | One-off measurement with a recorded conclusion | `49bb026`: “never better, and nothing crosses zero: best anywhere is dv\|55-20 + weekly” |
| `scripts/stop_tailor.py` | 270 | One-off measurement with a recorded conclusion | `8e65812`: “Tailored stops: four stop rules, no difference between them” |
| `scripts/survivorship_test.py` | 106 | One-off measurement with a recorded conclusion | `300b989`: “the gap moves from -2.15 to -2.12, not in the rules' favour, because ~15” |
| `scripts/turtle_hold.py` | 509 | One-off measurement with a recorded conclusion | `3148228`: “turtle_hold: with the stop kept, the Turtles never hold long enough to matter” |
| `scripts/us_rules.py` | 322 | One-off measurement with a recorded conclusion | `71de92c`: “The waterfall: the entry is worth nothing without the stop” |
| `scripts/validate.py` | 219 | One-off measurement with a recorded conclusion | `f0a2399`: “Six tests a trader would run, and most rules fail them” |
| `scripts/vol_target.py` | 219 | One-off measurement with a recorded conclusion | `697ea8d`: “Intraday, leverage, and volatility targeting: three closed doors” |
| `scripts/waterfall.py` | 534 | One-off measurement with a recorded conclusion | `71de92c`: “The waterfall: the entry is worth nothing without the stop” |
| `scripts/wf_ceiling.py` | 229 | One-off measurement with a recorded conclusion | `300b989`: “No threshold recovers these strategies.” |
| `scripts/wf_cost_gap.py` | 182 | One-off measurement with a recorded conclusion | `300b989`: “No threshold recovers these strategies.” |
| `scripts/wf_excess.py` | 181 | One-off measurement with a recorded conclusion | `300b989`: “No threshold recovers these strategies.” |
| `scripts/wf_intraday.py` | 424 | One-off measurement with a recorded conclusion | `697ea8d`: “Intraday, leverage, and volatility targeting: three closed doors” |
| `scripts/wf_leverage.py` | 355 | One-off measurement with a recorded conclusion | `697ea8d`: “Intraday, leverage, and volatility targeting: three closed doors” |
| `scripts/wf_lookahead.py` | 375 | One-off measurement with a recorded conclusion | `8e85276`: “The Holy Grail next-open row is not near zero, and the docstring said it was” |
| `scripts/wf_pine.py` | 654 | One-off measurement with a recorded conclusion | `b34e8c5`: “charges Rs 743M -> 85M -> 11M, 0 of 4,080 cells through mde_80), the commands,” |
| `scripts/wf_power.py` | 164 | One-off measurement with a recorded conclusion | `300b989`: “No threshold recovers these strategies.” |
| `scripts/wf_risk.py` | 338 | One-off measurement with a recorded conclusion | `2322a38`: “The rules are the noisier account too, not just the losing one” |
| `scripts/wf_survivor.py` | 286 | One-off measurement with a recorded conclusion | `300b989`: “the gap moves from -2.15 to -2.12, not in the rules' favour, because ~15” |
| `scripts/wf_tail.py` | 204 | One-off measurement with a recorded conclusion | `300b989`: “trails hold by 5.6 pts/yr once 2008 is excluded. wf_survivor applies NSE” |
| `scripts/wf_trail.py` | 676 | One-off measurement with a recorded conclusion | `0a7c440`: “trailing stops: measured, and they do not help” |
| `scripts/xrank_account.py` | 334 | One-off measurement with a recorded conclusion | `d0f296c`: “item 21: xrank does not survive the account layer” |
| `scripts/xrank_breadth.py` | 209 | One-off measurement with a recorded conclusion | `1a5f89e`: “item 22: xrank breadth sweep -- picking 5 is worse, and N=20 is a lead” |
| `scripts/xrank_gate.py` | 543 | One-off measurement with a recorded conclusion | `d0f296c`: “item 21: xrank does not survive the account layer” |

## RESEARCH-OPEN

| File | Lines | Reason | Evidence |
|---|---:|---|---|
| `scripts/build_fixed_report.py` | 564 | Pending fixed-rule migration remains actionable | `NEXT_TESTS.md:217–218` explicitly leaves migration of `fixed_rules.py` into `kitelab/entries.py` pending; these three files form that comparison/report chain. |
| `scripts/fixed_rules.py` | 404 | Pending fixed-rule migration remains actionable | `NEXT_TESTS.md:217–218` explicitly leaves migration of `fixed_rules.py` into `kitelab/entries.py` pending; these three files form that comparison/report chain. |
| `scripts/fixed_sim.py` | 420 | Pending fixed-rule migration remains actionable | `NEXT_TESTS.md:217–218` explicitly leaves migration of `fixed_rules.py` into `kitelab/entries.py` pending; these three files form that comparison/report chain. |

## DEAD

| File | Lines | Reason | Evidence |
|---|---:|---|---|
| `DI+_- VS 20 EMA - HAL.csv` | 40 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `DI___-_VS_20_EMA_MANAPPURAM_-_20_EMA.csv` | 102 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `experiments/__init__.py` | 0 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `experiments/di_stop_loss.py` | 199 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `experiments/turtle_vs_ema_hal.py` | 188 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `experiments/turtle_vs_ema_manappuram.py` | 272 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `pine/drawdown.pine` | 93 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `pine/ema_ath_band.pine` | 84 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `pine/ema_near_high.pine` | 123 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `pine/ema_stack.pine` | 216 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `pine/pullback_uptrend.pine` | 333 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `poster.png` | 0 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `rectangles.csv` | 12 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/cache_status.py` | 33 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/compare_hg_pull.py` | 599 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/deployment.py` | 208 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/diagnose.py` | 132 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/level_audit.py` | 105 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/overnight_split.py` | 188 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/pdftext.py` | 72 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/pine_check_pull.py` | 102 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/portfolio.py` | 76 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/priority_control.py` | 436 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/rank_board.py` | 179 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/rebuild_orphans.py` | 84 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/scan.py` | 97 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/show.py` | 116 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/stop_rescue.py` | 175 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/stops.py` | 98 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/us_overnight.py` | 279 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |
| `scripts/verify.py` | 178 | No live caller and no recorded conclusion located | No import/call from requested entry points or tests; no conclusion in README, NEXT_TESTS, or file history. |

## DOC-HISTORICAL

| File | Lines | Reason | Evidence |
|---|---:|---|---|
| `AUDIT_PROMPT.md` | 280 | Superseded audit/fix artifact or implemented design brief | Superseded by `AUDIT_REPORT.md` and `FIX_REPORT.md`; no runtime load. |
| `AUDIT_REPORT.md` | 388 | Superseded audit/fix artifact or implemented design brief | Superseded by `FIX_REPORT.md` and later `NEXT_TESTS.md` conclusions; no runtime load. |
| `FIX_PROMPT.md` | 277 | Superseded audit/fix artifact or implemented design brief | Superseded by `FIX_REPORT.md`; no runtime load. |
| `FIX_REPORT.md` | 451 | Superseded audit/fix artifact or implemented design brief | Later `NEXT_TESTS.md` and current code record the live state; no runtime load. |
| `experiments/gate_audit_2026-09-08.html` | 608 | Superseded audit/fix artifact or implemented design brief | Dated audit snapshot; current `web/dashboard.html` shows the later gates. |
| `web/LAYOUT_BRIEF.md` | 137 | Superseded audit/fix artifact or implemented design brief | Design brief implemented in current `web/dashboard.html`. |

## Secondary dependency audit

**Root-level data:** `rectangles.csv` is read only by `experiments/turtle_vs_ema_hal.py:19`; the two `DI...csv` files have no literal code reference; `poster.png` is mentioned by `web/article.html:40` but is never loaded as an asset. The HAL CSV and PNG are already missing in the working tree. The Nifty CSV is not stray: `scripts/nifty500_list.py` reads `data/keep/ind_nifty500list.csv`.

**Kitelab code orphaned from live pipeline after proposed removals:** `kitelab.excursion` (`excursions`, `worst_survivable`, `summarise`) is used only by `scripts/stops.py`, `scripts/stop_rescue.py`, and `tests/test_excursion.py`. The test prevents removing it now. Public functions with references only in proposed removals, by static call-site scan: `kitelab.signals.require` (`scripts/survivorship_test.py`); `kitelab.screener.scan` and `scan_history` (`scripts/scan.py`); `kitelab.portfolio.momentum_at` (`scripts/alpha_priority.py`, `scripts/priority_control.py`); `kitelab.entries.signal` (`scripts/wf_intraday.py`, `scripts/compare_hg_pull.py`, `scripts/wf_trail.py`); `kitelab.validation.monthly_returns` (`scripts/bootstrap.py`). No whole other kitelab module is research-only. Dynamic registration and attribute lookup mean these are candidates, not deletion instructions.

**Tests only of removed research code:** none directly import a RESEARCH-DONE or DEAD script. `tests/test_excursion.py` exclusively tests the research-only core module noted above; it remains and should be reviewed in a later, separate core cleanup.

## Temporary worktree proof

The original `.git` is read-only, so a direct `git worktree add` failed. I copied its Git metadata into `/tmp/kitelab-cleanup-source-20260930` and created a detached HEAD worktree at `/tmp/kitelab-cleanup-proof-20260930`, with no commit. Removed all 88 RESEARCH-DONE + DEAD + DOC-HISTORICAL paths there. The first run lacked the ignored `config.local.toml` and had 20 `SystemExit` test failures from `config.load()`; those were environment failures, not import errors. A symlink to the existing config was added **only in the temporary worktree**, and both checks were rerun. No dashboard rebuild occurred.

```text
proof removed files 88
pytest passed=471 failed=0 skipped=4 subtests_passed=25
check_all unittest ran=475 passed=471 failed=0 errors=0 skipped=4
check_all page checks passed= 2
pytest configured exit 0
471 passed, 4 skipped, 25 subtests passed in 9.10s
import error lines 0
check_all configured exit 0
  rows BEATING hold: 0 of 36
  dashboard.html:
    all checks passed
  article.html:
    all checks passed
  Ran 475 tests in 8.596s
  OK (skipped=4)
  scripts/refresh.py:69:5: 'pandas' imported but unused
```

`check_all.sh` reports the unittest verdict but exits 0 even if unittest fails; the first unconfigured run demonstrated that limitation. The configured run printed `OK (skipped=4)`. Its git-status section naturally lists the 88 intentional deletions in the worktree.

Additional import smoke test (full error):

```text
Traceback (most recent call last):
  File "<stdin>", line 5, in <module>
  File "/usr/local/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/tmp/kitelab-cleanup-proof-20260930/scripts/build_n500_report.py", line 49, in <module>
    from reportlab.lib.pagesizes import A4
ModuleNotFoundError: No module named 'reportlab'
```

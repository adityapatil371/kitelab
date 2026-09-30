# Kitelab audit — 2026-09-30

**Headline.** `pytest`: 471 passed, 4 skipped; `check_all.sh`: 475 unittest cases, 4 skipped, page checks passed. The largest unresolved number risk is present-day membership in both markets; the Nifty 500 “PIT” comparator also starts from a present-day screened list. The default board still fills close-derived decisions at that close. The waterfall's honest one-bar turnover lag is in place, but its cap changes held weights daily without charging rebalancing. The 2026-08-31 findings and `FIX_REPORT.md` fixes are baseline context, not findings below.

Scope: `git diff 1fac0f6 HEAD` has 211 paths, including 173 Python files. I scanned changed code for future shifts, centered windows, fitting, execution timing, pandas 3 patterns, and row loss, then ran targeted probes. I did not execute every changed research script: many write fixed names under `output/`, which this audit was instructed not to overwrite. All scratch files are in `output/audit_2026-09-30/`.

## Findings, ranked by effect on believed numbers

### 1. Present-day universes remain in every historical comparison — VERIFIED

**Where:** `scripts/nifty500_list.py:5-23`, `scripts/n500_hold.py:123-145`, `scripts/us_fetch.py:43-68`, `scripts/us_rules.py:62-76`, `kitelab/config.py:398-433`. The Nifty study uses the 2026-09-22 NSE constituent CSV. Its “PIT” basket ranks **pre-cut turnover only among `cfg.merged`, a list screened and retained in 2026**. It is a useful sensitivity comparison, but neither basket is historical index membership. The US list is 144 named companies listed today; `us_universe()` enumerates those cached files. Neither includes delisted failures.

**Measured effect:** the stored 2018 Nifty buy-and-hold rows read `TODAY 17.97934463035944`, `PIT 13.968436712709774`, gap **+4.0109 CAGR points**. Across the 36 strategy rows the reported premium has median **+6.25 points**, positive in **31/36**. These are gaps between two present-day survivor baskets; the true historical membership effect cannot be inferred from them. The 2012 and 2022 buy-and-hold gaps are +1.318 and +6.7589 points. For 2018 the baskets held 480/496 and 500/500 names, with only 276 names overlapping. The US backtest uses 144 current files, of which 143 have bars in 2018; the earliest files begin in 1962 with only 19 names. `tests/test_pointintime.py:1-98` has nine passing tests for pivot confirmation, EMA recursion and level validity; **zero test historical universe membership or the fill cap**.

### 2. Same-close execution remains the board default — VERIFIED

**Where:** `kitelab/backtest.py:77`, `kitelab/entries.py:320-390,431-449`, `kitelab/backtest.py:352-378`. Most entry families use that session's close, range, or volume to decide; `NEXT_OPEN_FILLS = False` enters at that same close. Exit decisions can also use the close at which they are filled. The synthetic gap probe shifted the same signal's entry from `2020-02-26 ₹105` to `2020-02-27 ₹110` by enabling the existing next-open path. Calendar and open-only gap signals can be known earlier; this is a default execution convention problem, not a claim that every signal is late.

**Measured effect:** the saved `wf_lookahead_2026-09-09.csv` has 190 cells for **19 older strategies**: median close-fill minus next-open **−0.285 points**, range **−8.845 to +23.081**, 86 positive. That file does not cover the current 36-row board, so it does not quantify the current headline. `check_all.sh` reports **10,800 current grid cells**, all built under the same default unless a producer overrides it. The effect can have either sign; the fill convention still cannot be achieved after seeing the close.

### 3. The waterfall cap is reapplied to every held position each day — VERIFIED

**Where:** `scripts/waterfall.py:224-244,332-340`. The 2026-09-24 `CAP_LAG=1` change correctly removes same-day turnover from the fill decision. Yet `account()` recomputes each held name's weight from the preceding bar's turnover on **every** day, while `fresh` entry cost is charged once. This models a changing liquidity tilt and free daily resizing, not just an entry fill cap.

**Measured effect:** with seed 0, one family (`mr`), two stops, both markets, freezing each position's cap room at entry moved NSE D0 annual CAGR **0.499→−0.594** (−1.094 points) on `own` and **4.063→1.883** (−2.180) on `atr3`; the matched random E arms moved **−13.186→−15.132** and **0.894→−0.369**. US changes were 0.085–0.233 points. This is a diagnostic alternative, not a full-account replay of actual shares and trades. It establishes that the daily cap behavior changes published waterfall-sized numbers.

### 4. A “random” control fits its firing frequency on future data — VERIFIED

**Where:** `scripts/entry_exit_grid.py:165-194` (`rates`, full-panel median, then `rand`); the same family of control is in `scripts/entry_zoo.py:185-223`. With a fixed seed-0 synthetic panel, truncating at session 350 changed the rate fitted for earlier sessions from **11.380% to 10.236%** and changed **72 of 7,000** pre-cut random firing bits. This is a genuine prefix failure for the random arm. The ranked xrank signal passed the same truncation test with **0 prefix mismatches**. I did not reprice the 72 changed synthetic trades or rebuild the published entry/exit grid, so the effect on its CAGR is unmeasured.

### 5. The waterfall's single random draw is not reproducibly seeded — VERIFIED

**Where:** `scripts/waterfall.py:355-358` uses Python's process-randomized `hash((tag,family,stop_name))`; `scripts/us_rules.py:62` specifies `DRAWS = 1` for the separate random-timing trade control. The waterfall builds **one random-timing book per rule/stop**, not 26 draws. “Median of 26 arms” is a median across 13 rules × 2 stops. `PYTHONHASHSEED=0..4` gives five different, repeatable draws:

| Hash seed | US rule − random median | NSE rule − random median | US/NSE arms ahead of hold |
|---:|---:|---:|---:|
| 0 | 4.099 | 6.705 | 0/26, 0/26 |
| 1 | 4.316 | 4.996 | 0/26, 0/26 |
| 2 | 4.815 | 4.788 | 0/26, 0/26 |
| 3 | 4.850 | 5.542 | 0/26, 0/26 |
| 4 | 4.328 | 5.397 | 0/26, 0/26 |

The median moves **0.751 US** and **1.917 NSE** CAGR points across five seeds. The published +4.39/+5.72 lies inside those ranges; the “0 of 52 beat hold” verdict survives all five. A stable seed should be derived from bytes, not Python `hash()`.

### 6. Future bars can change which historical bars or symbols exist — VERIFIED

**Where:** `kitelab/frames.py:95-139` uses a centered 41-bar rolling median and backfill to delete suspect zero-volume bars; `scripts/data_audit.py:158` repeats that test diagnostically. Appending 25 future traded bars to 10 earlier untraded bars changed the retained prefix from **10 to 0 rows**. This is a future-dependent cleaning decision. In the current 496-name Nifty, 144-name US and 1,000-name board raw files, however, `frames.daily` dropped **0 rows**; measured effect on these published datasets is **zero**.

**Where:** `scripts/waterfall.py:280-307` checks each symbol's maximum single-day move over its **entire available series** before applying an optional start cut. A synthetic stock with earlier closes `[100,101,102]` was eligible; appending one later close `800` made all earlier history ineligible. In the actual seed-0 run, US loaded 144/144; NSE loaded 123/144, with 20 too short and `CRUDEOIL` dropped for a 132,300% move. The actual NSE drop is an obvious non-equity, but the filter is not a causal eligibility rule for historical start dates.

### 7. The corrected gate is narrower than the research search — INFERRED

**Where:** `kitelab/validation.py:1350-1468`, `check_all.sh:58-70`. Section 2 uses a one-sided Bonferroni threshold **0.05/9,936 = 5.03e−6**; **0** pass, while **87** clear an uncorrected 5% bar. `expected_by_chance=496.8` is `9,936×0.05` under an all-null model; independence is not needed for that expectation, but the all-null assumption is. Section 3 computes `n_eff=11.2` from eigenvalues of monthly realized-profit correlations among the currently supplied strategy variants and reports **5** clearing its smaller hurdle. Correlation-based `n_eff` assumes those observed monthly series adequately represent the joint null dependence and does **not** count the many prior rules, settings, universes, stop widths, and research choices seen in git history. Thus “5 cleared” is not a research-wide multiple-testing guarantee. `check_all.sh` prints both values, which is good; they answer different questions.

## Regression and baseline comparison

`python3 -m pytest -q` printed **471 passed, 4 skipped, 25 subtests passed, 0 failed**. `./check_all.sh` exited 0: **475 unittest cases, 4 skipped**, both page self-checks passed, pyflakes reported only `scripts/refresh.py:69` unused `pandas`. Section 4 printed **0 of 36 rows beating buy and hold**. The built payload is dated **2026-09-19 13:32 IST** and contains **10,800** cells.

`FIX_REPORT.md` section 2 is a **192-stock, ₹2,50,000, 1%-risk snapshot**. The current payload has 1,000 names and no matching ₹2,50,000 cells for any of the six old strategy keys; therefore those values **cannot be rerun from the present grid**. This is a missing historical reproduction path, not evidence that the old arithmetic was wrong.

| `FIX_REPORT.md` row | Perfect / realistic CAGR | DD / taken | Trade count / net | Exact current cells |
|---|---:|---:|---:|---:|
| EMA M/W/D 2% | 12.3 / 11.9 | −61.7 / 2,513 | 9,224 / ₹7,745,431 | 0 |
| EMA Q/M/W 2% | 19.3 / 19.1 | −58.0 / 994 | 3,011 / ₹5,006,509 | 0 |
| EMA W/D/H 2% | −7.8 / −20.2 | −73.4 / 4,895 | 18,296 / ₹2,801,977 | 0 |
| ATH Breakout | 12.8 / 10.4 | −23.6 / 780 | 877 / ₹225,049 | 0 |
| Darvas 20/10 | 18.8 / 15.8 | −40.0 / 2,739 | 6,908 / ₹2,154,701 | 0 |
| Darvas 55/20 | 21.8 / 15.3 | −40.4 / 1,600 | 3,197 / ₹1,575,404 | 0 |

The four-mode cost/cap decomposition in that section likewise has no exact current 192-stock cells. Its values remain in `FIX_REPORT.md`; this audit did not rebuild the old universe or its old signal caches.

## Data, holdout, and statistical details

**Rows and NaNs — VERIFIED.** Direct parquet reads and `frames.daily` agree exactly: Nifty 500 **1,700,831→1,700,831**, US **1,558,650→1,558,650**, board **3,791,676→3,791,676**. No Nifty or US close was NaN after loading; direct raw close/ts `dropna` also removed 0. Date ranges: Nifty **2006-01-02..2026-09-24**, US **1962-01-02..2026-09-23**. Nifty 2018 hold deliberately reduces **496 named→480 held** (16 under 250 bars); the 2018 PIT basket is **500→500** and board hold **1,000→997** (3 under 250 bars). `entry_exit_grid.py:148-156` constructs sparse wide panels without forward filling. `entry_edge.py:130` does forward-fill prices but separately tracks traded sessions and last real close. `wf_trail.py:392` uses an inner date join; I did not rerun that publication and cannot quantify its dropped dates.

**Stocks with bars in each year — VERIFIED.** These are present-day members with historical data in that year, **not** that year's membership:

- Nifty 500, 2006–2026: `218,243,253,258,277,284,291,292,294,305,317,336,352,365,377,407,422,439,464,495,496`.
- US, 1962–2026: `19,19,19,19,20,20,20,20,20,20,34,46,46,47,47,47,50,50,77,86,89,92,93,93,97,98,99,100,102,105,108,109,111,111,113,114,119,121,125,127,127,129,129,131,131,133,134,136,138,140,141,141,142,143,143,144,144,144,144,144,144,144,144,144,144`.

**Corrupt-bar screen — VERIFIED.** Using close `<0.2 ×` the preceding 21-bar median with volume 0: **Nifty 0, US 0**. Using `|one-day move|>50%` followed next day by a return to within 20% of the pre-move close: **Nifty 2, US 0**. Both Nifty hits are BAJFINANCE, 2008-12-19 and 2008-12-23, where adjusted OHLC toggles between ₹0.50 and ₹1.00 with nonzero volume. They are suspicious adjusted-price precision, **not verified corrupt trades**. The old VINEETLAB defect and its removal were covered by the August audit and fix report and are not relisted as new.

**Holdout integrity — VERIFIED history, INFERRED implication.** `data/holdout_symbols.json` was introduced by `4044a82` (2026-08-25) and moved/deleted by `ebe1c84` (2026-09-01). `data/keep/accepted.txt` was touched by `2a3e4d2`, `d2f06c2` (both 2026-09-03), and `428460d` (2026-09-08); it is the second screening batch, not an untouched holdout. `data/keep/universe.json` was touched by `2a3e4d2`, `d2f06c2`, `d789a99`, `c99cc44`, `ddc9cc8`, `5b462f9`, `428460d`, `22b816d`, `748a032`, `c5afc1a`, `cb4df24`, `8dab457`, `860b7b1`, `663311b`. On 2026-09-02 (`e99271b`) the 101/399 split was scored; on 2026-09-03 (`a38cf78`) the observed gap was analyzed, and `d2f06c2` merged screened names into one universe. Current results therefore have **no untouched stock holdout**. I found no direct read of `data/keep/accepted.txt` by a current scorer: `scripts/backup_inputs.py` copies it, `scripts/screen_universe.py` produces the source list, and scorers read `config.merged`. Whether any historical choices were informally influenced by viewed holdout outputs cannot be proven from code alone.

**Variance and partial years — VERIFIED code review.** `kitelab/curves.py:115-141` computes account Sharpe with population SD (**ddof 0**, divisor `n`). `scripts/waterfall.py:395,403`, `scripts/wf_risk.py:188-218`, and Pandas rolling/std calls in `scripts/alpha_eval.py:336` use sample SD (**ddof 1** by default); `kitelab/validation.py:819,1193` and `scripts/wf_leverage.py:209-210` spell out ddof 1. Maximum drawdown is a running-peak minimum, **no std/var**; Ulcer index in `kitelab/portfolio.py:383-398` is population RMS of daily drawdowns. `portfolio.run` annualizes first offered entry to last offered exit using actual days/**365.25**, so partial years are fractional years; `wf_risk.cagr_of` does likewise. Waterfall CAGR instead uses number of return rows/**252** sessions, also including a partial final year. A wiped-out account returns `None` for portfolio CAGR, as fixed in `FIX_REPORT.md`.

**pandas 3.0.6 — VERIFIED scan and regression.** No chained slice assignment with `inplace=True`, deprecated `'M'/'Y'/'H'` resample alias, or unqualified `groupby.apply` was found in live source. `kitelab/frames.py:641` uses `"60min"` (harmless); `scripts/fixed_sim.py:402-405` passes `include_groups=False` (correct). `to_period("M")` in entries/frames uses a period code, not the removed resample alias. `kitelab/fetch.py:172,256` concatenates typed empty frames and passes through `with_ts` (harmless by inspection); `scripts/pbo.py:450` filters empty frames first. `fillna`/`ffill` sites in entries, slippage, entry grid and frame cleaning operate on explicit numeric or boolean data; no downcast-sensitive failure appeared in 471 pytest passes. The centered `ffill().bfill()` at `frames.py:127` is a real timing issue (finding 6), not a pandas 3 behavior change. Default string dtype is not currently implicated by an observed failure; I did not execute every CSV ingestion path.

**Hygiene — VERIFIED.** Before and after the audit, git status showed the pre-existing uncommitted deletions `DI+_- VS 20 EMA - HAL.csv`, `poster.png`, and untracked `.ai-runs/`; none were touched. `scripts/stop_sweep.py` logs that it skips **14 of the current 36 board rows** implemented by `entries.py`; its older stop conclusions do not cover those rows. The three `tests/test_resample.py:45,51,56` functions with no direct assertion call `_check()` at line 34, which contains six real assertions per group; they are not inert tests. Many older scripts were deleted in committed history, with no live reference found in README/NEXT_TESTS/scripts/kitelab. The only linter line is the known `scripts/refresh.py:69` unused import.

## Checked and found clean

- `waterfall.py:332-340` does lag turnover by one **symbol bar** by default; `wf_intraday.py:172` likewise shifts its rolling turnover. Reopening the already-fixed 4812ccc same-day cap finding as new would be wrong.
- Across changed live Python, no `shift(-k)` or `rolling(center=True)` appeared outside the two data-cleaning/diagnostic locations above. Forward loops in exit simulations price outcomes after entry, not earlier signals. The xrank 252-bar trailing return and cross-sectional same-date rank (`kitelab/entries.py:244-246`, `scripts/xrank_gate.py:109`) passed the prefix test (0 mismatches).
- `tests/test_pointintime.py` passed all 9 tests; the full suite passed. `check_all.sh` recomputed 0/36 rows ahead of buy and hold. Five independently seeded waterfall reruns retained 0/26 ahead of hold in each market.
- No raw-to-`frames.daily` row loss or NaN closes in the measured Nifty/US/main board paths. No VINEETLAB-shaped zero-volume low bar remains in the tested Nifty/US universes.

## Command runtime ledger

The table lists every **instrumented** command, including failed probes. Exact argv and raw stdout/stderr are in `output/audit_2026-09-30/commands.jsonl` and matching `.log` files. Short exploratory `sed`, `rg`, `cat`, `git status`, `head`, and `tail` reads were also run through the shell during investigation; they were not timed by the runner, so their individual runtimes are unavailable. That is a limit of this command ledger, not a claim that they were not run.

| # | Command / probe | Exit | Runtime (s) |
|---:|---|---:|---:|
| 1 | `baseline (exact argv in commands.jsonl)` | 0 | 0.007 |
| 2 | `claims (exact argv in commands.jsonl)` | 0 | 0.006 |
| 3 | `repo (exact argv in commands.jsonl)` | 0 | 0.027 |
| 4 | `bash -lc python3 -m pytest -q` | 0 | 10.523 |
| 5 | `bash -lc ./check_all.sh` | 0 | 12.221 |
| 6 | `suspects (exact argv in commands.jsonl)` | 0 | 0.008 |
| 7 | `universe (exact argv in commands.jsonl)` | 0 | 0.009 |
| 8 | `data_paths (exact argv in commands.jsonl)` | 0 | 0.010 |
| 9 | `history (exact argv in commands.jsonl)` | 0 | 0.248 |
| 10 | `python3 output/audit_2026-09-30/data_probe.py` | 0 | 3.563 |
| 11 | `python3 output/audit_2026-09-30/timing_probe.py` | 1 | 0.309 |
| 12 | `env PYTHONPATH=. python3 output/audit_2026-09-30/timing_probe.py` | 0 | 0.651 |
| 13 | `reference (exact argv in commands.jsonl)` | 0 | 0.120 |
| 14 | `reference_compare (exact argv in commands.jsonl)` | 1 | 0.114 |
| 15 | `reference_compare (exact argv in commands.jsonl)` | 0 | 0.112 |
| 16 | `pandas_scan (exact argv in commands.jsonl)` | 0 | 0.005 |
| 17 | `test_hygiene (exact argv in commands.jsonl)` | 2 | 0.004 |
| 18 | `numbers (exact argv in commands.jsonl)` | 2 | 0.004 |
| 19 | `holdout_history (exact argv in commands.jsonl)` | 0 | 0.290 |
| 20 | `python3 output/audit_2026-09-30/misc_probe.py` | 0 | 0.430 |
| 21 | `env PYTHONPATH=. PYTHONHASHSEED=0 python3 output/audit_2026-09-30/seed_probe.py 0` | 0 | 313.933 |
| 22 | `env PYTHONPATH=. python3 output/audit_2026-09-30/rows_probe.py` | 0 | 10.157 |
| 23 | `board_data (exact argv in commands.jsonl)` | 0 | 2.266 |
| 24 | `future_scan (exact argv in commands.jsonl)` | 0 | 0.005 |
| 25 | `fit_scan (exact argv in commands.jsonl)` | 0 | 0.006 |
| 26 | `pandas_specific (exact argv in commands.jsonl)` | 0 | 0.009 |
| 27 | `fit_scan (exact argv in commands.jsonl)` | 0 | 0.017 |
| 28 | `future_scan (exact argv in commands.jsonl)` | 0 | 0.035 |
| 29 | `pandas_specific (exact argv in commands.jsonl)` | 0 | 0.030 |
| 30 | `anomalies (exact argv in commands.jsonl)` | 0 | 0.504 |
| 31 | `env PYTHONPATH=. PYTHONHASHSEED=1 python3 output/audit_2026-09-30/seed_probe.py 1` | 0 | 309.687 |
| 32 | `env PYTHONPATH=. PYTHONHASHSEED=2 python3 output/audit_2026-09-30/seed_probe.py 2` | 0 | 309.905 |
| 33 | `env PYTHONPATH=. PYTHONHASHSEED=0 python3 output/audit_2026-09-30/cap_probe.py` | 0 | 37.872 |
| 34 | `env PYTHONPATH=. python3 output/audit_2026-09-30/timing_probe.py` | 0 | 0.690 |
| 35 | `scope (exact argv in commands.jsonl)` | 0 | 0.274 |
| 36 | `env PYTHONPATH=. python3 output/audit_2026-09-30/timing_probe.py` | 0 | 0.739 |
| 37 | `python3 -m pytest -q tests/test_pointintime.py` | 0 | 0.554 |
| 38 | `env PYTHONPATH=. PYTHONHASHSEED=3 python3 output/audit_2026-09-30/seed_probe.py 3` | 0 | 308.100 |
| 39 | `env PYTHONPATH=. PYTHONHASHSEED=4 python3 output/audit_2026-09-30/seed_probe.py 4` | 0 | 308.536 |
| 40 | `env PYTHONPATH=. python3 output/audit_2026-09-30/rows_probe.py` | 0 | 18.444 |
| 41 | `holdout_files (exact argv in commands.jsonl)` | 0 | 0.364 |
| 42 | `python3 output/audit_2026-09-30/write_report.py` | 0 | 0.023 |

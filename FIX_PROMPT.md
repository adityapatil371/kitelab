# Fix brief: kitelab

You are fixing the defects found by an audit of `/Users/adityapatil/kitelab`.

**Read these two files in the repo root before doing anything:**

- `AUDIT_REPORT.md` — the findings, with evidence and measured impact. This is
  your work list. Do not re-derive it; do verify anything marked *inferred*.
- `AUDIT_PROMPT.md` — the project context the auditor was given: what this
  codebase is, who it is for, how to run it, the architecture, the invariants,
  and the environment constraints. All of that still applies to you.

This brief adds only what a *fixer* needs that an auditor did not: sequencing,
the verification protocol, which changes are the owner's decision rather than
yours, and what not to touch.

---

## 1. The one thing that matters most

Aditya is a beginner who **cannot evaluate this code himself**. He teaches from
these numbers in a class. A plausible-but-wrong number is far worse than a crash.

So: **every number that changes must be changed on purpose, measured, and
explained.** A fix that silently moves three unrelated figures is worse than no
fix. If you cannot attribute a number change to a specific finding you were
fixing, stop and investigate before continuing.

---

## 2. Ground rules

1. **One finding per commit.** Never batch. The commit message states which
   numbers moved, from what to what, and why.
2. **Never adjust code to make a number match an expectation.** If a number does
   not reproduce, that is a finding to report, not a target to hit.
3. **State a prediction before each verification run**, then report whether it
   held. Wrong predictions are useful and must be reported as wrong.
4. **Mark every claim verified or assumed.** A number states the command that
   produced it.
5. **Do not refactor beyond the finding.** The audit lists eight duplicate stat
   implementations (D1) and superseded scripts (D3); consolidating them is a
   redesign, not a fix. See §6 — most of that needs a decision first.
6. If a fix requires a judgement the audit did not settle, **ask the owner**
   rather than picking for him. §6 lists the ones I already know about.

---

## 3. Order of work

### Phase 0 — before you touch any code

**B1 first, and it is not a code task.** The Bitcoin manual-validation sheet
(`output/Aditya P bitcoin - validated.xlsx`) is gone and `output/` is gitignored,
so git has no copy. Every other finding is recoverable by running code; this one
is not. Tell the owner to search email, Downloads, and class uploads **now**.
Then make `scripts/master_report.py:308` fail loudly instead of silently omitting
the sheet.

**Then settle the audit's side effects.** Running every script regenerated ~20
workbooks in `output/`, including several deleted in the 2026-08-28 cleanup, and
overwrote `EMA Timeframe Comparison.xlsx` — the copy whose 231 formula values had
been externally recalculated. Ask the owner which resurrected workbooks to delete
before you add more churn to that directory.

**Then capture a baseline** (see §4). You cannot safely change anything until you
can prove what moved.

### Phase 1 — fixes that change no numbers

Safe, mechanical, do them together at the start so later diffs are clean:

- **D3** dead code: `with_slippage()` (`scripts/factor_analysis.py:41`),
  `levels.describe()` (`kitelab/levels.py:125`), the stale `__pycache__` entry.
  Deleting *scripts* is a decision — see §6.
- **D4** frozen prose numbers: `drawdown_report.py:213-231`,
  `factor_analysis.py:150-152`, `tf_compare.py:271-273`, `full_report.py:297,672`,
  `darvas_breadth.py` docstring. Either compute them or date and label them as
  historical. The audit names which ones are already fine.
- **C4** docstring vs implementation on risk sizing — fix the *doc*, not the code
  (the convention is defensible; only the description is wrong). Confirm by
  execution first, it is marked *inferred*.
- **C3** guard `apply_spread()` against scale-out lists — raise rather than
  silently mis-price. Same for `report.order()`'s unconditional
  `net_profit_best` read.
- **A6** blank formula cells: `tf_compare.py`, `showcase.py`, `dd_proof.py` write
  formulas with no cached value, which render blank in the owner's viewer
  (LibreOffice is not installed). The zip post-processor exists in
  `band_sweep.py:291` — extract it to `kitelab/report.py` and call it from all
  five writers. Also fix the 12 formulas band_sweep's injector currently misses.

Verify: after Phase 1, the full baseline (§4) must be **byte-identical**. If
anything moved, you broke something.

### Phase 2 — display-only

- **A2** the wiped-account bug. `portfolio.run` returns `cagr_pct = 0.0` whenever
  growth ≤ 0 (`kitelab/portfolio.py:242`), so an account with final equity of
  −₹7 renders "+0.0%/yr". 157 grid cells affected; 55 of 192 Monte Carlo
  distributions contain sentinel baskets that sort *above* merely-negative ones
  and escape the "negative %" stat.

  The simulation is correct — this is a return-value and rendering decision.
  Note `web/dashboard.html:566/579` uses `?? 0` so a *missing* grid key also
  becomes 0: zero currently has three meanings (wiped, missing, genuinely flat).
  Fix all three. See §6 — the sentinel choice is the owner's.

### Phase 3 — fixes that change published numbers

**One at a time. Measure each in isolation. Rebuild once at the end, not after
each.**

- **A1** the six corrupt VINEETLAB bars (OHLC all 7.60, volume 0). Extend
  `frames.sanitise()` to catch zero-volume bars whose price is wildly out of line
  with neighbours — note the audit's finding that five sit at *consecutive* rows,
  which is exactly what defeated the earlier detector, so test against runs, not
  just isolated bars. Then re-simulate. The audit's impact estimate (Q/M/W
  18.06 → 18.21%) came from *excluding* the affected trades; a real repair will
  differ, because those trades would have exited later at real prices. Report
  both numbers.
- **A4** W/D/H trade lists billed intraday-aware while the EMA lists are billed
  delivery-only — ₹291,292 on 3,145 same-session trades. Pick one convention,
  apply it everywhere, say which and why. Account-level grid CAGRs are already
  consistent; this is trade-level only.
- **A3** the "realistic fills" toggle bundles the 1% participation cap (a
  beneficial *sizing rule*) with the costs, which is why realistic beats perfect
  on Q/M/W in 110 of 1,536 cell-pairs. `capped_shares` is gated on
  `slippage.ENABLED` (`kitelab/slippage.py:213`) so the cap cannot currently be
  tested alone. Ungate it, then either put the cap in both arms or surface it as
  its own lever. See §6.
- **A5** Q/M/W weekly bars stamped at the week's *first* session.
  **Verify before fixing** — the mechanism (cash moving up to four days before
  the price it uses existed) is serious but the claimed headline impact is only
  0.03 points. Demonstrate it on one named trade first. Two sub-issues: the
  higher-timeframe lookup (`scripts/tf_compare.py:89-92`) and the trade stamps
  (:155-158).
- **C2** `slippage.profile()` fills early volatility NaNs with the median of the
  *entire* series (`kitelab/slippage.py:94`) — lookahead, in the module whose
  job is honesty about costs. The ADV fallback beside it is already correct
  (expanding median, shifted); copy that pattern.
- **C1** cash can go negative: `run()` sizes by `cash / entry_price` then applies
  impact before debiting (`kitelab/portfolio.py:191-198` vs :221-227). Measured
  minimum −₹1,642 — economically trivial, but it is also what lets a dying
  account settle below zero and produce the −₹7 finals behind A2.

### Phase 4 — rebuild and re-verify

`./.venv/bin/python -m scripts.dashboard_data` (~45-70 min with warm caches;
**delete the affected `data/signal_cache/*.pkl` first** if a Phase-3 fix changed
what `simulate()` produces, or you will rebuild on stale trades — this is the
easiest way to waste an hour and publish wrong numbers).

Then rerun every account-based workbook: `slippage_report`, `factor_analysis`,
`darvas_test`, `darvas_breadth`, `breakout_exit_test`, `smallcap_test`,
`full_report`, `master_report`, `drawdown_report`, `dd_proof`, `assets_report`.
The audit found `factor_analysis.py` had been *silently broken for hours* by an
unpropagated key-format change — assume any script you have not run is broken.

---

## 4. Verification protocol

**Before you change anything**, write a script that captures a baseline and keep
it for the whole job:

- `portfolio.run` output (CAGR, max drawdown, final equity, taken count) for all
  six reference rows at perfect and realistic fills, from the cached pickles.
- Trade counts and summed net per strategy from every pickle in
  `data/signal_cache/`.
- A hash of the full `data/dashboard.json` grid.

Current reference values, all at 199 stocks, 1% risk, ₹2,50,000
(`dashboard.json` built 2026-08-31 16:53, `tie_break: liquidity`):

| Strategy | Perfect | Realistic |
|---|---|---|
| EMA M/W/D 2% | 12.60%, −56.8%, 2,588 taken | 10.00%, −60.7% |
| EMA Q/M/W 2% | 18.10%, −61.0%, 1,044 | 19.00%, −56.3% |
| EMA W/D/H 2% | −7.80%, −74.9% | −17.00%, −88.7% |
| ATH Breakout | 11.70%, −25.2%, 800 | 10.60%, −24.9% |
| Darvas 20/10 | 18.70%, −42.6%, 2,790 | 15.80%, −38.3% |
| Darvas 55/20 | 22.50%, −40.7% | 15.40%, −39.3% |

Trade counts: EMA 9,613 · Q/M/W 3,151 · W/D/H 19,073 · Breakout 2,083 ·
Darvas 20/10 7,206 · Darvas 55/20 3,328 · EMA half_be 10,202.

**Invariants that must survive every fix** (from `AUDIT_PROMPT.md` §4, all
currently verified clean — do not break them):

- Defaults reproduce history: `slippage.ENABLED = False`,
  `NEXT_OPEN_FILLS = False`, `scale_r = 1.0`, `exit_rule = "trail"`,
  `TIE_BREAK = "liquidity"`.
- `charges()` in `backtest.py` stays the only fee model.
- `run()` and `daily_curve()` agree to ₹0.00 on final equity.
- No lookahead anywhere (C2 and A5 are the two known breaches — fixing them
  must not introduce others).
- Scale-out stays trade-level; no `_half` list ever reaches `portfolio.run`.
- The dashboard stays a pure viewer.

Re-run the dashboard sweep after any UI change: drive all five pages × every
strategy × every slicer and grep the DOM for `NaN`/`undefined`. The audit's run
was clean across ~1,400 combinations; keep it that way.

---

## 5. Environment

Everything in `AUDIT_PROMPT.md` §9 applies. The ones that will bite you:

- **You cannot bind a port.** The owner has a dashboard server on
  `http://localhost:8765`; use it, you cannot start your own.
- **You cannot push.** Commit; the owner pushes.
- **You cannot list or kill processes** (`ps`, `pgrep`, `pkill` blocked).
- Use `./.venv/bin/python -m scripts.<name>`, never bare `python`.
- Node for JS syntax checks: `/opt/homebrew/bin/node --check`.
- A pre-bash hook blocks `.env`, `rm -rf`, filenames containing "key", chained
  `sleep`s, and anything under `.claude/`.

---

## 6. Decisions that are the owner's, not yours

Ask; do not choose silently. Batch these into one question early so you are not
blocked mid-job.

1. **A2 sentinel** — what should a wiped account display? `−100%`, `"wiped"`, or
   `null`? And should a *missing* grid key render differently from a real zero?
2. **A3 bundling** — put the participation cap in both arms of the fills toggle,
   or surface "size-capped sizing" as its own slicer? The second is more honest
   and more work.
3. **A4 fee convention** — bill same-session trades intraday-aware everywhere, or
   delivery-only everywhere? This changes W/D/H trade-level numbers either way.
4. **D3 script deletion** — `level_trades`, `trailing_trades`, `scaleout_test`,
   `wide_test`, `ema_trades` are superseded, but `ema_trades` is the sole caller
   of `backtest.recent_trades` and `verify_against_screener`, and `scaleout_test`
   alone writes the assets scale-out workbook. Deleting orphans real code.
5. **D1 stat consolidation** — eight implementations disagree (win rate 29.41%
   net vs 30.03% gross; PF 1.94 vs 2.11). Consolidating means picking a
   convention and *republishing numbers*. Probably a separate job.
6. **D5 `levels.json.bak`** — it is not a copy; HAL and HINDZINC entries differ.
   The drawing page is deleted, so decide which is canonical before deleting
   either.
7. **Known issues #1 and #2 from `AUDIT_PROMPT.md`** — the 15 open-but-counted
   breakout trades, and the 1,174 overlapping breakout trades (the audit
   corrects this from 1,169). Both have been awaiting his decision for a while.

---

## 7. Do not

- Do not touch `data/*.parquet` — re-downloadable but slow, and the owner's Kite
  session may not be live.
- Do not delete `data/levels.json` or `data/nse_delisted.csv` — both are
  unreproducible (the level-drawing page is gone; NSE blocks automation).
- Do not "improve" strategy logic. Fix defects; do not change what a rule does.
- Do not change `TIE_BREAK` away from `"liquidity"` — it was chosen deliberately
  and on evidence (it wins under realistic fills), and `None` exists only to
  reproduce pre-2026-08-31 numbers.
- Do not regenerate workbooks into `output/` until Phase 0 is settled.
- Do not commit `data/dashboard.json` (47 MB, gitignored) or anything in
  `output/`.

---

## 8. Deliverable

A short report at the end covering:

- **What was fixed**, with the commit for each.
- **Every number that moved**, before → after, attributed to the finding that
  moved it. Anything that moved and cannot be attributed is a new finding —
  say so plainly.
- **What was deferred**, and why — including anything waiting on §6.
- **Predictions you made and whether they held**, wrong ones included.

`AUDIT_REPORT.md` is currently untracked. Commit it early so the findings are in
history alongside the fixes.

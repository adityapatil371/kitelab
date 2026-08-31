# Audit brief: kitelab

You are auditing a personal backtesting codebase at `/Users/adityapatil/kitelab`.
Your goal is to find **redundancies, inconsistencies, anti-synergistic design, and
outright mistakes**. Nothing is off limits. Assume the code was written fast by a
competent but unsupervised assistant over several sessions, with real findings
mixed into real sloppiness. Your job is to separate them.

Be adversarial. The single most valuable thing you can produce is a defect that
changes a number the owner believes.

---

## 1. What this project is

Aditya is a beginner in a ~10-person trading class. He has never placed a real
trade. Classmates hand-backtest strategies on TradingView; this repo is the
machine version that validates and extends that work. It runs on his paid Zerodha
Kite Connect subscription (Indian equities) plus some free Binance data.

It fetches OHLCV candles, simulates several trading strategies over ~199 NSE
stocks from 2006 to 2026, and produces Excel reports plus a local web dashboard.

**He cannot evaluate the code himself.** He trusts the numbers it prints. That
raises the stakes on silent errors enormously: a plausible-but-wrong number is
far worse here than a crash.

---

## 2. How to run things

```bash
cd /Users/adityapatil/kitelab
./.venv/bin/python -m scripts.<name>          # never bare `python`
```

Python 3.11.5 in `./.venv` — pandas 3.0.5, numpy, openpyxl, pyarrow, kiteconnect.

- The dashboard server may already be running on `http://localhost:8765`.
  Start it with `./.venv/bin/python -m scripts.dashboard`.
- `python -m scripts.dashboard_data` rebuilds `data/dashboard.json`
  (~45–70 min with warm caches; the 44 pickles in `data/signal_cache/` are the
  warm part and are regenerable but slow — the hourly W/D/H band sweeps and the
  30-minute breakout runs dominate).
- Full strategy passes over 199 stocks: EMA ~90s, Darvas ~60s, breakout ~5 min.
  Budget accordingly; prefer the cached pickles when you only need trades.

Data lives in `data/*.parquet` (gitignored, ~400 files, re-downloadable from
Kite). `data/dashboard.json` is 47 MB and gitignored.

---

## 3. Architecture

### `kitelab/` — the library (2,900 lines)

| File | Lines | Role |
|---|---|---|
| `strategies.py` | 439 | Support-bounce and ATH-breakout strategies; `_build_trade` is the shared trade constructor for both |
| `backtest.py` | 398 | The EMA-stack strategy (`simulate`), plus `charges()` — the **single** source of Zerodha fee maths |
| `portfolio.py` | 263 | The one-account simulator: `run()` and `daily_curve()`. Re-sizes and re-prices every trade itself |
| `frames.py` | 233 | Parquet loading, resampling to any timeframe, and `sanitise()` for corrupt bars |
| `screener.py` | 231 | Expression evaluator used to cross-check the fast EMA path |
| `slippage.py` | 220 | Spread ladder, market impact, participation cap, liquidity lookup |
| `fetch.py` / `auth.py` | 267 | Kite API |
| `report.py` | 163 | Excel writers, `save()`, the class's 9-metric summary block |
| `darvas.py` | 156 | Donchian 20/10 channel strategy (newest) |
| `levels.py` / `trailing.py` | 268 | Pivot detection; swing-low trailing stops and the exit resolver |
| `indicators.py` / `sizing.py` / `config.py` | 196 | EMA; position sizing; config |
| `dashboard_server.py` | 65 | Serves `web/dashboard.html` and the JSON |

### `scripts/` — 34 entry points (6,759 lines)

Notable: `dashboard_data.py` (580, builds the whole JSON), `full_report.py` (707),
`tf_compare.py` (440), `drawdown_report.py`, `master_report.py`, `band_sweep.py`,
`slippage_report.py`, `factor_analysis.py`, `smallcap_test.py`, `darvas_test.py`,
`darvas_breadth.py`, `breakout_exit_test.py`, `scaleout_r_test.py`,
`survivorship_test.py`.

### `web/dashboard.html` — 1,094 lines

Single file: HTML + CSS + vanilla JS, no build step, no dependencies. A **pure
viewer** over `data/dashboard.json` — nothing is simulated in the browser. Five
pages: Portfolio, Individual stocks, Scale-out, How many stocks, Futures & assets.
Syntax-check with `/opt/homebrew/bin/node --check` after extracting the `<script>`
block.

---

## 4. Invariants that are supposed to hold

Check each. A violation is a finding.

1. **No lookahead, anywhere.** Higher-timeframe EMAs use the *forming* bar, not
   the completed one (`backtest.ema_stack_signal`). Darvas channels exclude the
   current candle. Pivot lows are only usable `PIVOT_SPAN` bars after they form.
   Trailing ADV is shifted one session. Verify these rather than trusting the
   comments — the comments are confident and were written by the same process
   that wrote the code.
2. **`charges()` in `backtest.py` is the only fee model.** Nothing should
   reimplement Zerodha costs.
3. **Trade dicts are one shape.** `backtest.simulate`, `strategies._build_trade`
   and `darvas.simulate` all produce dicts that `portfolio.run`, `report.py` and
   `dashboard_data` consume interchangeably. Divergence between them is a bug
   class worth hunting: check every key each consumer reads actually exists in
   every producer.
4. **Defaults reproduce history.** `slippage.ENABLED = False`,
   `backtest.NEXT_OPEN_FILLS = False`, `scale_r = 1.0`, `exit_rule = "trail"`
   must leave results byte-identical to the cached pickles.
5. **Stops are checked at closes** for the EMA stacks (class convention);
   the breakout keeps intrabar stops because its buy-stop entry is inherently
   intrabar. Darvas is close-based with an `intrabar=True` option.
6. **Drawdown is daily mark-to-market**, each dip divided by the peak standing at
   that moment. `legacy_*` keys exist only for reconciliation and must not be
   quoted anywhere.
7. **Scale-out results are trade-level only.** `portfolio.run` prices a trade as
   `shares × one exit price`; a scale-out has two exits and the banked leg never
   reaches the trade record. Any account-level scale-out number is wrong.
8. **The dashboard is a pure viewer.** No computation in the browser.

---

## 5. Reference numbers that must reproduce

Current `data/dashboard.json` (built 2026-08-31 16:53, `tie_break: liquidity`,
3,072 grid entries). All at all-199 stocks, 1% risk, ₹2,50,000:

| Strategy | Perfect fills | Realistic fills |
|---|---|---|
| EMA M/W/D, 2% band | 12.60% CAGR, −56.8% DD, 2,588 taken | 10.00%, −60.7% |
| EMA Q/M/W, 2% band | 18.10%, −61.0% | **19.00%, −56.3%** |
| EMA W/D/H, 2% band | −7.80%, −74.9% | −17.00%, −88.7% |
| ATH Breakout | 11.70%, −25.2%, 800 taken | 10.60%, −24.9% |
| Darvas 20/10 | 18.70%, −42.6%, 2,790 taken | 15.80%, −38.3% |
| Darvas 55/20 | 22.50%, −40.7% | 15.40%, −39.3% |

Trade counts (from the pickles): EMA 9,613 · Darvas 20/10 7,206 · Breakout 2,083
· EMA half 9,613 · EMA half_be 10,202.

**If any of these fail to reproduce, that is the finding.** Report which number
and by how much. Do not adjust code until it matches.

---

## 6. Already-known issues — do not report these as discoveries

Report them only if you find the diagnosis was *wrong*, or find a consequence
that was missed.

1. **15 breakout trades are still open** yet counted as closed with a
   mark-to-market exit. EMA drops unfinished trades; breakout does not.
   Inconsistent, unfixed, owner has been told.
2. **1,169 of 2,083 breakout trades (56%) overlap** another open trade in the
   same stock, so trade-sum views double-count the same rally. The one-account
   view is honest (one position per stock). `strategies.drop_overlaps` exists but
   is not applied. This is a strategy choice awaiting the owner's decision.
3. **`data/nse_delisted.csv`** lists 455 NSE delistings but survivorship bias is
   *not* corrected; a stress test measured the effect at ~0.2 CAGR points.
   BATLIBOI appears both in the delisting list and the live 199.
4. **One corrupt bar**: VINEETLAB 2018-01-30, ₹7.60 with zero volume amid
   ~₹1,900 prices. Costs the reference account exactly ₹0 (those trades are never
   taken). A universe-wide scan found no others of this shape.
5. **Kite has no fundamentals**, so there is no market cap; liquidity (median
   daily traded value) is used as the size proxy in `smallcap_test.py`.
6. **The "10 random stocks" universe is one basket** chosen as the median
   performer *for EMA M/W/D*. It is not neutral across strategies. Flagged in the
   UI, not fixed.
7. **`data/levels.json`** holds 38 hand-drawn support/resistance levels that are
   unreproducible; the page that drew them was deleted.

---

## 7. Leads worth pulling — these are suspicions, not conclusions

Verify or dismiss each. Do not assume any of them is real.

- **Dead code.** `with_slippage()` in `scripts/factor_analysis.py` is defined and
  no longer called. Sweep for others — several scripts predate their successors
  (`ema_trades.py`, `level_trades.py`, `trailing_trades.py`, `scaleout_test.py`
  vs the newer `scaleout_r_test.py`, `wide_test.py`, `showcase.py`).
- **Duplicated statistics.** At least eight files define their own
  `summarise` / `trade_stats` / `stats` / `tf_summarise`: `kitelab/backtest.py`,
  `kitelab/report.py`, `scripts/band_compare.py`, `scripts/breakout_exit_test.py`,
  `scripts/darvas_test.py`, `scripts/dashboard_data.py`,
  `scripts/scaleout_r_test.py`, `scripts/tf_compare.py`. Do they agree? Where
  they differ, is the difference intentional? Win rate, profit factor and
  expectancy in particular — check whether `net > 0` vs `>= 0` and gross-vs-net
  conventions are consistent, because a silent divergence would make two sheets
  disagree about the same trades.
- **The Q/M/W anomaly.** Realistic fills (19.00%) score *higher* than perfect
  fills (18.10%). Adding costs should not improve an account. The participation
  cap is bundled into "realistic" and is known to help by changing position
  sizes — but this has never been decomposed and may be masking a real bug.
  **This is the highest-value lead in this list.**
- **`portfolio.run` re-derives everything.** It re-sizes positions from
  `entry_price − stop` and re-prices cash from `entry_price`/`exit_price`,
  ignoring the trade's own `shares` and any cost fields. Check that nothing
  computed upstream is silently discarded, and that `daily_curve` and `run`
  cannot disagree about the same account.
- **`scripts/` staleness.** Nine scripts produce account-based workbooks. Five
  were rerun after a recent engine change (`slippage_report`, `factor_analysis`,
  `darvas_test`, `darvas_breadth`, `breakout_exit_test`); four were **not**:
  `full_report.py`, `master_report.py`, `drawdown_report.py`, `dd_proof.py`,
  `assets_report.py`. `factor_analysis.py` was found *broken* — it had been
  dead for hours because a grid-key format change was never propagated. **Run
  every script in `scripts/` and see which ones still work.** That alone may be
  the most productive hour of this audit.
- **Stray files**: `data/levels.json.bak`, three `instruments_*.parquet`
  snapshots from different dates.
- **`web/dashboard.html`** is 1,094 lines of untested JS. Render logic assumes
  the shape of `dashboard.json` in many places; a key that is missing for one
  strategy silently renders `undefined` or `NaN` rather than failing. Drive the
  live page (all five pages × every strategy × every slicer) and grep the DOM for
  `NaN`/`undefined`.
- **Hardcoded constants.** `factor_analysis.py` hardcodes a data-quality row
  (5.3% → 11.9%) from a historical measurement that can no longer be reproduced,
  because the data has since been repaired. Look for other frozen numbers
  presented as if live.

---

## 8. How the owner expects work to be done

These are his standing rules. Follow them in your audit and hold the code to
them too:

1. **Mark every claim verified or assumed.** A number states the command that
   produced it, or it is not stated.
2. **Read a tool's full output, not its summary line.** "Clean" is a claim, not
   proof.
3. **A number that does not reproduce is a finding.** Report which one and by how
   much. **Never adjust code until it matches.**
4. State predictions *before* running a test, then report whether they held.
   Wrong predictions are valuable and must be reported as wrong, not quietly
   dropped.

Do not fix anything that changes a published number without saying so
explicitly and separately — several numbers are load-bearing for a class he is
teaching from.

---

## 9. Environment constraints you will hit

- **Cannot bind ports.** `PermissionError` on `socket.bind`. To see the
  dashboard, use the server the owner already has running on port 8765, or drive
  it with browser tooling; you cannot start your own.
- **No network TLS** for most hosts: `git push`, `curl`, and web fetches of
  nseindia.com fail. The owner pushes; you commit.
- **Cannot list or kill processes** (`ps`, `pgrep`, `pkill` all blocked).
- A pre-bash hook blocks: `.env` files, `rm -rf`, filenames containing "key",
  chained `sleep`s, and anything under `.claude/`.
- LibreOffice is not installed, so Excel formulas cannot be recalculated
  externally — openpyxl writes `<f>…</f><v/>` with an empty cached value, which
  renders blank in his viewer. Two scripts (`band_sweep.py`, `band_compare.py`)
  post-process the xlsx zip to inject computed values. Check whether workbooks
  written since then need the same treatment.
- Node is at `/opt/homebrew/bin/node`.

---

## 10. What to produce

A single prioritised report. For each finding:

- **What** — one sentence.
- **Where** — `file:line`.
- **Evidence** — the command you ran and its actual output. Not reasoning alone.
- **Impact** — does it change a number the owner believes? Which one, by how
  much? Or is it cosmetic/maintenance?
- **Confidence** — verified by execution, or inferred from reading.

Order by impact, not by discovery order. Put anything that changes a published
result at the top, clearly separated from cleanliness issues.

Explicitly list what you checked and found **clean** — that is as useful as the
defects, and it stops the same ground being re-covered next time.

Do not refactor. Do not "improve" code as you go. Audit first; the owner decides
what gets changed.

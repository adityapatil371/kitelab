# The Kitelab Audit

Adversarial code & numbers audit of `/Users/adityapatil/kitelab` — 2026-08-31.

Every claim below is marked **verified** (a command was run and its output is quoted)
or **inferred** (from reading code). Baseline: `data/dashboard.json` built
2026-08-31 16:53, `tie_break: liquidity`, 3,072 grid cells. All six reference rows
in the brief reproduce from the cached pickles at defaults. 30 of 34 scripts
executed; zero code crashes.

**Headline:** two findings change numbers currently on the dashboard, both
quantified — a −₹43,200 fake loss inside the Q/M/W reference account, and 157 grid
cells where a wiped-out account (−100% return) displays as "+0.0%/yr".

---

## A · Findings that change numbers you believe

### A1 — VINEETLAB has six corrupt ₹7.60 bars, not one, and the Q/M/W reference account trades into one

*Changes a published number · verified by execution*

- **What.** Known issue #4 says one corrupt bar, costing the reference account
  exactly ₹0. Both halves are wrong: there are **six** identical bars (OHLC all
  7.60, volume 0, on the 30th of Jan/May/Jul/Aug/Oct/Nov 2018), and the Q/M/W
  reference account (all 199, 1%, ₹2,50,000) buys 27 shares at ₹1,605.12 on
  2018-05-21 and "sells" them into the fake bar at ₹7.60 — a manufactured
  **−₹43,200**.
- **Where.** `data/VINEETLAB_day.parquet` rows 2943, 3015–3019. `frames.sanitise()`
  (`kitelab/frames.py:69`) only repairs *non-positive* prices, so 7.60 passes.
- **Evidence.** Rolling-median scan over all 199 `*_day.parquet`
  (close < 0.2 × 21-day median, volume 0) → exactly these 6 bars, no other stock.
  Fake-exit trades in the signal lists: EMA one (net −8,861), QMW two (−12,936
  summed), Darvas 20/10 one (−6,334), Breakout none.
  `portfolio.run(QMW_199.pkl, 250000, 0.01)` → the 2018-05-21 trade is in `taken`,
  net −43,200.
- **Impact.** Q/M/W headline: perfect 18.06 → **18.21%**, realistic 19.00 →
  **19.07%** with the two fake-exit trades excluded (drawdowns unchanged).
  Trade-level stats for EMA / Q/M/W / Darvas on every page that includes VINEETLAB
  carry the fake losses. A proper fix (repair the bars, re-simulate) will differ
  slightly from this exclusion estimate — the trades would have exited later at
  real prices.

### A2 — A wiped-out account displays as "+0.0%/yr"

*Changes displayed numbers · verified by execution*

- **What.** `portfolio.run` returns `cagr_pct = 0.0` whenever growth ≤ 0. A
  destroyed account renders as break-even. **157 grid cells** have `ret: -100`
  and negative final equity but display CAGR 0.0 (e.g. `wdh|0|all|2|500000|1`:
  final **−₹7**, shown "+0.0%/yr"). In the 75-basket Monte Carlo, **55 of 192**
  distributions contain sentinel baskets: a wiped basket sorts *above* every
  merely-negative one and is excluded from the "negative %" stat.
- **Where.** `kitelab/portfolio.py:242` (`else 0.0`); compounded by
  `web/dashboard.html:524` (0.0 renders neutral, not "bad") and :566/:579
  (`?? 0` also maps a *missing* grid key to 0 — zero now has three meanings).
- **Evidence.** Grid scan of dashboard.json; corrected examples: `wdh|0|2|0`
  displayed median −35.5 / 61% negative → truth **−58.9 / 100% negative**
  (29 of 75 baskets wiped); `wdh|0|1|0` 76% → 100%. Independently surfaced by
  `scripts.portfolio`'s own printout: "final −4, return −100%, CAGR 0.0%".
- **Impact.** Default cells (2% band, 1% risk) are unaffected; one slicer click
  away (W/D/H, 2% risk, 0–1% bands, realistic) the page materially understates
  ruin. The fix is a display/return-value decision — no simulation is wrong.

### A3 — The Q/M/W anomaly decomposed: "Realistic fills" bundles a sizing improvement with costs

*Misleading comparison · verified by execution*

- **What.** Realistic (19.0%) beats perfect (18.1%) because the 1% participation
  cap — bundled into the realistic toggle — is a *beneficial position-sizing
  rule*, not a cost. It alone, with every cost zeroed, beats perfect fills by
  ~1.9 CAGR points. Costs genuinely subtract ~0.9 points from the cap-consistent
  baseline; the bundling makes them look like they add a point.
- **Where.** `scripts/dashboard_data.py:56-57` (bundling, with a comment
  acknowledging it), `kitelab/slippage.py:213` (`capped_shares` gated on
  `ENABLED`, so the cap cannot be tested alone through config).
- **Evidence.** QMW_199.pkl, all-199, 1%, ₹2,50,000, `portfolio.run` per cell:

  | Configuration | CAGR | Max DD | Taken |
  |---|---|---|---|
  | A · perfect (baseline) | 18.06% | −61.0% | 1,044 |
  | B · spread only | 16.43% | −62.1% | 1,033 |
  | C · spread + impact, no cap | 6.31% | −87.2% | 979 |
  | D · spread + impact + 1% cap (= dashboard "realistic") | 19.00% | −56.3% | 1,105 |
  | E · impact + cap, no spread | 19.66% | −55.8% | 1,109 |
  | F · cap only, all costs zeroed | **19.95%** | −55.5% | 1,160 |

- **Impact.** No arithmetic is wrong, but the slicer compares two things at once,
  and 110 of 1,536 cell-pairs show realistic > perfect. Honest comparisons: cap
  in both arms, or the cap surfaced as its own lever ("size-capped sizing",
  worth +1.9 pts on Q/M/W on its own). The pre-run prediction (cap, not costs,
  drives the anomaly) held — and understated it.

### A4 — W/D/H trade lists are billed under a cheaper fee convention than every other strategy: ₹291,292

*Convention divergence · verified by execution*

- **What.** `tf_compare.simulate_variant` sets `charges` intraday-aware
  (`scripts/tf_compare.py:167`) while `backtest.simulate` bills delivery-only
  into `net_profit` (`kitelab/backtest.py:279`). Only W/D/H has same-session
  trades — 3,145 of 19,073 — so its trade-level net is **₹291,292 higher**
  (₹28.3L vs ₹25.4L, ~10%) than the convention used for the EMA lists. Q/M/W and
  Breakout: zero same-session trades, unaffected.
- **Impact.** Dashboard `tradestats` and per-stock panels mix conventions across
  strategies. Account-level grid CAGRs are consistent (`portfolio.run` recomputes
  charges intraday-aware for all strategies).

### A5 — Q/M/W weekly bars are stamped at the week's first session; two consequences

*Signal defect · verified by execution*

- **What.** (a) The higher-timeframe lookup in `stack_signal` uses the week's
  *start* stamp, so a week that straddles a month boundary recurses the
  forming-month EMA from *two months back* (May instead of the just-completed
  June). (b) Trades carry `entry_ts`/`exit_ts` of the week's first session
  (463 of the first 500 are Mondays) while the fill price is the Friday close —
  the account simulation frees and commits cash up to four days before the price
  used existed, and the daily curve marks positions from Monday at a price from
  Friday.
- **Where.** `scripts/tf_compare.py:89-92` (searchsorted on `base["ts"]`),
  :155–158 (stamps). M/W/D and W/D/H are structurally immune (base-bar stamp =
  decision session).
- **Evidence.** Rebuilt all 199 symbols with the lookup keyed to each week's
  *last* session: 22 symbols produce different trade lists, 3,151 → 3,149 trades,
  taken 1,044 → 999, CAGR 18.06 → **18.03**, DD −61.0 → −60.6.
- **Impact.** Small on the headline (0.03 pts) but it changes *which* trades
  exist and when cash moves; worth fixing before Q/M/W numbers are taught from.

### A6 — Three workbooks render their computed columns blank in your viewer

*Broken deliverables · verified by execution*

- **What.** Without LibreOffice, openpyxl writes formulas with no cached value;
  your viewer shows blank cells. Freshly regenerated today:
  **EMA Timeframe Comparison.xlsx — 3,130 blank formula cells** (shares, cost,
  profit, cum-profit, R, totals), **EMA Showcase.xlsx — 520**,
  **Drawdown Proof.xlsx — 20,493**. Only band_sweep/band_compare have the zip
  post-processor — and band_sweep's injector misses 12 of 29,205 formulas.
- **Where.** `scripts/tf_compare.py:389-404`, `scripts/showcase.py:84-97`,
  `scripts/dd_proof.py:187-272` write formulas; the injector lives only in
  `band_sweep.py:291` / `band_compare.py`.
- **Impact.** Any rerun of these three scripts produces workbooks whose profit
  columns read blank on this machine. The audit's rerun of tf_compare *replaced*
  the Aug-29 copy, which had 231 externally-recalculated cached values — see
  Side effects.

---

## B · Data at risk

### B1 — The BTC manual-validation sheet is gone, and master_report silently omits it

*Unreproducible data missing · verified by execution*

- **What.** `master_report.py` calls its BTC Validation sheet "the only
  unreproducible content in any report" and preserves it by copying
  `output/Aditya P bitcoin - validated.xlsx`. That file does not exist anywhere
  in the repo (nor does the pre-audit master report that embedded it); `output/`
  is gitignored, so history holds nothing. The guard at
  `scripts/master_report.py:308` is a bare `if exists()` with no warning —
  today's regenerated master report simply lacks the sheet, and nothing said so.
- **Impact.** Unless a copy exists outside this repo, the manual backtest
  validation is lost. Check email/class uploads now, and make the missing-file
  case print loudly.

---

## C · Engine defects, small measured impact

### C1 — Cash can go negative: impact is applied after cash-constrained sizing

*Realistic mode only · verified by execution*

`run()` sizes shares by `cash / entry_price`, then multiplies the entry fill by
(1 + impact) before debiting — the account can spend money it doesn't have.
Measured on Q/M/W realistic: 46 entries left cash negative, minimum **−₹1,642**
(economically negligible on an account that reaches ₹86L). Separately, the flat
₹15.34 DP charge can settle a dying account below zero — this is what produces
the −₹7 / −₹16 finals behind A2 even at perfect fills.
Where: `kitelab/portfolio.py:191-198` (sizing) vs :221–227 (impact then debit);
:170–172 (settlement charge).

### C2 — Slippage volatility fallback reads the future

*Lookahead, enabled-mode only · inferred from reading*

`profile()` fills the first ~60 sessions' volatility NaNs with `vol.median()`
over the *entire series* — early-history impact costs are priced with knowledge
of the stock's whole future volatility. The ADV fallback beside it is correctly
point-in-time (expanding median, shifted). Where: `kitelab/slippage.py:94`.
Only matters with `ENABLED = True`, only in each stock's first weeks.

### C3 — apply_spread() is wrong for scale-out trades, with no guard

*Latent · inferred from reading*

It recomputes `gross = (exit − entry) × shares`, ignoring any banked half leg,
and re-bills charges on the wrong sell value. Currently harmless —
dashboard_data only applies it to non-scale-out lists, and its "exact, verified
on 1,806 trades" claim was tested on those — but nothing stops a future caller
from passing an `EMA_half` list and silently getting wrong numbers. Same class
of latent break: `report.order()` reads `net_profit_best` unconditionally and
would KeyError on Q/M/W-variant trade dicts.
Where: `kitelab/slippage.py:197-205`; `kitelab/report.py:75`.

### C4 — Risk is 1% of cost-basis equity, not "current equity"

*Doc vs implementation · inferred from reading*

The docstring says risk is 1% of current equity; the code values open positions
at *entry price* when sizing (`kitelab/portfolio.py:187`). In a drawdown this
overstates equity and risks more than 1% of marked equity. Defensible
convention — but the doc and the code disagree.

---

## D · Redundancy, drift, and frozen numbers

### D1 — Eight summary-stat implementations disagree about the same trades

*Silent divergence · verified by execution*

Computed both ways on the same 9,613 EMA trades (`EMA_199.pkl`):

| Statistic | Net-based (6 files) | Gross-based (tf_compare, band_compare) |
|---|---|---|
| Win rate | 29.41% | 30.03% |
| Profit factor | 1.94 | 2.11 |
| Expectancy / trade | ₹857 | ₹945 |

- The gross basis is *documented* in those two files' captions, but the column
  headers ("Win %", "Profit Factor") are identical across gross and net
  workbooks, so cross-sheet comparison silently mixes bases.
- Zero-loss profit factor: four behaviours for the same input — `report.stats`
  returns **0.0** (an all-winning list renders as the worst PF), tf_compare
  `inf`, band_compare `NaN`, four others `None`.
- Trade-P&L drawdown ordering: entry-ordered −₹278,103 vs exit-ordered
  (dashboard `trade_stats`) −₹246,679 — an 11% gap from ordering convention
  alone. `backtest.summarise` additionally assumes newest-first input (safe with
  its one caller, fragile otherwise).
- Win-rate units: fraction (0–1) in dashboard/tf/band code, percent everywhere
  else.

### D2 — The "one trade shape" invariant is loose

*Shape drift · verified by execution*

- Q/M/W / W/D/H dicts have 19 keys vs 31–34 elsewhere (no `entry_date`,
  `cost_of_entry`, `net_profit_best`, `level`, …). All *current* consumers
  survive; `report.order()` would not (see C3).
- `entry_date` is a `date` in `_build_trade` and a `Timestamp` in
  backtest/darvas; `days_held`/`sessions_held`/`spread_cost`/`months_done` exist
  only in some producers.
- The EMA/Breakout pickles predate the slippage fields — a fresh `simulate()`
  emits `quoted_entry`/`quoted_exit`/`spread_cost` the caches lack. Values on
  shared fields are identical (spot-verified ABB, HAL for EMA, Breakout,
  Darvas — zero mismatches), so results reproduce; the shapes don't.

### D3 — Dead and superseded code

*Verified by grep/AST*

- `with_slippage()` — `scripts/factor_analysis.py:41`: zero callers; superseded
  in the same file by `slippage.apply_spread`.
- `levels.describe()` — `kitelab/levels.py:125`: its only caller was the deleted
  `chart_server.py` (a stale `.pyc` remains in `__pycache__`).
- Superseded scripts: `level_trades`/`trailing_trades` (by `run_strategy`),
  `scaleout_test` (by `scaleout_r_test` + dashboard, though it alone writes the
  assets scale-out workbook), `wide_test` (by master_report/full_report/
  dashboard). `ema_trades` is stale (its RULE caption still describes the
  pre-2026-08-28 intrabar stop and omits the band) *but* is the sole caller of
  `backtest.recent_trades` and `verify_against_screener` — deleting it orphans
  both.

### D4 — Prose numbers that no longer match the tables beside them

*Frozen numbers presented as live · verified*

- `scripts/drawdown_report.py:213-231` — the VERDICTS block (regenerated into
  the workbook every run) hardcodes "1%: 12.2% / −59.9%"; the same file's live
  table now prints 12.6% / −56.8%. Also 24.6/−31.6, 27.3/−32.6, 2.9 vs 5.3 —
  all frozen, undated.
- `scripts/factor_analysis.py:150-152` — the "Data quality 5.3 → 11.9" row is a
  numeric literal inside an otherwise-computed ranking; the data it measured has
  since been repaired, so it can never be reproduced.
- `scripts/tf_compare.py:271-273` — "23 of the 428 hourly entries happen on stub
  bars" frozen in the Read Me; `full_report.py` narrative blocks (":297 cut fees
  by 80%", ":672 ~12% a year"); `darvas_breadth.py` docstring numbers. The
  clearly-dated, labelled measurements (portfolio.py tie-break comment,
  slippage.py Corwin–Schultz note, survivorship RATES) are fine.

### D5 — Strays and small corrections to the known-issue list

*Housekeeping · verified*

- `data/levels.json.bak` is not a copy — HAL and HINDZINC entries differ from
  `levels.json` (same counts, different content). Since the drawing page is
  deleted, decide which is canonical before deleting either.
- Three `instruments_*.parquet` snapshots (Aug 23/25/26) — two are dead weight.
- Breakout overlap count is **1,174** of 2,083 (56.4%), not the 1,169 in the
  notes (`drop_overlaps` on the pickle). The 15 open-marked-closed breakout
  trades reproduce exactly; EMA/Darvas/QMW/WDH drop unfinished trades as
  documented.
- `nse_delisted.csv` holds 454 unique symbols (455 rows); BATLIBOI overlap with
  the live 199 confirmed.

---

## E · Checked and found clean

- **All six reference rows reproduce** from the pickles at defaults — CAGR,
  drawdown, and taken-counts match dashboard.json to its rounding
  (`portfolio.run` per row). Pickle trade counts match the brief
  (9,613 / 3,151 / 19,073 / 2,083 / 7,206 / 3,328 / 10,202).
- **Caches equal fresh simulation**: EMA, Breakout, Darvas re-simulated on spot
  symbols — identical values on every shared field; Q/M/W rebuild reproduces
  3,151 trades exactly.
- **No-lookahead invariants hold**: forming-bar EMA verified against the
  screener 120/120 on two symbols (and 200/200 per symbol in `ema_trades`'s own
  run); Darvas channels exclude the current candle (`shift(1)`); pivot lows
  unusable until span bars after forming, in both trailing variants and the
  breakout stop; trailing ADV shifted one session. Exceptions already reported:
  A5(a), C2.
- **`charges()` is the single fee model** — repo-wide grep finds no
  reimplementation, only prose.
- **`run()` and `daily_curve()` agree to ₹0.00** on final equity for EMA,
  Darvas, and Breakout; `dd_proof` independently reports "walk matches engine on
  5,097 days (max diff ₹0.000000000)".
- **The dashboard is a pure viewer and renders clean**: ~1,400 driven
  combinations across all five pages (384 portfolio combos, 995 stock renders,
  every scale-out/breadth/asset view, all risk × capital cells) — zero
  NaN/undefined in the DOM; no simulation in the browser.
- **Scale-out stays trade-level** — no script or page feeds a `_half` list into
  `portfolio.run`; `legacy_*` drawdown keys appear only in explicitly-labelled
  reconciliation columns.
- **30 of 34 scripts run to completion with exit 0** — zero genuine code crashes
  (table below); `verify` reconstructs 137,298/137,298 derived bars exactly.
- **Data hygiene beyond VINEETLAB**: the corrupt-bar rescan (zero-volume
  collapse detector) found no other stock with this shape; MAZDOCK's 8
  non-positive bars are repaired by `sanitise()` as designed.

---

## F · Every script, executed

Sequential runs, 15-minute cap each. "OK" = exit 0 and sane output.

| Script | Runtime | Outcome |
|---|---|---|
| assets_report | 4s | OK |
| backfill · fetch_assets · login | — | skipped (network/auth); all import cleanly |
| band_compare / band_sweep | 13s / 38s | OK — value injection works (band_sweep misses 12 formulas, A6) |
| breakout_exit_test | 755s | OK (needs >10 min) |
| darvas_breadth / darvas_test | 41s / 265s | OK |
| dashboard | <1s | env-blocked — sandbox denies the port bind (`dashboard_server.py:59`); not a code defect |
| dashboard_data | — | skipped (~1 h); imports cleanly; its output was reproduced piecewise instead |
| dd_proof | 2s | OK — but writes 20,493 blank-formula cells (A6) |
| diagnose / level_audit / level_trades / showcase / show / portfolio / run_strategy / scaleout_test / trailing_trades | ≤20s each | OK (portfolio's printout independently shows finding A2) |
| drawdown_report / ema_trades / factor_analysis / full_report / master_report | 8s / 3s / 5s / 301s / 15s | OK — master_report silently omits the BTC sheet (B1) |
| scaleout_r_test | >900s | alive and progressing at the 15-min cap (mid Breakout sweep); no error — just slow |
| scan | 0s | needs args by design (argparse usage error); `--preset breakout-20d` runs, "no matches" on the latest bar |
| slippage_report / smallcap_test / survivorship_test / tf_compare / verify / wide_test | 33s / 41s / 32s / 7s / 60s / 303s | OK — tf_compare rewrote its workbook with 3,130 blank formulas (A6) |

---

## Side effects of this audit

**No code, data, or published number was changed.** `git status` is clean; every
pickle, parquet, and `dashboard.json` carries its pre-audit timestamp. But
running every script rewrote the gitignored `output/` directory: ~20 workbooks
were regenerated, including several deleted in the 2026-08-28 cleanup
(EMA Backtest, Breakout Backtest, Level Strategies ×2, EMA Strategy Backtest,
EMA Showcase, Scale-Out Test (Assets)), the master report was regenerated
*without* its BTC Validation sheet (B1), and the rerun of tf_compare replaced
the Aug-29 workbook whose 231 formula values had been recalculated externally —
those cached values are gone until the file is opened in real Excel again (A6).
Delete the resurrected workbooks if the 2026-08-28 cleanup should stand.

## Predictions, as required

- Stated before running: perfect-fill cells reproduce exactly from the pickles —
  **held**.
- Stated before running: the participation cap, not spread or impact, explains
  realistic > perfect on Q/M/W, with cap-off runs at or below 18.1% — **held**
  (B: 16.43, C: 6.31), and the cap's effect was larger than predicted: it beats
  perfect on its own.
- Assumed from the brief: the corrupt bar costs the reference accounts nothing —
  **failed**; that wrong expectation is finding A1.

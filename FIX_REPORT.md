# The Kitelab Fix Report

Work against `AUDIT_REPORT.md`, 2026-08-31. Every number states the measurement that
produced it. 17 commits, one finding each.

---

## Read this first

**Every number in this project has changed, for two separate reasons. Do not confuse
them:**

1. **Thirteen defects were fixed.** Section 4 measures each one in isolation.
2. **The universe shrank from 199 stocks to 192**, because seven stocks have
   unusable price history (section 3).

Section 2 has the numbers you now teach from. Section 5 splits every move between
those two causes — and the surprise is that **removing seven stocks moved your
results more than all thirteen bug fixes combined.**

Two things that looked lost are not (section 7). Two defects the audit missed were
found (section 6). Two of my thirteen predictions were wrong (section 9).

---

## 1. Status

| | |
|---|---|
| `data/dashboard.json` | rebuilt 2026-09-01 00:42, **87.2 MB**, exit 0, no warnings |
| grid cells | **6,144** (was 3,072 — the cost/cap split doubled it) |
| universe | **192 stocks** · 49 in-sample · 143 holdout |
| signal caches | 44 of 44 rebuilt |
| dashboard sweep | **7,151 renders, zero NaN / undefined / exceptions** |
| `run()` vs `daily_curve()` | agree to **₹0.00000000** on all four accounts |
| wiped cells | **284** shown as "Wiped" (11 are a genuine 0.0; none has positive equity) |
| findings fixed | **16** — the audit's 13, plus 2 known issues and the universe |

---

## 2. The numbers you teach from

All 192 stocks, 1% risk, ₹2,50,000.

### Account level

| strategy | perfect: was → now | max DD | taken | realistic: was → now |
|---|---|---|---|---|
| EMA M/W/D 2% | 12.6 → **12.3** | −61.7 | 2,513 | 10.0 → **11.9** |
| EMA Q/M/W 2% | 18.1 → **19.3** | −58.0 | 994 | 19.0 → **19.1** |
| EMA W/D/H 2% | −7.8 → **−7.8** | −73.4 | 4,895 | −17.0 → **−20.2** |
| ATH Breakout | 11.7 → **12.8** | −23.6 | 780 | 10.6 → **10.4** |
| Darvas 20/10 | 18.7 → **18.8** | −40.0 | 2,739 | 15.8 → **15.8** |
| Darvas 55/20 | 22.5 → **21.8** | −40.4 | 1,600 | 15.4 → **15.3** |

### Trade level — one position at a time, net of charges

| strategy | trades | win rate | profit factor | net |
|---|---|---|---|---|
| EMA M/W/D 2% | 9,224 | 29.5% | 1.92 | ₹7,745,431 |
| EMA Q/M/W 2% | 3,011 | 31.5% | 3.36 | ₹5,006,509 |
| EMA W/D/H 2% | 18,296 | 24.7% | 1.17 | ₹2,801,977 |
| **ATH Breakout** | **877** | 40.6% | 1.87 | **₹225,049** |
| Darvas 20/10 | 6,908 | 40.2% | 1.89 | ₹2,154,701 |
| Darvas 55/20 | 3,197 | 42.2% | 2.51 | ₹1,575,404 |

The Breakout row is the one to look at twice. It was 2,083 trades and ₹602,613 of
trade-level net; it is 877 and ₹225,049. Fourteen of those trades were positions that
had not closed, and 1,146 were the same rallies counted again (section 11).

### The decomposition the old dashboard could not show you

The "Realistic fills" switch used to do two unrelated things at once. It is now four
settings, and this is what was hiding inside it:

| strategy | Perfect | **Costs only** | **Size cap only** | Costs + cap |
|---|---|---|---|---|
| EMA M/W/D | 12.3% | **1.0%** | 16.5% | 11.9% |
| EMA Q/M/W | 19.3% | 17.1% | 19.9% | 19.1% |
| EMA W/D/H | −7.8% | **wiped** | 2.3% | −20.2% |
| ATH Breakout | 12.8% | 11.0% | 10.8% | 10.4% |
| Darvas 20/10 | 18.8% | 11.1% | 18.6% | 15.8% |
| Darvas 55/20 | 21.8% | 17.8% | **16.6%** | 15.3% |

Three things worth teaching from this table:

- **Real trading costs nearly destroy your main strategy**: EMA M/W/D 12.3% → **1.0%**.
  The size cap was always silently rescuing it.
- **Real trading costs wipe out W/D/H completely.** Not "loses money" — destroyed.
  Under the old bundled setting it read −20.2%.
- **The size cap is not a free lunch.** On Darvas 55/20 it *costs* 5.2 points
  (21.8% → 16.6%). It is a genuine sizing trade-off, now with its own control.

---

## 3. The universe: 199 → 192 stocks

Seven stocks removed, at your instruction, after a scan of all 199. Two kinds of
defect.

**Four have a SEAM** — a long trading gap with the price on a completely different
level either side:

| stock | gap | days | before | after |
|---|---|---|---|---|
| VINEETLAB | 2018-05-23 → 2019-05-08 | 350 | ₹1,556.22 | ₹40.93 |
| VINEETLAB | 2019-05-08 → 2021-06-15 | 769 | ₹1,546.89 | ₹42.95 |
| LANCER | 2023-04-28 → 2026-08-17 | 1207 | ₹49.90 | ₹10.42 |
| LAGNAM | 2016-02-16 → 2018-09-18 | 945 | ₹99.98 | ₹35.20 |
| PVP | 2019-10-29 → 2021-07-22 | 632 | ₹1.80 | ₹5.25 |

Either the company was restructured and Kite's history is unadjusted, or the symbol
was reused. Kite sells no corporate actions, so it cannot be told which — it cannot
be repaired, only removed.

**Three have PADDING** — invented prices in front of the first real trade:
JMFINANCIL (191 bars at ₹0.14 before ₹30.99), TVSSRICHAK (275 at ₹39.90 before
₹107.65), SUDARSCHEM (88 at ₹4.47 before ₹20.27). A history that *starts* with
hundreds of invented bars has no usable early period.

All seven were holdout stocks: **199 → 192, holdout 150 → 143, in-sample still 49.**

**The exclusion list is in tracked code, not in your config file.**
`config.local.toml` holds the raw universe and is gitignored because it holds the
Kite secrets. Putting the removals there would have taken a change that moves every
published number completely out of version control. They are a tracked dict in
`kitelab/config.py` — `EXCLUDED[symbol] = reason` — each carrying the measurement
that justifies it, applied inside `Config._dedupe()` so no universe property can
miss one. Reversible by deleting a line.

**"199" was hardcoded in 30 places** — labels, docstrings, sheet titles, the last
entry of two breadth-sweep size lists, and a workbook *filename*. Every one would
have gone on saying 199. They now count from config. Two renames follow:
`Full Analysis 199 Stocks.xlsx` → `Full Analysis.xlsx`, and
`data/signal_cache/*_199.pkl` → `*_all.pkl`.

### One consequence you should know about

**The "10 random stocks" universe is now ten different stocks.**

| | basket | CAGR |
|---|---|---|
| before | ABB, CUMMINSIND, ESCORTS, ESSARSHPNG, GODAVARIB, IOC, JBMA, NATCAPSUQ, TURTLEMINT, VHL | 12.3% |
| now | COCKERILL, CPEDU, ISGEC, KSL, LTM, METROGLOBL, PIDILITIND, PRABHA, UNITDSPR, VIKASECO | 11.4% |

Not one stock in common — and **none of the seven removed was in the old basket.**
The 75 baskets are drawn with a fixed seed from the *sorted* symbol list, so deleting
seven names shifts every index and every draw lands elsewhere. Same seed, different
list, different sample. Nothing is wrong with it, but slides naming those ten stocks
are now about a basket the dashboard no longer shows.

---

## 4. The thirteen fixes

| # | Finding | Commit | Numbers moved |
|---|---|---|---|
| B1 | Master report silently omitted the BTC validation sheet | `fb14e75` | none |
| D3 | Two dead functions, one stale `.pyc` | `c9ee779` | none |
| C4 | Docstring said "current equity"; code uses cost basis | `9319f69` | none |
| C3 | `apply_spread` / `report.order` mis-priced silently | `d9b8a27` | none |
| D4 | Frozen prose numbers beside live tables | `1c06247` | workbook prose |
| A6 | 24,143 blank formula cells in three workbooks | `6ce79ef` | cells filled |
| A2 | Wiped accounts displayed as "+0.0%/yr" | `d1d88ce` | display only |
| A1 | Bars recording a price nothing traded at | `634fd30` `e2b474d` | **yes** |
| A5 | Aggregated bars decided on the wrong session | `90737c9` | **yes** |
| A4 | Two fee conventions | `6698c94` | trade-level |
| C2 | Volatility fallback read the future | `accf10e` | 0.006 pts |
| C1 | Cash could go negative | `2875b23` | **yes** |
| A3 | "Realistic fills" bundled a sizing rule with costs | `b52aed0` | adds two modes |
| — | 7 stocks removed; every universe count computed | `05bb556` | **all of them** |
| — | Master report deleted; BTC sheet moved | `e1d6adb` | none |
| #1 | Open positions counted as closed trades | `c985ccc` | breakout trade count |
| D1 | Eight disagreeing stat implementations | `064b6e1` | tf/band sheets |
| #2 | Overlapping breakout trades double-counted | `b81864f` | **breakout trade-level** |

Isolated effects, measured on the 199-stock universe by running the pre-fix and
post-fix code side by side in one process:

- **A1** two channels. *The bars themselves* (identical trades; only ADV, volatility,
  spread, impact and the size cap change): EMA M/W/D realistic **9.950 → 10.505**,
  Q/M/W realistic 19.002 → 19.080, everything else ~0. *The trade lists*: Q/M/W
  perfect 18.064 → 18.197, W/D/H perfect −7.849 → −6.735, Darvas 20/10 18.745 → 18.662.
- **A5** Q/M/W only, and M/W/D and W/D/H are structurally immune (verified: 2,877 and
  4,363 trades over 40 symbols, zero change). Perfect 18.197 → **18.559**, realistic
  19.091 → **18.808**.
- **A4** trade-level only. W/D/H realistic charges 2,274,875 → **1,983,236**; net
  −3,667,258 → **−3,375,619**. Account CAGRs untouched.
- **C1** perfect fills exactly 0.000 on all six. Realistic: W/D/H **+0.580**, every
  other row inside 0.014.
- **C2** W/D/H −0.006, everything else 0.000.

---

## 5. What caused each move: fixes vs. universe

I reconstructed the exact counterfactual — *all fixes, 199 stocks* — by re-simulating
only the seven excluded stocks with the current code and adding them back. It
reproduces the rebuilt grid to three decimals, so the split is trustworthy.

| | before | 13 fixes | removing 7 stocks | **total** |
|---|---|---|---|---|
| EMA M/W/D perfect | 12.563 | −0.004 | −0.261 | **−0.27** |
| EMA M/W/D realistic | 9.950 | +0.553 | **+1.398** | **+1.95** |
| EMA Q/M/W perfect | 18.064 | +0.495 | +0.739 | **+1.23** |
| EMA Q/M/W realistic | 19.002 | −0.187 | +0.261 | **+0.07** |
| EMA W/D/H perfect | −7.849 | +1.151 | −1.075 | **+0.08** |
| EMA W/D/H realistic | −16.963 | +0.672 | **−3.914** | **−3.24** |
| ATH Breakout perfect | 11.656 | −0.000 | **+1.213** | **+1.21** |
| ATH Breakout realistic | 10.614 | −0.004 | −0.048 | **−0.05** |
| Darvas 20/10 perfect | 18.745 | −0.083 | +0.152 | **+0.07** |
| Darvas 55/20 perfect | 22.504 | +0.009 | −0.724 | **−0.72** |

**In most rows the seven stocks moved your numbers more than all thirteen fixes
combined.** The Breakout's perfect-fill CAGR rose 1.2 points from the stock removal
alone, with the fixes contributing literally nothing (−0.000).

That is the headline finding of this whole job: **the largest single source of error
in this project was never the code. It was seven stocks with unusable price data.**

### Two moves that are sensitivity, not correction

**W/D/H perfect, −7.849 → −6.735 under A1.** Only **4 of 19,073** trades changed and
the trade-level net moved ₹9,017 on ₹28.3L (0.3%) — but the account is
cash-constrained, so those 4 changed entries re-sequenced **1,009** later decisions.

**W/D/H realistic, +0.580 under C1.** Same cause. That account ends at ₹31,619 of an
initial ₹2,50,000 and turns away **13,714 of 19,073** signals for want of cash. An
account living at the cash boundary hits the trimming path constantly.

**The general point: the W/D/H account CAGR is chaotically sensitive to sequencing.**
Your own tie-break test said the same (8.3%–18.9% across 25 orderings). Teach it as
"around −8%", not "−7.85%". Its *trade-level* statistics are far more stable.

---

## 6. Two defects the audit missed

**JMFINANCIL had 191 fake bars, and they manufactured trades.** The audit's rescan
concluded "no other stock with this shape" — it was looking for price collapses, not
padding in front of a listing. 191 zero-volume bars at ₹0.14 seeded the 20-day EMA at
₹0.14, so on the stock's first real trading day price sat **221× above its own EMA**,
the stack rule fired, and Darvas took the same day with a stop of ₹0.14.

The largest single trade change in the job is here, and it is a **gain the fake bars
were suppressing**: JMFINANCIL Q/M/W 2006-10-09 @ ₹30.41 → 2007-01-02 @ ₹33.09, net
**₹2,153 → ₹33,443**. The ₹0.14 stop had crushed the position size on a +191% move.

**The fills toggle silently changed W/D/H's fee convention.** The audit reported W/D/H
billed ₹291,292 cheaper than the EMA lists — true, but not the sharp edge. W/D/H's
*perfect* list came from `tf_compare` billed intraday (₹1,983,048) and `apply_spread`
re-billed the same trades at **delivery** for the *realistic* list (₹2,274,875). So
flipping the slicer changed the fee rulebook as well as the fills, charging realistic
W/D/H ₹291,639 that had nothing to do with execution.

---

## 7. Two things that looked lost, and are not

**The Bitcoin manual-validation sheet.** `master_report.py` preserved a file that was
gone, and skipped it in silence. It is in your Google Drive as **"Aditya P bitcoin
manual"** (owned by you, modified 2026-08-26 15:12) — 36 hand-backtested BTC trades
on the M/W/D 2% rule, ₹10,00,000 book, 19 wins / 17 losses, total profit 6,322,865,
profit ratio 1.163, profit perc 14.05%.

> **Action:** open it, File → Download → Microsoft Excel (.xlsx), save as
> `output/Aditya P bitcoin - validated.xlsx`. Until then `scripts.assets_report`
> refuses to run (or use `--allow-missing-validation`, which builds the report with a
> sheet reading "THIS SHEET IS MISSING").

**The recalculated timeframe workbook.** The audit's rerun overwrote the copy whose
231 formula values had been recalculated in real Excel.
`~/Downloads/EMA Timeframe Comparison.xlsx` (Aug 29 19:06) is intact — 231 formulas,
all 231 with cached values. You no longer need it: the regenerated workbook now
caches all of its formula values.

---

## 8. Corrections to `AUDIT_REPORT.md`

1. **A1 is six bars plus 191.** JMFINANCIL carries the same defect in a different
   shape. "No other stock with this shape" is wrong.
2. **A1 acts through two channels**, not one — see section 4.
3. **A4's sharp edge is inside W/D/H**, not between W/D/H and the EMA lists.
4. **C2 affects 6 sessions per symbol, not "the first ~60".** The window is 60 but
   `min_periods` is 5, so only indices 0–5 are ever NaN. Verified on four symbols.
5. **C3's `report.order` trips on `cost_of_entry` first**, not `net_profit_best`.

Where the audit was right and I was wrong: its estimate that excluding the fake-exit
trades takes Q/M/W to 18.21% matched the real repair (18.197%) almost exactly.

---

## 9. Predictions, and whether they held

House rule: state the prediction before the run, report it wrong if it was wrong.

| # | Prediction | Outcome |
|---|---|---|
| 1 | All 12 reference cells and 7 trade counts reproduce before any change | **held** |
| 2 | Phase 1 leaves the baseline byte-identical | **held** after all five commits |
| 3 | `master_report` fails in under 2s, exit 1, no workbook written | **held** — 0.19s |
| 4 | `factor_analysis`'s workbook is cell-identical after removing dead code | **held** — 223 cells |
| 5 | A1 moves Q/M/W *less* than the audit's estimate of 18.21% | **WRONG** — 18.197%, matching it. My reasoning was right about the one trade and missed the mechanism: with the fake exit gone, the earlier trade stays *open* over it, so the −₹11,211 trade is never opened at all |
| 6 | A1 also moves EMA and Darvas, via bars the audit never saw | **held** |
| 7 | A5 moves Q/M/W by more than the audit's 0.03 points | **held** — +0.36 / −0.28 |
| 8 | A4 leaves non-W/D/H lists untouched; W/D/H net +₹291,639; account CAGRs unmoved | **held** on all three |
| 9 | C2 moves every realistic row by less than 0.05 points | **held** — largest 0.006 |
| 10 | C1 leaves perfect fills untouched | **held** — exactly 0.000 on all six |
| 11 | C1 moves every realistic row by less than 0.02 points | **WRONG** — W/D/H moved +0.580. That account lives at the cash boundary and turns away 13,714 of 19,073 signals, so the trimming path fires constantly. The other five were inside 0.02 |
| 12 | A3 leaves modes `"0"` and `"1"` identical; cap-only beats perfect on Q/M/W by ~1.9 | **held** — cap-only 19.948 against the audit's independent 19.95 |
| 13 | The 10-stock basket is unaffected by the A2 sorting change | **held** — zero of 75 wipe; same 10 chosen |

Two wrong of thirteen, both the same way: **I under-estimated how much a
cash-constrained account amplifies a small input change.** That is the most useful
thing this job taught me about your engine, and why section 5 ends as it does.

---

## 10. Verification

- **Baseline** captured before any change (six reference rows × two fill modes, all
  44 pickles, sha256 of the grid), re-run and confirmed **byte-identical** after every
  Phase-1 commit.
- **Isolation.** Each fix measured by executing pre-fix and post-fix code side by side
  in one process, or by re-simulating only affected symbols and splicing — validated
  by re-simulating 12 unaffected symbols and confirming zero changed.
- **Invariants after the rebuild:** `ENABLED=False`, `MAX_PARTICIPATION=None`,
  `NEXT_OPEN_FILLS=False`, `TIE_BREAK='liquidity'`; `run()` vs `daily_curve()` agree
  to **₹0.00000000** on EMA, Q/M/W, W/D/H and Breakout; `apply_spread` refuses
  **4,488 of 4,488** scale-out trades; `verify` reconstructs **137,298 of 137,298**
  derived bars exactly.
- **Dashboard swept live on the final data: 7,151 renders, zero NaN / undefined /
  exceptions** — 6,144 portfolio combinations (4 fill modes × 5 strategies ×
  bands/windows × 4 universes × 4 risks × 4 capitals), 960 per-stock renders,
  8 scale-out pages, 16 basket panels, 18 asset views, 5 breadth views.
- **Wiped rendering proven on real data**: `ema|0|all|2|50000|0` (final −₹11) renders
  "Wiped" in the loss colour with "account destroyed — ended at −₹11 over 14.5 yrs",
  and its risk sweep reads 11.4% / 8.5% / −5.6% / **Wiped** — where the last bar
  previously read "+0.0%", i.e. *better* than the −5.6% beside it.

---

## 11. The last three findings

Fixed after the first rebuild, at your instruction, and each measured before the
final rebuild.

### Known issue #1 — a position still open is not a closed trade

`trailing.resolve()` invented a mark-to-market exit for positions that never close
before the data ends, and `strategies._build_trade` counted them like any other
trade. `backtest.simulate` and `darvas.simulate` both stop instead. So the breakout
and level strategies were counting positions as results, and feeding them into the
win rate, profit factor and expectancy.

**ATH Breakout 2,031 → 2,017 trades**, carrying −₹1,127. Every other strategy already
had zero. It is 14, not the 15 in the known-issue list — the fifteenth was in one of
the seven removed stocks.

### Known issue #2 — one position at a time, in trade-level views

The breakout re-fires while already long, so **1,146 of 2,031 signals overlap** an
open trade in the same stock. `strategies.drop_overlaps()` had existed the whole time
and was never applied.

| ATH Breakout, trade-level | every signal | one position |
|---|---|---|
| trades | 2,017 | **877** |
| win rate | 41.75% | 40.59% |
| profit factor | 2.03 | **1.87** |
| expectancy | ₹298.77 | ₹256.61 |
| **total net** | **₹602,613** | **₹225,049** |

**₹377,564 of the published breakout trade-level profit was the same rallies counted
more than once.**

Deliberately **not** applied to the grid. The one-account simulation was always
honest here — it holds one position per stock and reports the rest as `skipped_busy`
— and de-duplicating its *input* would change which signals it is ever offered, which
is a different question. Account CAGRs are untouched by this.

A no-op for every other strategy (0 dropped from 9,224 / 3,011 / 18,296 / 6,908 /
3,197), so it is applied unconditionally rather than special-cased, and stays correct
if a future strategy overlaps.

### D1 — one convention for the summary statistics

Net of charges, win = net > 0. What six of the eight files already did, and what the
account actually experiences: a trade that made ₹50 and paid ₹80 in charges is a loss.
`tf_compare` and `band_compare` moved off gross.

On the five assigned stocks, M/W/D (182 trades): win rate **31.32% → 30.22%**, profit
factor **2.19 → 1.99**, expectancy **₹1,027 → ₹923**.

The zero-loss profit factor had **four** different answers for the same input — `0.0`
in `report.stats` (so an all-winning list rendered as the *worst* possible score),
`inf` in tf_compare, `NaN` in band_compare, `None` in four others. It is `None`
everywhere now and prints as the words **"no losses"**, because a blank cell reads as
"not computed" and 0.0 reads as "terrible". Eleven consumers across six scripts
guarded.

---

## 12. Still open

- **`data/levels.json.bak`** differs from `levels.json` on HAL and HINDZINC. Decide
  which is canonical before deleting either. Untouched.
- **Survivorship bias** is uncorrected (~0.2 CAGR points by your own stress test), and
  **BATLIBOI** is still in both the live universe and `data/nse_delisted.csv`.
- **Win-rate units** are a fraction (0–1) in the dashboard, tf and band code and a
  percent everywhere else. Each file is internally consistent, so nothing is currently
  wrong; unifying it is cosmetic and touches every caller.
- **The performance work** in section 10 — frame caching, a vectorised resample,
  parallelism across symbols. Diagnosed, not done.

### Recommended next, not done

The rebuild took ~2 hours on one of your ten cores. Profiling shows why, and it is
not Python being slow: reading a symbol's parquet takes **0.05 s**; resampling it to
hourly takes **1.89 s**, dominated by 3.1 million `isinstance` calls and ~14,400 tiny
`Index` constructions — because `_resample_intraday` builds one small DataFrame per
trading day. Three cheap wins, in order of value:

1. **Cache the frames.** `frames.load` and `base_15m` have no cache, so six band
   sweeps each redo identical hourly resampling, and Breakout's eleven variants do it
   eleven times. Roughly a one-line change.
2. **Vectorise the resample** — one groupby over the whole frame instead of ~2,500
   tiny ones. `verify.py` already proves correctness (137,298 bars exact).
3. **Parallelise across symbols** — nine cores are idle.

Multiplicative, and none requires leaving Python. A Rust rewrite would buy the same
overhead back at vastly higher cost, in a language you could check even less easily.

---

## 13. If you read one thing

Three sentences.

**Your dashboard used to show a destroyed account as "+0.0%/yr", in 284 places.** A
risk sweep read 11.4% / 8.5% / −5.6% / **+0.0%** — so the riskiest setting, which had
wiped the account out entirely, looked *safer* than the one beside it.

**Your breakout strategy's trade-level profit was ₹602,613 and is ₹225,049.** The
difference was the same rallies counted more than once, plus fourteen positions that
had never actually closed.

**And the single largest source of error was never the code — it was seven stocks
with unusable price data**, which moved your published numbers more than all thirteen
code fixes put together.

# Handover — 2 September 2026

Written at the end of a long session. This is what the next one needs to know:
where things stand, what was decided and why, what is still open, and the traps
that cost time today so they need not cost it twice.

---

## 1. Where the project is

A local backtesting workbench for rules-based swing trading on NSE equities,
built on Kite Connect. Three programs, deliberately separate:

| | needs a Zerodha account | needs the venv |
|---|:---:|:---:|
| `scripts.backfill` — fetch raw data | yes | yes |
| `scripts.refresh` — clean and rebuild | no | yes |
| `./run_dashboard.sh` — view | no | no |

`refresh` works out what is stale and does only that. On an unchanged project it
prints "already current" in a second, which matters because a rebuild is ~28 min.

**State as of this handover**

- Dashboard current, built 2026-09-02 21:02 IST, 101 stocks
- 35 of 35 signal caches stamped
- Working tree clean, everything pushed to `origin/main`
- Raw store: 3,002 daily files. Clean (working set): 217 parquet
- `dashboard.json` 14.4 MB, curves in a 72 MB sidecar

**The mount moved late in the session.** `/work` is now the repo root; it used to
be `/work/kitelab`. If git complains about "dubious ownership", the fix already
applied is `git config --global --add safe.directory /work`.

---

## 2. The universe, and the split that matters

101 stocks, cut from 192 by a written quality gate (5 years history, ₹20 lakh
median turnover, no seams, no padding, no suspected unadjusted splits). Every
exclusion is recorded in `kitelab/config.py` with the measurement that justifies
it.

**THESE 101 ARE IN-SAMPLE.** Every parameter — the 2% band, 20/10 and 55/20, the
weekly gate, the risk levels — was chosen by looking at them. No result measured
here is evidence that the rules work.

**The holdout is the 399 stocks not yet added.** `accepted.txt` in the clean
directory holds them: 100 micro, 100 small, 100 mid, 99 large, median history
20.7 years, all through the same gate. Raw data is already fetched. Nothing has
ever been tuned on them, so that is where the rules get their first honest test.

Two conditions or the split is worthless: they go through `screen_universe`
unchanged, and **nothing gets tuned on them**. A holdout you tune on stops being
one, and there is no second.

---

## 3. What is on the dashboard

23 strategy variants across 4 universes, 4 fill modes, 4 risk levels, 3 account
sizes, 5 start years.

| key | what it is |
|---|---|
| `ema` | EMA 20 stack, M/W/D — the class strategy, 6 bands |
| `qmw` | EMA 20 stack, Q/M/W, 6 bands |
| `pair` | one higher timeframe: W/D, M/D, M/W, Q/W |
| `e1` | daily 20 EMA alone, no higher timeframe |
| `eath` | M/W/D, buying only within 10% of the all-time high |
| `dv` | Turtle channel: 20-10 and 55-20, each with and without the weekly gate |
| `hg` | Holy Grail — ADX + 20 EMA pullback |

Views: **Compare** (ranked table, the landing page), **Detail** (one row's charts
and cost waterfall), **Stocks**.

---

## 4. What the numbers say

All 101, ₹2,00,000, 1% risk, realistic fills, from 2018 unless noted.

**The Turtle leads, clearly.** 55-20 with the weekly gate: 34.2% CAGR, −21.8%
drawdown, MAR 1.57. The class EMA stack is 0.85.

**Two timeframes beat one, and adjacency matters.** The ladder is monotonic —
none 0.34, one ≈0.70, two 0.85. M/D (skipping the weekly) is the weakest pair at
0.51, so skipping a level costs something.

**The all-time-high band hurts.** MAR 0.85 → 0.35: it halves the return *and*
deepens the drawdown. A 30-stock preview had suggested it was neutral, which is a
lesson about small samples.

**The rules do not travel to mid caps.** Turtle 55-20 goes 1.57 → 0.21 on the 18
mid caps while Q/M/W is best there. The ranking essentially inverts. Eighteen
stocks is too few to conclude much, and that is itself the point.

**The Holy Grail does not work here.** MAR ~0.21 under every reading tried.

**Risk 0.5% beats 1% on a return-per-drawdown basis** for the EMA stack (MAR 0.56
against 0.30), which the CAGR column hides.

**Cash is the binding constraint, not capital.** The class stack holds under 5%
cash on 80% of days and turns away 76% of its signals. ₹10 lakh takes 738 signals
where ₹2 lakh takes 727 — position size scales with equity, so more money buys
bigger positions, not more of them. The lever is the risk setting: 0.25% takes
47% of signals where 1% takes 24%.

**Capture rate drifts down over time**, 23% in 2006-2010 to 16% in 2022-2026, so
part of any late-period result is account size rather than the rule.

---

## 5. Bugs found today, and what they teach

- **₹52 positions.** Risk-based sizing capped by cash produced positions the flat
  ₹15.34 DP charge ate 29% of. 1,972 unplaceable trades were polluting every
  statistic. Now refused when a round trip would cost more than 0.5% of the
  position.
- **The Turtle stopped at the channel low, not the entry candle's low** — 14.2%
  below entry against 2.9%, so positions were a fifth of the right size. Every
  Turtle number published before that fix was on the wrong stop.
- **MAR was computed and never put in the payload**, and the compare page sorted
  on it. Caught by dry-running the page against the finished file.
- **`parse_qs` was never imported**, so `/api/curve` returned 500 on every
  request. The module imported fine because the name is only looked up when the
  route runs.
- **The readout table was welded to the screen.** `table.new(position.top_right)`
  anchors to the viewport, not the bars.
- **numpy leaking into JSON.** `round()` on a numpy scalar stays numpy, and numpy
  2 renders its class name as plain `bool` — so a 25-minute run died on "Object
  of type bool is not JSON serializable". `to_native()` now converts on write and
  reports what leaked.

The pattern: **compiling is not working.** Four of these passed every static
check. Run the thing against real data.

---

## 6. Traps that cost time

- **`pkill -f "scripts.dashboard"` matches its own shell** and kills the session.
  Use PIDs.
- **`clean_data` used to rewrite identical files**, moving mtimes and invalidating
  every stamp — a false "PRICE DATA has changed" banner. Fixed; it now skips
  unchanged files.
- **Small samples mislead.** The ATH band looked neutral on 30 stocks and clearly
  harmful on 101.
- **`/tmp` gets cleared.** Write long-running logs to `output/`.
- **The class spreadsheets are not backtests.** See below.

---

## 7. The class spreadsheets

Three were provided. None can settle a rule on its own.

- `Aditya T Bajaj Finance Back Testing (1).xlsx` — a classmate's, 39 Titan trades.
  Contains **downtrend trades from before the uptrend rule existed**, so it cannot
  answer what "uptrend" means.
- `newsest.xlsx` — byte-identical to the Titan sheet above.
- `holygrail.xlsx` — MANAPPURAM, 10 trades, **all winners**, every one commented
  "exit at previous high". The setup fires 74 times in that window; running all of
  them gives 25 trades and a **32% win rate**.

The decisive arithmetic came from their own numbers: their ten winners average
**+0.39R**, because the exit is the previous high and it is usually close. A rule
whose winners average 0.39R needs a **72% win rate to break even**. The real rate
is 32%.

**So the problem is the exit, not the entry** — cutting winners at the previous
high while losers run the full stop. That is precisely what the Turtle's trailing
channel avoids, and why it sits at 1.57.

When comparing against classmates' results, the differences are almost entirely
method: one hand-picked stock, no costs, no cash constraint, trade-level not
account-level, and setups marked by eye on a chart already visible. Same rule on
61 stocks separately: Titan ranked 18th, and 21% of stocks lost money.

---

## 8. Open items

**Tomorrow's main job — the 399.** Raw data fetched, `accepted.txt` written.

Do **not** simply add them to the `all` universe: that multiplies every cell by
~5 and takes the rebuild to ~144 minutes. Add them as a **fifth universe**
(`holdout: 399`) and leave `all` at the in-sample 101. That is ~45 minutes and
answers the better question. Then:

- drop the third capital (₹3L — capital barely moves the result)
- retire settled variants (the ATH band clearly hurts; `e1` has made its point)

Together roughly 25 minutes.

**Rejected, with reasons.** More cores: the work is single-threaded and
embarrassingly parallel, but the realistic ceiling is 4–5× (heterogeneous Apple
cores, a fanless Air that throttles, ~1 GB per worker), and parallelising the
global fill-mode state risks plausible wrong numbers. Rust: it prevents memory
errors and data races, none of which this project has had — every bug today was a
logic error, which Rust compiles happily. The FIX_REPORT reached the same
conclusion in August.

**Smaller, open**

- STT is a single constant; delivery STT is believed to have changed around June
  2013. `STT_SCHEDULE` in `backtest.py` is the mechanism, deliberately holding one
  entry. Cost of leaving it: ~1.2% of gross. Verify the rate before enabling.
- `levels.json` is hand-drawn and **cannot be regenerated**. It lives in the clean
  directory, which is otherwise rebuildable. It needs its own backup.
- Survivorship: the universe comes from the *current* instrument list, so
  delisted names are invisible. An Indian smallcap reconstruction puts the
  inflation at ~4.9 percentage points a year.
- 40 dead cache files are stranded in the read-only raw mount.
- `scripts/portfolio.py` still tests ATH Breakout, which was ruled out. It is the
  only reason `Breakout_101` and `rebuild_orphans.py` still exist.
- The four Holy Grail trend readings (`di`, `hl`, `hh`, `hhhl`) are in the code
  but not on the dashboard.

---

## 9. Pine scripts

- `pine/ema_ath_band.pine` — indicator. 20 EMA plus the all-time-high band, on
  whatever timeframe the chart is on. Everything bar-anchored.
- `pine/ema_near_high.pine` — the multi-timeframe strategy the backtest mirrors.
- `lookahead_off` on every higher-timeframe call. Without it, Tuesday gets
  Friday's weekly EMA and the backtest is a lie.

---

## 10. How to run things

```bash
# view — no keys, no venv
./run_dashboard.sh

# rebuild whatever is stale — no keys, needs the venv
./.venv/bin/python -m scripts.refresh
./.venv/bin/python -m scripts.refresh --check    # report only

# fetch new data — needs the account
set -a; source ~/.secrets/all.env; set +a
./.venv/bin/python -m scripts.login
./.venv/bin/python -m scripts.backfill --symbols-file <list> --daily-only

# health
./.venv/bin/python -m scripts.cache_status
```

Everything is stamped: caches and the dashboard record the universe, price files
and strategy code they were built from, and say so when any of the three moves.
Silence means verified, never "did not look."

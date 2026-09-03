# Handover — 3 September 2026

Supersedes the 2 September handover. **Read the first part of section 4 before
quoting any number.** Two things happened: most of what the previous version
published was rebuilt on corrected code and moved a long way, and the in-sample
universe turns out to be a list of winners — so the in-sample figures are
inflated before any strategy runs.

---

## 0. The split was retired — 3 September, later the same day

**This supersedes section 2 outright, and changes how sections 4 and 10 should
be read.** The 101/399 in-sample/holdout split is gone. There is one universe of
500 stocks (`config.Config.merged`), and `scripts/bootstrap.py` is the control
that took the holdout's place.

**The argument.** Train/test exists to catch a FITTED model that memorised its
training rows. These rules are not fitted that way — the shapes were taught in
class before this repo read a candle: the 20 EMA, the M/W/D stack, ADX>25 into a
pullback, Donchian 20-10 and 55-20. Holding 399 stocks back from a pre-specified
rule buys no validity and costs a great deal of power, and here it cost more than
usual, because it forced every headline number onto 101 stocks that turned out to
be a list of winners. A wider deck fixes that directly.

**Section 2's "every parameter was chosen by looking at them" was an
overstatement**, and the correction matters because it decides which control is
needed. Actually chosen here: the 2% band, the weekly gate added to the Turtle on
2026-09-01, and — the real one — the choice of WHICH of 24 variants to headline.
Everything else is class-given or swept as a grid axis.

**So the exposure is multiple testing, not contamination**, and a holdout is a
blunt instrument against it: it spends 80% of the data controlling for something
a bootstrap controls for on all of it. §4 already measured the exposure — 13
different variants won across 25 fresh draws.

**What replaced it.** `python -m scripts.bootstrap` reduces each rule's trades to
net R multiples (costs inside), resamples them into thousands of alternative
histories at fixed-fractional risk, and reports the CAGR/MAR/drawdown spread. A
rule whose 5th-percentile CAGR is above zero has an edge distinguishable from
luck on its own trades. Then the summary that matters: how many of the 24 clear
that bar, against the ~1.2 an edgeless menu clears by chance.

**What merging does NOT fix, so nothing here is a clean bill.** The old 101 are
still winners — now ~20% of the deck rather than 100%, which on large caps moves
the median buy-and-hold from +15.3% to roughly +11.2% against the 399's +9.7%.
Diluted, not gone. Survivorship is untouched and remains the largest known bias
in every number this project produces (~4.9pp/yr).

**Reading the rest of this document.** Section 4's tables were computed on the
old split and are superseded by the first merged rebuild; the *reasoning* in them
— why the Turtle's tail-dependence sinks it, why comparisons on the same deck
survive — still holds. The §10 open item "rerun the breadth sweep on the 399" is
closed by construction: breadth now runs on all 500. The item "decide what the
101 is for" is closed too — it is no longer a universe, only an argument in
`scripts.universe_bias`.

---

## 0b. Lean rebuild, two new axes, and an audit — 3 September, later still

Follows section 0. The dashboard was cut to its one purpose, comparing
strategies, and two axes were added that change how the compare table reads.

**Strategies are declared once**, in `kitelab/registry.py`. Adding one puts it in
the compare grid, the breadth sweep, the non-equity table and
`scripts.bootstrap` with no other edit. Each declares the module that produces
its trades and `signals.stamp()` derives the code digest from that, so the
2026-09-02 cache bug — a rewritten `holygrail.py` invisible to a hand-kept list —
is now unrepresentable rather than merely fixed.

**The grid is 2,688 cells and ~7 minutes**, from 23,040 and 15.5. Capital cut to
one value and risk to two, both on this document's own measurements; only
realistic fills are gridded, the other three having existed solely to feed the
cost waterfall.

**Scanning pool is now an axis.** Over 500 stocks at ₹2,00,000 the class EMA
stack skips **56%** of its signals for want of cash and its median falls to
**2.5%**, against **15.7%** at 30 names. The Turtle skips 49% and climbs to
15.1%. So the all-500 default reports partly how each rule's signal frequency
fits one account, not which rule is better.

**Signal priority is now an axis, and it is the bigger finding.** Which signal
wins the cash when you cannot fund them all was a hardcoded constant. It swings
CAGR by up to **20 points** — larger than the gap between the strategies being
compared. But no ordering wins consistently and Q/M/W's best rule is M/W/D's
worst, which is what noise looks like; picking the best of 5 orderings × 24
variants is 120 combinations, exactly the trap `bootstrap` exists to catch.
**The honest reading is that on a cash-starved account these CAGR numbers are
not stable estimates of anything.**

**Audit, 12 findings, all fixed.** The three that mattered:

1. `_pooled` dropped wiped baskets, so the median was taken over survivors and
   `pool_worst` named the worst basket THAT LIVED — flattering the exact number
   a reader leans on precisely because they cannot choose their stocks. Wiped
   draws now sort below every loss and their count is published, as the breadth
   sweep always did.
2. Breadth and the pool axis drew baskets from different seeds, so one question
   got different stocks in two views. One sampler now, one seed, and the pool
   axis reads a prefix of breadth's draws.
3. The non-equity results were computed and never rendered — half a megabyte of
   payload with no view. There is an Assets view now, and the instruments run
   through the same registry as the equities, so a new strategy is tested on
   them too.

Also: `clampScenario()` had been dead since the holdout payload keys went — it
returned on its first line while the universe selector kept calling it;
`scaleout`, `scaleout_r`, `tradestats`, `timeframes` and `nifty` were computed
every build and displayed nowhere, the scale-out sweep alone costing ~64s;
`report.py` and the openpyxl dependency are gone. Compare exports to CSV.

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
prints "already current" in a second, which matters because a rebuild is ~16 min.

**State as of this handover**

- Dashboard current, built 2026-09-03 11:17 IST
- 101 stocks in-sample, 399 holdout, 0 overlap
- 24 strategy variants, 23,808 grid cells, `dashboard.json` 14.9 MB
- Working tree clean
- **The in-sample 101 is a list of winners (§4). Quote the 399.**

---

## 2. The universe, and the split that mattered — SUPERSEDED BY §0

101 stocks, cut from 192 by a written quality gate (5 years history, ₹20 lakh
median turnover, no seams, no padding, no suspected unadjusted splits). Every
exclusion is recorded in `kitelab/config.py` with the measurement that justifies
it.

**THESE 101 ARE IN-SAMPLE.** Every parameter — the 2% band, 20/10 and 55/20, the
weekly gate, the risk levels — was chosen by looking at them. No result measured
here is evidence that the rules work.

**The 399 are the holdout, and they are now wired in.** `config.local.toml` holds
them as `[universe] unseen`; `cfg.out_of_sample` returns them minus anything
in-sample. They appear on the dashboard as `h_all`, `h_large`, `h_mid`, `h_small`.

**The holdout grid is deliberately narrow: 2 risk levels, one capital, one start
year.** That is the whole design. A wide holdout grid is a menu of results to
pick a favourite from, and picking one is how a holdout stops being a holdout.
The page greys out every setting outside `DATA.holdout_axes` so the narrowness is
visible rather than merely intended.

There is no second holdout. Nothing gets tuned on the 399.

**The split is sound; the in-sample side is not representative.** Nothing above
is wrong — the 399 really are untouched — but the 101 were assembled by an
unrecorded process and turn out to be winners (§4). Treat the 101 as a
development set and quote the 399.

---

## 3. What is on the dashboard

24 strategy variants across 8 universes (4 in-sample, 4 holdout), 4 fill modes,
4 risk levels, 3 account sizes, 5 start years.

| key | what it is |
|---|---|
| `ema` | EMA 20 stack, M/W/D — the class strategy, 6 bands |
| `qmw` | EMA 20 stack, Q/M/W, 6 bands |
| `pair` | one higher timeframe: W/D, M/D, M/W, Q/W |
| `e1` | daily 20 EMA alone, no higher timeframe |
| `eath` | M/W/D, buying only within 10% of the all-time high |
| `dv` | Turtle channel: 20-10 and 55-20, each with and without the weekly gate |
| `hg` | Holy Grail — ADX + 20 EMA pullback, **both** readings of its stop |

Views: **Compare** (ranked table, the landing page), **Detail** (one row's charts
and cost waterfall), **Breadth** (how many stocks the rule needs), **Stocks**.

The universes are `all` and three liquidity buckets by median daily turnover —
`small` under ₹5 cr, `mid` ₹5–25 cr, `large` over ₹25 cr — with the same four cut
again over the holdout. The holdout buckets come out 99 large, 100 mid, 200 small.

---

## 4. What the numbers say

**Every figure in the previous handover's section 4 was stale** — some of it
invalid (built from a cache that could not see a rewritten strategy, see §6), the
rest simply superseded. The Turtle's 1.57 MAR is now 0.94 at the same cell. Do not
quote the old file.

All at 101 or 399 stocks, ₹2,00,000, 1% risk, realistic fills, from 2018.

### FIRST: the in-sample 101 is a list of winners

Established 3 September, and it reframes everything below. **The 101 are not a
fair sample of NSE. They went up far more than the market they were drawn from,
so every number measured on them is inflated before any strategy runs.**

Buy and hold — no rule, no trading — from 2018, within each liquidity bucket so
company size cannot explain it:

| bucket | set | median buy & hold | share that lost money |
|---|---|---:|---:|
| large | **101** | **+15.3%** | **3%** |
| | 399 | +9.7% | 16% |
| mid | **101** | **+11.4%** | **6%** |
| | 399 | +7.4% | 20% |
| small | **101** | **+10.5%** | 26% |
| | 399 | +6.9% | 28% |

Of the 101's 37 large caps, **one** lost money in eight years. Of the 399's 99
large caps, sixteen did. That is not a sample of large caps; it is a list of
winners.

**This is not the usual selection bias, and the distinction matters.** The
obvious explanation for the holdout collapse is that 24 variants were tried and
the luckiest reported. That was tested directly — the same best-of-24 procedure
run on 25 fresh 101-stock draws from the 399:

| | best of 24 | **median rule of 24** |
|---|---:|---:|
| the real 101 | 0.98 | **0.33** |
| fresh 101 from the 399 | 0.40 (range 0.22–0.70) | **0.09** |

Selection can only inflate the *winner*. It cannot lift the *middle of the pack*,
and the middle moved from 0.33 to 0.09. Every rule does better on the 101,
including the comparison arms nobody chose. No fresh draw reached 0.98, and the
winning variant differed on almost every draw — 13 different rules won across 25
draws, which is what noise looks like.

Both tests are reproducible: `python -m scripts.universe_bias` reruns them and
prints the two tables above.

**Where the 101 came from is recorded nowhere** — not the README, the config, the
old handover, or the commit history. The 399 by contrast were built mechanically
and it is all written down (`screen_universe`: sweep every ordinary NSE equity,
apply the gate, stratify evenly across buckets, longest history wins within
each). The fingerprint of the 101 is a watchlist assembled from stocks that were
already worth mentioning, and a stock becomes worth mentioning by having gone up.
Nobody has to intend that for it to happen.

**Consequences, and they are not small:**

- Every percentage measured on the 101 is roughly a good-stock-list too high. It
  is not a forecast. That includes the Breadth page (§4) and every figure in the
  in-sample table below.
- The Turtle's 0.95 was mostly the deck, not the rule. Its fall to −0.13 on the
  holdout is therefore much less mysterious than it looks: a good part of that
  height was never real. It fell hardest because it needs rare big winners and
  few duds, which is exactly what the 101 oversupplies.
- **What survives is any comparison between rules on the SAME stocks.** Both
  rules face the same deck, so the deck cancels. That is why the finding below —
  EMA 0.22 against Turtle −0.03 on holdout large caps — is still worth having,
  and it is the shape every future result should take.
- The 101 remains a perfectly good DEVELOPMENT set. It is not a performance
  estimate and never was.

### In-sample (101) — the Turtle leads

| MAR | rule | CAGR | drawdown |
|---:|---|---:|---:|
| 0.95 | Turtle 20-10 + weekly | 23.5% | −24.8% |
| 0.94 | Turtle 55-20 + weekly | 25.3% | −27.0% |
| 0.61 | EMA M/W pair | 23.6% | −38.5% |
| 0.58 | EMA Q/M/W, 2% band | 22.7% | −39.4% |
| 0.48 | EMA M/W/D, 2% band — *the class strategy* | 19.6% | −40.6% |
| 0.06 | Holy Grail, swing-low stop | 2.1% | −34.7% |
| −0.08 | Holy Grail, candle-low stop | −3.9% | −50.8% |

### Holdout (399) — the ranking inverts

| MAR | rule | CAGR | drawdown |
|---:|---|---:|---:|
| 0.23 | EMA Q/M/W, no band | 7.8% | −33.7% |
| 0.20 | EMA Q/M/W, 5% band | 9.4% | −46.1% |
| 0.19 | EMA Q/M/W, 1% band | 6.5% | −34.1% |
| 0.10 | Holy Grail, swing-low stop | 4.1% | −42.4% |
| −0.01 | EMA M/W/D, 2% band — *the class strategy* | −0.5% | −51.7% |
| −0.13 | Turtle 20-10 + weekly | −8.7% | −66.3% |
| −0.13 | Turtle 55-20 + weekly | −6.7% | −53.6% |

**The best in-sample rule is the worst out of sample.** Both Turtle windows sit at
the bottom of the holdout table. The rule that holds up is Q/M/W, which was
middling in-sample.

Two things say the holdout data is sound rather than broken. Trade-level
expectancy stays positive out of sample for every strategy, and the Holy Grail's
expectancy is near-identical across both (+0.276R in-sample, +0.280R out). The
accounts fail; the trades do not change character.

A matched-size control was run to rule out the obvious objection that 399 stocks
is simply a different problem from 101: **20 random 101-stock draws from the 399**.
The Turtle still fails — median −1.3%, with 60% of draws negative. Q/M/W is the
most robust at +6.2% median with only 5% of draws negative. So the gap is not
universe size.

The diagnosis is ordinary in-sample optimism. The Turtle's median trade is
−1.08R; its edge lives in a thin tail of large winners that does not reproduce on
stocks it was not tuned on.

### Breadth — how wide a watchlist each rule needs

New view, answering the question that actually matters with limited capital.
Random baskets at nine sizes, ₹2,00,000 at 1% risk, median of many draws.

| rule | 5 stocks | full 101 | enough at |
|---|---:|---:|---|
| EMA M/W/D | 10.4% | 18.0% | **15 stocks** |
| EMA one higher TF | 12.8% | 20.4% | **10 stocks** |
| EMA daily only | 13.7% | 18.8% | **10 stocks** |
| EMA Q/M/W | 8.2% | 23.8% | 50 stocks |
| Turtle channel | 2.2% | 22.0% | 50 stocks |
| Holy Grail | 0.8% | 8.3% | 50 stocks |

"Enough at" is the smallest basket whose *middling* draw came within a fifth of
the full watchlist.

**These percentages are measured on the 101, so they carry the winner bias above
and are not forecasts.** What to trust here is the *comparison* between rules and
the shape of each curve, both of which are measured on the same stocks and so are
unaffected. Rerunning the sweep on the 399 is the top open item (§10).

The number that answers "but I do not know WHICH 15" is the worst draw, not the
median. Across 30 random 15-stock baskets the class EMA stack's unluckiest draw
still made +6.2% and none lost money; the Turtle's unluckiest made +1.1%, and at
5 or 10 names its bad draws lose money outright. So breadth does not tell you
which stocks to pick — nothing can, see the forward test below — it tells you
**which rule survives you not being able to pick.**

At 15 names the class EMA stack returns 17.1% against the Turtle's 9.9%, which is
the reverse of the Compare page's ranking. The leaderboard ranks rules on a
101-stock account nobody can run.

**The class EMA stack is tradeable on about 15 names. The Turtle is not.** At five
stocks the Turtle returns 2.2% against its own 22.0% — it depends on width more
than any other rule here, which is the same tail-dependence that sinks it on the
holdout. Two separate measurements, one cause.

This view replaced the ten-stock basket panel, which published **one draw** and
called it a result. At ten names that draw sat in a band eight times its own
width; it happened to land at the 48th percentile for the EMA stack and the 22nd
for the Turtle, so it was quietly tilting the compare-page ranking. Its Monte
Carlo also cost 16m45s — 60% of the whole build — for a number the web page never
displayed.

### Other standing results

- **Two timeframes beat one, and adjacency matters.** None ≈0.17, one ≈0.44–0.61,
  two 0.48–0.58. M/D (skipping the weekly) is the weakest pair, so skipping a
  level costs something.
- **The all-time-high band hurts**, 0.48 → 0.20. A 30-stock preview had suggested
  it was neutral, which is a lesson about small samples.
- **Cash is the binding constraint, not capital.** ₹10 lakh takes about as many
  signals as ₹2 lakh — position size scales with equity, so more money buys bigger
  positions, not more of them. The lever is the risk setting.
- **Capture rate drifts down over time**, so part of any late-period result is
  account size rather than the rule.

### Is there a pattern to which stocks run? (the teacher's question)

Backward-looking, yes: buy-and-hold return correlates +0.46 with strategy success
and trendiness +0.35. Measured **forward** — rank on the first half of history,
score on the second — everything collapses to about zero or slightly negative.

The backward pattern is just "the stocks that went up, went up". It is not
usable, and that is the argument for a wide watchlist over a picked one.

---

## 5. The Holy Grail's stop — both readings are now published

Rule 6 of the class sheet says "SL will be swing low", and that phrase has two
defensible readings. Between 2 and 3 September only one was on the board, which
was wrong in three separate ways, so both are published now.

| | `candle` | `swing` |
|---|---|---|
| what it is | the signal candle's own low | last confirmed multi-bar pivot low |
| fits the sheet's 39 marked stops to | 0.80% | 5.82% |
| median stop distance, 101 stocks | 4.63% | 13.41% |
| expectancy | +0.561R | +0.404R |
| win rate | 41.1% | 57.5% |
| average win / loss | 2.95R / −1.11R | 1.31R / −0.82R |
| MAR, all history | 0.14 | 0.20 |
| MAR, from 2018 | −0.08 | 0.06 |

What was wrong with publishing only `swing`:

1. **The repo contradicted itself.** `holygrail.py` has always taken
   `stop="signal_low"` as its default and argued at length that the candle
   reading is the right one; `dashboard_data.py` published the opposite and said
   so in a comment. Both files were in the same commit.
2. **The label was false.** The page printed "stop at swing low" against the
   *pivot* variant — the exact phrase `holygrail.py` argues means the candle.
3. **The stated reason did not hold.** `swing` was kept because the candle
   reading was "fitted to a classmate's spreadsheet". But the candle reading has
   its own written support in the class notes — the DI-crossover note says
   "STOPLOSS: signal candle low" — so it is a reading, not a curve fit.

**Neither rescues the rule.** They produce structurally different trades from
identical signals and both lose at account level, so showing both cannot be
cherry-picking a winner: there is not one. The choice is a question about what
the class meant, not about which number to quote.

The Holy Grail is genuinely bad here. But note *why*, because it is not the
entry: trade-level expectancy is positive under both readings. The account loses
to friction — trades too small to place, signals skipped for cash, and costs
eating a third of gross profit.

---

## 6. Bugs found, and what they teach

**The cache could not see a rewritten strategy.** `signals.py` hashes the
producer modules into every cache stamp, and `holygrail.py` was not on the list.
The rules were rewritten at 16:33 on 2 September; the dashboard built at 15:32
went on serving trades from the superseded code, and `refresh` reported "already
current" because the digest it compared could not see the file. Every Holy Grail
number published before this was invalid — including the "MAR ~0.21 under every
reading" in the last handover. `slippage.py` and `timeframes.py` were missing
too. The stamp digest went `b14babb788866032` → `64f840fbac76ce5e`.

**A deleted block took a shared import with it.** Removing the basket Monte Carlo
removed a local `import random` that the breadth sweep also used. It crashed 20
minutes in, after the grid had completed and been discarded. `import random` is
now at module level with a comment saying why.

Earlier, and still worth knowing:

- **₹52 positions.** Risk-based sizing capped by cash produced positions the flat
  ₹15.34 DP charge ate 29% of. 1,972 unplaceable trades were polluting every
  statistic. Now refused when a round trip costs more than 0.5% of the position.
- **The Turtle stopped at the channel low, not the entry candle's low** — 14.2%
  below entry against 2.9%, so positions were a fifth of the right size.
- **MAR was computed and never put in the payload**, and the compare page sorted
  on it. Caught by dry-running the page against the finished file.
- **`parse_qs` was never imported**, so `/api/curve` returned 500 on every
  request. The module imported fine because the name is only looked up when the
  route runs.
- **numpy leaking into JSON.** `round()` on a numpy scalar stays numpy, and numpy
  2 renders its class name as plain `bool` — so a 25-minute run died on "Object
  of type bool is not JSON serializable".

The pattern: **compiling is not working.** Most of these passed every static
check. The substitute is to dry-run the page against the built file, which is how
the MAR bug was found and how the Breadth view and both Holy Grail rows were
verified. That harness is now `scripts/check_dashboard.js` — run it after every
rebuild.

---

## 7. Traps that cost time

- **The code stamp uses file MTIMES, not contents.** Editing a docstring in any
  module listed in `signals._CODE` invalidates every signal cache and forces the
  full rebuild. So does anything else that touches mtimes — a `git checkout`, a
  fresh clone, copying the tree. It is the safe direction to be wrong in, but
  budget the 16 minutes.
- **The dashboard server binds the container's loopback.**
  `dashboard_server.serve()` hardcodes `127.0.0.1` with no host parameter. Started
  from inside the dev container it answers `curl` there and is unreachable from
  the host browser — published ports forward to the container's external
  interface, not its loopback, so `-p 8765:8765` does not help either. **Run
  `./run_dashboard.sh` on the host.** It is stdlib-only and needs no venv.
  Verifying a server with `curl` from the same container proves only that the
  process is alive.
- **`pkill -f "scripts.dashboard"` matches its own shell** and kills the session.
  Use PIDs. `pgrep -a -f` has the same problem and will report a server that is
  not running.
- **`/tmp` gets cleared**, including the scratchpad, between sessions. Long-running
  logs go in `output/`.
- **Small samples mislead.** The ATH band looked neutral on 30 stocks and clearly
  harmful on 101.
- **`clean_data` used to rewrite identical files**, moving mtimes and invalidating
  every stamp. Fixed; it now skips unchanged files.
- **The class spreadsheets are not backtests.** See §9.

---

## 8. What cannot be rebuilt, and where it now lives

Almost everything here is reproducible from the raw store. Four things were not,
and all four were untracked until 3 September:

- `levels.json` — hand-drawn, nothing regenerates it.
- `accepted.txt` — the 399 screened names. `screen_universe` would rewrite it, but
  against a *newer* instrument dump and data snapshot, so a rerun silently
  redefines the holdout rather than restoring it.
- **The split itself** — `[universe] symbols` / `holdout` / `unseen` lived only in
  `config.local.toml`, which is gitignored *because the same file holds
  `api_key` and `api_secret`*. The definition of the experiment was sharing a
  hiding place with the secrets and inheriting their absence from git.
- `.gitignore` claimed levels.json was committed, under a bare `data/` line that
  ignored the whole directory. The promise and the rule contradicted each other.

`python -m scripts.backup_inputs` copies all of it into `data/keep/`, which is now
the one part of `data/` that git tracks. It refuses to overwrite a backup with a
smaller one unless given `--force`, so a half-written config cannot quietly eat
the holdout list. Nothing secret is copied — the universe lists are read through
`config.load()` and written as plain JSON, so `[kite]` never leaves
`config.local.toml`. `scripts.refresh` runs it as its last step, because a backup
that depends on remembering is not one.

---

## 9. The class spreadsheets

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
high while losers run the full stop. Trailing the whole position instead of
scaling out confirms this directionally, but only moves the Holy Grail's MAR from
0.06 to 0.10. It is a real diagnosis and a small rescue.

When comparing against classmates' results, the differences are almost entirely
method: one hand-picked stock, no costs, no cash constraint, trade-level not
account-level, and setups marked by eye on a chart already visible.

---

## 10. Open items

**Settled since the last handover** — the 399 are wired in as four holdout
universes; the ten-stock basket is gone and Breadth replaces it; both Holy Grail
stops are published; the unreproducible inputs are backed up.

**Open, roughly in order of value**

- **Rerun the breadth sweep on the 399.** It is computed on `cfg.all_symbols`
  only, so its levels carry the winner bias. The question it answers — how few
  names can be traded before a rule stops working — is the one a real account
  asks, and it currently has an inflated answer. `breadth` in
  `scripts/dashboard_data.py` takes a symbol list; the work is passing it the
  holdout and giving the page a second series.
- **Decide what the 101 is for.** It is a fine development set and a bad
  performance estimate. Either document how it was built and accept it as
  development-only, or rebuild the in-sample side the way the 399 was built —
  mechanically, from `screen_universe`, with the provenance written down. The
  present state, where the headline universe has no recorded origin, is the thing
  that produced today's surprise.
- **The holdout has been read once. Do not read it again casually.** The result in
  §4 is the honest test, and it says the Turtle does not travel. Every further
  look costs a little of what makes it a holdout.
- **Extend `scripts/check_dashboard.js`.** It now runs the real
  `web/dashboard.html` JS over the real `dashboard.json` under a stub DOM and
  checks every view renders — it is the only test in the project. It asserts
  nothing about the *numbers*, so a payload that renders beautifully with
  every MAR silently null still passes. Worth adding: a floor on how many grid
  cells are non-null, and a check that the holdout grid contains nothing outside
  `holdout_axes`.
- `dashboard_server.serve()` needs an opt-in `--host` flag if the dashboard is
  ever to be served from the container. Default must stay `127.0.0.1`.
- STT is a single constant; delivery STT is believed to have changed around June
  2013. `STT_SCHEDULE` in `backtest.py` is the mechanism, deliberately holding one
  entry. Cost of leaving it: ~1.2% of gross. Verify the rate before enabling.
- Survivorship: the universe comes from the *current* instrument list, so delisted
  names are invisible. An Indian smallcap reconstruction puts the inflation at
  ~4.9 percentage points a year. This is the largest known bias in every number
  above and nothing in the project corrects for it.
- Retire settled variants. The ATH band clearly hurts and `e1` has made its point;
  both still cost a full share of every rebuild.
- Drop the third capital (₹3L) — capital barely moves the result.
- 40 dead cache files are stranded in the read-only raw mount.
- `scripts/portfolio.py` still tests ATH Breakout, which was ruled out. It is the
  only reason `Breakout_101` and `rebuild_orphans.py` still exist.
- The four Holy Grail trend readings (`di`, `hl`, `hh`, `hhhl`) are in the code
  but not on the dashboard.

**Rejected, with reasons.** More cores: the work is single-threaded and
embarrassingly parallel, but the realistic ceiling is 4–5× (heterogeneous Apple
cores, a fanless Air that throttles, ~1 GB per worker), and parallelising the
global fill-mode state risks plausible wrong numbers. Rust: it prevents memory
errors and data races, none of which this project has had — every bug in §6 was a
logic error, which Rust compiles happily.

---

## 11. Pine scripts

- `pine/ema_ath_band.pine` — indicator. 20 EMA plus the all-time-high band, on
  whatever timeframe the chart is on. Everything bar-anchored.
- `pine/ema_near_high.pine` — the multi-timeframe strategy the backtest mirrors.
- `lookahead_off` on every higher-timeframe call. Without it, Tuesday gets
  Friday's weekly EMA and the backtest is a lie.
- **The readout table was welded to the screen.** `table.new(position.top_right)`
  anchors to the viewport, not the bars.

---

## 12. How to run things

```bash
# view — no keys, no venv, and run it on the HOST, not in the container
./run_dashboard.sh

# rebuild whatever is stale — no keys, needs the venv
./.venv/bin/python -m scripts.refresh
./.venv/bin/python -m scripts.refresh --check    # report only

# back up the files nothing can rebuild (refresh does this automatically)
./.venv/bin/python -m scripts.backup_inputs

# fetch new data — needs the account
set -a; source ~/.secrets/all.env; set +a
./.venv/bin/python -m scripts.login
./.venv/bin/python -m scripts.backfill --symbols-file <list> --daily-only

# does the page still work with the file just built? (needs node, not the venv)
node scripts/check_dashboard.js

# is the in-sample universe a fair sample? (see section 4 -- it is not)
./.venv/bin/python -m scripts.universe_bias

# health
./.venv/bin/python -m scripts.cache_status
```

Everything is stamped: caches and the dashboard record the universe, price files
and strategy code they were built from, and say so when any of the three moves.
Silence means verified, never "did not look."

# NEXT TESTS — kitelab

State as of 2026-09-24. Three jobs ran this session (waterfall, Raschke,
short side). Read the three findings below before proposing anything new.

---

## 1. The waterfall: where the money dies  (`scripts/waterfall.py`)

Nine rungs, same universe / period / rules, all reported as account CAGR so
they subtract. Rungs: A random, A0 random no-stop, B rule, X rule no-stop,
C rule+costs, D0 +20 slots, D1 +10 slots, **E random+costs+20 slots**, F hold.

**Finding 1 — the entry is worth nothing without the stop.**

| layer (median of 26 arms, points of CAGR/yr) | US | NSE |
|---|---|---|
| entry is worth, NO stop on either side | +0.06 (13/26) | −1.08 (8/26) |
| entry is worth, board stop on BOTH sides | +8.68 (26/26) | +10.28 (23/26) |
| the board's stop costs the RULE | −3.45 | −1.37 |
| the board's stop costs RANDOM timing | −12.75 | −12.47 |

The rule does not pick better entries. It picks entries the board's stop
damages less. Remove the stop from both sides and the edge is zero.
Consistent with `kitelab-exit-beats-entry-3to1`.

**Finding 2 — the NSE "beat hold by +16.70" was the fill cap, not the rule.**
DIAGNOSED AND CLOSED. Two controls settled it:

| NSE, median CAGR | cap ON (1%) | cap OFF |
|---|---|---|
| C rule+costs, unlimited | 12.01 | 12.01 |
| D0 rule+costs, 20 slots | **34.92** | **10.01** |
| E RANDOM+costs, 20 slots | **29.35** | −0.33 |
| F buy and hold | 18.22 | 18.22 |

The 20-slot limit alone does nothing (cap off: 10.01, below C and below hold).
The lift is entirely the 1% fill cap, which is a **liquidity screen wearing a
friction's clothes** — it refuses to size up in thin names and leaves the money
in cash. On NSE small caps in a market that fell 73.5% that is worth ~+20
points; on US large caps it is worth −5. Random entries get the same screen
and the same lift (29.35%), so the ENTRY is worth D0−E = **+6.19**, not +16.70.

Honest final lines: **US −10.53 vs hold (0/26). NSE +16.70 vs hold but only
+6.19 vs a friction-matched random control**, and even that compares a
half-cash liquidity-screened book against an all-in benchmark.

NEXT: the only live question here is whether an explicit liquidity screen
(rank by turnover, hold the top N) beats hold on its own, entry rules deleted.
Rung E says it might. That is a NEW hypothesis, not a rule result.

---

## 2. Raschke, *Street Smarts* — 8 untested setups  (`scripts/raschke_rules.py`)

10 setups × 2 stops × 2 universes × 2 max-holds = 80 rows, trade-level,
year-clustered t. **10 of 80 clear Bonferroni |t| ≥ 3.70 and all 10 positive;
32 of 80 clear an uncorrected |t| ≥ 2.0 where 4 are expected, 30 positive.**

Three caveats that must travel with the number:
1. TRADE LEVEL ONLY. `kitelab-account-layer-reverses-stop-finding` says never
   promote one. The waterfall above is exactly why.
2. The board's OWN entries score +35 to +100 bps in the same test and still
   lose at the account layer. Raschke is MATCHING our rules, not beating them.
3. Per-trade bps grows mechanically with holding time, so hold-60 is not
   comparable to hold-6. **hold-6 is the column that tests her actual method**,
   and only 4 of the 10 survivors live there: NSE anti (both stops),
   US tsoup1|atr3, US whiplash|own.

2-Period ROC is deliberately absent — a daily proxy would be our invention
wearing her name. Her entries are intraday stop orders; we enter at the close.

NEXT: put `anti` and `whiplash` through `waterfall.py` at hold 6. If they die
at the account layer like everything else, the Raschke thread is closed.

---

## 3. The short side  (`scripts/short_rules.py`)

5 mirror rules + 3 neutral, both stops, both universes = 32 rows.
**7 of 32 clear Bonferroni |t| ≥ 3.39, 6 of them positive.**

But positive means "loses LESS than a random-timed short". Shorts lose money
outright — stocks drift up. Borrow fees, recall risk and the NSE cash-market
overnight-short ban are NOT modelled, so these are the BEST case. A rule that
fails here fails a fortiori.

The mirror rules that clear: US high252 (both stops), US rsi70 (both stops),
US mr_hi|own. The strongest rows are the NEUTRAL setups (US vcon|own t=8.4,
US inside|own t=7.5), which says nothing about a bearish edge.

NEXT: nothing, until someone prices borrow. Do not build on this.

---

## Still open from earlier sessions
- NSE gap-fill base rate for `gap` / `gapdn`.
- Backfill second pass stalled at 370 of 1,000 stale.
- `CLAUDE.md` still says the board is 20 rows; it is 36.
- Whether to migrate `fixed_rules.py` into `kitelab/entries.py` (103–178 min
  rebuild — the reason it has not happened).

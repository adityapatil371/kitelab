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

**Finding 2 — the NSE "beat hold by +16.70" was a LOOKAHEAD in this script.**
FOUND AND FIXED 2026-09-24, same day it was written.

`account()` sized every position against `turn[sym][t]` — the close x volume of
the **same bar it then earned `rets[sym][t]` on**. An order placed at the close
of t-1 cannot know bar t's traded value. The effect: a name was held at full
size exactly on its heavy days and shrunk on its quiet ones, and in NSE small
caps volume and same-day return correlate enough to manufacture the whole
result. Fixed with `CAP_LAG` / `--caplag 1`, which measures the cap against
bar t-1. Nothing else changed.

| median of 26 arms, points of CAGR/yr | US peek | US honest | NSE peek | NSE honest |
|---|---|---|---|---|
| capping at 20 positions costs | −5.58 | −7.26 | **+22.45** | **−8.49** |
| the same cap+costs is worth to RANDOM | −4.21 | −5.21 | **+27.01** | **−4.03** |
| FINAL: rule minus matched RANDOM | +4.53 | +4.39 (25/26) | +6.19 | +5.72 (22/26) |
| FINAL: rule minus buy and hold | −10.53 (0/26) | **−12.75 (0/26)** | **+16.70 (26/26)** | **−14.55 (0/26)** |

With the honest cap the two markets agree and the India exception is gone: the
fill cap is a **cost** in both, as a friction should be, and **0 of 52 arms beat
buy and hold**. What survives is real but small — the entry beats a
friction-matched random entry by +4.39 / +5.72, in 47 of 52 arms. It is not
enough to reach hold.

The main engine was never affected. `kitelab/slippage.py:92` builds ADV as a
rolling median `.shift(1)` and carries a docstring about this exact trap, and
`scripts/wf_intraday.py:172` mirrors it. `waterfall.py` was the one place that
hand-rolled the cap and skipped the shift. The 36-row board, the
"nine are worse than hold" result and the dashboard all stand.

**Finding 2b — the liquidity screen is dead** (`scripts/liquidity_screen.py`).
Built to test whether the cap's apparent lift was a real screen. It is not, and
it fails in the opposite direction to the story:

| CAGR | US | NSE |
|---|---|---|
| hold everything | 16.92 | 18.05 |
| top 20 by turnover | 15.36 | **9.69** |
| 20 at random | 16.70 | 17.78 |
| bottom 20 by turnover | **22.01** | **27.06** |
| capped, random names | 6.06 | 8.91 |
| cash-matched, no tilt | 7.58 | 8.96 |

Ranking content (top − random) = −1.34 US, −8.09 NSE: worse than a coin flip.
The tilt itself (capped − cash-matched) = −1.53 / −0.05: holding cash was the
entire effect. Concentration (random 20 − hold all) = −0.22 / −0.27: free, not
an edge. The only real pattern runs the other way — the least-traded names beat
hold by 5 (US) and 9 (NSE) points, which is the size/illiquidity premium and is
not tradeable at size by construction. Holds in all three NSE decades.

This script is also what caught the bug: it lagged turnover correctly from the
start and returned 8.91% where the waterfall claimed 28.6–34.9% for the same
idea. **A disagreement between two implementations of one mechanism is
evidence. Chase it.**

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

**RAN 2026-09-24 — the thread is CLOSED** (`scripts/raschke_account.py`).
All 10 setups x 2 stops x 2 universes through the waterfall's account at her
own hold of 6, with the honest cap (`--caplag 1`):

| account layer, hold 6 | US | NSE |
|---|---|---|
| beat buy and hold | **0 of 20** | **0 of 20** |
| of the 4 named on trial BEFORE the run | 0, median −21.21 | 0, median −19.24 |
| best row | eighty20/atr3, −16.97 | anti/atr3, −19.08 |
| buy and hold | 16.98%/yr | 18.22%/yr |

The four were named in the script before it ran, so this is not a post-hoc
reading. `anti` — the strongest row in the whole trade-level table — is
−19.08 against hold. Under the *peeking* cap NSE printed 12 of 20 beating hold
and `anti` at +16.97; that was the same lookahead as Finding 2, and it is the
only reason her setups ever looked different from ours.

Her setups behave exactly like the board's own: a real per-trade edge that the
account layer does not pay for. Nothing left to test here.

---

## 3. The short side  (`scripts/short_rules.py`)

5 mirror rules + 3 neutral, both stops, both universes = 32 rows.
**7 of 32 clear Bonferroni |t| ≥ 3.39, 6 of them positive.**

But positive means "loses LESS than a random-timed short". Shorts lose money
outright — stocks drift up. Borrow fees, recall risk and the NSE cash-market
overnight-short ban are NOT modelled, so these are the BEST case. A rule that
fails here fails a fortiori.

The mirror rules that clear: US high252 (both stops), US rsi70 (both stops).
That is TWO rules, not three. An earlier draft of this line also listed
US mr_hi|own; it does not clear -- t = 3.16 against a 3.39 threshold. It
clears the uncorrected |t| >= 2.0 only, which is the bar 4 of 32 rows are
expected to clear by chance. Corrected 2026-09-24 against
`output/measurements/short_rules_2026-09-24.csv`.

And every one of the 7 clearers has a NEGATIVE rule_bps -- the best,
US high252|own, loses 70.9 bps a trade against a random short's 105.0. Zero of
them make money. The strongest rows are the NEUTRAL setups (US vcon|own t=8.4,
US inside|own t=7.5), which says nothing about a bearish edge.

NEXT: nothing, until someone prices borrow. Do not build on this.

---

## Still open from earlier sessions
- NSE gap-fill base rate for `gap` / `gapdn`.
- Backfill second pass stalled at 370 of 1,000 stale.
- `CLAUDE.md` still says the board is 20 rows; it is 36.
- Whether to migrate `fixed_rules.py` into `kitelab/entries.py` (103–178 min
  rebuild — the reason it has not happened).

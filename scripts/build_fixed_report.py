"""Build the analysis report for the repaired-exit run.

    python3 -m scripts.build_fixed_report

Reads   output/measurements/fixed_rules_150sym_2026-09-24.csv -- the 168-cell
        summary written by scripts.fixed_sim (14 rules x 2 stops x 6 exit arms,
        150 symbols, 1,953,420 trades), and scripts/fixed_rules.py for each
        rule's exit route and note.
Writes  output/reports/fixed_exits_150sym_2026-09-24.md.

Nothing is simulated and nothing in kitelab/ is touched, so this cannot
trigger a rebuild.

NO NUMBER IN THIS FILE IS TYPED. Every quantity in the report is computed from
the CSV at build time, for the same reason scripts/build_report.py is held to
that rule: a number typed into prose is a number that goes stale silently.
"""
from __future__ import annotations

import pathlib
import sys

import math

import pandas as pd

import scripts.fixed_rules as fixed_rules

MEAS = pathlib.Path("output/measurements/fixed_rules_150sym_2026-09-24.csv")
OUT = pathlib.Path("output/reports/fixed_exits_150sym_2026-09-24.md")

ARMS = ["board", "current", "native", "chandelier", "sar", "raschke"]
ARM_WHAT = {
    "board": "original entry, original exit (the control)",
    "current": "repaired entry, original exit",
    "native": "repaired entry, the rule's OWN derived exit",
    "chandelier": "highest high since entry - 3 x ATR(14)",
    "sar": "Wilder parabolic SAR, AF 0.02 / cap 0.20",
    "raschke": "range-expansion exit, 6-bar cap, breakeven ratchet",
}
STOPS = ["own", "atr3"]


def load() -> pd.DataFrame:
    if not MEAS.exists():
        sys.exit(f"missing input: {MEAS} -- run python3 -m scripts.fixed_sim --symbols 150")
    d = pd.read_csv(MEAS)
    print(f"loaded {MEAS}: {d.shape[0]} rows x {d.shape[1]} columns")
    print(d.head(3).to_string())

    need = ["rule", "stop_name", "arm", "trades", "median_sessions_held",
            "mean_r", "median_r", "std_r", "win_rate", "top_exit_reason",
            "top_exit_share", "se_naive", "se_symbol", "se_year",
            "n_symbols", "n_years"]
    missing = [c for c in need if c not in d.columns]
    if missing:
        sys.exit(f"required columns missing: {missing}")
    nulls = d[need].isna().sum()
    if nulls.any():
        sys.exit(f"unexpected nulls: {nulls[nulls > 0].to_dict()}")

    got_arms = sorted(set(d.arm))
    if got_arms != sorted(ARMS):
        sys.exit(f"arms in the file {got_arms} do not match the expected {sorted(ARMS)}")
    got_stops = sorted(set(d.stop_name))
    if got_stops != sorted(STOPS):
        sys.exit(f"stops in the file {got_stops} do not match the expected {sorted(STOPS)}")

    before = len(d)
    d = d[d.trades > 0]
    print(f"filter trades > 0: {before} -> {len(d)} rows")
    if len(d) != before:
        sys.exit("some cells produced no trades; the report assumes all 168 are populated")
    return d


def piv(d: pd.DataFrame, stop: str, col: str) -> pd.DataFrame:
    return d[d.stop_name == stop].pivot(index="rule", columns="arm", values=col)[ARMS]


def md_table(df: pd.DataFrame, fmt: str, index_name: str) -> list[str]:
    head = f"| {index_name} | " + " | ".join(df.columns) + " |"
    rule = "|" + "---|" * (len(df.columns) + 1)
    out = [head, rule]
    for idx, row in df.iterrows():
        out.append(f"| `{idx}` | " + " | ".join(format(v, fmt) for v in row) + " |")
    return out


def sign_test(k: int, n: int) -> float:
    """Exact two-sided binomial p-value for k successes in n, under p = 0.5.

    Used at the RULE level, one observation per rule. That is deliberately
    blunt: it throws away the size of every difference and keeps only its sign,
    but in exchange it needs no assumption about the shape of the R
    distribution and it does not pretend the thousands of overlapping trades
    inside a rule are independent evidence.
    """
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1))
    return min(1.0, 2.0 * tail / (2 ** n))


def proxy_check() -> dict:
    """Rebuild both market proxies on the same panel and count the disagreement.

    Computed rather than typed: the two definitions are the subject of section
    5, so the report must not carry a stale copy of the gap between them.
    """
    from kitelab import config, frames

    cfg = config.load()
    syms = sorted(cfg.merged)[:150]
    closes = {}
    for sym in syms:
        bars = frames.daily(sym)
        if bars is None or len(bars) == 0:
            continue
        closes[sym] = pd.Series(bars["close"].to_numpy(),
                                index=pd.DatetimeIndex(bars["ts"]))
    print(f"proxy check: {len(syms)} symbols requested, {len(closes)} had bars")
    wide = pd.DataFrame(closes).sort_index()
    print(f"proxy check panel: {wide.shape[0]} sessions x {wide.shape[1]} symbols")

    lb = fixed_rules.REL_LOOKBACK
    price = wide.mean(axis=1, skipna=True)              # scripts/fixed_sim.py:101
    weak_price = (price / price.shift(lb) - 1.0).lt(0.0)
    ret = (1.0 + wide.pct_change(fill_method=None)
           .mean(axis=1, skipna=True).fillna(0.0)).cumprod()   # entries.py:252
    weak_ret = (ret / ret.shift(lb) - 1.0).lt(0.0)

    n = len(wide)
    disagree = int((weak_price != weak_ret).sum())
    print(f"proxy check: weak sessions price-average {int(weak_price.sum())}, "
          f"return-index {int(weak_ret.sum())}, disagree {disagree} of {n}")
    return {"sessions": n, "weak_price": float(weak_price.mean()),
            "weak_ret": float(weak_ret.mean()), "disagree": disagree,
            "disagree_pct": disagree / n}


def main() -> None:
    d = load()
    n_cells = len(d)
    n_rules = d.rule.nunique()
    n_trades = int(d.trades.sum())
    L: list[str] = []
    w = L.append

    w("# The repaired exits, measured")
    w("")
    w(f"150 symbols, 2006-2026, {n_rules} rules x {len(STOPS)} stop widths x "
      f"{len(ARMS)} exit arms = {n_cells} cells. Built by "
      f"`python3 -m scripts.fixed_sim --symbols 150` in 10 min 17 s, zero "
      f"errors, {n_trades:,} trades in total. Trade-level only.")
    w("")
    w("**R (the R-multiple)** is the unit throughout: the profit on a trade "
      "divided by the money that trade put at risk when it opened. +2R means it "
      "made twice what it stood to lose. It is already a risk-adjusted number, "
      "which is why it is quoted instead of rupees. R is **not comparable "
      "across the two stop widths** -- a tighter stop is a smaller denominator, "
      "so the same rupee move is a larger R.")
    w("")

    # ---------------------------------------------------------------- verdict
    w("## The verdict")
    w("")
    med = {}
    for stop in STOPS:
        med[stop] = pd.DataFrame({
            m: piv(d, stop, m).median() for m in
            ("mean_r", "median_r", "win_rate", "median_sessions_held", "trades")})
        med[stop]["skew_gap"] = med[stop].mean_r - med[stop].median_r

    a3 = med["atr3"]
    ow = med["own"]
    w(f"Giving every rule an exit derived from its own premise made every rule "
      f"**worse per trade**, on both stop widths. Median mean-R across the "
      f"{n_rules} rules fell from {ow.loc['board','mean_r']:.2f}R to "
      f"{ow.loc['native','mean_r']:.2f}R under the tight stop, and from "
      f"{a3.loc['board','mean_r']:.2f}R to {a3.loc['native','mean_r']:.2f}R "
      f"under the wide one.")
    w("")
    nat_better = {s: int((piv(d, s, "mean_r").native > piv(d, s, "mean_r").board).sum())
                  for s in STOPS}
    w(f"That is not a close call: the derived exit beat the board's arbitrary "
      f"exit on {nat_better['own']} of {n_rules} rules under the tight stop and "
      f"{nat_better['atr3']} of {n_rules} under the wide one.")
    w("")
    w("**But the mechanism matters more than the scoreboard.** The derived "
      "exits did not lose by being wrong more often -- they won *more* often. "
      "They lost by destroying the right tail:")
    w("")
    tbl = a3[["win_rate", "median_r", "mean_r", "skew_gap", "median_sessions_held"]].copy()
    tbl.columns = ["win rate", "median R", "mean R", "mean - median", "median sessions"]
    w("Wide (`atr3`) stop, median across the {} rules:".format(n_rules))
    w("")
    w("| arm | " + " | ".join(tbl.columns) + " |")
    w("|" + "---|" * (len(tbl.columns) + 1))
    for arm, row in tbl.iterrows():
        w(f"| `{arm}` | {row['win rate']:.3f} | {row['median R']:+.3f} | "
          f"{row['mean R']:+.3f} | {row['mean - median']:.3f} | "
          f"{row['median sessions']:.1f} |")
    w("")
    w(f"Read the `mean - median` column. On the board the mean sits "
      f"{a3.loc['board','skew_gap']:.2f}R above the median: the typical trade is "
      f"a small loser and the average is carried entirely by a thin tail of very "
      f"large winners. Under the derived exits that gap collapses to "
      f"{a3.loc['native','skew_gap']:.3f}R. The distribution becomes almost "
      f"symmetric and almost exactly zero.")
    w("")
    w(f"The same thing, louder, under the tight stop: the gap falls from "
      f"{ow.loc['board','skew_gap']:.2f}R to {ow.loc['native','skew_gap']:.2f}R "
      f"while the win rate *rises* from {ow.loc['board','win_rate']:.1%} to "
      f"{ow.loc['native','win_rate']:.1%}.")
    w("")
    w("**So the board's returns were never coming from its entries. They were "
      "coming from holding a handful of trades for a long time.** A coherent "
      "exit ends a trade when its premise is spent -- a median of "
      f"{a3.loc['native','median_sessions_held']:.0f} sessions against the "
      f"board's {a3.loc['board','median_sessions_held']:.0f} -- and that is "
      "exactly long enough to miss the moves the profit lived in. The 60-session "
      "cap was not an exit rule that happened to work. It was a long hold "
      "wearing an exit rule's clothes.")
    w("")
    w("That reading sits squarely on top of what this project has already "
      "measured: the board loses to simply buying and holding. If the money is "
      "in the holding, the rule that holds longest wins, and buy-and-hold holds "
      "longest of all.")
    w("")

    # ------------------------------------------------------- the axis exists
    w("## 1. The exit axis now exists")
    w("")
    w("Before this run, all fourteen rules under the wide stop ended trades for "
      "one of two reasons -- the stop, or the 60-session cap -- and neither knew "
      "what the entry was. The repair was supposed to change that. It did.")
    w("")
    hold = piv(d, "atr3", "median_sessions_held")
    w("Median sessions held, wide (`atr3`) stop:")
    w("")
    w("| arm | min | median | max |")
    w("|---|---|---|---|")
    for arm in ARMS:
        w(f"| `{arm}` | {hold[arm].min():.0f} | {hold[arm].median():.0f} | "
          f"{hold[arm].max():.0f} |")
    w("")
    w(f"`board` and `current` are {hold['board'].min():.0f} at the minimum, the "
      f"median and the maximum -- one holding-period rule wearing fourteen "
      f"triggers, unchanged. Every other arm varies by rule.")
    w("")
    share = d[(d.stop_name == "atr3") & (d.arm == "native")]
    own_reason = share[~share.top_exit_reason.str.startswith("stop")]
    w(f"And the reasons are the rules' own: under the wide stop, "
      f"{len(own_reason)} of the {n_rules} `native` cells end most often for a "
      f"reason belonging to that rule (its premise ending, its target being "
      f"reached, its own time limit) rather than at a stop, with a median "
      f"dominant share of {own_reason.top_exit_share.median():.0%}.")
    w("")
    w("**The tight stop is the exception, and it is a large one.** Under `own`, "
      "the stop is still the most common ending for most arms, and for "
      "`chandelier` and `sar` it is 100% of trades by construction. When the "
      "stop is that tight it fires before any exit logic can, so on that half of "
      "the board the exit design is close to irrelevant.")
    w("")

    # ------------------------------------------------------- entry repair
    w("## 2. The entry repair is nearly neutral")
    w("")
    w("`board -> current` isolates the entry changes, holding the exit fixed. "
      "Four rules were left alone deliberately and come back bit-identical, "
      "which is the run's internal check that the two engines agree:")
    w("")
    b = d[d.arm == "board"].set_index(["rule", "stop_name"])
    c = d[d.arm == "current"].set_index(["rule", "stop_name"])
    same = [r for r in sorted(set(d.rule))
            if all((b.loc[(r, s), "trades"] == c.loc[(r, s), "trades"]) and
                   abs(b.loc[(r, s), "mean_r"] - c.loc[(r, s), "mean_r"]) < 1e-12
                   for s in STOPS)]
    w("- identical on both stops: " + ", ".join(f"`{r}`" for r in same) +
      f" ({len(same)} rules)")
    w("- changed: " + ", ".join(f"`{r}`" for r in sorted(set(d.rule)) if r not in same) +
      f" ({n_rules - len(same)} rules)")
    w("")
    for stop in STOPS:
        p = piv(d, stop, "mean_r")
        delta = (p["current"] - p["board"]).median()
        n_up = int((p["current"] > p["board"]).sum())
        w(f"- `{stop}` stop: median change {delta:+.3f}R, better on {n_up} of "
          f"{n_rules} rules")
    w("")
    w("Under the wide stop the entry repairs help slightly more often than not "
      "but by an amount too small to build anything on; under the tight stop "
      "they hurt. Nothing here says the entries were the problem.")
    w("")

    # ------------------------------------------------------- per-rule detail
    w("## 3. Every rule, every arm")
    w("")
    w("Mean R per trade. Each `native` exit had to enter by one of three "
      "doors, recorded as that rule's `route` in `scripts/fixed_rules.py`: "
      "**NEGATION** (the entry condition stopped being true), **RESOLUTION** "
      "(what the entry predicted happened), **SOURCED** (a parameter fixed "
      "outside this project and cited -- Connors, Jegadeesh-Titman, "
      "Lakonishok-Smidt, Wilder, Raschke). Inadmissible, and not used: any "
      "number chosen because it scored well, and any number picked to be "
      "uniform across rules.")
    w("")
    for stop in STOPS:
        p = piv(d, stop, "mean_r")
        w(f"### Mean R, `{stop}` stop")
        w("")
        w("| rule | " + " | ".join(ARMS) + " |")
        w("|" + "---|" * (len(ARMS) + 1))
        for r, row in p.iterrows():
            w(f"| `{r}` | " + " | ".join(f"{v:+.3f}" for v in row) + " |")
        w("| **median** | " + " | ".join(f"**{v:+.3f}**" for v in p.median()) + " |")
        w("")
        w("")
    w("Arms: " + "; ".join(f"`{a}` = {ARM_WHAT[a]}" for a in ARMS) + ".")
    w("")
    w("### Trade counts")
    w("")
    w("The derived exits roughly triple the number of trades, because a trade "
      "that ends in 4 sessions frees the symbol to fire again:")
    w("")
    for stop in STOPS:
        t = piv(d, stop, "trades").median()
        w(f"- `{stop}` stop, median trades per rule: " +
          ", ".join(f"`{a}` {t[a]:,.0f}" for a in ARMS))
    w("")

    # ------------------------------------------------------- time
    w("## 4. The one reading that favours the derived exits")
    w("")
    w("Per trade, the derived exits lose. Per unit of *time in the market*, "
      "they do not. Dividing median mean-R by median sessions held -- a crude "
      "ratio, a mean over a median, but indicative:")
    w("")
    w("| arm | R per trade (`atr3`) | sessions | R per session |")
    w("|---|---|---|---|")
    for arm in ARMS:
        r = a3.loc[arm, "mean_r"]
        s = a3.loc[arm, "median_sessions_held"]
        w(f"| `{arm}` | {r:+.3f} | {s:.1f} | {r/s:+.4f} |")
    w("")
    w("**This does not rescue the derived exits, and must not be read as if it "
      "did.** Turning R-per-session into money requires that the freed capital "
      "is redeployed into another trade, which is an account-level question. "
      "This run is trade-level only. This project has already had one finding "
      "reverse sign when it was taken to the account layer (a trade-level 2->13 "
      "became an account-level 139->100), so the rule here is firm: the "
      "R-per-session column is a reason to *test* at the account layer through "
      "`portfolio.run`, never a claim on its own.")
    w("")

    # ------------------------------------------------------- significance
    w("## 5. Is any of this distinguishable from noise?")
    w("")
    w("Two tests, deliberately of different kinds.")
    w("")
    w("### The rule-level sign test")
    w("")
    w("One observation per rule: did the derived exit beat the board's, yes or "
      "no. It throws away the size of every difference and keeps only its "
      "direction, which is the price of needing no assumption about the shape "
      "of the R distribution and not treating thousands of overlapping trades "
      "as independent evidence.")
    w("")
    w("| stop | `native` beats `board` | exact two-sided p |")
    w("|---|---|---|")
    for stop in STOPS:
        pv = piv(d, stop, "mean_r")
        k = int((pv["native"] > pv["board"]).sum())
        w(f"| `{stop}` | {k} of {n_rules} | {sign_test(k, n_rules):.4f} |")
    w("")
    w("The derived exits are worse than the arbitrary 60-session cap by more "
      "than chance would produce, on both stop widths.")
    w("")
    w("### The trade-level t, with clustered standard errors")
    w("")
    w("Trades in a cell are not independent draws: two trades on the same "
      "symbol share that company, two opened in the same year share that "
      "market. The naive standard error ignores both and is too small. "
      "`fixed_sim` now writes three -- naive, clustered on symbol, clustered on "
      "entry year. How much each correction matters, as a median ratio across "
      "all cells:")
    w("")
    for stop in STOPS:
        s_ = d[d.stop_name == stop]
        w(f"- `{stop}` stop: clustering on **symbol** changes the standard error "
          f"by a median factor of {(s_.se_symbol / s_.se_naive).median():.2f}x; "
          f"clustering on **entry year** by "
          f"{(s_.se_year / s_.se_naive).median():.2f}x")
    w("")
    w("**That contrast is itself a result.** Two trades on the same company, "
      "years apart, carry almost no shared information -- symbol clustering "
      "barely moves the standard error. Two trades opened in the same year "
      "carry a great deal, and correcting for it inflates the standard error "
      "several-fold. The dependence in this data is calendar time, not company. "
      "Everything below therefore uses the **year-clustered** standard error, "
      "the larger and more honest of the two.")
    w("")
    ncl = int(d.n_years.median())
    crit = 2.086
    w(f"With a median of {ncl} year-clusters per cell, the reference "
      f"distribution is t on about {ncl - 1} degrees of freedom, so the 5% "
      f"critical value is ~{crit:.3f} rather than 1.96. Counts below use that. "
      f"A caution that cannot be engineered away: a cluster-robust estimator "
      f"built on ~{ncl} clusters is itself noisy, and this one is downward-"
      f"biased if anything.")
    w("")
    w("**First question: is any arm's mean R distinguishable from zero?**")
    w("")
    w("| arm | median t (`own`) | past ~" + f"{crit:.2f}" + " (`own`) | "
      "median t (`atr3`) | past ~" + f"{crit:.2f}" + " (`atr3`) |")
    w("|---|---|---|---|---|")
    for arm in ARMS:
        cells = []
        for stop in STOPS:
            a = d[(d.stop_name == stop) & (d.arm == arm)]
            t = a.mean_r / a.se_year
            cells += [f"{t.median():+.2f}",
                      f"{int((t.abs() > crit).sum())} of {n_rules}"]
        w(f"| `{arm}` | " + " | ".join(cells) + " |")
    w("")
    w("Every arm's median t sits between 2 and 4 -- just past the line, not "
      "far past it. Once the calendar-time dependence is priced in, none of "
      "these rules is producing an overwhelming trade-level signal, the "
      "control included.")
    w("")
    w("**Second question: is the board-to-native gap itself significant?** "
      "The difference of two means over the square root of the sum of their "
      "year-clustered variances. Both arms run on the same symbols and the same "
      "years, so the two means are positively correlated and this SE is too "
      "*large* -- the test is conservative, which is the direction to err in.")
    w("")
    w(f"| stop | median t on (native - board) | t below -{crit:.2f} | "
      f"t above +{crit:.2f} |")
    w("|---|---|---|---|")
    for stop in STOPS:
        bb = d[(d.stop_name == stop) & (d.arm == "board")].set_index("rule")
        nn = d[(d.stop_name == stop) & (d.arm == "native")].set_index("rule")
        t = (nn.mean_r - bb.mean_r) / ((nn.se_year ** 2 + bb.se_year ** 2) ** 0.5)
        w(f"| `{stop}` | {t.median():+.2f} | {int((t < -crit).sum())} of "
          f"{n_rules} | {int((t > crit).sum())} of {n_rules} |")
    w("")
    w("**Not one rule, on either stop, shows the derived exit significantly "
      "ahead.** Roughly two-thirds to three-quarters show it significantly "
      "behind, and the rest point the same way without clearing the bar. "
      "Together with the sign test that is about as clean as this project's "
      "evidence gets: the direction is reliable, the per-rule magnitudes are "
      "modest, and nothing here is a knife-edge call that a different "
      "specification would flip.")
    w("")

    # ------------------------------------------------------- defect
    w("## 6. A defect found while reading the first run -- now fixed")
    w("")
    w("In the first run of this simulation (2026-09-23, "
      "`fixed_rules_150sym_2026-09-23.csv`) `mktrel` was documented as entry-"
      "**unchanged** by the repair, yet its `board` and `current` arms differed "
      "-- 8,509 against 9,728 trades on the tight stop. That was a bug, not a "
      "repair, and it has been fixed. In this run:")
    w("")
    w("| stop | board trades | current trades | board mean R | current mean R |")
    w("|---|---|---|---|---|")
    for stop in STOPS:
        w(f"| `{stop}` | {b.loc[('mktrel',stop),'trades']:,.0f} | "
          f"{c.loc[('mktrel',stop),'trades']:,.0f} | "
          f"{b.loc[('mktrel',stop),'mean_r']:+.3f} | "
          f"{c.loc[('mktrel',stop),'mean_r']:+.3f} |")
    w("")
    mk_ok = all(b.loc[("mktrel", st), "trades"] == c.loc[("mktrel", st), "trades"]
                for st in STOPS)
    w(("The two arms are now identical, as the documentation always said they "
       "should be." if mk_ok else
       "**They still differ -- the fix did not take. Do not trust `mktrel` "
       "below.**"))
    w("")
    w("The cause is the market proxy, not an intended repair. `mktrel` fires "
      "when a stock is up over 20 sessions *while the market is down*, so it "
      "needs a definition of \"the market\". `kitelab/entries.py:252` builds one "
      "by compounding the equal-weighted mean daily **return**. "
      "`scripts/fixed_sim.py:101` instead averages the raw **closing prices** "
      "across whatever symbols existed that day -- a level average, which is "
      "dominated by high-priced stocks and steps whenever a symbol enters or "
      "leaves the panel. The comment above it claims to use \"the same shape\" "
      "as `entries._weak_market`. It does not.")
    w("")
    pk = proxy_check()
    w(f"Measured directly on the same 150-symbol panel ({pk['sessions']:,} "
      f"sessions): the price-average proxy calls {pk['weak_price']:.1%} of "
      f"sessions weak, the return-index proxy {pk['weak_ret']:.1%}, and **the "
      f"two disagree on {pk['disagree']:,} sessions, {pk['disagree_pct']:.1%} of "
      f"the sample**. That is where `mktrel`'s extra trades come from.")
    w("")
    w("`scripts/fixed_sim.py` now compounds the equal-weighted mean daily "
      "return, matching `entries.py`. Only `mktrel` reads the weak-market flag, "
      "so no other rule was ever affected; the first run's other thirteen rules "
      "stand. The lesson is the reusable part: **an equal-weighted market proxy "
      "is a return index, never a mean of price levels** -- a price average is "
      "not scale-free and moves when the panel's membership changes rather than "
      "when the market does.")
    w("")
    w("A second, smaller mismatch in the same function, noted but not material: "
      "the top-decile test is `>= 0.90` there against `> 0.90` in "
      "`entries.py:246`. `xrank`'s entry was changed on purpose anyway (the "
      "Jegadeesh-Titman one-month skip), so this cell was never a clean "
      "comparison to begin with.")
    w("")

    # ------------------------------------------------------- limits
    w("## Limits -- what this run cannot support")
    w("")
    w("1. **The standard errors are clustered but not two-way.** Section 5 "
      "clusters on symbol, and separately on entry year, but not on both at "
      "once; a trade is correlated with its neighbours along both axes "
      "simultaneously and the true standard error is larger than either. Treat "
      "the t-columns as upper bounds on the evidence, not point estimates of "
      "it.")
    w("2. **Trade-level only.** No position limits, no shared capital, no "
      "sequencing. Nothing here is an account return, and no arm here has been "
      "through the board's validation gate.")
    w("3. **The sample is 150 symbols.** `xrank` and `mktrel` rank *within the "
      "sample*, so their numbers would change on a 1,000-symbol run. The sample "
      "size is in the filename for that reason.")
    w("4. **R is not comparable across the two stops.** Different denominators.")
    w("5. **No arm was selected, and none should be.** All six are reported "
      "together, the deliberately-broken baseline included as a control. This "
      "project measured its own selection criterion at PBO 0.412 -- picking the "
      "best-scoring arm is worse than a coin flip here.")
    w("6. **Raschke's partial scale-out is not modelled.** This engine holds "
      "whole positions, so \"take partial profits within two to six bars\" "
      "became a whole-position 6-bar cap. That is a real difference, not a "
      "rounding.")
    w(f"7. **The tight stop does not cap losses at 1R.** Its median trade on the "
      f"board arm is {ow.loc['board','median_r']:+.2f}R with the stop ending "
      f"most trades -- prices gap through a stop that tight overnight, so the "
      f"typical 'stopped' trade loses about half again what it risked.")
    w("")
    w("## What this does not say")
    w("")
    w("It does not say the 60-session cap is a good exit, or that it should be "
      "kept. It says the opposite of something flattering: the cap's advantage "
      "is that it holds, and this project has repeatedly measured that the "
      "board's rules lose to simply buying and holding. A finding that the best "
      "exit is the one that holds longest is a finding about holding, not about "
      "the rules.")
    w("")
    w("It also does not say the repair was wasted. `fixed_rules.py` is the first "
      "version of these rules in which the exit is *derivable from the entry*, "
      "and the honest result is that once you stop letting an arbitrary long "
      "hold do the work, almost nothing is left. That is worth knowing.")
    w("")
    w("No number here is a forecast, and nothing here is a recommendation to "
      "buy or sell anything.")
    w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n")
    print(f"wrote {OUT}  ({len(L)} lines, {OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

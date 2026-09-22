"""The prose for scripts/build_n500_report.py, kept separate from the builder.

Same split, and the same reason, as scripts/report_text.py: the document is
mostly words, and mixing them into the layout code buries the layout code.

WHAT THIS FILE IS ALLOWED TO CONTAIN (set 2026-09-22, after two rounds of the
reader cutting it back). The reader is the user's teacher. The verdict on the
previous version was "literally half the doc is still explanation of what the
Nifty 500 is, not what the analysis on it says". So every definition is gone --
index, CAGR, total return, drawdown, look-ahead bias -- and so is every
paragraph that set up a result before giving it.

What is left is one of three things, and nothing else:
  a FINDING     a sentence that is false unless a measurement says otherwise;
  an IMPLICATION  what the finding costs someone reading a backtest;
  a LIMIT       what the measurement cannot support.
The numbers themselves live in the tables, the figures and their captions,
which the builder writes -- not here.

Test before adding a sentence: could it be said about any index anywhere? Then
it is description, and it does not go in.

The section order was set the same day, after the earlier verdict that the
document read as a critique of the index rather than an analysis of it: what
the index is made of, what it and its members did, how far they fell, where
the returns came from, and only then the membership-bias correction that says
how much of the four to believe.

NO NUMBER IS WRITTEN HERE. Every figure in the finished document is read out of
the measurement files at build time and formatted by the builder. A number
typed into prose is a number that goes stale silently, and this file is the
easiest place in the project for that to happen unnoticed.

Prose is plain ASCII and spells an em dash ' -- '; build_n500_report.dash()
renders it. Entities like &lsquo; are allowed where reportlab needs them.

Neither this file nor build_n500_report.py is in signals._ACCOUNT or _SUPPORT,
so editing either costs nothing and cannot trigger a rebuild.
"""
from __future__ import annotations

TITLE = "The Nifty 500, Measured"
SUBTITLE = "What the index is made of, what it did, and what it cost to hold"

# --------------------------------------------------------------- opening ---
WHY_THIS_EXISTS = (
    "Five measurements on the NSE constituent file: how lopsided the list is, "
    "how far its members&rsquo; returns are spread, how far they fell, which "
    "industries paid, and how much of all four is an artefact of using "
    "today&rsquo;s membership."
)

# -------------------------------------------- 1. what the index actually is -
COMPOSITION_LEAD = (
    "The list is far more concentrated than five hundred names suggests, on "
    "both counts that matter: which industries it holds, and where the money "
    "in it actually trades."
)

CONCENTRATION_READ = (
    "That lopsidedness is an execution problem, not an arithmetic one. An "
    "equal-weight study of this index puts the same rupees into the quietest "
    "name as into the busiest, and the quiet end is where filling at the "
    "screen price is least realistic."
)

CONCENTRATION_WHAT_IT_IS_NOT = (
    "<b>Trading activity, not index weight.</b> There is no market-value "
    "series on disk, so this cannot state what fraction of the index a name "
    "carries -- only how much money moved through it per day."
)

# ------------------------------------------- 2. the index vs its members ----
DISPERSION_LEAD = (
    "Three numbers have an equal claim to being &lsquo;what the Nifty 500 "
    "did&rsquo;, and they are far apart. Only the index is what a fund paid; "
    "only the median member is what picking a name at random felt like."
)

DISPERSION_WHY = (
    "The basket beating its own median member is structural. Losses stop at "
    "the money put in and gains do not, so the distribution leans hard right; "
    "an equal-weight basket collects the whole right tail while each loss is "
    "capped. The effect grows with the spread, and the spread here is wide."
)

DISPERSION_READ = (
    "So the basket did not beat the typical company by being selective -- it "
    "beat it by holding all of them, including the few that ran away. Any "
    "rule that narrows the list gives up part of that tail and has to earn it "
    "back before it has done anything."
)

OWN_HISTORY_CAVEAT = (
    "Each company&rsquo;s return is measured over the part of the window it "
    "actually traded in, so the spread mixes full-window rates with rates "
    "from names that listed late. The basket figure is unaffected: it is the "
    "board&rsquo;s own "
    "benchmark, reproduced here to the fourth decimal before anything else is "
    "computed."
)

# ------------------------------------------------- 3. the drawdown ---------
PAIN_LEAD = (
    "The fall the index took and the fall its typical member took are not the "
    "same number, and they are not close."
)

PAIN_READ = (
    "Five hundred companies do not fall together, so their individual "
    "collapses cancel inside the index and never appear in its chart. The "
    "index is not a smoothed version of its members; it is a different "
    "experience."
)

PAIN_STAKES = (
    "That decides how to read any backtest run on this list. A rule tested "
    "across the whole list reports the portfolio&rsquo;s drawdown, the "
    "shallow one; what a real person has to sit through is the drawdown on "
    "the few positions actually held."
)

# ------------------------------------------------- 4. sectors --------------
SECTOR_LEAD = (
    "The spread is not evenly distributed across the list. Part of it is "
    "industry, and the heaviest industry is not the one that paid."
)

SECTOR_LIMITS = (
    "A sector median is not a sector index: it weights a tiny constituent "
    "like a giant one, on purpose, so it describes the typical company rather "
    "than the industry&rsquo;s market value."
)

# ---------------------------------- 5. how to read every number above ------
READING_LEAD = (
    "Every number above was measured on the list NSE publishes today, and "
    "that carries a correction big enough to change how each of them reads."
)

THE_TWO_LISTS = (
    "NSE re-cuts the list twice a year, and a company joins it by having "
    "grown; the bias that creates is measurable rather than arguable. Run the "
    "same buy-and-hold on two lists: today&rsquo;s constituents, and one "
    "built as a trader in the start year would have had to -- every stock "
    "ranked by daily traded value using only sessions dated BEFORE the start "
    "year, top five hundred taken. The gap between them is membership bias."
)

NOT_THE_IPOS = (
    "It is not the recent flotations, which is the first explanation most "
    "readers reach for. Rerunning on only the companies already trading in "
    "the start year RAISES it -- this benchmark holds an unlisted "
    "member as idle cash, so new listings drag the basket down and every gap "
    "printed here is smaller than it would otherwise be. The bias comes from "
    "which ESTABLISHED companies are on today&rsquo;s list."
)

EQUAL_WEIGHT_GAP = (
    "The equal-weight basket beating the real index is a second correction, "
    "and it compounds with the first. It is not a free lunch: equal weight "
    "holds far more of its money in small companies, rebalances constantly to "
    "keep it there, and is correspondingly harder to trade."
)

# The claim stops where the measurement stops. How MANY start years it holds
# at is counted in build_n500_report.section_reading and appended there,
# because a count written into prose goes stale silently.
RULES_PREDICTION = (
    "A trading rule buys a name when its condition fires and sells days "
    "later, so the bias might be expected to thin out once the buying becomes "
    "selective. It does not. It concentrates."
)

RULES_WHY = (
    "These rules are mostly shaped to buy strength, and the companies that "
    "earned their way into today&rsquo;s index are by construction the ones "
    "that went up a lot. A strength rule pointed at a list assembled after "
    "the fact out of things that turned out strong finds what it was aimed "
    "at. That reading fits the numbers but has not been separately measured."
)

RULES_STAKES = (
    "Same rule, same parameters, same dates, same costs: only the list of "
    "stocks differs, and it roughly doubles the count of rules that appear to "
    "beat buying and holding."
)

# ------------------------------------------------------- trusting it -------
TRUST_ONE = (
    "Two checks run before any number here is computed, and both have to "
    "pass or nothing is written. <b>The account check:</b> this study drives "
    "the first report&rsquo;s "
    "simulator from outside it, so one cell of the already-built board is "
    "recomputed from scratch and compared to the recorded version on all five "
    "of annual return, final balance, worst drawdown, trade count and "
    "risk-adjusted return, to the digit -- a different cell at each start "
    "year."
)

TRUST_TWO = (
    "<b>The trade check.</b> A hundred-odd Nifty 500 names sit outside the "
    "board&rsquo;s own thousand, so their trades are built for this study. A "
    "sample of names that ARE in the board&rsquo;s universe is rebuilt and "
    "compared field by field: entry and exit date, entry and exit price, "
    "share count, exit reason, profit."
)

# ------------------------------------------------------------- the limits --
LIMIT_SURVIVORSHIP = (
    "<b>Survivorship.</b> Firms delisted, merged away or failed since the "
    "start year have no price history on disk, so they are missing from every "
    "table and from both sides of every comparison, and they are "
    "disproportionately the losers. Every spread, drawdown and gap above is a "
    "floor, by an amount this data cannot measure."
)

LIMIT_CLOSES = (
    "<b>Closing prices only.</b> An intraday low below the close never enters "
    "a drawdown, so every fall reported is at or shallower than what happened "
    "-- on the members and on the index alike."
)

LIMIT_ONE_INDEX = (
    "<b>One index, one exchange.</b> The mechanisms are general; the sizes "
    "are not, and none of them transfers to the Nifty 50 or to a list built "
    "on a different qualification rule."
)

LIMIT_NOT_ADVICE = (
    "<b>Not advice.</b> Every figure describes a fixed past window. No number "
    "here is a forecast and nothing here is a recommendation."
)

CLOSING = (
    "A small minority of the Nifty 500 carries most of its trading; its "
    "typical member returned far less than the index and fell far further; "
    "and today&rsquo;s membership was chosen partly by knowing how the story "
    "ended. Any claim about this index that does not say which of those it is "
    "quoting is not yet a claim about anything."
)

# ------------------------------------------------------------ the universe -
UNIVERSE_LEAD = (
    "The study uses the real NSE constituent file, not a proxy and not a "
    "sample. Names are absent for two reasons and no others."
)

UNIVERSE_RULE = (
    "A short price history is NOT one of them, which is a deliberate "
    "departure from the board&rsquo;s own universe rules: excluding recent "
    "listings would quietly delete the very cohort the membership finding is "
    "about."
)

UNIVERSE_TOO_NEW = (
    "Separately, the buy-and-hold benchmark will not admit a member with "
    "fewer than a year of trading days inside the span, because a basket "
    "weight from a handful of bars is noise. Those names are in the study "
    "universe and absent from every return column."
)

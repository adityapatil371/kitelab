"""The prose for scripts/build_n500_report.py, kept separate from the builder.

Same split, and the same reason, as scripts/report_text.py: the document is
mostly words, and mixing them into the layout code buries the layout code.

WHO IT IS FOR. The same reader as the first report -- an experienced
discretionary trader who does not read statistics. So: every term is defined in
words the first time it is used, the argument is carried by the prose rather
than by the reader's ability to interpret a significance test, and every claim
that rests on a measurement says which measurement.

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
SUBTITLE = ("What an index list does to a backtest -- and what it did to ours")

# --------------------------------------------------------------- opening ---
WHY_THIS_EXISTS = (
    "The first report measured eighteen trading rules on a thousand NSE "
    "stocks that were chosen for being liquid. It did not test them on an "
    "index, because the board does not trade one. The obvious next question "
    "is the one you asked: what happens on the Nifty 500, the list most "
    "people actually mean when they say &lsquo;the market&rsquo;?"
)

WHAT_AN_INDEX_IS = (
    "The Nifty 500 is a list. NSE publishes it, and it names the five hundred "
    "companies the exchange currently counts as the investable part of the "
    "market. Two things about that list matter more than anything else in "
    "this document, and neither is a statistical point -- they are facts "
    "about how the list is made."
)

FACT_REBALANCE = (
    "<b>The list changes twice a year.</b> NSE reviews it every March and "
    "every September and swaps names in and out. A company joins because it "
    "has grown large enough and traded heavily enough to qualify, and it "
    "leaves because it has shrunk. The list you can download today is "
    "today&rsquo;s list. It is not the list that existed in 2018, and nobody "
    "in 2018 could have known what it would become."
)

FACT_WEIGHT = (
    "<b>The index is not an equal bet on five hundred companies.</b> The real "
    "Nifty 500 is weighted by market value: the largest companies carry most "
    "of it and the smallest carry almost none. Holding an equal rupee amount "
    "in each of five hundred names is a different portfolio with a different "
    "return, and the difference is large enough to matter. Both numbers "
    "appear below, labelled, because quoting one while meaning the other is "
    "the most common way a backtest overstates itself."
)

# ------------------------------------------------------------ the method ---
THE_TWO_LISTS = (
    "Everything here rests on running the same thing twice on two different "
    "lists, and reading the gap. The first list is today&rsquo;s five hundred "
    "constituents, downloaded from NSE. The second is a list built the way a "
    "trader in the start year would have had to build one: rank every stock "
    "by how much money actually changed hands in it per day, using only "
    "sessions dated BEFORE the start year, and take the top five hundred. No "
    "bar dated after the start is allowed to influence which names are "
    "chosen. This is called a point-in-time list, and the phrase means "
    "exactly what it says -- built from what was knowable at that point in "
    "time."
)

WHAT_LOOKAHEAD_IS = (
    "The gap between the two has a name. A backtest that uses today&rsquo;s "
    "index membership to decide what to buy in 2018 is using information from "
    "the future: it is buying the companies that would go on to earn their "
    "place, because they went up. That is look-ahead bias, and the particular "
    "version of it that comes from an index list is called membership bias. "
    "It is not a rounding error and it is not a caveat. It is a number, and "
    "this document&rsquo;s job is to put a size on it."
)

WHAT_CAGR_IS = (
    "Every return quoted here is a compound annual growth rate: the single "
    "steady yearly rate that would have taken the starting money to the "
    "finishing money over the same span. Two returns are comparable only when "
    "they cover the same dates, so the tables are grouped by start year and "
    "never across them. A &lsquo;point&rsquo; means one percentage point of "
    "that rate -- the difference between 14 per cent a year and 18 per cent "
    "a year is four points, and over eight years four points is a great deal "
    "of money."
)

# ------------------------------------------------------------- finding 1 ---
FINDING_ONE_LEAD = (
    "Buying and holding today&rsquo;s Nifty 500 list, in equal amounts, from "
    "each of three start years, against the same thing on a list that could "
    "have been built in that year. The second column is achievable. The first "
    "is not, and the gap between them is the size of the illusion."
)

FINDING_ONE_READ = (
    "The gap is positive at every start year. It is not an artefact of one "
    "period, one market cycle or one choice of start date. And the mechanism "
    "is visible in a single count: only a little over half of today&rsquo;s "
    "list was in the top five hundred by traded value in 2018. Nearly half "
    "the names in a backtest run on today&rsquo;s list are names a trader "
    "that year had no reason to be looking at."
)

NOT_THE_IPOS = (
    "The obvious explanation is wrong, and it is worth saying so plainly "
    "because it is the explanation most people reach for. A large number of "
    "today&rsquo;s constituents had not listed in the start year -- the "
    "recent flotations everyone can name. You would expect them to be the "
    "whole story. They are not, and the measurement points the other way. "
    "Rerunning on only the companies that were ALREADY trading in the start "
    "year does not lower the result; it raises it, because in this benchmark "
    "a company that has not listed yet is held as idle cash earning nothing, "
    "which drags the basket down until it lists."
)

NOT_THE_IPOS_2 = (
    "So the recent listings are not the source of the bias. They work against "
    "it, and every gap printed in this document is smaller than it would be "
    "without them. What is left is the uncomfortable part: the bias comes "
    "from WHICH ESTABLISHED COMPANIES are on today&rsquo;s list -- the "
    "survivors of years of twice-yearly promotion and relegation, selected "
    "after the fact for having done well."
)

# ------------------------------------------------------------- finding 2 ---
FINDING_TWO_LEAD = (
    "The second fact is separate from the first and compounds with it. The "
    "equal-weight basket in Table 1 is not the Nifty 500. The real index is "
    "weighted by company size, and its published Total Return Index -- "
    "&lsquo;total return&rsquo; meaning dividends are counted as reinvested, "
    "so it is comparable with a portfolio that does the same -- is that "
    "table&rsquo;s last row."
)

FINDING_TWO_READ = (
    "Equal weighting beats the real index at every start year, by a wide "
    "margin. That is not a free lunch and it should not be read as one. An "
    "equal-weight basket of five hundred names holds far more of its money in "
    "small companies than the index does, rebalances constantly to keep it "
    "that way, and is correspondingly harder to trade and more volatile. The "
    "point of putting it here is narrower: if a backtest reports an "
    "equal-weight return and a reader compares it in their head to the index "
    "they follow, the comparison is wrong before the strategy is even "
    "considered."
)

# ------------------------------------------------------------- finding 3 ---
FINDING_THREE_LEAD = (
    "So much for holding. The question that matters for a trader is whether "
    "any of this survives contact with an actual rule. A rule does not own "
    "the whole list -- it buys a name only when its own condition fires, and "
    "sells days later. It is entirely reasonable to expect the bias to wash "
    "out, or at least thin out, once the buying becomes selective."
)

# The claim stops where the measurement stops. How MANY start years it holds
# at is counted in build_n500_report.section_finding_three and appended
# there, because a count written into prose goes stale silently.
FINDING_THREE_PREDICTION = (
    "That was the expectation going in, and it was wrong. Running all "
    "thirty-six board rows on both lists, the bias is not diluted by a "
    "trading rule. It is concentrated by one."
)

FINDING_THREE_WHY = (
    "The likely reason is uncomfortable rather than technical. The rules on "
    "this board are mostly shaped to buy strength -- breakouts, gaps, "
    "momentum, new highs. The companies that earned their way into "
    "today&rsquo;s index are, by construction, companies that went up a lot. "
    "They are therefore exactly the companies that fire those signals. A "
    "rule that hunts for strength, pointed at a list assembled after the fact "
    "out of things that turned out to be strong, finds what it was aimed at. "
    "This explanation is plausible and consistent with the numbers, but it "
    "has not been separately measured, and it should be read as the reading "
    "rather than as a finding."
)

FINDING_THREE_STAKES = (
    "The practical consequence is the part worth keeping. Choosing the wrong "
    "list roughly doubles the number of rules that appear to beat buying and "
    "holding, and it flips several from losing to winning outright. Table 4 "
    "has the counts. The rules named in Table 5 are not marginal cases: "
    "they lose money on a list a trader could have assembled, and make "
    "money on today&rsquo;s. Nothing about the rule changed. Only the "
    "list did."
)

# ------------------------------------------------------------ the universe -
UNIVERSE_LEAD = (
    "The study uses the real NSE constituent file, not a proxy and not a "
    "sample. Four of the five hundred names are not in it, for two reasons "
    "and no others."
)

UNIVERSE_RULE = (
    "A short price history is NOT a reason to drop a name here, and that is a "
    "deliberate departure from the board&rsquo;s own universe rules. A "
    "company that listed in 2024 contributes almost nothing to a backtest "
    "that starts in 2018, but it is not bad data, and excluding it would "
    "quietly delete the very cohort the first finding is about. Everything "
    "with a usable price file is in, however short."
)

UNIVERSE_TOO_NEW = (
    "A separate and smaller thing: the buy-and-hold benchmark will not admit "
    "a member with fewer than a year of trading days inside the span, because "
    "a basket weight computed from a handful of bars is noise. Those names "
    "are named below rather than quietly dropped. They are in the study "
    "universe; they are simply absent from the hold column."
)

# ------------------------------------------------------- trusting it -------
TRUST_LEAD = (
    "Two checks run before any number in this document is computed, and both "
    "have to pass or nothing is written at all. They are worth a paragraph "
    "each, because they are the reason these figures are worth more than an "
    "assertion."
)

TRUST_ONE = (
    "<b>The account check.</b> The machinery here is the same simulator the "
    "first report used, driven from outside it. That is only worth anything "
    "if it produces the same answers. So before each run, one cell of the "
    "already-built board is recomputed from scratch and compared to what the "
    "board recorded -- annual return, final balance, worst drawdown, number "
    "of trades and risk-adjusted return, all five, to the digit. It is a "
    "different cell at each start year, so agreement is not one lucky match. "
    "If any digit disagrees, the run stops and prints why."
)

TRUST_TWO = (
    "<b>The trade check.</b> A little over a hundred of the Nifty 500 names "
    "are outside the board&rsquo;s own thousand, so their trades have never "
    "been computed and are built for this study. That is only legitimate if a "
    "trade built today is identical to one the board built a fortnight ago. "
    "So a sample of names that ARE in the board&rsquo;s universe is rebuilt "
    "and compared to the stored version field by field: entry date, exit "
    "date, entry price, exit price, share count, exit reason and profit. Any "
    "mismatch stops the run."
)

# ------------------------------------------------------------- the limits --
LIMIT_SURVIVORSHIP = (
    "<b>Every gap in this document is a floor, not an estimate.</b> Both "
    "lists are drawn from companies whose price history still exists today. "
    "Companies that were delisted, merged away or failed between the start "
    "year and now are missing from BOTH sides of every comparison. Those are "
    "disproportionately the losers. Their absence flatters the achievable "
    "column as much as the unachievable one, which means the true size of the "
    "bias is larger than the number printed here -- by an amount this study "
    "cannot measure, because the data to measure it was never collected. "
    "This is called survivorship bias, and it is the largest known "
    "limitation of everything in both reports."
)

LIMIT_ONE_INDEX = (
    "<b>One index, one exchange, one country.</b> Nothing here says how large "
    "the same effect is in the Nifty 50, in a US index, or in a list built on "
    "a different qualification rule. The mechanism -- periodic promotion of "
    "things that went up -- is general. The size measured here is not."
)

LIMIT_NOT_ADVICE = (
    "<b>No number in this document is a forecast, and nothing in it is a "
    "recommendation to buy or sell anything.</b> Every figure describes what "
    "already happened in a simulation over a fixed past window. The reason "
    "for measuring the bias is to know how much of a backtest to believe, not "
    "to find a list worth trading."
)

CLOSING = (
    "The useful takeaway is a habit rather than a number. Whenever a backtest "
    "is quoted on an index -- anyone&rsquo;s, including this board&rsquo;s -- "
    "the first question is which list it used and when that list was made. If "
    "the answer is &lsquo;today&rsquo;s constituents&rsquo;, the measured "
    "correction is several points a year for holding and more than that for a "
    "rule, before any other objection is raised. That is usually larger than "
    "the edge being claimed."
)

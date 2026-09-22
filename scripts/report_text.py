"""The prose and formulas for scripts/build_report.py, kept separate from it.

WHY A SEPARATE FILE. The report is mostly words, and words about eighteen
trading rules are long enough that mixing them into the builder would bury the
twenty lines of layout code that actually matter. Every formula here is copied
from the module that implements it -- kitelab/entries.py for the fourteen,
backtest.py / darvas.py / timeframes.py for the four engines, indicators.py for
the smoothing conventions -- and the source is named beside each one so a reader
can check it rather than trust it.

Neither this file nor build_report.py is in signals._ACCOUNT or _SUPPORT, so
editing either costs nothing. Nothing here is simulated; the numbers all come
from the payload.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The eighteen entry rules. Each entry:
#   idea     what the rule is trying to catch, in one sentence
#   why      why this rule is ON THE BOARD -- the region of signal space it
#            covers, and the rule it is paired against. Written from the
#            selection notes, which predate any return being read.
#   formula  the exact condition, in plain notation, from the named source
#   src      file:function the formula was copied from
#   chart    what a trader would see on a chart when it fires
#   note     the detail that matters and is easy to get wrong
# ---------------------------------------------------------------------------
RULES = {
    "mr": dict(
        family="Mean reversion",
        why="The board needs one plain &lsquo;buy weakness&rsquo; rule, and "
             "this is the short-horizon version of it. It is also the "
             "control for the one-year low below: same idea, twenty sessions "
             "instead of two hundred and fifty-two, so the difference "
             "between the two rows prices the <i>horizon</i> and nothing "
             "else.",
        idea="Buy a stock at its lowest close in a month, betting the fall has "
             "gone far enough.",
        formula="Close[t]  <=  min( Close[t-19] ... Close[t] )",
        src="kitelab/entries.py, signal(), entry == 'mr'",
        chart="Price is making a new twenty-session low. You are buying the "
              "bottom of a visible slide, not a bounce off it -- there is no "
              "confirmation leg in this rule.",
        note="A twenty-day low can stay true for weeks. The engine enters only "
             "on the RISING EDGE (false yesterday, true today), so a long slide "
             "is one trade, not forty.",
    ),
    "pull": dict(
        family="Pullback in an uptrend",
        why="Neither weakness rule can reach the setup most discretionary "
             "traders actually use: a dip inside a rise. This rule covers "
             "that cell &mdash; long-term strength with short-term weakness "
             "&mdash; and is the only one on the board that requires the two "
             "to disagree.",
        idea="Buy a dip inside a rising trend: long-term up, short-term down.",
        formula="Close[t] > SMA(Close,200)[t]   AND   Close[t] < SMA(Close,20)[t]",
        src="kitelab/entries.py, signal(), entry == 'pull'",
        chart="Price is above its 200-day average (the trend is up) but has "
              "slipped under its 20-day average (the last few weeks are down). "
              "The classic 'buy the dip' setup.",
        note="Both averages are simple, not exponential, and both use "
             "min_periods equal to their own length -- so the rule is silent "
             "for a stock's first 200 sessions rather than guessing.",
    ),
    "vcon": dict(
        family="Volatility contraction",
        why="The &lsquo;buy quiet&rsquo; family needs a representative, and "
             "range contraction is its standard form. It is the seven-bar "
             "arm of a pair with the inside bar below, which is the one-bar "
             "arm.",
        idea="Buy when the daily range collapses, betting the quiet precedes a "
             "move.",
        formula="Range[t] = High[t] - Low[t]\n"
                "Range[t]  <=  min( Range[t-6] ... Range[t] )",
        src="kitelab/entries.py, signal(), entry == 'vcon'",
        chart="The narrowest candle of the last seven sessions -- a squeeze. "
              "Note the rule does not care which DIRECTION the move comes out; "
              "it always buys.",
        note="This is the rule with the highest raw return on the board and the "
             "third-worst drawdown. Both facts come from the same place: it "
             "fires in quiet markets and holds through the noisy ones.",
    ),
    "vol": dict(
        family="Volume spike",
        why="The first of only two rules that read <i>volume</i> rather "
             "than price, which is why either is here: a volume rule can fire "
             "on a day the chart itself looks ordinary. Paired with the "
             "dry-up rule below.",
        idea="Buy when today's volume is three times normal, betting that "
             "something real happened.",
        formula="VolMed[t] = median( Volume[t-49] ... Volume[t] )\n"
                "Volume[t] > 3 x VolMed[t]    AND    VolMed[t] > 0",
        src="kitelab/entries.py, signal(), entry == 'vol'",
        chart="A volume bar three times the height of the last fifty sessions' "
              "typical bar. Often a result, a block deal or an index change.",
        note="Median, not mean. A mean over fifty sessions is itself moved by "
             "the spike you are trying to detect; a median is not.",
    ),
    "cal": dict(
        family="Calendar",
        why="The yardstick. It reads no price at all, so any price rule "
             "that cannot beat it has not shown that reading price helped. "
             "It was put on the board for exactly that job before anything "
             "was run, and it is the reason a middling result elsewhere in "
             "this report can be called middling rather than promising.",
        idea="Buy on the first trading session of every month, whatever the "
             "chart says. A pure control.",
        formula="month( ts[t] )  !=  month( ts[t-1] )",
        src="kitelab/entries.py, signal(), entry == 'cal'",
        chart="Nothing. This rule reads no price at all -- it reads the "
              "calendar.",
        note="It finishes SECOND on raw return, out of thirty-six. That is "
             "the uncomfortable part, and it is the reason the rule is here: "
             "a rule that reads nothing at all outruns thirty-three of the "
             "thirty-four rows that do read the chart.",
    ),
    "xrank": dict(
        family="Cross-sectional momentum",
        why="The best-documented anomaly in the academic literature, and "
             "the first of only two rules here that look outside the symbol at "
             "all -- the only one that RANKS a stock against every other stock "
             "that day. Without it the board would be almost entirely "
             "self-referential.",
        idea="Buy the strongest tenth of the market over the last year -- the "
             "Jegadeesh-Titman effect.",
        formula="R[i,t]  =  Close[i,t] / Close[i,t-252]  -  1\n"
                "rank of R[i,t] among ALL listed stocks that day, as a "
                "percentile\n"
                "fires when that percentile  >  0.90",
        src="kitelab/entries.py, signal() + _build_panels()",
        chart="Nothing on this stock's own chart tells you. The rule is a "
              "statement about the stock RELATIVE to every other stock that "
              "day.",
        note="The 252-session lookback is fixed externally (the academic "
             "convention) and is not tuned here.",
    ),
    "gapdn": dict(
        family="Gap down",
        why="Overnight gaps are a distinct event from anything the intraday "
             "rules see, and a trader can either fade them or join them. "
             "This row fades. It is half of a matched pair with the row "
             "below.",
        idea="Buy a stock that opens sharply below yesterday's close, betting "
             "on the snap-back.",
        formula="Open[t]  <=  Close[t-1] x 0.97",
        src="kitelab/entries.py, signal(), entry == 'gapdn'",
        chart="A visible hole in the chart, price opening 3% or more under "
              "yesterday's close.",
        note="Under the tight stop this row has the deepest drawdown on the "
             "whole board. Buying a gap down means buying into a bar that is "
             "already falling, and then setting the stop at that same falling "
             "bar's low -- the two halves of the rule work against each other.",
    ),
    "gap": dict(
        family="Gap up",
        why="The other half: the identical threshold, the identical engine, "
             "the opposite direction. Running both answers &lsquo;does the "
             "direction of the gap matter&rsquo; in one experiment instead "
             "of two, and neither result can be read without the other.",
        idea="Buy a stock that opens sharply above yesterday's close, joining "
             "the move rather than fading it.",
        formula="Open[t]  >  Close[t-1] x 1.03",
        src="kitelab/entries.py, signal(), entry == 'gap'",
        chart="A hole upward -- price opening 3% or more above yesterday's "
              "close. The mirror image of the rule above.",
        note="3% is a round number, fixed once and used at the same "
             "magnitude on both sides of the pair. It is not tuned, and "
             "nothing here searched for a better threshold -- a stock that "
             "gaps 2.9% produces no signal at all.",
    ),
    "inside": dict(
        family="Inside bar (containment)",
        why="The other arm of that pair, and a check on it: if a squeeze "
             "measured over seven sessions and a squeeze measured over one "
             "fired on the same days, one of the two would be redundant. "
             "They were compared before any return was read, and they do "
             "not fire together nearly often enough for that.",
        idea="Buy a bar that trades entirely inside yesterday's range -- a "
             "pause before a move.",
        formula="High[t] < High[t-1]    AND    Low[t] > Low[t-1]",
        src="kitelab/entries.py, signal(), entry == 'inside'",
        chart="A small candle sitting wholly within the previous candle's high "
              "and low. A standard price-action pattern.",
        note="The test is strict on both sides -- the high must be LOWER "
             "and the low HIGHER. A bar that merely matches yesterday's low "
             "does not qualify, which is why this fires less often than the "
             "pattern's usual chart definition suggests.",
    ),
    "rsi30": dict(
        family="Oscillator",
        why="It is the most widely taught entry in retail charting, and a "
             "board that omitted it would be easy to dismiss. It is also the "
             "only rule here whose trigger is a <i>smoothed oscillator</i> "
             "on its own rather than raw price, which puts the smoothing "
             "convention itself on trial.",
        idea="Buy when RSI climbs back out of oversold -- the textbook signal.",
        formula="RSI(Close,14)[t] > 30    AND    RSI(Close,14)[t-1] <= 30\n"
                "    and t >= 14 (warm-up guard)",
        src="kitelab/entries.py, signal(), entry == 'rsi30'",
        chart="The RSI pane crossing back up through the 30 line. Every "
              "charting package draws this.",
        note="It is the CROSS that fires, not the level -- a stock that sits at "
             "RSI 25 for a month produces one signal, on the day it comes back "
             "up. The warm-up guard is not in the original panel definition; it "
             "was added here because a 14-period Wilder average seeded one bar "
             "ago is not a 14-bar oscillator. It can only remove firings.",
    ),
    "low252": dict(
        family="One-year low",
        why="The long arm of that pair. Buying weakness has an obvious free "
             "parameter &mdash; how much weakness &mdash; and running the "
             "same condition at two very different horizons is the only way "
             "to see whether the parameter matters, without tuning it.",
        idea="The same idea as mean reversion, stretched from twenty sessions "
             "to a year.",
        formula="Close[t]  <=  min( Close[t-251] ... Close[t] )",
        src="kitelab/entries.py, signal(), entry == 'low252'",
        chart="A new 52-week low.",
        note="The board's cleanest demonstration that 'buy weakness' does not "
             "improve by buying MORE weakness: this rule loses to its 20-day "
             "sibling at both stop widths, and loses to hold by more than any "
             "other rule.",
    ),
    "dryup": dict(
        family="Volume dry-up",
        why="The opposite direction on the same measurement, sharing its "
             "median and its fifty-session window so the two differ in "
             "direction only. Volume is either informative in both "
             "directions, one of them, or neither &mdash; this pair is what "
             "tells the three apart.",
        idea="Buy when nobody is trading, betting that supply has been "
             "exhausted.",
        formula="VolMed[t] = median( Volume[t-49] ... Volume[t] )\n"
                "Volume[t] < 0.5 x VolMed[t]    AND    VolMed[t] > 0",
        src="kitelab/entries.py, signal(), entry == 'dryup'",
        chart="An unusually short volume bar -- under half the typical bar of "
              "the last fifty sessions.",
        note="'Quiet' here is relative to this stock's own recent habit, "
              "not quiet in absolute terms -- the comparison is against its "
              "own fifty-session median. A large illiquid name and a small "
              "busy one are each judged on their own baseline.",
    ),
    "mktrel": dict(
        family="Relative strength",
        why="The second rule that looks outside the symbol, but relative to "
             "the <i>market</i> rather than to a ranking of peers. It covers "
             "the &lsquo;strength against the tide&rsquo; idea, which the "
             "ranking rule cannot express: a stock can be in the top decile "
             "in a market that is also rising.",
        idea="Buy a stock going up while the market goes down -- strength "
             "against the tide.",
        formula="own_up  =  ( Close[t] / Close[t-20] - 1 )  >  0\n"
                "proxy   =  cumulative product of the equal-weighted daily\n"
                "           mean return across every listed stock\n"
                "weak    =  ( proxy[t] / proxy[t-20] - 1 )  <  0\n"
                "fires when  own_up  AND  weak",
        src="kitelab/entries.py, signal() + _build_panels()",
        chart="Your stock's twenty-day line slopes up while the index's slopes "
              "down.",
        note="The stock's window is counted in ITS OWN sessions and the "
             "market's in the market's -- which differ "
             "for a thinly traded name, and that is the right way round.",
    ),
    "pine": dict(
        family="The hand-traded rule (Pine)",
        why="This is the rule actually being hand-traded, which is why it "
             "is here. Its presence is the point of the exercise &mdash; it "
             "is judged by the same yardstick, the same costs and the same "
             "corrections as the seventeen rules around it, rather than by "
             "its own chart.",
        idea="Your own TradingView rule: a Heikin-Ashi candle with no lower "
             "wick, in an uptrend on the timeframe above, with RSI rising "
             "through the middle.",
        formula="on WEEKLY bars --\n"
                "  HAc = (O+H+L+C)/4 ;  HAo[i] = (HAo[i-1] + HAc[i-1])/2\n"
                "  HAh = max(H, HAo, HAc) ;  HAl = min(L, HAo, HAc)\n"
                "  nowick  =  HAc > HAo   AND   (HAo - HAl) <= 0.08 x (HAh - HAl)\n"
                "  trend   =  Close > EMA(monthly Close, 20), read as-of\n"
                "  osc     =  RSI(Close,14) > 50   AND   RSI rising\n"
                "  fires when  nowick AND trend AND osc",
        src="kitelab/pine.py, signals() + entry_mask(); placed by "
            "kitelab/entries.py _pine_fires()",
        chart="A green Heikin-Ashi candle that opens on its own low -- a flat "
              "bottom -- above the higher-timeframe trend line, with RSI above "
              "50 and rising.",
        note="Two conventions were fixed BEFORE any return was read, because "
             "the rule's measured result flips sign with them: a weekly firing "
             "is dated to the session the weekly bar CLOSED, and shifted one "
             "session forward because the rule fills at the next open. "
             "IMPORTANT: what the board trades is this rule's ENTRY only. Its "
             "own exit (an ATR stop and a 3xATR target) was measured separately "
             "and rejected on 2026-09-16.",
    ),
    "e1": dict(
        family="EMA trend, daily only",
        why="The bottom rung of a deliberate timeframe ladder: daily here, "
             "weekly-over-monthly "
             "two rows down. It is the baseline the two other EMA rules are "
             "read against, each of which moves this same idea up a "
             "timeframe.",
        idea="The simplest trend rule there is: hold while price is above its "
             "20-day exponential average.",
        formula="in  =  Close[t] > EMA(Close,20)[t]\n"
                "EMA uses adjust=False, the TradingView convention\n"
                "exit when `in` turns false",
        src="kitelab/backtest.py, ema_stack_signal(stack='daily') + simulate()",
        chart="Price crossing up through the 20 EMA and staying there.",
        note="This rule and the three below carry their OWN exit signal, on top "
             "of the stop. The fourteen rules above share one fixed exit, so "
             "they differ only in their entry; these four do not. Read the "
             "comparison with that in mind.",
    ),
    "dv": dict(
        family="Turtle channel, weekly-gated",
        why="Channel breakouts are a whole family &mdash; Donchian, Turtle, "
             "Darvas &mdash; that none of the fourteen daily rules reaches, "
             "and this is its representative. It is also the only rule here "
             "whose exit is a channel &mdash; a new twenty-day low &mdash; "
             "rather than a time limit or a signal turning false.",
        idea="The Turtle breakout: buy a new 55-day high, but only while the "
             "weekly chart agrees.",
        formula="gate   =  weekly Close  >  max( weekly High[-20] ... [-1] )\n"
                "entry  =  Close[t]  >  max( High[t-55] ... High[t-1] )\n"
                "exit   =  Close[t]  <=  min( Low[t-20] ... Low[t-1] )",
        src="kitelab/darvas.py, module docstring + ENTRY_LEN/EXIT_LEN/WEEKLY_LEN",
        chart="Price breaking above a horizontal line drawn across the highest "
              "high of the last eleven weeks, with the weekly chart already "
              "closing above its own highest weekly high of the previous "
              "twenty weeks.",
        note="Every window is shifted one bar, so today's bar is never compared "
             "against a high it set itself. The weekly gate consults the "
             "PREVIOUS calendar week on every day of the current one, Friday "
             "included -- using this week's close on a Wednesday would let "
             "Thursday's decision depend on Friday's price.",
    ),
    "pair": dict(
        family="EMA trend, monthly over weekly",
        why="The top rung of the timeframe ladder, and the control for the "
             "row below it. Its whole purpose is to be identical to that row "
             "except for one filter, so the gap between them is that "
             "filter&rsquo;s entire contribution &mdash; the only clean way "
             "to price a filter.",
        idea="The same trend idea two timeframes up: trade weekly closes, with "
             "the monthly chart as the filter.",
        formula="on WEEKLY bars --\n"
                "  in  =  Close > EMA(weekly Close, 20)\n"
                "         AND  Close > EMA(monthly Close, 20) read as-of\n"
                "exit when `in` turns false",
        src="kitelab/timeframes.py, frames_for('MW') + stack_signal()",
        chart="A weekly chart above its own 20-week average, with the monthly "
              "chart above its 20-month average.",
        note="Both the entry and the EXIT are decided on weekly bars, so "
             "the rule can only act once a week. A stock can fall for four "
             "sessions with the signal still reading 'in'; the stop is the "
             "only thing that can respond inside the week.",
    ),
    "eath": dict(
        family="EMA trend + near all-time high",
        why="The filtered arm of that control pair. The near-all-time-high "
             "condition is the most common discretionary overlay there is "
             "&mdash; &lsquo;only buy what has no overhead supply&rsquo; "
             "&mdash; and a matched pair is the only honest way to find out "
             "what it is worth.",
        idea="The rule above, restricted to stocks trading within 10% of their "
             "own record high.",
        formula="in  =  (the `pair` condition above)\n"
                "       AND  Close  >=  0.90 x ( running peak of Close )\n"
                "the peak is point-in-time: the highest close SO FAR, never "
                "the whole history's",
        src="kitelab/timeframes.py via backtest.ema_stack_signal(ath_band=0.10)",
        chart="A stock in an uptrend that is also near the top of its own "
              "all-time chart -- no overhead supply.",
        note="The filter is its own column and is applied BEFORE the rising-edge "
             "test, so a cross the filter declines is skipped rather than "
             "deferred. It produces the lowest drawdown of any row under the "
             "wide stop, at the cost of most of the return.",
    ),
}

ORDER = ["mr", "low252", "pull", "vcon", "inside", "vol", "dryup", "cal",
         "gapdn", "gap", "rsi30", "xrank", "mktrel", "pine",
         "e1", "dv", "pair", "eath"]

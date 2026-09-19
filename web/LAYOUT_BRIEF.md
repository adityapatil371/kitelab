# Dashboard layout — brief for the next session

Written 2026-09-19 at the end of a long session. The *words* on the
dashboard are done; the **layout** is not. The user said:

> "im not satisfied with the layout can you suggest some sites with good
> layouts that i can see and decide what i want to keep, but we will do
> this first thing in the next session"

So: **do not start redesigning.** Open with the list below, let the user
pick what they want to keep, then plan.

---

## 1. What the page is today

One file, `web/dashboard.html` (2,168 lines: CSS, then HTML skeleton,
then one big `<script>`). No framework, no build step, no network calls
except two `fetch()` bootstraps. Served by `python3 -m scripts.dashboard`.

Skeleton, top to bottom:

| Element | What it is |
|---|---|
| `#nav` | two tabs — Compare / Detail |
| `#scenario` | the settings rail: which stocks, starting money, risk per trade, when it sells, who gets the money first, years |
| `#stale`, `#partial` | banners about the data |
| `#v-compare` | subtitle, notes, formulas fold, Export button, **one 36-row × 5-column table** |
| `#v-detail` | a `<select>` picker, then `#det-body` — six stacked cards for the one rule chosen |

The Detail cards, in order: headline tiles → what this rule does → the
five checks → is it skill or a rising market → did it win day after day →
what if you could not buy at the close → trade quality → equity and
drawdown charts.

Every card is the same shape: a heading, a plain sentence, a row of
tiles, and a `<details class="prec">` fold holding the precise wording.

**That sameness is probably the complaint.** Eight cards of identical
grey tiles, stacked, with no visual hierarchy saying which one matters.
Nothing on the page tells you where to look first.

## 2. Hard constraints any redesign must respect

1. **One file, no build step.** It has to open from `file://` and from
   the little Python server. No npm, no bundler, no CDN at runtime.
2. **Verify by RUNNING `node scripts/check_dashboard.js`.** Never by
   reading the HTML. It slices the `<script>` block out of the page,
   runs it against a stub DOM and the real
   `/data/clean/kitelab/dashboard.json`, and asserts ~133 values.
   It scrapes Detail tiles **structurally** — `<div class="k">label</div>`
   followed by `<div class="v…">value</div>` — into a Map that keeps only
   the **first** occurrence of each label. So:
   - moving a tile is free;
   - **changing that two-div shape breaks the harness**, and the harness
     is the only thing standing between us and a silently wrong page;
   - **two tiles must never share a label.**
3. **Prose lives in the page, never in the payload.**
   `scripts/dashboard_data.py` is inside `signals._ACCOUNT` and
   `kitelab/registry.py` inside `_SUPPORT`; a comment in either costs a
   ~178-minute rebuild to publish text that changes no number. That is
   why the page owns `PLAIN`, `PLAIN_FILL`, `PLAIN_STOP`, `PLAIN_FAMILY`,
   `PLAIN_GATE`, `PLAIN_GLOSS`, `PLAIN_PRIO`, all keyed by the payload's
   own keys with a fallback to the payload label.
4. **Demotion, not deletion.** Established rule: every number leads with
   one plain sentence; the exact version folds into
   `<details class="prec"><summary>the precise version`. A layout change
   must not throw the precise text away.
5. The strategy family names ("Pine · weekly · Heikin-Ashi no-wick +
   monthly EMA + RSI") stay as-is — they are names from `registry.py`,
   and each already carries a plain description in its own card.
6. Nothing on this page is advice. No number here is a forecast.

## 3. Sites worth looking at, grouped by the problem they solve

Browse these, then say which bits you want. Rough time: 20 minutes.

### A. "One number matters, the rest is support" — hierarchy
- **screener.in** — any company page (e.g. `screener.in/company/TCS/`).
  The Indian retail standard. Big ratios strip on top, chart, then
  collapsible sections. Notice how little is above the fold.
- **Stripe Dashboard** (stripe.com/docs/dashboard screenshots, or the
  product tour) — one metric per card, sparkline inside the card, and a
  deliberate size difference between the headline and everything else.
- **Monzo / Revolut spending breakdown** — consumer-grade: the number,
  then one sentence, then a list. No table anywhere.

### B. "Explain a statistic to someone who does not want statistics"
- **Our World in Data** — e.g. `ourworldindata.org/life-expectancy`.
  Chart first, one-sentence takeaway directly above it, sources and
  caveats in a box at the bottom. Closest published thing to our
  "plain sentence + the precise version" rule.
- **Distill.pub** (archived but live) — margin notes: the technical
  aside sits in the right margin instead of under a fold. Worth deciding
  whether you prefer margins to our `<details>` toggles.
- **Tufte CSS** (`edwardtufte.github.io/tufte-css/`) — the same idea as
  a stylesheet we could actually borrow from. Sidenotes, wide figures,
  generous margins, no boxes.

### C. "A strategy explained to a retail investor" — direct competitors
- **smallcase.com** — pick any smallcase. This is the closest thing to
  what our Detail view is trying to be: what the rule buys, why, how it
  did, and the risk, laid out for a non-technical Indian investor. Note
  how they handle backtest disclaimers.
- **Zerodha Console** (P&L and tax P&L pages) — the user's own broker.
  Plain tables done well; also shows what a familiar layout feels like.
- **Morningstar fund report** — the "Quote" tab. Decades of practice at
  putting twelve statistics on one screen without it reading as a grid.

### D. "Many rows, one screen" — for the Compare table
- **FT / Economist data tables** (`ft.com/visual-and-data-journalism`,
  Economist "Graphic detail") — in-cell bars instead of numbers, so the
  eye ranks the rows before reading any of them.
- **Koyfin** screener — dense done properly: sticky header, frozen first
  column, colour only where it carries meaning.
- **airtable.com** / **Linear** — grouping and row-height control; how a
  36-row list stays scannable.

### E. Anti-examples, useful for saying "not that"
- **portfoliovisualizer.com** results page — exactly the failure mode we
  just spent two sessions undoing.
- Any Bloomberg terminal screenshot — the opposite end of the dial.

## 4. Questions to settle before touching CSS

1. Is the complaint **density** (too much per screen), **hierarchy**
   (everything the same size), **navigation** (two tabs is wrong), or
   **the charts** (they arrive last and are small)?
2. Should Compare and Detail stay two tabs, or become one scrolling page
   with the table on the left and the chosen rule on the right?
3. Are the eight Detail cards the right eight, in the right order?
4. Should the folds (`the precise version`) become margin notes instead?
5. Is this still a page one person reads on a laptop, or a page nine
   people open on phones? (The sharing door went in at `8dcc45c`.)

Layout work is cheap — no rebuild, no payload change, and the checker
tells us within seconds whether the page still reports the right numbers.

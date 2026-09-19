# Showing the dashboard to other people

Built 2026-09-19, when the question came up as *"can we make it like livedesk
where other people can also interact with the dashboard?"*

The answer had two halves and only one of them was work. **Reaching it** — a
link nine people can open, with a passphrase in front — is livedesk's door
ported across, and that is what this file is the runbook for. **Interacting
with it** turned out to need nothing: every control on the page already works
for whoever opens it, and each viewer's choices are their own. There is no
shared state here to fight over, because there is nothing writable to share.

---

## The one-minute version

On the machine that has the data — the Mac, not the container:

```bash
export KITELAB_PASSPHRASE='harbour-ingot-jetty-kernel'   # yours, not this one
./run_dashboard.sh                                       # leave it running
```

In a second terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

It prints a line like `https://spare-violet-ridge-owl.trycloudflare.com`. Send
that link and the passphrase to the group, **by different routes if you can** —
the link in the group chat, the passphrase said out loud — because a link and
its password in one message is one forwarded message away from neither.

**Send the `/read` link, not the bare one.** `https://…trycloudflare.com/read`
is the reading page; the bare hostname is the board. See *Which link to send*
below — for anyone who has not sat with this data, it is the wrong question by
a wide margin which of the two you paste.

**If you would rather not leave the laptop open**, skip the tunnel entirely and
send the reading page as a single file: `python3 -m scripts.build_standalone`.
See *Sending a file instead of a link*.

Stop it with Ctrl-C in each terminal. The link dies with the tunnel and a new
one is minted next time; sessions live in memory, so stopping the server also
logs everybody out.

First time only: `brew install cloudflared`.

---

## What each person sees

Everything, and independently. They pick their own universe, start year,
account size, risk and sorting; they open any rule's detail; nothing they touch
changes the page for anyone else, because no request this server answers writes
a byte. The payload is a snapshot computed before the server started.

What they cannot do is rebuild it, change it, or reach anything else on your
machine. Every route this server answers is a read.

## Which link to send

There are two pages behind the one door, and they are for two different people.

- **`/read` — the reading page (`web/article.html`).** One column, read top to
  bottom once. It opens with the question (*did any of it beat buy & hold?*),
  answers it, and then shows the answer at four widening scales: one rule, the
  board, every setting of one rule, all 9,936 runs. Nothing to configure and
  nothing to sort — a person who has never heard the word *backtest* can read
  it on a laptop in five minutes and come away with the finding.
- **`/` — the board (`web/dashboard.html`).** Thirty-six rows you sort and
  filter, with a detail view per rule. It is an instrument: built to be read a
  hundred times by someone who already knows what the columns mean.

**Default to `/read`.** Send the board as well, to the one or two people who
will actually turn the knobs, and say which is which. A stranger handed the
board sorts by return, reads the top row as a recommendation, and leaves with
the opposite of the finding — which is the whole reason the reading page exists.

Both pages pull the same `/api/dashboard` payload behind the same passphrase,
so sharing either shares all of it. There is no smaller door.

## Sending a file instead of a link

The tunnel has one condition the rest of this file cannot argue away: **your
laptop has to be awake, with two terminals running.** Close the lid and every
link you sent goes dead. For a reading page that is a bad trade — people open a
link hours later, on a train, on a phone.

So the reading page can also be baked into **one self-contained HTML file**:

```bash
python3 -m scripts.build_standalone      # writes output/stock-analysis.html
```

About 1.1 MB. No server, no tunnel, no passphrase, no network of any kind — it
fetches nothing. Mail it, AirDrop it, put it in the group chat; it opens by
double-clicking.

**It needs no JavaScript, and that is not a nicety.** The first time this file
went into WhatsApp it arrived as the opening question and nothing else, because
**WhatsApp's in-app document viewer renders HTML and does not run script** —
and every chart on the reading page is drawn by script. Telling nine people to
"open it in a real browser instead" is not a fix; most of them will not. So the
builder now runs the page's own drawing code once, at build time
(`scripts/prerender.js`, under node), and bakes the finished charts into the
file. Where script does run it redraws over the top and adds the
tap-for-detail layer — nobody sees a difference, and a reader with no
JavaScript at all is missing no number.

Both geometries are baked — the 980-wide drawing and the 360-wide one — and a
CSS media query at 700 px picks between them, because a media query is the only
switch available when there is no script to choose. That is what the extra
200 KB buys.

**What travels with it.** The builder does not strip the payload down — it
copies across an allowlist, ten top-level keys and four fields per grid cell,
so a field added upstream next month cannot leak into a file built after it.
The 8,049 KB `dashboard.json` goes in and 852 KB of
payload comes out. The curves, the per-day excess series, the
fill-timing arm, the calendars and the trade statistics all stay behind; what
goes is exactly what the reading page puts on screen. `scripts/check_standalone.js`
proves the trim is lossless the only way worth trusting — it renders the page
from the trimmed payload and from the full `dashboard.json`, at 1440 px and at
380 px, and requires the HTML to come out byte-identical.

**A file is not a door.** Whoever has it can forward it, there is no passphrase
in front of it, and you cannot take it back. That is the whole trade for it
working with the laptop shut. The tunnel is still the right answer when you
want to know who is reading and be able to stop them; the file is the right
answer when you want the thing read at all.

`./check_all.sh` section 5 checks a built file if one exists, and tells you to
build one if not — a **stale** single file is the failure mode to watch for,
since unlike the tunnel it does not die when the data moves. The baked charts
have a second staleness of their own, and a nastier one: they would look
perfect in any browser, because there the script redraws over them. So
`check_standalone.js` compares the baked drawings against the live render at
both widths, character for character, and fails if they have drifted apart.

**Node is required to build the file** (it is not required to read one). Without
it the build still succeeds and says, loudly, that the charts were not baked and
the WhatsApp case will fail. `brew install node`.

## Sizing, and who the numbers describe

The account size on the page is an axis of the grid — ₹2,00,000 or ₹1,00,00,000
— not a fact about the viewer. Everyone is looking at the same simulated
account, so a drawdown on screen is that account's, not theirs. livedesk has
the same issue with its flat ₹500 risk and it travels a little better there;
here the honest reading is that **these are relative comparisons between rules**,
and the rupee columns are how the comparison is scaled.

## The thing to say when you send the link

This matters more than the setup does, so it is written here rather than left
to the moment.

This is about the board. The reading page now says most of it in its own
words — that is what it is for — but the board still lands cold, and the
sentence below is still the one to send with it.

A sorted table with a row at the top reads as *"this one won"* to anyone who
has not read the five checks. On this board **not one of the 36 rows beats
buying the same stocks and waiting**, and **0 of 9,936 tested cells** clear the
day-by-day gate where about 497 would clear an uncorrected 5% by luck alone —
fewer than chance, which points away from an edge rather than merely failing to
find one. The page says all of this in plain words, but people skim, and the
leaderboard is the part that skims well.

So: *"this is a record of rules that did not work, ordered by how badly"*. It
is not a recommendation, nothing on it is a forecast, and the top row is the
least bad of a losing set rather than a pick.

---

## The two interlocks

Neither has an off switch, and that is deliberate: a flag that disables a safety
check is a safety check one flag away from being off.

**1. `serve()` refuses to bind a non-loopback address without a passphrase**, and
refuses a passphrase under 10 characters (`access.MIN_PASSPHRASE`). What is at
stake here is disclosure rather than damage — nobody can break anything, there
is nothing to write — but `dashboard.json` is a complete account of which rules
were tried, over which liquidity buckets and start years, with five years of
equity curves and every account-level result behind them. That is your
research, and a link is not a secret.

**It does not, however, name a single stock.** An earlier version of this line
said it named all ~1,000 of them; that was wrong, and it was wrong in the
direction that makes you more cautious rather than less, which is why it
survived. Checked 2026-09-19 by intersecting every symbol on disk against
every uppercase token in the 8.2 MB file: **0 of 1,122 match**, and the only
five uppercase tokens in the whole payload are `ATH`, `ATR`, `EMA`, `IST` and
`RSI`. The payload is aggregate all the way down — rows, buckets, curves — so
what leaks is the research programme, never the watchlist. (The stock list is
in `kitelab/config.py`, which is a separate disclosure with a separate door.)

**2. The handler returns 403 to any request carrying proxy headers when no
passphrase is set.** This is the one that catches a tunnel. `cloudflared` runs
on your machine and connects to 127.0.0.1, so the socket still looks private
while the whole internet is on the other end — the bind check in (1) cannot see
it. The forwarding headers are the only signal that says otherwise.

Both are pinned by `tests/test_dashboard_door.py`. The 403 especially needs a
test, because it fires only on a request no browser and no person makes by hand,
so nothing else would ever exercise it.

## Brute force

`access.Gate` counts failures two ways: **8 per client** and **30 in total**,
both over a rolling **5 minutes**. The global counter is the one that holds
behind a tunnel, where every request arrives from 127.0.0.1 and the only thing
separating callers is a header the caller writes. While a client is locked out,
even the correct passphrase is refused.

## Weight

The payload is 8.2 MB and every viewer pulls all of it. It is gzipped to
**1.07 MB** (0.09 s, memoised on the file's size and mtime, so nine viewers cost
one compression). Equity curves are not in it — the detail view fetches one at a
time by byte range — which is why the page is usable on a phone.

It is still not cached, for the same reason it never was: the page and the data
are only ever in step by version, and a viewer holding yesterday's payload
behind today's page renders blank with nothing on screen to explain it.

---

## Traps

- **Do not point the tunnel at the HTTPS wrapper.** `~/.kitelab-dev-tls/serve_https.py`
  exists because Safari refuses a plain-http *local* navigation; it is the right
  way to open the dashboard on your own Mac. A tunnel does not need it —
  Cloudflare terminates TLS, so the group gets `https://` either way — and
  `cloudflared` pointed at a self-signed local certificate is one more thing to
  go wrong. Run `./run_dashboard.sh` for tunnelling, the TLS wrapper for
  yourself. The wrapper imports `Handler` directly and so inherits interlock (2),
  but **not** (1): it does its own binding. Keep it on loopback.
- **`--host 0.0.0.0` is a different problem.** It is for running the server
  inside the dev container and opening the page from the host browser (loopback
  inside a container is unreachable no matter how the port is published). It is
  not how you share with people, and it demands a passphrase just the same.
- **A passphrase on the command line lands in your shell history and in `ps`**,
  where every user on the machine can read it. `--passphrase` exists; prefer
  `KITELAB_PASSPHRASE`, and keep it in `~/.secrets/all.env` with the rest.
- **The link changes every restart.** A quick tunnel mints a fresh hostname each
  time. If the group ever needs a bookmark that survives, that is a *named*
  Cloudflare tunnel and a domain — and at that point per-person logins become
  worth having instead of one shared passphrase.
- **Your laptop is the server.** Close the lid and the link dies mid-sentence for
  everyone. `caffeinate -i ./run_dashboard.sh` is the fix, as on livedesk.
- **The staleness banner still applies, and now other people see it.** If the
  page says the numbers are out of date, they are out of date for the whole
  group; `/api/status` checks the live config on every request, so it tells the
  truth to every viewer independently.

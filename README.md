# kitelab

A local backtesting workbench for rules-based swing trading on NSE equities, built
on Kite Connect. Data acquisition, cleaning, ~1,000-stock strategy simulation, an
account-level grid, a validation layer and a local dashboard. `CLAUDE.md` is the
current description of the system; this file keeps the setup steps and the
sections marked as still accurate there.

## Setup

```
cp config.example.toml config.local.toml
```

Edit `config.local.toml` and fill in your `api_key` and `api_secret` from
<https://developers.kite.trade/apps>. That file is gitignored — keep the values in it
and nowhere else.

On the host, dependencies live in `.venv`; inside the dev container that venv
is a dead symlink and plain `python3` (3.12, pandas installed) is the interpreter
for everything below. If you ever need to rebuild the venv:

```
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

## Daily use

Three jobs, deliberately separate, because they need different things.

### 1. Get new data — the only job that needs a Zerodha account

```
./.venv/bin/python -m scripts.login       # once a day; tokens die at ~06:00 IST
./.venv/bin/python -m scripts.backfill    # first run pulls history, later runs top up
```

This writes raw candles and stops there. It does not clean, and it does not touch
the dashboard.

The login prints a URL. You open it, log in to Zerodha yourself, and paste back the
redirect URL. Nothing in this project asks for or stores your password, PIN, or TOTP.

### 2. Refresh the dashboard — needs the venv, needs no account

```
./.venv/bin/python -m scripts.refresh           # do whatever is stale
./.venv/bin/python -m scripts.refresh --check   # say what is stale, change nothing
```

It decides for itself what work is needed: cleans raw files that arrived since the
last clean, and rebuilds `dashboard.json` if the universe, the price data or the
strategy code has moved. When nothing has changed it prints "already current" in a
second, which matters because a full rebuild is long (time it before quoting a
number; `output/rebuild_*.log` records the last one).

### 3. Look at it — needs neither

```
./run_dashboard.sh
```

Standard library only. No venv, no API keys, no computation: it serves what is on
disk and opens your browser. If the numbers are out of date the page says so, in a
red banner naming what changed.

### What needs what

| | Zerodha account | venv (pandas etc.) |
|---------------------------|:---:|:---:|
| `scripts.backfill` (get data) | yes | yes |
| `scripts.refresh` (rebuild)   | no  | yes |
| `./run_dashboard.sh` (view)   | no  | no  |

`config.load()` never asks for credentials. Only `kitelab/auth.py` reads them, and
only the three fetching programs call `config.require_secrets()`.

## Timeframes

Kite daily candles are the price source. Weekly and monthly bars are grouped from
the cleaned daily frame; fetching waits until 16:00 IST so the closing auction price
has time to appear.

| Timeframe | Source |
|-----------|--------|
| `1d` | Kite native daily candle |
| `1w` | grouped from daily candles |
| `1M` | grouped from daily candles |

```python
from kitelab import frames
df = frames.load("HINDZINC", "1w")
```

## Verify before trusting a backtest

**The resampled timeframes are computed here, not by Zerodha.** Kite serves no weekly
or monthly candles at all. Run
`python -m scripts.show HAL 1w -n 12` and compare against TradingView before relying on
them.

**Corporate-action adjustment looks correct but is not proven.** Across all four
symbols the only daily move over 20% is HAL on 2018-11-09, and that one opened just
+3.1% and then travelled intraday on 53x normal volume — the signature of a real move,
not a split, which would arrive as a gapped open on ordinary volume. `scripts.diagnose`
now applies that gap-versus-volume test automatically to anything it flags.

## Known limits

- **Survivorship bias.** The universe comes from the current instrument list, so
  delisted companies are invisible. With ~1,000 stocks screened from that list it
  is the largest known bias in every number here (estimated ~4.9pp/yr on
  2026-09-03), and nothing in the repo corrects for it. Since 2026-09-07 the
  validation layer at least tests each rule against random entries on the same
  survivors, so the drift they carry is no longer credited to the rule.
- **HYUNDAI has little history** — listed October 2024, so roughly 23 monthly bars.
  Monthly analysis on it is not meaningful.
- **Expired derivatives are unrecoverable.** Kite only returns instrument tokens for
  live contracts. If options backtesting is ever wanted, the daily instrument dump has
  to be snapshotted from day one. `fetch.py` already caches it per day.

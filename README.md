# kitelab

A local historical-data workbench for manual backtesting, built on Kite Connect.

Stage 1 only: data acquisition and timeframe construction. No screener, no chart UI yet.

## Setup

```
cp config.example.toml config.local.toml
```

Edit `config.local.toml` and fill in your `api_key` and `api_secret` from
<https://developers.kite.trade/apps>. That file is gitignored — keep the values in it
and nowhere else.

Dependencies are already installed in `.venv`. If you ever need to rebuild it:

```
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

## Daily use

```
./.venv/bin/python -m scripts.login       # once a day; tokens die at ~06:00 IST
./.venv/bin/python -m scripts.backfill    # first run pulls history, later runs top up
./.venv/bin/python -m scripts.show        # coverage + health check
```

The login prints a URL. You open it, log in to Zerodha yourself, and paste back the
redirect URL. Nothing in this project asks for or stores your password, PIN, or TOTP.

## Timeframes

15-minute candles are the only intraday interval fetched. Everything else is derived:

| Timeframe | Source                                            |
|-----------|---------------------------------------------------|
| `15m`     | Kite, native                                      |
| `30m`     | resampled from 15m                                |
| `1h`      | resampled from 15m                                |
| `1d`      | Kite native if `use_daily_source = true`, else from 15m |
| `1w`      | grouped from the daily frame                      |
| `1M`      | grouped from the daily frame                      |

```python
from kitelab import frames
df = frames.load("HINDZINC", "1w")
```

### Why `use_daily_source` defaults to true

Kite's intraday history is shallow (roughly 2015 onward) while its daily history goes
back much further. Deriving monthly candles from 15-minute bars would truncate your
monthly chart to whatever the intraday feed covers. Fetching the `day` interval costs
one extra request per symbol — the daily interval allows 2000 days in a single call —
and preserves the full history on `1d`, `1w` and `1M`. Set it to `false` if you want a
pure 15-minute derivation instead.

## The 2026-08-03 session change

NSE introduced a Closing Auction Session on 3 August 2026. For stocks covered by it —
currently those with active derivatives contracts, which includes all four symbols here
— continuous trading now ends at **15:15**, and an auction from 15:15 to 15:35 sets the
official closing price.

|                | before 2026-08-03 | from 2026-08-03 |
|----------------|-------------------|-----------------|
| continuous session | 09:15–15:30 (375 min) | 09:15–15:15 (360 min) |
| 15m bars/day   | 25                | 24              |
| 30m bars/day   | 12 + a 15-min stub at 15:15 | 12, no stub |
| 1h bars/day    | 6 + a 15-min stub at 15:15  | 6, no stub  |

Two consequences:

- **The daily close is no longer the last intraday close.** It is the auction price
  struck around 15:35, while the last 15-minute bar ends at 15:15. Deriving daily bars
  from intraday data would miss the auction entirely, which is the main reason
  `use_daily_source` defaults to true.
- **A backtest spanning that date contains a regime change in the data itself.**
  Close-based results either side of it are not strictly comparable.

`frames.expected_bars(date)` encodes this, and both `scripts.show` and
`scripts.diagnose` judge each session against the rule that applied on its own date.

## Verify before trusting a backtest

**The resampled timeframes are computed here, not by Zerodha.** Kite serves no weekly
or monthly candles at all, and 30m/1h are rebuilt locally. Run
`python -m scripts.show HAL 1w -n 12` and compare against TradingView before relying on
them.

**Corporate-action adjustment looks correct but is not proven.** Across all four
symbols the only daily move over 20% is HAL on 2018-11-09, and that one opened just
+3.1% and then travelled intraday on 53x normal volume — the signature of a real move,
not a split, which would arrive as a gapped open on ordinary volume. `scripts.diagnose`
now applies that gap-versus-volume test automatically to anything it flags.

## Known limits

- **IRFC intraday before 2021-01-29 is not the equity.** Kite serves 15-minute bars
  from 2018 under the IRFC symbol, but the stock only listed in January 2021, and those
  years contain 52 / 97 / 4 sessions instead of ~250 — a thinly traded instrument,
  almost certainly listed debt. `frames.base_15m` drops any intraday bar that predates
  the first native daily bar and prints what it dropped.
- **Survivorship bias.** The universe comes from the current instrument list, so
  delisted companies are invisible. Not an issue for four hand-picked symbols; it
  becomes one the moment you screen a broad universe.
- **HYUNDAI has little history** — listed October 2024, so roughly 23 monthly bars.
  Monthly analysis on it is not meaningful.
- **Expired derivatives are unrecoverable.** Kite only returns instrument tokens for
  live contracts. If options backtesting is ever wanted, the daily instrument dump has
  to be snapshotted from day one. `fetch.py` already caches it per day.

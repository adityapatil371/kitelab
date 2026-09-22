"""The user's TradingView strategy, ported and measured on kitelab's board.

    python3 -m scripts.wf_pine --verify RELIANCE   # one symbol, show the maths
    python3 -m scripts.wf_pine --pilot 40          # time it, print an estimate
    python3 -m scripts.wf_pine                     # the full run
    python3 -m scripts.wf_pine --ablate            # which component does the work

THE SOURCE. Pine v5, "HA + RSI Entry / HTF EMA Trend Filter":

    trendUp    = close > request.security(sym, "W", ta.ema(close, 20), lookahead_off)
    bullNoWick = haC > haO and (haO - haL) <= (haH - haL) * 0.08
    long       = trendUp and bullNoWick and rsi(close,14) > 50 and rsi > rsi[1]
    on long: entry at market; exit stop = close - 1.5*ATR(14), limit = close + 3*ATR(14)
    also close the long when haC < haO and (haH - haO) > (haH - haL) * 0.08

    Heikin Ashi:  haC = (O+H+L+C)/4,  haO = (haO[1] + haC[1])/2  (seed (O+C)/2),
                  haH = max(H, haO, haC),  haL = min(H is not it -- L, haO, haC).
    RSI, ATR and the HTF EMA all read the REAL close, not the HA close: the Pine
    runs on a normal chart and pulls HA bars in through request.security.

WHY IT IS OUT OF BAND. Registering this rule in kitelab/registry.py would put it
in signals._SUPPORT and invalidate every signal cache on the board -- a ~58
minute rebuild to answer one question. A new file under scripts/ that is not
dashboard_data.py is in neither stamp tier, so it invalidates nothing. This
script loads the same prices, applies the same spread, calls the same
portfolio.run and reuses the same hold curves as scripts/wf_attach.py, so the
answer lands on the same axes as the nine rules already on the board.

THE FOUR PLACES THE PINE AND THIS PROJECT DISAGREE, all run both ways:

  1. LONG AND SHORT vs LONG ONLY. portfolio.py is long-only, and NSE cash
     equity cannot be held short overnight by a retail account anyway (that
     needs stock futures, which exist for ~200 of the 1,000 names here). The
     long side goes through the account engine; the short side is reported at
     trade level only and is NOT tradable as written.
  2. THE STOP. The Pine risks 1.5*ATR(14); every rule on this board risks to
     the entry candle's own low. Variants `atr` and `low`.
  3. THE FILL. The Pine fills at the next session's OPEN; the board fills at
     the signal bar's own CLOSE. Variants `open` and `close`.
  4. WHERE THE STOP IS ANCHORED. The Pine computes `close - 1.5*ATR` on the
     SIGNAL bar but fills on the NEXT one, so its risk is measured from a price
     it did not pay. Variant `atrfill` re-anchors to the actual fill and is the
     size of that defect.

  Not run both ways, and why: POSITION SIZE. The Pine is
  default_qty_value=100 percent_of_equity -- the entire account in one name,
  every time. Nothing on this board does that and it is not a portfolio, so
  sizing is kitelab's (risk 1% of equity to the stop). And COSTS: the Pine
  assumes a flat 0.05% commission; this uses the measured Zerodha + statutory
  schedule plus spread plus the 1%-of-turnover participation cap. A great deal
  of the difference between a TradingView equity curve and this one lives in
  those two lines.

Reads:  /data/clean/kitelab/dashboard.json      (axes, universe labels, hold key)
        the cleaned parquet candles, via kitelab.frames
        output/wf_attach_hold_<board key>.pkl   (hold curves, if already built)
Writes: output/wf_pine_ckpt_<board key>/*.pkl   (one per variant, resumable)
        output/measurements/wf_pine_<date>.json              (every cell)
        output/measurements/wf_pine_<date>.csv  (the same, flat)
"""
from __future__ import annotations

import argparse
import bisect
import json
import pickle
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import config, frames, indicators, portfolio, sizing, slippage
# Re-exported, not redefined: scripts/pine_span.py, scripts/band_scope.py,
# scripts/pine_candidate.py and scripts/pine_ablate_fix.py all reach these
# through this module, and every one of them must see the rule the BOARD
# trades rather than a second copy of it.
from kitelab.pine import EMA_LEN, TIMEFRAMES, entry_mask, signals
from kitelab.backtest import charges
from kitelab.config import CLEAN
from scripts import dashboard_data as dd
from scripts import wf_attach as wa
from scripts.wf_daily import MIN_DAYS

OUT = Path(__file__).resolve().parent.parent / "output"
MEAS = OUT / "measurements"        # every finished CSV or JSON result
MEAS.mkdir(parents=True, exist_ok=True)

# Everything the SIGNAL needs now lives in kitelab/pine.py and is imported
# above; only the trade-construction settings are still declared here. Keeping
# a second copy of EMA_LEN or WICK_TOL in this file is precisely how the rule
# this script measures could drift from the rule the board trades.
ATR_STOP, ATR_TP = 1.5, 3.0

VARIANTS = [
    ("atr", "open"),       # the Pine as written: ATR stop off the signal close,
                           # filled at the next open
    ("atrfill", "open"),   # the same, with the stop re-anchored to what was paid
    ("atr", "close"),      # the Pine's stop, the board's fill
    ("low", "close"),      # the board's stop and fill -- comparable to the nine
    ("low", "open"),
]


def vkey(stop, fill, tf="D"):
    return f"pine{'' if tf == 'D' else tf}|{stop}-{fill}"


# ------------------------------------------------------------- signals ----
# MOVED TO kitelab/pine.py ON 2026-09-19, and re-exported here so this file's
# own readers (scripts/pine_span.py calls P.signals and P.entry_mask) keep
# working unchanged. The move was forced by NEXT_TESTS item 24: the rule became
# a board ENTRY that day, and a board signal produced from scripts/ would sit
# outside the tree signals.stamp() walks. See kitelab/pine.py's docstring for
# what moved and what stayed.
# -------------------------------------------------------------- trades ----
def trades_for(symbol, sig=None, *, tf="D", side="long", stop_mode="atr", fill="open",
               use_htf=True, use_wick=True, use_rsi=True) -> list[dict]:
    """Walk the signals and return closed trades, oldest first.

    The loop is scripts/backtest.simulate's, with three differences the Pine
    forces: the entry is ANY qualifying bar while flat rather than a rising edge
    (Pine's pyramiding=0 ignores repeats in-position, it does not require a
    fresh cross); the stop and target are live INTRABAR orders, because that is
    what strategy.exit places; and the HA-reversal exit is a market order, so it
    fills at the next open whenever the entry does. Same-bar stop-and-target
    goes to the stop, the house convention.
    """
    if sig is None:
        sig = signals(symbol, tf)
    total = len(sig)
    if total == 0:
        return []
    long = side == "long"
    sign = 1.0 if long else -1.0
    ok = entry_mask(sig, side, use_htf=use_htf, use_wick=use_wick, use_rsi=use_rsi)
    rev = sig["ha_exit_long" if long else "ha_exit_short"].to_numpy()
    o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = sig["atr"].to_numpy(float)
    # THE STAMP IS THE SESSION THE DECISION WAS TAKEN ON, and it moves with the
    # fill. An aggregated bar is dated to its FIRST session but closes on its
    # LAST, so a Mon->Fri weekly bar carries a Monday stamp. Stamping a
    # close-filled weekly entry on that Monday makes portfolio.run commit cash
    # four sessions before the price it paid existed, and makes the daily curve
    # mark the position from Monday at Friday's price. A next-open fill really
    # does happen on the fill bar's opening session, so that one is stamped on
    # `ts`. Both rules are kitelab/timeframes.py:195-218's, followed here so the
    # weekly numbers sit on the same footing as the board's weekly rules.
    # Daily frames carry no end_ts and are unaffected: stamp == session.
    closed_on = (sig["end_ts"] if "end_ts" in sig.columns else sig["ts"]).tolist()
    opened_on = sig["ts"].tolist()
    months = sig["months_done"].to_numpy()
    next_open = fill == "open"

    trades, position = [], 0
    while position < total:
        if not ok[position] or not np.isfinite(atr[position]):
            position += 1
            continue

        ref = c[position]                       # the Pine anchors on the signal close
        if next_open:
            if position + 1 >= total:
                break
            entry_index = position + 1
            entry_price = float(o[entry_index])
            entry_stamp = opened_on[entry_index]
        else:
            entry_index = position
            entry_price = float(c[position])
            entry_stamp = closed_on[entry_index]

        if stop_mode == "atr":
            stop = ref - sign * ATR_STOP * atr[position]
            target = ref + sign * ATR_TP * atr[position]
        elif stop_mode == "atrfill":
            stop = entry_price - sign * ATR_STOP * atr[position]
            target = entry_price + sign * ATR_TP * atr[position]
        elif stop_mode == "low":
            stop = float(l[position]) if long else float(h[position])
            target = None
        else:
            raise SystemExit(f"unknown stop_mode {stop_mode!r}")

        if sign * (entry_price - stop) <= 0:
            # It opened through the line you were going to defend. You would not
            # take the trade, so the signal is skipped rather than filled.
            position += 1
            continue

        exit_at = None
        for step in range(entry_index if next_open else position + 1, total):
            hit_stop = (l[step] <= stop) if long else (h[step] >= stop)
            if hit_stop:
                gapped = (o[step] < stop) if long else (o[step] > stop)
                exit_at = (step, float(o[step]) if gapped else float(stop),
                           "gap through stop" if gapped else "stop", False)
                break
            if target is not None:
                hit_tp = (h[step] >= target) if long else (l[step] <= target)
                if hit_tp:
                    gapped = (o[step] > target) if long else (o[step] < target)
                    exit_at = (step, float(o[step]) if gapped else float(target),
                               "target", False)
                    break
            if rev[step]:
                exit_at = (step, float(c[step]), "ha reversal", True)
                break
        if exit_at is None:
            break                               # still open at the end of the data

        exit_index, exit_price, reason, market = exit_at
        if market and next_open:
            # strategy.close() is a market order: it fills on the next bar.
            if exit_index + 1 >= total:
                break
            exit_index += 1
            exit_price = float(o[exit_index])
            exit_stamp = opened_on[exit_index]
        elif next_open and not market:
            # A stop or target fills INTRABAR, on some session inside the bar we
            # cannot name. The bar's closing session is the honest upper bound:
            # dating it any earlier would free the account's cash before the
            # trade could have ended.
            exit_stamp = closed_on[exit_index]
        else:
            exit_stamp = closed_on[exit_index]

        risk = abs(entry_price - stop)
        # sizing.position() reads (price, stop) and refuses a stop above the entry,
        # so a short passed its real stop sized to zero shares and EVERY short trade
        # was silently dropped (found 2026-09-11, after the first full run). The
        # sizing rule is direction-free -- it needs the price and the risk per
        # share -- so both sides are sized as the equivalent long. The long numbers
        # are unaffected: sign is +1 there and this was already entry_price - risk.
        shares, risk_taken, capped = sizing.position(entry_price, entry_price - risk)
        if shares <= 0 or (not sizing.FRACTIONAL and shares < 1):
            position = exit_index + 1
            continue
        quoted_entry, quoted_exit = entry_price, exit_price
        entry_price = slippage.fill(symbol, entry_stamp, quoted_entry, int(sign))
        exit_price = slippage.fill(symbol, exit_stamp, quoted_exit, -int(sign))
        buy_value, sell_value = entry_price * shares, exit_price * shares
        gross = sign * (exit_price - entry_price) * shares
        spread_cost = sign * ((quoted_exit - exit_price) + (entry_price - quoted_entry)) * shares
        same_session = entry_stamp.date() == exit_stamp.date()
        cost = charges(buy_value, sell_value, intraday=same_session)

        trades.append({
            "symbol": symbol, "side": side, "tf": tf,
            "entry_ts": entry_stamp, "exit_ts": exit_stamp,
            "same_session": same_session, "entry_time": "EOD",
            "level": None, "level_kind": f"pine {stop_mode}",
            "max_risk_capital": sizing.risk_budget(), "risk_taken": risk_taken,
            "capital_capped": capped, "range": risk, "target": target,
            "final_stop": stop, "bars_held": exit_index - entry_index,
            "charges_best": cost, "net_profit_best": gross - cost,
            "entry_date": entry_stamp, "entry_price": entry_price,
            "quoted_entry": quoted_entry, "quoted_exit": quoted_exit,
            "spread_cost": spread_cost, "stop": stop, "risk_per_share": risk,
            "exit_date": exit_stamp, "exit_price": exit_price,
            "exit_reason": reason,
            "days_held": (exit_stamp - entry_stamp).days,
            "sessions_held": exit_index - entry_index, "shares": shares,
            "cost_of_entry": buy_value, "gross_profit": gross,
            "r_multiple": (gross / risk_taken) if risk_taken else 0.0,
            "charges": cost, "net_profit": gross - cost,
            "months_done": int(months[position]),
        })
        position = exit_index + 1
    return trades


def build_all(symbols, *, side, stop_mode, fill, tf="D", quiet=False):
    """Every symbol's trades under one convention, with the signal frame reused."""
    out, skipped = [], 0
    for i, sym in enumerate(symbols, 1):
        try:
            sig = signals(sym, tf)
        except SystemExit as exc:
            print(f"    {sym}: {exc}")
            skipped += 1
            continue
        if len(sig) == 0:
            skipped += 1
            continue
        out.extend(trades_for(sym, sig, tf=tf, side=side, stop_mode=stop_mode,
                              fill=fill))
        if not quiet and i % 200 == 0:
            print(f"    {i}/{len(symbols)} symbols, {len(out):,} trades", flush=True)
    if not quiet:
        print(f"    {len(symbols) - skipped} symbols usable ({skipped} too short "
              f"or unreadable) -> {len(out):,} trades")
    return out


# ---------------------------------------------------------------- cells ----
def cells_for(trades, unis, holds, hold_cagr, label):
    """The grid loop, straight out of wf_attach.cells_for so the two agree."""
    cells = {}
    for ukey, members in unis.items():
        subset = (trades if members is None
                  else [t for t in trades if t["symbol"] in members])
        if not subset:
            continue
        subset = sorted(subset, key=lambda t: t["entry_ts"])
        stamps = [pd.Timestamp(t["entry_ts"]) for t in subset]
        hold = holds.get(ukey)
        live_years = dd.gridded_years(stamps)
        for year in dd.START_YEARS:
            cut = bisect.bisect_left(stamps, pd.Timestamp(f"{year}-01-01"))
            window = subset[cut:] if year in live_years else []
            if not window:
                continue
            for prio in dd.PRIORITIES:
                for risk in dd.RISKS:
                    for capital in dd.CAPITALS:
                        r = portfolio.run(window, capital, risk / 100, prio)
                        pay = dd.run_payload(r)
                        held = hold_cagr.get(f"{ukey}|{year}")
                        st = wa.excess_stats(r.get("curve") or [], hold)
                        key = (f"{label}|{ukey}|{risk:g}|{capital}|1|{year}|{prio}")
                        cells[key] = {
                            "variant": label, "universe": ukey, "risk": risk,
                            "capital": capital, "start": year, "priority": prio,
                            "cagr": pay["cagr"], "taken": pay["taken"],
                            "wiped": bool(pay["wiped"]), "hold_cagr": held,
                            "beats_hold": (None if (held is None or pay["wiped"]
                                                    or pay["cagr"] is None)
                                           else bool(pay["cagr"] > held)),
                            "daily": st,
                        }
    return cells


def hold_cagrs(holds):
    """Buy-and-hold CAGR per (universe, start year), from the same daily curves."""
    out = {}
    for ukey, series in holds.items():
        if series is None or len(series) < MIN_DAYS:
            continue
        for year in dd.START_YEARS:
            w = series[series.index >= pd.Timestamp(f"{year}-01-01")]
            if len(w) < MIN_DAYS:
                continue
            yrs = (w.index[-1] - w.index[0]).days / 365.25
            if yrs <= 0 or w.iloc[0] <= 0:
                continue
            out[f"{ukey}|{year}"] = round(100 * ((w.iloc[-1] / w.iloc[0]) ** (1 / yrs) - 1), 2)
    return out


def spread_of_shorts(trades):
    """The project's spread model is LONG-ONLY; price a short as its mirror.

    slippage.apply_spread refuses any trade whose gross_profit is not
    (exit - entry) x shares -- a structural guard against multi-leg trades,
    and a short violates it by construction. Editing slippage.py is not an
    option: it sits in signals._SUPPORT, so a one-line change there invalidates
    all nine signal caches and costs a ~58-minute rebuild.

    So each short is handed over with its two legs SWAPPED: entry_price becomes
    the cover and exit_price the original sale, and the two timestamps swap with
    them. apply_spread then buys the cover leg at its own day's ask and sells the
    entry leg at its own day's bid, which is exactly the pair of crossings a real
    short pays, on the right dates.

    ONE THING IT GETS WRONG, stated rather than hidden: charges() bills the
    NSE equity schedule, where STT falls on the SELL side and differs between
    intraday and delivery. A delivery short is not a thing a cash account can
    do at all, so there is no correct schedule to bill -- these numbers are an
    approximation on the cost side of a position that cannot be held anyway.
    """
    mirrored = []
    for t in trades:
        m = dict(t)
        m["entry_price"], m["exit_price"] = t["exit_price"], t["entry_price"]
        m["entry_ts"], m["exit_ts"] = t["exit_ts"], t["entry_ts"]
        mirrored.append(m)
    return wa.spread_of(mirrored)


def trade_stats(trades):
    """Trade-level summary -- the only reading the short side gets."""
    if not trades:
        return {"n": 0}
    r = np.array([t["r_multiple"] for t in trades], float)
    net = np.array([t["net_profit"] for t in trades], float)
    wins = r > 0
    reasons = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return {
        "n": len(trades), "symbols": len({t["symbol"] for t in trades}),
        "win_rate": round(100 * wins.mean(), 1),
        "avg_win_r": round(float(r[wins].mean()), 2) if wins.any() else None,
        "avg_loss_r": round(float(r[~wins].mean()), 2) if (~wins).any() else None,
        "expectancy_r": round(float(r.mean()), 3),
        "median_days": int(np.median([t["days_held"] for t in trades])),
        "gross": round(float(sum(t["gross_profit"] for t in trades))),
        "charges": round(float(sum(t["charges"] for t in trades))),
        "net": round(float(net.sum())),
        "exits": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }


# ----------------------------------------------------------------- runs ----
def load_board():
    payload = json.loads((CLEAN / "dashboard.json").read_text())
    print(f"  dashboard.json built {payload['built']}, {len(payload['grid'])} grid cells")
    return payload


def universes(members, payload):
    unis = wa.stock_universes(members)
    wa.assert_buckets_match_payload(unis, payload)
    return unis


def verify(symbol, tf="D"):
    """One symbol, every intermediate column printed, as the house rules require."""
    sig = signals(symbol, tf)
    print(f"\n{symbol} on {tf} -- {TIMEFRAMES[tf][0]}")
    print(f"signal frame {sig.shape[0]} rows x {sig.shape[1]} columns")
    cols = ["ts", "open", "high", "low", "close", "ha_open", "ha_high", "ha_low",
            "ha_close", "htf_ema", "rsi", "atr"]
    print(sig[cols].head(3).to_string(index=False))
    print("\n  hand-check of the Heikin Ashi seed and one recursion:")
    o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    print(f"    haC[0] = (O+H+L+C)/4 = {(o[0]+h[0]+l[0]+c[0])/4:.4f}  "
          f"frame says {sig['ha_close'].iloc[0]:.4f}")
    print(f"    haO[0] = (O+C)/2     = {(o[0]+c[0])/2:.4f}  "
          f"frame says {sig['ha_open'].iloc[0]:.4f}")
    print(f"    haO[1] = (haO0+haC0)/2 = "
          f"{(sig['ha_open'].iloc[0]+sig['ha_close'].iloc[0])/2:.4f}  "
          f"frame says {sig['ha_open'].iloc[1]:.4f}")
    _, higher = TIMEFRAMES[tf][1](frames.daily(symbol).reset_index(drop=True))
    he = indicators.ema(higher["close"], EMA_LEN)
    row = sig.index[sig["htf_ema"].notna()][0]
    dec = (sig["end_ts"] if "end_ts" in sig.columns else sig["ts"]).iloc[row]
    print(f"\n  the filter reads the last CLOSED higher bar. Base bar {row} is stamped "
          f"{sig['ts'].iloc[row].date()} but DECIDES on {dec.date()};")
    print(f"    the frame says htf_ema {sig['htf_ema'].iloc[row]:.4f}")
    hk = np.searchsorted(higher["ts"].to_numpy(), np.datetime64(dec), side="right") - 1
    print(f"    higher bar {hk} starts {higher['ts'].iloc[hk].date()} (still forming, "
          f"EMA {he.iloc[hk]:.4f}); bar {hk-1} starts "
          f"{higher['ts'].iloc[hk-1].date()}, EMA {he.iloc[hk-1]:.4f}  <- used")
    for name in ("trend_up", "bull_nowick", "rsi_long", "ha_exit_long"):
        print(f"  {name:<14} true on {int(sig[name].sum()):>6,} of {len(sig):,} bars")
    both = entry_mask(sig, "long")
    print(f"  {'long entry':<14} true on {int(both.sum()):>6,} bars "
          f"({100*both.mean():.2f}%)")
    for stop_mode, fill in VARIANTS:
        t = trades_for(symbol, sig, tf=tf, side="long", stop_mode=stop_mode, fill=fill)
        s = trade_stats(t)
        print(f"  {vkey(stop_mode, fill, tf):<20} {s['n']:>4} trades  "
              f"win {s.get('win_rate')}%  expectancy {s.get('expectancy_r')}R  "
              f"exits {s.get('exits')}")


def pilot(symbols, n, tf="D"):
    sub = symbols[:n]
    print(f"\nPILOT: {len(sub)} symbols x {len(VARIANTS)} variants on {tf}")
    t0 = time.time()
    counts = {}
    for stop_mode, fill in VARIANTS:
        s0 = time.time()
        tr = build_all(sub, side="long", stop_mode=stop_mode, fill=fill, tf=tf, quiet=True)
        counts[vkey(stop_mode, fill, tf)] = (len(tr), time.time() - s0)
        print(f"  {vkey(stop_mode, fill, tf):<20} {len(tr):>6,} trades  "
              f"{time.time()-s0:>6.1f}s")
    per = (time.time() - t0) / len(sub)
    print(f"\n  {per*1000:.0f} ms per symbol for all {len(VARIANTS)} variants")
    print(f"  {len(symbols)} symbols -> trade stage ~{per*len(symbols)/60:.1f} min")
    k = vkey("atr", "open", tf)
    print(f"  scaling the pilot's {counts[k][0]:,} trades to the full universe: "
          f"~{counts[k][0]*len(symbols)//len(sub):,} per variant")
    return per


def ablate(symbols, unis, holds, hold_cagr, tf="D"):
    """Which of the three entry legs is doing the work, on one cell."""
    print(f"\nABLATION on {tf} ({TIMEFRAMES[tf][0]}) -- stop=atr, fill=open, "
          f"universe all, start 2018, risk 1%, capital 10,000,000, "
          f"priority {dd.PRIORITY_DEFAULT}")
    legs = [("full rule", dict()),
            ("no HTF EMA filter", dict(use_htf=False)),
            ("no wick condition", dict(use_wick=False)),
            ("no RSI condition", dict(use_rsi=False)),
            ("HTF filter alone", dict(use_wick=False, use_rsi=False)),
            ("wick alone", dict(use_htf=False, use_rsi=False)),
            ("RSI alone", dict(use_htf=False, use_wick=False))]
    held = hold_cagr.get("all|2018")
    print(f"  buy-and-hold on the same window: {held} %/yr")
    print(f"  {'leg':<20} {'trades':>8} {'win%':>6} {'expct R':>8} "
          f"{'CAGR':>7} {'vs hold':>8}")
    rows = []
    for name, kw in legs:
        tr = []
        for sym in symbols:
            try:
                sig = signals(sym, tf)
            except SystemExit:
                continue
            if len(sig) == 0:
                continue
            tr.extend(trades_for(sym, sig, tf=tf, side="long", stop_mode="atr",
                                 fill="open", **kw))
        tr = wa.spread_of(tr)
        st = trade_stats(tr)
        window = [t for t in sorted(tr, key=lambda t: t["entry_ts"])
                  if pd.Timestamp(t["entry_ts"]) >= pd.Timestamp("2018-01-01")]
        cagr = None
        if window:
            pay = dd.run_payload(portfolio.run(window, 10_000_000, 0.01,
                                               dd.PRIORITY_DEFAULT))
            cagr = pay["cagr"]
        gap = None if (cagr is None or held is None) else round(cagr - held, 2)
        print(f"  {name:<20} {st['n']:>8,} {st.get('win_rate'):>6} "
              f"{st.get('expectancy_r'):>8} {str(cagr):>7} {str(gap):>8}", flush=True)
        rows.append({"leg": name, **st, "cagr": cagr, "excess": gap})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", metavar="SYMBOL")
    ap.add_argument("--pilot", type=int, metavar="N")
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--symbols", type=int, help="cap the universe (testing only)")
    ap.add_argument("--tf", default="D", choices=sorted(TIMEFRAMES),
                    help="D = the Pine as written (daily bars, weekly filter); "
                         "W = the same rule stepped up (weekly bars, monthly filter)")
    args = ap.parse_args()

    slippage.ENABLED = False
    slippage.MAX_PARTICIPATION = None
    slippage.reset()

    if args.verify:
        verify(args.verify, args.tf)
        return

    cfg = config.load()
    members = sorted(cfg.merged)
    if args.symbols:
        members = members[:args.symbols]
    print(f"universe: {len(members)} symbols")

    print(f"timeframe {args.tf}: {TIMEFRAMES[args.tf][0]}")
    if args.pilot:
        pilot(members, args.pilot, args.tf)
        return

    payload = load_board()
    unis = universes(set(members), payload)
    board = wa.board_key(payload)
    holds = wa.hold_curves(set(members), unis, [], board)
    hold_cagr = hold_cagrs(holds)
    print(f"  hold CAGR by cell: {len(hold_cagr)} entries, "
          f"all|2018 = {hold_cagr.get('all|2018')}")

    if args.ablate:
        ablate(members, unis, holds, hold_cagr, args.tf)
        return

    ck = OUT / f"wf_pine_ckpt_{args.tf}_{board}"
    ck.mkdir(parents=True, exist_ok=True)
    all_cells, summary = {}, {}
    t0 = time.time()
    for stop_mode, fill in VARIANTS:
        label = vkey(stop_mode, fill, args.tf)
        path = ck / f"{label.replace('|', '__')}.pkl"
        if path.exists():
            cells, stats, shorts = pickle.loads(path.read_bytes())
            print(f"  {label:<20} reused {len(cells)} cells")
        else:
            s0 = time.time()
            print(f"  {label}: building long trades", flush=True)
            raw = build_all(members, side="long", stop_mode=stop_mode, fill=fill,
                            tf=args.tf)
            stats = trade_stats(wa.spread_of(raw))
            cells = cells_for(wa.spread_of(raw), unis, holds, hold_cagr, label)
            print(f"  {label}: building short trades (trade level only)", flush=True)
            sraw = build_all(members, side="short", stop_mode=stop_mode, fill=fill,
                             tf=args.tf)
            shorts = trade_stats(spread_of_shorts(sraw)) if sraw else {"n": 0}
            path.write_bytes(pickle.dumps((cells, stats, shorts)))
            print(f"  {label:<20} {stats['n']:>7,} long trades -> "
                  f"{len(cells):>4} cells  {time.time()-s0:>6.0f}s", flush=True)
        all_cells.update(cells)
        summary[label] = {"long": stats, "short": shorts}
    print(f"\n  {len(all_cells)} cells in {(time.time()-t0)/60:.1f} min")

    stamp = date.today().isoformat()
    blob = {"built": payload["built"], "board": board, "generated": stamp,
            "timeframe": args.tf, "timeframe_label": TIMEFRAMES[args.tf][0],
            "variants": [vkey(s, f, args.tf) for s, f in VARIANTS],
            "summary": summary, "cells": all_cells}
    jpath = MEAS / f"wf_pine_{args.tf}_{stamp}.json"
    jpath.write_text(json.dumps(blob, indent=1, default=str))
    flat = pd.DataFrame([
        {k: v for k, v in c.items() if k != "daily"} |
        {f"daily_{k}": v for k, v in (c["daily"] or {}).items()}
        for c in all_cells.values()])
    cpath = OUT / "measurements" / f"wf_pine_{args.tf}_{stamp}.csv"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    flat.to_csv(cpath, index=False)
    print(f"  wrote {jpath.name} and measurements/{cpath.name} "
          f"({len(flat)} rows x {len(flat.columns)} columns)")
    report(flat, summary)


def report(flat, summary):
    print("\n" + "=" * 78)
    print("TRADE LEVEL")
    print(f"  {'variant':<20} {'side':<6} {'trades':>8} {'win%':>6} {'avg win':>8} "
          f"{'avg loss':>9} {'expct':>7} {'gross':>13} {'charges':>12}")
    for label, s in summary.items():
        for side in ("long", "short"):
            st = s[side]
            if not st.get("n"):
                continue
            print(f"  {label:<20} {side:<6} {st['n']:>8,} {st['win_rate']:>6} "
                  f"{str(st['avg_win_r']):>8} {str(st['avg_loss_r']):>9} "
                  f"{st['expectancy_r']:>7} {st['gross']:>13,} {st['charges']:>12,}")
    print("\nACCOUNT LEVEL, long only (every cell the board uses)")
    print(f"  {'variant':<20} {'cells':>6} {'med CAGR':>9} {'med hold':>9} "
          f"{'med excess':>11} {'beat hold':>10} {'med mde80':>10} {'pass':>5}")
    for label in summary:
        sub = flat[flat["variant"] == label]
        if sub.empty:
            continue
        ex = (sub["cagr"] - sub["hold_cagr"]).dropna()
        beat = sub["beats_hold"].dropna()
        mde = sub.get("daily_mde_80")
        big = 0
        if mde is not None:
            pair = sub[["daily_excess_pts", "daily_mde_80"]].dropna()
            big = int((pair["daily_excess_pts"] >= pair["daily_mde_80"]).sum())
        print(f"  {label:<20} {len(sub):>6} {sub['cagr'].median():>9.2f} "
              f"{sub['hold_cagr'].median():>9.2f} {ex.median():>11.2f} "
              f"{100*beat.mean():>9.1f}% "
              f"{(mde.median() if mde is not None else float('nan')):>10.2f} "
              f"{big:>5}")
    print("\n  'pass' counts cells whose measured excess is at least as large as the")
    print("  smallest excess this much data could detect 80% of the time. Anything")
    print("  else is a number the test could not have found even if it were real.")
    print("=" * 78)


if __name__ == "__main__":
    main()

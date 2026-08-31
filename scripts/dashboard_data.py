"""Precompute everything the local dashboard can show.

    python -m scripts.dashboard_data

Writes data/dashboard.json for http://localhost:8765/dashboard (served by
python -m scripts.dashboard). The page is a pure viewer -- every control selects
among these precomputed results, nothing is simulated in the browser.

Everything is combinable with everything:
    grid        one-account simulations for FOUR strategy stacks (EMA on
                M/W/D, Q/M/W and W/D/H timeframes, plus ATH Breakout)
                x universe (all 199 / 49 in-sample / 150 holdout)
                x risk (0.25-2%) x capital (50k-5L)
    scaleout    the "sell half at +1R" scale-out idea, per strategy AND per
                universe, columns matching the old Scale-Out Test sheet --
                measured per trade (a two-part exit cannot be priced by the
                one-account simulation)
    stocks      every one of the 199 stocks: weekly closes + trades + stats
                for all four stacks
    assets      the six class instruments: closes, per-stack trades/accounts,
                scale-out variants, buy-and-hold comparison (W/D/H exists only
                where hourly data does: BITCOIN and the two indices)
    timeframes  the classic 5-assigned-stock gross comparison tables
    nifty       NIFTY 50 overlay series

Conventions: EMA stacks use the class rule (stops checked at closes only);
Breakout keeps intrabar stops (its buy-stop entry is inherently intrabar).
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd

from kitelab import (backtest, config, darvas, frames, portfolio, sizing, slippage,
                     strategies)
from scripts.drawdown_report import bh_stats, episodes, underwater_stats
from scripts.tf_compare import (ASSIGNED, VARIANTS as TF_VARIANTS, simulate_variant,
                                summarise as tf_summarise, window_start)

CACHE = Path(__file__).resolve().parent.parent / "data" / "signal_cache"
OUT = Path(__file__).resolve().parent.parent / "data" / "dashboard.json"
RISKS = [0.25, 0.5, 1.0, 2.0]

# The fills dimension. "0" is every result this project produced before
# 2026-08-31: you get the chart price, instantly, at any size. "1" is what
# execution actually looks like -- you cross a spread, you move the price you
# are trading against, and you cannot buy more of a stock than it trades.
#
# The size limit is bundled in deliberately. Without it the model charges the
# account a fortune for orders it could never have placed (up to 37x a stock's
# ENTIRE daily turnover), which measures the simulator's sizing bug rather than
# the cost of dealing. The page says so under the slicer.
FILL_MODES = [("0", "Perfect fills"), ("1", "Realistic fills")]
REALISTIC_PARTICIPATION = 0.01     # one order <= 1% of the stock's daily turnover
CAPITALS = [50_000, 100_000, 250_000, 500_000]
ASSETS = [("BITCOIN", "30m", 0.0010), ("NIFTY 50", "30m", 0.0005),
          ("NIFTY BANK", "30m", 0.0005), ("GOLD", "1d", 0.0005),
          ("SILVER", "1d", 0.0005), ("CRUDEOIL", "1d", 0.0005)]
STEP = 10
BANDS = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
STRATEGY_LABELS = {"ema": "EMA · M/W/D", "qmw": "EMA · Q/M/W",
                   "wdh": "EMA · W/D/H", "brk": "ATH Breakout",
                   "dv": "Darvas channel"}

# Darvas has no band. It has a pair of windows instead, and they matter at least as
# much, so they ride in the same slot of the key that the band uses for the EMA
# stacks: "dv|20-10|all|1|250000|0". 20/10 is what the class specified and is the
# page default; it is not the best of them.
DARVAS_WINDOWS = [(10, 5), (20, 10), (20, 20), (40, 20), (55, 20)]
DARVAS_TAGS = [f"{a}-{b}" for a, b in DARVAS_WINDOWS]
DARVAS_DEFAULT = "20-10"

# The one setting each strategy shows on the per-stock page.
PRIMARY = {"ema": 0.02, "qmw": 0.02, "wdh": 0.02, "brk": 0.02, "dv": DARVAS_DEFAULT}


def tag(band) -> str:
    """Key fragment for the band slot: a number for the EMA stacks, a window pair
    like "20-10" for Darvas."""
    return f"{band:g}" if isinstance(band, (int, float)) else str(band)


# ------------------------------------------------------------ helpers ----

def curve_payload(curve):
    days, eq, dd = [], [], []
    peak = float("-inf")
    for i, (day, equity) in enumerate(curve):
        peak = max(peak, equity)
        if i % STEP and i != len(curve) - 1:
            continue
        days.append(day.strftime("%Y-%m-%d"))
        eq.append(round(equity))
        dd.append(round(100 * (equity - peak) / peak, 1))
    return {"d": days, "eq": eq, "dd": dd}


def run_payload(r):
    longest, current = underwater_stats(r["curve"])
    eps = [{"peak": str(e["peak_day"].date()), "trough": str(e["trough_day"].date()),
            "depth": round(e["depth_pct"], 1),
            "recovered": str(e["recovered"].date()) if e["recovered"] is not None else None,
            "years": round(e["days"] / 365.25, 1)} for e in episodes(r["curve"])]
    return {"cagr": round(r["cagr_pct"], 1), "final": round(r["final"]),
            "ret": round(r["return_pct"]), "maxdd": round(r["max_drawdown_pct"], 1),
            "uw_long": round(longest / 365.25, 1), "uw_now": round(current / 365.25, 1),
            "taken": len(r["taken"]), "signals": r["signals"],
            "episodes": eps, "curve": curve_payload(r["curve"])}


def cached_signals(name: str, build) -> list[dict]:
    path = CACHE / f"{name}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    cfg = config.load()
    out = []
    for index, symbol in enumerate(cfg.all_symbols, 1):
        try:
            out.extend(build(symbol))
        except SystemExit:
            pass
        if index % 25 == 0:
            print(f"    {name}: {index}/{len(cfg.all_symbols)}", flush=True)
    path.write_bytes(pickle.dumps(out))
    return out


def variant_builder(key: str, band: float = 0.02):
    """simulate_variant trades, padded with the fields portfolio.run needs."""
    def build(symbol):
        out = []
        for t in simulate_variant(symbol, key, band=band):
            t = dict(t)
            t["same_session"] = (pd.Timestamp(t["entry_ts"]).date()
                                 == pd.Timestamp(t["exit_ts"]).date())
            t["net_profit"] = t["gross_profit"] - t["charges"]
            out.append(t)
        return out
    return build


def capture_ratio(trades: list[dict]):
    """Points the strategy took / the stock's own move, averaged per stock.

    The class sheet's metric. A stock that ENDED LOWER than it started has a
    negative denominator, which makes the ratio meaningless rather than merely
    small, so those stocks are skipped instead of averaged in.
    """
    by_symbol: dict[str, list] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    caps = []
    for lst in by_symbol.values():
        lst = sorted(lst, key=lambda t: t["entry_ts"])
        move = lst[-1]["exit_price"] - lst[0]["entry_price"]
        if move <= 0:
            continue
        caps.append(sum(t["exit_price"] - t["entry_price"] for t in lst) / move)
    # MEDIAN, not mean: a stock that moved +5 points while the strategy lost 500
    # gives a ratio of -100, and a couple of those drag a mean into nonsense.
    if not caps:
        return None
    caps.sort()
    middle = len(caps) // 2
    value = caps[middle] if len(caps) % 2 else (caps[middle - 1] + caps[middle]) / 2
    return round(value, 4)


def trade_stats(trades: list[dict]) -> dict:
    nets = [t["net_profit"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    running = peak = 0.0
    worst_run = 0.0
    for t in sorted(trades, key=lambda t: t["exit_ts"]):
        running += t["net_profit"]
        peak = max(peak, running)
        worst_run = min(worst_run, running - peak)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {"trades": len(trades), "wins": len(wins), "losses": len(losses),
            "total_win": round(sum(wins)), "total_loss": round(sum(losses)),
            "avg_win": round(avg_win), "avg_loss": round(avg_loss),
            "expectancy": round(sum(nets) / len(nets)) if nets else 0,
            "rr": round(avg_win / abs(avg_loss), 2) if avg_loss else None,
            "capture": capture_ratio(trades),
            "win_rate": round(len(wins) / len(nets), 3) if nets else 0,
            "banked": sum(1 for t in trades if "banked" in t["exit_reason"]),
            "gross": round(sum(t["gross_profit"] for t in trades)),
            "charges": round(sum(t["charges"] for t in trades)),
            "net": round(sum(nets)),
            "avg": round(sum(nets) / len(nets)) if nets else 0,
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "best": round(max(nets)) if nets else 0,
            "worst": round(min(nets)) if nets else 0,
            "worst_run": round(worst_run)}


def slim_trades(trades: list[dict]) -> list[dict]:
    return [{"e": pd.Timestamp(t["entry_ts"]).strftime("%Y-%m-%d"),
             "x": pd.Timestamp(t["exit_ts"]).strftime("%Y-%m-%d"),
             "ep": round(t["entry_price"], 2), "xp": round(t["exit_price"], 2),
             "st": round(t["stop"], 2), "sh": round(t["shares"], 4),
             "net": round(t["net_profit"]), "why": t["exit_reason"]}
            for t in sorted(trades, key=lambda t: t["entry_ts"], reverse=True)]


def close_series(daily: pd.DataFrame) -> dict:
    d, c = [], []
    for i, rec in enumerate(daily.itertuples(index=False)):
        if i % STEP and i != len(daily) - 1:
            continue
        d.append(rec.ts.strftime("%Y-%m-%d"))
        c.append(round(float(rec.close), 2))
    return {"d": d, "c": c}


SCALE_VARIANTS = [("Keep full position (baseline)", None),
                  ("Sell half at +1R", "half"),
                  ("Sell half at +1R, stop to breakeven", "half_be")]

# Where to bank the half, as a multiple of R (the risk taken: entry minus stop).
# 1.0 is the rule as taught and reuses the original cache names.
SCALE_MULTIPLES = [0.5, 1.0, 1.5, 2.0, 3.0]
SCALE_RULES = [("half", "Sell half"), ("half_be", "Sell half, stop to breakeven")]


# --------------------------------------------------------------- main ----

def main() -> None:
    cfg = config.load()

    print("  signal lists (cached where possible):", flush=True)
    base = {}
    for band in BANDS:
        # band 2% keeps the old cache names. Named cache_tag, not tag: tag() is the
        # module-level key formatter and a local of that name shadows it.
        cache_tag = "" if band == 0.02 else f"_b{band*100:g}"
        base[("ema", band)] = cached_signals(
            f"EMA{cache_tag}_199", lambda s, b=band: backtest.simulate(s, band=b))
        base[("qmw", band)] = cached_signals(
            f"QMW{cache_tag}_199", variant_builder("QMW", band))
        base[("wdh", band)] = cached_signals(
            f"WDH{cache_tag}_199", variant_builder("WDH", band))
        print(f"    band {band:.0%} ready", flush=True)
    base[("brk", 0.02)] = cached_signals(
        "Breakout_199", lambda s: strategies.ath_breakout_trades(s, trailing_stops=True))
    for (entry_len, exit_len), window_tag in zip(DARVAS_WINDOWS, DARVAS_TAGS):
        base[("dv", window_tag)] = cached_signals(
            f"Darvas_{entry_len}_{exit_len}_199",
            lambda s, a=entry_len, b=exit_len: darvas.simulate(s, a, b))
    print("    darvas windows ready", flush=True)
    scale_lists = {
        ("ema", "half"): cached_signals("EMA_half_199",
                                        lambda s: backtest.simulate(s, scale_out="half")),
        ("ema", "half_be"): cached_signals("EMA_halfbe_199",
                                           lambda s: backtest.simulate(s, scale_out="half_be")),
        ("brk", "half"): cached_signals("Breakout_half_199",
                                        lambda s: strategies.ath_breakout_trades(
                                            s, trailing_stops=True, scale_out="half")),
        ("brk", "half_be"): cached_signals("Breakout_halfbe_199",
                                           lambda s: strategies.ath_breakout_trades(
                                               s, trailing_stops=True, scale_out="half_be")),
    }

    # Nobody at class level follows 199 stocks; ~10 is realistic. Draw 75 random
    # baskets (fixed seed) and promote the MEDIAN performer -- at a fixed
    # reference setting -- to a universe of its own, so every chart can be read
    # through it. Median, not best: picking the winner would be cherry-picking.
    import random
    rng = random.Random(20260823)
    symbols_all = sorted(cfg.all_symbols)
    baskets = [rng.sample(symbols_all, 10) for _ in range(75)]
    scored = []
    for basket in baskets:
        members = set(basket)
        subset = [t for t in base[("ema", 0.02)] if t["symbol"] in members]
        scored.append((portfolio.run(subset, 100_000, 0.01)["cagr_pct"], basket))
    scored.sort(key=lambda x: x[0])
    median_basket = scored[len(scored) // 2][1]
    print(f"  10-stock universe (median of 75 draws, {scored[len(scored)//2][0]:.1f}% CAGR "
          f"at the reference setting): {', '.join(sorted(median_basket))}", flush=True)

    universes = {"all": ("All 199 stocks", None),
                 "b10": ("10 random stocks", set(median_basket)),
                 "in": ("49 in-sample", set(cfg.in_sample)),
                 "out": ("150 holdout", set(cfg.out_of_sample))}

    # The spread is charged onto the cached trades rather than re-simulated:
    # nothing in the simulation depends on the fill price, so this is exact and
    # it keeps the whole toggle affordable (see slippage.apply_spread).
    slippage.ENABLED = True
    slippage.reset()
    slipped = {key: [slippage.apply_spread(t) for t in trades]
               for key, trades in base.items()}
    slippage.ENABLED = False
    print("  spread applied to the cached signal lists", flush=True)
    sets = {"0": base, "1": slipped}

    def realistic(on: bool):
        """Impact and the size limit live in portfolio.run, so they are globals."""
        slippage.ENABLED = on
        slippage.MAX_PARTICIPATION = REALISTIC_PARTICIPATION if on else None
        slippage.reset()

    grid = {}
    for fkey, _flabel in FILL_MODES:
        realistic(fkey == "1")
        for (skey, band), signals in sets[fkey].items():
            for ukey, (ulabel, members) in universes.items():
                subset = (signals if members is None
                          else [t for t in signals if t["symbol"] in members])
                for risk in RISKS:
                    for capital in CAPITALS:
                        r = portfolio.run(subset, capital, risk / 100)
                        grid[f"{skey}|{tag(band)}|{ukey}|{risk:g}|{capital}|{fkey}"] = \
                            run_payload(r)
            print(f"  grid[{fkey}]: {skey} {tag(band)} done", flush=True)
    realistic(False)

    tradestats = {}
    for fkey, _flabel in FILL_MODES:
        for (skey, band), signals in sets[fkey].items():
            for ukey, (_, members) in universes.items():
                subset = (signals if members is None
                          else [t for t in signals if t["symbol"] in members])
                tradestats[f"{skey}|{tag(band)}|{ukey}|{fkey}"] = trade_stats(subset)
    print("  universe trade metrics done", flush=True)

    scaleout = {}
    for skey in ("ema", "brk"):
        lists = {None: base[(skey, 0.02)], "half": scale_lists[(skey, "half")],
                 "half_be": scale_lists[(skey, "half_be")]}
        scaleout[skey] = {}
        for ukey, (_, members) in universes.items():
            rows = []
            for label, variant in SCALE_VARIANTS:
                trades = lists[variant]
                subset = (trades if members is None
                          else [t for t in trades if t["symbol"] in members])
                rows.append({"variant": label, **trade_stats(subset)})
            scaleout[skey][ukey] = rows
        print(f"  scale-out: {skey} done", flush=True)

    # ---- the scale-out page: every rule at every multiple of R ----------
    # Trade level only, and it cannot be otherwise: portfolio.run prices a trade as
    # shares x ONE exit price, but a scale-out has two, and the banked leg never
    # reaches the trade record. Verified 2026-08-31 -- 1,522 of 1,523 banked trades
    # disagree with the account engine's formula. So no CAGR appears on that page.
    print("  scale-out R sweep:", flush=True)
    r_lists = {}
    for skey, builder in (("ema", lambda s, v, r: backtest.simulate(s, scale_out=v,
                                                                    scale_r=r)),
                          ("brk", lambda s, v, r: strategies.ath_breakout_trades(
                              s, trailing_stops=True, scale_out=v, scale_r=r))):
        for variant, _vlabel in SCALE_RULES:
            for multiple in SCALE_MULTIPLES:
                if multiple == 1.0:
                    r_lists[(skey, variant, multiple)] = scale_lists[(skey, variant)]
                    continue          # the 1R runs are already cached under old names
                r_tag = f"{multiple:g}".replace(".", "p")
                name = ("EMA" if skey == "ema" else "Breakout")
                name += ("_half" if variant == "half" else "_halfbe") + f"_r{r_tag}_199"
                r_lists[(skey, variant, multiple)] = cached_signals(
                    name, lambda s, v=variant, r=multiple: builder(s, v, r))

    scaleout_r = {}
    for skey in ("ema", "brk"):
        for ukey, (_, members) in universes.items():
            def cut(trades):
                return (trades if members is None
                        else [t for t in trades if t["symbol"] in members])
            rows = [{"rule": "Keep the whole position", "variant": None,
                     "multiple": None, **trade_stats(cut(base[(skey, 0.02)]))}]
            keep = rows[0]["net"] or 1
            for variant, vlabel in SCALE_RULES:
                for multiple in SCALE_MULTIPLES:
                    stats = trade_stats(cut(r_lists[(skey, variant, multiple)]))
                    stats.update(rule=vlabel, variant=variant, multiple=multiple,
                                 vs_keep=round(100 * (stats["net"] - keep) / abs(keep), 1))
                    rows.append(stats)
            scaleout_r[f"{skey}|{ukey}"] = rows
        print(f"    {skey} done", flush=True)

    print("  per-stock detail:", flush=True)
    by_symbol = {k: {} for k in STRATEGY_LABELS}
    for (skey, band), signals in base.items():
        if band != PRIMARY[skey]:
            continue
        for t in signals:
            by_symbol[skey].setdefault(t["symbol"], []).append(t)
    stocks = {}
    for index, symbol in enumerate(sorted(cfg.all_symbols), 1):
        try:
            daily = frames.daily(symbol)
        except SystemExit:
            continue
        entry = {"closes": close_series(daily), "assigned": symbol in set(ASSIGNED)}
        for skey in STRATEGY_LABELS:
            tr = by_symbol[skey].get(symbol, [])
            entry[skey] = {"stats": trade_stats(tr), "trades": slim_trades(tr)}
        stocks[symbol] = entry
        if index % 50 == 0:
            print(f"    stocks: {index}/{len(cfg.all_symbols)}", flush=True)

    print("  assets detail:", flush=True)
    assets = {}
    for symbol, brk_tf, fee in ASSETS:
        backtest.FLAT_FEE_RATE = fee
        sizing.FRACTIONAL = True
        try:
            daily = frames.daily(symbol)
            bh = bh_stats(daily)
            entry = {"closes": close_series(daily),
                     "bh": {"cagr": round(bh["cagr"], 1), "maxdd": round(bh["maxdd"], 1),
                            "uw": round(bh["longest_uw"] / 365.25, 1),
                            "years": round(bh["years"], 1)}}
            builders = {"ema": lambda so=None: backtest.simulate(symbol, scale_out=so),
                        "brk": lambda so=None: strategies.ath_breakout_trades(
                            symbol, True, timeframe=brk_tf, scale_out=so),
                        "qmw": lambda so=None: variant_builder("QMW")(symbol),
                        "wdh": lambda so=None: variant_builder("WDH")(symbol),
                        # Darvas needs only daily bars, so it runs on every
                        # instrument here, at the windows the class specified.
                        "dv": lambda so=None: darvas.simulate(symbol, 20, 10)}
            for skey, build in builders.items():
                try:
                    trades = build()
                except (SystemExit, FileNotFoundError):
                    entry[skey] = None
                    continue
                r = portfolio.run(trades, 100_000, 0.01) if trades else None
                entry[skey] = {"stats": trade_stats(trades), "trades": slim_trades(trades),
                               "account": run_payload(r) if r else None}
            # scale-out variants for the two scale-out-capable strategies
            entry["scaleout"] = {}
            for skey in ("ema", "brk"):
                rows = []
                for label, variant in SCALE_VARIANTS:
                    try:
                        trades = builders[skey](variant)
                    except (SystemExit, FileNotFoundError):
                        trades = []
                    rows.append({"variant": label, **trade_stats(trades)})
                entry["scaleout"][skey] = rows
            assets[symbol] = entry
        finally:
            backtest.FLAT_FEE_RATE = None
            sizing.FRACTIONAL = False
        print(f"    {symbol} done", flush=True)

    tf = {}
    for key, label, desc in TF_VARIANTS:
        rows, allt = [], []
        for symbol in ASSIGNED:
            w = window_start(symbol)
            kept = [t for t in simulate_variant(symbol, key)
                    if pd.Timestamp(t["entry_ts"]).normalize() >= w]
            allt += kept
            s = tf_summarise(kept)
            rows.append({"sym": symbol, **{k: round(v, 2) if isinstance(v, float) else v
                        for k, v in s.items() if k != "charges"}})
        s = tf_summarise(allt)
        rows.append({"sym": "ALL 5", **{k: round(v, 2) if isinstance(v, float) else v
                     for k, v in s.items() if k != "charges"}})
        tf[key] = {"label": label, "desc": desc, "rows": rows}
        print(f"  timeframes: {label} done", flush=True)

    # ---- the 10-stock reality check: Monte Carlo over random baskets -------
    # Nobody at class level tracks 199 stocks; ~10 is realistic. Which 10 you
    # pick dominates the outcome, so we run the SAME account simulation on
    # many random baskets and report the distribution. Fixed Rs1,00,000
    # capital (class level); risk follows the slider; baskets identical
    # across strategies/risks so comparisons are apples-to-apples.
    basket10 = {}
    for fkey, _flabel in FILL_MODES:
        realistic(fkey == "1")
        for (skey, band), signals in sets[fkey].items():
            by_sym = {}
            for t in signals:
                by_sym.setdefault(t["symbol"], []).append(t)
            for risk in RISKS:
                cagrs, dds = [], []
                for basket in baskets:
                    subset = [t for s in basket for t in by_sym.get(s, [])]
                    if not subset:
                        continue
                    r = portfolio.run(subset, 100_000, risk / 100)
                    cagrs.append(round(r["cagr_pct"], 1))
                    dds.append(round(r["max_drawdown_pct"], 1))
                cagrs_sorted = sorted(cagrs)
                n = len(cagrs_sorted)
                basket10[f"{skey}|{tag(band)}|{risk:g}|{fkey}"] = {
                    "cagrs": cagrs,
                    "median": cagrs_sorted[n // 2],
                    "mean": round(sum(cagrs) / n, 1),
                    "p10": cagrs_sorted[n // 10],
                    "p90": cagrs_sorted[9 * n // 10],
                    "best": cagrs_sorted[-1], "worst": cagrs_sorted[0],
                    "beat_fd": round(100 * sum(1 for c in cagrs if c >= 7) / n),
                    "negative": round(100 * sum(1 for c in cagrs if c < 0) / n),
                    "median_dd": sorted(dds)[len(dds) // 2],
                }
            print(f"  baskets[{fkey}]: {skey} {tag(band)} done", flush=True)
    realistic(False)

    nifty = frames.daily("NIFTY 50")
    payload = {
        "built": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "strategies": STRATEGY_LABELS,
        "universes": {k: v[0] for k, v in universes.items()},
        "risks": RISKS, "capitals": CAPITALS, "bands": BANDS,
        "darvas_windows": DARVAS_TAGS, "darvas_default": DARVAS_DEFAULT,
        "fills": FILL_MODES, "participation": REALISTIC_PARTICIPATION,
        "assigned": list(ASSIGNED),
        "basket_members": sorted(median_basket),
        "grid": grid, "tradestats": tradestats, "scaleout": scaleout,
        "scaleout_r": scaleout_r, "scale_multiples": SCALE_MULTIPLES,
        "stocks": stocks, "assets": assets,
        "timeframes": tf, "basket10": basket10, "nifty": close_series(nifty),
    }
    OUT.write_text(json.dumps(payload))
    print(f"\n  written: {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()

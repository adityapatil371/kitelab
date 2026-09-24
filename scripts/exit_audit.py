"""How do the board's trades actually END?

Moved out of the scratchpad into scripts/ on 2026-09-23 because it
produced the evidence the whole rule repair rests on and the scratchpad
is wiped between sessions. Run: python3 -m scripts.exit_audit 150

Reads price frames through kitelab.frames (the read-only CLEAN parquet) and
writes one CSV to output/measurements/. For every entry family x stop width it
runs entries.simulate over a sample of symbols and tabulates the exit reason
and the holding period. Nothing is fitted; this only describes trades the
board already produces.
"""
import sys, time, collections
import numpy as np, pandas as pd
from kitelab import config, entries

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
MEAS = "/work/kitelab/output/measurements"
OUT = f"{MEAS}/exit_audit_2026-09-23.csv"

cfg = config.load()
syms = sorted(cfg.merged)[:N]
print(f"symbols {len(syms)}   families {len(entries.ENTRIES)}   stops {list(entries.STOPS)}")

rows, t0 = [], time.time()
with entries.panel_scope(syms):            # rank xrank/mktrel within the sample
    for fam in entries.ENTRIES:
        for stop_name, mult in entries.STOPS.items():
            why, held, errs = collections.Counter(), [], 0
            for s in syms:
                try:
                    tr = entries.simulate(s, fam, mult)
                except Exception:
                    errs += 1
                    continue
                for t in tr:
                    why[t["exit_reason"]] += 1
                    held.append(t["sessions_held"])
            n = sum(why.values())
            if not n:
                print(f"  {fam:7s} {stop_name:5s} NO TRADES (errors {errs})", flush=True)
                continue
            rec = {"family": fam, "stop": stop_name, "trades": n, "errors": errs,
                   "median_sessions_held": float(np.median(held)),
                   "pct_ended_by_stop": round(100.0 * sum(v for k, v in why.items()
                                                          if "stop" in k) / n, 1),
                   "pct_ended_by_cap": round(100.0 * sum(v for k, v in why.items()
                                                         if "limit" in k) / n, 1),
                   "pct_ended_day1": round(100.0 * sum(1 for h in held if h <= 1) / n, 1)}
            rows.append(rec)
            print(f"  {fam:7s} {stop_name:5s} n={n:6d}  med={rec['median_sessions_held']:5.0f}"
                  f"  stop={rec['pct_ended_by_stop']:5.1f}%  cap={rec['pct_ended_by_cap']:5.1f}%"
                  f"  day1={rec['pct_ended_day1']:5.1f}%", flush=True)

df = pd.DataFrame(rows)
print(f"\nelapsed {time.time()-t0:.1f}s")
print(f"rows {df.shape[0]} cols {df.shape[1]}")
print(df.head(3).to_string())
for col in ("family", "stop", "trades"):
    if col not in df.columns:
        raise SystemExit(f"required column {col} missing")
if df[["family", "stop", "trades"]].isna().any().any():
    raise SystemExit("unexpected nulls in required columns")
df.to_csv(OUT, index=False)
print(f"wrote {OUT}")

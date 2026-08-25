"""Test whether a level is respected at wider distances than the default tolerance.

    python -m scripts.level_audit
    python -m scripts.level_audit --only-dismissed

A level counts as "touched" when price comes within a tolerance band of it. 0.25% is a
tight band -- a line you drew by eye may well be respected at 1-2% without ever
registering. But proximity alone is weak evidence. What makes a line real is that price
turned there, so each approach is also scored for its REACTION: how far price travelled
away from the level in the direction the level implies, within the following N sessions.

Touches say price visited. Reactions say the level did something.
"""
from __future__ import annotations

import argparse

from kitelab import config, frames, levels

TOLERANCES = [0.0025, 0.005, 0.01, 0.02, 0.03]
REACTION_BARS = 10     # daily sessions to look for the move away
REACTION_PCT = 3.0     # % move that counts as respecting the level


def reaction_size(frame, position: int, kind: str, bars: int) -> float:
    """How far price moved in the level's implied direction after this approach."""
    window = frame.iloc[position: position + bars + 1]
    if window.empty:
        return 0.0
    if kind == "support":
        base = float(frame.iloc[position]["low"])
        return (float(window["high"].max()) - base) / base * 100
    base = float(frame.iloc[position]["high"])
    return (base - float(window["low"].min())) / base * 100


def audit_level(daily, price: float, kind: str) -> list[dict]:
    rows = []
    for tolerance in TOLERANCES:
        events = levels.touch_events(daily, price, tolerance=tolerance)
        reactions = [e for e in events
                     if reaction_size(daily, e, kind, REACTION_BARS) >= REACTION_PCT]
        established = daily.iloc[events[1]]["ts"].date() if len(events) >= 2 else None
        rows.append({
            "tolerance": tolerance, "touches": len(events),
            "reactions": len(reactions), "valid_from": established,
            "best": max((reaction_size(daily, e, kind, REACTION_BARS) for e in events),
                        default=0.0),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-dismissed", action="store_true",
                        help="show only levels with under 2 touches at the default tolerance")
    args = parser.parse_args()

    cfg = config.load()
    rescued = []
    for symbol in cfg.symbols:
        daily = frames.load(symbol, "1d")
        entries = levels.load_all().get(symbol, [])
        printed_header = False

        for raw in sorted(entries, key=lambda r: -r["price"]):
            rows = audit_level(daily, raw["price"], raw["kind"])
            dismissed = rows[0]["touches"] < 2
            if args.only_dismissed and not dismissed:
                continue
            if not printed_header:
                print(f"\n{'=' * 72}\n{symbol}\n{'=' * 72}")
                printed_header = True

            flag = "  <-- currently dismissed" if dismissed else ""
            print(f"\n  {raw['kind']:<11} {raw['price']:>10,.2f}{flag}")
            print(f"    {'tol':>6}  {'touches':>7}  {'reacted':>7}  {'best move':>9}  valid_from")
            for row in rows:
                marker = ""
                if dismissed and row["touches"] >= 2 and row["reactions"] >= 2:
                    marker = "  <-- would qualify"
                    rescued.append((symbol, raw["kind"], raw["price"], row["tolerance"],
                                    row["touches"], row["reactions"]))
                print(f"    {row['tolerance'] * 100:>5.2f}%  {row['touches']:>7}  "
                      f"{row['reactions']:>7}  {row['best']:>8.1f}%  "
                      f"{row['valid_from'] or '-'}{marker}")

    print(f"\n{'=' * 72}")
    if rescued:
        print(f"{len(rescued)} level/tolerance combinations would rescue a dismissed level:\n")
        seen = set()
        for symbol, kind, price, tolerance, touches, reactions in rescued:
            key = (symbol, price)
            if key in seen:
                continue
            seen.add(key)
            print(f"  {symbol:<10} {kind:<11} {price:>10,.2f}  qualifies from "
                  f"{tolerance * 100:.2f}% ({touches} touches, {reactions} reacted)")
    else:
        print("No dismissed level qualifies even at 3% -- they really are single visits.")
    print()


if __name__ == "__main__":
    main()

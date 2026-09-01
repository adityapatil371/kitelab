"""Is every signal cache still trustworthy?

    python -m scripts.cache_status

A cache is only as good as the universe, the price files and the strategy code it was
built from. This says which of the caches on disk still match all three, so you never
have to guess whether a report is reading current trades.
"""
from kitelab import config, signals


def main() -> None:
    cfg = config.load()
    rows = signals.status(cfg.all_symbols)
    if not rows:
        print("\n  no signal caches on disk -- the next dashboard_data run builds them\n")
        return
    want = signals.stamp(cfg.all_symbols)
    print(f"\n  universe: {want['n_symbols']} symbols, {want['n_files']} price files\n")
    width = max(len(n) for n, _ in rows)
    ok = sum(1 for _, v in rows if v.startswith("ok"))
    for name, verdict in rows:
        mark = "  " if verdict.startswith("ok") else "!!"
        print(f"  {mark} {name:<{width}}  {verdict}")
    print(f"\n  {ok} of {len(rows)} caches match the current universe, data and code.")
    if ok != len(rows):
        print("  Rebuild with:  python -m scripts.dashboard_data\n")
    else:
        print()


if __name__ == "__main__":
    main()

"""Serve the local chart so you can draw support/resistance levels.

    python -m scripts.chart
    python -m scripts.chart --port 9000

Levels are saved to data/levels.json as you place them. Ctrl-C to stop.
"""
from __future__ import annotations

import argparse

from kitelab import chart_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    chart_server.serve(parser.parse_args().port)


if __name__ == "__main__":
    main()

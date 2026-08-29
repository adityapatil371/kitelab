"""Serve the kitelab dashboard.

    python -m scripts.dashboard
    python -m scripts.dashboard --port 9000

Then open http://localhost:8765. Ctrl-C to stop. Rebuild the numbers with
`python -m scripts.dashboard_data`.
"""
from __future__ import annotations

import argparse

from kitelab import dashboard_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    dashboard_server.serve(parser.parse_args().port)


if __name__ == "__main__":
    main()

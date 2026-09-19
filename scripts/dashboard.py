"""Serve the kitelab dashboard.

    python -m scripts.dashboard
    python -m scripts.dashboard --port 9000

Then open http://localhost:8765. Ctrl-C to stop. Rebuild the numbers with
`python -m scripts.dashboard_data`.

To show it to other people, see SHARING.md. In short: set KITELAB_PASSPHRASE,
run it as usual, and point `cloudflared tunnel --url http://127.0.0.1:8765`
at it. `--host 0.0.0.0` is for the other case -- running it inside a container
and opening the page on the host -- and requires a passphrase just the same.
"""
from __future__ import annotations

import argparse
import os

from kitelab import dashboard_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 to accept connections from other machines. "
                             "Requires --passphrase.")
    parser.add_argument("--passphrase", default=None,
                        help="ask for this before showing anything. Defaults to "
                             "$KITELAB_PASSPHRASE; leave both unset for a "
                             "private dashboard on this machine only.")
    args = parser.parse_args()
    # The environment rather than the command line by default: a passphrase in
    # argv lands in the shell history and in `ps` for every user on the box.
    dashboard_server.serve(args.port, args.host,
                           args.passphrase or os.environ.get("KITELAB_PASSPHRASE"))


if __name__ == "__main__":
    main()

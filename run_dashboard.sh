#!/usr/bin/env bash
#
# Start the kitelab dashboard and open it in your browser.
#
#     ./run_dashboard.sh              # port 8765
#     ./run_dashboard.sh 9000         # some other port
#
# Press Ctrl-C to stop it.
#
# This does NOT need your Kite API keys. Viewing saved results is not trading,
# so the server reads the configured stock list straight from
# config.local.toml. Keep your secrets out of this.
#
# It also does not compute anything. It serves the numbers already on disk. To
# rebuild those (the long job), run:  ./.venv/bin/python -m scripts.dashboard_data

set -euo pipefail

PORT="${1:-8765}"

# The repo is wherever this script lives, so it works from any directory.
cd "$(dirname "${BASH_SOURCE[0]}")"

# ---------------------------------------------------------------- python ----
# Prefer the project's own virtual environment; fall back to system python3.
if [ -x ".venv/bin/python" ]; then
    PY="./.venv/bin/python"
else
    PY="$(command -v python3 || true)"
    if [ -z "$PY" ]; then
        echo "No python3 found. Install it, or rebuild the venv:" >&2
        echo "    python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
        exit 1
    fi
    echo "note: .venv not found, using $PY"
fi

# ------------------------------------------------------------------ data ----
# Two directories: RAW is the read-only download store, CLEAN is everything the
# dashboard actually reads. If you already export KITELAB_DATA_DIR /
# KITELAB_CLEAN_DIR, those win and nothing here overrides them.
#
# Otherwise try the usual spots and pick the first that holds real data. Edit
# the lists if yours lives somewhere else.
find_dir() {           # find_dir <marker-glob> <candidate>...
    local marker="$1"; shift
    for d in "$@"; do
        # shellcheck disable=SC2086
        if [ -d "$d" ] && compgen -G "$d/$marker" > /dev/null; then
            echo "$d"; return 0
        fi
    done
    return 1
}

if [ -z "${KITELAB_CLEAN_DIR:-}" ]; then
    KITELAB_CLEAN_DIR="$(find_dir '*_day.parquet' \
        "$HOME/data/kitelab/clean" \
        "$HOME/data/kitelab" \
        "/data/clean/kitelab" \
        "./data" || true)"
fi

if [ -z "${KITELAB_DATA_DIR:-}" ]; then
    KITELAB_DATA_DIR="$(find_dir '*_day.parquet' \
        "$HOME/data/kitelab/raw" \
        "$HOME/data/kitelab" \
        "/data/raw/kitelab" \
        "./data" || true)"
fi

if [ -z "$KITELAB_CLEAN_DIR" ]; then
    cat >&2 <<'EOF'

  Could not find your cleaned data.

  The dashboard reads price files and dashboard.json from the CLEAN directory.
  Tell it where that is, then run this again:

      export KITELAB_CLEAN_DIR="$HOME/data/kitelab/clean"
      export KITELAB_DATA_DIR="$HOME/data/kitelab/raw"

  If you have raw downloads but have never cleaned them:

      ./.venv/bin/python -m scripts.clean_data

EOF
    exit 1
fi

export KITELAB_CLEAN_DIR
[ -n "$KITELAB_DATA_DIR" ] && export KITELAB_DATA_DIR

echo "  repo   $(pwd)"
echo "  clean  $KITELAB_CLEAN_DIR"
echo "  raw    ${KITELAB_DATA_DIR:-(not found - only needed for fetching/cleaning)}"

# Warn early rather than serving an empty page.
if [ ! -f "$KITELAB_CLEAN_DIR/dashboard.json" ]; then
    echo
    echo "  WARNING: no dashboard.json in that directory."
    echo "  The page will load but show 'No data yet'. Build it with:"
    echo "      $PY -m scripts.dashboard_data"
fi

# ------------------------------------------------------------------ port ----
if command -v lsof > /dev/null && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then
    echo >&2
    echo "  Port $PORT is already in use - the dashboard may already be running." >&2
    echo "  Open http://localhost:$PORT, or pick another port: $0 9000" >&2
    exit 1
fi

# ----------------------------------------------------------------- serve ----
# Open the browser once the server is actually accepting connections, so you
# never land on a "cannot connect" page. Backgrounded; the server stays in front
# so Ctrl-C stops everything.
(
    for _ in $(seq 1 40); do
        if curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/" 2>/dev/null; then
            if command -v open > /dev/null; then open "http://localhost:$PORT"
            elif command -v xdg-open > /dev/null; then xdg-open "http://localhost:$PORT"
            fi
            exit 0
        fi
        sleep 0.25
    done
) &

exec "$PY" -m scripts.dashboard --port "$PORT"

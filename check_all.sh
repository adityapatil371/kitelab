#!/usr/bin/env bash
#
# Audit the dashboard yourself, without trusting anything said in a chat window.
#
#     ./check_all.sh
#
# Eight sections. Every one of them READS; not one writes, so this is safe to
# run at any time, including mid-rebuild (the numbers will just be the last
# finished build's). No Kite keys needed.
#
# What each section is for:
#   1  when the payload was built, and how big the board is
#   2  the multiple-testing numbers -- how many cells were tested, how many
#      cleared, and how many would clear by luck alone
#   3  how many INDEPENDENT ideas the board holds (n_eff), not how many labels
#   4  the question that matters: does each row beat buy-and-hold?
#   5  does the page still render this exact file (scripts/check_dashboard.js)
#   6  the test suite and the linter
#   7  git state -- is the tree clean
#   8  how to open the page
#
# Section 4 recomputes from the raw grid rather than reading a summary, so it
# cannot inherit a mistake from whatever wrote the summary.
#
# THIS FILE HARDCODES NO PATHS. The first version did, and it printed four
# FileNotFoundErrors on the Mac (where CLEAN is under $HOME, not /data) and ran
# the tests under a python with no pandas, reporting 40 errors that were really
# one missing import. Both are resolved below the same way the rest of the repo
# resolves them -- run_dashboard.sh, check_dashboard.js and config._resolve()
# all search the same candidates in the same order.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# ------------------------------------------------------------- python ----
# On the host that is the venv (3.14, pandas). In the dev container that venv
# is a DEAD SYMLINK, and `test -x` follows symlinks, so it correctly falls
# through to plain python3 (3.12, pandas). Same rule as kitelab/CLAUDE.md.
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python3

# --------------------------------------------------------------- data ----
# An environment variable always wins, then $HOME, then the container mount,
# then the in-repo fallback -- config._resolve()'s order exactly.
D=""
for c in "${KITELAB_CLEAN_DIR:-}" "$HOME/data/clean/kitelab" "$HOME/data/kitelab/clean" \
         /data/clean/kitelab ./data-clean ./data; do
    if [ -n "$c" ] && [ -f "$c/dashboard.json" ]; then D="$c/dashboard.json"; break; fi
done
if [ -z "$D" ]; then
    echo "No dashboard.json anywhere. Looked under \$KITELAB_CLEAN_DIR, \$HOME/data,"
    echo "/data/clean/kitelab and ./data-clean. Build one:  $PY -m scripts.refresh"
    exit 1
fi

echo "reading  $D"
echo "python   $($PY -V 2>&1)  [$PY]"

echo; echo "=== 1. build stamp, grid size, family count ==="
$PY -c "import json,sys;d=json.load(open(sys.argv[1]));print(' ',d['built'],'|',len(d['grid']),'cells |',len(d['strategies']),'families x 2 stops')" "$D"

echo; echo "=== 2. gate numbers ==="
$PY -c "import json,sys;g=json.load(open(sys.argv[1]))['diagnostics'];[print(f'  {k:<34}{g[k]}') for k in ['n_tested','n_uncorrected','expected_by_chance','n_bh','n_bonferroni','bonferroni_threshold','median_mde_80','walk_forward_false_positive_rate','walk_forward_mde_80']]" "$D"

echo; echo "=== 3. independent ideas + trade-level hurdle ==="
$PY -c "import json,sys;v=json.load(open(sys.argv[1]))['validation_summary'];print('  n_eff',v['n_eff'],'| avg_corr',v['avg_correlation'],'| cleared',v['cleared'],v['cleared_keys'])" "$D"

echo; echo "=== 4. every row's median CAGR minus buy-and-hold ==="
$PY - "$D" <<'PY'
import json, sys, statistics as st
d = json.load(open(sys.argv[1]))
hold = d["validation_summary"]["hold_cagr_by_scenario"]
rows = {}
for key, c in d["grid"].items():
    if not c or c.get("cagr") is None: continue
    s, var, uni, _r, _c, _f, yr, _p = key.split("|")
    h = hold.get(f"{uni}|{yr}")
    if h is not None: rows.setdefault(f"{s}|{var}", []).append(c["cagr"] - h)
med = sorted(((st.median(v), k, len(v)) for k, v in rows.items()))
for m, k, n in med: print(f"  {k:<15}{n:>5} cells{m:>9.2f}")
print(f"\n  rows BEATING hold: {sum(1 for m,_,_ in med if m>0)} of {len(med)}")
PY

echo; echo "=== 5. the page's own self-checks ==="
# the verdict is "all checks passed" or "N FAILURE(S)"; a blank line follows it,
# so grep for the line rather than taking the last one.
if command -v node >/dev/null; then
    node scripts/check_dashboard.js | grep -E "all checks passed|FAILURE" | sed "s/^/  /"
else
    echo "  (node not installed -- skipped)"
fi

echo; echo "=== 6. test suite + lint ==="
# the suite needs pandas. Without it every strategy import fails and you get
# dozens of errors that are really one missing package -- say so instead.
if $PY -c "import pandas" 2>/dev/null; then
    PYTHONPATH="$PWD" $PY -m unittest discover -s tests -t . 2>&1 \
        | grep -E "^(Ran [0-9]+ test|OK$|FAILED)" | sed "s/^/  /"
else
    echo "  (no pandas in $PY -- suite skipped, it would report import errors only)"
fi
if $PY -c "import pyflakes" 2>/dev/null; then
    $PY -m pyflakes kitelab scripts tests | sed "s/^/  /"
    echo "  (only scripts/refresh.py:69 is expected; anything else is new)"
else
    echo "  (no pyflakes in $PY -- skipped;  $PY -m pip install pyflakes)"
fi

echo; echo "=== 7. git state ==="
git log --oneline -1 | sed "s/^/  /"; git status --short | sed "s/^/  /"
echo "  (no ?? or M lines above = clean tree)"

echo; echo "=== 8. open the page ==="
if [ "$(uname)" = "Darwin" ]; then
    # Safari enforces HTTPS-Only on this Mac and refuses plain http outright.
    echo "  python3 ~/.kitelab-dev-tls/serve_https.py   # then https://localhost:8765"
    echo "  (./run_dashboard.sh serves plain http -- Safari will refuse it)"
else
    echo "  ./run_dashboard.sh                          # then http://localhost:8765"
fi

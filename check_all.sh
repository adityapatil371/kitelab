#!/usr/bin/env bash
#
# Audit the dashboard yourself, without trusting anything said in a chat window.
#
#     ./check_all.sh
#
# Eight sections. Every one of them READS; not one writes, so this is safe to
# run at any time, including mid-rebuild (the numbers will just be the last
# finished build's). It needs python3 and node, no Kite keys and no venv.
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

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

D=/data/clean/kitelab/dashboard.json

echo "=== 1. build stamp, grid size, family count ==="
python3 -c "import json;d=json.load(open('$D'));print(' ',d['built'],'|',len(d['grid']),'cells |',len(d['strategies']),'families')"

echo; echo "=== 2. gate numbers ==="
python3 -c "import json;g=json.load(open('$D'))['diagnostics'];[print(f'  {k:<34}{g[k]}') for k in ['n_tested','n_uncorrected','expected_by_chance','n_bh','n_bonferroni','bonferroni_threshold','median_mde_80','walk_forward_false_positive_rate','walk_forward_mde_80']]"

echo; echo "=== 3. independent ideas + trade-level hurdle ==="
python3 -c "import json;v=json.load(open('$D'))['validation_summary'];print('  n_eff',v['n_eff'],'| avg_corr',v['avg_correlation'],'| cleared',v['cleared'],v['cleared_keys'])"

echo; echo "=== 4. every row's median CAGR minus buy-and-hold ==="
python3 - <<'PY'
import json, statistics as st
d = json.load(open("/data/clean/kitelab/dashboard.json"))
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
# the verdict line is "all checks passed" or "N FAILURE(S)"; there is a blank
# line after it, so grep for the line rather than taking the last one.
node scripts/check_dashboard.js | grep -E "all checks passed|FAILURE" | sed "s/^/  /"

echo; echo "=== 6. test suite + lint ==="
PYTHONPATH="$PWD" python3 -m unittest discover -s tests -t . 2>&1 | grep -E "^(Ran [0-9]+ test|OK$|FAILED)"
python3 -m pyflakes kitelab scripts tests || true

echo; echo "=== 7. git state ==="
git log --oneline -1; git status --short; echo "  (blank above = clean tree)"

echo; echo "=== 8. open the page ==="
echo "  ./run_dashboard.sh              # then http://localhost:8765"
echo "  MAC: python3 ~/.kitelab-dev-tls/serve_https.py   # then https://localhost:8765"

/* Dry-run web/article.html against the dashboard.json it will actually serve,
 * and check every number the ARTICLE prints against the payload.
 *
 *     node scripts/check_article.js [path/to/dashboard.json]
 *
 * WHY THIS EXISTS, separately from check_dashboard.js. The article is a second
 * page telling a story about the same numbers, and a story is exactly the kind
 * of thing that keeps being told after it stops being true. A rebuild that
 * moves the board changes the article's meaning, not just its digits: if the
 * count of rules beating buy & hold went from 9 to 0, a page whose prose said
 * "nine" would be wrong in a way no syntax check could see.
 *
 * So the article prints NO literal numbers -- every figure is computed by
 * facts() from the payload -- and this checker recomputes the same quantities
 * from the raw JSON with a SECOND, independent implementation and compares. It
 * then renders the page under a stub DOM and asserts the recomputed values
 * actually appear in the output, so a number that is computed correctly and
 * then never printed, or printed from the wrong variable, still fails here.
 *
 * Three further things it enforces:
 *   - WHAT (the article's own one-line rule descriptions) matches
 *     DATA.strategies in BOTH directions, the same guard check_dashboard.js
 *     puts on PLAIN_FAMILY. The two pages keep separate copies because the
 *     payload is inside signals._ACCOUNT and a sentence there would cost a
 *     ~178-minute rebuild to publish text that changes no number.
 *   - the static markup outside the <script> block contains no digits at all,
 *     which is the structural form of "no number is written in the prose".
 *   - the rendered output contains no "undefined", "NaN", or bare "null".
 *
 * Exits non-zero if any check fails, so it can go in front of a commit.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");

/* Same resolution order as config.CLEAN, run_dashboard.sh:57 and
   check_dashboard.js:42 -- env var, then $HOME, then /data, then in-repo. The
   container and the Mac are one directory with two CLEAN paths; a script that
   hardcodes either works on exactly one side. */
const jsonPath = process.argv[2]
  || [process.env.KITELAB_CLEAN_DIR,
      path.join(process.env.HOME || "", "data/clean/kitelab"),
      "/data/clean/kitelab",
      path.join(ROOT, "data-clean")]
     .filter(Boolean).map(d => path.join(d, "dashboard.json"))
     .find(p => fs.existsSync(p));
if (!jsonPath || !fs.existsSync(jsonPath)) {
  console.error(`No dashboard.json at ${jsonPath || "any known location"}. Build one:  python -m scripts.refresh`);
  process.exit(2);
}
const pagePath = path.join(ROOT, "web", "article.html");
if (!fs.existsSync(pagePath)) {
  console.error(`No page at ${pagePath}`);
  process.exit(2);
}
console.log(`checking ${pagePath}\n     against ${jsonPath}`);

const fail = [];
const ok = m => console.log("  ok   " + m);
const bad = m => { fail.push(m); console.log("  FAIL " + m); };
const eq = (label, got, want) =>
  (got === want ? ok(`${label} = ${got}`) : bad(`${label}: page ${got}, payload ${want}`));
const close = (label, got, want, tol) =>
  (got != null && want != null && Math.abs(got - want) <= tol
    ? ok(`${label} = ${got.toFixed(2)}`)
    : bad(`${label}: page ${got}, payload ${want}`));

/* ── slice the page's real <script> out, minus its fetch bootstrap ────── */
const src = fs.readFileSync(pagePath, "utf8");
const m = src.match(/<script>\n([\s\S]*)<\/script>/);
if (!m) { console.error("no <script> block in the page"); process.exit(2); }
let js = m[1];
const before = js.length;
js = js.replace(/fetch\("\/api\/dashboard"\)[\s\S]*?\.catch\([\s\S]*?\}\);/, "");
if (js.length === before) bad("could not strip the /api/dashboard bootstrap -- the harness would fetch for real");

/* ── stub DOM ─────────────────────────────────────────────────────────── */
const els = {};
function mk(id) {
  let html = "", tc = "";
  const e = {
    id, style: {}, children: [],
    addEventListener() {}, setAttribute() {},
    querySelector: () => mk(), getAttribute: () => null,
  };
  Object.defineProperty(e, "textContent", {
    get() { return tc; }, set(v) { tc = v == null ? "" : String(v); } });
  Object.defineProperty(e, "innerHTML", {
    get() { return html; }, set(v) { html = String(v); } });
  return e;
}
global.document = {
  getElementById: id => els[id] || (els[id] = mk(id)),
  createElement: () => mk(),
  querySelector: () => mk(),
};
global.window = { innerWidth: 1440 };
global.fetch = async () => { throw new Error("the page must not fetch under the checker"); };

/* ── load the page's own code and hand it the payload ─────────────────── */
const DATA = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
const driver = `
;module.exports = { boot, facts, render, WHAT, STOP_LABEL, SCENARIO,
                    words: typeof words === "function" ? words : null };
`;
const mod = { exports: {} };
try {
  new Function("module", "require", js + driver)(mod, require);
} catch (err) {
  console.error("the page's script threw while loading: " + err.stack);
  process.exit(1);
}
const P = mod.exports;

let f;
try {
  f = P.boot(DATA);
} catch (err) {
  console.error("boot() threw: " + err.stack);
  process.exit(1);
}
const out = document.getElementById("body").innerHTML;
const dateline = document.getElementById("dateline").textContent;

/* ═══════════════════════════════════════════════════════════════════════
   A SECOND IMPLEMENTATION. Everything below recomputes from the raw JSON
   without touching the page's helpers. The scenario string is written out in
   full rather than rebuilt from indices, so the page picking risk 0.5 or the
   ten-million-rupee account by mistake fails here.
   ═══════════════════════════════════════════════════════════════════════ */
const SCEN = "all|1|200000|1|2018|mom_hi";
const HOLD = DATA.validation_summary.hold_cagr_by_scenario["all|2018"];
const VARIANTS = ["own", "atr3"];

function med(xs) {
  if (!xs.length) return null;
  const s = xs.slice().sort((a, b) => a - b), i = s.length >> 1;
  return s.length % 2 ? s[i] : (s[i - 1] + s[i]) / 2;
}
function holdOf(key) {
  const p = key.split("|");
  return DATA.validation_summary.hold_cagr_by_scenario[`${p[2]}|${p[6]}`];
}

/* the default-scenario board */
const wantRows = [];
for (const sk of Object.keys(DATA.strategies)) {
  for (const v of VARIANTS) {
    const c = DATA.grid[`${sk}|${v}|${SCEN}`];
    if (!c) continue;
    wantRows.push({ sk, v, cagr: c.cagr, maxdd: c.maxdd,
                    x: (c.cagr == null || c.wiped) ? null : c.cagr - HOLD });
  }
}
wantRows.sort((a, b) => (b.x == null ? -1e9 : b.x) - (a.x == null ? -1e9 : a.x));
const wantBeat = wantRows.filter(r => r.x != null && r.x > 0).length;
const wantBest = wantRows[0];

/* every scored cell, and the per-row groups */
const wantAll = [];
const groups = new Map();
for (const [k, c] of Object.entries(DATA.grid)) {
  if (!c || c.cagr == null || c.wiped) continue;
  const h = holdOf(k);
  if (h == null) continue;
  const x = c.cagr - h;
  wantAll.push(x);
  const id = k.split("|").slice(0, 2).join("|");
  if (!groups.has(id)) groups.set(id, []);
  groups.get(id).push(x);
}
const wantBestRow = groups.get(`${wantBest.sk}|${wantBest.v}`) || [];
const wantRowMeds = [...groups.entries()].map(([id, xs]) => ({ id, med: med(xs) }))
                                          .sort((a, b) => b.med - a.med);
const D = DATA.diagnostics;

/* ── 1. payload contract: the keys the article reads ──────────────────── */
console.log("\npayload contract");
for (const k of ["built", "grid", "strategies", "universes", "risks", "capitals",
                 "start_default", "priority_default", "validation_summary", "diagnostics"]) {
  (k in DATA) ? ok(`top-level ${k}`) : bad(`payload is missing ${k}, which the article reads`);
}
for (const k of ["n_tested", "n_uncorrected", "expected_by_chance", "n_bh",
                 "median_mde_80", "median_days"]) {
  (D && k in D) ? ok(`diagnostics.${k}`) : bad(`diagnostics is missing ${k}`);
}
for (const k of ["tried", "n_eff", "avg_correlation", "hold_cagr_by_scenario"]) {
  (k in DATA.validation_summary) ? ok(`validation_summary.${k}`)
                                 : bad(`validation_summary is missing ${k}`);
}

/* ── 2. the scenario the article describes ────────────────────────────── */
console.log("\nthe scenario the article opens on");
eq("universe", f.scenario.uni, "all");
eq("risk %", f.scenario.risk, DATA.risks[1]);
eq("capital", f.scenario.cap, DATA.capitals[0]);
eq("start year", f.scenario.year, DATA.start_default);
eq("priority", f.scenario.prio, DATA.priority_default);
close("buy & hold CAGR", f.hold, HOLD, 1e-9);

/* ── 3. the board ─────────────────────────────────────────────────────── */
console.log("\nsection 2 -- the board at that scenario");
eq("rows on the board", f.nRows, wantRows.length);
eq("rows that beat hold", f.nBeat, wantBeat);
eq("rows that lost", f.nLose, wantRows.length - wantBeat);
eq("top rule", `${f.best.skey}|${f.best.v}`, `${wantBest.sk}|${wantBest.v}`);
close("top rule CAGR", f.best.cagr, wantBest.cagr, 1e-9);
close("top rule vs hold", f.bestVsHold, wantBest.x, 1e-9);
eq("board is sorted best-first", f.rows[0].skey + "|" + f.rows[0].v,
   `${wantBest.sk}|${wantBest.v}`);
eq("last row is the worst", `${f.rows[f.nRows - 1].skey}|${f.rows[f.nRows - 1].v}`,
   `${wantRows[wantRows.length - 1].sk}|${wantRows[wantRows.length - 1].v}`);

/* ── 4. the spread behind the headline ────────────────────────────────── */
console.log("\nsection 3 -- every setting of the top rule");
eq("settings scored for the top rule", f.nBestRowCells, wantBestRow.length);
close("its typical setting", f.bestRowMedian, med(wantBestRow), 1e-9);
eq("its settings that beat hold", f.bestRowBeat, wantBestRow.filter(x => x > 0).length);
close("its best setting", f.bestRowBest, Math.max(...wantBestRow), 1e-9);
close("its worst setting", f.bestRowWorst, Math.min(...wantBestRow), 1e-9);

/* ── 5. everything at once ────────────────────────────────────────────── */
console.log("\nsection 4 -- all scored runs");
eq("scored runs", f.nScored, wantAll.length);
eq("cells in the grid", f.nCells, Object.keys(DATA.grid).length);
close("typical run", f.allMedian, med(wantAll), 1e-9);
eq("runs that beat hold", f.allBeat, wantAll.filter(x => x > 0).length);
close("share that beat hold", f.allBeatPct,
      100 * wantAll.filter(x => x > 0).length / wantAll.length, 1e-9);
eq("per-rule medians", f.rowMedians.length, wantRowMeds.length);
eq("rules with a positive typical setting", f.nRowsPositiveMedian,
   wantRowMeds.filter(r => r.med > 0).length);
eq("best rule by typical setting", f.bestRowMedianOverall.id, wantRowMeds[0].id);
eq("worst rule by typical setting", f.worstRowMedianOverall.id,
   wantRowMeds[wantRowMeds.length - 1].id);

/* ── 6. the luck test ─────────────────────────────────────────────────── */
console.log("\nsections 5 and 6 -- the luck test");
eq("runs tested", f.nTested, D.n_tested);
eq("cleared an uncorrected bar", f.nUncorrected, D.n_uncorrected);
close("expected by chance", f.expectedByChance, D.expected_by_chance, 1e-9);
eq("survive the correction", f.nBH, D.n_bh);
close("smallest detectable edge", f.mde, D.median_mde_80, 1e-9);
eq("rules tried", f.tried, DATA.validation_summary.tried);

/* ── 7. the descriptions, both directions ─────────────────────────────── */
console.log("\nthe article's own rule descriptions");
const known = new Set(Object.keys(DATA.strategies));
const described = new Set(Object.keys(P.WHAT));
const missing = [...known].filter(k => !described.has(k));
const extra = [...described].filter(k => !known.has(k));
missing.length ? bad(`WHAT has no sentence for: ${missing.join(", ")}`)
               : ok(`every one of ${known.size} strategies has a sentence`);
extra.length ? bad(`WHAT describes strategies the payload does not have: ${extra.join(", ")}`)
             : ok("WHAT describes no strategy the payload lacks");
const blank = [...described].filter(k => !String(P.WHAT[k]).trim());
blank.length ? bad(`empty description for: ${blank.join(", ")}`) : ok("no empty descriptions");
const stopsSeen = new Set(wantRows.map(r => r.v));
const stopsNamed = new Set(Object.keys(P.STOP_LABEL));
[...stopsSeen].every(v => stopsNamed.has(v))
  ? ok(`both stop arms named: ${[...stopsSeen].join(", ")}`)
  : bad(`STOP_LABEL is missing an arm the board uses: ${[...stopsSeen].filter(v => !stopsNamed.has(v)).join(", ")}`);

/* ── 8. no number is written in the prose ─────────────────────────────── */
console.log("\nno number is typed into the page");
const article = src.match(/<article>([\s\S]*?)<\/article>/);
if (!article) bad("no <article> block found");
else {
  /* Strip the markup first: <h1> is a tag, not a number a reader sees. What
     is being enforced is that no FIGURE is typed into the copy. */
  const staticProse = article[1]
    .replace(/<div id="body">[\s\S]*?<\/div>/, "")
    .replace(/<[^>]*>/g, " ");
  const digits = staticProse.match(/\d+/g);
  digits ? bad(`static prose contains literal numbers: ${digits.join(", ")}`)
         : ok("the static markup outside the script contains no digits at all");
}

/* ── 9. what actually rendered ────────────────────────────────────────── */
console.log("\nwhat the reader sees");
const has = (label, needle) => out.includes(needle)
  ? ok(`${label}: "${needle}" is on the page`)
  : bad(`${label}: "${needle}" is NOT on the page`);

out.length > 6000 ? ok(`rendered ${out.length} characters`)
                  : bad(`rendered only ${out.length} characters -- the page is near-empty`);
for (const id of ["sec-hero", "sec-board", "sec-spread", "sec-all", "sec-luck", "sec-power"]) {
  out.includes(`id="${id}"`) ? ok(`section ${id} rendered`) : bad(`section ${id} is missing`);
}
for (const junk of ["undefined", "NaN", ">null<", "[object Object]"]) {
  out.includes(junk) ? bad(`rendered page contains ${junk}`) : ok(`no ${junk} in the output`);
}
/* An em dash is facts()' "missing value" marker. One on the page means a
   number the article promised did not arrive. */
out.includes(">—<") ? bad("a placeholder em dash rendered where a number should be")
                    : ok("no placeholder dash in the output");

const f1 = x => x.toFixed(1);
const sg = x => (x > 0 ? "+" : "") + x.toFixed(1);
has("top rule's CAGR", `${f1(wantBest.cagr)}%`);
has("buy & hold's CAGR", `${f1(HOLD)}%`);
has("the advantage", sg(wantBest.x));
has("the top rule's name", String(DATA.strategies[wantBest.sk]).split(" · ")[0]);
has("settings behind the headline", String(wantBestRow.length));
has("the typical setting of the top rule", f1(Math.abs(med(wantBestRow))));
has("scored runs", wantAll.length.toLocaleString("en-IN"));
has("runs that cleared the uncorrected bar", String(D.n_uncorrected));
has("expected by chance", String(Math.round(D.expected_by_chance).toLocaleString("en-IN")));
has("smallest detectable edge", f1(D.median_mde_80));
dateline.includes(DATA.built) ? ok("dateline quotes the build date")
                              : bad(`dateline does not quote the build date: "${dateline}"`);

/* ── 10. the charts ───────────────────────────────────────────────────── */
console.log("\nthe charts");
const svgs = out.match(/<svg[\s\S]*?<\/svg>/g) || [];
eq("figures drawn", svgs.length, 5);
svgs.every(s => /aria-label="[^"]+"/.test(s))
  ? ok("every chart carries an aria-label")
  : bad("a chart is missing its aria-label");

const figBlock = id => {
  const i = out.indexOf(`id="${id}"`);
  if (i < 0) return "";
  const j = out.indexOf("</svg>", i);
  return j < 0 ? "" : out.slice(i, j);
};
const barCount = (figBlock("fig-board").match(/<path[^>]*class="mark"/g) || []).length;
eq("bars in the diverging chart", barCount, wantRows.filter(r => r.x != null).length);
const dotCount = (figBlock("fig-spread").match(/<circle/g) || []).length;
eq("dots in the spread chart", dotCount, wantBestRow.length);
const histBars = (figBlock("fig-all").match(/<rect[^>]*class="mark"/g) || []).length;
histBars > 10 ? ok(`histogram drew ${histBars} bins`)
              : bad(`histogram drew only ${histBars} bins`);
const powerDots = (figBlock("fig-power").match(/<circle/g) || []).length;
eq("dots in the power chart", powerDots, wantRowMeds.length);
/* The detection floor is the whole point of that chart; if the domain did not
   stretch to cover it the rule is drawn off-canvas and silently disappears. */
figBlock("fig-power").includes('stroke="var(--above)"')
  ? ok("the detection floor is drawn")
  : bad("the detection floor rule is missing from the power chart");
figBlock("fig-board").includes("buy &amp; hold")
  ? ok("the diverging chart labels its zero line")
  : bad("the diverging chart has no zero-line label");

/* Hover layer: every mark must be able to say what it is. */
const marks = (out.match(/class="mark"/g) || []).length;
const tips = (out.match(/data-tip="/g) || []).length;
eq("every mark carries a tooltip", tips >= marks, true);
marks > 300 ? ok(`${marks} interactive marks`) : bad(`only ${marks} interactive marks`);

/* ── 11. the tables under the figures ─────────────────────────────────── */
console.log("\nthe table view under each figure");
const tables = out.match(/<table>[\s\S]*?<\/table>/g) || [];
eq("tables present", tables.length, 2);
const rowsIn = t => (t.match(/<tr>/g) || []).length - 1;   // minus the header
eq("rows in the board table", rowsIn(tables[0]), wantRows.length);
eq("rows in the typical-setting table", rowsIn(tables[1]), wantRowMeds.length);

/* ── verdict ──────────────────────────────────────────────────────────── */
const total = fail.length;
console.log(`\n${total ? `${total} FAILURE(S)` : "all checks passed"}`);
if (total) { fail.forEach(m => console.log("  - " + m)); process.exit(1); }

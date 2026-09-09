/* Dry-run the dashboard page against the dashboard.json it will actually serve,
 * and check the NUMBERS on the page against the payload, not just that markup
 * came out.
 *
 *     node scripts/check_dashboard.js [path/to/dashboard.json]
 *
 * WHY THIS EXISTS. Compiling is not working. MAR was computed, left out of the
 * payload, and sorted on by the compare page for a whole day while every file
 * compiled and every import resolved; `parse_qs` was never imported and the
 * route that used it returned 500 on every request, because a name is only
 * looked up when the line runs. Neither was a syntax error and neither could
 * have been caught by one.
 *
 * WHY IT CHECKS VALUES (2026-09-07). The first version only counted rows and
 * grepped for "undefined". It passed while Detail printed an em dash for
 * Sharpe on every row (`r.r.sharpe` on a record that was already the cell),
 * while the banner said "the 24" on a board of 19, and while "vs Hold"
 * compared a 2024 rule against a 2018 hold. A rendered dash is not a missing
 * field to a render-only check. So this now:
 *
 *   - refuses any payload missing a key the page reads (the contract in the
 *     REQUIRED_* lists below), so a build that drops `maxdd` fails here and
 *     not on a reader's screen;
 *   - recomputes every Compare cell and all five Validated gates from the
 *     payload INDEPENDENTLY of the page's own rows(), formats them the same
 *     way, and compares cell by cell at several scenarios;
 *   - checks the banner quotes the payload's counts, that Detail shows the
 *     payload's Sharpe/Sortino/credibility pieces, and waits for the curve
 *     fetch before deciding, so an exception in the chart path cannot hide
 *     behind an early exit.
 *
 * It loads the REAL <script> block out of web/dashboard.html under a stub DOM.
 * Exits non-zero if any check fails, so it can go in front of a commit.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");
// Default matches config.CLEAN's own resolution order, so this finds the
// same file the server serves without being told where it is.
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
const pagePath = path.join(ROOT, "web", "dashboard.html");
console.log(`checking ${pagePath}\n     against ${jsonPath}`);
const src = fs.readFileSync(pagePath, "utf8");
let js = src.match(/<script>\n([\s\S]*)<\/script>/)[1];
js = js.replace(/fetch\("\/api\/dashboard"\)[\s\S]*?\.catch\([\s\S]*?\}\);/, "");
js = js.replace(/fetch\("\/api\/status"\)[\s\S]*?\.catch\(\(\) => \{\}\);/, "");

const fail = [];
const ok = m => console.log("  ok   " + m);
const bad = m => { fail.push(m); console.log("  FAIL " + m); };

/* ── stub DOM ─────────────────────────────────────────────────────────── */
const els = {};
function mk(id) {
  const e = {
    id, value: "", className: "", title: "",
    dataset: {}, options: [], children: [], style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    /* Record options by the CHILD's tag, not the parent's. getElementById
       cannot know the element it is handing back is a <select>, so keying on
       the parent meant every picker built through it reported zero options --
       and a check that read options.length silently passed on an empty one. */
    appendChild(c) {
      this.children.push(c);
      if (this.tag === "select" || c.tag === "option") this.options.push(c);
    },
    addEventListener() {}, querySelector: () => mk(), remove() {},
    setAttribute() {},
    // the table API renderCompare drives
    createTHead() { return (this._head = this._head || mk()); },
    createTBody() { return (this._body = this._body || mk()); },
    insertRow() { const r = mk(); r.cells = []; this.children.push(r); this.rows.push(r); return r; },
    insertCell() { const c = mk(); this.children.push(c); (this.cells = this.cells || []).push(c); return c; },
  };
  e.rows = [];
  /* innerHTML = "" empties a real element, including a table's thead/tbody.
     The first stub kept them, so every render APPENDED to the last one's rows
     and a check that counted rows saw 38, 57, 76... */
  let html = "", tc = "";
  // A browser coerces textContent to a string; the rank column assigns a number.
  Object.defineProperty(e, "textContent", { get() { return tc; }, set(v) { tc = v == null ? "" : String(v); } });
  Object.defineProperty(e, "innerHTML", {
    get() { return html; },
    set(v) { html = v; if (v === "") { e._head = e._body = undefined; e.children = []; e.rows = []; e.cells = []; e.options = []; } },
  });
  return e;
}
global.document = {
  getElementById: id => els[id] || (els[id] = mk(id)),
  createElement: tag => { const e = mk(); e.tag = tag; return e; },
  querySelector: () => mk(),
};
global.window = {};

/* THE CURVE ROUTE, served from the sidecar next to the payload exactly as
   dashboard_server does (byte offset + length). Without this the page's
   fetch() would reject on a relative URL and the rejection would land after
   process.exit -- which is how the chart path went unexercised. If there is
   no sidecar (a payload copied on its own) a synthetic curve keeps the chart
   code running rather than silently skipping it. */
const curvesPath = jsonPath.replace(/\.json$/, ".curves.jsonl");
const haveCurves = fs.existsSync(curvesPath);
global.fetch = async url => {
  const m = /\/api\/curve\?at=(\d+)&len=(\d+)/.exec(String(url));
  if (!m) return { ok: false, json: async () => ({}) };
  if (!haveCurves) {
    const eq = Array.from({ length: 40 }, (_, i) => 200000 + 1000 * i);
    return { ok: true, json: async () => ({
      curve: { c: 0, eq, cash: eq.map(v => v / 2), dd: eq.map(() => -1) }, episodes: [{}] }) };
  }
  const at = +m[1], len = +m[2];
  const fd = fs.openSync(curvesPath, "r");
  const buf = Buffer.alloc(len);
  fs.readSync(fd, buf, 0, len, at);
  fs.closeSync(fd);
  return { ok: true, json: async () => JSON.parse(buf.toString("utf8")) };
};

const driver = `
  DATA = hydrate(JSON.parse(require("fs").readFileSync(process.env.KITELAB_DASHBOARD_JSON, "utf8")));
  init();
  module.exports = { DATA, S, render, rows, sortedRows, variantsOf, settingLabel, keyFor, VIEWS };
`;
process.env.KITELAB_DASHBOARD_JSON = jsonPath;
const mod = { exports: {} };
new Function("module", "require", js + driver)(mod, require);
const { DATA, S, render, rows, sortedRows, variantsOf, settingLabel, keyFor, VIEWS } = mod.exports;

/* ── helpers ──────────────────────────────────────────────────────────── */
const text = html => String(html || "").replace(/<[^>]+>/g, " ")
  .replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&")
  .replace(/&mdash;/g, "—").replace(/&ndash;/g, "–").replace(/&nbsp;/g, " ")
  .replace(/\s+/g, " ");
const has = (o, k) => o != null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k);
const missing = (o, keys) => keys.filter(k => !has(o, k));
const prioDefault = () => S.prio || DATA.priority_default;
const tableRows = () => ((els["cmp"] || {})._body || { rows: [] }).rows
  .map(tr => tr.cells.map(td => td.textContent));

/* ═══════════════════════════════════════════════════════════════════════
   1. THE PAYLOAD CONTRACT. Every key the page reads must be PRESENT (null is
   a value; absence is a build fault). Lists mirror the reads in
   web/dashboard.html; if you add a read there, add the key here.
   ═══════════════════════════════════════════════════════════════════════ */
console.log("\n== payload contract ==");
const REQUIRED_TOP = ["built", "strategies", "universes", "risks", "capitals", "priorities",
  "priority_default", "single_name", "start_years", "start_default", "fills", "grid",
  "validation", "validation_summary", "curve_index", "partial", "assets"];
const REQUIRED_SUMMARY = ["tried", "n_eff", "avg_correlation", "alpha", "hurdle", "expected_best",
  "expected_by_chance", "cleared", "best_t", "best_key", "clears_hurdle", "hold_cagr_by_scenario"];
const REQUIRED_CELL = ["cagr", "wiped", "final", "maxdd", "uw_long", "uw_now", "taken", "signals",
  "mar", "ulcer", "sharpe", "sortino", "exposure", "full_pct", "skipped_cash",
  "skipped_tiny_cash", "skipped_tiny_risk", "skipped_size", "t_taken"];
const REQUIRED_VAL = ["top_n", "breakeven", "bootstrap", "permutation", "fixed_gates",
  "walk_forward_by_scenario", "fixed_checks_by_universe"];
const REQUIRED_CRED = ["n", "n_clusters", "mean_r", "drift_r", "se_r", "t_iid", "t_cluster", "t_stat", "t_gate",
  "p05_mean_r", "p50_mean_r", "p95_mean_r", "p_neg", "p05", "p50", "p95"];
const REQUIRED_PERM = ["observed", "shuffled_median", "beat_by", "rounds", "p", "distinguishable", "pool"];
const REQUIRED_GATES = ["distinguishable", "breakeven_margin"];
const REQUIRED_WF = ["wins", "total_windows", "windows"];
const REQUIRED_WINDOW = ["from", "to", "cagr", "hold", "win", "partial"];
const REQUIRED_TS = ["trades", "wins", "win_rate", "pf", "rr", "expectancy", "avg_win", "avg_loss", "best", "worst",
  "worst_run", "paper_capital", "paper_risk_pct", "expectancy_r", "avg_win_r", "avg_loss_r"];

{
  const m = missing(DATA, REQUIRED_TOP);
  m.length ? bad(`top level missing: ${m.join(", ")}`) : ok(`top level carries all ${REQUIRED_TOP.length} keys the page reads`);
  Array.isArray(DATA.partial) ? ok(`partial = [${DATA.partial.join(", ")}]`) : bad("partial is not a list");
  console.log(`  universes: ${Object.keys(DATA.universes || {}).join(", ")}`);
  console.log(`  grid cells: ${Object.keys(DATA.grid || {}).length}, validation records: ${Object.keys(DATA.validation || {}).length}`);
}
const VS = DATA.validation_summary || {};
{
  const m = missing(VS, REQUIRED_SUMMARY);
  m.length ? bad(`validation_summary missing: ${m.join(", ")}`) : ok("validation_summary carries the multiple-testing arithmetic and hold_cagr_by_scenario");
  if (VS.hold_cagr_by_scenario) {
    const want = [];
    for (const u of Object.keys(DATA.universes || {}))
      for (const y of DATA.start_years || []) if (!has(VS.hold_cagr_by_scenario, `${u}|${y}`)) want.push(`${u}|${y}`);
    want.length ? bad(`hold_cagr_by_scenario lacks ${want.length} universe|year keys: ${want.slice(0, 5).join(", ")}${want.length > 5 ? " ..." : ""}`)
                : ok(`hold_cagr_by_scenario has every universe × start year (${Object.keys(VS.hold_cagr_by_scenario).length} keys)`);
  }
  if (VS.n_eff > VS.tried) bad(`n_eff (${VS.n_eff}) exceeds tried (${VS.tried})`);
  has(DATA.universes || {}, "recent") ? ok(`recent universe present: ${DATA.universes.recent}`)
                                      : bad("no `recent` universe -- the fifth liquidity bucket (contract: universes)");
}
{
  const cells = Object.entries(DATA.grid || {});
  const byKey = {};
  let nulls = 0;
  for (const [k, c] of cells) {
    if (c === null) { nulls++; continue; }
    for (const mk of missing(c, REQUIRED_CELL)) byKey[mk] = (byKey[mk] || 0) + 1;
  }
  const m = Object.entries(byKey);
  m.length ? bad(`grid cells missing keys: ${m.map(([k, n]) => `${k} (${n} cells)`).join(", ")}`)
           : ok(`every non-null grid cell carries all ${REQUIRED_CELL.length} keys the page reads (${nulls} null cells)`);
  const cagrs = cells.map(([, c]) => c && c.cagr).filter(v => v != null);
  new Set(cagrs).size > 1 && cagrs.some(v => v !== 0)
    ? ok("cagr varies across the grid") : bad("cagr is constant/zero across the grid -- the build emitted a placeholder");
}
{
  const problems = [];
  let stale = 0;
  for (const [key, v] of Object.entries(DATA.validation || {})) {
    if (has(v, "hold_cagr")) stale++;
    for (const mk of missing(v, REQUIRED_VAL)) problems.push(`${key}: ${mk}`);
    const checks = Object.assign({ "(top)": { credibility: v.bootstrap, permutation: v.permutation, fixed_gates: v.fixed_gates } },
                                 v.fixed_checks_by_universe || {});
    for (const [uni, fc] of Object.entries(checks)) {
      if (!fc) { problems.push(`${key}/${uni}: empty`); continue; }
      for (const mk of missing(fc.credibility, REQUIRED_CRED)) problems.push(`${key}/${uni}.credibility: ${mk}`);
      for (const mk of missing(fc.permutation, REQUIRED_PERM)) problems.push(`${key}/${uni}.permutation: ${mk}`);
      for (const mk of missing(fc.fixed_gates, REQUIRED_GATES)) problems.push(`${key}/${uni}.fixed_gates: ${mk}`);
      if (fc.permutation && fc.fixed_gates && fc.permutation.distinguishable !== fc.fixed_gates.distinguishable)
        problems.push(`${key}/${uni}: fixed_gates.distinguishable disagrees with permutation.distinguishable`);
    }
    for (const [sk, wf] of Object.entries(v.walk_forward_by_scenario || {})) {
      for (const mk of missing(wf, REQUIRED_WF)) problems.push(`${key}/${sk}: ${mk}`);
      for (const w of wf.windows || [])
        for (const mk of missing(w, REQUIRED_WINDOW)) problems.push(`${key}/${sk} window: ${mk}`);
      if (wf.windows) {
        const counted = wf.windows.filter(w => !w.partial && w.win != null);
        const wins = counted.filter(w => w.win === true).length;
        if (counted.length !== wf.total_windows || wins !== wf.wins)
          problems.push(`${key}/${sk}: wins/total_windows (${wf.wins}/${wf.total_windows}) do not match the windows (${wins}/${counted.length})`);
        for (const w of wf.windows) if (w.cagr != null && w.hold != null && !w.partial && w.win !== (w.cagr > w.hold))
          problems.push(`${key}/${sk} ${w.from}: win flag disagrees with cagr > hold`);
      }
    }
  }
  const uniq = [...new Set(problems)];
  uniq.length ? bad(`validation records: ${uniq.length} contract problems, e.g. ${uniq.slice(0, 4).join("; ")}`)
              : ok("every validation record carries the clustered credibility, permutation p/rounds/pool and walk-forward windows");
  stale ? bad(`${stale} validation records still carry hold_cagr -- removed from the contract 2026-09-07`)
        : ok("no validation record carries the retired hold_cagr");
}
{
  const problems = [];
  for (const [key, t] of Object.entries(DATA.trade_stats || {}))
    for (const mk of missing(t, REQUIRED_TS)) problems.push(`${key}: ${mk}`);
  problems.length ? bad(`trade_stats: ${problems.length} missing fields, e.g. ${problems.slice(0, 4).join("; ")}`)
                  : ok(`every trade_stats record carries the R-multiple fields and its paper book (${Object.keys(DATA.trade_stats || {}).length} records)`);
}
{
  // The page must not read the retired key either. Strip comments the way
  // scripts/preflight.py does, then look for a bare hold_cagr read.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/^\s*\/\/.*$/gm, " ");
  /\bhold_cagr\b(?!_by_scenario)/.test(code)
    ? bad("dashboard.html still reads validation[key].hold_cagr")
    : ok("the page reads hold_cagr_by_scenario, never hold_cagr");
}

/* ═══════════════════════════════════════════════════════════════════════
   2. THE COMPARE TABLE, CELL BY CELL. Expected values are built here from
   the payload with the page's rows() nowhere in the loop; only the label
   text (settingLabel) is borrowed, because it is a label, not a number.
   ═══════════════════════════════════════════════════════════════════════ */
const fmt = {
  pct1: v => v == null ? "—" : v.toFixed(1) + "%",
  num2: v => v == null ? "—" : v.toFixed(2),
  signed1: v => v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(1),
  int: v => v == null ? "—" : v.toLocaleString("en-IN"),
};
function expectedRows(uni, year, prio, capIdx, riskIdx) {
  const capital = DATA.capitals[capIdx], risk = DATA.risks[riskIdx];
  const hold = (VS.hold_cagr_by_scenario || {})[`${uni}|${year}`];
  const out = [];
  for (const [skey, family] of Object.entries(DATA.strategies))
    for (const v of variantsOf(skey)) {
      const key = `${skey}|${v}|${uni}|${risk}|${capital}|${S.fill}|${year}|${prio}`;
      const cell = DATA.grid[key];
      const val = (DATA.validation || {})[`${skey}|${v}`] || null;
      const setting = settingLabel(skey, v);
      if (!cell) {
        out.push({ key, family, setting: setting === "—" ? "no trades" : `${setting} · no trades`,
                   noTrades: true, validated: null, gates: null, mar: null,
                   cells: ["—", "—", "—", "—", "—", "—", "—", "—"] });
        continue;
      }
      const fcu = val && val.fixed_checks_by_universe;
      const fc = !val ? null : (fcu && fcu[uni]) ? fcu[uni]
        : { credibility: val.bootstrap, permutation: val.permutation, fixed_gates: val.fixed_gates };
      const cred = fc && fc.credibility, fg = fc && fc.fixed_gates;
      const wf = val && (val.walk_forward_by_scenario || {})[`${uni}|${prio}|${capital}`];
      const vsHold = (hold != null && !cell.wiped && cell.cagr != null) ? cell.cagr - hold : null;
      const gates = [
        fg ? fg.distinguishable : null,
        fg ? fg.breakeven_margin : null,
        vsHold == null ? null : vsHold > 0,
        (wf && wf.total_windows) ? wf.wins * 2 > wf.total_windows : null,
        (cred && cred.t_gate != null && VS.hurdle != null) ? cred.t_gate > VS.hurdle : null,
      ];
      const validated = gates.every(g => g != null) ? gates.every(g => g === true) : null;
      const passed = gates.filter(g => g === true).length;
      out.push({ key, family, setting, noTrades: false, validated, mar: cell.mar,
        cells: [
          validated == null ? "—" : (validated ? "✓" : "✗") + ` ${passed}/5`,
          cell.wiped ? "wiped" : fmt.pct1(cell.cagr),
          fmt.signed1(vsHold),
          fmt.pct1(cell.maxdd),
          fmt.num2(cell.mar),
          wf ? `${wf.wins}/${wf.total_windows}` : "—",
          cred && cred.t_stat != null ? "t=" + cred.t_stat.toFixed(1) : "—",
          fmt.int(cell.taken),
          cell.signals ? Math.round(100 * cell.taken / cell.signals) + "%" : "—",
        ] });
    }
  return out;
}
const HEAD = ["Strategy", "Setting", "Validated", "CAGR", "vs Hold", "Drawdown", "MAR", "Walk-fwd", "Credibility", "Trades", "Captured"];

function checkScenario(label, uni, year, prio, capIdx = 0, riskIdx = 1) {
  S.view = "compare"; S.uni = uni; S.year = year; S.cap = capIdx; S.risk = riskIdx;
  S.prio = (DATA.single_name || []).includes(uni) ? null : prio;
  S.sort = "rankKey"; S.desc = true;
  render();
  const got = tableRows();
  const head = ((els["cmp"] || {})._head || { rows: [] }).rows[0];
  const headText = head ? head.children.map(c => c.textContent).slice(1) : [];
  if (headText.join("|") !== HEAD.join("|")) bad(`${label}: header is ${headText.join("|")}`);
  const exp = expectedRows(uni, year, prioDefault(), capIdx, riskIdx);
  if (got.length !== exp.length) { bad(`${label}: table has ${got.length} rows, payload implies ${exp.length}`); return; }
  const byLabel = new Map(exp.map(e => [`${e.family}|${e.setting}`, e]));
  if (byLabel.size !== exp.length) bad(`${label}: strategy+setting labels are not unique -- cannot match rows`);
  let mismatches = [];
  let seenValidated = 0, lastMar = Infinity, group = "✓", noTradesStarted = false;
  got.forEach((cells, i) => {
    const [rank, family, setting, ...rest] = cells;
    if (rank !== String(i + 1)) mismatches.push(`row ${i + 1}: rank cell ${rank}`);
    const e = byLabel.get(`${family}|${setting}`);
    if (!e) { mismatches.push(`row ${i + 1}: no payload row labelled "${family} | ${setting}"`); return; }
    byLabel.delete(`${family}|${setting}`);
    // one cell fewer than HEAD-2: the CAGR cell for a no-trade row is a dash too
    const want = e.noTrades ? Array(rest.length).fill("—") : e.cells;
    rest.forEach((g, j) => { if (g !== want[j]) mismatches.push(`row ${i + 1} ${family}/${setting} ${HEAD[j + 2]}: page "${g}", payload "${want[j]}"`); });
    if (e.validated === true) seenValidated++;
    // ordering: ✓ block first, then the rest by MAR desc, no-trade rows last
    if (e.noTrades) noTradesStarted = true;
    else {
      if (noTradesStarted) mismatches.push(`row ${i + 1}: a traded row sorts below a no-trade row`);
      const g = e.validated === true ? "✓" : "rest";
      if (g !== group) { if (group === "rest") mismatches.push(`row ${i + 1}: a Validated row sorts below a non-validated one`); group = g; lastMar = Infinity; }
      const m = e.mar == null ? -Infinity : e.mar;
      if (m > lastMar + 1e-9) mismatches.push(`row ${i + 1}: MAR ${e.mar} sorts below a smaller MAR in the same group`);
      lastMar = m;
    }
  });
  const tableValidated = got.filter(c => c[3].startsWith("✓")).length;
  if (tableValidated !== seenValidated)
    mismatches.push(`Validated count: table shows ${tableValidated}, payload implies ${seenValidated}`);
  mismatches.length
    ? bad(`${label}: ${mismatches.length} cell mismatches -- ${mismatches.slice(0, 3).join(" | ")}`)
    : ok(`${label}: ${got.length} rows × ${HEAD.length - 2} value cells match the payload; ${tableValidated} Validated; order holds`);
  const nt = got.filter(c => c[2].endsWith("no trades")).length;
  if (nt) ok(`${label}: ${nt} no-trade row(s) rendered as dashes at the bottom`);
}

console.log("\n== compare table values vs payload ==");
S.fill = DATA.fills[0][0];
checkScenario(`default all/${DATA.start_default}`, "all", DATA.start_default, DATA.priority_default, 0, 1);
{
  const otherUni = ["large", "mid", "small", "recent"].find(u => DATA.universes[u]);
  const otherYear = DATA.start_years.find(y => y !== DATA.start_default);
  const otherPrio = (DATA.priorities || []).find(p => p !== DATA.priority_default) || DATA.priority_default;
  if (otherUni && otherYear != null)
    checkScenario(`${otherUni}/${otherYear}/${otherPrio}/${DATA.capitals[DATA.capitals.length - 1]}`,
                  otherUni, otherYear, otherPrio, DATA.capitals.length - 1, 0);
  else bad("no second universe/year to check");
  for (const sym of DATA.single_name || [])
    checkScenario(`${sym}/${DATA.start_default} (own hold key)`, sym, DATA.start_default, DATA.priority_default);
  // a scenario with at least one null cell, so the no-trade row path is exercised
  const nullKey = Object.keys(DATA.grid).find(k => DATA.grid[k] === null && k.split("|")[5] === S.fill);
  if (nullKey) {
    const [, , u, risk, cap, , y, p] = nullKey.split("|");
    checkScenario(`null cell ${u}/${y}`, u, +y, p, DATA.capitals.findIndex(c => String(c) === cap),
                  DATA.risks.findIndex(r => String(r) === risk));
  } else console.log("  (no null grid cell in this payload -- no-trade rendering not exercised)");
}
S.uni = "all"; S.year = DATA.start_default; S.prio = null; S.cap = 0; S.risk = 1;

/* ═══════════════════════════════════════════════════════════════════════
   3. BANNER, TOOLTIPS, GLOSSARY: quote the payload, never a literal count.
   ═══════════════════════════════════════════════════════════════════════ */
console.log("\n== multiple-testing banner ==");
{
  S.view = "compare"; render();
  const note = els["cmp-multitest"] || {};
  const t = text(note.innerHTML);
  note.hidden === false ? ok("banner shown") : bad("banner hidden despite validation_summary");
  for (const k of ["tried", "hurdle", "cleared", "expected_best", "expected_by_chance", "n_eff", "avg_correlation", "best_key"])
    t.includes(String(VS[k])) ? ok(`banner quotes ${k} = ${VS[k]}`) : bad(`banner does not contain ${k} = ${VS[k]}: "${t.slice(0, 160)}..."`);
  /^\s*\d+ of \d+ rules clear a family-wise \d+% bar/.test(t)
    ? ok("banner opens with 'N of M rules clear a family-wise 95% bar'") : bad(`banner wording: "${t.slice(0, 120)}"`);
  /the \d+\b/.test(t.replace(new RegExp(`the ${VS.tried}\\b`), "")) && bad(`banner still has a literal count: "${t.match(/the \d+\b/)[0]}"`);
  const formulas = text((els["cmp-formulas"] || {}).innerHTML);
  for (const word of ["drift_r", "t_taken", "t_cluster", "hurdle", "Fully deployed", "₹2L / 1% / most-liquid-first", "strict majority", "selected start year"])
    formulas.includes(word) ? ok(`formulas/glossary mention "${word}"`) : bad(`formulas/glossary lack "${word}"`);
  /5,000|block-resampled/.test(formulas) && bad("formulas still describe the 5,000-round block bootstrap");
  /\bthe 24\b|\b13 variants\b/.test(formulas) && bad("formulas carry a literal board size");
}

/* ═══════════════════════════════════════════════════════════════════════
   4. DETAIL: payload numbers, not dashes -- and wait for the curve.
   ═══════════════════════════════════════════════════════════════════════ */
async function checkDetail() {
  console.log("\n== detail view values ==");
  S.view = "compare"; S.uni = "all"; S.year = DATA.start_default; S.prio = null; S.cap = 0; S.risk = 1; render();
  const prio = prioDefault(), capital = DATA.capitals[0];
  const candidates = Object.entries(DATA.strategies).flatMap(([skey, family]) => variantsOf(skey).map(v => {
    const cell = DATA.grid[keyFor(skey, v)];
    const val = DATA.validation[`${skey}|${v}`];
    return { id: `${skey}|${v}`, family, cell, val };
  })).filter(c => c.cell && c.cell.sharpe != null && c.cell.sortino != null && c.val);
  if (!candidates.length) { bad("no row with a Sharpe and a validation record to open in Detail"); return; }
  const pick = candidates.find(c => c.cell.t_taken != null) || candidates[0];
  const { cell, val } = pick;
  S.view = "detail"; S.sel = pick.id;
  const p = render();
  (p && typeof p.then === "function") ? ok("render() returns the curve promise in Detail") : bad("render() did not return a promise for Detail");
  await p;
  const html = (els["det-body"] || {}).innerHTML || "";
  const t = text(html);
  const stat = label => { const m = new RegExp(`${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} (\\S+)`).exec(t); return m ? m[1] : null; };
  const eq = (label, want) => { const g = stat(label);
    g === want ? ok(`${pick.id}: ${label} = ${want}`) : bad(`${pick.id}: ${label} shows "${g}", payload says "${want}"`); };
  eq("Sharpe", cell.sharpe.toFixed(2));
  eq("Sortino", cell.sortino.toFixed(2));
  eq("MAR", fmt.num2(cell.mar));
  eq("Invested", Math.round(cell.exposure) + "%");
  eq("Fully deployed", Math.round(cell.full_pct) + "%");
  eq("Ulcer", cell.ulcer == null ? "—" : cell.ulcer.toFixed(1));
  const captured = `Captured ${fmt.int(cell.taken)} / ${fmt.int(cell.signals)} signals; ${fmt.int(cell.skipped_cash)} unaffordable (cash); `
    + `${fmt.int(cell.skipped_tiny_cash)} under the ₹5.5k floor from cash scraps; ${fmt.int(cell.skipped_tiny_risk)} under it from risk sizing; `
    + `${fmt.int(cell.skipped_size)} over the size cap.`;
  t.includes(captured) ? ok("Captured line itemises the four refusal counts") : bad(`Captured line wrong; wanted "${captured}"`);
  // credibility card
  const cred = (val.fixed_checks_by_universe && val.fixed_checks_by_universe.all) ? val.fixed_checks_by_universe.all.credibility : val.bootstrap;
  const f3 = v => v == null ? "—" : v.toFixed(3), f2 = v => v == null ? "—" : v.toFixed(2);
  eq("n_clusters", fmt.int(cred.n_clusters));
  eq("mean_r", f3(cred.mean_r));
  eq("drift_r", f3(cred.drift_r));
  eq("t_iid", f2(cred.t_iid));
  eq("t_cluster", f2(cred.t_cluster));
  eq("t_stat", f2(cred.t_stat));
  eq("t_taken", f2(cell.t_taken));
  /5,000|block-resampled/i.test(t) ? bad("Detail still describes the 5,000-round block bootstrap") : ok("Detail describes the clustered, drift-adjusted t");
  t.includes("one quarter as one bet") || t.includes("one quarter, one bet") ? ok("credibility caption explains the clustering") : bad("credibility caption lacks the one-quarter-one-bet explanation");
  // walk-forward table
  const wf = (val.walk_forward_by_scenario || {})[`all|${prio}|${capital}`];
  if (!wf) bad(`no walk_forward_by_scenario for all|${prio}|${capital}`);
  else {
    t.includes(`wins/total ${wf.wins}/${wf.total_windows}`) ? ok(`walk-forward header wins/total ${wf.wins}/${wf.total_windows}`)
      : bad(`walk-forward header lacks wins/total ${wf.wins}/${wf.total_windows}`);
    const rowsMissing = wf.windows.filter(w => {
      const want = `${w.from} ${w.to} ${fmt.signed1(w.cagr)} ${fmt.signed1(w.hold)} ${w.win == null ? "—" : w.win ? "✓" : "✗"}`
        + (w.partial ? " partial, not counted" : "");
      return !t.includes(want);
    });
    rowsMissing.length ? bad(`${rowsMissing.length} walk-forward windows not rendered as expected, e.g. ${rowsMissing[0].from}`)
                       : ok(`all ${wf.windows.length} walk-forward windows rendered with from/to/rule/hold/win${wf.windows.some(w => w.partial) ? "/partial" : ""}`);
  }
  const perm = (val.fixed_checks_by_universe && val.fixed_checks_by_universe.all) ? val.fixed_checks_by_universe.all.permutation : val.permutation;
  t.includes(`p = ${perm.p == null ? "—" : perm.p.toFixed(3)} over ${perm.rounds} shuffles of a ${perm.pool}-stock pool, at ₹2L / 1% / most-liquid-first`)
    ? ok("shuffled-price note quotes p, rounds, pool and the fixed account settings") : bad("shuffled-price note lacks p/rounds/pool/settings");
  t.includes("Breakeven cost") && t.includes("at ₹2L / 1% / most-liquid-first") ? ok("cost caption states the fixed account settings") : bad("cost caption lacks the fixed account settings");
  // trade quality: R first, rupees captioned with the payload's paper book
  const ts = (DATA.trade_stats || {})[pick.id];
  if (!ts) bad(`no trade_stats for ${pick.id}`);
  else {
    const R = v => v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2) + " R";
    const inrChk = v => Math.abs(v) >= 1e7 ? "₹" + (v / 1e7).toFixed(2) + " cr" : Math.abs(v) >= 1e5 ? "₹" + (v / 1e5).toFixed(2) + " L" : "₹" + Math.round(v).toLocaleString("en-IN");
    const q = t.indexOf("Trade quality");
    const card = q < 0 ? "" : t.slice(q);
    const rFirst = card.indexOf(`Expectancy ${R(ts.expectancy_r)} average net per trade, in R`);
    rFirst >= 0 ? ok(`trade quality leads with expectancy ${R(ts.expectancy_r)}`) : bad("trade quality does not lead with expectancy in R");
    card.includes(`Avg win / loss ${R(ts.avg_win_r)} / ${R(ts.avg_loss_r)}`) ? ok("average win/loss shown in R") : bad("average win/loss in R missing");
    const paper = `on a ${inrChk(ts.paper_capital)} paper book at ${ts.paper_risk_pct}% risk, not this account`;
    const nPaper = card.split(paper).length - 1;
    nPaper >= 5 ? ok(`rupee figures captioned "${paper}" (${nPaper}×)`) : bad(`rupee figures captioned "${paper}" only ${nPaper}× -- wanted every rupee stat and the subtitle`);
    const rupeeAt = card.indexOf(`Expectancy ${inrChk(ts.expectancy)}`);
    rupeeAt > rFirst ? ok("rupee expectancy follows the R version") : bad("rupee expectancy precedes the R version");
    /₹1 ?L\b|₹1 lakh|1,00,000 paper/.test(card) && bad("trade quality carries a literal ₹1L paper book");
  }
  // the curve landed
  const eqHtml = (els["chart-eq"] || {}).innerHTML || "";
  eqHtml.includes("<svg") ? ok(`equity chart drawn after the curve fetch${haveCurves ? "" : " (synthetic curve: no sidecar beside the payload)"}`)
                          : bad("equity chart not drawn after awaiting the curve fetch");
  ((els["ep-note"] || {}).textContent || "").includes("spells") ? ok("drawdown episodes note filled in") : bad("ep-note not filled after the fetch");
  // and a no-trade row in Detail must not throw
  const nullKey = Object.keys(DATA.grid).find(k => DATA.grid[k] === null);
  if (nullKey) {
    const [skey, v, u, risk, cap, fill, y, pr] = nullKey.split("|");
    S.uni = u; S.year = +y; S.fill = fill; S.prio = (DATA.single_name || []).includes(u) ? null : pr;
    S.cap = DATA.capitals.findIndex(c => String(c) === cap); S.risk = DATA.risks.findIndex(r => String(r) === risk);
    S.sel = `${skey}|${v}`;
    try { await render(); const h = text((els["det-body"] || {}).innerHTML);
      h.includes("No trades") ? ok(`Detail on a null cell (${skey}|${v} @ ${u}/${y}) says No trades`) : bad("Detail on a null cell shows something other than No trades");
    } catch (e) { bad(`Detail on a null cell threw: ${e.message}`); }
    S.uni = "all"; S.year = DATA.start_default; S.prio = null; S.cap = 0; S.risk = 1; S.fill = DATA.fills[0][0];
  }
  S.sel = null; S.view = "compare";
}

/* ═══════════════════════════════════════════════════════════════════════
   5. THE OLDER SMOKE CHECKS: every axis resolves, controls behave, nothing
   renders as undefined. Kept because each caught something once.
   ═══════════════════════════════════════════════════════════════════════ */
function smoke() {
  console.log("\n== every axis resolves ==");
  S.view = "compare"; S.uni = "all"; S.year = DATA.start_default; S.prio = null;
  const withMar = () => rows().filter(r => r.mar != null).length;
  for (const prio of (DATA.priorities || [DATA.priority_default])) {
    S.prio = prio; render();
    withMar() > 0 ? ok(`priority ${prio.padEnd(10)}: ${withMar()} rows with a MAR`) : bad(`priority ${prio}: NO rows resolved -- axis published but not computed`);
  }
  S.prio = null;
  for (const [f, label] of (DATA.fills || [])) { S.fill = f; render(); withMar() > 0 ? ok(`${label}: ${withMar()} rows`) : bad(`${label}: offered but not gridded`); }
  S.fill = DATA.fills[0][0];
  for (const y of DATA.start_years) { S.year = y; render(); withMar() > 0 ? ok(`year ${y}: ${withMar()} rows`) : bad(`year ${y}: offered but not gridded`); }
  S.year = DATA.start_default;
  for (const u of Object.keys(DATA.universes)) {
    S.uni = u; S.prio = null; render();
    const cells = Object.keys(DATA.grid).filter(k => k.split("|")[2] === u);
    cells.length ? ok(`${u.padEnd(8)} ${cells.length} cells, ${withMar()} with trades`) : bad(`${u}: universe offered but NOT COMPUTED`);
    if ((DATA.single_name || []).includes(u)) {
      const gridded = new Set(cells.map(k => k.split("|")[7]));
      gridded.size === 1 && gridded.has(DATA.priority_default) ? ok(`${u.padEnd(8)} one priority only`) : bad(`${u}: ${gridded.size} priorities gridded on a single instrument`);
      const bh = (DATA.assets || {})[u];
      bh && bh.bh ? ok(`${u.padEnd(8)} buy & hold ${bh.bh.cagr}%/yr`) : bad(`${u}: no buy-and-hold benchmark in assets`);
      const labels = Object.values(els).map(e => e.textContent || "").join(" ");
      labels.includes("Signal priority") ? bad(`${u}: priority control still offered`) : ok(`${u.padEnd(8)} priority control hidden`);
    }
  }
  S.uni = "all";

  console.log("\n== Holy Grail stop on the board ==");
  const hg = variantsOf("hg");
  hg.length === 1 ? ok(`hg variants: ${hg.join(", ")}`) : bad(`hg variants: ${hg.join(", ")}, expected 1`);
  for (const v of hg) { const l = settingLabel("hg", v); /^stop at /.test(l) ? ok(`  "${v}" -> "${l}"`) : bad(`  "${v}" -> "${l}"`); }

  console.log("\n== detail strategy picker ==");
  S.view = "detail"; S.sel = null; render();
  {
    const pick = els["det-pick"] || { options: [] };
    pick.options.length > 1 ? ok(`picker offers ${pick.options.length} strategies`) : bad(`picker offers ${pick.options.length} options`);
    const other = pick.options.map(o => o.value).find(v => v !== S.sel);
    if (other) { S.sel = other; render(); S.sel === other ? ok(`switching to ${other} holds`) : bad(`switching reverted to ${S.sel}`); }
  }
  S.sel = null; S.view = "compare";

  console.log("\n== tailoring is live ==");
  S.uni = "all"; S.prio = "liquidity"; render();
  const snap = () => rows().filter(r => r.val).map(r => ({ validated: r.validated, walkText: r.walkText, tStat: r.tStat,
    d: r.fixedGates && r.fixedGates.distinguishable, b: r.fixedGates && r.fixedGates.breakeven_margin, vs: r.vsHold }));
  const a = snap(); S.prio = "illiquid"; render(); const b = snap();
  a.filter((x, i) => x.validated !== b[i].validated || x.walkText !== b[i].walkText).length
    ? ok("Validated/Walk-fwd change between two priorities") : bad("NOTHING changed between two priorities -- walk_forward_by_scenario lookup is broken");
  a.filter((x, i) => x.tStat !== b[i].tStat || x.d !== b[i].d || x.b !== b[i].b).length
    ? bad("trade-level checks moved with Priority -- they must not") : ok("trade-level checks do not move with Priority");
  S.prio = null;
  const smallUni = ["large", "mid", "small"].find(u => DATA.universes[u]);
  if (smallUni) {
    S.uni = "all"; render(); const c = snap(); S.uni = smallUni; render(); const d = snap();
    c.filter((x, i) => x.tStat !== d[i].tStat).length ? ok(`Credibility changes between all and ${smallUni}`) : bad("Credibility does not move with Universe");
    c.filter((x, i) => x.d !== d[i].d || x.b !== d[i].b).length ? ok(`distinguishable/breakeven change between all and ${smallUni}`) : bad("fixed gates do not move with Universe");
  }
  S.uni = "all"; render(); const y0 = snap(); S.year = DATA.start_years[0]; render(); const y1 = snap();
  y0.filter((x, i) => x.vs !== y1[i].vs).length ? ok(`vs Hold changes between ${DATA.start_default} and ${DATA.start_years[0]}`) : bad("vs Hold does not move with the start year -- hold_cagr_by_scenario lookup is broken");
  S.year = DATA.start_default;

  console.log("\n== partial-build banner ==");
  const pb = text((els["partial"] || {}).innerHTML);
  if ((DATA.partial || []).length)
    DATA.partial.every(m => pb.includes(m)) ? ok(`partial banner names ${DATA.partial.join(", ")}`) : bad(`partial banner does not name ${DATA.partial.join(", ")}`);
  else pb === "" ? ok("no partial banner on a full build") : bad(`partial banner shown on a full build: "${pb}"`);

  console.log("\n== no undefined/NaN in any view ==");
  for (const [v] of VIEWS) {
    S.view = v;
    try { render(); } catch (e) { bad(`${v} threw: ${e.message}`); continue; }
    let bads = 0, where = [];
    for (const [id, el] of Object.entries(els)) {
      const html = (el.innerHTML || "") + " " + (el.textContent || "");
      const hits = (html.match(/\bundefined\b|\bNaN\b/g) || []).length;
      if (!hits) continue;
      bads += hits;
      const at = html.search(/\bundefined\b|\bNaN\b/);
      where.push(`#${id}: ...${html.slice(Math.max(0, at - 45), at + 25).replace(/\s+/g, " ")}...`);
    }
    bads === 0 ? ok(`${v}: clean`) : bad(`${v}: ${bads} undefined/NaN -- ${where[0]}`);
  }
}

(async () => {
  try {
    await checkDetail();
    smoke();
  } catch (e) {
    bad(`check threw: ${e.stack}`);
  }
  console.log(fail.length ? `\n${fail.length} FAILURE(S)\n` : "\nall checks passed\n");
  process.exitCode = fail.length ? 1 : 0;
})();

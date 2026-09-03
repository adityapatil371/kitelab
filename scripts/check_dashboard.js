/* Dry-run the dashboard page against the dashboard it will actually be served.
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
 * So this loads the REAL <script> block out of web/dashboard.html, hands it the
 * REAL dashboard.json under a stub DOM, renders every view, and checks that
 * markup came out the other side. It is not a unit test and does not pretend to
 * be: it is the cheapest possible answer to "does the page still work with the
 * file we just spent sixteen minutes building".
 *
 * Exits non-zero if any check fails, so it can go in front of a commit.
 */
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
if (!jsonPath) {
  console.error("No dashboard.json found. Build one:  python -m scripts.refresh");
  process.exit(2);
}
console.log(`checking ${path.join(ROOT, "web", "dashboard.html")}\n     against ${jsonPath}`);
const src = fs.readFileSync(path.join(ROOT, "web", "dashboard.html"), "utf8");
let js = src.match(/<script>\n([\s\S]*)<\/script>/)[1];
js = js.replace(/fetch\("\/api\/dashboard"\)[\s\S]*?\.catch\([\s\S]*?\}\);/, "");
js = js.replace(/fetch\("\/api\/status"\)[\s\S]*?\.catch\(\(\) => \{\}\);/, "");

const els = {};
function mk(id) {
  const e = {
    id, innerHTML: "", textContent: "", value: "", className: "",
    dataset: {}, options: [], children: [], style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    appendChild(c) { this.children.push(c); if (this.tag === "select") this.options.push(c); },
    addEventListener() {}, querySelector: () => mk(), remove() {},
    setAttribute() {},
    // the table API renderCompare drives
    createTHead() { return (this._head = this._head || mk()); },
    createTBody() { return (this._body = this._body || mk()); },
    insertRow() { const r = mk(); r.cells = []; this.children.push(r); this.rows.push(r); return r; },
    insertCell() { const c = mk(); this.children.push(c); (this.cells = this.cells || []).push(c); return c; },
  };
  e.rows = [];
  return e;
}
global.document = {
  getElementById: id => els[id] || (els[id] = mk(id)),
  createElement: tag => { const e = mk(); e.tag = tag; return e; },
  querySelector: () => mk(),
};
global.window = {};

const driver = `
  DATA = hydrate(JSON.parse(require("fs").readFileSync(process.env.KITELAB_DASHBOARD_JSON, "utf8")));
  init();
  module.exports = { DATA, S, render, rows, variantsOf, settingLabel, keyFor, VIEWS };
`;
process.env.KITELAB_DASHBOARD_JSON = jsonPath;
const mod = { exports: {} };
new Function("module", "require", js + driver)(mod, require);

const { DATA, S, render, rows, variantsOf, settingLabel, keyFor, VIEWS } = mod.exports;
const fail = [];
const ok = m => console.log("  ok   " + m);
const bad = m => { fail.push(m); console.log("  FAIL " + m); };

console.log("\n== payload ==");
console.log(`  universes: ${Object.keys(DATA.universes).join(", ")}`);
console.log(`  pools:     ${(DATA.pools || []).map(p => p == null ? "all" : p).join(", ")}`);
console.log(`  hg_tags:   ${(DATA.hg_tags || []).join(", ")}`);
console.log(`  grid cells: ${Object.keys(DATA.grid).length}`);
console.log(`  breadth keys: ${Object.keys(DATA.breadth || {}).join(", ")}`);

console.log("\n== views render ==");
for (const [v] of VIEWS) {
  S.view = v;
  try {
    render();
    if (v === "detail") { ok(`${v} rendered`); continue; }
    // Views built through the DOM table API rather than by assigning innerHTML.
    // The stub does not serialise those, so counting rows is the only honest
    // check -- measuring innerHTML would report 0 chars for a table that
    // rendered perfectly, which is what it did for `assets` until 2026-09-03.
    const byTable = { compare: "cmp", assets: "ast" };
    if (byTable[v]) {
      const n = ((els[byTable[v]] || {})._body || { rows: [] }).rows.length;
      n > 0 ? ok(`${v} rendered ${n} rows`) : bad(`${v} rendered only ${n} rows`);
      continue;
    }
    const body = { breadth: "brd-body", stocks: "stk-body" }[v];
    const html = (els[body] || {}).innerHTML || "";
    html.length > 200 ? ok(`${v} rendered (${html.length} chars)`)
                      : bad(`${v} produced only ${html.length} chars`);
  } catch (e) { bad(`${v} threw: ${e.message}`); }
}

console.log("\n== both Holy Grail stops on the board ==");
const hg = variantsOf("hg");
hg.length === 2 ? ok(`hg variants: ${hg.join(", ")}`) : bad(`hg variants: ${hg.join(", ")}`);
for (const v of hg) {
  const label = settingLabel("hg", v);
  /^stop at /.test(label) ? ok(`  "${v}" -> "${label}"`) : bad(`  "${v}" -> "${label}"`);
}
S.view = "compare"; S.uni = "all"; render();
const hgRows = rows().filter(r => r.skey === "hg");
hgRows.length === 2 ? ok(`compare shows ${hgRows.length} Holy Grail rows`)
                    : bad(`compare shows ${hgRows.length} Holy Grail rows, expected 2`);
for (const r of hgRows) console.log(`       ${r.v.padEnd(7)} MAR ${r.mar}  CAGR ${r.cagr}  taken ${r.taken}`);

/* THE POOL AXIS MUST RESOLVE. Every size the page offers has to reach real grid
   cells. A missing cell renders as a blank row, not an error, so without this an
   axis that was published but never computed would look like a rule that simply
   made no trades -- which is exactly how MAR was sorted on for a day while it
   was absent from the payload. */
console.log("\n== scanning-pool axis ==");
S.uni = "all";
for (const pool of (DATA.pools || [null])) {
  S.pool = pool;
  if (pool != null && DATA.pool_year != null) S.year = DATA.pool_year;
  render();
  const got = rows().filter(r => r.mar !== null && r.mar !== undefined);
  const label = pool == null ? "all" : String(pool);
  got.length > 0 ? ok(`pool ${label.padEnd(4)}: ${got.length} rows with a MAR`)
                 : bad(`pool ${label.padEnd(4)}: NO rows resolved -- axis published but not computed`);
  if (pool != null && got.length) {
    const w = got.filter(r => r.r && r.r.pool_wiped !== undefined).length;
    const blown = got.filter(r => r.r && r.r.pool_wiped > 0).length;
    w === got.length ? ok(`       all ${w} report pool_wiped`)
                     : bad(`       only ${w}/${got.length} report pool_wiped`);
    console.log(`       ${blown} variant(s) had at least one basket destroyed`);
  }
}
S.pool = null;

console.log("\n== assets view (non-equity) ==");
S.view = "assets"; render();
{
  const names = Object.keys(DATA.assets || {});
  names.length ? ok(`${names.length} instruments: ${names.join(", ")}`)
               : bad("no non-equity instruments in the payload");
  const fams = Object.keys(DATA.strategies);
  for (const nm of names) {
    const row = DATA.assets[nm] || {};
    const got = fams.filter(f => row[f] && row[f].account).length;
    console.log(`       ${nm.padEnd(11)} ${got}/${fams.length} strategies ran`);
  }
}

console.log("\n== breadth view ==");
S.uni = "all"; S.view = "breadth"; render();
const brd = els["brd-body"].innerHTML;
const sub = els["brd-sub"].textContent;
for (const [k, r] of Object.entries(DATA.breadth || {})) {
  const sizes = r.map(x => x.size);
  console.log(`  ${k.padEnd(6)} ${r.length} sizes ${sizes[0]}..${sizes[sizes.length-1]}  median ${r.map(x=>x.median).join("/")}`);
}
/Stocks/.test(brd) ? ok("table header present") : bad("no table header");
/About \d+ stocks|No smaller basket|does not make money/.test(brd)
  ? ok("headline present") : bad("no headline: " + brd.slice(0, 160));
sub.length > 20 ? ok(`subtitle: ${sub}`) : bad("no subtitle");
(brd.match(/<tr>/g) || []).length >= 5 ? ok(`${(brd.match(/<tr>/g)||[]).length} rows`) : bad("too few rows");
/undefined|NaN/.test(brd) ? bad("breadth markup contains undefined/NaN") : ok("no undefined/NaN in markup");

console.log(fail.length ? `\n${fail.length} FAILURE(S)\n` : "\nall checks passed\n");
process.exit(fail.length ? 1 : 0);

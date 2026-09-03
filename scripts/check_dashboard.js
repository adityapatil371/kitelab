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
console.log(`  priorities: ${(DATA.priorities || []).join(", ")}`);
console.log(`  hg_tags:   ${(DATA.hg_tags || []).join(", ")}`);
console.log(`  grid cells: ${Object.keys(DATA.grid).length}`);
console.log(`  fills:      ${(DATA.fills || []).map(f => f[0]).join(", ")}`);

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
    const byTable = { compare: "cmp" };
    if (byTable[v]) {
      const n = ((els[byTable[v]] || {})._body || { rows: [] }).rows.length;
      n > 0 ? ok(`${v} rendered ${n} rows`) : bad(`${v} rendered only ${n} rows`);
      continue;
    }
    const body = { assets: "ast-body", stocks: "stk-body" }[v];
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
console.log("\n== signal-priority axis ==");
S.uni = "all";
for (const prio of (DATA.priorities || [DATA.priority_default])) {
  S.prio = prio;
  render();
  const got = rows().filter(r => r.mar !== null && r.mar !== undefined);
  got.length > 0 ? ok(`priority ${prio.padEnd(10)}: ${got.length} rows with a MAR`)
                 : bad(`priority ${prio.padEnd(10)}: NO rows resolved -- axis published but not computed`);
}
S.prio = null; S.year = DATA.start_default;

/* EVERY OFFERED FILL MODE MUST RESOLVE. The payload used to publish all four
   while only "realistic" was gridded, so choosing one of the other three gave a
   table of em dashes -- indistinguishable from a rule that took no trades. */
console.log("\n== fill modes ==");
for (const [f, label] of (DATA.fills || [])) {
  S.fill = f; render();
  const got = rows().filter(r => r.mar !== null && r.mar !== undefined);
  got.length > 0 ? ok(`${label}: ${got.length} rows`)
                 : bad(`${label}: offered but not gridded -- renders blank`);
}
S.fill = DATA.fills[0][0];

/* THE START YEAR MUST BE FREE. Every year is gridded at the default priority;
   only a non-default priority pins it. A year that silently resolves to nothing
   is what makes the selector look broken. */
console.log("\n== start years ==");
for (const y of DATA.start_years) {
  S.year = y; render();
  const got = rows().filter(r => r.mar !== null && r.mar !== undefined);
  got.length > 0 ? ok(`year ${y}: ${got.length} rows`)
                 : bad(`year ${y}: offered but not gridded`);
}
S.year = DATA.start_default;

/* THE NON-EQUITY INSTRUMENTS ARE UNIVERSES NOW, not a page. Each must resolve
   in the grid like any other universe, and each must carry a buy-and-hold
   benchmark -- on one instrument that is the only comparison that means
   anything. A universe published but not gridded renders as blank rows. */
console.log("\n== non-equity instruments as universes ==");
for (const sym of Object.keys(DATA.assets || {})) {
  S.uni = sym; S.year = DATA.start_default; S.prio = null; render();
  /* TWO DIFFERENT THINGS, and conflating them cost a false failure. A cell that
     was never COMPUTED is a broken axis. A cell that exists but holds no trades
     is the account correctly refusing what it cannot afford -- one lot of MCX
     gold is ~Rs1.6 crore of metal at ~Rs9.8 lakh margin, so a Rs2,00,000 account
     takes none, and that is the honest answer rather than a fault. */
  const cells = Object.keys(DATA.grid).filter(k => k.split("|")[2] === sym);
  const got = rows().filter(r => r.mar !== null && r.mar !== undefined);
  const bh = (DATA.assets || {})[sym];
  cells.length > 0 ? ok(`${sym.padEnd(11)} ${cells.length} cells computed, ${got.length} with trades`)
                   : bad(`${sym.padEnd(11)} universe offered but NOT COMPUTED`);
  if (cells.length && got.length === 0)
    console.log(`       none affordable at this scenario -- expected where one lot exceeds the account`);
  bh && bh.bh ? console.log(`       buy & hold ${bh.bh.cagr}%/yr, fee ${bh.fee_pct}%/side`)
              : bad(`${sym}: no buy-and-hold benchmark`);
  /* PRIORITY MUST BE ABSENT HERE, NOT MERELY INERT. One instrument never has
     two signals competing, so every ordering returns the same number. Gridding
     them anyway cost four duplicate cells per real one; offering them on the
     page invited "the ordering does not matter" from a universe that cannot
     ask the question. Only the default may be present. */
  const gridded = new Set(Object.keys(DATA.grid)
    .filter(k => k.split("|")[2] === sym).map(k => k.split("|")[7]));
  gridded.size === 1 && gridded.has(DATA.priority_default)
    ? ok(`${sym.padEnd(11)} one priority only (the rest would be duplicates)`)
    : bad(`${sym}: ${gridded.size} priorities gridded on a single instrument`);
}
S.uni = "all";

/* And the control itself must be gone from the page there. */
console.log("\n== priority control is hidden on single-name universes ==");
for (const sym of (DATA.single_name || [])) {
  S.uni = sym; render();
  const labels = Object.values(els).map(e => e.textContent || "").join(" ");
  labels.includes("Signal priority")
    ? bad(`${sym}: priority control still offered`)
    : ok(`${sym.padEnd(11)} control hidden`);
}
S.uni = "all";


/* THE DETAIL DROPDOWN MUST BE POPULATED AND MUST SWITCH. A select that renders
   with no options, or one whose change does not move the view, looks identical
   to a working one until someone tries it. */
console.log("\n== detail strategy picker ==");
S.view = "detail"; S.uni = "all"; S.sel = null; render();
{
  const pick = els["det-pick"] || { options: [] };
  const n = pick.options.length;
  n > 1 ? ok(`picker offers ${n} strategies`)
        : bad(`picker offers ${n} options`);
  const first = S.sel;
  const other = pick.options.map(o => o.value).find(v => v !== first);
  if (other) {
    S.sel = other; render();
    S.sel === other ? ok(`switching to ${other} holds`)
                    : bad(`switching to ${other} reverted to ${S.sel}`);
    const body = (els["det-body"] || {}).innerHTML || "";
    body.length > 200 ? ok("detail re-rendered for the new pick")
                      : bad("detail body did not re-render");
  } else bad("only one strategy in the picker");
}
S.sel = null; S.view = "compare";

/* NOTHING MAY RENDER AS "undefined" OR "NaN". These do not throw, they print --
   a missing field arrives on the page as the word undefined and reads like data.
   Checked across every view rather than one, which is where it used to live. */
console.log("\n== no undefined/NaN in any view ==");
for (const [v] of VIEWS) {
  S.view = v;
  try { render(); } catch (e) { bad(`${v} threw: ${e.message}`); continue; }
  let bads = 0, where = [];
  for (const [id, el] of Object.entries(els)) {
    const html = el.innerHTML || "";
    const hits = (html.match(/\bundefined\b|\bNaN\b/g) || []).length;
    if (!hits) continue;
    bads += hits;
    const at = html.search(/\bundefined\b|\bNaN\b/);
    where.push(`#${id}: ...${html.slice(Math.max(0, at - 45), at + 25).replace(/\s+/g, " ")}...`);
  }
  bads === 0 ? ok(`${v}: clean`)
             : bad(`${v}: ${bads} undefined/NaN -- ${where[0]}`);
}

console.log(fail.length ? `\n${fail.length} FAILURE(S)\n` : "\nall checks passed\n");
process.exit(fail.length ? 1 : 0);

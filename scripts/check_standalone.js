/* Check the single-file build in output/stock-analysis.html.
 *
 *     node scripts/check_standalone.js [path/to/stock-analysis.html]
 *
 * WHAT THIS IS FOR. scripts/build_standalone.py trims dashboard.json down to
 * the ten keys and four cell fields the article reads, and inlines the result.
 * A trim is exactly the kind of change that looks fine and quietly removes a
 * number: the page would not crash, it would just print one fewer fact, or an
 * em dash, and nobody reading it would know.
 *
 * So the test is not "does the file look right". It is: render the article
 * from the TRIMMED payload and from the FULL dashboard.json, at a laptop width
 * and at a phone width, and require the four outputs to match pairwise byte
 * for byte. If they do, the trim removed nothing the reader sees.
 *
 * It also checks the properties the file is being SENT for:
 *   - it fetches nothing and links to nothing (it must work offline, from a
 *     filesystem, with no server and no network),
 *   - the payload holds only the allowlisted keys, and no equity curves,
 *     no day-by-day series and no symbol-shaped strings.
 *
 * Exits non-zero on any failure.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");

const filePath = process.argv[2] || path.join(ROOT, "output", "stock-analysis.html");
if (!fs.existsSync(filePath)) {
  console.error(`No build at ${filePath}. Make one:  python3 -m scripts.build_standalone`);
  process.exit(2);
}
const jsonPath = [process.env.KITELAB_CLEAN_DIR,
                  path.join(process.env.HOME || "", "data/clean/kitelab"),
                  "/data/clean/kitelab",
                  path.join(ROOT, "data-clean")]
  .filter(Boolean).map(d => path.join(d, "dashboard.json")).find(p => fs.existsSync(p));
if (!jsonPath) {
  console.error("No dashboard.json in any known CLEAN directory.");
  process.exit(2);
}
console.log(`checking ${filePath}\n     against ${jsonPath}`);

const fail = [];
const ok = m => console.log("  ok   " + m);
const bad = m => { fail.push(m); console.log("  FAIL " + m); };
const eq = (label, got, want) =>
  (got === want ? ok(`${label} = ${got}`) : bad(`${label}: got ${got}, want ${want}`));

/* ── a stub DOM, one per render, so the two runs cannot contaminate each
      other through a shared element ─────────────────────────────────────── */
function mk(id) {
  let html = "", tc = "";
  const e = { id, style: {}, addEventListener() {}, setAttribute() {},
              querySelector: () => mk(), getAttribute: () => null };
  Object.defineProperty(e, "textContent", { get: () => tc, set: v => { tc = v == null ? "" : String(v); } });
  Object.defineProperty(e, "innerHTML", { get: () => html, set: v => { html = String(v); } });
  return e;
}

/* Run a page's script at a given viewport width and return what it rendered.
   `payload` non-null means call boot() ourselves (the served page, whose
   bootstrap we stripped); null means the script boots itself (the build). */
function renderAt(js, width, payload, label) {
  const els = {};
  global.document = {
    getElementById: id => els[id] || (els[id] = mk(id)),
    createElement: () => mk(), querySelector: () => mk(),
  };
  global.window = { innerWidth: width, innerHeight: 900, addEventListener() {} };
  global.fetch = async () => { throw new Error("this page must not fetch"); };
  const mod = { exports: {} };
  try {
    new Function("module", "require",
      js + ";module.exports={boot,DATA:typeof DATA!==\"undefined\"?DATA:null};")(mod, require);
    if (payload) mod.exports.boot(payload);
  } catch (err) {
    bad(`${label} at ${width}px threw: ${err.message}`);
    return { out: "", dateline: "", data: null };
  }
  return { out: els["body"] ? els["body"].innerHTML : "",
           dateline: els["dateline"] ? els["dateline"].textContent : "",
           data: mod.exports.DATA };
}

const buildSrc = fs.readFileSync(filePath, "utf8");
const pageSrc = fs.readFileSync(path.join(ROOT, "web", "article.html"), "utf8");
const FULL = JSON.parse(fs.readFileSync(jsonPath, "utf8"));

const buildJs = (buildSrc.match(/<script>\n([\s\S]*)<\/script>/) || [])[1];
if (!buildJs) { console.error("no <script> block in the build"); process.exit(2); }

/* The served page, minus its bootstrap -- spliced on the same markers the
   builder uses, so the two can never disagree about where the seam is. */
let pageJs = (pageSrc.match(/<script>\n([\s\S]*)<\/script>/) || [])[1];
const S = "/* BOOTSTRAP-START */", E = "/* BOOTSTRAP-END */";
if (!pageJs || pageJs.indexOf(S) < 0 || pageJs.indexOf(E) < 0) {
  console.error("web/article.html has no BOOTSTRAP markers to splice on");
  process.exit(2);
}
pageJs = pageJs.slice(0, pageJs.indexOf(S)) + pageJs.slice(pageJs.indexOf(E) + E.length);

/* ── 1. the file is self-contained ────────────────────────────────────── */
console.log("\nself-contained");
/fetch\s*\(/.test(buildJs) ? bad("the build still calls fetch()")
                           : ok("the build never calls fetch()");
/<(script|link|img)[^>]+(src|href)=/i.test(buildSrc)
  ? bad("the build references an external file")
  : ok("no external script, stylesheet or image");
/https?:\/\//.test(buildSrc) ? bad("the build contains an http(s) URL")
                             : ok("no http(s) URL anywhere in the file");
/* The quoted form, not the bare word: the file keeps the comments that
   explain where it came from, and those name the endpoint in prose. */
buildSrc.includes('"/api/dashboard"') ? bad("the build still holds the API path as a string")
                                      : ok("the dashboard API path survives only in comments");

/* ── 2. render four ways and compare ──────────────────────────────────── */
console.log("\nthe trim changed nothing the reader sees");
const WIDE = 1440, PHONE = 380;
const bWide = renderAt(buildJs, WIDE, null, "the build");
const bNarrow = renderAt(buildJs, PHONE, null, "the build");
const pWide = renderAt(pageJs, WIDE, FULL, "the served page");
const pNarrow = renderAt(pageJs, PHONE, FULL, "the served page");

bWide.out.length > 6000 ? ok(`the build renders ${bWide.out.length} characters at ${WIDE}px`)
                        : bad(`the build rendered only ${bWide.out.length} characters`);
bWide.out === pWide.out
  ? ok(`identical to the served page at ${WIDE}px`)
  : bad(`the build differs from the served page at ${WIDE}px (${bWide.out.length} vs ${pWide.out.length} chars)`);
bNarrow.out === pNarrow.out
  ? ok(`identical to the served page at ${PHONE}px`)
  : bad(`the build differs from the served page at ${PHONE}px (${bNarrow.out.length} vs ${pNarrow.out.length} chars)`);
bWide.dateline === pWide.dateline && bWide.dateline.includes(FULL.built)
  ? ok(`dateline matches and quotes the build date`)
  : bad(`dateline differs: build "${bWide.dateline}" vs page "${pWide.dateline}"`);

/* The phone is a different drawing, not the same one scaled -- if these two
   matched, setView() would not be doing anything. */
bWide.out === bNarrow.out
  ? bad("the phone render is identical to the laptop render -- setView() did nothing")
  : ok("the phone render is a different drawing, not the same one scaled");
for (const junk of ["undefined", "NaN", ">null<", "[object Object]", ">—<"]) {
  bNarrow.out.includes(junk) ? bad(`the phone render contains ${junk}`)
                             : ok(`no ${junk} in the phone render`);
}

/* ── 2b. it reads with JavaScript switched OFF ────────────────────────── */
/* This is the property the file is actually sent for. WhatsApp's in-app
   document viewer renders HTML and runs no script, so the charts are baked in
   at build time. A bake is exactly the kind of thing that goes stale silently
   -- the page looks perfect in a browser, because there the script redraws
   over the top -- so it is compared against the live render, both geometries,
   character for character. */
console.log("\nit reads with JavaScript switched off");
const bodyBlock = (buildSrc.match(/<div id="body">([\s\S]*?)<\/div>\s*\n\s*<noscript>/) || [])[1];
if (!bodyBlock) {
  bad("no pre-rendered #body in the file -- it will be blank wherever script is off");
} else {
  /* the seam the builder writes, matched exactly -- an off-by-one here would
     read as a stale bake and send someone rebuilding a file that is fine */
  const HEAD = '<div class="pre-wide">', SEAM = '</div><div class="pre-narrow">';
  const i = bodyBlock.indexOf(HEAD), j = bodyBlock.indexOf(SEAM);
  const preWide = (i < 0 || j < 0) ? null : bodyBlock.slice(i + HEAD.length, j);
  const preNarrow = j < 0 ? null : bodyBlock.slice(j + SEAM.length, bodyBlock.lastIndexOf("</div>"));
  preWide ? ok(`a laptop drawing is baked in (${preWide.length} characters)`)
          : bad("no .pre-wide block");
  preNarrow ? ok(`a phone drawing is baked in (${preNarrow.length} characters)`)
            : bad("no .pre-narrow block");
  /* the narrow copy's ids are suffixed so the document has no duplicate id */
  const expectNarrow = bNarrow.out.replace(/id="(sec|fig)-/g, 'id="$1-n-');
  preWide === bWide.out
    ? ok("the baked laptop drawing is what the script would draw")
    : bad("the baked laptop drawing is STALE -- rebuild: python3 -m scripts.build_standalone");
  preNarrow === expectNarrow
    ? ok("the baked phone drawing is what the script would draw")
    : bad("the baked phone drawing is STALE -- rebuild: python3 -m scripts.build_standalone");
  /(id="(sec|fig)-[a-z]+")[\s\S]*\1/.test(bodyBlock)
    ? bad("the two baked drawings share an id")
    : ok("the two baked drawings carry distinct ids");
}
buildSrc.includes('class="loading"')
  ? bad('"Reading the results..." survives -- that is what a script-less viewer would show')
  : ok("no loading placeholder left to strand a script-less viewer");
/\.pre-narrow\s*\{\s*display:\s*none/.test(buildSrc) && /max-width:\s*700px/.test(buildSrc)
  ? ok("CSS alone picks the geometry, so no script is needed to choose")
  : bad("the .pre-wide / .pre-narrow media query is missing");
const datelineBaked = (buildSrc.match(/id="dateline">([^<]*)</) || [])[1] || "";
datelineBaked.includes(FULL.built)
  ? ok("the dateline is baked, not left blank")
  : bad(`the dateline is not baked: "${datelineBaked}"`);

/* ── 3. what the payload carries, and what it does not ────────────────── */
console.log("\nwhat is in the file");
const D = bWide.data;
if (!D) bad("could not read the inlined payload");
else {
  const ALLOW = ["built", "capitals", "diagnostics", "grid", "priority_default",
                 "risks", "start_default", "strategies", "universes", "validation_summary"];
  const keys = Object.keys(D).sort();
  const extra = keys.filter(k => !ALLOW.includes(k));
  const missing = ALLOW.filter(k => !keys.includes(k));
  extra.length ? bad(`payload carries keys outside the allowlist: ${extra.join(", ")}`)
               : ok(`payload carries only the ${ALLOW.length} allowlisted keys`);
  missing.length ? bad(`payload is missing: ${missing.join(", ")}`)
                 : ok("every allowlisted key is present");
  eq("grid cells", Object.keys(D.grid).length, Object.keys(FULL.grid).length);

  const CELL = new Set(["cagr", "wiped", "maxdd", "taken"]);
  const seen = new Set();
  for (const c of Object.values(D.grid)) if (c) for (const k of Object.keys(c)) seen.add(k);
  const cellExtra = [...seen].filter(k => !CELL.has(k));
  cellExtra.length ? bad(`grid cells carry fields outside the allowlist: ${cellExtra.join(", ")}`)
                   : ok(`grid cells carry only ${[...seen].sort().join(", ")}`);
}
for (const gone of ["curve_index", "daily_excess", "fill_timing", "calendars", "trade_stats"]) {
  buildSrc.includes(`"${gone}"`) ? bad(`the file still contains ${gone}`)
                                 : ok(`no ${gone}`);
}
/* The file is meant to be forwardable, so the disclosure question is settled
   here rather than trusted: nothing in it may look like a stock symbol. */
const payloadBlob = buildJs.slice(buildJs.indexOf("boot({"));
const symbolish = [...new Set((payloadBlob.match(/"[A-Z][A-Z0-9&\-]{2,}"/g) || []))];
symbolish.length ? bad(`symbol-shaped strings in the payload: ${symbolish.slice(0, 10).join(", ")}`)
                 : ok("no symbol-shaped strings in the payload");

const kb = n => (n / 1024).toFixed(0) + " KB";
console.log(`\n  file is ${kb(Buffer.byteLength(buildSrc))}, `
          + `against ${kb(fs.statSync(jsonPath).size)} of payload on the server`);

const total = fail.length;
console.log(`\n${total ? `${total} FAILURE(S)` : "all checks passed"}`);
if (total) { fail.forEach(m => console.log("  - " + m)); process.exit(1); }

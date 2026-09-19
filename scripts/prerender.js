/* Render web/article.html's charts to STATIC HTML, so the page works with
 * JavaScript switched off.
 *
 *     node scripts/prerender.js <article.html> <payload.json> <width>
 *
 * Prints one JSON object to stdout: {"body": "...", "dateline": "..."}.
 * scripts/build_standalone.py calls it twice -- once per geometry -- and
 * inlines both results.
 *
 * WHY. Every chart on the reading page is drawn by script into #body. That is
 * fine in a browser and useless in WhatsApp's in-app document viewer, which
 * renders HTML and does not run JavaScript: the reader gets the opening
 * question and nothing under it. Since the drawing is a pure function of the
 * payload, it can be run once here at build time instead of on every reader's
 * machine -- and then the script, where it does run, simply redraws over the
 * top and adds the tooltips.
 *
 * The DOM below is a stub, not a browser. It does not need to be a browser:
 * render() only ever builds a string and assigns it to one innerHTML.
 */
"use strict";
const fs = require("fs");

const [pagePath, payloadPath, widthArg] = process.argv.slice(2);
if (!pagePath || !payloadPath || !widthArg) {
  console.error("usage: node scripts/prerender.js <article.html> <payload.json> <width>");
  process.exit(2);
}
const width = Number(widthArg);

const src = fs.readFileSync(pagePath, "utf8");
const m = src.match(/<script>\n([\s\S]*?)\n<\/script>/);
if (!m) { console.error("no <script> block in " + pagePath); process.exit(2); }

/* Drop the bootstrap. We call boot() ourselves with the payload we were
   handed, so whatever is between the markers -- a fetch on the served page,
   an inlined blob on a rebuild -- must not also run. */
let js = m[1];
const START = "/* BOOTSTRAP-START */", END = "/* BOOTSTRAP-END */";
if (js.includes(START) && js.includes(END)) {
  js = js.slice(0, js.indexOf(START)) + js.slice(js.indexOf(END) + END.length);
} else {
  const call = js.lastIndexOf("\nboot({");
  if (call === -1) { console.error("no bootstrap to strip"); process.exit(2); }
  js = js.slice(0, call);
}

function mk(id) {
  let html = "", tc = "";
  const e = { id, style: {}, addEventListener() {}, setAttribute() {},
              querySelector: () => mk(), getAttribute: () => null };
  Object.defineProperty(e, "textContent", { get: () => tc, set: v => { tc = v == null ? "" : String(v); } });
  Object.defineProperty(e, "innerHTML", { get: () => html, set: v => { html = String(v); } });
  return e;
}
const els = {};
global.document = {
  getElementById: id => els[id] || (els[id] = mk(id)),
  createElement: () => mk(), querySelector: () => mk(),
  addEventListener() {},
};
global.window = { innerWidth: width, innerHeight: 900, addEventListener() {} };
global.fetch = async () => { throw new Error("prerender must not fetch"); };

const mod = { exports: {} };
new Function("module", "require", js + ";module.exports={boot};")(mod, require);
mod.exports.boot(JSON.parse(fs.readFileSync(payloadPath, "utf8")));

const body = els["body"] ? els["body"].innerHTML : "";
if (!body || body.length < 1000) {
  console.error(`prerender at ${width}px produced ${body.length} bytes -- that is not a page`);
  process.exit(1);
}
process.stdout.write(JSON.stringify({
  body,
  dateline: els["dateline"] ? els["dateline"].textContent : "",
}));

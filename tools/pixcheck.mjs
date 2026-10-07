// A multi-cell light (pixel bar, multi-head) in one of its modes: every cell
// gets its own colour on the wire, and the 3D must colour each cell to match.
//
//   node tools/pixcheck.mjs <src> <key> <mode>
//   node tools/pixcheck.mjs ofl chauvet-dj/colorband-pix.json 36-channel
//
// (src / key: `python tools/brands.py --product "words" --json out.json`.)
// Runs its own server on scratch data; your shows are untouched.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const [src, key, mode] = process.argv.slice(2);
if (!src || !key) { console.error("usage: node tools/pixcheck.mjs <src> <key> [mode]"); process.exit(2); }
let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const exe = process.env.CHROMIUM || (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = +(process.env.PIXCHECK_PORT || 8817), BASE = `http://127.0.0.1:${PORT}/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const srv = spawn(process.env.PYTHON || "python3", ["-m", "app.main"], { cwd: ROOT, stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: mkdtempSync(join(tmpdir(), "jarvis-pix-")), CONSOLE_AUTOSAVE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (code) => { try { srv.kill(); } catch { /* gone */ } process.exit(code); };
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE)).ok) break; } catch { /* not yet */ }
  if (i > 160) { console.error("server did not start"); done(2); }
  await sleep(250);
}
const post = (p, b) => fetch(BASE + p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) }).then((r) => r.json());
const act = (a, params = {}) => post("api/console", { action: a, params }).then((j) => j.result || j);

const inst = await post("api/fixtures/library/install", { src, key });
if (!inst.fixture) { console.error(inst.error || "not installed"); done(2); }
const r = await act("add_heads", { fixture_id: inst.fixture.id, mode, qty: 1 });
if (!r.ok) { console.error(r.error); done(2); }
const rep = await (await fetch(BASE + "api/console/channels?heads=1")).json();
const map = ((rep.heads || [])[0] || {}).channels?.map((c) => c.role) || [];
await act("select_heads", { heads: [1] });
await act("set_intensity", { level: 100 });
const HUES = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 0], [0, 255, 255], [255, 0, 255]];
const reds = Math.max(1, map.filter((x) => x === "red").length);
for (let k = 1; k <= reds; k++) {
  if (reds === 1) { await act("set_colour", { hex: "#ff0000" }); break; }
  const [R, G, B] = HUES[(k - 1) % 6];
  for (const [role, v] of [["red", R], ["green", G], ["blue", B]]) await act("set_attribute", { attribute: role, value: v, cell: k });
}
const b = await chromium.launch({ executablePath: exe, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
await p.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.quality", "fast"); } catch { /* fine */ } });
await p.goto(BASE);
for (let i = 0; i < 40; i++) { if (await p.evaluate(() => window.jarvisStage && window.jarvisStage.fixtures.size)) break; await sleep(500); }
await sleep(3000);
const out = await p.evaluate(() => {
  const i = window.jarvisStage.fixtures.get(1), sk = i.sk, px = sk.pixels;
  const hue = (r, g, b) => (r > 0.5 ? "R" : "") + (g > 0.5 ? "G" : "") + (b > 0.5 ? "B" : "");
  const drawn = px ? Array.from({ length: px.count }, (_, k) => { const a = px.instanceColor.array; return hue(a[k * 3], a[k * 3 + 1], a[k * 3 + 2]); }) : [];
  const fed = (i.cur.cells || []).map((c) => hue(c.r, c.g, c.b));
  return { type: i.data.body.type, cells: i.data.body.cells, fed, drawn, multihead: (sk.cells || []).length };
});
await b.close();
const want = Array.from({ length: reds }, (_, k) => ["R", "G", "B", "RG", "GB", "RB"][k % 6]);
console.log(`${inst.fixture.manufacturer} ${inst.fixture.model} (${mode || "default mode"}): 3D "${out.type}", ${out.cells} cells`);
console.log(`  wire:  ${want.join(" ")}`);
console.log(`  feed:  ${out.fed.join(" ")}`);
console.log(`  drawn: ${out.drawn.join(" ") || (out.multihead ? `${out.multihead} heads, each its own lens` : "-")}`);
const one = map.filter((x) => x === "red").length <= 1;      // one cell: the whole light one colour
const ok = one ? out.drawn.every((d) => d === "R") : out.fed.join() === want.join() && (out.multihead >= reds
  || (out.drawn.length >= reds && out.drawn.every((d, k) => d === out.fed[Math.min(out.fed.length - 1, Math.floor(k * out.fed.length / out.drawn.length))])));
console.log(ok ? "ok: every cell shows its own colour" : "FAIL: the 3D does not show each cell's colour");
done(ok ? 0 : 1);

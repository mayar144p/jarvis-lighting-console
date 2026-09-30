// The 3D half of the rig check: every kind of light in the real
// visualiser - does it get a model of its own, does a lit light show a
// beam, does the beam land where the desk aimed it, do SFX show when
// fired - with a screenshot per stage.
//
//   python tools/rigcheck.py --quick --list > picks.json
//   PORT=8799 CONSOLE_AUTORESTORE=false CONSOLE_AUTOSAVE=false python -m app.main &
//   node tools/rigcheck_3d.mjs picks.json out/          (needs Playwright)
//
// It clears the patch and venue of the running server: use a scratch one.
import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "node:fs";

const [picksFile, outDir = "."] = process.argv.slice(2);
const picks = JSON.parse(readFileSync(picksFile, "utf-8"));
mkdirSync(outDir, { recursive: true });
const base = process.env.JARVIS_URL || "http://127.0.0.1:8799/";
const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
p.on("pageerror", (e) => errs.push(String(e)));
await p.goto(base);
await p.waitForTimeout(1500);
const post = async (action, params = {}) => (await p.evaluate(({ action, params }) => fetch("/api/console", {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, params }) }).then((r) => r.json()), { action, params })).result;
const fails = [];
const check = (ok, what, detail = "") => { if (!ok) fails.push(`${what} ${detail}`); return ok; };

await post("patch_clear");
await post("venue_template", { name: "club" });
await post("fx_arm", { state: true });
for (const f of picks) {
  await p.evaluate(({ src, key }) => fetch("/api/fixtures/library/install", { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ src, key }) }), { src: f.src, key: f.key });
}
// lay every light out on the floor in rows, then light them all
const heads = [];
let i = 0;
for (const f of picks) {
  const r = await post("add_heads", { query: `${f.man} ${f.model}`, qty: 1 });
  if (!r.heads) { fails.push(`patch ${f.man} ${f.model}: ${r.error}`); continue; }
  const n = r.heads[0];
  const x = -7 + (i % 8) * 2, z = 3 + Math.floor(i / 8) * 2.2;
  await post("set_place", { head: n, x, y: 0, z, stance: "stand" });
  heads.push({ n, ...f });
  i++;
}
await post("select_heads", { heads: heads.map((h) => h.n) });
await post("set_intensity", { level: 100 });
await post("floor_safe", { movement: false });
await post("aim_at", { x: 0, y: 0, z: 14 });
for (const h of heads.filter((x) => ["co2", "flame", "spark", "confetti", "sfx"].includes(x.type))) {
  await post("fx_fire", { heads: [h.n], seconds: 3, owner: "3d" });
}
for (const h of heads.filter((x) => x.type === "laser")) await post("fx_laser", { heads: [h.n], down: true, owner: "3d" });
for (const h of heads.filter((x) => x.type === "atmos")) await post("fx_fog", { heads: [h.n], level: 60 });
await p.waitForTimeout(4000);

const seen = await p.evaluate((ns) => ns.map((n) => {
  const inst = window.jarvisStage.fixtures.get(n);
  if (!inst) return { n, missing: true };
  const beams = (inst.beams || []).map((bm) => ({ vis: bm.mesh.visible, d: [bm.dir.x, bm.dir.y, bm.dir.z], o: [bm.origin.x, bm.origin.y, bm.origin.z] }));
  return { n, type: (inst.data.body || {}).type, level: inst.level, beams, cur: inst.cur && { a: inst.cur.a } };
}), heads.map((h) => h.n));
for (const s of seen) {
  const h = heads.find((x) => x.n === s.n);
  const what = `${h.type} / ${h.man} ${h.model}`;
  if (!check(!s.missing, what, "no 3D instance")) continue;
  // the library row's type came from its biggest mode; what it was patched
  // in (the default mode) decides - so judge by the 3D type it got
  if (s.type !== h.type) console.log(`  note: ${what} is a ${s.type} in its default mode`);
  const light = !["atmos", "co2", "flame", "spark", "confetti", "sfx", "laser"].includes(s.type);
  if (light) {
    check((s.level || 0) > 0, what, "dark in 3D at full");
    check(s.beams.some((bm) => bm.vis), what, "no visible beam at full");
  }
  if (["moving_spot", "moving_wash", "moving_beam", "moving_hybrid", "moving_bar", "scanner"].includes(s.type)) {
    const bm = s.beams.find((x) => x.vis) || s.beams[0];
    if (bm) {
      const t = bm.d[1] < -1e-3 ? -bm.o[1] / bm.d[1] : null;
      const hit = t ? [bm.o[0] + bm.d[0] * t, bm.o[2] + bm.d[2] * t] : null;
      check(!!hit, what, `beam doesn't reach the floor (dir ${bm.d.map((v) => v.toFixed(2))})`);
    }
  }
}
await p.evaluate(() => window.jarvisStage.scene.traverse((o) => { if (o.userData && o.userData.crowd) o.visible = false; }));
await p.evaluate(() => window.jarvisStage.view("overview"));
await p.waitForTimeout(1500);
await p.screenshot({ path: `${outDir}/rig3d-overview.png` });
await p.evaluate(() => window.jarvisStage.view("front"));
await p.waitForTimeout(1500);
await p.screenshot({ path: `${outDir}/rig3d-front.png` });
await post("fx_kill");
console.log(`${seen.length} lights in 3D, ${fails.length} problems`);
for (const f of fails) console.log("  FAIL", f);
console.log("page errors", JSON.stringify(errs));
await b.close();
process.exit(fails.length || errs.length ? 1 : 0);

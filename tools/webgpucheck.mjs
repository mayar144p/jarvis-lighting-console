// The 3D on real WebGPU, inside the desktop app (Electron's Chromium):
// it starts on the WebGPU path, draws without errors, and draws the same
// picture as its WebGL 2 fallback (gobos, beams, pools, shadows, effects).
// Runs on scratch data.  On a computer with no graphics card it uses
// SwiftShader (software Vulkan) for WebGPU.
//
//   cd desktop && npm install && cd .. && node tools/webgpucheck.mjs [outDir]
//   (Linux with no screen: xvfb-run -a node tools/webgpucheck.mjs)
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

let _electron, chromium;
try { ({ _electron, chromium } = await import("playwright")); } catch {
  ({ _electron, chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DESK = join(ROOT, "desktop");
const OUT = process.argv[2] || mkdtempSync(join(tmpdir(), "haze-webgpu-"));
const exe = join(DESK, "node_modules", "electron", "dist", process.platform === "win32" ? "electron.exe" : "electron");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + extra)); if (!ok) fails++; };
// a real graphics card when there is one (CHECKS_GPU=1); otherwise SwiftShader
const GPU = process.env.CHECKS_GPU === "1"
  ? ["--enable-unsafe-webgpu"]
  : ["--enable-unsafe-webgpu", "--enable-features=Vulkan", "--use-vulkan=swiftshader", "--use-webgpu-adapter=swiftshader", "--enable-unsafe-swiftshader"];

async function scene(mode) {
  const app = await _electron.launch({ executablePath: exe,
    args: [...(process.platform === "linux" ? ["--no-sandbox"] : []), ...GPU, `--user-data-dir=${mkdtempSync(join(tmpdir(), "u-"))}`, DESK],
    env: { ...process.env, CONSOLE_DATA_DIR: mkdtempSync(join(tmpdir(), "d-")), CONSOLE_AUTOSAVE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
  const win = await app.firstWindow({ timeout: 90000 });
  const errs = [];
  win.on("console", (m) => { if (m.type() === "error") errs.push(m.text().slice(0, 200)); });
  win.on("pageerror", (e) => errs.push(String(e).slice(0, 200)));
  await win.waitForSelector("#app", { timeout: 60000 });
  await win.setViewportSize({ width: 1300, height: 820 }).catch(() => {});
  const act = (a, params = {}) => win.evaluate(async ([a, params]) => (await (await fetch("/api/console", { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ action: a, params }) })).json()).result, [a, params]);
  const install = (src, key) => win.evaluate(async ([src, key]) => (await (await fetch("/api/fixtures/library/install", { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ src, key }) })).json()).fixture.id, [src, key]);
  await win.evaluate((m) => { sessionStorage.setItem("haze.venuepick", "1"); localStorage.setItem("haze.quality", "high"); localStorage.setItem("haze.qualityV", "2"); localStorage.setItem("haze.noLockAsk", "1");
    localStorage.setItem("haze.people", "0"); localStorage.setItem("haze.3d", "on"); if (m === "webgl") localStorage.setItem("haze.renderer", "webgl"); else localStorage.removeItem("haze.renderer"); }, mode);
  await act("venue_template", { name: "club" });
  const spots = (await act("add_heads", { fixture_id: await install("qlc", "Robe/Robe-Pointe.qxf"), qty: 2 })).heads;
  await act("set_place", { head: spots[0], x: -1.5, y: 6, z: 7 });
  await act("set_place", { head: spots[1], x: 1.5, y: 6, z: 7 });
  await act("select_heads", { heads: spots });
  await act("set_intensity", { level: 100 });
  await act("set_colour", { hex: "#4a8cff" });
  await act("set_attribute", { attribute: "gobo", value: 13 });
  await act("set_attribute", { attribute: "zoom", value: 255 });
  await act("clear_selection").catch(() => {});
  await win.reload();
  errs.length = 0;
  for (let i = 0; i < 60; i++) { if (await win.evaluate(() => window.hazeStage && window.hazeStage.ready)) break; await sleep(500); }
  await win.evaluate(() => window.hazeStage.setOptions({ dance: false }));
  await sleep(6000);
  const backend = await win.evaluate(() => window.hazeStage.backend);
  const shot = join(OUT, `scene-${mode}.png`);
  await win.locator("#stage-wrap").screenshot({ path: shot });
  // the light map (its own shader: false colours by lux, contour lines)
  await win.evaluate(() => window.hazeStage.setOptions({ lightMap: true }));
  await sleep(2500);
  const luxShot = join(OUT, `lightmap-${mode}.png`);
  await win.locator("#stage-wrap").screenshot({ path: luxShot });
  // a best-quality film from this canvas (a WebGPU canvas is read differently)
  const film = await win.evaluate(async () => {
    const st = window.hazeStage;
    st.setOptions({ lightMap: false });
    const M = await import("/app/movierender.js");
    const codec = await M.pickCodec(320, 180, 10, 2e6);
    if (!codec) return { none: true };
    const notes = new M.ShowNotes(st);
    await new Promise((ok) => setTimeout(ok, 600));
    notes.stop();
    const out = await M.renderFilm(st, notes, { size: { w: 320, h: 180, bits: 2e6 }, fps: 10, codec });
    const v = document.createElement("video");
    v.muted = true;
    v.src = URL.createObjectURL(out.blob);
    await new Promise((ok, bad) => { v.onloadeddata = ok; v.onerror = () => bad(new Error("the film doesn't play")); });
    await new Promise((ok) => { v.onseeked = ok; v.currentTime = 0.25; });
    // (the picture shown, not just the seek done: read too soon, a WebGPU page's video read black)
    await new Promise((ok) => { if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(() => ok()); setTimeout(ok, 500); });
    const c = document.createElement("canvas");
    c.width = 64; c.height = 36;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(v, 0, 0, 64, 36);
    const d = g.getImageData(0, 0, 64, 36).data;
    let hi = 0, sum = 0;
    const grid = new Array(32).fill(0);                 // 8 x 4 cells: which way up it is
    for (let i = 0; i < d.length; i += 4) {
      const l = d[i] + d[i + 1] + d[i + 2]; sum += l; if (l > hi) hi = l;
      const k = i / 4, x = k % 64, y = Math.floor(k / 64);
      grid[Math.floor(y / 9) * 8 + Math.floor(x / 8)] += l / 3 / 72;
    }
    return { codec: codec.codec, frames: out.frames, mean: sum / (d.length / 4) / 3, hi: hi / 3, filming: !!st.filming, grid };
  }).catch((e) => ({ error: String(e) }));
  // the same rig outdoors at sunset (the sky's own shader: stars, the sun's glow)
  await win.evaluate(() => {
    const st = window.hazeStage;
    st.setOptions({ lightMap: false });
    const v = JSON.parse(JSON.stringify(st.built.venue));
    v.room.ceiling = "none";
    v.room.sky = "dusk";
    // and a projector throwing the rig's colours onto the stage (its own path in the shaders)
    v.objects = [...(v.objects || []), { id: "pjx", kind: "projector", x: 0, y: 3, z: 12, w: 0.5, d: 0.45, h: 0.22, rot: 180,
      content: "rig", lm: 20000, throw: 1.4, aspect: 1.778, tilt: 0 }];
    st.buildVenue(v, []);
  });
  await sleep(2500);
  const skyShot = join(OUT, `sky-${mode}.png`);
  await win.locator("#stage-wrap").screenshot({ path: skyShot });
  await app.evaluate(({ app: a }) => a.quit());
  try { await app.close(); } catch { /* closed */ }
  return { backend, errs, shot, luxShot, skyShot, film };
}

const gpu = await scene("webgpu");
check("the 3D starts on WebGPU", gpu.backend === "webgpu", gpu.backend);
check("...and draws with no errors", !gpu.errs.length, gpu.errs.slice(0, 3).join(" | "));
const gl = await scene("webgl");
check("forced to WebGL 2, the same renderer starts there", gl.backend === "webgl2", gl.backend);
check("...with no errors", !gl.errs.length, gl.errs.slice(0, 3).join(" | "));
// compare the two pictures pixel by pixel
const b = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
const p = await b.newPage();
const compare = (a, c) => p.evaluate(async ([a, c]) => {
  const load = (src) => new Promise((ok) => { const i = new Image(); i.onload = () => ok(i); i.src = src; });
  const px = (img) => { const cv = document.createElement("canvas"); cv.width = img.width; cv.height = img.height;
    const g = cv.getContext("2d"); g.drawImage(img, 0, 0); return g.getImageData(0, 0, cv.width, cv.height).data; };
  const [A, C] = await Promise.all([load(a), load(c)]);
  const x = px(A), y = px(C);
  let diff = 0, bright = 0;
  for (let i = 0; i < x.length; i += 4) {
    diff += Math.abs(x[i] - y[i]) + Math.abs(x[i + 1] - y[i + 1]) + Math.abs(x[i + 2] - y[i + 2]);
    bright += x[i] + x[i + 1] + x[i + 2];
  }
  const n = x.length / 4;
  return { mean: diff / n / 3, bright: bright / n / 3 };
}, [a, c].map((f) => "data:image/png;base64," + readFileSync(f).toString("base64")));
for (const [name, run] of [["WebGPU", gpu], ["WebGL 2", gl]]) {
  const f = run.film || {};
  check(`a best-quality film made from the ${name} canvas: every picture, not blank`, !f.error && !f.none && f.frames >= 5
    && f.hi > 60 && f.mean > 3 && !f.filming, JSON.stringify(f));
}
{
  const a = (gpu.film || {}).grid, b = (gl.film || {}).grid;
  const diff = a && b ? a.reduce((s, v, i) => s + Math.abs(v - b[i]), 0) / a.length : 999;
  check("...the WebGPU film the same picture as the WebGL 2 one (not upside down)", diff < 12, `mean difference ${diff.toFixed(1)}`);
}
const d = await compare(gpu.shot, gl.shot);
const lux = await compare(gpu.luxShot, gl.luxShot);
const sky = await compare(gpu.skyShot, gl.skyShot);
await b.close();
check("WebGPU draws a picture (not blank)", d.bright > 8 && d.bright < 200, d.bright.toFixed(1));
check("WebGPU and WebGL 2 draw the same picture (beams, gobos, pools, shadows)", d.mean < 4, `mean difference ${d.mean.toFixed(2)} / 255`);
check("the light map draws on WebGPU, the same as on WebGL 2", lux.bright > 8 && lux.mean < 4,
  `mean difference ${lux.mean.toFixed(2)} / 255, brightness ${lux.bright.toFixed(1)}`);
check("an outdoor sky (and a projector) draws on WebGPU, the same as on WebGL 2", sky.bright > 8 && sky.mean < 4,
  `mean difference ${sky.mean.toFixed(2)} / 255, brightness ${sky.bright.toFixed(1)}`);
console.log(`pictures in ${OUT}`);
console.log(fails ? `${fails} failed` : "all ok");
process.exit(fails ? 1 : 0);

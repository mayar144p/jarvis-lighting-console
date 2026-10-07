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
const OUT = process.argv[2] || mkdtempSync(join(tmpdir(), "jarvis-webgpu-"));
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
  await win.evaluate((m) => { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.quality", "high");
    localStorage.setItem("jarvis.people", "0"); if (m === "webgl") localStorage.setItem("jarvis.renderer", "webgl"); else localStorage.removeItem("jarvis.renderer"); }, mode);
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
  for (let i = 0; i < 60; i++) { if (await win.evaluate(() => window.jarvisStage && window.jarvisStage.ready)) break; await sleep(500); }
  await win.evaluate(() => window.jarvisStage.setOptions({ dance: false }));
  await sleep(6000);
  const backend = await win.evaluate(() => window.jarvisStage.backend);
  const shot = join(OUT, `scene-${mode}.png`);
  await win.locator("#stage-wrap").screenshot({ path: shot });
  await app.evaluate(({ app: a }) => a.quit());
  try { await app.close(); } catch { /* closed */ }
  return { backend, errs, shot };
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
const d = await p.evaluate(async ([a, c]) => {
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
}, [gpu.shot, gl.shot].map((f) => "data:image/png;base64," + readFileSync(f).toString("base64")));
await b.close();
check("WebGPU draws a picture (not blank)", d.bright > 8 && d.bright < 200, d.bright.toFixed(1));
check("WebGPU and WebGL 2 draw the same picture (beams, gobos, pools, shadows)", d.mean < 4, `mean difference ${d.mean.toFixed(2)} / 255`);
console.log(`pictures in ${OUT}`);
console.log(fails ? `${fails} failed` : "all ok");
process.exit(fails ? 1 : 0);

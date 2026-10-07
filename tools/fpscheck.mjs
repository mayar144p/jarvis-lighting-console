// How smooth the 3D view runs with the test rig (20 real lights, 124 with BIG_RIG=1, tools/
// bigrig.mjs) all lit, moving and changing colour, at each quality:
// High, Medium (auto) and Low.  Frames a second and the slowest frames.
//
//   node tools/fpscheck.mjs                 software drawing (servers, cloud)
//   CHECKS_GPU=1 node tools/fpscheck.mjs    on this computer's graphics card
//
// Runs its own server on scratch data; your shows are untouched.  On a
// graphics card High should hold 30+ fps; software drawing is far slower
// and only good for comparing the qualities with each other.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { RIG } from "./bigrig.mjs";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const exe = process.env.CHROMIUM || (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = +(process.env.FPSCHECK_PORT || 8818), BASE = `http://127.0.0.1:${PORT}/`;
const SECONDS = +(process.env.FPS_SECONDS || 12);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const srv = spawn(process.env.PYTHON || "python3", ["-m", "app.main"], { cwd: ROOT, stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: mkdtempSync(join(tmpdir(), "jarvis-fps-")), CONSOLE_AUTOSAVE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (code) => { try { srv.kill(); } catch { /* gone */ } process.exit(code); };
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE)).ok) break; } catch { /* not yet */ }
  if (i > 160) { console.error("server did not start"); done(2); }
  await sleep(250);
}
const post = (p, b) => fetch(BASE + p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) }).then((r) => r.json());
const act = (a, params = {}) => post("api/console", { action: a, params }).then((j) => j.result || j);

await act("venue_template", { name: "club" });
let n = 0;
for (const [src, key, query, qty] of RIG) {
  if (src !== "jarvis") await post("api/fixtures/library/install", { src, key });
  const r = await act("add_heads", { query, qty });
  if (r.ok) n += r.heads.length;
}
await act("select_all");
await act("set_intensity", { level: 100 });
await act("run_fx", { name: "rainbow" });
await act("run_fx", { name: "circle" });
console.log(`${n} lights, all lit, rainbow + circle, ${SECONDS} s per quality`);

const GPU = process.env.CHECKS_GPU === "1";
const b = await chromium.launch({ executablePath: GPU ? undefined : exe, headless: !GPU,
  args: GPU ? ["--ignore-gpu-blocklist"] : ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const rows = [];
const ONLY = (process.env.FPS_ONLY || "").toLowerCase();       // e.g. FPS_ONLY=low
for (const [label, q] of [["High", "high"], ["Medium", "auto"], ["Low", "fast"]].filter(([l]) => !ONLY || l.toLowerCase() === ONLY)) {
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  await p.addInitScript((quality) => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.quality", quality); } catch { /* fine */ } }, q);
  await p.goto(BASE + "?window=stage");                // the 3D on its own, as in Show mode
  for (let i = 0; i < 60; i++) { if (await p.evaluate(() => window.jarvisStage && window.jarvisStage.fixtures.size > 100)) break; await sleep(500); }
  await sleep(4000);                                    // models and gobos loaded
  // FPS_SETUP: options to try, e.g. FPS_SETUP='{"haze":0}' (stage.setOptions)
  if (process.env.FPS_SETUP) { await p.evaluate((o) => window.jarvisStage.setOptions(JSON.parse(o)), process.env.FPS_SETUP); await sleep(1500); }
  const r = await p.evaluate(async (secs) => {
    const st = window.jarvisStage, gaps = [];
    let last = st.lastRender;
    const t0 = performance.now();
    await new Promise((res) => {
      const tick = () => {
        if (st.lastRender !== last) { if (last) gaps.push(st.lastRender - last); last = st.lastRender; }
        if (performance.now() - t0 < secs * 1000) requestAnimationFrame(tick); else res();
      };
      requestAnimationFrame(tick);
    });
    gaps.sort((a, b2) => a - b2);
    const avg = gaps.reduce((a, b2) => a + b2, 0) / Math.max(1, gaps.length);
    return { frames: gaps.length, fps: gaps.length ? 1000 / avg : 0, p95: gaps[Math.floor(gaps.length * 0.95)] || 0, ratio: st.q && st.q.ratio };
  }, SECONDS);
  rows.push([label, r]);
  console.log(`  ${label.padEnd(6)} ${r.fps.toFixed(1).padStart(5)} fps   slowest 5%: ${Math.round(r.p95)} ms a frame   (resolution ${Math.round((r.ratio || 1) * 100)}%)`);
  await p.close();
}
await b.close();
const order = rows.map(([, r]) => r.fps);
const sane = order.every((f) => f > 0) && (order.length < 3 || order[2] >= order[0] * 0.9);
console.log(sane ? "ok: every quality draws, and Low is at least as smooth as High" : "CHECK: a quality drew nothing, or Low is slower than High");
done(sane ? 0 : 1);

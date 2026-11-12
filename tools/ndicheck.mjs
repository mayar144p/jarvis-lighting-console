// NDI video in and out, checked without NDI on the machine:
//  1. the desktop app's side (desktop/ndi.js) against a stand-in NDI runtime
//     (tools/ndifake/fakendi.c, built here with the real headers' struct
//     layouts): sources found, frames in (rows unpadded, nothing leaked),
//     frames out (size, kind, every byte);
//  2. the desk's pages in a real browser with a stand-in for the desktop
//     app's NDI link: an LED screen in the 3D shows a source, Paint the rig
//     takes its frames to the lights, Settings sends the 3D out.
// What it can't check: a real NDI runtime and a real network source.
//
//   node tools/ndicheck.mjs        (CI runs it with the screen check)
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + String(extra).slice(0, 300))); if (!ok) fails++; };

// ------------------------------------------------- 1. the desktop app's side
console.log("The desktop app's NDI (a stand-in runtime)");
const TMP = mkdtempSync(join(tmpdir(), "haze-ndi-"));
const ext = process.platform === "win32" ? "dll" : process.platform === "darwin" ? "dylib" : "so";
const LIB = join(TMP, `libfakendi.${ext}`);
const cc = spawnSync(process.env.CC || "cc", ["-shared", "-fPIC", "-O1", "-o", LIB, join(ROOT, "tools", "ndifake", "fakendi.c")], { encoding: "utf8" });
const require = createRequire(join(ROOT, "desktop", "ndi.js"));
let koffi = null;
try { koffi = require("koffi"); } catch { /* not installed */ }
if (cc.status !== 0 || !koffi) {
  console.log(`  skip (${cc.status !== 0 ? "no C compiler here" : "koffi isn't installed: cd desktop && npm install"})`);
  if (process.env.CI) check("the stand-in runtime and koffi are there in CI", false, cc.stderr || "no koffi");
} else {
  process.env.HAZE_NDI_LIB = LIB;
  const ndi = require(join(ROOT, "desktop", "ndi.js"));
  const fake = koffi.load(LIB);
  const g = (sig) => fake.func(sig)();
  const st = ndi.status();
  check("the runtime loads (found by its path), and says which it is", st.ok && /stand-in/.test(st.version), JSON.stringify(st));
  const srcs = ndi.sources();
  check("the sources on the network, by name and address", srcs.length === 2 && srcs[0].name === "TEST (Red)" && /:5961$/.test(srcs[0].url), JSON.stringify(srcs));
  const wc = { isDestroyed: () => false };
  const got = [];
  check("watching a source opens it", ndi.watch("TEST (Red)", wc, (f) => got.push(f)).ok);
  await sleep(400);
  const f = got[got.length - 1];
  check("frames come in (RGBA, the lower NDI size, the colour the source sends)",
    got.length >= 5 && f.w === 64 && f.h === 36 && f.data.length === 64 * 36 * 4 && f.data[0] === 255 && f.data[1] === 0 && f.data[3] === 255,
    JSON.stringify({ n: got.length, w: f && f.w, h: f && f.h, len: f && f.data.length }));
  check("...rows packed (the source's row padding doesn't show)", f && [...f.data.subarray(64 * 4 - 4, 64 * 4 + 4)].join() === "255,0,0,255,255,0,0,255",
    f && [...f.data.subarray(64 * 4 - 4, 64 * 4 + 4)].join());
  check("...asked for RGBA at the lower size", g("int fake_color()") === 2 && g("int fake_bandwidth()") === 0, "");
  const wc2 = { isDestroyed: () => false };
  ndi.watch("TEST (Red)", wc2, () => {});
  check("two screens on one source share one receiver", g("int fake_recvs()") === 1, g("int fake_recvs()"));
  ndi.unwatch("TEST (Red)", wc);
  check("...one letting go keeps it for the other", g("int fake_recvs()") === 1);
  ndi.unwatchAll(wc2);
  check("...the last one letting go stops it, every frame handed back", g("int fake_recvs()") === 0 && g("int fake_outstanding()") === 0,
    `recvs ${g("int fake_recvs()")} frames held ${g("int fake_outstanding()")}`);
  check("out: the 3D as a source by its name", ndi.outStart("Haze 3D").ok && g("const char *fake_sent_name()") === "Haze 3D");
  const W = 320, H = 180, px = new Uint8Array(W * H * 4);
  for (let i = 0; i < px.length; i += 4) { px[i] = (i / 4) % 251; px[i + 1] = 7; px[i + 2] = 200; px[i + 3] = 255; }
  let sum = 0;
  for (let i = 0; i < px.length; i++) sum = (Math.imul(sum, 31) + px[i]) >>> 0;
  check("...a frame goes out whole (size, RGBA, every byte, the library times it)", ndi.outFrame(W, H, px, 30)
    && g("int fake_sent_w()") === W && g("int fake_sent_h()") === H && g("int fake_sent_stride()") === W * 4
    && g("uint32_t fake_sent_fourcc()") === 0x41424752 && g("uint32_t fake_sent_sum()") === sum
    && g("int64_t fake_sent_tc()") === 2n ** 63n - 1n, `sum ${g("uint32_t fake_sent_sum()")} vs ${sum}`);
  check("...a frame too small for its size is refused", !ndi.outFrame(W, H, new Uint8Array(100), 30));
  const n0 = g("int fake_sent()");
  await sleep(1300);
  check("...with nothing new (the 3D resting) the last frame goes again, so the source never goes quiet", g("int fake_sent()") > n0, g("int fake_sent()"));
  check("...and it says who's watching", ndi.status().out.watching === 1);
  ndi.shutdown();
  check("closing the app stops it all", !ndi.status().out);
  // without a runtime: a plain reason, nothing breaks
  const r2 = spawnSync(process.execPath, ["-e", `process.env.NDI_RUNTIME_DIR_V6='/nowhere';const n=require(${JSON.stringify(join(ROOT, "desktop", "ndi.js"))});
    const s=n.status();console.log(JSON.stringify({s,src:n.sources(),w:n.watch('x',{isDestroyed:()=>false},()=>{}),o:n.outStart('y')}))`],
  { encoding: "utf8", env: { ...process.env, HAZE_NDI_LIB: "/nowhere/libndi.so", NODE_PATH: [join(ROOT, "desktop", "node_modules"), process.env.NODE_PATH].filter(Boolean).join(process.platform === "win32" ? ";" : ":") } });
  let no = {};
  try { no = JSON.parse(r2.stdout.trim().split("\n").pop()); } catch { no = { err: r2.stderr }; }
  check("no NDI on the computer: it says so plainly, and nothing breaks",
    no.s && !no.s.ok && /isn't installed/.test(no.s.why) && no.src.length === 0 && !no.w.ok && !no.o.ok, JSON.stringify(no));
}

// ---------------------------------------------------------- 2. the pages
console.log("The desk's pages (a stand-in for the desktop app's NDI link)");
let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.NDI_PORT || 8874), BASE = `http://127.0.0.1:${PORT}/`;
try { await fetch(BASE); console.log(`something is already on port ${PORT} - stop it or set NDI_PORT`); process.exit(2); } catch { /* free */ }
const DATA = mkdtempSync(join(tmpdir(), "haze-ndi-desk-"));
const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore", cwd: ROOT,
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: DATA, CONSOLE_AUTOSAVE: "false", CONSOLE_AUTORESTORE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
process.on("uncaughtException", (e) => { console.log("  FAIL", e.message.split("\n")[0]); done(1); });
process.on("unhandledRejection", (e) => { console.log("  FAIL", String(e && e.message || e).split("\n")[0]); done(1); });
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const act = (a, params = {}) => fetch(BASE + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: a, params }) }).then((r) => r.json()).then((j) => j.result || j);

await act("venue_template", { name: "club" });
await act("add_heads", { query: "LED PAR", qty: 4 });
const scr = await act("venue_add", { item: { kind: "screen", x: 0, z: 1, w: 4, h: 2.25, d: 0.1, y: 2, content: "ndi:TEST (Red)" } });
check("the room keeps a screen showing an NDI source", scr.item && scr.item.content === "ndi:TEST (Red)", JSON.stringify(scr).slice(0, 200));

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const errs = [];
// the desktop app's link (preload.js), standing in: sources, frames, the out
await ctx.addInitScript(() => {
  const T = window.__ndi = { watched: [], unwatched: [], frames: 0, out: [], setOut: [], canSend: null, fns: [], outFns: [] };
  const colour = (name) => (/Red/.test(name) ? [255, 0, 0] : [0, 255, 0]);
  setInterval(() => {
    for (const name of new Set(T.watched.filter((n) => !T.unwatched.includes(n)))) {
      const w = 64, h = 36, d = new Uint8Array(w * h * 4), [r, g, b] = colour(name);
      for (let i = 0; i < d.length; i += 4) { d[i] = r; d[i + 1] = g; d[i + 2] = b; d[i + 3] = 255; }
      T.frames++;
      for (const fn of T.fns) fn({ name, w, h, data: d });
    }
  }, 50);
  window.hazeDesk = {
    ready() {}, onAsk() {}, answer() {},
    ndi: {
      status: async () => ({ ok: true, why: "", version: "NDI stand-in", out: null, inputs: [], settings: { on: false, name: "Haze 3D", size: "1280x720", fps: 30 } }),
      sources: async () => [{ name: "TEST (Red)", url: "127.0.0.1:5961" }, { name: "TEST (Green)", url: "127.0.0.1:5962" }],
      watch: (n) => { T.watched.push(n); T.unwatched = T.unwatched.filter((x) => x !== n); },
      unwatch: (n) => T.unwatched.push(n),
      onFrame: (fn) => T.fns.push(fn),
      setOut: async (cfg) => { T.setOut.push(cfg); return { ok: true, settings: cfg }; },
      onOut: (fn) => T.outFns.push(fn),
      canSend: (yes) => { T.canSend = yes; },
      frame: (w, h, data, fps) => {
        let lit = 0, red = 0;
        for (let i = 0; i < data.length; i += 4) {
          if (data[i] + data[i + 1] + data[i + 2] > 30) lit++;
          if (data[i] > 150 && data[i + 1] < 60 && data[i + 2] < 60) red++;
        }
        T.out.push({ w, h, len: data.length, fps, px: [data[0], data[1], data[2], data[3]], lit, red });
      },
    },
  };
});
await ctx.addInitScript(() => { try { sessionStorage.setItem("haze.venuepick", "1"); localStorage.setItem("haze.noLockAsk", "1"); } catch (e) { /* fine */ } });
// the 3D window (its own page, as the desktop app opens it)
const p3 = await ctx.newPage();
p3.on("pageerror", (e) => errs.push("3D: " + e.message));
await p3.goto(BASE + "?window=stage");
await sleep(6000);
const has3d = await p3.evaluate(() => !!window.hazeStage);
if (!has3d) {
  console.log("  skip the 3D checks (no 3D view in this browser)");
} else {
  // the 3D as it is on screen, resting (nothing nudged): the source's red
  // reaches the picture only if each new frame wakes it
  await sleep(1500);
  const shot = (await p3.screenshot()).toString("base64");
  const seen = await p3.evaluate(async (b64) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    const x = c.getContext("2d");
    x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, c.width, c.height).data;
    let red = 0;
    for (let i = 0; i < d.length; i += 4 * 61) if (d[i] > 150 && d[i + 1] < 60 && d[i + 2] < 60) red++;
    return red;
  }, shot);
  check("...it's on the screen in the 3D, live, while the room rests (each new frame wakes the 3D)", seen > 50, `red samples ${seen}`);
  const s = await p3.evaluate(() => {
    const st = window.hazeStage, list = (st.built && st.built.screens) || [];
    const m = list.find((x) => x.material.userData.ndi);
    const cv = m && m.material.map && m.material.map.image;
    const px = cv && cv.getContext ? [...cv.getContext("2d").getImageData(2, 2, 1, 1).data] : null;
    return { screens: list.length, watched: window.__ndi.watched, frames: m ? m.material.userData.ndi.frames : -1, px, w: cv && cv.width };
  });
  check("the 3D's screen watches its source", s.watched.includes("TEST (Red)"), JSON.stringify(s));
  check("...and shows its frames (the screen's picture is the source's)", s.frames > 3 && s.w === 64 && s.px && s.px[0] === 255 && s.px[1] === 0, JSON.stringify(s));
  // the room changed to a clip: the NDI source is let go
  // what the 3D draws: the screen red in the picture (seen through the 3D out)
  await p3.evaluate(() => { for (const fn of window.__ndi.outFns) fn({ on: true, name: "Haze 3D", w: 320, h: 180, fps: 30, sending: true }); });
  await sleep(1500);
  const drawn = await p3.evaluate(() => window.__ndi.out.slice(-1)[0] || null);
  check("...and in the 3D that goes out as NDI", drawn && drawn.red > 500, JSON.stringify(drawn));
  await p3.evaluate(() => { for (const fn of window.__ndi.outFns) fn({ on: false, sending: false }); window.__ndi.out = []; });
  if (process.env.NDI_SHOT) {
    await p3.evaluate(() => { const st = window.hazeStage; st.dirty = true; });
    await sleep(500);
    await p3.screenshot({ path: process.env.NDI_SHOT });
  }
  await act("venue_update", { id: scr.item.id, changes: { content: "rig" } });
  await sleep(2500);
  const let_go = await p3.evaluate(() => window.__ndi.unwatched.includes("TEST (Red)"));
  check("...changed to something else, the source is let go", let_go);
  check("a 3D view on screen offers to send the 3D out", await p3.evaluate(() => window.__ndi.canSend === true));
}

// the desk's main window: Paint the rig, Settings
const p = await ctx.newPage();
p.on("pageerror", (e) => errs.push(e.message));
await p.goto(BASE);
await sleep(4000);

// Paint the rig: an NDI source over the lights
const m = await act("media_save", { kind: "ndi", source: "TEST (Green)" });
check("Paint the rig keeps an NDI source with the show, by name", m.id && (m.media || []).some((x) => x.kind === "ndi" && x.source === "TEST (Green)"), JSON.stringify(m).slice(0, 200));
const noSrc = await act("media_save", { kind: "ndi" });
check("...an NDI source needs its name", !!noSrc.error || noSrc.ok === false, JSON.stringify(noSrc).slice(0, 200));
await p.evaluate(() => import("/app/pixpanel.js").then((x) => x.renderPix(true)));
await sleep(600);
const clicked = await p.evaluate(() => {
  const b = [...document.querySelectorAll("#pix-pane button")].find((x) => /Use here/.test(x.textContent));
  if (b) b.click();
  return !!b;
});
check("...its row has ▶ Use here", clicked);
await act("select_heads", { heads: [1, 2, 3, 4] });
await act("set_intensity", { level: 100 });
await act("run_media", { id: m.id, view: "front" });
await sleep(1500);
const snap = await fetch(BASE + "api/console").then((r) => r.json());
const row = ((snap.snapshot || snap).media || []).find((x) => x.id === m.id);
check("...its frames reach the desk (it reads as live)", row && row.playing, JSON.stringify(row));
const rep = await fetch(BASE + "api/console/channels?heads=1").then((r) => r.json()).catch(() => ({}));
const ch = ((rep.heads || [])[0] || {}).channels || [];
const val = (r) => (ch.find((c) => c.role === r) || {}).value;
check("...and the lights take its colour (green)", val("green") > 200 && (val("red") || 0) < 40, JSON.stringify(ch.map((c) => [c.role, c.value])));

// Settings -> Video (NDI): the 3D out
await p.evaluate(() => import("/app/settings.js").then((x) => x.openSettingsAt("video")));
await sleep(1200);
const box = await p.evaluate(() => {
  const lab = [...document.querySelectorAll("label.check")].find((l) => /Send the 3D view as an NDI source/.test(l.textContent));
  if (lab) lab.querySelector("input").click();
  return { found: !!lab, text: (document.querySelector(".ndi-panel") || {}).textContent || "" };
});
check("Settings has Video (NDI): the 3D out, and the sources on the network", box.found && /TEST \(Red\)/.test(box.text), box.text.slice(0, 300));
await sleep(400);
check("...ticking it turns the out on", await p.evaluate(() => window.__ndi.setOut.some((c) => c.on === true)));
if (has3d) {
  await p3.evaluate(() => { for (const fn of window.__ndi.outFns) fn({ on: true, name: "Haze 3D", w: 320, h: 180, fps: 30, sending: true }); });
  await p3.evaluate(() => window.hazeStage.frame && window.hazeStage.frame());
  await sleep(1500);
  const out = await p3.evaluate(() => window.__ndi.out.slice(-3));
  check("...the 3D's frames go out at the size asked (the room in them, not a blank)", out.length > 0 && out.every((f) => f.w === 320 && f.h === 180 && f.len === 320 * 180 * 4 && f.px[3] === 255 && f.lit > 2000), JSON.stringify(out));
  await p3.evaluate(() => { for (const fn of window.__ndi.outFns) fn({ on: true, sending: false, w: 320, h: 180, fps: 30 }); window.__ndi.out = []; });
  await p3.evaluate(() => window.hazeStage.frame && window.hazeStage.frame());
  await sleep(800);
  check("...and only from the window the app picked", await p3.evaluate(() => window.__ndi.out.length === 0));
}
check("no page errors", !errs.length, errs.join(" | "));
await browser.close();
console.log(fails ? `${fails} failed` : "all ok");
done(fails ? 1 : 0);

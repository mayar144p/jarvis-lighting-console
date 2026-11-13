// What the 3D view DRAWS, measured in the picture itself.  Small fixed
// scenes - one light over a floor, a person in a beam, a gobo, colours, a
// strobe, a big rig - are handed straight to the real 3D code (web/js/stage)
// in a real browser, drawn, and read back pixel by pixel:
//
//   * a lit light makes a pool where it points, and nothing when it is dark
//   * in haze its beam shows in the air; with no haze it doesn't
//   * a gobo breaks the pool up; an open beam is smooth
//   * a red, green or blue light lights the floor in that colour
//   * every quality (Low, Medium, High) draws
//
// It also saves a "look book" - the same club scene from the house, the
// side and the dance floor - so a change to the 3D can be judged by eye,
// before and after (stagecheck/*.png).
//
//   node tools/stagecheck.mjs [outDir]          software drawing (CI)
//   CHECKS_GPU=1 node tools/stagecheck.mjs      this computer's graphics card
//   node tools/stagecheck.mjs --only gobo       the checks whose name matches
//
// Runs its own server on scratch data (only for the page's files): the
// scenes go to the 3D directly, so no show, patch or light feed is involved.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const argv = process.argv.slice(2);
const ONLY = argv.includes("--only") ? new RegExp(argv[argv.indexOf("--only") + 1], "i") : null;
const OUT = argv.find((a, i) => !a.startsWith("--") && argv[i - 1] !== "--only") || join(ROOT, "stagecheck");
mkdirSync(OUT, { recursive: true });
const PORT = +(process.env.STAGECHECK_PORT || 8877), BASE = `http://127.0.0.1:${PORT}/`;
const GPU = process.env.CHECKS_GPU === "1";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => {
  console.log((ok ? "  ok   " : "  FAIL ") + name + (extra ? "  " + String(extra).slice(0, 400) : ""));
  if (!ok) fails++;
};

try { await fetch(BASE); console.log(`something is already on port ${PORT} - stop it or set STAGECHECK_PORT`); process.exit(2); } catch { /* free */ }
const DATA = mkdtempSync(join(tmpdir(), "haze-stage-"));
const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { cwd: ROOT, stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: DATA, CONSOLE_AUTOSAVE: "false", CONSOLE_AUTORESTORE: "false",
    DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false", HAZE_NAME_ON_NETWORK: "false" } });
const done = (c) => { try { srv.kill(); } catch { /* gone */ } process.exit(c); };
process.on("uncaughtException", (e) => { console.log("  FAIL", e.stack || e.message); done(1); });
process.on("unhandledRejection", (e) => { console.log("  FAIL", (e && e.stack) || String(e)); done(1); });
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE)).ok) break; } catch { /* not yet */ }
  if (i > 200) { console.log("the desk didn't start"); done(2); }
  await sleep(250);
}

// ---------------------------------------------------------------- the page
// The 3D on its own: the desk's import map, a box the size of a small
// window, and helpers the checks call (window.SC).
const W = 480, H = 300;
const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<script type="importmap">{ "imports": { "three": "/vendor/three/three.webgpu.js", "three/webgpu": "/vendor/three/three.webgpu.js",
  "three/tsl": "/vendor/three/three.tsl.js", "three/addons/": "/vendor/three/addons/" } }</script>
<style>html,body{margin:0;background:#000} #stage{position:relative;width:${W}px;height:${H}px;overflow:hidden}</style>
</head><body><div id="stage"></div>
<script type="module">
import * as THREE from "three";
import { Stage } from "/js/stage/stage.js";
import * as look from "/js/stage/look.js";
import { LIGHTS } from "/js/stage/materials.js";
const el = document.getElementById("stage");
let frames = 0;
const stage = new Stage(el, { onFrame: () => { frames++; } });
let img = null;                       // the last picture read back
window.stage = stage;
window.SC = {
  THREE, look, LIGHTS,
  frames: () => frames,
  ready: () => !!stage.ready || !!stage.failed,
  /** wait until the 3D has drawn n more pictures */
  async settle(n = 3, ms = 30000) {
    const want = frames + n, end = performance.now() + ms;
    while (frames < want && performance.now() < end) { stage.dirty = true; await new Promise((r) => setTimeout(r, 30)); }
    return frames >= want;
  },
  /** a plain room: w wide (x), d deep (z from 0), h high */
  room(w = 10, d = 12, h = 5, more = {}) {
    return { version: 2, name: "check", room: { width: w, depth: d, height: h, back: 0, cx: 0, ceiling: "flat",
      floor: more.floor || "concrete", wall_colour: more.wall || "#3a3d45", outline: [] },
      stage: more.stage || null, rigging: more.rigging || [], objects: more.objects || [], zones: more.zones || [],
      cameras: [], crowd: { style: "simple", density: 0.5, show: true }, underlay: null };
  },
  /** a light's description, as the desk sends it (app/fixture_kind.py) */
  body(type, beam, more = {}) {
    return { type, label: type, moving: /^moving/.test(type), class: "light", brand: "generic", brand_name: "",
      style: { body: "#1b1c20", accent: "#9aa4b2", finish: "matte" }, cells: 1, beam: { min: beam, max: beam },
      features: [], ...more };
  },
  /** build a scene: the room, the lights, their looks, the options and the camera */
  async scene({ venue, fixtures, looks = {}, options = {}, camera }) {
    stage.setOptions({ people: true, labels: false, ...options });
    stage.setRig({ fixtures, venue });
    stage.setLooks(looks, 0);
    if (camera) stage.setCamera(camera);
    await this.settle(4);
  },
  setLooks(looks) { stage.setLooks(looks, 0); },
  /** where a point in the room lands in the picture, in pixels */
  at(x, y, z) {
    const v = new THREE.Vector3(x, y, z).project(stage.camera);
    return [Math.round((v.x + 1) / 2 * ${W}), Math.round((1 - v.y) / 2 * ${H})];
  },
  /** draw the view again and read it back; returns a PNG data URL */
  async grab() {
    await this.settle(2);
    const url = stage.photo(${Math.max(W, H)});
    const im = new Image();
    im.src = url;
    await im.decode();
    const c = document.createElement("canvas");
    c.width = im.width; c.height = im.height;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(im, 0, 0);
    img = { w: c.width, h: c.height, data: ctx.getImageData(0, 0, c.width, c.height).data, kx: c.width / ${W}, ky: c.height / ${H} };
    return url;
  },
  /** mean [r, g, b] (0..255) of the last picture round pixel (px, py) */
  rgb(px, py, r = 2) {
    const s = [0, 0, 0];
    let n = 0;
    const cx = Math.round(px * img.kx), cy = Math.round(py * img.ky);
    for (let y = cy - r; y <= cy + r; y++) for (let x = cx - r; x <= cx + r; x++) {
      if (x < 0 || y < 0 || x >= img.w || y >= img.h) continue;
      const i = (y * img.w + x) * 4;
      s[0] += img.data[i]; s[1] += img.data[i + 1]; s[2] += img.data[i + 2]; n++;
    }
    return s.map((v) => v / Math.max(1, n));
  },
  lum(px, py, r = 2) { const [a, b, c] = this.rgb(px, py, r); return 0.2126 * a + 0.7152 * b + 0.0722 * c; },
  /** brightness along a line in the picture (n samples) */
  line(p0, p1, n = 32, r = 1) {
    const out = [];
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1);
      out.push(this.lum(p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t, r));
    }
    return out;
  },
  /** how even the light is on the floor round (x, z): brightness on a grid
   *  of points within r metres - its mean and spread */
  floorSpread(x, z, r, y = 0.002) {
    const v = [];
    for (let i = -6; i <= 6; i++) for (let j = -6; j <= 6; j++) {
      if (i * i + j * j > 36) continue;
      const [px, py] = this.at(x + i * r / 6, y, z + j * r / 6);
      v.push(this.lum(px, py, 0));
    }
    const mean = v.reduce((a, b) => a + b, 0) / v.length;
    return { mean, std: Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length) };
  },
  /** any pixel that isn't a number, or a picture that is all one colour */
  sane() {
    let lo = 255, hi = 0;
    for (let i = 0; i < img.data.length; i += 4) {
      const v = img.data[i] + img.data[i + 1] + img.data[i + 2];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    return { lo, hi };
  },
};
window.SC_READY = true;
</script></body></html>`;

const browser = await chromium.launch({
  ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}),
  headless: !GPU,
  args: GPU ? ["--ignore-gpu-blocklist"] : ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});
const page = await browser.newPage({ viewport: { width: W + 20, height: H + 20 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.route(BASE + "__stagecheck.html", (route) => route.fulfill({ status: 200, contentType: "text/html", body: PAGE }));
await page.goto(BASE + "__stagecheck.html");
await page.waitForFunction(() => window.SC_READY && window.SC.ready(), null, { timeout: 60000 });
const backend = await page.evaluate(() => window.stage.failed ? "failed" : window.stage.backend);
console.log(`The 3D view, drawn by ${backend}${GPU ? " on the graphics card" : " in software"}`);
if (backend === "failed") { check("the 3D starts", false, errors.join(" | ")); done(1); }

const sc = (fn, arg) => page.evaluate(fn, arg);
// draw the view again and read it back (every measurement reads this picture)
let lastShot = "";
const shot = async () => { lastShot = await sc(() => window.SC.grab()); return lastShot; };
// keep the picture just read back, for the person judging the look
const save = (name) => writeFileSync(join(OUT, name + ".png"), Buffer.from(lastShot.split(",")[1], "base64"));

// ------------------------------------------------------------ the scenes
// A downlight: one light hung 4.5 m up over the middle of a 10 x 12 m room,
// pointing straight down; the camera at the back of the room, looking at it.
const CAM = { pos: [0, 5.2, 15.5], target: [0, 1.2, 6] };
async function downlight({ type = "par", beam = 25, look = {}, options = {}, objects = [], body = {} } = {}) {
  await sc(async ({ type, beam, look, options, objects, body, CAM }) => {
    const S = window.SC;
    await S.scene({
      venue: S.room(10, 12, 5, { objects }),
      fixtures: [{ head_no: 1, model: "Check light", manufacturer: "", body: S.body(type, beam, body), x: 0, y: 4.5, z: 6,
        stance: "hang", rot: S.body(type, beam, body).moving ? null : [0, -90], kind: "truss" }],
      looks: { 1: { hex: "#ffffff", a: 1, ...(S.body(type, beam, body).moving ? { pan: 0.5, tilt: 0.5 } : {}), ...look } },
      options: { haze: 0.6, quality: "high", house: 0.35, people: true, ...options },
      camera: CAM,
    });
  }, { type, beam, look, options, objects, body, CAM });
  await shot();
}
const at = (x, y, z) => sc(([x, y, z]) => window.SC.at(x, y, z), [x, y, z]);
const lum = (p, r = 2) => sc(([p, r]) => window.SC.lum(p[0], p[1], r), [p, r]);
const rgb = (p, r = 2) => sc(([p, r]) => window.SC.rgb(p[0], p[1], r), [p, r]);
const selected = (name) => !ONLY || ONLY.test(name);

const CHECKS = [];
const add = (name, run) => CHECKS.push({ name, run });

add("draws", async () => {
  await downlight();
  save("01-downlight");
  const s = await sc(() => window.SC.sane());
  check("the 3D draws a picture (not one flat colour)", s.hi - s.lo > 60, JSON.stringify(s));
  check("it keeps drawing", await sc(() => window.SC.settle(2, 20000)));
});

add("pool", async () => {
  await downlight({ options: { haze: 0 } });
  save("02-pool");
  const c = await lum(await at(0, 0, 6)), off = await lum(await at(3.5, 0, 9));
  check("a lit downlight makes a pool on the floor under it", c > off * 2.2 + 12, `centre ${c.toFixed(1)} vs away ${off.toFixed(1)}`);
  await downlight({ options: { haze: 0 }, look: { a: 0 } });
  const c0 = await lum(await at(0, 0, 6));
  check("a dark light makes no pool", c0 < off + 8, `centre ${c0.toFixed(1)} vs away ${off.toFixed(1)}`);
});

add("beam", async () => {
  await downlight({ options: { haze: 0.8 } });
  save("03-beam-haze");
  const mid = await at(0, 2.4, 6), side = await at(2.2, 2.4, 6);
  const inBeam = await lum(mid, 3), beside = await lum(side, 3);
  check("in haze the beam shows in the air", inBeam > beside + 6, `beam ${inBeam.toFixed(1)} vs beside ${beside.toFixed(1)}`);
  await downlight({ options: { haze: 0 } });
  const none = await lum(mid, 3), beside0 = await lum(side, 3);
  check("with no haze there is no beam in the air", none < beside0 + 6, `beam ${none.toFixed(1)} vs beside ${beside0.toFixed(1)}`);
  check("haze makes the beam brighter", inBeam > none + 4, `haze ${inBeam.toFixed(1)} vs none ${none.toFixed(1)}`);
});

add("gobo", async () => {
  // a moving spot, straight down: open, then each drawn pattern (what a
  // light gets when its file has no gobo pictures) - each must break the
  // pool up, an open beam must not
  await downlight({ type: "moving_spot", beam: 22, options: { haze: 0 } });
  const open = await sc(() => window.SC.floorSpread(0, 6, 0.45));
  const names = ["dot ring", "star", "bars", "rings", "petals", "break-up", "cross"];
  for (let id = 1; id <= 7; id++) {
    await downlight({ type: "moving_spot", beam: 22, options: { haze: 0 }, look: { beam: { gobo: (id - 0.5) / 7.99 } } });
    if (id === 4) save("04-gobo-rings");
    const g = await sc(() => window.SC.floorSpread(0, 6, 0.45));
    check(`the ${names[id - 1]} gobo breaks the pool up (an open beam is smooth)`, g.std > open.std * 2 + 6,
      `gobo ${g.std.toFixed(1)} vs open ${open.std.toFixed(1)}`);
  }
});

add("colour", async () => {
  for (const [hex, ch, name] of [["#ff0000", 0, "red"], ["#00ff00", 1, "green"], ["#0000ff", 2, "blue"]]) {
    await downlight({ options: { haze: 0, house: 0 }, look: { hex } });
    const c = await rgb(await at(0, 0, 6), 3);
    const others = c.filter((_, i) => i !== ch);
    check(`a ${name} light lights the floor ${name}`, c[ch] > Math.max(...others) * 1.5 + 10, c.map((v) => v.toFixed(0)).join(","));
  }
  save("05-colour-blue");
});

// The colour on the desk is the colour on the screen: a pool keeps the
// hue the desk sent, dim or bright (the old film curve turned bright red
// orange and green yellow).
const hueOf = ([r, g, b]) => {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  if (mx - mn < 1) return null;
  let h = mx === r ? (g - b) / (mx - mn) : mx === g ? 2 + (b - r) / (mx - mn) : 4 + (r - g) / (mx - mn);
  return ((h * 60) % 360 + 360) % 360;
};
const hueGap = (a, b) => Math.abs(((a - b) % 360 + 540) % 360 - 180);
add("truecolour", async () => {
  for (const [hex, want, name] of [["#ff0000", 0, "red"], ["#00ff00", 120, "green"], ["#2050ff", 227, "deep blue"], ["#ffaa00", 40, "amber"]]) {
    for (const level of [0.35, 1]) {
      await downlight({ options: { haze: 0, house: 0 }, look: { hex, a: level } });
      const h = hueOf(await rgb(await at(0, 0, 6), 3));
      check(`a ${name} pool keeps its colour at ${Math.round(level * 100)} %`, h !== null && hueGap(h, want) < 6, `hue ${h && h.toFixed(1)} (desk ${want})`);
    }
  }
});

add("exposure", async () => {
  await downlight({ options: { haze: 0, house: 0.2, exposure: 0 } });
  const mid = await lum(await at(0, 0, 6)), wall = await lum(await at(3.5, 0, 9));
  const lights0 = await sc(() => window.SC.LIGHTS.uCol.value[0].toArray());
  await downlight({ options: { haze: 0, house: 0.2, exposure: 1 } });
  const up = await lum(await at(0, 0, 6));
  const lights1 = await sc(() => window.SC.LIGHTS.uCol.value[0].toArray());
  await downlight({ options: { haze: 0, house: 0.2, exposure: -1 } });
  const down = await lum(await at(0, 0, 6)), wallDown = await lum(await at(3.5, 0, 9));
  check("exposure up makes the picture brighter", up > mid + 5, `${mid.toFixed(1)} -> ${up.toFixed(1)}`);
  check("exposure down makes it darker", down < mid - 5 && wallDown <= wall, `${mid.toFixed(1)} -> ${down.toFixed(1)}`);
  check("exposure leaves the lights as they are", JSON.stringify(lights0) === JSON.stringify(lights1));
  await sc(() => window.stage.setOptions({ exposure: 0 }));
});

// Strobes at their real speed: the picture-by-picture level, at 60 and at
// 30 pictures a second, for strobes from 2 to 40 flashes a second.
add("strobe", async () => {
  const runs = await sc(() => {
    const { strobeLevel } = window.SC.look;
    const out = [];
    for (const fps of [60, 30]) for (const hz of [2, 5, 10, 14, 20, 25, 30, 40]) for (const mode of [undefined, "random"]) {
      const st = { on: false };
      let prev = null, edges = 0, lit = 0, run = 0, longest = 0, t0 = null;
      const secs = 4;
      for (let i = 1; i <= fps * secs; i++) {
        const t = i / fps + 0.0137;
        const v = strobeLevel(hz, mode, t0, t, 3, st);
        t0 = t;
        if (v > 0.5 && prev !== null && prev <= 0.5) edges++;
        if (v > 0.5) { lit++; run++; longest = Math.max(longest, run); } else run = 0;
        prev = v;
      }
      out.push({ fps, hz, mode: mode || "plain", perSec: edges / secs, lit: lit / (fps * secs), longest });
    }
    // calm: never a flash - few changes a second, never dark
    const st = { on: false };
    let lo = 1, hi = 0, flips = 0, prev = null;
    for (let i = 1; i <= 60 * 4; i++) {
      const v = strobeLevel(20, undefined, (i - 1) / 60, i / 60, 3, st, true);
      lo = Math.min(lo, v); hi = Math.max(hi, v);
      const up = prev === null ? null : v > prev;
      if (prev !== null && window.__up !== undefined && up !== window.__up) flips++;
      window.__up = up;
      prev = v;
    }
    return { out, calm: { lo, hi, flipsPerSec: flips / 4 } };
  });
  for (const r of runs.out) {
    if (r.mode === "plain" && r.hz <= r.fps / 2) {
      check(`a ${r.hz} Hz strobe flashes ${r.hz} times a second at ${r.fps} pictures a second`, Math.abs(r.perSec - r.hz) <= 0.5, `${r.perSec} a second`);
    } else if (r.mode === "plain") {
      check(`a ${r.hz} Hz strobe at ${r.fps} pictures a second flickers as fast as the screen can (every other picture)`,
        r.perSec >= r.fps / 2 - 1 && r.longest === 1, `${r.perSec} a second, longest lit run ${r.longest}`);
    } else if (r.hz <= r.fps / 2) {
      check(`a random ${r.hz} Hz strobe flashes on some periods, never steady (${r.fps} a second)`,
        r.perSec > 0 && r.perSec < r.hz && r.lit < 0.6, `${r.perSec} a second, lit ${(r.lit * 100).toFixed(0)} %`);
    }
  }
  check("calm flashing never flashes: a steady glow, a slow shimmer", runs.calm.lo > 0.45 && runs.calm.hi < 0.8 && runs.calm.flipsPerSec <= 3,
    JSON.stringify(runs.calm));
  // the 3D draws every picture while a light strobes (and not in calm)
  await downlight({ options: { haze: 0.5 }, look: { hz: 12 } });
  const strobing = await sc(async () => { await window.SC.settle(3); return window.stage.strobing; });
  check("while a light strobes the 3D draws every picture", strobing === true, String(strobing));
  await sc(() => window.stage.setOptions({ calm: true }));
  const calmNow = await sc(async () => { await window.SC.settle(3); return window.stage.strobing; });
  check("calm flashing: no need to", calmNow === false, String(calmNow));
  await sc(() => window.stage.setOptions({ calm: false }));
  await downlight({ options: { haze: 0.5 }, look: { hz: 0 } });
  const steady = await sc(async () => { await window.SC.settle(3); return window.stage.strobing; });
  check("a steady light: back to resting when nothing moves", steady === false, String(steady));
});

add("quality", async () => {
  for (const q of ["fast", "auto", "high"]) {
    await downlight({ options: { haze: 0.6, quality: q } });
    const c = await lum(await at(0, 0, 6)), off = await lum(await at(3.5, 0, 9));
    check(`${q === "fast" ? "Low" : q === "auto" ? "Medium" : "High"} quality draws the pool`, c > off * 2 + 10, `${c.toFixed(1)} vs ${off.toFixed(1)}`);
  }
});

// The look book: a small club - a stage, a truss of movers in pink and
// blue, washes, a crowd - from the house, the side and the dance floor.
add("lookbook", async () => {
  await sc(async () => {
    const S = window.SC;
    const fixtures = [], looks = {};
    let n = 0;
    for (let i = 0; i < 8; i++) {
      n++;
      const x = -4.2 + i * 1.2;
      fixtures.push({ head_no: n, model: "Spot", body: S.body("moving_spot", 16), x, y: 5.4, z: 3, stance: "hang", kind: "truss" });
      looks[n] = { hex: i % 2 ? "#2050ff" : "#ff2a80", a: 1, pan: 0.5 + (i - 3.5) * 0.012, tilt: 0.5 + 0.09, beam: i % 3 === 0 ? { gobo: 0.5 } : {} };
    }
    for (let i = 0; i < 6; i++) {
      n++;
      fixtures.push({ head_no: n, model: "Wash", body: S.body("moving_wash", 30), x: -3.5 + i * 1.4, y: 5.4, z: 8, stance: "hang", kind: "truss" });
      looks[n] = { hex: "#1030ff", a: 0.9, pan: 0.5, tilt: 0.5 - 0.05 };
    }
    for (let i = 0; i < 4; i++) {
      n++;
      fixtures.push({ head_no: n, model: "Par", body: S.body("par", 25), x: -4 + i * 2.6, y: 0.1, z: 1.2, stance: "stand", rot: [0, 70], kind: "floor" });
      looks[n] = { hex: "#ff6020", a: 1 };
    }
    await S.scene({
      venue: S.room(14, 18, 6.5, {
        stage: { x: 0, z: 0, width: 10, depth: 4, height: 0.8, colour: "#26272c" },
        rigging: [{ id: "t1", kind: "truss", name: "", size: 0.3, a: [-5.5, 5.6, 3], b: [5.5, 5.6, 3] },
          { id: "t2", kind: "truss", name: "", size: 0.3, a: [-5, 5.6, 8], b: [5, 5.6, 8] }],
        objects: [{ id: "dj", kind: "dj_booth", x: 0, z: 2, w: 2.4, d: 0.9, h: 1.1, y: 0.8 }],
        zones: [{ id: "df", kind: "dancefloor", points: [[-5, 6], [5, 6], [5, 14], [-5, 14]], y: 0, density: 0.6 }],
      }),
      fixtures, looks, options: { haze: 0.6, quality: "high", house: 0.25, people: true },
      camera: { pos: [0, 4.2, 20.5], target: [0, 2.2, 5] },
    });
  });
  await shot();
  save("lookbook-1-house");
  await sc(() => window.stage.setCamera({ pos: [11, 3.2, 12], target: [-1, 2.4, 5] }));
  await shot();
  save("lookbook-2-side");
  await sc(() => window.stage.setCamera({ pos: [1.2, 1.65, 11], target: [0, 3.4, 4] }));
  await shot();
  save("lookbook-3-dancefloor");
  const info = await sc(() => ({ lights: window.SC.LIGHTS.uCount.value, shadows: window.SC.LIGHTS.uShadowCount.value }));
  console.log(`  ..   look book saved (${OUT}): ${info.lights} lights on the surfaces, ${info.shadows} casting shadows`);
});

for (const c of CHECKS) {
  if (!selected(c.name)) continue;
  console.log(c.name);
  const before = errors.length;
  await c.run();
  if (errors.length > before) check(`no page errors (${c.name})`, false, errors.slice(before).join(" | "));
}

await browser.close();
console.log(fails ? `${fails} check(s) FAILED` : "all 3D checks passed");
done(fails ? 1 : 0);

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
import { LIGHTS, GRID } from "/js/stage/materials.js";
import { Atmosphere } from "/js/stage/atmos.js";
const el = document.getElementById("stage");
let frames = 0;
const stage = new Stage(el, { onFrame: () => { frames++; } });
let img = null;                       // the last picture read back
window.stage = stage;
window.SC = {
  THREE, look, LIGHTS, GRID, Atmosphere,
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
try {
  await page.waitForFunction(() => window.SC_READY && window.SC.ready(), null, { timeout: 60000 });
} catch (e) {
  check("the 3D page starts", false, errors.join(" | ") || e.message);
  done(1);
}
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

// Beams as light in the air (step 3): bright at the lens and fading as the
// beam spreads; brighter looking into it; gobo shafts in the haze; cut off
// exactly where something solid stands in it - not where its centre line
// happens to land.
add("haze-beam", async () => {
  await downlight({ options: { haze: 0.6 } });
  const near = await lum(await at(0, 4.0, 6), 1), far = await lum(await at(0, 0.9, 6), 1);
  check("a beam is brightest at the lens and fades as it spreads", near > far * 1.3 + 5, `near ${near.toFixed(1)} vs far ${far.toFixed(1)}`);
  for (const q of ["auto", "fast"]) {
    await downlight({ options: { haze: 0.6, quality: q } });
    const b = await lum(await at(0, 2.4, 6), 2), s = await lum(await at(2.2, 2.4, 6), 2);
    check(`${q === "auto" ? "Medium" : "Low"} quality: the beam shows in the haze`, b > s + 6, `${b.toFixed(1)} vs ${s.toFixed(1)}`);
  }
});

add("forward", async () => {
  // a beam across the room, seen from where it shines towards and from behind it
  const sideways = async (pos) => {
    await sc(async (pos) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5),
        fixtures: [{ head_no: 1, model: "Check light", body: S.body("par", 20), x: -4, y: 2, z: 6, stance: "stand", rot: [90, 0], kind: "floor" }],
        looks: { 1: { hex: "#ffffff", a: 1 } }, options: { haze: 0.6, quality: "high", house: 0.1, exposure: -1.5 },
        camera: { pos, target: [-4, 2, 6] } });
    }, pos);
    await shot();
    return lum(await at(-1, 2, 6), 2);
  };
  // (exposure down: nothing near white, where brighter can't show)
  const toward = await sideways([4.5, 0.6, 9]);
  save("06-into-the-beam");
  const behind = await sideways([-8.5, 0.6, 9]);
  await sc(() => window.stage.setOptions({ exposure: 0 }));
  check("a beam shining towards you is brighter in the haze than seen from behind", toward > behind * 1.4 + 4,
    `towards ${toward.toFixed(1)} vs behind ${behind.toFixed(1)}`);
});

add("shafts", async () => {
  // bars, turned across the view: stripes in the air
  // how much the light jumps from one point to the next across the beam
  // (stripes jump; an open beam only swells smoothly to its middle)
  const across = async () => {
    const a = await at(-0.35, 2.4, 6), b = await at(0.35, 2.4, 6);
    const v = await sc(([a, b]) => window.SC.line(a, b, 48, 0), [a, b]);
    let jump = 0;
    for (let i = 1; i < v.length; i++) jump += Math.abs(v[i] - v[i - 1]);
    return jump / (v.length - 1);
  };
  await downlight({ type: "moving_spot", beam: 22, options: { haze: 0.9 } });
  const open = await across();
  await downlight({ type: "moving_spot", beam: 22, options: { haze: 0.9 }, look: { beam: { gobo: 2.5 / 7.99 }, look: { grot: { at: 90 } } } });
  save("07-gobo-shafts");
  const bars = await across();
  check("a gobo makes shafts in the haze (stripes across a barred beam)", bars > open * 1.6 + 2, `bars ${bars.toFixed(1)} vs open ${open.toFixed(1)}`);
});

add("blocked", async () => {
  // a wide downlight, its centre and left half over a balcony 2.6 m up:
  // the right half reaches the floor; under the balcony the haze is dark
  await downlight({ beam: 40, options: { haze: 0.8, quality: "high", shadows: true },
    objects: [{ id: "bal", kind: "balcony", x: -1.35, z: 6, w: 3, d: 3, h: 0.25, y: 2.6, rot: 0 }] });
  save("08-half-blocked");
  const open = await lum(await at(0.75, 1.0, 6.5), 2), under = await lum(await at(-0.75, 1.0, 6.5), 2);
  const away = await lum(await at(3.6, 1.0, 6.5), 2);
  check("the half of a beam that misses a balcony goes on to the floor", open > away + 10, `open side ${open.toFixed(1)} vs away ${away.toFixed(1)}`);
  check("under the balcony the haze is dark (its shadow in the air)", under < open * 0.6, `under ${under.toFixed(1)} vs open side ${open.toFixed(1)}`);
});

add("slant", async () => {
  // a beam slanting down at 45 degrees: its far side reaches the floor
  // too (the old cone stopped square across where its centre landed)
  await sc(async () => {
    const S = window.SC;
    await S.scene({ venue: S.room(10, 14, 5),
      fixtures: [{ head_no: 1, model: "Check light", body: S.body("par", 30), x: 0, y: 4.5, z: 4, stance: "hang", rot: [0, -45], kind: "truss" }],
      looks: { 1: { hex: "#ffffff", a: 1 } }, options: { haze: 0.8, quality: "high", house: 0.1 },
      camera: { pos: [9, 2.5, 8], target: [0, 1.5, 8] } });
  });
  await shot();
  save("09-slant");
  const farSide = await lum(await at(0, 0.6, 10), 2), outside = await lum(await at(0, 2.8, 11.5), 2);
  check("a slanting beam reaches the floor all round", farSide > outside + 10, `far side ${farSide.toFixed(1)} vs outside ${outside.toFixed(1)}`);
});

// Every light lights the room (step 4): pools as bright as the light's
// output spread over its beam; every light of a big rig lands; shadows for
// the brightest, drawn again only when something moves; the pools and the
// LED screens light the room back.
add("output", async () => {
  // two lights side by side, the same beam: 6000 lumens and 1000
  const pair = async (a, b) => {
    await sc(async ([a, b]) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5),
        fixtures: [
          { head_no: 1, model: "A", body: S.body("par", a.beam), photo: { lm: a.lm, lm_from: "file" }, x: -2, y: 4.5, z: 6, stance: "hang", rot: [0, -90], kind: "truss" },
          { head_no: 2, model: "B", body: S.body("par", b.beam), photo: { lm: b.lm, lm_from: "file" }, x: 2, y: 4.5, z: 6, stance: "hang", rot: [0, -90], kind: "truss" }],
        looks: { 1: { hex: "#ffffff", a: 1 }, 2: { hex: "#ffffff", a: 1 } }, options: { haze: 0, quality: "high", house: 0.1, exposure: -2 },
        camera: { pos: [0, 9, 13], target: [0, 0, 6] } });
    }, [a, b]);
    await shot();
    return [await lum(await at(-2, 0, 6), 2), await lum(await at(2, 0, 6), 2)];
  };
  const [bright, dim] = await pair({ lm: 6000, beam: 25 }, { lm: 1000, beam: 25 });
  save("10-output");
  check("a light that puts out more lumens makes a brighter pool", bright > dim * 1.3 + 5, `6000 lm ${bright.toFixed(1)} vs 1000 lm ${dim.toFixed(1)}`);
  const [narrow, wide] = await pair({ lm: 2000, beam: 12 }, { lm: 2000, beam: 40 });
  check("the same light in a narrower beam makes a brighter pool", narrow > wide * 1.3 + 5, `12 deg ${narrow.toFixed(1)} vs 40 deg ${wide.toFixed(1)}`);
  await sc(() => window.stage.setOptions({ exposure: 0 }));
});

add("bigrig", async () => {
  // 120 downlights, a metre apart: every one makes its pool
  const stats = await sc(async () => {
    const S = window.SC;
    const fixtures = [], looks = {};
    let n = 0;
    for (let i = 0; i < 12; i++) for (let j = 0; j < 10; j++) {
      n++;
      fixtures.push({ head_no: n, model: "Par", body: S.body("par", 14), x: -5.5 + i, y: 4.5, z: 2 + j, stance: "hang", rot: [0, -90], kind: "truss" });
      looks[n] = { hex: "#ffffff", a: 1 };
    }
    await S.scene({ venue: S.room(16, 14, 5.5), fixtures, looks, options: { haze: 0, quality: "high", house: 0.05, exposure: -1, bloom: false },
      camera: { pos: [0, 18, 7.01], target: [0, 0, 7] } });
    return window.stage.lightStats;
  });
  await shot();
  save("11-bigrig");
  check("all 120 lights reach the surfaces (not the brightest 32)", stats.lights >= 120, JSON.stringify(stats));
  let lit = 0;
  const probes = [[0, 0], [11, 9], [3, 7], [8, 2], [5, 5], [10, 0], [1, 8]];
  for (const [i, j] of probes) {
    const c = await lum(await at(-5.5 + i, 0, 2 + j), 1), gap = await lum(await at(-5.0 + i, 0, 2.5 + j), 1);
    if (c > gap + 8) lit++;
  }
  check("every light probed has its own pool on the floor", lit === probes.length, `${lit} of ${probes.length}`);
  check("High: 16 lights cast shadows", stats.shadows === 16, String(stats.shadows));
  // nothing moves: the shadow pictures aren't drawn again
  const drawn = await sc(async () => { const d0 = window.stage.shadows.drawn; await window.SC.settle(4); return window.stage.shadows.drawn - d0; });
  check("the shadow pictures aren't drawn again while nothing moves", drawn === 0, `${drawn} drawn`);
  // (Medium adapts to the computer: a slow one - software drawing here -
  // sheds resolution and, below 60 %, the shadows; from the top, it has them)
  await sc(() => window.stage.setOptions({ quality: "auto" }));
  const med = await sc(async () => {
    const s = window.stage;
    s.q.ratio = s.q.cap; s.q.lite = false; s.q.ema = 16; s.q.slowSince = 0;
    await window.SC.settle(1);
    return s.lightStats.shadows;
  });
  check("Medium: 6 lights cast shadows", med === 6, String(med));
  await sc(() => window.stage.setOptions({ quality: "high", exposure: 0, bloom: true }));
});

add("bounce", async () => {
  // a red wash on a white wall, the floor in front of it outside the beam:
  // with the light bounced back it turns red
  const floor = async (bounce) => {
    await sc(async (bounce) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5, { wall: "#f0f0f0" }),
        fixtures: [{ head_no: 1, model: "Wash", body: S.body("par", 30), x: 0, y: 2.5, z: 3, stance: "stand", rot: [180, 0], kind: "floor" }],
        looks: { 1: { hex: "#ff0000", a: 1 } }, options: { haze: 0, quality: "high", house: 0, bounce },
        camera: { pos: [0, 4, 9], target: [0, 1.2, 1] } });
    }, bounce);
    await shot();
    return rgb(await at(0, 0, 2.2), 3);
  };
  const off = await floor(false);
  const on = await floor(true);
  save("12-bounce");
  check("a wall lit red lights the floor in front of it red (light bounced back)", on[0] > off[0] + 4 && on[0] > on[1] + 3,
    `bounced ${on.map((v) => v.toFixed(0))} vs not ${off.map((v) => v.toFixed(0))}`);
});

add("screenglow", async () => {
  // an LED screen showing the rig: one light, red, pointing up at the ceiling
  const floor = async (a) => {
    await sc(async (a) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5, { objects: [{ id: "scr", kind: "screen", x: 0, z: 1, w: 4, h: 2.4, d: 0.1, y: 0.3, rot: 0, content: "rig" }] }),
        fixtures: [{ head_no: 1, model: "Par", body: S.body("par", 20), x: 4, y: 0.3, z: 10, stance: "stand", rot: [0, 90], kind: "floor" }],
        looks: { 1: { hex: "#ff0000", a } }, options: { haze: 0, quality: "high", house: 0, bounce: true },
        camera: { pos: [0, 3.5, 8], target: [0, 1, 1] } });
      await S.settle(12);
    }, a);
    await shot();
    return rgb(await at(0, 0, 2.2), 3);
  };
  const dark = await floor(0);
  const red = await floor(1);
  save("13-screen-glow");
  check("an LED screen showing red lights the floor in front of it red", red[0] > dark[0] + 4 && red[0] > red[1] + 3,
    `screen red ${red.map((v) => v.toFixed(0))} vs dark ${dark.map((v) => v.toFixed(0))}`);
});

// Mirror floors (step 5): a black dance floor shows the beam reflected in
// it; carpet doesn't; Low draws no mirror.
add("mirror", async () => {
  const look = async (floor, mirror, quality = "high") => {
    await sc(async ([floor, mirror, quality]) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5, { floor }),
        fixtures: [{ head_no: 1, model: "Spot", body: S.body("moving_spot", 14), x: 0, y: 4.5, z: 5, stance: "hang", kind: "truss" }],
        looks: { 1: { hex: "#30a0ff", a: 1, pan: 0.5, tilt: 0.5 } }, options: { haze: 0.8, quality, house: 0.05, mirror },
        camera: { pos: [0, 1.2, 12], target: [0, 1.2, 5] } });
    }, [floor, mirror, quality]);
    await shot();
    // where the beam's lower part shows in the floor: the point mirrored under it
    return lum(await at(0, -1.0, 5), 2);
  };
  const off = await look("black", false);
  const on = await look("black", true);
  save("14-mirror-floor");
  check("a black dance floor shows the beam reflected in it", on > off + 8, `mirror ${on.toFixed(1)} vs none ${off.toFixed(1)}`);
  // the light off: its reflection goes too (on a rough floor the mirror is
  // read from its blurrier levels - they kept the old beam once)
  await sc(() => window.SC.setLooks({ 1: { hex: "#30a0ff", a: 0, pan: 0.5, tilt: 0.5 } }));
  await sc(() => window.stage.setOptions({ haze: 0 }));
  await shot();
  const gone = await lum(await at(0, -1.0, 5), 2);
  check("the light off, its reflection goes too (nothing left in the mirror)", gone < off + 4, `${gone.toFixed(1)} vs ${off.toFixed(1)}`);
  const carpet = await look("carpet", true);
  const carpetOff = await look("carpet", false);
  check("carpet doesn't", Math.abs(carpet - carpetOff) < 4, `carpet ${carpet.toFixed(1)} vs none ${carpetOff.toFixed(1)}`);
  const low = await look("black", true, "fast");
  const lowOff = await look("black", false, "fast");
  check("Low draws no mirror", Math.abs(low - lowOff) < 4, `${low.toFixed(1)} vs ${lowOff.toFixed(1)}`);
  await sc(() => window.stage.setOptions({ mirror: true, quality: "high" }));
});

// Atmosphere (step 6): the haze the hazers make (Auto), haze areas, CO2
// lit by the beams it's fired through.
add("atmos", async () => {
  const sim = await sc(() => {
    const T = window.SC.THREE;
    const a = new window.SC.Atmosphere();
    const hz = [{ head: 1, pos: new T.Vector3(0, 0.3, 1), dir: new T.Vector3(0, 0, 1), out: 1 }];
    for (let i = 0; i < 600; i++) a.step(hz, 1000, 0.1);              // a minute at full
    const full = a.level, clouds = a.clouds().length;
    for (let i = 0; i < 3000; i++) a.step([{ ...hz[0], out: 0 }], 1000, 0.1);   // five minutes off
    return { full, clouds, after: a.level, cloudsAfter: a.clouds().length };
  });
  check("a hazer running for a minute fills the room with haze", sim.full > 0.2 && sim.clouds === 1, JSON.stringify(sim));
  check("...and five minutes after it stops, the haze has settled to half or less, its cloud gone", sim.after < sim.full * 0.55 && sim.cloudsAfter === 0, JSON.stringify(sim));
  // a haze area with no haze in the room: the beam shows only inside it
  await downlight({ beam: 20, options: { haze: 0, quality: "high" }, objects: [{ id: "hz", kind: "haze", x: 0, z: 6, w: 3, d: 3, h: 2, y: 0, density: 1 }] });
  save("15-haze-area");
  const inside = await lum(await at(0, 1.0, 6), 2), above = await lum(await at(0, 3.4, 6), 2);
  check("a haze area: the beam shows in it, not above it (no haze in the room)", inside > above + 10, `inside ${inside.toFixed(1)} vs above ${above.toFixed(1)}`);
  // Auto: a rig with a hazer; the haze is the hazer's
  const autoScene = async (fog) => {
    await sc(async (fog) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5),
        fixtures: [{ head_no: 1, model: "Check light", body: S.body("par", 25), x: 0, y: 4.5, z: 6, stance: "hang", rot: [0, -90], kind: "truss" },
          { head_no: 2, model: "Hazer", body: S.body("atmos", 0), x: -3, y: 0.2, z: 2, stance: "stand", kind: "floor" }],
        looks: { 1: { hex: "#ffffff", a: 1 }, 2: { hex: "#ffffff", a: 0, fx: fog ? { fog: 1 } : null } },
        options: { haze: 1, hazeAuto: true, quality: "high", house: 0.1 }, camera: { pos: [0, 5.2, 15.5], target: [0, 1.2, 6] } });
    }, fog);
    await shot();
  };
  await autoScene(false);
  const clear = await lum(await at(0, 2.4, 6), 2), beside = await lum(await at(2.2, 2.4, 6), 2);
  check("Auto, the hazer off from the start: clear air, no beam in it", clear < beside + 6, `${clear.toFixed(1)} vs ${beside.toFixed(1)}`);
  await sc(async () => { window.stage.atmos.level = 0.8; await window.SC.settle(3); });
  await shot();
  const hazy = await lum(await at(0, 2.4, 6), 2);
  check("Auto: the room full of the hazer's haze shows the beam", hazy > clear + 10, `${hazy.toFixed(1)} vs ${clear.toFixed(1)}`);
  await autoScene(true);
  const clouds = await sc(async () => { await window.SC.settle(6); return window.stage.atmos.clouds().length; });
  check("Auto: a running hazer makes a cloud round it", clouds === 1, String(clouds));
  await sc(() => window.stage.setOptions({ hazeAuto: false, haze: 0.6 }));
  // CO2 fired up through a red beam glows red; with the beam off, white-grey
  const co2 = async (beam) => {
    await sc(async (beam) => {
      const S = window.SC;
      await S.scene({ venue: S.room(10, 12, 5),
        fixtures: [{ head_no: 1, model: "Check light", body: S.body("par", 40), x: 0, y: 4.5, z: 6, stance: "hang", rot: [0, -90], kind: "truss" },
          { head_no: 2, model: "CO2", body: S.body("co2", 0), x: 0, y: 0, z: 6, stance: "stand", kind: "floor" }],
        looks: { 1: { hex: "#ff0000", a: beam ? 1 : 0 }, 2: { hex: "#ffffff", a: 0, fx: { fire: true } } },
        options: { haze: 0, quality: "high", house: 0.4 }, camera: { pos: [0, 2.5, 12], target: [0, 2, 6] } });
      await S.settle(10);
    }, beam);
    await shot();
    return rgb(await at(0, 2.0, 6), 4);
  };
  const red = await co2(true);
  save("16-co2-in-a-red-beam");
  const plain = await co2(false);
  check("CO2 fired through a red beam glows red", red[0] > red[1] * 1.6 + 8 && red[0] > plain[0] + 8,
    `in the beam ${red.map((v) => v.toFixed(0))} vs not ${plain.map((v) => v.toFixed(0))}`);
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
  const lights0 = await sc(() => [...window.SC.GRID.lights.image.data.slice(8, 11)]);
  await downlight({ options: { haze: 0, house: 0.2, exposure: 1 } });
  const up = await lum(await at(0, 0, 6));
  const lights1 = await sc(() => [...window.SC.GRID.lights.image.data.slice(8, 11)]);
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
  // the picture's recipe doesn't change with each flash (that would
  // rebuild its shaders at every flash: a stutter)
  const keys = await sc(async () => {
    const seen = new Set();
    for (let i = 0; i < 12; i++) { await window.SC.settle(1); seen.add(window.stage._postKey); }
    return [...seen];
  });
  check("a strobing light never rebuilds the picture's recipe", keys.length === 1, keys.join(" / "));
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

// How long a picture takes at each quality, in the look book's club
// (software drawing here: only the qualities against each other mean
// anything; CHECKS_GPU=1 for this computer's graphics card).
add("speed", async () => {
  const out = [];
  for (const q of ["fast", "auto", "high"]) {
    await sc((q) => window.stage.setOptions({ quality: q }), q);
    const ms = await sc(async () => {
      await window.SC.settle(3);
      const t0 = performance.now(), f0 = window.SC.frames();
      await window.SC.settle(6, 60000);
      return (performance.now() - t0) / Math.max(1, window.SC.frames() - f0);
    });
    out.push(`${q === "fast" ? "Low" : q === "auto" ? "Medium" : "High"} ${ms.toFixed(0)} ms`);
  }
  console.log("  ..   a picture takes: " + out.join(", "));
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
      fixtures.push({ head_no: n, model: "Par", body: S.body("par", 25), x: -4 + i * 2.6, y: 0.85, z: 1.2, stance: "stand", rot: [0, 70], kind: "floor" });
      looks[n] = { hex: "#ff6020", a: 1 };
    }
    await S.scene({
      venue: S.room(14, 18, 6.5, {
        floor: "black",
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

// (the speed check times the look book's club, so it runs last)
CHECKS.push(...CHECKS.splice(CHECKS.findIndex((c) => c.name === "speed"), 1));
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

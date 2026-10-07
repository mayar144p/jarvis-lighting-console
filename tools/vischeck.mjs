// Does the 3D view show what the desk is doing?  A 124-light rig of real
// fixtures (MAC Aura, Sharpy, Rogue R2 Wash, Source Four LED, COLORado,
// pixel bars, strobes, lasers, foggers...) is programmed through the
// SCREEN - clicks on the fixture list, the programmer, the buttons - and
// after every step each light is checked at three layers:
//
//   DMX  -> the light feed:  the engine's look for the light follows its DMX
//            (Full lights it, a colour pick tints it, Blackout darkens it)
//   feed -> the 3D target:   what the 3D view was told is what the feed says
//            (colour, level, pan, tilt)
//   3D target -> the drawing: once settled, the model shows it - the lens
//            glows that colour, the beam is on exactly when it is lit, a
//            moving head has turned to its pan / tilt
//
// and lights the step did not touch must not change.  Page errors are
// findings too.
//
//   node tools/vischeck.mjs [outDir]
//   node tools/vischeck.mjs --brands [outDir]          the top 20 brands
//   node tools/vischeck.mjs --brand Antari [outDir]    one brand (any brand)
//   node tools/vischeck.mjs --product "robe megapointe" [outDir]
//
// --brands: the top 20 brands one at a time (tools/brands.py: up to 15
// products each - effects machines first, then lights by kind).  Each brand
// gets a fresh patch and the same steps, plus the effects: nothing fires or
// lights a laser while disarmed; armed, fire / fog / laser show in the feed
// and in the 3D (the fan is drawn, the particles fly); KILL stops it all.
//
// Runs its own server on a scratch data folder; your shows are untouched.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { RIG } from "./bigrig.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const argv = process.argv.slice(2);
const flag = (f) => (argv.includes(f) ? argv[argv.indexOf(f) + 1] : null);
const ONLY = flag("--brand"), PRODUCT = flag("--product");
const BRANDS = argv.includes("--brands") || !!ONLY || !!PRODUCT;
const outDir = argv.find((a, i) => !a.startsWith("--") && !["--brand", "--product"].includes(argv[i - 1])) || join(tmpdir(), "vischeck");
mkdirSync(outDir, { recursive: true });
const PORT = +(process.env.VISCHECK_PORT || 8815);
const BASE = `http://127.0.0.1:${PORT}/`;
let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const exe = process.env.CHROMIUM || (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const data = mkdtempSync(join(tmpdir(), "jarvis-vis-"));
const server = spawn(process.env.PYTHON || "python3", ["-m", "app.main"], {
  cwd: ROOT, stdio: ["ignore", "ignore", "pipe"],
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: data, CONSOLE_AUTOSAVE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" },
});
server.stderr.on("data", () => {});
process.on("exit", () => { try { server.kill(); } catch { /* gone */ } });
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE)).ok) break; } catch { /* not yet */ }
  if (i > 160) { console.error("server did not start"); process.exit(2); }
  await sleep(250);
}
const post = (path, body) => fetch(BASE + path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());
const act = (action, params = {}) => post("api/console", { action, params }).then((j) => j.result || j);
const getj = (path) => fetch(BASE + path).then((r) => r.json());

let oks = 0;
const fails = [];
function check(ok, what, detail = "") {
  if (ok) oks++;
  else { fails.push(`${what}  ${detail}`.trim()); console.log(`  FAIL ${what}  ${detail}`); }
  return ok;
}

// ------------------------------------------------------------- the rig
// [{brand, items: [{src, key, name, kind, qty}]}]
let RIGS;
if (BRANDS) {
  const out = join(data, "brands.json");
  const sel = ONLY ? ["--brand", ONLY] : PRODUCT ? ["--product", PRODUCT] : [];
  execFileSync(process.env.PYTHON || "python3", ["tools/brands.py", "--json", out, ...sel], { cwd: ROOT, stdio: "ignore" });
  RIGS = JSON.parse(readFileSync(out, "utf-8")).filter((r) => r.items.length);
  if (!RIGS.length) { console.error("no library products match"); process.exit(2); }
} else {
  RIGS = [{ brand: "", items: RIG.map(([src, key, name, qty]) => ({ src, key, name, qty })) }];
}

await act("venue_template", { name: "club" });
// CHECKS_GPU=1: a visible window on the computer's graphics card (fast, and you
// can watch); otherwise software drawing (servers / cloud with no GPU)
const GPU = process.env.CHECKS_GPU === "1";
const browser = await chromium.launch({ executablePath: exe, headless: !GPU,
  args: GPU ? ["--ignore-gpu-blocklist"] : ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const p = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
p.on("pageerror", (e) => errors.push(String(e.message || e)));
p.on("console", (m) => { if (m.type() === "error" && !/favicon|Failed to load resource/.test(m.text())) errors.push("console: " + m.text().slice(0, 200)); });
await p.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.people", "0"); localStorage.setItem("jarvis.quality", "fast"); } catch { /* fine */ } });
let patch = [];
let tag = "";                       // the brand, in front of every finding

async function build(rig) {
  await act("fx_kill");
  await act("patch_clear");
  await act("clear_programmer");
  // the last brand's cues: gone (GO must play this brand's look)
  for (let i = 0; i < 50 && (await act("delete_cue", { playback: 1, cue: 1 })).ok; i++);
  await act("playback_level", { playback: 1, level: 0 });
  for (const it of rig.items) {
    const inst = await post("api/fixtures/library/install", { src: it.src, key: it.key });
    const fid = inst.fixture && inst.fixture.id;
    const qty = it.qty || 1;
    const r = fid ? await act("add_heads", { fixture_id: fid, qty }) : { error: inst.error || "not installed" };
    check(r.ok && (r.heads || []).length === qty, `${tag}${qty} x ${it.name} patched`, r.error || "");
  }
  await p.goto(BASE);
  patch = [];
  for (let i = 0; i < 40 && !patch.length; i++) {
    await sleep(500);
    patch = await p.evaluate(async () => {
      const st = (await import("/app/store.js")).state;
      return ((st.snap && st.snap.patch) || []).map((h) => ({ head_no: h.head_no, model: h.model, map: h.map || [] }));
    });
  }
  console.log(`  ${patch.length} lights`);
  if (!BRANDS) check(patch.length >= 120, "a rig of 120+ lights", String(patch.length));
  for (let i = 0; i < 60; i++) {
    const n = await p.evaluate(() => (window.jarvisStage && window.jarvisStage.fixtures ? window.jarvisStage.fixtures.size : 0));
    if (n >= patch.length) break;
    await sleep(500);
  }
  check(await p.evaluate(() => window.jarvisStage.fixtures.size) === patch.length, `${tag}every light is in the 3D view`,
    `${await p.evaluate(() => window.jarvisStage.fixtures.size)} of ${patch.length}`);
}

// what each layer says, per light
const feed = async () => Object.fromEntries(((await getj("api/console/look")).heads || []).map((r) => [r.n, r]));
const view = () => p.evaluate(() => {
  const out = {};
  for (const [head, inst] of window.jarvisStage.fixtures) {
    const L = inst.cur || {}, T = inst.to || {}, sk = inst.sk || {}, m = inst.motor, body = inst.data.body || {};
    const lens = (sk.lenses || [])[0];
    out[head] = {
      a: L.a, c: [L.r, L.g, L.b], pan: L.pan, tilt: L.tilt,
      ta: T.a, tc: [T.r, T.g, T.b], tpan: T.pan, ttilt: T.tilt,
      level: inst.level, mpan: m ? m.pan.x : null, mtilt: m ? m.tilt.x : null,
      lens: lens ? [lens.emissive.r, lens.emissive.g, lens.emissive.b] : null,
      beams: (inst.beams || []).length, lit: (inst.beams || []).some((b) => b.glow.visible),
      moving: !!body.moving, cls: body.class || "light", type: body.type || "", prog: !!L.prog, hz: L.hz || 0,
      fx: L.fx || null,
      fan: !!(window.jarvisStage.sfx.lasers.get(head) && window.jarvisStage.sfx.lasers.get(head).lines.visible),
      puffs: (window.jarvisStage.sfx.pools.get(head) || {}).alive || 0,
    };
  }
  return out;
});
const lin = (hex) => {
  const h = (hex || "#ffffff").replace("#", "");
  return [0, 2, 4].map((i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
};
const near = (a, b, tol = 0.02) => Math.abs((a ?? 0) - (b ?? 0)) <= tol;
const nearC = (a, b, tol = 0.02) => a && b && a.every((x, i) => near(x, b[i], tol));

// layer checks for every light; `touched` = the lights the step changed
async function layers(step, settleMs = 1600) {
  await sleep(settleMs);
  const F = await feed(), V = await view();
  let bad = 0;
  const note = (what, detail) => { if (bad++ < 6) check(false, `${tag}${step}: ${what}`, detail); };
  for (const [hs, v] of Object.entries(V)) {
    const f = F[hs] || null;
    // feed -> 3D target
    const fa = f ? +f.a || 0 : 0;
    if (!near(v.ta, fa, 0.001)) note(`#${hs} level in 3D = the feed`, `3D ${v.ta} feed ${fa}`);
    if (f && !nearC(v.tc, lin(f.hex), 0.002)) note(`#${hs} colour in 3D = the feed`, `${f.hex}`);
    if (f && typeof f.pan === "number" && !near(v.tpan, f.pan, 0.001)) note(`#${hs} pan in 3D = the feed`, `${v.tpan} vs ${f.pan}`);
    if (f && typeof f.tilt === "number" && !near(v.ttilt, f.tilt, 0.001)) note(`#${hs} tilt in 3D = the feed`, `${v.ttilt} vs ${f.tilt}`);
    // 3D target -> drawing (settled; a strobing or self-running light flickers by design)
    if (v.hz || v.prog) continue;
    if (!near(v.a, v.ta, 0.02)) note(`#${hs} the 3D level settled`, `${v.a} -> ${v.ta}`);
    if (v.lens && v.ta > 0.05 && !nearC(v.lens.map((x) => x / Math.max(1e-6, v.level * 7)), v.c, 0.03)) note(`#${hs} the lens glows its colour`, JSON.stringify(v.lens));
    if (v.beams && (v.level > 0.01) !== v.lit && v.cls === "light") note(`#${hs} the beam is on exactly when it is lit`, `level ${v.level} beam ${v.lit}`);
    if (v.moving && typeof v.tpan === "number" && v.mpan !== null && !near(v.mpan, v.tpan, 0.02)) note(`#${hs} the head turned to its pan`, `${v.mpan} vs ${v.tpan}`);
    if (v.moving && typeof v.ttilt === "number" && v.mtilt !== null && !near(v.mtilt, v.ttilt, 0.02)) note(`#${hs} the head turned to its tilt`, `${v.mtilt} vs ${v.ttilt}`);
  }
  check(bad === 0, `${tag}${step}: all ${Object.keys(V).length} lights - feed, 3D and drawing agree`, bad ? `${bad} mismatches` : "");
  return { F, V };
}
const click = async (sel, what) => {
  try { await p.click(sel, { timeout: 20000 }); return true; } catch (e) { check(false, `${tag}click ${what || sel}`, String(e.message).split("\n")[0]); return false; }
};
const clickText = (sel, text) => click(`${sel}:has-text("${text}")`, text);
const shot = (name) => p.screenshot({ path: join(outDir, name + ".png") });
const lights = (V) => Object.entries(V).filter(([, v]) => v.cls === "light").map(([h]) => +h);
const roles = (h) => (patch.find((x) => x.head_no === +h) || {}).map || [];
// a light that can make red (RGB, or CMY): a wheel's slots vary by model
const canRed = (h) => roles(h).includes("red") || (roles(h).includes("magenta") && roles(h).includes("yellow"));
// a light nothing on DMX can darken (an old lamp scanner: no dimmer, shutter
// or colour): lit always, like the real one (the Ready? check warns)
const DIMS = ["dimmer", "zone_dimmer", "shutter", "strobe", "red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "lime"];
const lampOnly = (h) => !roles(h).some((r) => DIMS.includes(r)) && roles(h).some((r) => ["pan", "tilt", "wheel", "gobo", "gobo_rot", "prism", "zoom", "focus"].includes(r));
const name = (h) => `#${h} ${(patch.find((x) => x.head_no === +h) || {}).model || ""}`;

// --------------------------------------------------------------- steps

// ------------------------------------------------------------ fit check
// Does the programmer offer what each product CAN do, and only that?  And is
// its 3D model the kind of light it is?  One product at a time, selected
// alone, every tab it shows opened and read.
const LIB_3D = [                      // the library's own type -> the 3D body it should get
  [/moving head/i, (b) => b.moving, "a moving head"],
  [/scanner/i, (b) => b.type === "scanner", "a scanner (mirror)"],
  // ("Effect" is also what MagicFX calls its confetti / CO2 / flame machines)
  [/flower|effect/i, (b) => ["effect", "scanner"].includes(b.type) || b.moving || b.class !== "light", "an effect light"],
  [/^(strobe|blinder)/i, (b) => ["strobe", "blinder", "bar", "matrix", "tube"].includes(b.type), "a strobe / blinder"],
  [/led bar|pixel bar/i, (b) => ["bar", "moving_bar", "tube", "matrix", "strobe", "blinder"].includes(b.type), "a bar"],
  [/laser/i, (b) => b.type === "laser", "a laser"],
  [/smoke|hazer/i, (b) => ["atmos", "co2"].includes(b.type), "a fog / haze machine"],
  [/^dimmer$/i, (b) => !b.moving, "a fixed light"],
];
const FULL_HUES = ["Red", "Green", "Blue"];
async function fit(rig) {
  console.log("0. programmer and 3D fit, product by product");
  const byName = Object.fromEntries(rig.items.map((it) => [it.name, it]));
  const info = await p.evaluate(async () => {
    const st = (await import("/app/store.js")).state;
    return ((st.snap && st.snap.patch) || []).map((h) => ({ n: h.head_no, model: h.model, manufacturer: h.manufacturer, map: h.map || [], body: h.body || {} }));
  });
  for (const hd of info) {
    const it = byName[`${hd.manufacturer} ${hd.model}`] || rig.items.find((x) => x.name.endsWith(hd.model)) || {};
    const who = `${tag}${hd.manufacturer} ${hd.model}`;
    const has = new Set(hd.map);
    const bad = (what, detail = "") => check(false, `${who}: ${what}`, detail);
    // --- the 3D body is the kind of product it is
    // the library's first type is what the product is ("Color Changer,
    // Dimmer, Effect" is a PAR that has effects)
    const rule = LIB_3D.find(([re]) => re.test((it.type || "").split(",")[0].trim()));
    // (a combo with a laser in it - Stairville All FX Bar - is a laser for
    // safety; a combined bar + laser model is in docs/BACKLOG.md)
    // (and the product's own name wins over a library type that disagrees:
    // the ADJ "Encore Profile" is filed as a Blinder)
    const named = new RegExp(`\\b${String(hd.body.type || "").replace(/^moving_/, "")}\\b`, "i").test(hd.model || "");
    if (rule && !rule[1](hd.body) && !named && !(hd.body.type === "laser" && hd.map.some((r) => r.startsWith("laser_")))) bad(`the 3D draws it as "${hd.body.type}", the library says ${rule[2]}`, it.type);
    if (hd.body.class === "light") {
      // each colour cell (a pixel bar's pixels, a multi-head light's heads)
      // is coloured on its own in the 3D
      const reds = hd.map.filter((r) => r === "red").length;
      const cells = await p.evaluate((n) => {
        const i = window.jarvisStage.fixtures.get(n);
        if (!i) return -1;
        const sk = i.sk;
        return Math.max(sk.pixels ? sk.pixels.count : 0, (sk.cells || []).length,
          new Set((sk.emitters || []).map((e) => e.cell).filter((c) => c !== undefined)).size);
      }, hd.n);
      if (cells >= 0 && reds > 1 && cells < Math.min(reds, 32)) bad(`the 3D colours ${cells} cell(s) on their own, the light has ${reds} colour cells`);
    }
    // --- the programmer, selected alone
    await act("select_heads", { heads: [hd.n] });
    await sleep(500);
    const tabs = await p.evaluate(() => [...document.querySelectorAll("#prog-tabs button")].filter((b) => !b.hidden).map((b) => b.dataset.tab));
    const light = hd.body.class === "light";
    const rgb = ["red", "green", "blue"].filter((r) => has.has(r));
    const cmy = ["cyan", "magenta", "yellow"].filter((r) => has.has(r));
    const fullMix = rgb.length === 3 || cmy.length === 3;
    const anyColour = light && ["red", "green", "blue", "cyan", "magenta", "yellow", "wheel", "white", "amber", "uv", "lime", "cto"].some((r) => has.has(r));
    const want = { position: has.has("pan") || has.has("tilt"), colour: anyColour,
      laser: [...has].some((r) => r.startsWith("laser_")), sfx: !light && hd.body.class === "sfx" };
    for (const [t, w] of Object.entries(want)) if (tabs.includes(t) !== w) bad(`${w ? "no" : "a"} ${t} tab`, `tabs: ${tabs.join(", ")}`);
    if (tabs.includes("colour")) {
      await click('#prog-tabs [data-tab="colour"]', "Colour tab");
      const attrs = await getj(`api/console/attributes?heads=${hd.n}`);
      const wheel = (attrs.pages || []).flatMap((pg) => pg.attrs || []).find((a) => a.role === "wheel");
      // the tab reloads the light's details after a selection: give it up to
      // 3 s to show THIS light's wheel (a lag is fine, a wrong wheel is not)
      let c;
      for (let i = 0; i < 10; i++) {
        await sleep(300);
        c = await p.evaluate(() => {
        const vis = (el) => !!el && !el.hidden && !!el.offsetParent;
        const pane = document.querySelector('[data-pane="colour"]');
        return { picker: vis(document.querySelector("#picker")) && !pane.classList.contains("wheel-only"),
          swatches: vis(document.querySelector("#swatches")) ? [...document.querySelectorAll("#swatches button")].filter(vis).map((b) => b.title) : [],
          kelvin: vis(document.querySelector("#kelvin-row")),
          wheel: vis(document.querySelector("#wheel-steps")), slots: [...document.querySelectorAll("#wheel-steps .chip.slot")].filter(vis).length,
          reach: (document.querySelector("#colour-reach") || {}).textContent || "" };
        });
        if (!(wheel && wheel.slots && wheel.slots.length) || c.slots === wheel.slots.length) break;
      }
      if (fullMix && !c.picker) bad("mixes any colour but has no colour picker");
      if (!fullMix && c.picker) bad(`the colour picker is offered, but it ${rgb.length || cmy.length ? `only mixes ${[...rgb, ...cmy].join(" + ")}` : has.has("wheel") ? "has a colour wheel only" : "has no colour mixing"}`);
      if (!fullMix && c.swatches.some((t) => FULL_HUES.includes(t))) {
        const cant = c.swatches.filter((t) => FULL_HUES.includes(t) && !(rgb.includes(t.toLowerCase())));
        if (cant.length) bad(`colour buttons it can't make: ${cant.join(", ")}`);
      }
      if (has.has("wheel") && !c.wheel) bad("has a colour wheel, the Colour tab shows no wheel colours");
      if (!fullMix && c.swatches.length) bad(`hue swatches for a light that can't mix them: ${c.swatches.slice(0, 4).join(", ")}…`);
      if (wheel && wheel.slots && wheel.slots.length && c.slots !== wheel.slots.length) bad(`the wheel shows ${c.slots} colours, the light has ${wheel.slots.length}`);
      // a light that can't mix: one button per colour it makes (each
      // emitter, and red / green / blue in pairs), no more, no less
      if (!fullMix && !has.has("wheel")) {
        const em = ["red", "green", "blue", "white", "amber", "uv", "lime"].filter((r) => has.has(r));
        const makes = em.length + (rgb.length === 2 ? 1 : 0);
        if (makes && c.slots !== makes) bad(`it makes ${makes} colours, the Colour tab offers ${c.slots}`);
      }
      if (!fullMix && /mixes any colour/.test(c.reach)) bad(`says "${c.reach}"`);
      if (c.kelvin && !fullMix) bad("a white-temperature slider, but it can't mix white");
    }
    if (tabs.includes("position")) {
      await click('#prog-tabs [data-tab="position"]', "Move tab");
      await sleep(300);
      const pad = await p.evaluate(() => { const el = document.querySelector("#pad"); return el ? { noPan: el.classList.contains("no-pan"), noTilt: el.classList.contains("no-tilt") } : null; });
      if (pad && pad.noPan === has.has("pan")) bad(has.has("pan") ? "pans, the pad says it doesn't" : "doesn't pan, the pad offers pan");
      if (pad && pad.noTilt === has.has("tilt")) bad(has.has("tilt") ? "tilts, the pad says it doesn't" : "doesn't tilt, the pad offers tilt");
    }
  }
  await act("clear_selection");
}

async function steps() {
  await shot(`${tag}01-start`);
  console.log("1. start: everything dark");
  let { F, V } = await layers("start");
  console.log("  covering:", await p.evaluate(() => [...document.querySelectorAll(".modal-scrim, .menu, .cmdbar-scrim:not(.hidden)")].map((x) => (x.innerText || "").slice(0, 120)).join(" | ")));
  const litAtStart = lights(V).filter((h) => !lampOnly(h) && F[h] && +F[h].a > 0.01);
  check(!litAtStart.length, `${tag}start: no light is lit`, litAtStart.map(name).join(", "));
  if (!lights(V).length) {                 // a brand of effects only (Laserworld)
    console.log("  no lights: effects only");
    if (BRANDS) await effects();
    return;
  }

  console.log("2. All -> Full");
  await click('.sel-actions [data-act="select_all"]', "All");
  await click('#prog-tabs [data-tab="intensity"]', "Level tab");
  await click('#int-quick [data-level="100"]', "Full");
  ({ F, V } = await layers("Full"));
  const dark = lights(V).filter((h) => !(F[h] && +F[h].a > 0.5));
  check(!dark.length, `${tag}Full: every light is lit in the feed`, dark.slice(0, 12).map(name).join(", "));
  await shot(`${tag}02-full`);

  console.log("3. Colour: red");
  await click('#prog-tabs [data-tab="colour"]', "Colour tab");
  await click('#swatches button[title="Red"]', "Red swatch");
  ({ F, V } = await layers("red"));
  const notRed = lights(V).filter((h) => { const f = F[h]; if (!f || !(+f.a > 0.05) || !canRed(h)) return false; const [r, g, b] = lin(f.hex); return !(r > 0.3 && r > g * 3 && r > b * 3); });
  check(!notRed.length, `${tag}red: the colour lights turn red in the feed`, `${notRed.length} not red: ${notRed.slice(0, 10).map((h) => `${name(h)} ${F[h].hex}`).join(", ")}`);
  await shot(`${tag}03-red`);

  console.log("4. One light: blue (only it changes)");
  const before = F;
  await click('.sel-actions [data-act="clear_selection"]', "None");
  const target = patch.find((x) => /Mega PAR/.test(x.model || "")) || patch.find((x) => canRed(x.head_no) && lights(V).includes(x.head_no));
  const tno = target ? target.head_no : null;
  if (tno !== null) {
  // expand the fold that holds it, then click its own row
  await p.evaluate((n) => {
    for (const tr of document.querySelectorAll("#fx-rows tr.fold")) {
      if (tr.dataset.fold.split(",").map(Number).includes(n) && !tr.classList.contains("open")) tr.querySelector(".fold-btn").click();
    }
  }, tno);
  await sleep(400);
  await click(`#fx-rows tr[data-head="${tno}"]:not(.fold) td.c-name`, `fixture #${tno} row`);
  await click('#swatches button[title="Blue"]', "Blue swatch");
  ({ F, V } = await layers("one blue"));
  const [r1, g1, b1] = lin(F[tno] && F[tno].hex);
  check(b1 > r1 * 3, `${tag}the picked light (#${tno}) is blue`, F[tno] && F[tno].hex);
  const changed = lights(V).filter((h) => h !== tno && before[h] && F[h] && (before[h].hex !== F[h].hex || Math.abs(+before[h].a - +F[h].a) > 0.01));
  check(!changed.length, `${tag}no other light changed`, changed.slice(0, 10).map((h) => `#${h} ${before[h].hex}->${F[h].hex}`).join(", "));
  await shot(`${tag}04-one-blue`);
  }

  console.log("5. Movers to the dance floor");
  const movers = Object.entries(V).filter(([, v]) => v.moving).map(([h]) => +h);
  if (movers.length || !BRANDS) {
  await click('.sel-actions [data-act="clear_selection"]', "None");
  await clickText("#group-chips .chip", "Moving");
  await click('#prog-tabs [data-tab="position"]', "Move tab");
  const pan0 = Object.fromEntries(movers.map((h) => [h, F[h] && F[h].pan]));
  await clickText(".mv-spot", "Dance floor");
  ({ F, V } = await layers("aim at the floor", 4500));
  const unmoved = movers.filter((h) => F[h] && F[h].pan === pan0[h] && F[h].tilt === (F[h].tilt));
  check(movers.length > 0, `${tag}the rig has moving heads`, String(movers.length));
  check(unmoved.length < movers.length, `${tag}the movers aim somewhere new`, `${unmoved.length} of ${movers.length} unchanged`);
  await shot(`${tag}05-floor`);
  }

  console.log("6. Rainbow on everything");
  await click('.sel-actions [data-act="select_all"]', "All");
  await click('#prog-tabs [data-tab="fx"]', "FX tab");
  await clickText(".fx-card", "Rainbow");
  const f1 = await feed();
  await sleep(900);
  const f2 = await feed();
  const tinted = lights(V).filter(canRed);
  const moved = tinted.filter((h) => f1[h] && f2[h] && f1[h].hex !== f2[h].hex);
  if (tinted.length) check(moved.length > tinted.length / 3, `${tag}the rainbow moves the colours over time`, `${moved.length} of ${tinted.length} changed`);
  // the 3D follows a moving effect: its target is the look the page was sent
  // at that moment (read together, so the effect can't move in between)
  const liveBad = await p.evaluate(async () => {
    const looks = (await import("/app/store.js")).state.looks || {};
    const bad = [];
    for (const [head, inst] of window.jarvisStage.fixtures) {
      const row = looks[head];
      const a = row ? +row.a || 0 : 0;
      if (Math.abs((inst.to.a || 0) - a) > 0.001) bad.push(`#${head} level ${inst.to.a} vs ${a}`);
      if (row && row.hex) {
        const c = window.jarvisStage._c.clone().set(row.hex);
        if (Math.abs(c.r - inst.to.r) + Math.abs(c.g - inst.to.g) + Math.abs(c.b - inst.to.b) > 0.01) bad.push(`#${head} colour`);
      }
    }
    return bad;
  });
  check(!liveBad.length, `${tag}rainbow (moving): the 3D follows every look it is sent`, liveBad.slice(0, 6).join(", "));
  await shot(`${tag}06-rainbow`);
  await act("stop_fx");

  console.log("7. Blackout and back");
  await click("#bo-btn", "BLACKOUT");
  ({ F, V } = await layers("blackout"));
  const stillLit = Object.entries(V).filter(([, v]) => v.level > 0.01).map(([h]) => +h).filter((h) => !lampOnly(h));
  check(!stillLit.length, `${tag}blackout: nothing is lit in 3D`, stillLit.slice(0, 10).map(name).join(", "));
  await click("#bo-btn", "BLACKOUT off");
  ({ F, V } = await layers("after blackout"));
  check(lights(V).some((h) => F[h] && +F[h].a > 0.5), `${tag}after blackout the look comes back`);

  console.log("8. Record a cue, clear, GO");
  await act("record_cue", { playback: 1, name: "Big look" });
  await click('#prog-tabs [data-tab="intensity"]', "Level tab");
  // (recording empties the programmer: Clear is then rightly greyed out)
  if (await p.evaluate(() => !document.querySelector("#prog-clear").disabled)) await click("#prog-clear", "Clear");
  ({ F, V } = await layers("cleared"));
  await click('#pb-mode [data-mode="faders"]', "Faders");
  await p.evaluate(() => fetch("/api/console", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "playback_level", params: { playback: 1, level: 100 } }) }));
  await click("#pb-strip .pb .go", "GO");
  ({ F, V } = await layers("cue GO", 2500));
  check(lights(V).filter((h) => F[h] && +F[h].a > 0.5).length > lights(V).length / 2, `${tag}GO brings the recorded look back`);
  await shot(`${tag}08-go`);

  console.log("9. Undo from the keyboard");
  const undoDepth = () => p.evaluate(async () => (((await import("/app/store.js")).state.snap || {}).undo || {}).depth || 0);
  const depth = await undoDepth();
  await p.mouse.click(700, 400);                 // focus the page, not a field
  await p.keyboard.press("Control+z");
  await sleep(800);
  check(depth > 0 && await undoDepth() < depth, `${tag}Ctrl+Z undoes a step`, `${depth} -> ${await undoDepth()}`);
  await layers("after undo");

  if (BRANDS) await effects();
}

// the effects machines and lasers of a rig: the wire, the feed and the 3D
async function effects() {
  const V0 = await view();
  const sfx = Object.entries(V0).filter(([, v]) => v.cls !== "light").map(([h]) => +h);
  if (!sfx.length) return;
  console.log(`10. effects (${sfx.length})`);
  // what the page was sent (the live stream: it carries effects too)
  const sent = () => p.evaluate(async () => (await import("/app/store.js")).state.looks || {});
  const wire = async () => JSON.stringify(await getj(`api/console/channels?heads=${sfx.join(",")}`));
  await act("fx_kill");
  await act("clear_programmer");
  // disarmed: fire and laser do nothing; fog runs (it needs no ARM)
  const w0 = await wire();
  for (const a of ["fx_fire", "fx_laser"]) await act(a, { heads: sfx, down: true });
  let F, V;
  ({ V } = await layers("disarmed fire / laser"));
  F = await sent();
  const w1 = await wire();
  check(w0 === w1, `${tag}disarmed: fire / laser change nothing on the wire`);
  const lit = sfx.filter((h) => (F[h] && F[h].fx && (F[h].fx.fire || F[h].fx.laser)) || V[h].fan);
  check(!lit.length, `${tag}disarmed: nothing fires, no laser is drawn`, lit.map(name).join(", "));
  await act("fx_fire", { down: false });
  await act("fx_laser", { down: false });
  // armed: every machine shows what it does, in the feed and the drawing
  await act("fx_arm", { state: true });
  const can = (h, kind) => {
    const m = roles(h);
    if (kind === "laser") return V[h].cls === "laser";
    if (kind === "fog") return m.includes("fog");
    return m.includes("fx_fire");
  };
  for (const kind of ["fire", "fog", "laser"]) {
    const hs = sfx.filter((h) => can(h, kind));
    if (!hs.length) continue;
    const r = await act(`fx_${kind}`, kind === "fog" ? { heads: hs, level: 80, seconds: 20 } : { heads: hs, down: true, seconds: kind === "fire" ? 3 : undefined });
    check(r.ok, `${tag}armed: ${kind} fires`, r.error || "");
    await sleep(900);
    ({ V } = await layers(`armed ${kind}`, 300));
    F = await sent();
    const silent = hs.filter((h) => {
      const fx = (F[h] && F[h].fx) || {};
      return kind === "laser" ? !fx.laser : kind === "fog" ? !(fx.fog > 0) : !fx.fire;
    });
    check(!silent.length, `${tag}armed ${kind}: the feed shows it on`, silent.map(name).join(", "));
    const undrawn = hs.filter((h) => !silent.includes(h) && V[h].type && (kind === "laser" ? !V[h].fan : !V[h].puffs));
    check(!undrawn.length, `${tag}armed ${kind}: the 3D draws it`, undrawn.map((h) => `${name(h)} (${V[h].type})`).join(", "));
    await shot(`${tag}10-${kind}`);
  }
  await act("fx_kill");
  ({ V } = await layers("KILL FX", 600));
  F = await sent();
  const still = sfx.filter((h) => (F[h] && F[h].fx && (F[h].fx.fire || F[h].fx.laser || F[h].fx.fog > 0)) || V[h].fan);
  check(!still.length, `${tag}KILL FX: everything stops`, still.map(name).join(", "));
}
for (const rig of RIGS) {
  tag = rig.brand ? `${rig.brand}: ` : "";
  console.log(rig.brand ? `\n== ${rig.brand} (${rig.items.length} products)` : "building the rig");
  await build(rig);
  const before = fails.length;
  if (BRANDS) await fit(rig);
  await steps();
  if (rig.brand) console.log(`  ${rig.brand}: ${fails.length - before} failed`);
}
check(errors.length === 0, "no page errors", errors.slice(0, 3).join(" | "));
await browser.close();
const lines = [`# 3D vs desk, ${patch.length} lights - ${new Date().toISOString().slice(0, 16)}`, "", `${oks} ok, ${fails.length} failed`, "", ...fails.map((f) => "- " + f)];
writeFileSync(join(outDir, "report.md"), lines.join("\n"));
console.log(`\n${oks} ok, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL", f);
server.kill();
process.exit(fails.length ? 1 : 0);

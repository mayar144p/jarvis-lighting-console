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
//
// Runs its own server on a scratch data folder; your shows are untouched.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { RIG } from "./bigrig.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = process.argv[2] || join(tmpdir(), "vischeck");
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

console.log("building the rig");
await act("venue_template", { name: "club" });
for (const [src, key, query, qty] of RIG) {
  await post("api/fixtures/library/install", { src, key });
  const r = await act("add_heads", { query, qty });
  check(r.ok && (r.heads || []).length === qty, `${qty} x ${query} patched`, r.error || "");
}

// ----------------------------------------------------------- the browser
const browser = await chromium.launch({ executablePath: exe, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const p = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
p.on("pageerror", (e) => errors.push(String(e.message || e)));
p.on("console", (m) => { if (m.type() === "error" && !/favicon|Failed to load resource/.test(m.text())) errors.push("console: " + m.text().slice(0, 200)); });
await p.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.people", "0"); localStorage.setItem("jarvis.quality", "fast"); } catch { /* fine */ } });
await p.goto(BASE);
let patch = [];
for (let i = 0; i < 40 && !patch.length; i++) {
  await sleep(500);
  patch = await p.evaluate(async () => {
    const st = (await import("/app/store.js")).state;
    return ((st.snap && st.snap.patch) || []).map((h) => ({ head_no: h.head_no, model: h.model }));
  });
}
console.log(`  ${patch.length} lights`);
check(patch.length >= 120, "a rig of 120+ lights", String(patch.length));
for (let i = 0; i < 60; i++) {
  const n = await p.evaluate(() => (window.jarvisStage && window.jarvisStage.fixtures ? window.jarvisStage.fixtures.size : 0));
  if (n >= patch.length) break;
  await sleep(500);
}
check(await p.evaluate(() => window.jarvisStage.fixtures.size) === patch.length, "every light is in the 3D view",
  `${await p.evaluate(() => window.jarvisStage.fixtures.size)} of ${patch.length}`);

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
  const note = (what, detail) => { if (bad++ < 6) check(false, `${step}: ${what}`, detail); };
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
  check(bad === 0, `${step}: all ${Object.keys(V).length} lights - feed, 3D and drawing agree`, bad ? `${bad} mismatches` : "");
  return { F, V };
}
const click = async (sel, what) => {
  try { await p.click(sel, { timeout: 20000 }); return true; } catch (e) { check(false, `click ${what || sel}`, String(e.message).split("\n")[0]); return false; }
};
const clickText = (sel, text) => click(`${sel}:has-text("${text}")`, text);
const shot = (name) => p.screenshot({ path: join(outDir, name + ".png") });
const lights = (V) => Object.entries(V).filter(([, v]) => v.cls === "light").map(([h]) => +h);

// --------------------------------------------------------------- steps
await shot("01-start");
console.log("1. start: everything dark");
let { F, V } = await layers("start");
console.log("  covering:", await p.evaluate(() => [...document.querySelectorAll(".modal-scrim, .menu, .cmdbar-scrim:not(.hidden)")].map((x) => (x.innerText || "").slice(0, 120)).join(" | ")));
check(lights(V).every((h) => !(F[h] && +F[h].a > 0.01)), "start: no light is lit");

console.log("2. All -> Full");
await click('.sel-actions [data-act="select_all"]', "All");
await click('#prog-tabs [data-tab="intensity"]', "Level tab");
await click('#int-quick [data-level="100"]', "Full");
({ F, V } = await layers("Full"));
const dark = lights(V).filter((h) => !(F[h] && +F[h].a > 0.5));
check(!dark.length, "Full: every light is lit in the feed", dark.slice(0, 12).map((h) => `#${h} ${patch.find((x) => x.head_no === h)?.model || ""}`).join(", "));
await shot("02-full");

console.log("3. Colour: red");
await click('#prog-tabs [data-tab="colour"]', "Colour tab");
await click('#swatches button[title="Red"]', "Red swatch");
({ F, V } = await layers("red"));
const notRed = lights(V).filter((h) => { const f = F[h]; if (!f || !(+f.a > 0.05)) return false; const [r, g, b] = lin(f.hex); return !(r > 0.3 && r > g * 3 && r > b * 3); });
check(notRed.length <= 6, "red: the colour lights turn red in the feed", `${notRed.length} not red: ${notRed.slice(0, 10).map((h) => `#${h} ${F[h].hex}`).join(", ")}`);
await shot("03-red");

console.log("4. One light: blue (only it changes)");
const before = F;
await click('.sel-actions [data-act="clear_selection"]', "None");
const target = patch.find((x) => /Mega PAR/.test(x.model || "")) || patch[0];
const tno = target.head_no;
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
check(b1 > r1 * 3, `the picked light (#${tno}) is blue`, F[tno] && F[tno].hex);
const changed = lights(V).filter((h) => h !== tno && before[h] && F[h] && (before[h].hex !== F[h].hex || Math.abs(+before[h].a - +F[h].a) > 0.01));
check(!changed.length, "no other light changed", changed.slice(0, 10).map((h) => `#${h} ${before[h].hex}->${F[h].hex}`).join(", "));
await shot("04-one-blue");

console.log("5. Movers to the dance floor");
await click('.sel-actions [data-act="clear_selection"]', "None");
await clickText("#group-chips .chip", "Moving");
await click('#prog-tabs [data-tab="position"]', "Move tab");
const movers = Object.entries(V).filter(([, v]) => v.moving).map(([h]) => +h);
const pan0 = Object.fromEntries(movers.map((h) => [h, F[h] && F[h].pan]));
await clickText(".mv-spot", "Dance floor");
({ F, V } = await layers("aim at the floor", 4500));
const unmoved = movers.filter((h) => F[h] && F[h].pan === pan0[h] && F[h].tilt === (F[h].tilt));
check(movers.length > 0, "the rig has moving heads", String(movers.length));
check(unmoved.length < movers.length, "the movers aim somewhere new", `${unmoved.length} of ${movers.length} unchanged`);
await shot("05-floor");

console.log("6. Rainbow on everything");
await click('.sel-actions [data-act="select_all"]', "All");
await click('#prog-tabs [data-tab="fx"]', "FX tab");
await clickText(".fx-card", "Rainbow");
const f1 = await feed();
await sleep(900);
const f2 = await feed();
const moved = lights(V).filter((h) => f1[h] && f2[h] && f1[h].hex !== f2[h].hex);
check(moved.length > lights(V).length / 3, "the rainbow moves the colours over time", `${moved.length} of ${lights(V).length} changed`);
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
check(!liveBad.length, "rainbow (moving): the 3D follows every look it is sent", liveBad.slice(0, 6).join(", "));
await shot("06-rainbow");
await act("stop_fx");

console.log("7. Blackout and back");
await click("#bo-btn", "BLACKOUT");
({ F, V } = await layers("blackout"));
const stillLit = Object.entries(V).filter(([, v]) => v.level > 0.01).map(([h]) => +h);
check(!stillLit.length, "blackout: nothing is lit in 3D", stillLit.slice(0, 10).join(", "));
await click("#bo-btn", "BLACKOUT off");
({ F, V } = await layers("after blackout"));
check(lights(V).some((h) => F[h] && +F[h].a > 0.5), "after blackout the look comes back");

console.log("8. Record a cue, clear, GO");
await act("record_cue", { playback: 1, name: "Big look" });
await click('#prog-tabs [data-tab="intensity"]', "Level tab");
await click("#clear-btn", "Clear");
({ F, V } = await layers("cleared"));
await click('#pb-mode [data-mode="faders"]', "Faders");
await p.evaluate(() => fetch("/api/console", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "playback_level", params: { playback: 1, level: 100 } }) }));
await click("#pb-strip .pb .go", "GO");
({ F, V } = await layers("cue GO", 2500));
check(lights(V).filter((h) => F[h] && +F[h].a > 0.5).length > lights(V).length / 2, "GO brings the recorded look back");
await shot("08-go");

console.log("9. Undo from the keyboard");
const undoDepth = () => p.evaluate(async () => (((await import("/app/store.js")).state.snap || {}).undo || {}).depth || 0);
const depth = await undoDepth();
await p.mouse.click(700, 400);                 // focus the page, not a field
await p.keyboard.press("Control+z");
await sleep(800);
check(depth > 0 && await undoDepth() < depth, "Ctrl+Z undoes a step", `${depth} -> ${await undoDepth()}`);
await layers("after undo");

check(errors.length === 0, "no page errors", errors.slice(0, 3).join(" | "));
await browser.close();
const lines = [`# 3D vs desk, ${patch.length} lights - ${new Date().toISOString().slice(0, 16)}`, "", `${oks} ok, ${fails.length} failed`, "", ...fails.map((f) => "- " + f)];
writeFileSync(join(outDir, "report.md"), lines.join("\n"));
console.log(`\n${oks} ok, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL", f);
server.kill();
process.exit(fails.length ? 1 : 0);

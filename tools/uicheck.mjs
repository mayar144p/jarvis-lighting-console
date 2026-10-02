// The screen check: every part of the console, driven in a real browser
// the way an operator would - top bar, fixtures, every programmer tab, the
// 3D view and the venue editor, Faders / Buttons / Timeline, the Copilot,
// every dialog and Settings - at 1280, 1440 and 1920 px wide.
//
// After every step it audits what is on screen and fails on:
//   - a page error (an uncaught exception or a rejected promise)
//   - text spilling out of its box (a label wider or taller than the
//     control that holds it), or the page scrolling sideways
//   - a control too small to click comfortably (under 20 x 20 px)
//   - an empty panel or dialog, or "null" / "undefined" / "NaN" printed
// and saves a screenshot per step, to look through by eye.
// At 1440 it also presses every (safe) button of every tab and dialog.
//
//   node tools/uicheck.mjs [outDir] [--widths 1280,1440,1920] [--no-click] [--only regex] [--big]
//
// It starts its own server on a scratch fixture database and show folder,
// so your shows and patch are never touched.  Needs Playwright.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { RIG } from "./bigrig.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const outDir = args.find((a, i) => !a.startsWith("--") && !(i && args[i - 1].startsWith("--"))) || join(ROOT, "uicheck-out");
const WIDTHS = opt("--widths", "1280,1440,1920").split(",").map(Number);
const CLICK = !args.includes("--no-click");
// --only <regex>: run just the steps whose name matches (plus the start)
const ONLY = opt("--only", "") ? new RegExp(opt("--only", "")) : null;
// --big: the 124-light rig (tools/bigrig.mjs) instead of the small one
const BIG = args.includes("--big");
const PORT = +(process.env.UICHECK_PORT || 8811);
const BASE = `http://127.0.0.1:${PORT}/`;
mkdirSync(outDir, { recursive: true });

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const exe = process.env.CHROMIUM || (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);

// ------------------------------------------------------------- the server
const scratch = mkdtempSync(join(tmpdir(), "jarvis-ui-"));
const server = spawn(process.env.PYTHON || "python3", ["-m", "app.main"], {
  cwd: ROOT, stdio: ["ignore", "ignore", "pipe"],
  env: { ...process.env, PORT: String(PORT), CONSOLE_AUTORESTORE: "false", CONSOLE_AUTOSAVE: "false",
    FIXTURE_DB: join(scratch, "fixtures.db"), CONSOLE_SHOW_DIR: join(scratch, "shows"), DMX_HOST: "127.0.0.1" },
});
let serverErr = "";
server.stderr.on("data", (d) => { serverErr += d; if (serverErr.length > 20000) serverErr = serverErr.slice(-10000); });
const stop = () => { try { server.kill(); } catch { /* gone */ } };
process.on("exit", stop);
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE)).ok) break; } catch { /* not yet */ }
  if (i > 120) { console.error("server did not start\n" + serverErr); process.exit(2); }
  await new Promise((r) => setTimeout(r, 250));
}

const findings = [];
const seen = new Set();
const pageErrors = [];
function finding(width, step, kind, what, detail = "") {
  const key = `${kind}|${what}|${detail}`;
  if (seen.has(key)) return;
  seen.add(key);
  findings.push({ width, step, kind, what, detail });
}

// --------------------------------------------------------------- the audit
// Runs in the page: what is wrong with what is on screen right now.
const AUDIT = () => {
  const out = [];
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return null;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || +cs.opacity === 0) return null;
    // covered by a modal: only the top layer counts
    return r;
  };
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    const cls = [...el.classList].slice(0, 3);
    if (cls.length) s += "." + cls.join(".");
    const t = (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return t ? `${s} “${t}”` : s;
  };
  const clips = (el) => {
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.textOverflow === "ellipsis" || /hidden|clip|auto|scroll/.test(cs.overflowX + cs.overflowY)) return a;
    }
    return null;
  };
  const top = document.querySelector(".modal-scrim:last-of-type") || document.querySelector(".cmdbar-scrim:not(.hidden)") || null;
  const inScope = (el) => !top || top.contains(el) || el.closest(".menu, .toasts");
  // 1. text spilling out of its box
  for (const el of document.querySelectorAll("button, .chip, label, h2, h3, h4, th, .k, .tb-btn, .hud-btn, .qbtn b, .fx-card b, .knob-k, .tab-pane [class] > b, small, .out-state")) {
    if (!inScope(el)) continue;
    const r = vis(el);
    if (!r) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "inline" || cs.display === "contents") continue;
    if (!(el.innerText || "").trim()) continue;
    const overX = el.scrollWidth > el.clientWidth + 2 && !/hidden|clip|auto|scroll/.test(cs.overflowX) && cs.textOverflow !== "ellipsis";
    const overY = el.scrollHeight > el.clientHeight + 3 && !/hidden|clip|auto|scroll/.test(cs.overflowY) && el.clientHeight > 0 && cs.height !== "auto";
    if (overX || overY) out.push(["spill", name(el), `${el.scrollWidth}x${el.scrollHeight} in ${el.clientWidth}x${el.clientHeight}`]);
  }
  if (document.documentElement.scrollWidth > innerWidth + 1) out.push(["page-scrolls-sideways", "page", `${document.documentElement.scrollWidth} > ${innerWidth}`]);
  // 2. controls too small to click
  for (const el of document.querySelectorAll("button, select, input:not([type=hidden]):not([type=range]):not([type=file]), [role=button], [role=tab], a[href]")) {
    if (!inScope(el) || el.disabled) continue;
    // a checkbox inside its label: the whole label is the target
    const lab = /checkbox|radio/.test(el.type) && el.closest("label");
    const r = vis(lab || el);
    if (!r) continue;
    if (el.closest(".menu") && r.height >= 24) continue;
    if (r.width < 20 || r.height < 20) out.push(["tiny", name(el), `${Math.round(r.width)}x${Math.round(r.height)}`]);
  }
  // 3. empty panels and dialogs; words that mean a bug
  for (const el of document.querySelectorAll(".modal-body, .tab-pane:not([hidden]), .menu, .ctab:not([hidden])")) {
    if (!vis(el)) continue;
    const has = (el.innerText || "").trim() || el.querySelector("canvas, svg, input, select, textarea, img, video");
    if (!has) out.push(["empty", name(el), ""]);
  }
  const text = document.body.innerText;
  for (const bad of [/\bundefined\b/, /\bNaN\b/, /\[object Object\]/, /(^|\s)null(\s|$)/m]) {
    const m = text.match(bad);
    if (m) {
      const i = m.index;
      out.push(["bad-text", JSON.stringify(m[0].trim()), text.slice(Math.max(0, i - 40), i + 40).replace(/\s+/g, " ")]);
    }
  }
  // 4. a control that runs outside the window
  for (const el of document.querySelectorAll("button, .chip, select, input")) {
    if (!inScope(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || getComputedStyle(el).visibility === "hidden") continue;
    if ((r.right > innerWidth + 1 || r.left < -1) && !clips(el)) out.push(["off-screen", name(el), `x ${Math.round(r.left)}..${Math.round(r.right)}`]);
  }
  return out;
};

// ------------------------------------------------------------------ helpers
async function setup(p) {
  const post = (action, params = {}) => p.evaluate(({ action, params }) => fetch("/api/console", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, params }) })
    .then((r) => r.json()).then((j) => j.result || j), { action, params });
  const install = (src, key) => p.evaluate(({ src, key }) => fetch("/api/fixtures/library/install", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ src, key }) }).then((r) => r.json()), { src, key });
  return { post, install };
}

async function buildRig(p) {
  const { post, install } = await setup(p);
  await post("patch_clear");
  await post("venue_template", { name: "club" });
  if (BIG) {
    for (const [src, key, query, qty] of RIG) {
      await install(src, key);
      const r = await post("add_heads", { query, qty });
      if (!r.ok) finding(0, "setup", "setup", `add_heads ${query}`, r.error || "");
    }
    await post("select_all");
    await post("set_intensity", { level: 100 });
    await post("record_cue", { playback: 1 });
    await post("quick_defaults", {});
    await post("clear_programmer");
    return;
  }
  for (const [src, key] of [["qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"], ["qlc", "Laserworld/Laserworld-RS400G.qxf"],
    ["ofl", "stairville/af-180-led-fogger.json"], ["qlc", "Showtec/Showtec-Pixel-Bar-12.qxf"], ["qlc", "Nicols/Nicols-Moover-Spot-120.qxf"]]) {
    await install(src, key);
  }
  const add = async (query, qty) => {
    const r = await post("add_heads", { query, qty });
    if (!r.ok) finding(0, "setup", "setup", `add_heads ${query}`, r.error || "");
  };
  await add("LED PAR 4ch", 6);
  await add("Moving Head Spot 16ch", 4);
  await add("Intimidator Wave 360", 1);
  await add("Moover Spot 120", 2);
  await add("RGBW Bar 12ch", 2);
  await add("Pixel Bar 12", 1);
  await add("RS400G", 1);
  await add("AF-180 LED Fogger", 1);
  await post("select_all");
  await post("set_intensity", { level: 100 });
  await post("set_colour", { colour: "#3366ff" });
  await post("record_cue", { playback: 1 });
  await post("set_colour", { colour: "#ff3300" });
  await post("record_cue", { playback: 1 });
  await post("quick_defaults", {});
  await post("clear_programmer");
}

async function closeAll(p) {
  for (let i = 0; i < 4; i++) {
    const n = await p.evaluate(() => document.querySelectorAll(".modal-scrim, .menu").length);
    if (!n) break;
    await p.keyboard.press("Escape");
    await p.waitForTimeout(120);
  }
  await p.evaluate(() => {
    for (const x of document.querySelectorAll(".modal-scrim .icon-x")) x.click();
    for (const m of document.querySelectorAll(".menu")) m.remove();
  });
  await p.waitForTimeout(80);
}

const mod = (p, file, fn, ...a) => p.evaluate(async ({ file, fn, a }) => {
  const m = await import("/app/" + file);
  const st = (await import("/app/store.js")).state;
  const hd = (st.snap && st.snap.patch || []).find((x) => (x.map || []).includes("pan")) || (st.snap && st.snap.patch || [])[0];
  const args = a.map((x) => (x === "$hd" ? hd : x === "$heads" ? [hd && hd.head_no] : x));
  // not awaited: some openers resolve only when their dialog closes
  Promise.resolve(m[fn](...args)).catch((e) => console.error("opener " + fn + ": " + (e && e.message || e)));
  await new Promise((r) => setTimeout(r, 300));
}, { file, fn, a });

// Never pressed by the click-through: they lose the test rig or leave
// the page (they are covered by their own tests).
const DANGER = /delete|remove|clear|reset|new show|^load|open show|blackout|black out|go live|kill|unpatch|forget|wipe|import|export|download|upload|restart|update|quit|patch …|replace|discard|^save|save as|overwrite|turn it off|release all|log ?in|sign|connect|scan|rdm|send|attach|operator|take over|run checks|design the show|design concepts|floor plan|full screen|fullscreen|⛶|×|close|cancel|done|lock|undo|redo/i;

async function clickAll(p, width, step, scope) {
  const total = await p.evaluate((scope) => {
    const root = document.querySelector(scope);
    return root ? root.querySelectorAll("button").length : 0;
  }, scope);
  let pressed = 0;
  for (let i = 0; i < Math.min(total, 80); i++) {
    const info = await p.evaluate(({ scope, i, src }) => {
      const DANGER = new RegExp(src, "i");
      const root = document.querySelector(scope);
      const b = root && root.querySelectorAll("button")[i];
      if (!b || b.disabled) return null;
      const r = b.getBoundingClientRect();
      if (r.width < 1 || r.height < 1 || getComputedStyle(b).visibility === "hidden") return null;
      const t = (b.innerText || b.getAttribute("aria-label") || b.title || "").trim();
      if (!t || DANGER.test(t) || DANGER.test(b.title || "")) return null;
      b.setAttribute("data-uic", String(i));
      return t.slice(0, 40);
    }, { scope, i, src: DANGER.source });
    if (!info) continue;
    const before = pageErrors.length;
    try {
      await p.click(`[data-uic="${i}"]`, { timeout: 1500 });
      pressed++;
    } catch (e) {
      // covered by something, or gone: not an error of the button itself
      if (process.env.UICHECK_DEBUG) console.log("    could not press", info, String(e.message).split("\n").slice(0, 6).join(" | "));
    }
    await p.waitForTimeout(160);
    if (pageErrors.length > before) finding(width, step, "page-error", `pressing “${info}” in ${scope}`, pageErrors.at(-1));
    await closeAll(p);
    await p.evaluate(() => document.querySelectorAll("[data-uic]").forEach((x) => x.removeAttribute("data-uic")));
  }
  return pressed;
}

// ------------------------------------------------------------------- steps
// Each: [name, async (p) => {...}, {click: scope}]
const tab = (t) => async (p) => {
  await p.click(`#prog-tabs [data-tab="${t}"]`, { timeout: 3000 });
  await p.waitForTimeout(500);
};
const sel = (heads) => async (p) => {
  const { post } = await setup(p);
  await post("select_heads", { heads });
  await p.waitForTimeout(500);
};
// light numbers per rig: movers, a Wave 360, two of a colour-wheel spot,
// a pixel bar, the laser, the fogger
const H = BIG
  ? { movers: [1, 2, 3, 4], wave: [25], wheel: [27, 28], pix: [97], laser: [121], sfx: [123], teach: [27], few: [31, 32, 33, 34] }
  : { movers: [7, 8, 9, 10], wave: [11], wheel: [12, 13], pix: [16], laser: [17], sfx: [18], teach: [12], few: [1, 2, 3, 4] };
const STEPS = [
  ["01-start", async () => {}],
  ["02-select-all", async (p) => { const { post } = await setup(p); await post("select_all"); await p.waitForTimeout(600); }],
  ["03-level", tab("intensity"), { click: '.tab-pane[data-pane="intensity"]' }],
  ["04-colour", tab("colour"), { click: '.tab-pane[data-pane="colour"]' }],
  ["05-beam", tab("beam"), { click: '.tab-pane[data-pane="beam"]' }],
  ["06-fx", tab("fx"), { click: '.tab-pane[data-pane="fx"]' }],
  ["07-looks", tab("looks"), { click: '.tab-pane[data-pane="looks"]' }],
  ["08-setup", tab("tools"), { click: '.tab-pane[data-pane="tools"]' }],
  ["09-movers", sel(H.movers)],
  ["10-move", tab("position"), { click: '.tab-pane[data-pane="position"]' }],
  ["11-wave360-move", async (p) => { await sel(H.wave)(p); await tab("position")(p); }],
  ["12-moover-colour", async (p) => { await sel(H.wheel)(p); await tab("colour")(p); }],
  ["13-pixelbar-fx", async (p) => { await sel(H.pix)(p); await tab("fx")(p); }],
  ["14-laser", async (p) => { await sel(H.laser)(p); await p.click('#prog-tabs [data-tab="laser"]', { timeout: 3000 }); await p.waitForTimeout(500); }, { click: '.tab-pane[data-pane="laser"]' }],
  ["15-sfx", async (p) => { await sel(H.sfx)(p); await p.click('#prog-tabs [data-tab="sfx"]', { timeout: 3000 }); await p.waitForTimeout(500); }, { click: '.tab-pane[data-pane="sfx"]' }],
  ["16-faders", async (p) => { await sel(H.few.slice(0, 3))(p); await p.click('#pb-mode [data-mode="faders"]'); await p.waitForTimeout(500); }, { click: "#pb-strip" }],
  ["17-buttons", async (p) => { await p.click('#pb-mode [data-mode="buttons"]'); await p.waitForTimeout(600); }],
  ["18-buttons-edit", async (p) => { await p.click("#qb-edit"); await p.waitForTimeout(500); }],
  ["19-timeline", async (p) => { await p.click("#qb-edit"); await p.click('#pb-mode [data-mode="timeline"]'); await p.waitForTimeout(700); }, { click: "#tl" }],
  ["20-fixtures-menus", async (p) => { await p.click('#pb-mode [data-mode="faders"]'); await p.click("#fx-more"); await p.waitForTimeout(300); }],
  ["21-show-menu", async (p) => { await closeAll(p); await p.click("#show-btn"); await p.waitForTimeout(400); }],
  ["22-tempo-menu", async (p) => { await closeAll(p); if (await p.isVisible("#tempo-more")) await p.click("#tempo-more"); await p.waitForTimeout(300); }],
  ["23-views-menu", async (p) => { await closeAll(p); await p.click("#views-more"); await p.waitForTimeout(300); }],
  ["24-crowd-menu", async (p) => { await closeAll(p); await p.click("#people-btn"); await p.waitForTimeout(300); }],
  ["25-zones", async (p) => { await closeAll(p); await p.click("#zones-btn"); await p.waitForTimeout(500); }],
  ["26-arrange", async (p) => { await closeAll(p); await p.click("#zones-btn").catch(() => {}); if (!(await p.evaluate(() => document.body.classList.contains("arranging")))) await p.click("#arrange-btn"); await p.waitForTimeout(700); }, { click: "#venue-tools" }],
  ["27-room-dialog", async (p) => { await closeAll(p); await p.click("#vt-room"); await p.waitForTimeout(600); }, { click: ".modal-scrim:last-of-type .modal" }],
  ["28-rigging-dialog", async (p) => { await closeAll(p); await p.click("#vt-rigging"); await p.waitForTimeout(600); }, { click: ".modal-scrim:last-of-type .modal" }],
  ["29-more-dialog", async (p) => { await closeAll(p); await p.click("#vt-add"); await p.waitForTimeout(500); }],
  ["30-venues-menu", async (p) => { await closeAll(p); await p.click("#vt-venues"); await p.waitForTimeout(500); }],
  ["31-arrange-done", async (p) => { await closeAll(p); await p.click("#vt-done"); await p.waitForTimeout(500); }],
  ["32-add-dialog", async (p) => { await p.click("#add-btn"); await p.waitForTimeout(800); }, { click: ".modal-scrim:last-of-type .modal" }],
  ["33-settings", async (p) => { await closeAll(p); await p.click("#settings-btn"); await p.waitForTimeout(900); }, { click: ".modal-scrim:last-of-type .modal" }],
  ["34-help", async (p) => { await closeAll(p); await p.click("#help-btn"); await p.waitForTimeout(500); }],
  ["35-cmdbar", async (p) => { await closeAll(p); await p.click("#palette-btn"); await p.waitForTimeout(400); await p.keyboard.type("red"); await p.waitForTimeout(500); }],
  ["36-copilot", async (p) => { await p.keyboard.press("Escape"); await closeAll(p); await p.click("#ai-btn"); await p.waitForTimeout(600); }],
  ["37-copilot-design", async (p) => { await p.click('#copilot-tabs [data-ctab="design"]'); await p.waitForTimeout(400); }],
  ["38-copilot-doctor", async (p) => { await p.click('#copilot-tabs [data-ctab="doctor"]'); await p.waitForTimeout(400); }],
  // "What I remember" is shown only with an AI key
  ["39-ai-memory", async (p) => { await p.click('#copilot-tabs [data-ctab="chat"]'); if (await p.isVisible("#ai-memory")) await p.click("#ai-memory"); await p.waitForTimeout(500); }],
  ["40-copilot-close", async (p) => { await closeAll(p); await p.click("#copilot-close"); await p.waitForTimeout(400); }],
  ["41-cue-dialog", async (p) => mod(p, "dialogs.js", "openCueDialog", 1), { click: ".modal-scrim:last-of-type .modal" }],
  ["42-cue-list", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openCueList", 1); }, { click: ".modal-scrim:last-of-type .modal" }],
  ["43-ready-check", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openReadyCheck"); await p.waitForTimeout(600); }],
  ["44-dmx-map", async (p) => { await closeAll(p); await mod(p, "fixtures.js", "openDmxMap"); await p.waitForTimeout(500); }],
  ["45-channels", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openChannels", "$heads"); await p.waitForTimeout(500); }],
  ["46-profile-editor", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openProfileEditor", "$hd"); await p.waitForTimeout(500); }],
  ["47-light-test", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openLightTest", "$hd"); await p.waitForTimeout(500); }],
  ["48-motion-cal", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openMotionCalibration", "$hd"); await p.waitForTimeout(500); }],
  ["49-csv-import", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openCsvImport"); }],
  ["50-manual-fixture", async (p) => { await closeAll(p); await mod(p, "dialogs.js", "openManualFixture"); }],
  ["51-macros", async (p) => { await closeAll(p); await mod(p, "macros.js", "openMacros"); await p.waitForTimeout(400); }, { click: ".modal-scrim:last-of-type .modal" }],
  ["52-node-monitor", async (p) => { await closeAll(p); await mod(p, "monitors.js", "openNodeMonitor"); await p.waitForTimeout(600); }],
  ["53-midi-monitor", async (p) => { await closeAll(p); await mod(p, "monitors.js", "openMidiMonitor"); await p.waitForTimeout(400); }],
  ["54-sound", async (p) => { await closeAll(p); await mod(p, "sounddialog.js", "openSoundDialog"); await p.waitForTimeout(500); }],
  ["55-step-editor", async (p) => { await closeAll(p); await sel(H.few)(p); await mod(p, "stepfx.js", "openStepEditor"); await p.waitForTimeout(400); }],
  ["56-shape-editor", async (p) => { await closeAll(p); await mod(p, "shapeeditor.js", "openShapeEditor"); await p.waitForTimeout(400); }],
  ["57-teach-wheel", async (p) => { await closeAll(p); await sel(H.teach)(p); await mod(p, "teachwheel.js", "openTeachWheel", "wheel"); await p.waitForTimeout(400); }],
  ["58-colour-match", async (p) => { await closeAll(p); await mod(p, "colourmatch.js", "openColourMatch"); await p.waitForTimeout(400); }],
  ["59-autopilot", async (p) => { await closeAll(p); await mod(p, "autopilot.js", "openAutopilot"); await p.waitForTimeout(400); }],
  ["60-rig-dialog", async (p) => { await closeAll(p); await mod(p, "rigdialog.js", "openRigDialog", "add"); await p.waitForTimeout(500); }],
  ["61-gig-buttons-full", async (p) => { await closeAll(p); await p.click('#pb-mode [data-mode="buttons"]'); await p.click("#qb-full"); await p.waitForTimeout(600); }],
  ["62-stage-full", async (p) => { await p.click("#qb-full"); await p.waitForTimeout(300); await p.click("#fullscreen-btn"); await p.waitForTimeout(600); }],
  ["63-end", async (p) => { await p.keyboard.press("Escape"); await closeAll(p); await p.waitForTimeout(300); }],
];

// ---------------------------------------------------------------- the run
const browser = await chromium.launch({ executablePath: exe, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
let rigBuilt = false;
for (const width of WIDTHS) {
  const height = width >= 1900 ? 1080 : width >= 1440 ? 900 : 800;
  const p = await browser.newPage({ viewport: { width, height } });
  p.on("pageerror", (e) => { pageErrors.push(String(e.message || e)); });
  p.on("console", (m) => { if (m.type() === "error" && !/favicon|Failed to load resource/.test(m.text())) pageErrors.push("console: " + m.text().slice(0, 300)); });
  await p.addInitScript((big) => {
    try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.people", "0"); localStorage.removeItem("jarvis.bottom"); if (big) localStorage.setItem("jarvis.quality", "fast"); } catch { /* fine */ }
  }, BIG);
  await p.goto(BASE);
  await p.waitForTimeout(1500);
  if (!rigBuilt) { await buildRig(p); rigBuilt = true; await p.reload(); await p.waitForTimeout(1800); }
  const dir = join(outDir, String(width));
  mkdirSync(dir, { recursive: true });
  for (const [name, run, o = {}] of STEPS) {
    if (ONLY && name !== "01-start" && !ONLY.test(name)) continue;
    const before = pageErrors.length;
    try {
      await Promise.race([run(p), new Promise((_, no) => setTimeout(() => no(new Error("step took over 20 s")), 20000))]);
    } catch (e) {
      finding(width, name, "step-failed", name, String(e.message || e).split("\n")[0].slice(0, 200));
    }
    await p.waitForTimeout(250);
    for (const [kind, what, detail] of await p.evaluate(AUDIT)) finding(width, name, kind, what, detail);
    for (const e of pageErrors.slice(before)) finding(width, name, "page-error", name, e);
    await p.screenshot({ path: join(dir, name + ".png") });
    if (CLICK && o.click && width === 1440) {
      const n = await clickAll(p, width, name, o.click);
      console.log(`  ${width} ${name}: pressed ${n} buttons`);
      // pressing may have changed the tab; put the step back
      try { await run(p); } catch { /* fine */ }
    }
  }
  await p.close();
}
await browser.close();
stop();

// ----------------------------------------------------------------- report
const kinds = {};
for (const f of findings) (kinds[f.kind] ||= []).push(f);
const lines = [`# Screen check - ${new Date().toISOString().slice(0, 16)}`, "",
  `Widths: ${WIDTHS.join(", ")} · ${STEPS.length} steps each · ${findings.length} findings`, ""];
for (const [k, list] of Object.entries(kinds)) {
  lines.push(`## ${k} (${list.length})`, "");
  for (const f of list) lines.push(`- [${f.width} ${f.step}] ${f.what}${f.detail ? " - " + f.detail : ""}`);
  lines.push("");
}
writeFileSync(join(outDir, "report.md"), lines.join("\n"));
writeFileSync(join(outDir, "findings.json"), JSON.stringify(findings, null, 1));
console.log(lines.slice(0, 3).join("\n"));
for (const [k, list] of Object.entries(kinds)) console.log(`  ${k}: ${list.length}`);
console.log(`report: ${join(outDir, "report.md")}`);
const hard = findings.filter((f) => ["page-error", "step-failed", "empty", "bad-text"].includes(f.kind));
process.exit(hard.length ? 1 : 0);

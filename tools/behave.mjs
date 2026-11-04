// What the screens DO, in a real browser on scratch data - the behaviour,
// not the source text: each part opens the real desk, clicks what an
// operator clicks, and checks what they'd see and what the desk did.
//
//   node tools/behave.mjs            (CI runs it with the screen check)
//
// Replaces the selftest checks that read a .js file for a word: those pass
// when the word is there and the screen is broken.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.BEHAVE_PORT || 8871), BASE = `http://127.0.0.1:${PORT}/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + String(extra).slice(0, 300))); if (!ok) fails++; };

try { await fetch(BASE); console.log(`something is already on port ${PORT} - stop it (an earlier run?) or set BEHAVE_PORT`); process.exit(2); } catch { /* free */ }
const DATA = mkdtempSync(join(tmpdir(), "haze-behave-"));
// the installed app's update check, as if a newer Haze were already found
writeFileSync(join(DATA, "update_check.json"), JSON.stringify({ checked: Date.now() / 1000, available: true, latest: "9.9.0",
  url: "https://example.invalid/Haze.exe", page: "https://example.invalid/r", notes: "test" }));
const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: DATA, CONSOLE_AUTOSAVE: "false", DMX_HOST: "127.0.0.1",
    AUTO_UPDATE: "false", HAZE_VERSION: "0.1.0", HAZE_PACKED: "1" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
// a step that throws still stops the desk (a left-over desk on the port answered the next run)
process.on("uncaughtException", (e) => { console.log("  FAIL", e.message.split("\n")[0]); done(1); });
process.on("unhandledRejection", (e) => { console.log("  FAIL", String(e && e.message || e).split("\n")[0]); done(1); });
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const post = (u, b) => fetch(BASE + u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) }).then((r) => r.json());
const act = (a, params = {}) => post("api/console", { action: a, params }).then((j) => j.result || j);
const snap = () => fetch(BASE + "api/console").then((r) => r.json()).then((j) => j.state || j);

await act("venue_template", { name: "lounge" });
await act("add_heads", { query: "moving head", qty: 2 });
await act("add_heads", { query: "LED PAR", qty: 4 });
await act("select_heads", { heads: [3, 4] }); await act("set_intensity", { level: 70 }); await act("record_cue", { playback: 1 });
await act("clear_programmer");

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const p = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.addInitScript(() => { try { sessionStorage.setItem("haze.venuepick", "1"); localStorage.setItem("haze.noLockAsk", "1"); } catch (e) { /* fine */ } });
await p.goto(BASE); await sleep(3500);
const closeModals = async () => { for (const x of await p.locator(".modal .icon-x").all()) await x.click().catch(() => {}); await sleep(300); };

// -- Line up: two marks where the light plays, every step shown -------------
console.log("Line up");
await p.evaluate(() => import("/app/lineup.js").then((m) => m.openLineUp([1])));
await sleep(1500);
const steps = async () => p.locator(".lineup-step").allInnerTexts();
let s0 = await steps();
check("both marks show as steps before the first press", s0.length === 3 && !/bar|vip/i.test(s0.join(" ")), s0.join(" | "));
const title0 = await p.locator(".lineup-title").innerText();
await p.locator(".lineup-go").click(); await sleep(1500);
const s1 = await steps(), title1 = await p.locator(".lineup-title").innerText();
check("after the first press: mark 1 ticked, the second named, the box says so",
  s1[0].startsWith("✓") && title1 !== title0 && /Mark 1 kept/.test(await p.locator(".lineup-said").innerText()), `${s1} / ${title1}`);
await p.locator(".lineup-go").click(); await sleep(1500);
check("after the second: lined up, and the light knows it", /lined up/i.test(await p.locator(".lineup-title").innerText())
  && !!((await snap()).patch || []).find((h) => h.head_no === 1 && h.align), "");
await closeModals();

// -- the Copilot's first look ------------------------------------------------
console.log("Copilot");
await p.evaluate(() => import("/app/copilot.js").then((m) => (m.openCopilot || m.toggleCopilot || (() => {}))()));
await sleep(800);
const opened = await p.locator("#ai-suggest").count();
if (opened) {
  const sug = await p.locator("#ai-suggest").innerText().catch(() => "");
  check("it opens with what it can do and suggestions for this desk", /I can run the whole desk/.test(sug) && (await p.locator("#ai-suggest .chip").count()) >= 3, sug.slice(0, 120));
  await p.locator("#ai-suggest .chip").first().click();
  check("a suggestion tapped is put in the box, not sent", ((await p.locator("#ai-text").inputValue()) || "").length > 5, "");
  const pb = await p.locator("#auto-pb option").allInnerTexts();
  check("'Put its cues on' says which faders are empty and which would be replaced",
    pb.some((t) => /Fader 1 .*replaced/.test(t)) && pb.some((t) => /empty/.test(t)), pb.slice(0, 3).join(" | "));
}

// -- the problems list: click one to see it in full, with its fix --------------
console.log("Problems");
await p.evaluate(() => import("/app/api.js").then((m) => m.noteError("/api/console/autoshow: Designing a whole show needs the AI: Settings -> AI (a key, or the offline AI)")));
await sleep(300);
await p.locator("#err-btn").click(); await sleep(300);
await p.locator(".menu button, .menu .menu-item").filter({ hasText: "Designing a whole show" }).first().click(); await sleep(500);
const m = await p.locator(".modal").last().innerText();
check("a problem opens in full, in words, with the fix", /What happened/.test(m) && /AI isn't set up/.test(m) && /Set up the AI/.test(m), m.slice(0, 200));
await closeModals();

// -- What the AI has learned -----------------------------------------------------
console.log("What the AI has learned");
await p.evaluate(() => import("/app/ailearning.js").then((m) => m.openLearning())); await sleep(1000);
await p.locator(".ai-learn input.input").fill("warm white for speeches");
await p.locator(".ai-learn button", { hasText: "Add" }).click(); await sleep(800);
check("a note typed in is kept and shown", /warm white for speeches/.test(await p.locator(".ai-learn").innerText()), "");
await p.locator(".ai-learn input[type=checkbox]").first().check(); await sleep(600);
check("Pause learning reaches the desk", (await post("api/console/assistant", { learning: true })).paused === true, "");
await p.locator(".ai-learn-row button", { hasText: "Forget" }).first().click(); await sleep(600);
check("Forget takes the note away", !(await post("api/console/assistant", { learning: true })).notes.length, "");
await closeModals();

// -- Settings: strobe safety; the update check ---------------------------------
console.log("Settings");
await p.evaluate(() => import("/app/settings.js").then((m) => m.openSettingsAt("output"))); await sleep(1200);
const strobe = p.locator("label.check", { hasText: "Strobes at most 3 flashes" }).locator("input");
check("the strobe safety switch is there", (await strobe.count()) === 1, "");
if (await strobe.count()) {
  await strobe.check(); await sleep(800);
  check("...and on, the desk caps every strobe at 3 a second", +((await snap()).strobe_cap || 0) === 3, "");
}
await closeModals();
check("a newer Haze shows by the name", /Haze 9\.9\.0 is out/.test(await p.locator(".update-pill").innerText().catch(() => "")), "");

// -- talking with the copilot (Google's side stood in for) ----------------------
console.log("Talking with the copilot");
await p.route("**/api/console/assistant", async (route) => {
  const b = JSON.parse(route.request().postData() || "{}");
  if (b.live_voice) return route.fulfill({ json: { ok: true, ws: "wss://stand-in.invalid/live", setup: { setup: { model: "models/x" } } } });
  if (b.session === "voice") return route.fulfill({ json: { ok: true, reply: "Amber on the VIP.", steps: [{ summary: "colour amber" }] } });
  return route.continue();
});
await p.evaluate(() => {
  window.__sent = [];
  // the microphone: a quiet tone; Google: a stand-in that hears, asks the desk, answers
  navigator.mediaDevices.getUserMedia = async () => { const c = new AudioContext(); const o = c.createOscillator(); const d = c.createMediaStreamDestination(); o.connect(d); o.start(); return d.stream; };
  window.WebSocket = class {
    constructor(url) { this.url = url; this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 50); }
    send(x) {
      const m = JSON.parse(x); window.__sent.push(Object.keys(m)[0]);
      const say = (o) => setTimeout(() => this.onmessage && this.onmessage({ data: JSON.stringify(o) }), 30);
      if (m.setup) {
        say({ setupComplete: {} });
        say({ serverContent: { inputTranscription: { text: "warm on the VIP please" } } });
        say({ toolCall: { functionCalls: [{ id: "c1", name: "do_on_desk", args: { request: "warm on the VIP please" } }] } });
      }
      if (m.toolResponse) { say({ serverContent: { outputTranscription: { text: "Done - amber on the VIP." } } }); say({ serverContent: { turnComplete: true } }); }
    }
    close() { this.readyState = 3; }
  };
  return import("/app/store.js").then((s) => { if (s.state.status) s.state.status.llm_configured = true; });
});
await p.evaluate(() => import("/app/copilot.js").then((m) => m.openCopilot()));
await p.locator("#ai-talk").click(); await sleep(2500);
const log = await p.locator("#ai-log").innerText();
const sent = await p.evaluate(() => window.__sent);
check("a talk: what you said, what the desk did, and its answer show in the conversation",
  /warm on the VIP please/.test(log) && /Amber on the VIP/.test(log) && /Done - amber on the VIP/.test(log), log.slice(-300));
check("...the voice got the desk's answer back, and the microphone is going out",
  sent.includes("toolResponse") && sent.includes("realtimeInput"), sent.join(","));
await p.locator("#ai-talk").click(); await sleep(300);
check("...and Stop talking ends it", /Talk/.test(await p.locator("#ai-talk").innerText()) && !/Stop/.test(await p.locator("#ai-talk").innerText()), "");
await p.unroute("**/api/console/assistant");

// -- a sparkle machine: its own Sparks section, its heights by name, a shot at the one picked --
console.log("Sparkle machine");
await p.evaluate(() => import("/app/copilot.js").then((m) => m.closeCopilot())); await closeModals();
const ins = await post("api/fixtures/library/install", { src: "ofl", key: "showven/sparkular.json" });
const sp = (await act("add_heads", { fixture_id: ins.fixture.id, qty: 1 })).heads[0];
await act("select_heads", { heads: [sp] }); await sleep(1200);
await p.locator('[data-tab="sfx"]:visible').first().click(); await sleep(800);
const pane = await p.locator("#sfx-pane").innerText();
check("it has its own Sparks section with Height 1 to Height 10 (not 'Height' ten times)",
  /\bSparks\b/.test(pane) && /Height 1\b/.test(pane) && /Height 10/.test(pane) && !/CO2 \/ flames/.test(pane), pane.slice(0, 200));
await act("fx_arm", { state: true }); await sleep(600);
await p.locator("#sfx-pane .chip", { hasText: /^Height 10$/ }).click();
await p.locator("#sfx-pane .chip", { hasText: /^3 s$/ }).click(); await sleep(400);
const rep = await fetch(BASE + "api/console/channels?heads=" + sp).then((r) => r.json());
const fountain = (((rep.heads || [])[0] || {}).channels || []).find((c) => c.role === "fx_fire") || {};
check("a quick shot after picking Height 10 fires at Height 10 (232-255), not the lowest", fountain.value >= 232, JSON.stringify(fountain));
await act("fx_kill");

// -- how do I...?  answered from the guide (no AI key here), nothing changed --
console.log("How do I...?");
const rigBefore = JSON.stringify(((await snap()).patch || []).map((x) => [x.head_no, x.x, x.y, x.z]));
await p.evaluate(() => import("/app/copilot.js").then((m) => m.openCopilot())); await sleep(600);
await p.locator("#ai-text").fill("help me align the lights");
await p.locator("#ai-send").click(); await sleep(1500);
const ans = await p.locator(".guide-answer").last().innerText().catch(() => "");
check("'help me align the lights' gets the guide's line-up steps, with the real button names",
  /Line up with the real lights/.test(ans) && /mark/i.test(ans) && !/\*\*/.test(ans), ans.slice(0, 200));
check("...and nothing on the rig changed", JSON.stringify(((await snap()).patch || []).map((x) => [x.head_no, x.x, x.y, x.z])) === rigBefore, "");
await p.evaluate(() => import("/app/copilot.js").then((m) => m.closeCopilot()));

check("no page errors", !errors.length, errors.join(" | "));
await browser.close();
console.log(fails ? `${fails} failed` : "all ok");
done(fails ? 1 : 0);

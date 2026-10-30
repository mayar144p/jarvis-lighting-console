// Show safety: what the UI/UX review found could go wrong at a show, in a
// real browser on scratch data.  A typed number + Enter never fires GO;
// "go live" typed is Go live, not a cue GO; a tap can't stop the output
// (Hold to stop); X can't take a blackout off; Live keeps GO on screen; the
// programmer's tabs say what they are; the playback faders and options are
// big enough for a finger.
//
//   node tools/safetycheck.mjs
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.SAFETYCHECK_PORT || 8862), BASE = `http://127.0.0.1:${PORT}/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + extra)); if (!ok) fails++; };

const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: mkdtempSync(join(tmpdir(), "jarvis-safety-")),
    // real output (not simulated) so Live / Hold to stop are real - to this
    // computer only (127.0.0.1): nothing leaves it
    CONSOLE_AUTOSAVE: "false", CONSOLE_DRY_RUN: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const act = (a, params = {}) => fetch(BASE + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: a, params }) }).then((r) => r.json()).then((j) => j.result || j);
const status = () => fetch(BASE + "api/console").then((r) => r.json());

await act("add_heads", { query: "moving head spot", qty: 4 });
await act("add_heads", { query: "LED PAR", qty: 4 });
await act("select_heads", { heads: [1, 2] }); await act("set_intensity", { level: 60 }); await act("record_cue", { playback: 1 });
await act("select_heads", { heads: [5, 6] }); await act("set_intensity", { level: 80 }); await act("record_cue", { playback: 2 });

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const p = await (await browser.newContext({ viewport: { width: 1366, height: 768 } })).newPage();
const errs = [];
p.on("pageerror", (e) => errs.push(e.message));
await p.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.quality", "fast"); localStorage.setItem("jarvis.qualityV", "2"); localStorage.setItem("jarvis.noLockAsk", "1"); } catch {} });
await p.goto(BASE);
await sleep(3000);
const ws = (w) => p.evaluate((w) => import("/app/workspaces.js").then((m) => m.useWorkspace(w, { quiet: true })), w);
const blur = () => p.evaluate(() => { if (document.activeElement) document.activeElement.blur(); });
const cmdOpen = () => p.evaluate(() => !document.querySelector("#cmdbar-scrim").classList.contains("hidden"));
const closeCmd = () => p.evaluate(() => import("/app/cmdbar.js").then((m) => m.close()));

await ws("program");
await act("select_heads", { heads: [1] });
await sleep(900);
const tabs = await p.evaluate(() => [...document.querySelectorAll("#prog-tabs button:not([hidden]) span")]
  .map((s) => ({ t: s.textContent, cut: s.scrollWidth > s.clientWidth + 1, w: s.getBoundingClientRect().width })));
check("every programmer tab shows its whole name at 1366 (Looks and Rig too)", tabs.length >= 8 && tabs.every((t) => !t.cut && t.w > 10), JSON.stringify(tabs));

// the keyboard: Enter never fires GO; a number starts a command
await blur();
const before = (await status()).playbacks[0].index;
await p.keyboard.press("Enter");
await sleep(400);
const opened = await cmdOpen();
await closeCmd();
check("Enter does not fire GO (it opens the command line)", (await status()).playbacks[0].index === before && opened);
await blur();
await p.keyboard.press("1");
await p.keyboard.type("2");
await sleep(300);
check("a typed number goes to the command line: 12 stays 12", (await p.evaluate(() => document.querySelector("#cmd-input").value)) === "12");
await closeCmd();
await p.evaluate(() => import("/app/cmdbar.js").then((m) => m.openCmdbar("")));
await p.keyboard.type("go live");
await sleep(700);
check("typing \"go live\" offers Go live - not a cue GO", /Go live/.test(await p.evaluate(() => document.querySelector("#cmdbar-scrim").innerText)));
await closeCmd();
const dry = await act("run_command", { text: "go live", dry: true });
check("...and the command line refuses it rather than firing GO", dry.ok === false, JSON.stringify(dry).slice(0, 160));

// X blacks out; X again doesn't bring the lights back (Shift+X does)
await blur();
await p.keyboard.press("x");
await sleep(500);
const on = !!(await status()).blackout;
await p.keyboard.press("x");
await sleep(500);
const still = !!(await status()).blackout;
await p.keyboard.press("Shift+X");
await sleep(500);
check("X blacks out, a second X doesn't undo it, Shift+X does", on && still && !(await status()).blackout);

// Update names the cue that is on
await act("cue_go", { playback: 1 });
await act("select_heads", { heads: [1] });
await act("set_intensity", { level: 20 });
await sleep(1200);
const upd = await p.evaluate(() => { const b = document.querySelector("#prog-update"); return { t: b.textContent, d: b.disabled }; });
check("the programmer has Update, naming the cue it merges into", /Update PB1/.test(upd.t) && !upd.d, JSON.stringify(upd));

// the playback fader and options are a finger's size
await p.evaluate(() => import("/app/playbacks.js").then((m) => m.setBottom && m.setBottom("faders")));
const fader = await p.evaluate(() => { const f = document.querySelector('.pb[data-pb="1"] .vfader'); return f ? f.getBoundingClientRect().width : 0; });
check("a playback fader is at least 36 px wide", fader >= 35.5, String(fader));

// Live: GO on screen above the buttons; one BLACKOUT
await ws("live");
await sleep(1200);
const mini = await p.evaluate(() => [...document.querySelectorAll("#pb-mini .pbm:not([hidden])")].length);
check("Live shows the playbacks that hold cues, with GO, above the buttons", mini === 2, String(mini));
const bos = await p.evaluate(() => [...document.querySelectorAll("button")].filter((b) => /BLACK\s*OUT/i.test(b.textContent) && b.getBoundingClientRect().width > 0).length);
check("one BLACKOUT on the Live screen", bos === 1, String(bos));

// going live, a tap can't stop the output
await act("set_output", { state: 1, confirm: true });
await sleep(1200);
await p.click("#out-arm");
await sleep(700);
const live = await p.evaluate(() => import("/app/store.js").then((m) => m.outputState()));
check("a tap on Hold to stop does not stop sending", live === "live", String(live));
await act("set_output", { state: 0 });

// a touch screen: the options buttons that show on hover are there
const touch = await browser.newContext({ viewport: { width: 1366, height: 768 }, hasTouch: true, isMobile: false });
const tp = await touch.newPage();
await tp.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.quality", "fast"); localStorage.setItem("jarvis.qualityV", "2"); localStorage.setItem("jarvis.noLockAsk", "1"); localStorage.setItem("jarvis.gig", "0"); } catch {} });
await tp.emulateMedia({ });
await tp.goto(BASE);
await sleep(3000);
const hoverNone = await tp.evaluate(() => matchMedia("(hover: none)").matches);
if (hoverNone) {
  const op = await tp.evaluate(() => { const b = document.querySelector('.pb[data-pb="1"] .pb-top .btn.icon'); return b ? getComputedStyle(b).opacity : "none"; });
  check("on a touch screen the playback options button shows (no hover needed)", op === "1", op);
} else console.log("  (this browser reports a hover pointer: the touch-screen check is skipped)");

check("no page errors", !errs.length, errs.join(" | "));
await browser.close();
console.log(fails ? `${fails} failed` : "all ok");
done(fails ? 1 : 0);

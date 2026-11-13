// The desk and the phone remote show the same thing: a button lit on one is
// lit on the other, and both are what the desk is really doing.  The desk's
// Buttons page and a phone side by side, a long run of presses from either
// (tap, hold, on/off, a colour row where one at a time is on, a timed shot,
// Release all, Blackout, a page turned), and after every one: the desk's
// own state, the desk's screen and the phone's screen agree.  Also: a held
// button shows held on the other screen while the finger is down, how long
// the other screen takes to follow, and both after the phone drops off and
// comes back.
//
//   node tools/synccheck.mjs            (CI runs it with the screen check)
//   SYNC_STEPS=1000 node tools/synccheck.mjs
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.SYNC_PORT || 8885), BASE = `http://127.0.0.1:${PORT}/`;
const STEPS = +(process.env.SYNC_STEPS || 160);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + String(extra).slice(0, 400))); if (!ok) fails++; };

try { await fetch(BASE); console.log(`something is already on port ${PORT} - stop it or set SYNC_PORT`); process.exit(2); } catch { /* free */ }
const DATA = mkdtempSync(join(tmpdir(), "haze-sync-"));
const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: DATA, CONSOLE_AUTOSAVE: "false", CONSOLE_AUTORESTORE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
process.on("uncaughtException", (e) => { console.log("  FAIL", e.message.split("\n")[0]); done(1); });
process.on("unhandledRejection", (e) => { console.log("  FAIL", String(e && e.message || e).split("\n")[0]); done(1); });
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const act = (a, params = {}) => fetch(BASE + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: a, params }) }).then((r) => r.json()).then((j) => j.result || j);
const lite = () => fetch(BASE + "api/console?lite=1").then((r) => r.json());

// a page with every way a button plays
await act("venue_template", { name: "club" });
await act("add_heads", { query: "LED PAR", qty: 4 });
const B = (slot, button) => act("quick_set", { page: 1, slot, button });
await B(1, { kind: "flash", label: "FLASH", mode: "hold", target: { all: true } });
await B(2, { kind: "colour", label: "Red", mode: "latch", colour: "#ff0000", exclusive: "colours", target: { all: true } });
await B(3, { kind: "colour", label: "Blue", mode: "latch", colour: "#0040ff", exclusive: "colours", target: { all: true } });
await B(4, { kind: "colour", label: "Green", mode: "latch", colour: "#00ff40", exclusive: "colours", target: { all: true } });
await B(5, { kind: "strobe", label: "STROBE", mode: "hold", target: { all: true } });
await B(6, { kind: "flash", label: "Shot 1 s", mode: "tap", seconds: 1, target: { all: true } });
await B(7, { kind: "flash", label: "Warm", mode: "latch", target: { all: true } });
await act("quick_set", { page: 2, slot: 1, button: { kind: "colour", label: "Page 2 amber", mode: "latch", colour: "#ffaa00", target: { all: true } } });
const ids = ["q1-1", "q1-2", "q1-3", "q1-4", "q1-5", "q1-6", "q1-7"];
const HOLD = new Set(["q1-1", "q1-5"]);

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
// the desk: its Buttons page
const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await dctx.addInitScript(() => { try { sessionStorage.setItem("haze.venuepick", "1"); localStorage.setItem("haze.noLockAsk", "1"); } catch (e) { /* fine */ } });
const desk = await dctx.newPage();
const errs = [];
desk.on("pageerror", (e) => errs.push("desk: " + e.message));
await desk.goto(BASE); await sleep(3500);
await desk.click('#pb-mode [data-mode="buttons"]').catch(() => {});
await sleep(800);
// the phone
const pctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
await pctx.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); localStorage.setItem("haze.remote.autofull", "0"); } catch (e) { /* fine */ } });
const phone = await pctx.newPage();
phone.on("pageerror", (e) => errs.push("phone: " + e.message));
await phone.goto(BASE + "remote.html"); await sleep(2500);

const deskOn = () => desk.evaluate((ids) => ids.filter((id) => { const el = document.querySelector(`#qb-grid .qbtn[data-id="${id}"]`); return el && el.classList.contains("on"); }), ids);
const phoneOn = () => phone.evaluate((ids) => ids.filter((id) => { const el = document.querySelector(`.rm-btn[data-id="${id}"]`); return el && el.classList.contains("on"); }), ids);
const truth = async () => { const l = await lite(); return { on: ids.filter((id) => (l.quick_active || []).includes(id)), bo: !!l.blackout }; };
const visible = { desk: await desk.evaluate((ids) => ids.filter((id) => document.querySelector(`#qb-grid .qbtn[data-id="${id}"]`)), ids),
  phone: await phone.evaluate((ids) => ids.filter((id) => document.querySelector(`.rm-btn[data-id="${id}"]`)), ids) };
check("both screens show the page's buttons", visible.desk.length === ids.length && visible.phone.length === ids.length, JSON.stringify(visible));

// the desk's Blackout: the Buttons page's own in full screen, the dock's otherwise
const deskBlackout = async () => { if (await desk.isVisible("#qb-bo")) await desk.click("#qb-bo"); else await desk.click("#bo-btn"); };
const el = (page, id) => page === desk ? desk.locator(`#qb-grid .qbtn[data-id="${id}"]`) : phone.locator(`.rm-btn[data-id="${id}"]`);
let pid = 100;
const down = (page, id) => { const p = ++pid; return el(page, id).dispatchEvent("pointerdown", { pointerId: p, button: 0, isPrimary: true }).then(() => p); };
const up = (page, id, p) => el(page, id).dispatchEvent("pointerup", { pointerId: p, button: 0, isPrimary: true });
const tap = async (page, id) => { const p = await down(page, id); await sleep(60); await up(page, id, p); };

// all three agree, within a settle time (a timed shot may end meanwhile: read it twice)
async function agree(label) {
  let last = null;
  for (let i = 0; i < 12; i++) {
    await sleep(120);
    const [t, d, ph] = [await truth(), await deskOn(), await phoneOn()];
    const dBo = await desk.evaluate(() => document.querySelector("#qb-bo") ? document.querySelector("#qb-bo").classList.contains("on") : null);
    const pBo = await phone.evaluate(() => document.querySelector("#rm-bo").classList.contains("on"));
    last = { t, d, ph, dBo, pBo };
    const same = JSON.stringify(t.on) === JSON.stringify(d) && JSON.stringify(t.on) === JSON.stringify(ph) && pBo === t.bo && (dBo === null || dBo === t.bo);
    if (same) return true;
  }
  check(`after "${label}": the desk, its screen and the phone agree`, false, JSON.stringify(last));
  return false;
}

console.log("Both ways, one press at a time");
// the plain cases, each from both sides
for (const [who, page] of [["phone", phone], ["desk", desk]]) {
  await act("quick_release_all"); await sleep(300);
  await tap(page, "q1-2");
  check(`${who}: an on/off button turns on - lit on both screens`, await agree(`${who} taps Red`));
  await tap(page, "q1-3");
  check(`${who}: another of the colour row - the first goes off on both (one at a time)`, await agree(`${who} taps Blue`) && JSON.stringify((await truth()).on) === '["q1-3"]', JSON.stringify((await truth()).on));
  await tap(page, "q1-3");
  check(`${who}: tapped again, off on both`, await agree(`${who} taps Blue off`));
  const p = await down(page, "q1-1");
  await sleep(500);
  const mid = { other: await (page === phone ? deskOn() : phoneOn()), t: (await truth()).on };
  check(`${who}: a hold button held - lit on the other screen while the finger is down`, mid.other.includes("q1-1") && mid.t.includes("q1-1"), JSON.stringify(mid));
  await up(page, "q1-1", p);
  check(`${who}: let go - off on both`, await agree(`${who} lets FLASH go`));
  await tap(page, "q1-6");
  await sleep(350);
  const shot = { d: await deskOn(), ph: await phoneOn() };
  check(`${who}: a 1 s shot is lit on both while it runs`, shot.d.includes("q1-6") && shot.ph.includes("q1-6"), JSON.stringify(shot));
  await sleep(1200);
  check(`${who}: ...and off on both when it ends`, await agree(`${who}'s shot ends`));
}

console.log("How fast the other screen follows");
const lag = [];
for (let i = 0; i < 12; i++) {
  await act("quick_release_all"); await sleep(250);
  const [from, to, toOn] = i % 2 ? [desk, phone, phoneOn] : [phone, desk, deskOn];
  const t0 = Date.now();
  await tap(from, "q1-7");
  for (;;) { if ((await toOn()).includes("q1-7")) break; if (Date.now() - t0 > 3000) break; await sleep(15); }
  lag.push(Date.now() - t0);
}
lag.sort((a, b) => a - b);
console.log(`  the other screen lit in ${lag[0]}-${lag[lag.length - 1]} ms (middle ${lag[lag.length >> 1]} ms)`);
check("the other screen follows within half a second, every time", lag[lag.length - 1] < 500, lag.join(","));

console.log(`A long random run: ${STEPS} presses from both sides`);
let seed = 7;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
await act("quick_release_all"); await act("blackout", { state: false });
let bad = 0;
const held = new Map();                    // id -> [page, pointer]
for (let i = 0; i < STEPS && bad < 3; i++) {
  const page = rnd(2) ? phone : desk, id = ids[rnd(ids.length)], r = rnd(20);
  let label;
  if (r === 0) {
    // Release all, from either side (the phone's is a hold)
    for (const [hid, [hp, p]] of held) await up(hp, hid, p);
    held.clear();
    if (page === desk) await desk.click("#qb-release");
    else { const rel = phone.locator("#rm-rel"); await rel.dispatchEvent("pointerdown", { pointerId: 900 + i, button: 0 }); await sleep(800); await rel.dispatchEvent("pointerup", { pointerId: 900 + i, button: 0 }); }
    label = `${page === desk ? "desk" : "phone"} Release all`;
  } else if (r === 1) {
    if (page === desk) await deskBlackout(); else await phone.locator("#rm-bo").click();
    label = `${page === desk ? "desk" : "phone"} Blackout`;
  } else if (HOLD.has(id) && !held.has(id) && r < 8) {
    held.set(id, [page, await down(page, id)]);
    label = `${page === desk ? "desk" : "phone"} holds ${id}`;
  } else if (held.has(id)) {
    const [hp, p] = held.get(id);
    held.delete(id);
    await up(hp, id, p);
    label = `${hp === desk ? "desk" : "phone"} lets ${id} go`;
  } else {
    await tap(page, id);
    label = `${page === desk ? "desk" : "phone"} taps ${id}`;
  }
  if (!(await agree(`step ${i + 1}: ${label}`))) bad++;
}
for (const [hid, [hp, p]] of held) await up(hp, hid, p);
check(`${STEPS} random presses from both sides: they agreed after every one`, bad === 0);

console.log("Pages, and a phone that drops off");
// a page turned on one screen doesn't turn the other (each looks at its own), but a button lit on page 2 is lit on both
await act("quick_release_all"); await act("blackout", { state: false });
await act("quick_press", { id: "q2-1", down: true }); await act("quick_press", { id: "q2-1", down: false });
await phone.locator('#rm-pages [data-p="2"]').click(); await sleep(700);
const p2 = await phone.evaluate(() => { const e = document.querySelector('.rm-btn[data-id="q2-1"]'); return e && e.classList.contains("on"); });
check("a button on another page: lit on the phone when it turns to that page", p2);
await phone.locator('#rm-pages [data-p="1"]').click(); await sleep(500);
await act("quick_release_all");
// the phone loses the Wi-Fi; the desk plays on; back, the phone catches up at once
await pctx.setOffline(true); await sleep(300);
await tap(desk, "q1-4"); await deskBlackout(); await sleep(600);
await pctx.setOffline(false);
let caught = false;
for (let i = 0; i < 40 && !caught; i++) {
  await sleep(150);
  const t = await truth();
  caught = JSON.stringify(t.on) === JSON.stringify(await phoneOn()) && t.bo === await phone.evaluate(() => document.querySelector("#rm-bo").classList.contains("on"));
}
check("the phone off the Wi-Fi while the desk plays: back on, it shows what the desk is doing", caught && await agree("the phone back"));
await deskBlackout(); await act("quick_release_all");
check("no page errors on either screen", !errs.length, errs.join(" | "));

await browser.close();
console.log(fails ? `${fails} failed` : "all ok");
done(fails ? 1 : 0);

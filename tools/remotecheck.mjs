// The phone and tablet remote (web/remote.html), in a real browser at the
// sizes of the phones and tablets of the last ten years: it fits (no
// sideways scroll, every button a thumb wide), it plays the desk's buttons,
// the risky ones need a hold, and the bottom strip does what it says.
//
//   node tools/remotecheck.mjs        (CI runs it with the screen check)
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.REMOTE_PORT || 8873), BASE = `http://127.0.0.1:${PORT}/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + String(extra).slice(0, 300))); if (!ok) fails++; };

try { await fetch(BASE); console.log(`something is already on port ${PORT} - stop it or set REMOTE_PORT`); process.exit(2); } catch { /* free */ }
const DATA = mkdtempSync(join(tmpdir(), "haze-remote-"));
const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: DATA, CONSOLE_AUTOSAVE: "false", CONSOLE_AUTORESTORE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
process.on("uncaughtException", (e) => { console.log("  FAIL", e.message.split("\n")[0]); done(1); });
process.on("unhandledRejection", (e) => { console.log("  FAIL", String(e && e.message || e).split("\n")[0]); done(1); });
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const act = (a, params = {}) => fetch(BASE + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: a, params }) }).then((r) => r.json()).then((j) => j.result || j);
const lite = () => fetch(BASE + "api/console?lite=1").then((r) => r.json());

// a show like a real one: headings over blocks, a heading per row, long names, a fader, CO2
await act("venue_template", { name: "club" });
await act("add_heads", { query: "LED PAR", qty: 4 });
const B = (page, slot, button) => act("quick_set", { page, slot, button });
await B(1, 1, { kind: "label", label: "Cobra 120's", w: 3 });
await B(1, 13, { kind: "flash", label: "FLASH", mode: "hold", target: { all: true } });
await B(1, 14, { kind: "colour", label: "Intimidator Wave 360 (L)", mode: "latch", colour: "#ff0000", target: { all: true } });
await B(1, 15, { kind: "sfx", label: "CO2", mode: "tap", target: { type: "co2" } });
await B(1, 25, { kind: "fader", label: "Strobe speed", control: { what: "master" } });
await B(2, 1, { kind: "label", label: "VIP" });
await B(2, 2, { kind: "colour", label: "Blue", mode: "latch", colour: "#0040ff", target: { all: true } });
await act("quick_layout", { page: 1, cols: 12, rows: 4 }).catch(() => {});
await act("quick_layout", { page: 2, cols: 12, rows: 4 }).catch(() => {});

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const dev = (w, hgt) => ({ viewport: { width: w, height: hgt }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const SIZES = [["iPhone SE (2016)", dev(320, 568)], ["small Android", dev(360, 640)], ["iPhone 6-8", dev(375, 667)],
  ["iPhone 12-15", dev(390, 844)], ["iPhone Pro Max", dev(430, 932)], ["phone on its side", dev(844, 390)],
  ["iPad", dev(768, 1024)], ["iPad on its side", dev(1180, 820)]];

console.log("Fits every phone and tablet");
for (const [name, d] of SIZES) {
  const ctx = await browser.newContext(d);
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  await p.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
  await p.goto(BASE + "remote.html"); await sleep(1800);
  const m = await p.evaluate(() => {
    const tiles = [...document.querySelectorAll(".rm-btn")].map((e) => e.getBoundingClientRect());
    const strip = document.querySelector("#rm-strip").getBoundingClientRect();
    return { hscroll: document.documentElement.scrollWidth > innerWidth + 1, n: tiles.length,
      minW: Math.min(...tiles.map((r) => r.width)), minH: Math.min(...tiles.map((r) => r.height)),
      stripIn: strip.bottom <= innerHeight + 1 && strip.top > innerHeight / 2,
      clipped: [...document.querySelectorAll(".rm-btn b")].filter((e) => e.scrollHeight > e.clientHeight + 1).length,
      heads: [...document.querySelectorAll(".rm-head")].map((e) => e.textContent) };
  });
  check(`${name}: no sideways scroll, ${m.n} buttons at least 64 px, names whole, the strip at the bottom`,
    !m.hscroll && m.n >= 3 && m.minW >= 64 && m.minH >= 56 && !m.clipped && m.stripIn, JSON.stringify(m));
  check(`${name}: no page errors`, !errs.length, errs.join(" | "));
  await ctx.close();
}

console.log("Plays the desk");
const ctx = await browser.newContext(dev(390, 844));
const p = await ctx.newPage();
const sent = [];
p.on("request", (r) => { if (r.method() === "POST" && /api\/console$/.test(r.url())) { try { sent.push(JSON.parse(r.postData())); } catch { /* fine */ } } });
await p.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
await p.goto(BASE + "remote.html"); await sleep(1800);
check("the block's heading is over its buttons", (await p.locator(".rm-head").first().innerText()).toUpperCase().includes("COBRA"));
const col = p.locator('.rm-btn[data-id="q1-14"]');
await col.dispatchEvent("pointerdown", { pointerId: 5, button: 0, isPrimary: true }); await col.dispatchEvent("pointerup", { pointerId: 5, button: 0 });
await sleep(700);
check("a tap on an on/off button turns it on at the desk", ((await lite()).quick_active || []).includes("q1-14"), JSON.stringify((await lite()).quick_active));
check("...and the tile shows it", await col.evaluate((e) => e.classList.contains("on")));

const co2 = p.locator('.rm-btn[data-id="q1-15"]');
const pressesOf = (id) => sent.filter((x) => x.action === "quick_press" && x.params.id === id && x.params.down).length;
await co2.dispatchEvent("pointerdown", { pointerId: 6, button: 0 }); await sleep(80); await co2.dispatchEvent("pointerup", { pointerId: 6, button: 0 });
await sleep(400);
check("a brush over CO2 doesn't fire it", pressesOf("q1-15") === 0, String(pressesOf("q1-15")));
await co2.dispatchEvent("pointerdown", { pointerId: 7, button: 0 }); await sleep(600); await co2.dispatchEvent("pointerup", { pointerId: 7, button: 0 });
await sleep(300);
check("held a moment, CO2 fires", pressesOf("q1-15") === 1, String(pressesOf("q1-15")));

await p.locator("#rm-bo").click(); await sleep(600);
check("Blackout at the bottom blacks out the desk", (await lite()).blackout === true);
await p.locator("#rm-bo").click(); await sleep(600);
check("...and back", (await lite()).blackout === false);
const rel = p.locator("#rm-rel");
await rel.dispatchEvent("pointerdown", { pointerId: 8, button: 0 }); await sleep(900); await rel.dispatchEvent("pointerup", { pointerId: 8, button: 0 });
await sleep(700);
check("Release all, held, lets go of the buttons", !((await lite()).quick_active || []).includes("q1-14"), JSON.stringify((await lite()).quick_active));
check("no editing on the remote", !(await p.locator("text=/^Edit$/").count()));

// swipe to the next page
await p.evaluate(() => {
  const g = document.querySelector("#rm-grid");
  const ev = (type, x) => g.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: x, clientY: 400, pointerId: 9 }));
  ev("pointerdown", 330); ev("pointerup", 120);
});
await sleep(500);
check("a swipe turns the page", await p.locator('#rm-pages .rm-tab.on[data-p="2"]').count() === 1);
await ctx.close();

console.log("A phone opening the desk");
const ph = await browser.newContext(dev(390, 844));
const pp = await ph.newPage();
await pp.goto(BASE); await sleep(1500);
check("gets the remote", /remote\.html/.test(pp.url()), pp.url());
await pp.goto(BASE + "?desk=1"); await sleep(1500);
check("...unless it asks for the full desk", !/remote\.html/.test(pp.url()), pp.url());
await ph.close();

await browser.close();
console.log(fails ? `${fails} failed` : "all ok");
done(fails ? 1 : 0);

// Touch: the desk as a touch-screen control surface, with real (simulated)
// fingers in Chromium - two hold buttons held at once and let go one by
// one, a second finger on the same button or fader ignored, a finger held
// on an effect button opening no menu, and a swipe, a pinch or an edge
// overscroll moving nothing: no scroll, no zoom, no "back".  Scratch data.
//
//   node tools/touchcheck.mjs
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.TOUCHCHECK_PORT || 8861), BASE = `http://127.0.0.1:${PORT}/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + extra)); if (!ok) fails++; };

const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: mkdtempSync(join(tmpdir(), "jarvis-touch-")),
    CONSOLE_AUTOSAVE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const act = (a, params = {}) => fetch(BASE + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: a, params }) }).then((r) => r.json()).then((j) => j.result || j);
const status = () => fetch(BASE + "api/console").then((r) => r.json());

await act("add_heads", { query: "LED PAR", qty: 4 });
await act("quick_set", { page: 1, slot: 1, button: { kind: "kill", label: "BLACKOUT", mode: "hold", target: { all: true } } });
await act("quick_set", { page: 1, slot: 2, button: { kind: "flash", label: "Flash", mode: "hold", target: { all: true } } });
await act("quick_set", { page: 1, slot: 3, button: { kind: "fx", label: "Rainbow", fx: "rainbow", mode: "hold", target: { all: true } } });
await act("select_heads", { heads: [1, 2] });
await act("set_intensity", { level: 50 });
await act("record_cue", { playback: 1 });

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, hasTouch: true });
const p = await ctx.newPage();
const errs = [];
p.on("pageerror", (e) => errs.push(e.message));
await p.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); } catch {} });
await p.goto(BASE);
await sleep(3500);
await p.evaluate(() => import("/app/workspaces.js").then((m) => m.useWorkspace("live", { quiet: true })));
await sleep(1200);
const cdp = await ctx.newCDPSession(p);
const touch = (type, points) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points });
const centre = async (sel) => {
  const b = await p.locator(sel).first().boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
};
const active = async () => ((await status()).quick || {}).active || [];
const ids = Object.fromEntries(((await status()).quick.buttons || []).map((b) => [b.label, b.id]));
const tile = (label) => `.qbtn:has-text("${label}")`;

// two buttons held with two fingers, let go one at a time
const a = await centre(tile("BLACKOUT")), f = await centre(tile("Flash"));
await touch("touchStart", [{ x: a.x, y: a.y, id: 1 }]);
await sleep(200);
await touch("touchStart", [{ x: a.x, y: a.y, id: 1 }, { x: f.x, y: f.y, id: 2 }]);
await sleep(400);
let on = await active();
check("two fingers hold two buttons at once", on.includes(ids.BLACKOUT) && on.includes(ids.Flash), JSON.stringify(on));
await touch("touchEnd", [{ x: f.x, y: f.y, id: 2 }]);
await sleep(400);
on = await active();
check("lifting one finger lets go of its button only", on.includes(ids.BLACKOUT) && !on.includes(ids.Flash), JSON.stringify(on));
await touch("touchEnd", [{ x: a.x, y: a.y, id: 1 }]);
await sleep(400);
check("and the other when it lifts", !(await active()).includes(ids.BLACKOUT), "");

// a second finger on the same held button: the first one still owns it
await touch("touchStart", [{ x: a.x, y: a.y, id: 3 }]);
await sleep(200);
await touch("touchStart", [{ x: a.x, y: a.y, id: 3 }, { x: a.x + 6, y: a.y + 4, id: 4 }]);
await sleep(200);
await touch("touchEnd", [{ x: a.x + 6, y: a.y + 4, id: 4 }]);
await sleep(400);
check("a second finger on the same button can't let it go", (await active()).includes(ids.BLACKOUT), "");
await touch("touchEnd", [{ x: a.x, y: a.y, id: 3 }]);
await sleep(400);
check("the first finger lets it go", !(await active()).includes(ids.BLACKOUT), "");

// a finger held still on an effect button (Windows: a right-click) opens no
// speed menu over the buttons; the mouse's right-click still does
const s = await centre(tile("Rainbow"));
await touch("touchStart", [{ x: s.x, y: s.y, id: 5 }]);
await sleep(300);
await p.evaluate(({ x, y }) => {
  const el = document.elementFromPoint(x, y);
  el.dispatchEvent(new PointerEvent("contextmenu", { bubbles: true, cancelable: true, pointerType: "touch", clientX: x, clientY: y }));
}, s);
await sleep(300);
check("a finger held on an effect button opens no menu mid-show", !(await p.locator(".menu").count()), "");
await touch("touchEnd", [{ x: s.x, y: s.y, id: 5 }]);
await sleep(1700);
await p.mouse.click(s.x, s.y, { button: "right" });
await sleep(300);
check("the mouse's right-click still opens its speed menu", (await p.locator(".menu").count()) === 1, "");
await p.keyboard.press("Escape");
await sleep(200);

// the faders: one finger moves one; a second finger on it doesn't jump it
await p.click('#pb-mode button[data-mode="faders"]');
await sleep(700);
const fd = await p.locator('.pb[data-pb="1"] .vfader').boundingBox();
const fx = fd.x + fd.width / 2;
await touch("touchStart", [{ x: fx, y: fd.y + fd.height * 0.8, id: 6 }]);
await touch("touchMove", [{ x: fx, y: fd.y + fd.height * 0.2, id: 6 }]);
await sleep(400);
const lvl = () => status().then((st) => (st.playbacks || []).find((x) => x.n === 1).level);
const up = await lvl();
check("a finger moves a fader", up >= 70, String(up));
await touch("touchStart", [{ x: fx, y: fd.y + fd.height * 0.2, id: 6 }, { x: fx, y: fd.y + fd.height * 0.95, id: 7 }]);
await touch("touchMove", [{ x: fx, y: fd.y + fd.height * 0.2, id: 6 }, { x: fx, y: fd.y + fd.height * 0.97, id: 7 }]);
await sleep(400);
check("a second finger on the same fader doesn't jump it", Math.abs((await lvl()) - up) <= 2, String(await lvl()));
await touch("touchEnd", [{ x: fx, y: fd.y + fd.height * 0.97, id: 7 }]);
await touch("touchEnd", [{ x: fx, y: fd.y + fd.height * 0.2, id: 6 }]);
await sleep(300);

// a swipe across the page, a pinch, a swipe off the edge: nothing moves
const url = p.url();
const before = await p.evaluate(() => [scrollX, scrollY, visualViewport.scale, document.documentElement.scrollLeft]);
const mid = { x: 720, y: 160 };
await touch("touchStart", [{ x: mid.x, y: mid.y, id: 8 }]);
for (let i = 1; i <= 10; i++) await touch("touchMove", [{ x: mid.x + i * 60, y: mid.y, id: 8 }]);
await touch("touchEnd", [{ x: mid.x + 600, y: mid.y, id: 8 }]);
await touch("touchStart", [{ x: 5, y: 400, id: 9 }]);
for (let i = 1; i <= 10; i++) await touch("touchMove", [{ x: 5 + i * 50, y: 400, id: 9 }]);
await touch("touchEnd", [{ x: 505, y: 400, id: 9 }]);
await touch("touchStart", [{ x: 700, y: 300, id: 10 }, { x: 740, y: 300, id: 11 }]);
for (let i = 1; i <= 8; i++) await touch("touchMove", [{ x: 700 - i * 30, y: 300, id: 10 }, { x: 740 + i * 30, y: 300, id: 11 }]);
await touch("touchEnd", [{ x: 460, y: 300, id: 10 }, { x: 980, y: 300, id: 11 }]);
await sleep(800);
const after = await p.evaluate(() => [scrollX, scrollY, visualViewport.scale, document.documentElement.scrollLeft]);
check("a swipe, an edge swipe or a pinch: no scroll, no zoom", JSON.stringify(before) === JSON.stringify(after), `${before} -> ${after}`);
check("and no going \"back\": the desk is still on screen", p.url() === url && (await p.locator("#qb").count()) === 1, p.url());
const css = await p.evaluate(() => { const s = getComputedStyle(document.body); return [s.overscrollBehaviorX, s.touchAction, s.userSelect]; });
check("the page: no overscroll, pan only, no text selection", css[0] === "none" && /pan-x/.test(css[1]) && css[2] === "none", css.join(" "));
check("no page errors", !errs.length, errs.join(" | "));

await browser.close();
console.log(fails ? `\n${fails} failed` : "\nall ok");
done(fails ? 1 : 0);

// Odd inputs, the browser half: two screens on one desk at once, and the
// desk's server dropping out and coming back.
//
//   node tools/oddcheck_ui.mjs [outDir]
//
// Two browsers: what one does, the other sees within a second (patch,
//   selection, a level, the grand master, a button page); both pressing at
//   once leaves one consistent desk; Undo from either undoes the same step.
// Dropped connection: the server stops - both screens say so within a few
//   seconds and nothing on screen breaks when pressed; the server comes back
//   (with its autosave) - both screens reconnect by themselves and show the
//   rig as it was.
// Any page error is a finding.  It runs its own server on a scratch data
// folder (CONSOLE_DATA_DIR), so your shows are never touched.  Needs Playwright.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = process.argv[2] || join(tmpdir(), "oddcheck-ui");
mkdirSync(outDir, { recursive: true });
const PORT = +(process.env.ODDCHECK_PORT || 8813);
const BASE = `http://127.0.0.1:${PORT}/`;
let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const exe = process.env.CHROMIUM || (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);

const data = mkdtempSync(join(tmpdir(), "jarvis-odd-"));
let server = null;
function startServer() {
  server = spawn(process.env.PYTHON || "python3", ["-m", "app.main"], {
    cwd: ROOT, stdio: ["ignore", "ignore", "pipe"],
    env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: data, CONSOLE_AUTOSAVE: "true",
      CONSOLE_AUTORESTORE: "true", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" },
  });
  server.stderr.on("data", () => {});
}
async function waitUp() {
  for (let i = 0; i < 160; i++) {
    try { if ((await fetch(BASE)).ok) return true; } catch { /* not yet */ }
    await sleep(250);
  }
  return false;
}
function stopServer() {
  return new Promise((r) => { if (!server || server.exitCode !== null) return r(); server.once("exit", r); server.kill(); });
}
process.on("exit", () => { try { server && server.kill(); } catch { /* gone */ } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let oks = 0;
const fails = [];
function check(ok, what, detail = "") {
  if (ok) oks++;
  else { fails.push(`${what}  ${detail}`.trim()); console.log(`  FAIL ${what}  ${detail}`); }
  return ok;
}
const act = (action, params = {}) => fetch(BASE + "api/console", {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, params }),
}).then((r) => r.json()).then((j) => j.result || j);
// wait until fn(page) is true (polls), up to ms
async function until(p, fn, ms = 3000, arg) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if (await p.evaluate(fn, arg)) return Date.now() - t0; } catch { /* page busy */ }
    await sleep(100);
  }
  return -1;
}
const errors = { A: [], B: [] };

startServer();
if (!(await waitUp())) { console.error("server did not start"); process.exit(2); }
await act("venue_template", { name: "club" });
await act("add_heads", { query: "LED PAR 4ch", qty: 8 });
await act("add_heads", { query: "Moving Head Spot 16ch", qty: 4 });

const browser = await chromium.launch({ executablePath: exe, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const open = async (who) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors[who].push(String(e.message || e)));
  p.on("console", (m) => { if (m.type() === "error" && !/favicon|Failed to load resource|net::ERR|stream/.test(m.text())) errors[who].push("console: " + m.text().slice(0, 200)); });
  await p.addInitScript(() => { try { sessionStorage.setItem("jarvis.venuepick", "1"); localStorage.setItem("jarvis.people", "0"); } catch { /* fine */ } });
  await p.goto(BASE);
  await sleep(1800);
  return p;
};
const A = await open("A");
const B = await open("B");
const count = (p) => p.evaluate(() => +document.querySelector("#fx-count").textContent || 0);

// ------------------------------------------------------------ two browsers
console.log("two browsers at once");
check(await count(A) === 12 && await count(B) === 12, "both screens show the 12 lights", `${await count(A)} / ${await count(B)}`);
// patch in A -> B sees it
await A.evaluate(() => fetch("/api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: "add_heads", params: { query: "LED PAR 4ch", qty: 2 } }) }));
let ms = await until(B, () => document.querySelector("#fx-count").textContent.trim() === "14");
check(ms >= 0, "a light patched on one screen appears on the other", ms >= 0 ? `${ms} ms` : "not within 3 s");
// select in B by clicking All -> A's programmer shows the selection
await B.click('.sel-actions [data-act="select_all"]');
ms = await until(A, () => /14/.test(document.querySelector("#sel-count").textContent));
check(ms >= 0, "selecting on one screen selects on the other", ms >= 0 ? `${ms} ms` : await A.evaluate(() => document.querySelector("#sel-count").textContent));
// Full in A -> B's level reads 100
await A.click('#prog-tabs [data-tab="intensity"]');
await A.click('#int-quick [data-level="100"]');
ms = await until(B, () => parseInt(document.querySelector("#int-num").textContent) === 100);
check(ms >= 0, "Full on one screen reads 100 on the other", ms >= 0 ? `${ms} ms` : await B.evaluate(() => document.querySelector("#int-num").textContent));
// both press at the same moment: different levels -> one consistent answer
await Promise.all([A.click('#int-quick [data-level="25"]'), B.click('#int-quick [data-level="75"]')]);
await sleep(800);
const [la, lb] = [await A.evaluate(() => document.querySelector("#int-num").textContent.trim()), await B.evaluate(() => document.querySelector("#int-num").textContent.trim())];
check(la === lb && /^(25|75)/.test(la), "pressed on both at once, both screens agree", `${la} vs ${lb}`);
// grand master dragged on A -> B's GM follows
const gm = await A.$("#gm-fader");
const box = await gm.boundingBox();
await A.mouse.move(box.x + box.width / 2, box.y + 8);
await A.mouse.down();
await A.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 });
await A.mouse.up();
const gmA = await A.evaluate(() => document.querySelector("#gm-num").textContent.trim());
ms = await until(B, (v) => document.querySelector("#gm-num").textContent.trim() === v, 3000, gmA);
check(gmA !== "100" && ms >= 0, "the grand master moved on one screen moves on the other", `A ${gmA}, B ${await B.evaluate(() => document.querySelector("#gm-num").textContent)}`);
await act("master", { level: 100 });
// buttons page: a button pressed on A lights on B
await A.click('#pb-mode [data-mode="buttons"]');
await B.click('#pb-mode [data-mode="buttons"]');
await act("quick_defaults");
await sleep(700);
const tile = await A.$(".qbtn:not(.empty):not(.qctl)");
if (check(!!tile, "the buttons page has buttons")) {
  const id = await tile.getAttribute("data-id");
  const tb = await tile.boundingBox();
  await A.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2);
  await A.mouse.down();
  ms = await until(B, (i) => { const t = document.querySelector(`.qbtn[data-id="${i}"]`); return !!t && (t.classList.contains("on") || t.classList.contains("down")); }, 3000, id);
  await A.mouse.up();
  check(ms >= 0, "a button held on one screen lights on the other", ms >= 0 ? `${ms} ms` : `button ${id}`);
  await act("quick_release_all");
}
// undo from B undoes the last step, whoever did it
const before = (await act("status")).undo || {};
await B.keyboard.press("Control+z");
await sleep(600);
const after = (await act("status")).undo || {};
check((after.depth ?? 0) < (before.depth ?? 1) || before.depth === undefined, "Ctrl+Z on the other screen undoes the shared last step",
  `depth ${before.depth} -> ${after.depth}`);
await A.screenshot({ path: join(outDir, "two-A.png") });
await B.screenshot({ path: join(outDir, "two-B.png") });

// --------------------------------------------------------- dropped connection
console.log("dropped connection");
await act("select_all");
await act("set_intensity", { level: 60 });
const lightsBefore = await count(A);
await sleep(1500);                                   // autosave has the rig
await stopServer();
ms = await until(A, () => /reconnecting/.test(document.querySelector("#st-feed").textContent), 6000);
check(ms >= 0, "the screen says it lost the desk", ms >= 0 ? `${ms} ms` : "no sign within 6 s");
const banner = await A.evaluate(() => { const b = document.querySelector("#banner"); return b && !b.classList.contains("hidden") ? b.textContent : ""; });
check(/Lost contact/.test(banner), "a banner says nothing reaches the rig", banner.slice(0, 80));
await B.click('#pb-mode [data-mode="faders"]').catch(() => {});
const errsBefore = errors.A.length + errors.B.length;
// press things while the desk is gone: nothing must break
for (const sel of ['#int-quick [data-level="100"]', "#locate-btn", "#prog-clear", '#prog-tabs [data-tab="colour"]', "#bo-btn"]) {
  await A.click(sel, { timeout: 1500 }).catch(() => {});
  await sleep(150);
}
await A.keyboard.press("Control+z");
await sleep(500);
const toasts = await A.evaluate(() => [...document.querySelectorAll(".toast")].map((t) => t.textContent).join(" | "));
check(errors.A.length + errors.B.length === errsBefore, "pressing while disconnected breaks nothing", [...errors.A, ...errors.B].slice(errsBefore).join(" | ").slice(0, 200));
await A.screenshot({ path: join(outDir, "dropped.png") });
console.log(`  (while down, the screen said: ${toasts.slice(0, 160) || "nothing"})`);
// the server comes back
startServer();
check(await waitUp(), "the desk starts again");
ms = await until(A, () => /connected/.test(document.querySelector("#st-feed").textContent) && !/reconnecting/.test(document.querySelector("#st-feed").textContent), 8000);
check(ms >= 0, "screen A reconnects by itself", ms >= 0 ? `${ms} ms` : "not within 8 s");
ms = await until(B, () => /connected/.test(document.querySelector("#st-feed").textContent) && !/reconnecting/.test(document.querySelector("#st-feed").textContent), 8000);
check(ms >= 0, "screen B reconnects by itself", ms >= 0 ? `${ms} ms` : "not within 8 s");
await sleep(800);
check(await count(A) === lightsBefore, "the rig is back as it was (autosave)", `${await count(A)} of ${lightsBefore}`);
const bannerAfter = await A.evaluate(() => { const b = document.querySelector("#banner"); return b && !b.classList.contains("hidden") ? b.textContent : ""; });
check(!/Lost contact/.test(bannerAfter), "the lost-contact banner goes away", bannerAfter.slice(0, 80));
const tabs = await A.evaluate(() => [...document.querySelectorAll("#prog-tabs button")].filter((b) => !b.hidden).map((b) => b.dataset.tab).join(","));
check(/intensity/.test(tabs), "after reconnecting the programmer still has its Level tab", tabs);
if (/intensity/.test(tabs)) {
  await A.click('#prog-tabs [data-tab="intensity"]');
  await A.click('#int-quick [data-level="50"]');
}
ms = await until(B, () => parseInt(document.querySelector("#int-num").textContent) === 50);
check(ms >= 0, "after the dropout both screens still work together", ms >= 0 ? `${ms} ms` : "");
await A.screenshot({ path: join(outDir, "back.png") });

check(errors.A.length === 0, "screen A: no page errors", errors.A.slice(0, 3).join(" | "));
check(errors.B.length === 0, "screen B: no page errors", errors.B.slice(0, 3).join(" | "));
await browser.close();
await stopServer();
const lines = [`# Odd inputs (browser) - ${new Date().toISOString().slice(0, 16)}`, "", `${oks} ok, ${fails.length} failed`, "", ...fails.map((f) => "- " + f)];
writeFileSync(join(outDir, "report.md"), lines.join("\n"));
console.log(`\n${oks} ok, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL", f);
process.exit(fails.length ? 1 : 0);

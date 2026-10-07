// The desktop app (desktop/), end to end: it starts the engine hidden on a
// private port with a private key, shows the desk, refuses requests without
// the key, opens Show mode (a window per part of the desk), and stops the
// engine cleanly when it closes.  Runs on scratch data, never data/.
//
//   cd desktop && npm install && cd .. && node tools/desktopcheck.mjs
//   (Linux with no screen: xvfb-run -a node tools/desktopcheck.mjs)
//   DESKTOP_EXE=path/to/Jarvis.exe node tools/desktopcheck.mjs   (a built app)
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir, networkInterfaces } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

let _electron;
try { ({ _electron } = await import("playwright")); } catch {
  ({ _electron } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DESK = join(ROOT, "desktop");
// DESKTOP_EXE: check an installed / built app (Jarvis.exe) instead of the repo's
const PACKED = process.env.DESKTOP_EXE || "";
const exe = PACKED || join(DESK, "node_modules", "electron", "dist", process.platform === "win32" ? "electron.exe" : "electron");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + extra)); if (!ok) fails++; };

const env = { ...process.env, CONSOLE_DATA_DIR: mkdtempSync(join(tmpdir(), "jarvis-desk-")), CONSOLE_AUTOSAVE: "false",
  DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false", ELECTRON_USER_DATA: "",
  // as on an operator's PC: PYTHON names a folder with no Python in it - the app must find the real one
  PYTHON: mkdtempSync(join(tmpdir(), "jarvis-not-python-")) };
// CHECKS_GPU=1: the computer's graphics card; otherwise software drawing
// (servers / cloud with no GPU), like the other browser checks
const GPU = process.env.CHECKS_GPU === "1";
const UD = mkdtempSync(join(tmpdir(), "jarvis-desk-ud-"));
const args = [...(process.platform === "linux" ? ["--no-sandbox"] : []),
  ...(GPU ? ["--ignore-gpu-blocklist"] : ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]),
  `--user-data-dir=${UD}`, ...(PACKED ? [] : [DESK])];
const app = await _electron.launch({ executablePath: exe, args, env, timeout: 90000 });
const win = await app.firstWindow({ timeout: 90000 });
await win.waitForSelector("#app", { timeout: 60000 });
const url = win.url();
const base = url.replace(/\?.*$/, "");
check("the desk opens in the app's own window", /^http:\/\/127\.0\.0\.1:\d+\/$/.test(base), url);
check("the engine is on a free port, not the usual 8787", !base.endsWith(":8787/"), base);
const noKey = await fetch(base + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: "blackout", params: {} }) }).then((r) => r.status).catch((e) => String(e));
check("anything else without the key is refused", noKey === 401 || noKey === 403, String(noKey));
const r = await win.evaluate(async () => {
  const j = await fetch("/api/console", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "add_heads", params: { query: "Moving Head", qty: 2 } }) }).then((x) => x.json());
  return j.result || j;
});
check("the desk works through the app (patch 2 lights)", r && r.ok, JSON.stringify(r).slice(0, 200));
let linked = false;
for (let i = 0; i < 60 && !linked; i++) { linked = await win.evaluate(() => document.querySelector("#fx-count")?.textContent.trim() === "2"); if (!linked) await sleep(500); }
check("the window's live link to the engine works (the list shows them)", linked);

// Show mode from the menu: the 3D, the programmer and the playbacks each in a window
await app.evaluate(({ Menu }) => Menu.getApplicationMenu().items.find((m) => m.label === "Desk").submenu.items[0].click());
await sleep(6000);
const wins = app.windows();
const parts = await Promise.all(wins.map(async (w) => new URL(w.url()).searchParams.get("window") || "main"));
check("Show mode opens a window per part of the desk", ["desk", "playbacks", "stage"].every((p) => parts.includes(p)) && !parts.includes("main"), parts.join(","));
for (const w of wins) {
  const part = new URL(w.url()).searchParams.get("window");
  await w.waitForSelector("#app", { timeout: 60000 });
  await sleep(1500);
  const seen = await w.evaluate(() => {
    const vis = (sel) => { const e = document.querySelector(sel); return !!e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== "none"; };
    return { stage: vis(".stage-wrap"), prog: vis(".programmer"), fixtures: vis(".fixtures"), pb: vis(".playbacks"), canvas: !!document.querySelector("#stage canvas") };
  });
  const want = { stage: { stage: true, prog: false, fixtures: false, pb: false },
    desk: { stage: false, prog: true, fixtures: true, pb: false },
    playbacks: { stage: false, prog: false, fixtures: false, pb: true } }[part];
  if (want) check(`the ${part} window shows only its part`, Object.entries(want).every(([k, v]) => seen[k] === v), JSON.stringify(seen));
  if (part === "desk" || part === "playbacks") check(`the ${part} window draws no 3D (one per computer)`, !seen.canvas);
  if (process.env.SHOTS) await w.screenshot({ path: join(process.env.SHOTS, `desktop-${part}.png`) });
}

// Ctrl+R / F5 don't reload during a show
const desk = wins.find((w) => new URL(w.url()).searchParams.get("window") === "desk");
if (desk) {
  await desk.evaluate(() => { window.__still = 1; });
  await desk.keyboard.press("Control+r");
  await desk.keyboard.press("F5");
  await sleep(1500);
  check("Ctrl+R and F5 don't reload a window", await desk.evaluate(() => window.__still === 1));
}

// closing: the engine stops cleanly (not killed)
const pid = await app.evaluate(() => process.pid);
await app.evaluate(({ app: a }) => a.quit());
await sleep(5000);
const gone = await fetch(base + "api/status").then(() => false).catch(() => true);
check("closing the app stops the engine", gone);
void pid;
try { await app.close(); } catch { /* already closed */ }

// Phones and tablets on (as Desk -> Phones and tablets leaves it): the desk
// answers the network, locked with the pairing code; a "phone" page that
// opens the address with the code typed in lower case gets in
const CODE = "ABCDEFGH23";
const PORT = 8800 + Math.floor(Math.random() * 150);
const state = join(UD, "windows.json");
let prev = {};
try { prev = JSON.parse(readFileSync(state, "utf8")); } catch { /* none */ }
writeFileSync(state, JSON.stringify({ ...prev, _desk: { remotes: true, port: PORT, code: CODE }, main: { open: true } }));
const app2 = await _electron.launch({ executablePath: exe, args, env, timeout: 90000 });
const win2 = await app2.firstWindow({ timeout: 90000 });
await win2.waitForSelector("#app", { timeout: 60000 });
const lan = Object.values(networkInterfaces()).flat().find((i) => i && i.family === "IPv4" && !i.internal);
const where = `http://${lan ? lan.address : "127.0.0.1"}:${PORT}/`;
check("phones on: the desk answers the network on its fixed port", new URL(win2.url()).port === String(PORT), win2.url());
const st = async (k) => fetch(where + "api/console", { headers: k ? { "X-Jarvis-Token": k } : {} })
  .then((r) => r.status).catch((e) => String(e));
check("from the network, no code is refused", (await st("")) === 401, String(await st("")));
check("from the network, the pairing code gets in", (await st(CODE)) === 200, String(await st(CODE)));
const phone = await app2.evaluate(async ({ BrowserWindow }, url) => {
  const w = new BrowserWindow({ show: false, width: 420, height: 860, webPreferences: { partition: "phone" } });
  await w.loadURL(url);
  for (let i = 0; i < 40; i++) {
    const ok = await w.webContents.executeJavaScript("!!document.querySelector('#statusbar') && /connected/i.test(document.body.innerText) && !location.search.includes('code')");
    if (ok) { w.destroy(); return true; }
    await new Promise((r) => setTimeout(r, 500));
  }
  const t = await w.webContents.executeJavaScript("location.href + ' ' + document.body.innerText.slice(0, 200)");
  w.destroy();
  return t;
}, where + "?code=abcde-fgh23");
check("a phone opening the address with the code gets the desk (code taken out of the address)", phone === true, String(phone));
await app2.evaluate(({ app: a }) => a.quit());
await sleep(4000);
try { await app2.close(); } catch { /* already closed */ }
console.log(fails ? `${fails} failed` : "all ok");
process.exit(fails ? 1 : 0);

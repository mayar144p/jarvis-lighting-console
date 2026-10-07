// Jarvis as a desktop app (docs/BACKLOG.md A9 step 1).
//
// Nothing is rewritten: this starts the same Python engine, hidden, on a
// free port that only this computer can reach, with a private key shared
// at start-up, and shows the same screens in the app's own windows - one
// per monitor in Show mode.  Closing the app stops the engine cleanly.
//
//   cd desktop && npm install && npm start      (or run-desktop.bat / .sh)
"use strict";
const { app, BrowserWindow, Menu, dialog, powerSaveBlocker, screen, session, shell } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

// Installed: the engine's files and a Python of its own sit in the app's
// resources (see .github/workflows/desktop.yml).  From the repo: the repo.
const PACKED = app.isPackaged;
const ROOT = PACKED ? path.join(process.resourcesPath, "engine") : path.join(__dirname, "..");
const TOKEN = crypto.randomBytes(24).toString("hex");
const STATE_FILE = () => path.join(app.getPath("userData"), "windows.json");

// the parts of the desk a window can show (?window=... in web/app/main.js)
const PARTS = {
  main: { q: "", title: "Jarvis" },
  stage: { q: "stage", title: "Jarvis - 3D view" },
  desk: { q: "desk", title: "Jarvis - Programmer" },
  playbacks: { q: "playbacks", title: "Jarvis - Playbacks" },
};

let engine = null, base = "", quitting = false, restarting = false, saved = {};
let key = TOKEN;                              // what the engine's requests must carry
const open = new Map();                       // part -> BrowserWindow
const reopen = new Set();                     // the windows to open next time

// ---------------------------------------------------------------- engine
function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

// The first Python that really runs.  Not just $PYTHON: on some computers
// it names a folder (...\Python312\Scripts), and on Windows "python" can be
// the Microsoft Store stand-in, which runs nothing.  JARVIS_PYTHON wins.
function findPython() {
  const win = process.platform === "win32";
  const tries = [];
  const own = path.join(process.resourcesPath, "python", win ? "python.exe" : "bin/python3");
  if (PACKED && fs.existsSync(own)) return [own, []];   // the installer's own Python
  for (const v of [process.env.JARVIS_PYTHON, process.env.PYTHON]) {
    if (!v) continue;
    let p = v.replace(/^"|"$/g, "");
    try { if (fs.statSync(p).isDirectory()) p = path.join(p, win ? "python.exe" : "python3"); } catch { continue; }
    tries.push([p, []]);
  }
  tries.push(...(win ? [["python", []], ["py", ["-3"]], ["python3", []]] : [["python3", []], ["python", []]]));
  for (const [cmd, pre] of tries) {
    const r = spawnSync(cmd, [...pre, "-c", "import sys; print(sys.version_info[0])"], { windowsHide: true, encoding: "utf8", timeout: 15000 });
    if (r.status === 0 && String(r.stdout).trim() === "3") return [cmd, pre];
  }
  return null;
}

// Installed, the shows, fixtures, settings (.env) and inbox live in the
// user's own folder (AppData\Roaming\Jarvis): they survive updates and a
// reinstall.  From the repo: the repo's data/ and .env, as run.bat uses.
function userFolders() {
  if (!PACKED) return {};
  const ud = app.getPath("userData");
  const env = path.join(ud, ".env");
  if (!fs.existsSync(env)) {
    try { fs.copyFileSync(path.join(ROOT, ".env.example"), env); } catch { /* none bundled */ }
  }
  const want = { CONSOLE_DATA_DIR: path.join(ud, "data"), CONSOLE_ENV_FILE: env,
    CONSOLE_INBOX: path.join(ud, "fixtures_inbox"), AUTO_UPDATE: "false" };
  // a value already set (the checks run on scratch data) wins
  return Object.fromEntries(Object.entries(want).filter(([k]) => !process.env[k]));
}

// Phones and tablets (Desk -> Phones and tablets): off, the engine answers
// this computer only, on a new free port each time, with a private key.  On,
// it answers the network on a fixed port, and the key is a short pairing
// code the operator types on the phone once.
const desk = () => (saved._desk = saved._desk || { remotes: false, port: 8787, code: "" });
const CODE_ABC = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";       // no 0/O, 1/I/L
const newCode = () => Array.from({ length: 10 }, () => CODE_ABC[crypto.randomInt(CODE_ABC.length)]).join("");
const showCode = (c) => `${c.slice(0, 5)}-${c.slice(5)}`;
function portOr(want) {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once("error", () => resolve(freePort()));
    s.listen(want, "0.0.0.0", () => s.close(() => resolve(want)));
  });
}
function lanAddresses() {
  return Object.values(os.networkInterfaces()).flat()
    .filter((i) => i && i.family === "IPv4" && !i.internal).map((i) => i.address);
}

// Jarvis from the repo brings itself up to date every time it opens, like
// run.bat: tools/update.py fast-forwards to the latest version (never with
// local edits; your data is never touched).  If the update changed the app
// itself, it opens again on the new version.  The installed app has no
// repo to update from, so it skips this.
async function selfUpdate() {
  if (PACKED || !fs.existsSync(path.join(ROOT, ".git"))) return;
  const found = findPython();
  if (!found) return;
  const splash = new BrowserWindow({ width: 360, height: 120, frame: false, resizable: false, backgroundColor: "#0b0b0d", show: false });
  splash.loadURL("data:text/html," + encodeURIComponent(
    "<body style='margin:0;display:grid;place-items:center;height:100vh;background:#0b0b0d;color:#c9cad3;font:14px system-ui'>Jarvis: checking for updates…</body>"));
  splash.once("ready-to-show", () => splash.show());
  const run = (cmd, args, opts = {}) => new Promise((resolve) => {
    const p = spawn(cmd, args, { cwd: ROOT, windowsHide: true, ...opts });
    let out = "";
    p.stdout && p.stdout.on("data", (d) => { out += d; });
    p.on("error", () => resolve(""));
    p.on("exit", () => resolve(out));
  });
  const out = await run(found[0], [...found[1], "tools/update.py"]);
  try { fs.writeFileSync(path.join(app.getPath("userData"), "update.log"), out); } catch { /* not fatal */ }
  const m = /updated (\w+) -> (\w+)/.exec(out);
  if (m) {
    const changed = await run("git", ["diff", "--name-only", m[1], m[2], "--", "desktop/"]);
    if (/desktop\/package(-lock)?\.json/.test(changed)) {
      await run(process.platform === "win32" ? "npm.cmd" : "npm", ["install", "--no-audit", "--no-fund"],
        { cwd: __dirname, shell: process.platform === "win32" });
    }
    if (changed.trim()) {                     // the app itself changed: open the new one
      splash.destroy();
      app.releaseSingleInstanceLock();      // or the new one would find this one still open
      app.relaunch();
      app.exit(0);
      return new Promise(() => {});
    }
  }
  splash.destroy();
}

async function startEngine() {
  const remote = desk().remotes && desk().code;
  const port = remote ? await portOr(desk().port) : await freePort();
  key = remote ? desk().code : TOKEN;
  const found = findPython();
  if (!found) throw new Error("Python 3 wasn't found. Install it from python.org (tick \"Add python.exe to PATH\"), then open Jarvis again");
  const [py, pre] = found;
  const log = fs.createWriteStream(path.join(app.getPath("userData"), "engine.log"), { flags: "w" });
  engine = spawn(py, [...pre, "app/main.py"], {
    cwd: ROOT, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    // HOST: this computer only (or the network, for phones).  CONSOLE_TOKEN:
    // the key.  The engine shuts down cleanly when our end of its stdin closes.
    env: { ...process.env, ...userFolders(), HOST: remote ? "0.0.0.0" : "127.0.0.1", PORT: String(port),
      CONSOLE_TOKEN: key, JARVIS_DESKTOP: "1", PYTHONUNBUFFERED: "1" },
  });
  engine.stdout.pipe(log);
  engine.stderr.pipe(log);
  let died = null;
  engine.once("exit", (code) => {
    died = code;
    if (!quitting && !restarting) {
      dialog.showErrorBox("Jarvis stopped", `The desk's engine stopped (code ${code}). Its log is in:\n${app.getPath("userData")}\\engine.log`);
      app.exit(1);
    }
  });
  engine.once("error", (e) => {
    dialog.showErrorBox("Jarvis can't start", `Python didn't start (${e.message}). Install Python 3 from python.org, then open Jarvis again.`);
    app.exit(1);
  });
  base = `http://127.0.0.1:${port}/`;
  for (let i = 0; i < 240 && died === null; i++) {          // up to a minute
    try { if ((await fetch(base + "api/status")).ok) return; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("the engine didn't answer");
}

function stopEngine() {
  if (!engine || engine.exitCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const hard = setTimeout(() => { try { engine.kill(); } catch { /* gone */ } resolve(); }, 8000);
    engine.once("exit", () => { clearTimeout(hard); resolve(); });
    engine.stdin.end();                       // the clean way: it saves and stops
  });
}

// --------------------------------------------------------------- windows
function loadState() {
  try { saved = JSON.parse(fs.readFileSync(STATE_FILE(), "utf8")) || {}; } catch { saved = {}; }
}
function saveState() {
  try { fs.writeFileSync(STATE_FILE(), JSON.stringify(saved, null, 1)); } catch { /* not fatal */ }
}
function onScreen(b) {
  return b && screen.getAllDisplays().some(({ workArea: d }) =>
    b.x < d.x + d.width && b.x + b.width > d.x && b.y < d.y + d.height && b.y + b.height > d.y);
}

// where a window goes the first time in Show mode: the 3D on the second
// monitor (full screen), playbacks on the third, the programmer on the first
function defaultPlace(part) {
  const ds = screen.getAllDisplays();
  const pick = { stage: ds[1], playbacks: ds[2], desk: ds[0], main: ds[0] }[part] || ds[0];
  const d = pick.workArea;
  return { bounds: { x: d.x + 40, y: d.y + 40, width: Math.min(1440, d.width - 80), height: Math.min(900, d.height - 80) },
    full: part === "stage" && ds.length > 1 };
}

function openPart(part) {
  if (open.has(part)) { open.get(part).focus(); return open.get(part); }
  const mem = saved[part];
  const place = mem && onScreen(mem.bounds) ? mem : defaultPlace(part);
  const win = new BrowserWindow({
    ...place.bounds, title: PARTS[part].title, backgroundColor: "#0b0b0d", show: false,
    icon: path.join(__dirname, "res", "icon.png"),
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  open.set(part, win);
  reopen.add(part);
  win.loadURL(base + (PARTS[part].q ? `?window=${PARTS[part].q}` : ""));
  win.once("ready-to-show", () => { if (place.full) win.setFullScreen(true); win.show(); });
  const remember = () => {
    if (win.isDestroyed()) return;
    saved[part] = { bounds: win.isFullScreen() || win.isMaximized() ? win.getNormalBounds() : win.getBounds(), full: win.isFullScreen() };
    saveState();
  };
  win.on("moved", remember);
  win.on("resized", remember);
  win.on("enter-full-screen", remember);
  win.on("leave-full-screen", remember);
  win.on("close", (e) => {
    remember();
    // the last window closes the desk: ask, so a stray click can't end a show
    if (!quitting && open.size === 1 && !confirmQuit(win)) e.preventDefault();
  });
  win.on("closed", () => {
    open.delete(part);
    if (!quitting) reopen.delete(part);        // closing the last one keeps it for next time
    if (!open.size) app.quit();
  });
  guard(win.webContents);
  return win;
}

function confirmQuit(win) {
  const r = dialog.showMessageBoxSync(win, {
    type: "question", buttons: ["Keep the desk open", "Close Jarvis"], defaultId: 0, cancelId: 0,
    message: "Close Jarvis?", detail: "The show is saved. The lights stop getting DMX from this computer.",
  });
  if (r === 1) quitting = true;
  return r === 1;
}

// keys that would close or reload a window belong to the desk during a
// show; links to other sites open in the normal browser
function guard(wc) {
  wc.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    const k = input.key.toLowerCase(), mod = input.control || input.meta;
    if (k === "f5" || (mod && (k === "r" || k === "w"))) e.preventDefault();
  });
  wc.on("will-navigate", (e, url) => { if (!url.startsWith(base)) { e.preventDefault(); shell.openExternal(url); } });
  wc.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(base)) return { action: "allow" };
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
}

function showMode() {
  for (const part of ["stage", "desk", "playbacks"]) openPart(part);
  const all = open.get("main");
  if (all) { reopen.delete("main"); quitting = true; all.close(); quitting = false; }
}
function oneWindow() {
  openPart("main");
  for (const part of ["stage", "desk", "playbacks"]) {
    const w = open.get(part);
    if (w) { reopen.delete(part); quitting = true; w.close(); quitting = false; }
  }
}

function menu() {
  const mac = process.platform === "darwin";
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(mac ? [{ role: "appMenu" }] : []),
    { label: "Desk", submenu: [
      { label: "Show mode (a window per monitor)", accelerator: "CmdOrCtrl+Shift+S", click: showMode },
      { label: "Everything in one window", accelerator: "CmdOrCtrl+Shift+A", click: oneWindow },
      { type: "separator" },
      { label: "3D view", click: () => openPart("stage") },
      { label: "Programmer and fixtures", click: () => openPart("desk") },
      { label: "Playbacks and buttons", click: () => openPart("playbacks") },
      { type: "separator" },
      { label: "Phones and tablets…", click: phonesDialog },
      { type: "separator" },
      { label: "Close Jarvis", accelerator: "CmdOrCtrl+Q", click: () => { if (confirmQuit(BrowserWindow.getFocusedWindow())) app.quit(); } },
    ] },
    { label: "View", submenu: [
      { role: "togglefullscreen", accelerator: "F11" },
      { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
      { type: "separator" },
      { role: "toggleDevTools", accelerator: "CmdOrCtrl+Shift+I" },
    ] },
  ]));
}

// every request the app's windows make to the engine carries its key
function trustEngine() {
  session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [base + "*"] }, (d, cb) => {
    d.requestHeaders["X-Jarvis-Token"] = key;
    cb({ requestHeaders: d.requestHeaders });
  });
}

// the engine started again with new settings; the windows follow it
async function restartEngine() {
  restarting = true;
  await stopEngine();
  try { await startEngine(); } finally { restarting = false; }
  trustEngine();
  for (const [part, w] of open) w.loadURL(base + (PARTS[part].q ? `?window=${PARTS[part].q}` : ""));
}

async function phonesDialog() {
  const win = BrowserWindow.getFocusedWindow();
  const d = desk();
  if (!d.remotes) {
    const r = await dialog.showMessageBox(win, {
      type: "question", buttons: ["Allow phones and tablets", "Cancel"], defaultId: 0, cancelId: 1,
      message: "Use a phone or tablet as a remote?",
      detail: "The desk then answers on this computer's network, locked with a pairing code you type on the phone once. "
        + "The phone must be on the same network (Wi-Fi) as this computer. Windows may ask to allow Jarvis through its firewall: allow it on private networks.",
    });
    if (r.response !== 0) return;
    Object.assign(d, { remotes: true, code: newCode() });
    saveState();
    await restartEngine();
  }
  const port = Number(new URL(base).port);
  const addrs = lanAddresses();
  const r = await dialog.showMessageBox(win, {
    type: "info", buttons: ["OK", "New code", "Stop allowing phones"], defaultId: 0, cancelId: 0,
    message: `Pairing code: ${showCode(d.code)}`,
    detail: (addrs.length ? `On the phone or tablet, open:\n${addrs.map((a) => `  http://${a}:${port}`).join("\n")}\nthen type the code.`
      + `\n\nJust the faders and buttons (a remote in your hand):\n  http://${addrs[0]}:${port}/?window=playbacks`
      : "This computer isn't on a network right now. Connect it to the same Wi-Fi as the phone.")
      + "\n\n\"New code\" signs every phone out.",
  });
  if (r.response === 1) { d.code = newCode(); saveState(); await restartEngine(); return phonesDialog(); }
  if (r.response === 2) { d.remotes = false; saveState(); await restartEngine(); }
}

// ------------------------------------------------------------------ start
// the app decides when it closes (the last desk window, asked first) -
// not Electron's default, which would end it when the update splash closes
app.on("window-all-closed", () => {});
const FIRST = app.requestSingleInstanceLock();
if (!FIRST) app.quit();                    // Jarvis is already open: that one comes forward
app.on("second-instance", () => { const w = [...open.values()][0]; if (w) { if (w.isMinimized()) w.restore(); w.focus(); } });

app.whenReady().then(async () => {
  if (!FIRST) return;
  loadState();
  await selfUpdate();
  try {
    await startEngine();
  } catch (e) {
    dialog.showErrorBox("Jarvis can't start", `${e.message}. Its log is in:\n${app.getPath("userData")}`);
    await stopEngine();
    app.exit(1);
    return;
  }
  trustEngine();
  // the desk's screens may use MIDI controllers (Web MIDI) and full screen;
  // nothing else (camera, microphone, location...) is ever granted
  const ALLOW = new Set(["midi", "midiSysex", "fullscreen", "clipboard-sanitized-write"]);
  session.defaultSession.setPermissionRequestHandler((_wc, perm, cb) => cb(ALLOW.has(perm)));
  session.defaultSession.setPermissionCheckHandler((_wc, perm) => ALLOW.has(perm));
  powerSaveBlocker.start("prevent-display-sleep");    // the screens stay awake during a show
  menu();
  const parts = Object.keys(saved).filter((p) => saved[p] && saved[p].open);
  if (parts.length) parts.forEach(openPart); else openPart("main");
});

// remember which windows were open, for next time
app.on("before-quit", () => {
  quitting = true;
  for (const part of Object.keys(PARTS)) saved[part] = { ...(saved[part] || {}), open: reopen.has(part) };
  saveState();
});
app.on("will-quit", (e) => {
  if (!engine || engine.exitCode !== null) return;
  e.preventDefault();
  stopEngine().then(() => app.exit(0));
});

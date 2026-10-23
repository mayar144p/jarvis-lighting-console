// Jarvis as a desktop app (docs/BACKLOG.md A9 step 1).
//
// Nothing is rewritten: this starts the same Python engine, hidden, on a
// free port that only this computer can reach, with a private key shared
// at start-up, and shows the same screens in the app's own windows - one
// per monitor in Show mode.  Closing the app stops the engine cleanly.
//
//   cd desktop && npm install && npm start      (or run-desktop.bat / .sh)
"use strict";
const { app, BrowserWindow, Menu, dialog, ipcMain, powerSaveBlocker, screen, session, shell } = require("electron");

// A touch screen is the desk's control surface, not a web page: a sideways
// swipe must not go "back" a page, a pinch must not zoom it (set before the
// app is ready; the page's CSS and guard() below back this up)
app.commandLine.appendSwitch("overscroll-history-navigation", "0");
app.commandLine.appendSwitch("disable-pinch");
app.commandLine.appendSwitch("disable-features", "OverscrollHistoryNavigation,TouchpadOverscrollHistoryNavigation");
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
// reinstall.  From the repo: the repo's data/ and .env (made from
// .env.example the first time).
function userFolders() {
  if (!PACKED) {
    const env = path.join(ROOT, ".env");
    if (!fs.existsSync(env)) {
      try { fs.copyFileSync(path.join(ROOT, ".env.example"), env); } catch { /* no example: defaults */ }
    }
    return {};
  }
  const ud = app.getPath("userData");
  const env = path.join(ud, ".env");
  if (!fs.existsSync(env)) {
    try { fs.copyFileSync(path.join(ROOT, ".env.example"), env); } catch { /* none bundled */ }
  }
  const want = { CONSOLE_DATA_DIR: path.join(ud, "data"), CONSOLE_ENV_FILE: env,
    CONSOLE_INBOX: path.join(ud, "fixtures_inbox"), AUTO_UPDATE: "false",
    CONSOLE_LLAMA_DIR: path.join(process.resourcesPath, "llama") };      // the offline AI's engine
  // the installer's "Include the offline AI" tick: handed to the desk, which
  // downloads the model in the background (and forgets the note once it has it)
  const tick = path.join(ud, "offline-ai-wanted");
  if (fs.existsSync(tick)) {
    try {
      const dir = path.join(process.env.CONSOLE_DATA_DIR || want.CONSOLE_DATA_DIR, "ai");
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, "wanted.json"), JSON.stringify({ from: "installer", at: new Date().toISOString() }));
      fs.unlinkSync(tick);
    } catch { /* the desk offers it in Settings -> AI instead */ }
  }
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
// a Tailscale (or other CGNAT-range VPN) address: 100.64.0.0/10
const isTailscale = (a) => { const [x, y] = a.split(".").map(Number); return x === 100 && y >= 64 && y <= 127; };

// Windows Firewall: the other computer reaches the desk only once Windows
// lets the port in.  Its own "allow?" question covers private networks
// only, a home network is often set "Public", and a dismissed question
// isn't asked again - the page then just never loads on the other
// computer.  One rule for the desk's port, from this network and Tailscale
// only (never the open internet), added after Windows' admin prompt.
const fwName = (port) => `Jarvis desk (port ${port})`;
function firewallHasRule(port) {
  if (process.platform !== "win32") return true;
  const r = spawnSync("netsh", ["advfirewall", "firewall", "show", "rule", `name=${fwName(port)}`], { windowsHide: true });
  return r.status === 0;
}
function firewallAllow(port) {
  const args = `advfirewall firewall add rule name="${fwName(port)}" dir=in action=allow protocol=TCP localport=${port} profile=any remoteip=LocalSubnet,100.64.0.0/10`;
  // the admin prompt: netsh started elevated by PowerShell, waited for
  const ps = `Start-Process -FilePath netsh -ArgumentList '${args.replace(/'/g, "''")}' -Verb RunAs -Wait -WindowStyle Hidden`;
  spawnSync("powershell.exe", ["-NoProfile", "-Command", ps], { windowsHide: true });
  return firewallHasRule(port);
}

// Jarvis from the repo brings itself up to date every time it opens, like
// tools/update.py fast-forwards to the latest version (never with
// local edits; your data is never touched).  If the update changed the app
// itself, it opens again on the new version.  The installed app has no
// repo to update from, so it skips this.
async function selfUpdate() {
  if (PACKED || !fs.existsSync(path.join(ROOT, ".git"))) return;
  const found = findPython();
  if (!found) return;
  const splash = new BrowserWindow({ width: 360, height: 120, frame: false, resizable: false, backgroundColor: "#0b0b0d", show: false });
  // charset: without it Windows reads the "…" as "â€¦"
  splash.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(
    "<!doctype html><meta charset='utf-8'><body style='margin:0;display:grid;place-items:center;height:100vh;background:#0b0b0d;"
    + "color:#c9cad3;font:14px system-ui;border:1px solid #303037;box-sizing:border-box'>"
    + "<div style='text-align:center'><b style='color:#ececf0;letter-spacing:.08em'>JARVIS</b>"
    + "<div style='margin-top:6px;color:#8e8e9a'>Checking for updates…</div></div></body>"));
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
      // npm on Windows is a .cmd: run it through cmd.exe ourselves (shell:true
      // with an argument list is what printed Node's DEP0190 warning)
      if (process.platform === "win32") await run(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", "npm install --no-audit --no-fund"], { cwd: __dirname });
      else await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: __dirname });
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
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false, preload: path.join(__dirname, "preload.js") },
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
    if (quitting || open.size !== 1) return;
    e.preventDefault();
    confirmQuit(win).then((yes) => { if (yes && !win.isDestroyed()) win.close(); });
  });
  win.on("closed", () => {
    open.delete(part);
    if (!quitting) reopen.delete(part);        // closing the last one keeps it for next time
    if (!open.size) app.quit();
  });
  guard(win.webContents);
  watchFullScreen(win);
  win.webContents.on("did-start-loading", () => pageUp.delete(win.webContents));   // asks wait for the new page
  return win;
}

// ---------------------------------------------------------------- questions
// The app's questions show inside the desk page, in its own look (preload.js
// + deskbridge.js).  A window whose page isn't up yet gets the system box.
const pageUp = new WeakSet();
const asks = new Map();
let askNo = 0;
ipcMain.on("desk:ready", (e) => pageUp.add(e.sender));
// Show menu -> Save everything to a file: the operator picks where (a USB
// stick, another drive) - not straight into Downloads like other saves
ipcMain.handle("desk:saveAs", async (e, name, bytes) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  const safe = String(name || "show.jarvis").replace(/[\\/:*?"<>|]/g, "_").slice(0, 120) || "show.jarvis";
  const r = await dialog.showSaveDialog(win, {
    title: "Save the show and everything it needs",
    defaultPath: path.join(app.getPath("documents"), safe),
    filters: [{ name: "Jarvis show (everything)", extensions: ["jarvis"] }],
  });
  if (r.canceled || !r.filePath) return null;
  await fs.promises.writeFile(r.filePath, Buffer.from(bytes));
  return r.filePath;
});
ipcMain.on("desk:answer", (_e, id, index) => {
  const done = asks.get(id);
  if (done) { asks.delete(id); done(Number.isInteger(index) ? index : -1); }
});

function waitForPage(wc, ms) {
  if (pageUp.has(wc)) return Promise.resolve(true);
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = setInterval(() => {
      if (wc.isDestroyed() || pageUp.has(wc) || Date.now() - t0 > ms) { clearInterval(tick); resolve(!wc.isDestroyed() && pageUp.has(wc)); }
    }, 100);
  });
}

// q: { title, message, detail, buttons, primary, cancel, danger, type }
// resolves with the pressed button's index (closing the box is `cancel`)
async function ask(win, q) {
  win = win && !win.isDestroyed() ? win : [...open.values()][0];
  const cancel = q.cancel ?? 0;
  if (win && !win.isDestroyed()) {
    const wc = win.webContents;
    if (await waitForPage(wc, 8000)) {
      if (win.isMinimized()) win.restore();
      win.focus();
      const id = ++askNo;
      // the page reloading or the window going away answers `cancel`
      let drop;
      const gone = new Promise((resolve) => {
        asks.set(id, resolve);
        drop = () => { if (asks.delete(id)) resolve(cancel); };
        wc.on("did-start-loading", drop);
        wc.on("destroyed", drop);
      });
      wc.send("desk:ask", { id, ...q, cancel });
      const i = await gone;
      if (!wc.isDestroyed()) { wc.off("did-start-loading", drop); wc.off("destroyed", drop); }
      return i < 0 ? cancel : i;
    }
  }
  const r = await dialog.showMessageBox(win, {
    type: q.type || "info", buttons: q.buttons, defaultId: q.primary ?? 0, cancelId: cancel,
    message: q.message || q.title, detail: q.detail || "",
  });
  return r.response;
}

let quitAsk = null;
function confirmQuit(win) {
  if (!quitAsk) {
    quitAsk = ask(win, {
      title: "Close Jarvis?", type: "question", buttons: ["Keep the desk open", "Close Jarvis"], primary: 0, cancel: 0, danger: 1,
      detail: "The show is saved. The lights stop getting DMX from this computer.",
    }).then((r) => { if (r === 1) quitting = true; return r === 1; })
      .finally(() => { quitAsk = null; });
  }
  return quitAsk;
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
  // no zoom by pinch, and no page to go "back" to: a stray swipe stays in the show
  wc.on("did-finish-load", () => {
    wc.setVisualZoomLevelLimits(1, 1).catch(() => {});
    try { wc.navigationHistory.clear(); } catch { /* an older Electron: the switches above */ }
  });
  wc.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(base)) return { action: "allow" };
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
}

// ---------------------------------------------------------------- touch
// On a touch screen a swipe in from an edge leaves the show: Task View (left),
// the notifications (right), the taskbar (bottom).  The sure way to stop it
// on Windows 10 and 11 is the "Allow Edge swipe" policy off, as kiosks do:
// Desk menu -> Lock touch swipes on this computer (asks for admin once,
// starts after signing back in; Unlock puts it back).  (Windows' per-window
// "no edge gestures while full screen" switch only works set from inside
// the app's own process, and Microsoft lists it for Windows 8 / 10 only.)
function powershell(script, opts = {}) {
  // -EncodedCommand: the script as UTF-16LE base64, so nothing in it needs quoting
  const enc = Buffer.from(script, "utf16le").toString("base64");
  return spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
    { windowsHide: true, stdio: "ignore", ...opts });
}
const EDGE_KEY = "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\EdgeUI";
// Windows 11's three- and four-finger touch gestures (switch apps, show the
// desktop): Settings > Bluetooth & devices > Touch, kept per user here (no
// administrator needed; starts after signing back in)
const FINGERS_KEY = "HKCU\\Control Panel\\Desktop";
// ---- full screen = every Windows touch gesture locked ---------------------
// While a desk window is full screen (the buttons page, a Show mode screen)
// Windows' touch gestures are locked, and back as they were when it leaves:
//  - edge swipes: the window's own "no edge gestures while full screen"
//    switch (System.EdgeGesture.DisableTouchWhenFullscreen).  Windows only
//    keeps it set from the window's own process, so it is set here, through
//    koffi (calls into Windows from the app itself) - a helper process's
//    setting was dropped;
//  - Windows 11's own per-user switches (Settings > Bluetooth & devices >
//    Touch): swipes in from the left and right edges, and the three- and
//    four-finger gestures (switch apps, show the desktop) - off while full
//    screen and put back exactly as they were after (at the next start if
//    the app stopped while full screen).  No administrator needed.
// What Windows took is written to touchlock.log in the app's data folder.
// The whole-computer lock (Desk menu, admin once) stays the sure way.
const gestureLock = () => saved._gestureLock !== false;   // (Desk menu: on by default)
let ffi;                                       // undefined: not loaded yet; null: none here
function winApi() {
  if (ffi !== undefined) return ffi;
  ffi = null;
  if (process.platform !== "win32") return ffi;
  try {
    const koffi = require("koffi");
    koffi.struct("JGUID", { d1: "uint32", d2: "uint16", d3: "uint16", d4: koffi.array("uint8", 8) });
    koffi.struct("JPKEY", { fmtid: "JGUID", pid: "uint32" });
    koffi.struct("JPROPVAR", { vt: "uint16", r1: "uint16", r2: "uint16", r3: "uint16", val: "int64", pad: "int64" });
    // (what a window's store hands back: Windows keeps these values as text)
    koffi.struct("JPROPVARP", { vt: "uint16", r1: "uint16", r2: "uint16", r3: "uint16", p: "void *", pad: "int64" });
    const ole32 = koffi.load("ole32.dll");
    const shell32 = koffi.load("shell32.dll"), user32 = koffi.load("user32.dll");
    ffi = {
      koffi,
      free: ole32.func("void __stdcall CoTaskMemFree(void *p)"),
      getStore: shell32.func("long __stdcall SHGetPropertyStoreForWindow(void *hwnd, JGUID *riid, _Out_ void **ppv)"),
      settingChange: user32.func("intptr_t __stdcall SendMessageTimeoutW(intptr_t hwnd, uint32 msg, uintptr_t wp, const char16_t *lp, uint32 flags, uint32 ms, _Out_ uintptr_t *res)"),
      GetValue: koffi.proto("long __stdcall JGetValue(void *self, JPKEY *key, _Out_ JPROPVARP *pv)"),
      SetValue: koffi.proto("long __stdcall JSetValue(void *self, JPKEY *key, JPROPVAR *pv)"),
      Commit: koffi.proto("long __stdcall JCommit(void *self)"),
      Release: koffi.proto("uint32 __stdcall JRelease(void *self)"),
    };
  } catch (e) { touchLog(`no Windows calls here: ${e.message}`); }
  return ffi;
}
const guid = (s) => {
  const h = s.replace(/-/g, "");
  return { d1: parseInt(h.slice(0, 8), 16), d2: parseInt(h.slice(8, 12), 16), d3: parseInt(h.slice(12, 16), 16),
    d4: Array.from({ length: 8 }, (_, i) => parseInt(h.slice(16 + i * 2, 18 + i * 2), 16)) };
};
function touchLog(line) {
  try { fs.appendFileSync(path.join(app.getPath("userData"), "touchlock.log"), `${new Date().toISOString()} ${line}\n`); } catch { /* not fatal */ }
}
// the window's switch; returns what Windows reads back (1 on, 0 off, <0 refused)
function edgeSwitch(win, on) {
  const api = winApi();
  if (!api || !win || win.isDestroyed()) return -9;
  try {
    const { koffi } = api;
    const b = win.getNativeWindowHandle();
    const hwnd = b.length >= 8 ? b.readBigUInt64LE(0) : BigInt(b.readUInt32LE(0));
    const out = [null];
    const hex = (n) => "0x" + (Number(n) >>> 0).toString(16);
    const hr = api.getStore(hwnd, guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), out);
    if (hr !== 0 || !out[0]) { touchLog(`edge switch: no property store (${hex(hr)}, ${typeof out[0]})`); return -2; }
    const store = out[0];
    const fns = koffi.decode(koffi.decode(store, "void *"), koffi.array("void *", 8));
    const key = { fmtid: guid("32CE38B2-2C9A-41B1-9BC5-B3784394AA44"), pid: 2 };
    const set = koffi.call(fns[6], api.SetValue, store, key, { vt: 11, r1: 0, r2: 0, r3: 0, val: on ? -1 : 0, pad: 0 });
    const commit = koffi.call(fns[7], api.Commit, store);
    const got = {};
    const get = koffi.call(fns[5], api.GetValue, store, key, got);
    koffi.call(fns[2], api.Release, store);
    // read back: a boolean, or (as Windows keeps a window's values) its text
    let back = null;
    if (got.vt === 11) back = Number(BigInt.asIntN(16, BigInt(koffi.address ? koffi.address(got.p) : 0))) !== 0;
    if (got.vt === 31 && got.p) {
      try { back = koffi.decode(got.p, "char16_t", -1); } catch { back = "?"; }
      try { api.free(got.p); } catch { /* a few bytes */ }
    }
    touchLog(`edge switch: hwnd ${hwnd} set ${hex(set)} commit ${hex(commit)} get ${hex(get)} vt ${got.vt} reads ${JSON.stringify(back)}`);
    if (set !== 0 || commit !== 0) return -1;
    if (typeof back === "string") return /^(-1|1|true)$/i.test(back.trim()) ? 1 : (/^(0|false)$/i.test(back.trim()) ? 0 : 1);
    return back === null ? 1 : (back ? 1 : 0);   // set and committed: Windows took it
  } catch (e) { touchLog(`edge switch failed: ${e.message}`); return -3; }
}
// tell Windows (and the shell) a setting changed, so it is read again now
function settingChanged() {
  const api = winApi();
  if (!api) return;
  for (const what of ["TouchGestureSetting", "Control Panel\\Desktop", "Software\\Microsoft\\Wisp\\Touch"]) {
    try { api.settingChange(0xffff, 0x001a, 0, what, 0x0002, 1000, [0]); } catch { /* not fatal */ }
  }
}
// the per-user touch switches full screen turns off: [key, value]
const WISP_KEY = "HKCU\\Software\\Microsoft\\Wisp\\Touch";
const TOUCH_SWITCHES = { fingers: [FINGERS_KEY, "TouchGestureSetting"],
  left: [WISP_KEY, "Left_Edgy_Enabled"], right: [WISP_KEY, "Right_Edgy_Enabled"] };
function readSwitch(name) {
  const [key, value] = TOUCH_SWITCHES[name];
  const r = spawnSync("reg", ["query", key, "/v", value], { windowsHide: true, encoding: "utf8" });
  const m = r.status === 0 && new RegExp(`${value}\\s+REG_DWORD\\s+0x([0-9a-f]+)`, "i").exec(r.stdout || "");
  return m ? parseInt(m[1], 16) : null;      // null: never set (Windows' default: on)
}
function writeSwitch(name, v) {
  const [key, value] = TOUCH_SWITCHES[name];
  if (v === null) spawnSync("reg", ["delete", key, "/v", value, "/f"], { windowsHide: true });
  else spawnSync("reg", ["add", key, "/v", value, "/t", "REG_DWORD", "/d", String(v), "/f"], { windowsHide: true });
}
let gesturesLocked = false;
function lockGestures(on) {
  if (process.platform !== "win32" || on === gesturesLocked) return;
  gesturesLocked = on;
  if (on) {
    if (!("_fingersWere" in saved)) {
      saved._fingersWere = readSwitch("fingers");
      saved._touchWere = Object.fromEntries(["left", "right"].map((n) => [n, readSwitch(n)]));
      saveState();
    }
    for (const n of Object.keys(TOUCH_SWITCHES)) writeSwitch(n, 0);
    settingChanged();
  } else if ("_fingersWere" in saved) {
    putTouchBack();
  }
  touchLog(`full screen ${on ? "on" : "off"}: three/four fingers ${readSwitch("fingers") === 0 ? "off" : "on"}, `
    + `left edge ${readSwitch("left") === 0 ? "off" : "on"}, right edge ${readSwitch("right") === 0 ? "off" : "on"}`);
}
// the operator's own touch switches, as they were before full screen
function putTouchBack() {
  writeSwitch("fingers", saved._fingersWere);
  for (const [n, v] of Object.entries(saved._touchWere || {})) writeSwitch(n, v);
  delete saved._fingersWere;
  delete saved._touchWere;
  saveState();
  settingChanged();
}
// a desk window went in or out of full screen: lock while any one is
function fullScreenChanged() {
  const any = gestureLock() && [...open.values()].some((w) => !w.isDestroyed() && w.isFullScreen());
  lockGestures(any);
}
function watchFullScreen(win) {
  if (process.platform !== "win32") return;
  win.once("ready-to-show", () => {
    if (gestureLock()) touchLog(`window ${win.getTitle()}: edge swipes while full screen ${edgeSwitch(win, true) === 1 ? "locked" : "NOT locked"}`);
  });
  for (const ev of ["enter-full-screen", "leave-full-screen", "enter-html-full-screen", "leave-html-full-screen", "closed"]) {
    win.on(ev, () => setTimeout(fullScreenChanged, 50));
  }
}
function setGestureLock(on) {
  saved._gestureLock = on;
  saveState();
  for (const w of open.values()) edgeSwitch(w, on);
  fullScreenChanged();
  menu();
}

function fingersOff() {
  const r = spawnSync("reg", ["query", FINGERS_KEY, "/v", "TouchGestureSetting"], { windowsHide: true, encoding: "utf8" });
  return r.status === 0 && /TouchGestureSetting\s+REG_DWORD\s+0x0\b/i.test(r.stdout || "");
}
function setFingers(on) {
  // (the computer-wide lock is what full screen puts back afterwards, too)
  if ("_fingersWere" in saved) { saved._fingersWere = on ? 1 : 0; saveState(); }
  spawnSync("reg", ["add", FINGERS_KEY, "/v", "TouchGestureSetting", "/t", "REG_DWORD", "/d", on ? "1" : "0", "/f"], { windowsHide: true });
  return fingersOff() === !on;
}
function machineSwipesOff() {
  const r = spawnSync("reg", ["query", EDGE_KEY, "/v", "AllowEdgeSwipe"], { windowsHide: true, encoding: "utf8" });
  return r.status === 0 && /AllowEdgeSwipe\s+REG_DWORD\s+0x0\b/i.test(r.stdout || "");
}
async function machineSwipes(lock) {
  const win = BrowserWindow.getFocusedWindow();
  const yes = await ask(win, lock ? {
    title: "Lock touch swipes on this computer",
    message: "Turn off Windows' touch gestures on this computer?",
    detail: "No more Task View, notifications or taskbar from a swipe in from an edge, and no app switching or "
      + "desktop from three or four fingers - for every app, until you unlock it here. "
      + "Windows asks for an administrator's OK once (for the edge swipes), and it starts after you sign out "
      + "and back in (or restart).",
    buttons: ["Cancel", "Lock swipes"], primary: 1, cancel: 0,
  } : {
    title: "Unlock touch swipes on this computer",
    message: "Turn Windows' touch gestures back on (edge swipes, three and four fingers)?",
    detail: "Windows asks for an administrator's OK, and it starts after you sign out and back in.",
    buttons: ["Cancel", "Unlock swipes"], primary: 1, cancel: 0,
  });
  if (yes !== 1) return;
  const fingers = setFingers(!lock);              // per user: no administrator needed
  // Windows 11's own per-user left / right edge switches too
  for (const n of ["left", "right"]) {
    writeSwitch(n, lock ? 0 : null);
    if (saved._touchWere) saved._touchWere[n] = lock ? 0 : null;
  }
  settingChanged();
  const args = lock ? ["add", EDGE_KEY, "/v", "AllowEdgeSwipe", "/t", "REG_DWORD", "/d", "0", "/f"]
    : ["delete", EDGE_KEY, "/v", "AllowEdgeSwipe", "/f"];
  const list = args.map((a) => `'${a.replace(/'/g, "''")}'`).join(",");
  await new Promise((resolve) => {
    powershell(`Start-Process reg.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList ${list}`)
      .on("exit", resolve).on("error", resolve);
  });
  const done = machineSwipesOff() === lock;
  await ask(win, {
    title: "Touch swipes",
    message: done ? (lock ? "Touch gestures are locked on this computer." : "Touch gestures are back on.")
      : fingers ? (lock ? "Three- and four-finger gestures are off; the edge swipes weren't changed (the administrator's OK wasn't given)."
        : "Three- and four-finger gestures are back on; the edge swipes weren't changed (the administrator's OK wasn't given).")
        : "Nothing changed (the administrator's OK wasn't given).",
    detail: done || fingers ? "Sign out and back in (or restart) for it to start." : "",
    buttons: ["OK"], cancel: 0,
  });
  menu();
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
      { label: "Other computers, phones and tablets…", click: phonesDialog },
      ...(process.platform === "win32" ? [
        { type: "separator" },
        { label: "Lock every touch gesture while full screen", type: "checkbox", checked: gestureLock(),
          click: (item) => setGestureLock(item.checked) },
        machineSwipesOff()
          ? { label: "Unlock touch swipes on this computer…", click: () => machineSwipes(false) }
          : { label: "Lock touch swipes on this computer…", click: () => machineSwipes(true) },
      ] : []),
      { type: "separator" },
      { label: "Close Jarvis", accelerator: "CmdOrCtrl+Q", click: async () => { if (await confirmQuit(BrowserWindow.getFocusedWindow())) app.quit(); } },
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

// Saves (a photo, a video, an export, a bug report) go straight into the
// Downloads folder, as in a browser - not a Save As box in the middle of
// the desk.  A bug report's zip is then shown in its folder, ready to drag
// into the GitHub page that opened in the browser.
function quietDownloads() {
  session.defaultSession.on("will-download", (_e, item) => {
    const dir = app.getPath("downloads");
    const name = path.basename(item.getFilename() || "jarvis-download");
    const ext = path.extname(name), stem = name.slice(0, name.length - ext.length);
    let file = path.join(dir, name);
    for (let i = 2; fs.existsSync(file); i++) file = path.join(dir, `${stem} (${i})${ext}`);
    item.setSavePath(file);
    item.once("done", (_ev, state) => {
      if (state === "completed" && /^jarvis-report-/.test(name)) shell.showItemInFolder(file);
    });
  });
}

// the engine started again with new settings; the windows follow it
async function restartEngine() {
  restarting = true;
  await stopEngine();
  try { await startEngine(); } finally { restarting = false; }
  trustEngine();
  for (const [part, w] of open) { pageUp.delete(w.webContents); w.loadURL(base + (PARTS[part].q ? `?window=${PARTS[part].q}` : "")); }
}

async function phonesDialog() {
  const win = BrowserWindow.getFocusedWindow();
  const d = desk();
  if (!d.remotes) {
    const r = await ask(win, {
      title: "Other computers, phones and tablets", type: "question", buttons: ["Allow phones and tablets", "Cancel"], primary: 0, cancel: 1,
      message: "Use this desk from another computer, a phone or a tablet?",
      detail: "The desk then answers on this computer's network, locked with a pairing code you type there once. "
        + "Another computer gets the whole desk - patch, program, the 3D - working on this computer's show; a phone can have just the faders. "
        + "It must be on the same network as this computer, or joined to it by Tailscale (for another town or country).",
    });
    if (r !== 0) return;
    Object.assign(d, { remotes: true, code: newCode() });
    saveState();
    await restartEngine();
  }
  const port = Number(new URL(base).port);
  const addrs = lanAddresses();
  const fwOk = firewallHasRule(port);
  const where = (a) => `  http://${a}:${port}` + (isTailscale(a) ? "   (Tailscale: from anywhere)" : "");
  const buttons = ["OK", "New code", "Stop allowing others"];
  if (!fwOk) buttons.push("Let it through Windows Firewall");
  const r = await ask(win, {
    title: "Other computers, phones and tablets", type: "info", buttons, primary: 0, cancel: 0, danger: 2,
    message: `Pairing code: ${showCode(d.code)}`,
    detail: (addrs.length ? `On the other computer, phone or tablet, open:\n${addrs.map(where).join("\n")}\nthen type the code. A computer gets the whole desk.`
      + `\n\nJust the faders and buttons (a remote in your hand):\n  http://${addrs[0]}:${port}/?window=playbacks`
      : "This computer isn't on a network right now. Connect it to the same Wi-Fi as the other device.")
      + (fwOk ? "" : "\n\nIf the page doesn't load on the other device, Windows Firewall is blocking it: press \"Let it through Windows Firewall\" (Windows asks for permission once).")
      + "\n\n\"New code\" signs every other device out.",
  });
  if (r === 1) { d.code = newCode(); saveState(); await restartEngine(); return phonesDialog(); }
  if (r === 2) { d.remotes = false; saveState(); await restartEngine(); }
  if (r === 3) {
    const ok = firewallAllow(port);
    await ask(win, {
      title: "Windows Firewall", type: ok ? "info" : "warning", buttons: ["OK"],
      message: ok ? "Windows Firewall now lets the desk in" : "Windows Firewall wasn't changed",
      detail: ok ? `From this network and Tailscale only, on port ${port}. Load the page on the other device again.`
        : "The permission was declined, or Windows refused. In Windows Security → Firewall → Allow an app, allow Jarvis (and Python) on private and public networks.",
    });
    return phonesDialog();
  }
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
  // stopped while full screen last time: the operator's own touch setting back
  if (process.platform === "win32" && "_fingersWere" in saved) putTouchBack();
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
  quietDownloads();
  // the desk's screens may use MIDI controllers (Web MIDI), full screen and
  // a microphone / line input (Sound: the beat, the drop) - audio only;
  // nothing else (camera, screen capture, location...) is ever granted
  const ALLOW = new Set(["midi", "midiSysex", "fullscreen", "clipboard-sanitized-write"]);
  const audioOnly = (d, unknownOk) => {
    const types = (d && (d.mediaTypes || (d.mediaType ? [d.mediaType] : []))) || [];
    return types.length > 0 && types.every((t) => t === "audio" || (unknownOk && t === "unknown"));
  };
  session.defaultSession.setPermissionRequestHandler((_wc, perm, cb, details) =>
    cb(ALLOW.has(perm) || (perm === "media" && audioOnly(details, false))));
  // (the check also covers listing the inputs by name, which asks as "unknown")
  session.defaultSession.setPermissionCheckHandler((_wc, perm, _origin, details) =>
    ALLOW.has(perm) || (perm === "media" && audioOnly(details, true)));
  powerSaveBlocker.start("prevent-display-sleep");    // the screens stay awake during a show
  menu();
  const parts = Object.keys(saved).filter((p) => saved[p] && saved[p].open);
  if (parts.length) parts.forEach(openPart); else openPart("main");
});

// remember which windows were open, for next time
app.on("before-quit", () => {
  quitting = true;
  lockGestures(false);                          // the operator's own touch setting back
  for (const part of Object.keys(PARTS)) saved[part] = { ...(saved[part] || {}), open: reopen.has(part) };
  saveState();
});
app.on("will-quit", (e) => {
  if (!engine || engine.exitCode !== null) return;
  e.preventDefault();
  stopEngine().then(() => app.exit(0));
});

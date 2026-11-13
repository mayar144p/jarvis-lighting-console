// The phone and tablet remote (web/remote.html): the show's buttons in your
// hand.  It plays the desk's own buttons - the same presses the desk makes,
// so a button does exactly what it does there - and edits nothing.
//
// Phone: each page's buttons flow into big thumb-sized tiles (3 across
// upright, more on its side), its headings become section titles.  Tablet:
// the page's own grid, as on the desk, while every cell stays big enough
// for a finger; past that it flows like a phone.

import { groupColour } from "../app/groupcolour.js";

const $ = (s) => document.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {
  // (no attributes: the second argument is already a child)
  if (attrs === null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) { kids.unshift(attrs); attrs = {}; }
  const [name, ...cls] = tag.split(".");
  const el = document.createElement(name);
  if (cls.length) el.className = cls.join(" ");
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.append(k);
  return el;
};

// ------------------------------------------------------------------ the code
// The pairing code: kept on this device when asked (a phone on the home
// screen opens fresh each time - typing it at every gig is no remote).
// "New code" on the desk signs every device out.
const KEY = "haze.remote.code";
const clean = (t) => { const c = String(t || "").trim().replace(/[\s-]/g, ""); return /^[a-z0-9]{8,12}$/i.test(c) ? c.toUpperCase() : String(t || "").trim(); };
let code = "";
try { code = localStorage.getItem(KEY) || sessionStorage.getItem(KEY) || ""; } catch (e) { /* private window */ }
try {
  const u = new URL(location.href);
  if (u.searchParams.get("code")) {
    code = clean(u.searchParams.get("code"));
    try { localStorage.setItem(KEY, code); } catch (e) { /* ignore */ }
    u.searchParams.delete("code");
    history.replaceState(null, "", u.pathname + u.search + u.hash);
  }
} catch (e) { /* ignore */ }
// moved here from the desk's address (see moveToName): this phone's layout,
// hidden tabs and switches come along in the link, once
try {
  if (location.hash.startsWith("#carry=")) {
    const got = JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(7).replace(/-/g, "+").replace(/_/g, "/")))));
    for (const [k, v] of Object.entries(got || {})) {
      if (typeof k === "string" && k.startsWith("haze.remote.") && typeof v === "string" && localStorage.getItem(k) === null) localStorage.setItem(k, v);
    }
    history.replaceState(null, "", location.pathname + location.search);
  }
} catch (e) { /* nothing to carry */ }
const CLIENT = Math.random().toString(36).slice(2, 10);
const headers = (json) => ({ ...(json ? { "Content-Type": "application/json" } : {}), "X-Haze-Client": "remote-" + CLIENT, ...(code ? { "X-Haze-Token": code } : {}) });

async function act(action, params = {}) {
  const r = await fetch("/api/console", { method: "POST", headers: headers(true), body: JSON.stringify({ action, params }) });
  if (r.status === 401) { askCode("That code didn't work - type the one the desk shows now."); throw new Error("code"); }
  const d = await r.json().catch(() => ({}));
  const res = d.result || {};
  if (!r.ok || res.ok === false || res.error) note(res.error || d.error || `the desk said no (${r.status})`);
  return res;
}

// --------------------------------------------------------------- live state
let snap = null, lite = {};
let connected = false;
const quick = () => (snap && snap.quick) || { buttons: [], names: {}, layout: {}, faders: {} };
const activeIds = () => new Set(lite.quick_active || quick().active || []);

function stream() {
  let delay = 500, ctrl = null, last = 0;
  setInterval(() => { if (ctrl && last && performance.now() - last > 3000) ctrl.abort(); }, 1000);
  const wake = () => { if (ctrl && (!connected || performance.now() - last > 1500)) ctrl.abort(); };
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { wake(); keepAwake(); } });
  window.addEventListener("online", wake);
  (async () => {
    for (;;) {
      ctrl = new AbortController();
      last = 0;
      try {
        const r = await fetch("/api/console/stream", { headers: headers(false), signal: ctrl.signal });
        if (r.status === 401) { askCode(code ? "The desk has a new code - type the one it shows now." : ""); return; }
        if (!r.ok || !r.body) throw new Error("HTTP " + r.status);
        delay = 500;
        last = performance.now();
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          last = performance.now();
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const block = buf.slice(0, i);
            buf = buf.slice(i + 2);
            let ev = "message", data = "";
            for (const line of block.split("\n")) {
              if (line.startsWith("event: ")) ev = line.slice(7);
              else if (line.startsWith("data: ")) data += line.slice(6);
            }
            if (data) { try { onEvent(ev, JSON.parse(data)); } catch (e) { console.error(ev, e); } }
          }
        }
        throw new Error("stream ended");
      } catch (e) {
        setLink(false);
        await new Promise((res) => setTimeout(res, delay));
        delay = Math.min(delay * 1.8, 2000);
      }
    }
  })();
}

function onEvent(ev, d) {
  if (ev === "snapshot") snap = d;
  else if (ev === "snapdiff" && snap) { snap = { ...snap, ...(d.set || {}) }; for (const k of d.del || []) delete snap[k]; }
  else if (ev === "lite") { lite = d; if (!connected) setLink(true); live(); return; }
  else return;
  if (!connected) setLink(true);
  render();
}

function setLink(on) {
  if (on && !connected) setTimeout(moveToName, 400);
  connected = on;
  $("#rm-dot").classList.toggle("on", on);
  $("#rm-dot").title = on ? "Connected to the desk" : "Not connected";
  document.body.classList.toggle("offline", !on);
  banner(on ? "" : "Not connected to the desk - trying again… (same Wi-Fi as the desk?)");
}
function banner(text) { const b = $("#rm-banner"); b.textContent = text; b.hidden = !text; }
let noteTimer = 0;
function note(text) {
  banner(text);
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => banner(connected ? "" : "Not connected to the desk - trying again…"), 3500);
}

// ------------------------------------------------------------------- views
// Buttons always; Cues when a playback has cues, Timeline when it has
// clips - a phone shows what plays the show, nothing that builds it
let view = "buttons";
try { view = sessionStorage.getItem("haze.remote.view") || "buttons"; } catch (e) { /* ignore */ }
const playbacksWithCues = () => ((snap && snap.playbacks) || []).filter((p) => (p.stack || []).length);
const timelineHasClips = () => (((snap && snap.timeline) || {}).tracks || []).some((t) => (t.clips || []).length);
const groupsThere = () => (snap && snap.groups) || [];
// the tabs the show has (Buttons always)
function viewsInShow() {
  return [["buttons", "Buttons"], groupsThere().length ? ["faders", "Faders"] : null,
    playbacksWithCues().length ? ["cues", "Cues"] : null, timelineHasClips() ? ["timeline", "Timeline"] : null].filter(Boolean);
}
// ...less the ones hidden on this phone (⋯ → Tabs on this phone; kept on the device, so
// each phone or tablet can show its own)
const HIDE_KEY = "haze.remote.hide";
let hidden = [];
try { hidden = JSON.parse(localStorage.getItem(HIDE_KEY) || "[]"); } catch (e) { hidden = []; }
function setHidden(v, hide) {
  hidden = hide ? [...new Set([...hidden, v])] : hidden.filter((x) => x !== v);
  try { localStorage.setItem(HIDE_KEY, JSON.stringify(hidden)); } catch (e) { /* this visit only */ }
  render(true);
}
function viewsThere() {
  return viewsInShow().filter(([k]) => k === "buttons" || !hidden.includes(k));
}
function setView(v) {
  view = v;
  if (v !== "buttons" && arranging) { arranging = false; document.body.classList.remove("arranging"); $("#rm-arrange-bar").hidden = true; }
  try { sessionStorage.setItem("haze.remote.view", v); } catch (e) { /* ignore */ }
  render(true);
}
function renderViews() {
  const nav = $("#rm-views");
  const there = viewsThere();
  if (!there.some(([k]) => k === view)) view = "buttons";
  const key = JSON.stringify([there, view]);
  if (nav.dataset.key === key) return;
  nav.dataset.key = key;
  nav.replaceChildren(...(there.length > 1 ? there.map(([k, label]) => h("button.rm-view" + (k === view ? ".on" : ""), { type: "button", onclick: () => setView(k) }, label))
    : [h("span.rm-title", "Buttons")]));
}

// ------------------------------------------------------------------- pages
let page = 1;
try { page = +sessionStorage.getItem("haze.remote.page") || 1; } catch (e) { /* ignore */ }
const pageName = (p) => (quick().names || {})[p] || `Page ${p}`;
function pagesThere() {
  const q = quick();
  return [...new Set([...q.buttons.map((b) => b.page), ...Object.keys(q.names || {}).filter((k) => q.names[k]).map(Number)])]
    .filter((p) => p >= 1 && q.buttons.some((b) => b.page === p)).sort((a, b) => a - b);
}
function setPage(p) {
  const there = pagesThere();
  if (!there.includes(p)) return;
  page = p;
  try { sessionStorage.setItem("haze.remote.page", String(p)); } catch (e) { /* ignore */ }
  render(true);
  const tab = $(`#rm-pages [data-p="${p}"]`);
  if (tab) tab.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
}

// ------------------------------------------------------------------ tiles
const KIND_COLOUR = { flash: "#f8fafc", strobe: "#fde047", kill: "#64748b", fx: "#a78bfa", sfx: "#fb923c", fog: "#94a3b8",
  laser: "#22c55e", blackout: "#ef4444", estop: "#ef4444", page: "#475569", go: "#22c55e" };
const tint = (b) => b.tint || b.colour || KIND_COLOUR[b.kind] || "#94a3b8";
// a tile's shades from its colour, worked out here: phones from a few years
// back (an iPhone 7 stays on iOS 15) can't mix colours in CSS
const rgb = (hex) => { const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim()); const n = parseInt(m ? m[1] : "94a3b8", 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
const mix = (a, b, k) => { const x = rgb(a), y = rgb(b); return `rgb(${x.map((v, i) => Math.round(v * k + y[i] * (1 - k))).join(",")})`; };
function paint(el, colour) {
  const c = rgb(colour).join(",");
  el.style.setProperty("--tint", `rgb(${c})`);
  el.style.setProperty("--tint-bg", mix(colour, "#131b25", 0.14));
  el.style.setProperty("--tint-edge", mix(colour, "#26333f", 0.6));
  el.style.setProperty("--tint-down", mix(colour, "#131b25", 0.38));
  el.style.setProperty("--tint-soft", `rgba(${c},.5)`);
  el.style.setProperty("--tint-text", mix(colour, "#e6f1fa", 0.7));
}
// CO2, confetti, sparks, flames, fog and lasers play on a tap, the same as
// on the desk: the desk's ARM FX is what lets fire and lasers go (a phone
// can't fire what isn't armed).  Their tiles are drawn with a
// double edge so they stand out.
const RISKY = new Set(["sfx", "fog", "laser"]);
const CONTROL = new Set(["fader", "xy", "tempo", "cuelist"]);
// a page button that only turns the page: the tabs at the top do that
const justTurns = (b) => b.kind === "page" && !b.release_here && !(b.turn_on || []).length;
const SIZE_WH = { wide: [2, 1], tall: [1, 2], big: [2, 2] };
const spanOf = (b) => (b.w || b.h ? [b.w || 1, b.h || 1] : SIZE_WH[b.size] || [1, 1]);
const modeText = (b) => (b.kind === "page" || b.kind === "folder" ? "page" : b.mode === "hold" ? "hold" : b.mode === "latch" ? "on/off" : "");

// the finger stays the tile's even if it slides off (a pointer the browser
// has already let go of can't be captured: carry on without it)
const capture = (el, id) => { try { el.setPointerCapture(id); } catch (e) { /* fine */ } };
let seq = 0;
const held = new Map();                  // button id -> finger
function press(b, down, finger = null) {
  if (down) { held.set(b.id, finger); if (navigator.vibrate) navigator.vibrate(8); } else held.delete(b.id);
  // (a hold button: the desk lets go by itself if this phone goes quiet - see below)
  act("quick_press", { id: b.id, down, seq: ++seq, client: CLIENT, lease: b.mode === "hold" }).catch(() => {});
  aliveTick();
}
// Fail-safes for a finger that never lifts, as far as the desk can tell:
// - while this phone holds a hold button it checks in every second, and the
//   desk lets go of it after 3 s without one (locked, out of Wi-Fi, closed);
// - hidden (screen locked, another app, the tab switched) it lets go of
//   everything it holds at once - a held button never outlives the phone's
//   attention.
let aliveTimer = 0;
function aliveTick() {
  const holding = [...held.keys()].some((id) => { const x = quick().buttons.find((y) => y.id === id); return x && x.mode === "hold"; });
  if (holding && !aliveTimer) {
    aliveTimer = setInterval(() => { act("quick_alive", { client: CLIENT }).catch(() => {}); }, 1000);
  } else if (!holding && aliveTimer) { clearInterval(aliveTimer); aliveTimer = 0; }
}
function letGoOfAll() {
  for (const id of [...held.keys()]) {
    const b = quick().buttons.find((x) => x.id === id);
    held.delete(id);
    if (!b || b.mode !== "hold") continue;
    try {
      fetch("/api/console", { method: "POST", keepalive: true, headers: headers(true),
        body: JSON.stringify({ action: "quick_press", params: { id, down: false, seq: ++seq, client: CLIENT } }) }).catch(() => {});
    } catch (e) { /* the desk lets go by itself in 3 s */ }
  }
  for (const el of document.querySelectorAll(".rm-btn.down")) el.classList.remove("down");
  aliveTick();
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") letGoOfAll(); });
window.addEventListener("pagehide", letGoOfAll);
window.addEventListener("blur", () => { if (held.size) letGoOfAll(); });
// a finger lifted anywhere lets go of the button it held, even if the page was redrawn
const lift = (e) => {
  for (const [id, f] of [...held]) {
    if (f !== e.pointerId) continue;
    const b = quick().buttons.find((x) => x.id === id);
    if (b && b.mode === "hold") press(b, false); else held.delete(id);
  }
};
window.addEventListener("pointerup", lift, true);
window.addEventListener("pointercancel", lift, true);

function tile(b) {
  const risky = RISKY.has(b.kind);
  const el = h("button.rm-btn" + (risky ? ".risky" : "") + (b.kind === "blackout" || b.kind === "estop" ? ".danger" : ""),
    { type: "button", "aria-label": b.label, dataset: { id: b.id } },
    h("b", b.label || "·"), modeText(b) ? h("small", modeText(b)) : null);
  paint(el, tint(b));
  let finger = null;
  el.addEventListener("pointerdown", (e) => {
    if (finger !== null || !connected || arranging) return;      // (arranging: tiles move, they don't play)
    e.preventDefault();
    finger = e.pointerId;
    capture(el, e.pointerId);
    el.classList.add("down");
    if (b.kind === "page" || b.kind === "folder") {
      if (b.release_here || (b.turn_on || []).length) act("quick_press", { id: b.id, down: true }).catch(() => {});
      setPage(b.to_page || 1);
      return;
    }
    press(b, true, e.pointerId);
  });
  const up = (e) => {
    if (finger === null || e.pointerId !== finger) return;
    finger = null;
    el.classList.remove("down");
    if (b.mode === "hold" && held.has(b.id)) press(b, false);
    else held.delete(b.id);
  };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
  el.addEventListener("lostpointercapture", up);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  return el;
}

const snapOr = (k, dflt) => (lite[k] !== undefined ? lite[k] : snap && snap[k] !== undefined ? snap[k] : dflt);
function faderValue(b) {
  const c = b.control || { what: "master" };
  if (c.what === "lights") return (quick().faders || {})[b.id] ?? (c.mode === "speed" ? 50 : c.mode === "strobe" ? 31 : 0);
  if (c.what === "master") return snapOr("master", 100);
  if (c.what === "speed") return Math.round(Math.min(100, (snapOr("speed_master", 1) || 1) * 50));
  if (c.what === "playback") { const p = ((snap && snap.playbacks) || []).find((x) => x.n === c.playback); return p ? p.level : 0; }
  return 0;
}
function throttle(fn, ms) {
  let t = 0, last = null;
  return (x) => { last = x; if (t) return; fn(x); t = setTimeout(() => { t = 0; if (last !== x) fn(last); }, ms); };
}
function control(b) {
  const el = h("div.rm-ctl", { dataset: { id: b.id } });
  paint(el, tint(b));
  if (b.kind === "fader") {
    const out = h("output", `${faderValue(b)}%`);
    const inp = h("input", { type: "range", min: 0, max: 100, value: faderValue(b), "aria-label": b.label });
    const send = throttle((x) => act("quick_fader", { id: b.id, level: x }).catch(() => {}), 60);
    inp.addEventListener("input", () => { out.textContent = `${inp.value}%`; inp.dataset.busy = "1"; send(+inp.value); });
    inp.addEventListener("change", () => setTimeout(() => { delete inp.dataset.busy; }, 400));
    el.classList.add("fader");
    el.append(h("span.rm-ctl-top", h("b", b.label), out), inp);
  } else if (b.kind === "xy") {
    const dot = h("i.rm-xy-dot"), pad = h("div.rm-xy", dot);
    const send = throttle(([pan, tilt]) => act("quick_xy", { id: b.id, pan, tilt }).catch(() => {}), 70);
    let f = null;
    const at = (e) => {
      const r = pad.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
      dot.style.left = x * 100 + "%"; dot.style.top = y * 100 + "%";
      send([Math.round(x * 255), Math.round((1 - y) * 255)]);
    };
    pad.addEventListener("pointerdown", (e) => { if (f !== null) return; f = e.pointerId; capture(pad, f); at(e); });
    pad.addEventListener("pointermove", (e) => { if (f === e.pointerId) at(e); });
    for (const ev of ["pointerup", "pointercancel"]) pad.addEventListener(ev, (e) => { if (f === e.pointerId) f = null; });
    el.classList.add("xy");
    el.append(h("b", b.label), pad);
  } else {
    // the tempo (tap on the beat) and a cue list (GO): a press each
    return tile(b);
  }
  return el;
}

// --------------------------------------------------------------- the page
// The tiles of one page in the desk's order: by row, then column.  A big
// tile keeps its size; the cells it covers on the desk are skipped.
function pageItems(p) {
  const q = quick();
  const lay = { cols: 8, rows: 3, ...(q.layout_default || {}), ...((q.layout || {})[p] || {}) };
  const bs = q.buttons.filter((b) => b.page === p && b.slot <= lay.cols * lay.rows).sort((a, b) => a.slot - b.slot);
  return { lay, bs };
}

// A tablet keeps the page's own grid when every cell gets a good-sized
// finger target (72 px) and there is height for it; else the tiles flow.
const MIN_CELL = 72;
function render(force = false) {
  if (!snap) return;
  renderViews();
  $("#rm-pages").hidden = view !== "buttons";
  const grid = $("#rm-grid");
  grid.dataset.view = view;
  if (view === "faders") { grid.dataset.key = ""; renderFaders(grid); live(); return; }
  if (view === "cues") { grid.dataset.key = ""; renderCues(grid); live(); return; }
  if (view === "timeline") { grid.dataset.key = ""; renderTimeline(grid); live(); return; }
  renderTabs();
  const w = grid.clientWidth || innerWidth;
  const { lay, bs } = pageItems(page);
  const key = JSON.stringify([page, w, bs, quick().names, arranging, pageLayout(page)]);
  if (!force && key === grid.dataset.key) { live(); return; }
  grid.dataset.key = key;
  const fits = !arranging && !pageLayout(page) && lay.cols * MIN_CELL + (lay.cols - 1) * 6 <= w - 20 && innerHeight >= 600;
  grid.classList.toggle("asdesk", fits);
  grid.replaceChildren(...(fits ? deskGrid(lay, bs) : flowGrid(bs, w, lay.cols)));
  if (!bs.length) grid.replaceChildren(h("p.rm-empty", `${pageName(page)} has no buttons`));
  live();
}

// tablet: the operator's own layout, cell for cell
function deskGrid(lay, bs) {
  const box = h("div.rm-desk");
  box.style.setProperty("--cols", lay.cols);
  const used = new Set();
  let lastRow = 0;
  for (const b of bs) {
    if (used.has(b.slot)) continue;
    const col = (b.slot - 1) % lay.cols, row = Math.floor((b.slot - 1) / lay.cols);
    const [ww, hh] = spanOf(b);
    const w = Math.max(1, Math.min(ww, lay.cols - col)), t = Math.max(1, Math.min(hh, lay.rows - row));
    for (let r = 0; r < t; r++) for (let c = 0; c < w; c++) used.add(b.slot + r * lay.cols + c);
    const el = b.kind === "label" ? h("div.rm-label", h("b", b.label)) : CONTROL.has(b.kind) ? control(b) : tile(b);
    if (b.kind === "label") paint(el, tint(b));
    el.style.gridColumn = `${col + 1} / span ${w}`;
    el.style.gridRow = `${row + 1} / span ${t}`;
    lastRow = Math.max(lastRow, row + t);
    box.append(el);
  }
  box.style.setProperty("--rows", lastRow || 1);
  return [box];
}

// Which heading each button sits under on the desk's grid.  A heading
// one cell wide with buttons beside it heads its ROW (Colours: "Spot 260's"
// then Red, Blue...).  A wider one heads the BLOCK under it (Show: "Cobra
// 120's" over its Flash / Strobe / Blackout), down to the first row with
// nothing in its columns or the next heading.  Buttons under no heading
// stay together where they are.
function sectionsOf(bs, cols) {
  const at = (b) => [Math.floor((b.slot - 1) / cols), (b.slot - 1) % cols];
  const labels = bs.filter((b) => b.kind === "label");
  const items = bs.filter((b) => b.kind !== "label");
  const covers = (b, r, c) => { const [br, bc] = at(b), [w, t] = spanOf(b); return r >= br && r < br + t && c >= bc && c < bc + w; };
  const rowHead = new Set(labels.filter((l) => spanOf(l)[0] === 1
    && items.some((b) => at(b)[0] === at(l)[0] && at(b)[1] > at(l)[1])).map((l) => l.id));
  const owner = new Map();
  for (const l of labels) {
    const [lr, lc] = at(l);
    if (rowHead.has(l.id)) {
      const stop = Math.min(cols, ...labels.filter((x) => x !== l && at(x)[0] === lr && at(x)[1] > lc).map((x) => at(x)[1]));
      for (const b of items) { const [r, c] = at(b); if (r === lr && c > lc && c < stop && !owner.has(b.id)) owner.set(b.id, l); }
      continue;
    }
    const w = spanOf(l)[0];
    for (let r = lr + 1; ; r++) {
      if (labels.some((x) => x !== l && at(x)[0] === r && at(x)[1] < lc + w && at(x)[1] + spanOf(x)[0] > lc)) break;
      const inRow = items.filter((b) => { for (let c = lc; c < lc + w; c++) if (covers(b, r, c)) return true; return false; });
      if (!inRow.length) break;
      for (const b of inRow) if (at(b)[0] === r && !owner.has(b.id)) owner.set(b.id, l);
    }
  }
  const out = [];
  let loose = null;
  for (const b of [...labels, ...items.filter((x) => !owner.has(x.id))].sort((a, c) => a.slot - c.slot)) {
    if (b.kind === "label") { out.push({ label: b.label, id: b.id, items: items.filter((x) => owner.get(x.id) === b) }); loose = null; continue; }
    if (!loose) { loose = { label: "", items: [] }; out.push(loose); }
    loose.items.push(b);
  }
  return out;
}

// ----------------------------------------------------- this phone's layout
// A phone can arrange each page its own way - move tiles and headings,
// make a tile bigger or smaller, hide what it doesn't need - kept on the
// phone (per show), never on the desk: the desk's buttons don't change.
// A button added on the desk later shows at the end of the page; one
// deleted there just goes.
const LAYOUT_KEY = () => "haze.remote.layout:" + ((snap && snap.show_file) || "show");
function layouts() { try { return JSON.parse(localStorage.getItem(LAYOUT_KEY()) || "{}"); } catch (e) { return {}; } }
const pageLayout = (p) => layouts()[p] || null;
function savePageLayout(p, lo) {
  const all = layouts();
  if (lo) all[p] = lo; else delete all[p];
  try { localStorage.setItem(LAYOUT_KEY(), JSON.stringify(all)); } catch (e) { note("This phone won't keep the layout (private browsing?)"); }
}
let arranging = false;

// the page as the desk lays it out: headings and tiles in order
function defaultPlan(bs, gridCols) {
  const plan = [];
  for (const sec of sectionsOf(bs, gridCols)) {
    const items = sec.items.filter((b) => !justTurns(b));
    if (!items.length) continue;                 // (a heading with nothing to press under it)
    if (sec.label) plan.push({ aid: "h:" + sec.id, head: sec.label });
    for (const b of items) plan.push({ aid: b.id, b });
  }
  return plan;
}
// ...with this phone's own order, sizes and hidden tiles
function phonePlan(plan, lo) {
  if (!lo) return plan;
  const by = new Map(plan.map((x) => [x.aid, x]));
  const out = (lo.order || []).filter((aid) => by.has(aid)).map((aid) => by.get(aid));
  for (const x of plan) if (!out.includes(x)) out.push(x);          // new on the desk: at the end
  return out.map((x) => ({ ...x, size: (lo.size || {})[x.aid], hidden: (lo.hide || []).includes(x.aid) }));
}
// a tile's size on the phone: normal, wide (2 across), big (2 x 2), the whole row
const SIZES = ["1", "w", "b", "f"];
const SIZE_NAME = { 1: "normal", w: "wide", b: "big", f: "full row" };
function sizeOf(item) {
  if (item.size) return item.size;
  const b = item.b, [ww, hh] = spanOf(b);
  if (b.kind === "fader" || b.kind === "xy" || ww >= 3) return "f";
  if (ww === 2 && hh > 1) return "b";
  if (ww === 2) return "w";
  return hh > 1 ? "t" : "1";                     // (tall: a big desk tile on a 3-across phone)
}
function place(el, size, cols) {
  if (size === "f") el.style.gridColumn = "1 / -1";
  else if (size === "w") el.style.gridColumn = `span ${Math.min(2, cols)}`;
  else if (size === "b") { el.style.gridColumn = `span ${Math.min(2, cols)}`; el.style.gridRow = "span 2"; el.classList.add("rm-tall"); }
  else if (size === "t") { el.style.gridRow = "span 2"; el.classList.add("rm-tall"); }
}

// phone: one grid, each heading a title across it, the tiles under it big
// (three across upright, more on its side)
function flowGrid(bs, w, gridCols) {
  // (arranging on a phone on its side: wider tiles, so their size / hide chips read whole)
  const side = arranging && innerWidth > innerHeight && innerHeight <= 520;
  const cols = Math.max(side ? 2 : 3, Math.min(6, Math.floor((w - 16) / (side ? 150 : 108))));
  const plan = phonePlan(defaultPlan(bs, gridCols), pageLayout(page));
  const box = h("div.rm-flow" + (arranging ? ".arranging" : ""));
  box.style.setProperty("--cols", cols);
  for (const item of plan) {
    if (item.hidden && !arranging) continue;
    let el;
    if (item.head) el = h("div.rm-head", item.head);
    else {
      const b = item.b;
      el = CONTROL.has(b.kind) && b.kind !== "tempo" && b.kind !== "cuelist" ? control(b) : tile(b);
      place(el, sizeOf(item), cols);
    }
    el.dataset.aid = item.aid;
    if (item.hidden) el.classList.add("rm-hidden");
    if (arranging) arrangeChips(el, item);
    box.append(el);
  }
  return [box];
}

// ------------------------------------------------------ arranging the page
function arrangeChips(el, item) {
  const chips = h("span.rm-chips");
  if (!item.head) {
    chips.append(h("button.rm-chip", { type: "button", "aria-label": "Size on this phone",
      onclick: () => {
        const cur = sizeOf(item), i = SIZES.indexOf(cur);
        changePage((lo) => { lo.size = { ...(lo.size || {}), [item.aid]: SIZES[(i + 1) % SIZES.length] }; });
      } }, "⤢ " + (SIZE_NAME[sizeOf(item)] || "tall")));
  }
  chips.append(h("button.rm-chip", { type: "button", "aria-label": item.hidden ? "Show on this phone" : "Hide on this phone",
    onclick: () => changePage((lo) => {
      const hide = new Set(lo.hide || []);
      if (hide.has(item.aid)) hide.delete(item.aid); else hide.add(item.aid);
      lo.hide = [...hide];
    }) }, item.hidden ? "👁 show" : "hide"));
  el.append(chips);
}
// a change to this page's layout, starting from what is on screen now
function changePage(fn) {
  const lo = { ...(pageLayout(page) || {}) };
  lo.order = [...document.querySelectorAll("#rm-grid [data-aid]")].map((x) => x.dataset.aid);
  fn(lo);
  savePageLayout(page, lo);
  render(true);
}
function setArranging(on) {
  arranging = on;
  document.body.classList.toggle("arranging", on);
  $("#rm-arrange-bar").hidden = !on;
  render(true);
}
// drag a tile or a heading: a lifted copy follows the finger, a dashed slot
// shows where it lands, and the others slide out of the way (not jump).  The
// slot only moves once the finger settles over a new place for a moment, so
// passing over tiles on the way doesn't shuffle the page; let go and the
// copy glides into the slot.  Near the top or bottom the page scrolls on.
(() => {
  const grid = $("#rm-grid");
  const SETTLE = 110, SLIDE = 200;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let drag = null;
  // FLIP: where each tile was, then slide it from there to its new place
  const rects = () => new Map([...grid.querySelectorAll("[data-aid]")].map((x) => [x, x.getBoundingClientRect()]));
  const slide = (was) => {
    if (reduce) return;
    for (const [x, r0] of was) {
      if (x === drag.el) continue;
      const r1 = x.getBoundingClientRect();
      const dx = r0.left - r1.left, dy = r0.top - r1.top;
      if (!dx && !dy) continue;
      x.style.transition = "none";
      x.style.transform = `translate(${dx}px, ${dy}px)`;
      x.getBoundingClientRect();                       // (the start position takes)
      x.style.transition = `transform ${SLIDE}ms cubic-bezier(.2,.8,.2,1)`;
      x.style.transform = "";
    }
  };
  // where the slot would go for the finger at (cx, cy): before or after the tile under it
  const target = (cx, cy) => {
    const g = drag.ghost;
    g.style.visibility = "hidden";
    const under = document.elementFromPoint(cx, cy);
    g.style.visibility = "";
    const t = under && under.closest("#rm-grid [data-aid]");
    if (!t || t === drag.el) return null;
    const r = t.getBoundingClientRect();
    const after = t.classList.contains("rm-head") ? cy > r.top + r.height / 2
      : (cy > r.bottom - r.height / 4) || (cx > r.left + r.width / 2 && cy > r.top + r.height / 4);
    const ref = after ? t.nextSibling : t;
    return ref === drag.el || (ref && ref === drag.el.nextSibling && !after && t === drag.el.nextSibling) ? null : { t, after };
  };
  const follow = () => {
    const d = drag;
    d.ghost.style.transform = `translate(${d.cx - d.gx}px, ${d.cy - d.gy}px) scale(1.06)`;
    const want = target(d.cx, d.cy);
    const key = want ? want.t.dataset.aid + (want.after ? ">" : "<") : "";
    if (key !== d.want) { d.want = key; d.since = performance.now(); }
    if (!want || performance.now() - d.since < SETTLE) return;
    const was = rects();
    want.t.parentNode.insertBefore(d.el, want.after ? want.t.nextSibling : want.t);
    d.want = "";
    slide(was);
    if (navigator.vibrate) navigator.vibrate(4);
  };
  const tick = () => {
    if (!drag || !drag.on) return;
    const g = grid.getBoundingClientRect();
    const step = drag.cy < g.top + 60 ? -Math.ceil((g.top + 60 - drag.cy) / 4) : drag.cy > g.bottom - 60 ? Math.ceil((drag.cy - g.bottom + 60) / 4) : 0;
    if (step) grid.scrollTop += Math.max(-24, Math.min(24, step));
    follow();                                          // (also: the finger resting still settles the slot)
    drag.raf = requestAnimationFrame(tick);
  };
  const lift = () => {
    const d = drag, r = d.el.getBoundingClientRect();
    const ghost = d.el.cloneNode(true);
    ghost.classList.add("rm-ghost");
    ghost.removeAttribute("data-aid");
    Object.assign(ghost.style, { position: "fixed", left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px", margin: 0 });
    document.body.append(ghost);
    d.ghost = ghost;
    d.gx = d.x; d.gy = d.y;                            // the finger's place on the tile when it was lifted
    d.el.classList.add("rm-slot");
    d.on = true;
    if (navigator.vibrate) navigator.vibrate(10);
    d.raf = requestAnimationFrame(tick);
  };
  grid.addEventListener("pointerdown", (e) => {
    if (!arranging || e.target.closest(".rm-chip")) return;
    const el = e.target.closest("[data-aid]");
    if (!el) return;
    e.preventDefault(); e.stopPropagation();       // (no presses while arranging)
    drag = { el, id: e.pointerId, x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, on: false, raf: 0, want: "", since: 0 };
    capture(grid, e.pointerId);
  }, true);
  grid.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    e.preventDefault();
    drag.cx = e.clientX; drag.cy = e.clientY;
    if (!drag.on && Math.hypot(drag.cx - drag.x, drag.cy - drag.y) < 8) return;
    if (!drag.on) lift();
  }, true);
  const end = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    cancelAnimationFrame(d.raf);
    if (!d.on) return;
    // let go: it lands where the finger is now, settled or not
    drag = d;
    const last = target(d.cx, d.cy);
    if (last) { const was = rects(); last.t.parentNode.insertBefore(d.el, last.after ? last.t.nextSibling : last.t); slide(was); }
    drag = null;
    // the copy glides into the slot, then the tile is back in its place
    const r = d.el.getBoundingClientRect(), g = d.ghost;
    const done = () => {
      g.remove();
      d.el.classList.remove("rm-slot");
      if (navigator.vibrate) navigator.vibrate(8);
      changePage(() => {});
    };
    if (reduce) { done(); return; }
    g.style.transition = `transform ${SLIDE}ms cubic-bezier(.2,.8,.2,1), box-shadow ${SLIDE}ms`;
    g.style.transform = `translate(${r.left - parseFloat(g.style.left)}px, ${r.top - parseFloat(g.style.top)}px) scale(1)`;
    g.classList.add("rm-ghost-drop");
    setTimeout(done, SLIDE + 20);
  };
  grid.addEventListener("pointerup", end, true);
  grid.addEventListener("pointercancel", end, true);
})();
$("#rm-arrange-done").addEventListener("click", () => setArranging(false));
$("#rm-arrange-reset").addEventListener("click", () => {
  savePageLayout(page, null);
  note(`${pageName(page)}: back to the desk's layout`);
  render(true);
});

// ------------------------------------------------------------- cues
function renderCues(grid) {
  const key = JSON.stringify(playbacksWithCues().map((p) => [p.n, p.name, (p.stack || []).map((c) => [c.n, c.name])]));
  if (grid.dataset.cues === key && grid.querySelector(".rm-pb")) return;
  grid.dataset.cues = key;
  grid.replaceChildren(...playbacksWithCues().map((p) => {
    const lvl = h("input", { type: "range", min: 0, max: 100, value: p.level ?? 100, "aria-label": `Playback ${p.n} level` });
    const send = throttle((x) => act("playback_level", { playback: p.n, level: x }).catch(() => {}), 60);
    lvl.addEventListener("input", () => { lvl.dataset.busy = "1"; send(+lvl.value); });
    lvl.addEventListener("change", () => setTimeout(() => { delete lvl.dataset.busy; }, 400));
    return h("section.rm-pb", { dataset: { n: p.n } },
      h("div.rm-pb-top", h("b", p.name || `Playback ${p.n}`), h("span.rm-pb-cue", "")),
      h("small.rm-pb-next", ""),
      lvl,
      h("div.rm-pb-btns",
        h("button.rm-go", { type: "button", onclick: () => act("cue_go", { playback: p.n }).catch(() => {}) }, "GO"),
        h("button", { type: "button", onclick: () => act("cue_back", { playback: p.n }).catch(() => {}) }, "◀ Back"),
        h("button", { type: "button", onclick: () => act("playback_release", { playback: p.n }).catch(() => {}) }, "Release")));
  }));
}
function liveCues() {
  for (const el of document.querySelectorAll(".rm-pb[data-n]")) {
    const p = ((snap && snap.playbacks) || []).find((x) => x.n === +el.dataset.n);
    if (!p) continue;
    const cur = p.index >= 0 ? p.stack[p.index] : null, nxt = p.stack[(p.index ?? -1) + 1];
    el.classList.toggle("on", !!p.active);
    el.querySelector(".rm-pb-cue").textContent = cur && p.active ? `${cur.n} · ${cur.name || ""}` : "ready";
    el.querySelector(".rm-pb-next").textContent = nxt ? `next: ${nxt.n} · ${nxt.name || ""}` : "last cue";
    const lvl = el.querySelector("input");
    if (!lvl.dataset.busy) lvl.value = p.level ?? 100;
  }
}

// ------------------------------------------------------------- faders
// A fader per group (its lights at that share of whatever they are doing,
// as on the desk's group masters), the grand master first.  A double tap
// puts one back to full.
const groupLevel = (g) => ((lite.group_masters || {})[g.n] ?? g.master ?? 100);
function faderRow({ key, name, value, send, colour }) {
  const out = h("output", `${value}%`);
  const inp = h("input", { type: "range", min: 0, max: 100, value, "aria-label": `${name} level` });
  const go = throttle((x) => send(x), 60);
  const set = (x) => { inp.value = x; out.textContent = `${x}%`; inp.dataset.busy = "1"; go(x); setTimeout(() => { delete inp.dataset.busy; }, 500); };
  inp.addEventListener("input", () => { out.textContent = `${inp.value}%`; inp.dataset.busy = "1"; go(+inp.value); });
  inp.addEventListener("change", () => setTimeout(() => { delete inp.dataset.busy; }, 400));
  const row = h("section.rm-fader", { dataset: { key } }, h("div.rm-ctl-top", h("b", name), out), inp);
  paint(row, colour);
  // double tap on the name or the number: back to 100 % (phones have no dblclick on a slider)
  let last = 0;
  row.querySelector(".rm-ctl-top").addEventListener("pointerup", () => {
    const now = performance.now();
    if (now - last < 350) { set(100); if (navigator.vibrate) navigator.vibrate(10); last = 0; } else last = now;
  });
  return row;
}
function renderFaders(grid) {
  const key = JSON.stringify(groupsThere().map((g) => [g.n, g.name, g.colour]));
  if (grid.dataset.faders === key && grid.querySelector(".rm-fader")) return;
  grid.dataset.faders = key;
  grid.replaceChildren(
    h("p.rm-hint", "Each group at a share of what it's doing · double-tap a name for 100%"),
    faderRow({ key: "master", name: "Grand master", value: snapOr("master", 100), colour: "#2fd6f5",
      send: (x) => act("master", { level: x }).catch(() => {}) }),
    ...groupsThere().map((g, i) => faderRow({ key: `g${g.n}`, name: g.name || `Group ${g.n}`, value: groupLevel(g), colour: groupColour(g, i),
      send: (x) => act("group_master", { group: g.n, level: x }).catch(() => {}) })));
}
function liveFaders() {
  for (const row of document.querySelectorAll(".rm-fader[data-key]")) {
    const inp = row.querySelector("input");
    if (inp.dataset.busy) continue;
    const k = row.dataset.key;
    const g = k === "master" ? null : groupsThere().find((x) => `g${x.n}` === k);
    const v = k === "master" ? snapOr("master", 100) : g ? groupLevel(g) : null;
    if (v === null || +inp.value === v) continue;
    inp.value = v;
    row.querySelector("output").textContent = `${v}%`;
  }
}

// ------------------------------------------------------------- timeline
const clock = (s) => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
function renderTimeline(grid) {
  if (grid.querySelector(".rm-tl")) return;
  const doc = (snap && snap.timeline) || {};
  const bar = h("div.rm-tl-bar", h("i.rm-tl-pos"), ...(doc.markers || []).map((m) => {
    const t = h("i.rm-tl-mark", { title: m.name || "" });
    t.style.left = `${(100 * (m.t ?? m.at ?? 0)) / (doc.length || 1)}%`;
    return t;
  }));
  // tap or drag on the bar: go to that point
  const seek = (e) => {
    const r = bar.getBoundingClientRect();
    const k = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    act("timeline_seek", { t: Math.round(k * (doc.length || 0) * 10) / 10 }).catch(() => {});
  };
  bar.addEventListener("pointerdown", (e) => { capture(bar, e.pointerId); seek(e); });
  grid.replaceChildren(h("section.rm-tl",
    h("div.rm-tl-time", h("b.rm-tl-now", "0:00"), h("span", " / " + clock(doc.length))),
    bar,
    h("div.rm-tl-btns",
      h("button.rm-tl-play", { type: "button", onclick: () => act(tlNow().playing ? "timeline_pause" : "timeline_play").catch(() => {}) }, "▶ Play"),
      h("button", { type: "button", onclick: () => act("timeline_stop").catch(() => {}) }, "■ Stop")),
    h("p.rm-tl-note", `${(doc.tracks || []).filter((t) => (t.clips || []).length).length} tracks · ${doc.bpm ? Math.round(doc.bpm) + " BPM · " : ""}${doc.loop ? "loops" : "plays once"}`)));
}
const tlNow = () => lite.timeline || ((snap && snap.timeline) || {}).transport || {};
function liveTimeline() {
  const el = document.querySelector(".rm-tl");
  if (!el) return;
  const t = tlNow();
  el.querySelector(".rm-tl-now").textContent = clock(t.pos);
  el.querySelector(".rm-tl-pos").style.width = `${(100 * (t.pos || 0)) / (t.length || 1)}%`;
  const play = el.querySelector(".rm-tl-play");
  play.textContent = t.playing ? "❚❚ Pause" : "▶ Play";
  play.classList.toggle("on", !!t.playing);
}

function renderTabs() {
  const nav = $("#rm-pages");
  const there = pagesThere();
  if (there.length && !there.includes(page)) page = there[0];
  const key = JSON.stringify([there, there.map(pageName), page]);
  if (nav.dataset.key === key) return;
  nav.dataset.key = key;
  nav.replaceChildren(...there.map((p) => h("button.rm-tab" + (p === page ? ".on" : ""), { type: "button", dataset: { p }, onclick: () => setPage(p) }, pageName(p))));
}

// what's on, what's held, the masters - many times a second, no redraw
function live() {
  if (view === "faders") liveFaders();
  if (view === "cues") liveCues();
  if (view === "timeline") liveTimeline();
  const on = activeIds();
  const pend = new Set(lite.quick_pending || []);
  for (const el of document.querySelectorAll(".rm-btn[data-id]")) {
    el.classList.toggle("on", on.has(el.dataset.id));
    el.classList.toggle("pending", pend.has(el.dataset.id));
  }
  for (const el of document.querySelectorAll(".rm-ctl.fader[data-id]")) {
    const inp = el.querySelector("input");
    if (inp.dataset.busy) continue;
    const b = quick().buttons.find((x) => x.id === el.dataset.id);
    if (!b) continue;
    const v = faderValue(b);
    inp.value = v;
    el.querySelector("output").textContent = `${v}%`;
  }
  const bo = !!snapOr("blackout", false);
  $("#rm-bo").classList.toggle("on", bo);
  $("#rm-bo").textContent = bo ? "Blackout ON" : "Blackout";
  const m = $("#rm-master");
  if (!m.dataset.busy) { m.value = snapOr("master", 100); $("#rm-master-val").textContent = `${m.value}%`; }
}

// ------------------------------------------------------- swipe between pages
(() => {
  let x0 = null, y0 = 0, t0 = 0;
  const grid = $("#rm-grid");
  grid.addEventListener("pointerdown", (e) => {
    if (view !== "buttons" || arranging || e.target.closest("input, .rm-xy")) { x0 = null; return; }
    x0 = e.clientX; y0 = e.clientY; t0 = performance.now();
  }, true);
  grid.addEventListener("pointerup", (e) => {
    if (x0 === null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    x0 = null;
    if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.8 || performance.now() - t0 > 600) return;
    const there = pagesThere(), i = there.indexOf(page);
    const next = there[i + (dx < 0 ? 1 : -1)];
    if (next) setPage(next);
  }, true);
})();

// --------------------------------------------------------- the bottom strip
$("#rm-bo").addEventListener("click", () => { if (connected) act("blackout", { state: snapOr("blackout", false) ? 0 : 1 }).catch(() => {}); });
{
  const m = $("#rm-master");
  const send = throttle((x) => act("master", { level: x }).catch(() => {}), 60);
  m.addEventListener("input", () => { m.dataset.busy = "1"; $("#rm-master-val").textContent = `${m.value}%`; send(+m.value); });
  m.addEventListener("change", () => setTimeout(() => { delete m.dataset.busy; }, 400));
}
// Release all: a hold, so a stray tap doesn't drop the whole look
{
  const rel = $("#rm-rel");
  let t = 0;
  rel.addEventListener("pointerdown", (e) => {
    if (!connected) return;
    e.preventDefault();
    rel.classList.add("arming");
    t = setTimeout(async () => {
      t = 0;
      rel.classList.remove("arming");
      if (navigator.vibrate) navigator.vibrate(20);
      await act("quick_release_all").catch(() => {});
      await act("playback_release_all").catch(() => {});
      note("Released every button and playback");
    }, 600);
  });
  const stop = () => { if (t) { clearTimeout(t); t = 0; rel.classList.remove("arming"); note("Hold Release all to let go of everything"); } };
  rel.addEventListener("pointerup", stop);
  rel.addEventListener("pointercancel", stop);
  rel.addEventListener("pointerleave", stop);
}

// ----------------------------------------------------- awake and full screen
// The screen stays on while the remote is open.  The browser's wake lock
// only works on a secure (https) address; on the desk's Wi-Fi address a
// silent, invisible video loop does the same job (started by a first tap:
// phones only play media after one).
let lock = null;
async function keepAwake() {
  if (document.visibilityState !== "visible") return;
  try {
    if ("wakeLock" in navigator && window.isSecureContext) {
      if (!lock) { lock = await navigator.wakeLock.request("screen"); lock.addEventListener("release", () => { lock = null; }); }
      return;
    }
  } catch (e) { /* fall through to the video */ }
  const v = $("#rm-awake");
  if (v.paused) v.play().catch(() => {});
}
window.addEventListener("pointerdown", keepAwake, { capture: true, passive: true });

const standalone = () => matchMedia("(display-mode: standalone), (display-mode: fullscreen)").matches || navigator.standalone === true;
const canFull = () => !!(document.fullscreenEnabled || document.webkitFullscreenEnabled);
function toggleFull() {
  const d = document;
  if (d.fullscreenElement || d.webkitFullscreenElement) (d.exitFullscreen || d.webkitExitFullscreen).call(d);
  else {
    const el = d.documentElement;
    const p = (el.requestFullscreen || el.webkitRequestFullscreen).call(el, { navigationUI: "hide" });
    if (p && p.catch) p.catch(() => note("This browser won't go full screen - add the remote to the home screen instead"));
  }
}
// Full screen by itself: a browser only allows it after a touch, so the
// first touch on the remote (and the next one after leaving full screen)
// takes it - the touch still does what it touched.  From the iPhone home
// screen it's full screen already; an Android home-screen icon opens a
// browser tab (Chrome makes an app only for an https site), so this is what
// takes the bars away there.  Off in ⋯ for this phone.
const AUTOFULL = "haze.remote.autofull";
const autoFull = () => { try { return localStorage.getItem(AUTOFULL) !== "0"; } catch (e) { return true; } };
const isFull = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
window.addEventListener("pointerup", () => {
  if (!autoFull() || !canFull() || isFull() || standalone() && matchMedia("(display-mode: fullscreen)").matches) return;
  if (!matchMedia("(pointer: coarse)").matches) return;   // (a phone or tablet - not a computer with a mouse)
  if (!$("#rm-connect").hidden) return;                // (typing the code: the keyboard first)
  const el = document.documentElement;
  try {
    const p = (el.requestFullscreen || el.webkitRequestFullscreen).call(el, { navigationUI: "hide" });
    if (p && p.catch) p.catch(() => {});
  } catch (e) { /* not here */ }
}, { capture: true, passive: true });
const iPhone = /iPhone|iPod/.test(navigator.userAgent);
function installTip() {
  if (iPhone) return "On iPhone: tap Share (the square with the arrow) → Add to Home Screen. It's named after the venue - type another name there if you like. The icon opens the remote full screen.";
  return "In Chrome: ⋮ → Add to Home screen. It's named after the venue - type another name there if you like. It opens full screen at your first touch.";
}

$("#rm-more").addEventListener("click", () => {
  const old = $(".rm-menu");
  if (old) { old.close(); return; }
  // a tap outside closes the menu, and only closes it - it must not also
  // press the button under the finger (switching a tab on or off keeps it open)
  const outside = (e) => {
    if (m.contains(e.target) || e.target.closest("#rm-more")) return;
    e.preventDefault(); e.stopPropagation();
    close();
  };
  const close = () => { m.remove(); document.removeEventListener("pointerdown", outside, true); };
  const m = h("div.rm-menu", {},
    canFull() ? h("button", { type: "button", onclick: () => { close(); toggleFull(); } }, isFull() ? "⛶ Leave full screen" : "⛶ Full screen") : null,
    canFull() ? h("button.rm-tabtoggle" + (autoFull() ? ".on" : ""), { type: "button", "aria-pressed": String(autoFull()),
      onclick: (e) => {
        const on = !autoFull();
        try { localStorage.setItem(AUTOFULL, on ? "1" : "0"); } catch (err) { /* this visit only */ }
        e.currentTarget.classList.toggle("on", on);
        e.currentTarget.setAttribute("aria-pressed", String(on));
        e.currentTarget.querySelector("span").textContent = on ? "on" : "off";
      } }, "Full screen on the first touch", h("span", autoFull() ? "on" : "off")) : null,
    standalone() ? null : h("button", { type: "button", onclick: () => { close(); note(installTip()); } }, "📱 Put it on the home screen"),
    // the show's other tabs, each shown or hidden on this phone
    ...viewsInShow().filter(([k]) => k !== "buttons").map(([k, label]) => h("button.rm-tabtoggle" + (hidden.includes(k) ? "" : ".on"), {
      type: "button", "aria-pressed": String(!hidden.includes(k)),
      onclick: (e) => {
        setHidden(k, !hidden.includes(k));
        const on = !hidden.includes(k);
        e.currentTarget.classList.toggle("on", on);
        e.currentTarget.setAttribute("aria-pressed", String(on));
        e.currentTarget.querySelector("span").textContent = on ? "shown" : "hidden";
      } }, `${label} tab`, h("span", hidden.includes(k) ? "hidden" : "shown"))),
    view === "buttons" ? h("button", { type: "button", onclick: () => { close(); setArranging(true); } }, "✥ Arrange buttons on this phone") : null,
    h("button", { type: "button", onclick: () => { close(); render(true); } }, "↻ Redraw"),
    ...fxMenuItems(close),
    // a big tablet can run the whole desk (patch, programmer, the 3D)
    Math.min(screen.width, screen.height) >= 600 ? h("button", { type: "button", onclick: () => { location.href = "/?desk=1" + (code ? "&code=" + encodeURIComponent(code) : ""); } }, "🖥 Full desk") : null,
    h("button", { type: "button", onclick: () => { close(); forget(); } }, "Sign out of this desk"));
  m.close = close;
  document.body.append(m);
  setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
});

// ARM FX and KILL FX, kept in the ⋯ menu (not on the page, where a stray
// touch could stop the show): each needs a second tap within 3 s.  ARM lets
// the fire and laser buttons go, as the desk's ARM FX does; KILL FX stops
// every effect and laser at once and disarms.  Only when the show has them.
function fxMenuItems(close) {
  const sfx = snapOr("sfx", {}) || {};
  if (!Object.keys(sfx.heads || {}).length) return [];
  const confirmBtn = (cls, label, again, run) => {
    let armedAt = -Infinity;                     // (not 0: the page's clock starts at 0 - a first tap is never the second)
    const el = h("button.rm-fxbtn" + cls, { type: "button" }, label);
    el.addEventListener("click", () => {
      if (performance.now() - armedAt < 3000) { close(); run(); return; }
      armedAt = performance.now();
      el.textContent = again;
      el.classList.add("confirm");
      setTimeout(() => { if (el.isConnected && performance.now() - armedAt >= 2900) { el.textContent = label; el.classList.remove("confirm"); } }, 3000);
    });
    return el;
  };
  const armed = !!sfx.armed;
  return [
    h("div.rm-menu-head", "Special effects"),
    armed
      ? confirmBtn(".arm.on", "ARMED - tap to disarm", "Tap again to disarm", () => act("fx_arm", { state: false }).then(() => note("Effects disarmed")))
      : confirmBtn(".arm", "ARM FX", "Tap again to ARM FX", () => act("fx_arm", { state: true }).then((r) => note(r.summary || "Effects armed"))),
    confirmBtn(".kill", "KILL FX - stop every effect", "Tap again to KILL FX", () => act("fx_kill", {}).then(() => note("Every effect stopped, disarmed"))),
  ];
}

// first visit in a browser (not from the home screen): one hint how to make it full screen
function firstTip() {
  if (standalone()) return;
  let seen = false;
  try { seen = localStorage.getItem("haze.remote.tip") === "1"; localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* ignore */ }
  if (!seen) setTimeout(() => note(installTip()), 1500);
}

// ----------------------------------------------- one icon for every venue
// The desk answers to a name as well as its address (haze.local, app/mdns.py).
// The address changes from venue to venue; the name doesn't.  Opened at the
// address (the QR code), the remote checks this phone can reach the same desk
// by its name, and moves there - so the home-screen icon, made after that,
// opens the desk at any venue.  A phone that can't (some Android browsers
// don't look names up this way) stays on the address and says so once.
let moveTried = false;
async function moveToName() {
  if (moveTried) return;
  moveTried = true;
  let info;
  try { info = await (await fetch("/api/remote", { headers: headers(false) })).json(); } catch (e) { return; }
  const name = info && info.name;
  if (!name || location.hostname.toLowerCase() === name.toLowerCase()) return;
  const base = `http://${name}:${location.port || info.port}`;
  let hello = null;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 3000);
    hello = await (await fetch(base + "/hello.json", { signal: ctl.signal, cache: "no-store" })).json();
    clearTimeout(t);
  } catch (e) { hello = null; }
  if (!hello || hello.id !== info.id) {
    let told = false;
    try { told = localStorage.getItem("haze.remote.noname") === "1"; localStorage.setItem("haze.remote.noname", "1"); } catch (e) { /* ignore */ }
    if (!told) note(`This phone can't find the desk by its name (${name}), only by this address - at another venue, scan the desk's QR code again.`);
    return;
  }
  // this phone's own settings come along (they're kept per address)
  const carry = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("haze.remote.") && k !== KEY) carry[k] = localStorage.getItem(k);
    }
  } catch (e) { /* nothing to carry */ }
  let hash = "";
  try {
    const packed = btoa(unescape(encodeURIComponent(JSON.stringify(carry)))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    if (packed.length < 12000) hash = "#carry=" + packed;
  } catch (e) { hash = ""; }
  const url = `${base}/remote.html?code=${encodeURIComponent(code)}${hash}`;
  if (standalone()) {
    // (an icon made at the address: it can't change where it points - make it once more from the name)
    banner(`This icon only works on this Wi-Fi. Open ${name} once and put Haze on the home screen again - that icon finds the desk at any venue.`);
    $("#rm-banner").onclick = () => { location.href = url; };
    return;
  }
  location.replace(url);
}

// --------------------------------------------------------------- connecting
function askCode(why = "") {
  $("#rm-connect").hidden = false;
  $("#rm").hidden = true;
  $("#rm-err").textContent = why;
  $("#rm-code").value = "";
  setTimeout(() => $("#rm-code").focus(), 50);
}
function forget() {
  try { localStorage.removeItem(KEY); sessionStorage.removeItem(KEY); } catch (e) { /* ignore */ }
  code = "";
  location.reload();
}
async function join() {
  const c = clean($("#rm-code").value);
  if (!c) return;
  const r = await fetch("/api/console", { method: "POST", headers: { ...headers(true), "X-Haze-Token": c }, body: JSON.stringify({ action: "status", params: {} }) }).catch(() => null);
  if (!r) { $("#rm-err").textContent = "The desk isn't answering. Is this phone on the same Wi-Fi?"; return; }
  if (r.status === 401) { $("#rm-err").textContent = "That code isn't right - type the one the desk shows."; return; }
  code = c;
  try { ($("#rm-keep").checked ? localStorage : sessionStorage).setItem(KEY, c); } catch (e) { /* ignore */ }
  location.reload();
}
$("#rm-join").addEventListener("click", join);
$("#rm-code").addEventListener("keydown", (e) => { if (e.key === "Enter") join(); });

let resizeT = 0;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => render(true), 120); });
setLink(false);
firstTip();
stream();

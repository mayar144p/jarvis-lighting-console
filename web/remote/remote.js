// The DJ-booth remote: a phone or tablet page with only what a DJ needs -
// the buttons, tap tempo, the autopilot, the master and blackout.  It uses
// the same API (and the same access token) as the desk; nothing here can
// patch, delete or save.
import { act, get } from "/app/api.js";

const $ = (s) => document.querySelector(s);
let snap = {}, lite = {}, page = 1, recv = { t: 0, beats: 0, bpm: 120 };

const say = (t) => { $("#status").textContent = t; };
const call = async (action, params = {}) => {
  try {
    const r = await act(action, params);
    if (!r.ok && r.error) say(r.error);
    else if (r.summary) say(r.summary);
    return r;
  } catch (e) { say(e.message); return {}; }
};

function drawButtons() {
  const q = snap.quick || { buttons: [] };
  const pages = [...new Set(q.buttons.map((b) => b.page))].sort((a, b) => a - b);
  if (pages.length && !pages.includes(page)) page = pages[0];
  $("#pages").replaceChildren(...pages.map((p) => {
    const b = document.createElement("button");
    b.textContent = (q.names && q.names[p]) || `Page ${p}`;
    b.className = p === page ? "on" : "";
    b.onclick = () => { page = p; drawButtons(); };
    return b;
  }));
  const active = new Set((lite.quick_active || q.active || []).map(String));
  const btns = q.buttons.filter((b) => b.page === page).sort((a, b) => a.slot - b.slot);
  if (!btns.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "No buttons yet: make them on the desk (Buttons page).";
    $("#grid").replaceChildren(d);
    return;
  }
  $("#grid").replaceChildren(...btns.map((b) => {
    const el = document.createElement("button");
    el.className = "q" + (active.has(b.id) ? " on" : "");
    el.style.setProperty("--tint", b.tint || b.colour || "#94a3b8");
    el.textContent = b.label;
    const hold = b.mode === "hold";
    el.onpointerdown = (e) => { e.preventDefault(); call("quick_press", { id: b.id, down: true }); el.classList.add("on"); };
    el.onpointerup = el.onpointercancel = el.onpointerleave = () => { if (hold) call("quick_press", { id: b.id, down: false }); };
    return el;
  }));
}

function drawLive() {
  const t = lite.tempo || snap.tempo;
  if (t) {
    recv = { t: performance.now(), beats: (t.beat - 1) + (t.phase || 0), bpm: t.bpm };
    $("#bpm").textContent = t.bpm.toFixed(t.bpm % 1 ? 1 : 0);
  }
  $("#bo").classList.toggle("on", !!lite.blackout);
  const ap = lite.autopilot || snap.autopilot || {};
  $("#auto").classList.toggle("on", !!ap.on);
  $("#auto").textContent = ap.on ? `Autopilot${ap.bars_left != null ? " · " + ap.bars_left : ""}` : "Autopilot";
  if (document.activeElement !== $("#master") && lite.master != null) { $("#master").value = lite.master; $("#mv").textContent = lite.master; }
  const active = new Set((lite.quick_active || []).map(String));
  document.querySelectorAll(".q").forEach((el, i) => {
    const b = ((snap.quick || {}).buttons || []).filter((x) => x.page === page).sort((a, c) => a.slot - c.slot)[i];
    if (b) el.classList.toggle("on", active.has(b.id));
  });
}

(function beat() {
  requestAnimationFrame(beat);
  const beats = recv.beats + (performance.now() - recv.t) / 1000 * recv.bpm / 60;
  const lit = beats - Math.floor(beats) < 0.18;
  $("#dot").className = "dot" + (lit ? (Math.floor(beats) % 4 === 0 ? " on one" : " on") : "");
})();

async function poll() {
  try {
    lite = await get("/api/console?lite=1");
    drawLive();
    say("");
  } catch (e) { say("lost the desk - retrying… " + e.message); }
  setTimeout(poll, 400);
}
async function full() {
  try { snap = await get("/api/console"); drawButtons(); } catch (e) { /* the poll says */ }
  setTimeout(full, 4000);
}

$("#tap").addEventListener("pointerdown", (e) => { e.preventDefault(); call("tempo_tap"); });
$("#bo").addEventListener("click", () => call("blackout", { state: lite.blackout ? 0 : 1 }));
$("#auto").addEventListener("click", () => call("autopilot", { state: !((lite.autopilot || {}).on) }));
$("#next").addEventListener("click", () => call("autopilot_next"));
$("#big").addEventListener("click", () => call("autopilot_next", { biggest: true }));
let mt = 0;
$("#master").addEventListener("input", (e) => {
  $("#mv").textContent = e.target.value;
  clearTimeout(mt);
  mt = setTimeout(() => call("master", { level: +e.target.value }), 60);
});
full();
poll();

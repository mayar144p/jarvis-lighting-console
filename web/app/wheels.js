// The programmer's value bar, like a real desk's encoders: along its foot,
// the open tab's attributes for the selected lights - each with its value
// now (where the desk shows it: level, colour, pan and tilt) and a line
// filled to it.  Drag one sideways (or scroll on it) to change it; hold
// Shift - or tap Fine - for fine.  Each moves every selected light by the
// same share of its travel, so a mixed rig keeps its spread.
import { state, on } from "./store.js";
import { act } from "./api.js";
import { h, $ } from "./ui.js";

const WHEELS = {
  colour: [["red", "Red"], ["green", "Green"], ["blue", "Blue"], ["white", "White"], ["cyan", "Cyan"], ["magenta", "Magenta"], ["yellow", "Yellow"]],
  position: [["pan", "Pan"], ["tilt", "Tilt"]],

  beam: [["zoom", "Zoom"], ["focus", "Focus"], ["iris", "Iris"], ["frost", "Frost"], ["gobo_rot", "Gobo spin"], ["prism_rot", "Prism spin"]],
};
const COARSE = 0.01, FINE = 0.001;                 // a share of the travel per notch
let fine = false;
let tab = "colour";

function rolesOfSelection() {
  const sel = new Set((state.lite && state.lite.selected) || (state.snap && state.snap.selected) || []);
  const roles = new Set();
  for (const hd of (state.snap && state.snap.patch) || []) if (sel.has(hd.head_no)) for (const r of hd.map || []) roles.add(r);
  return roles;
}

// notches add up between requests: a fast spin is one request every 40 ms, never a flood
const owed = new Map();
function turn(attr, notches) {
  const had = owed.has(attr);
  owed.set(attr, (owed.get(attr) || 0) + notches * (fine ? FINE : COARSE));
  if (had) return;
  setTimeout(() => {
    const step = owed.get(attr); owed.delete(attr);
    if (step) act("nudge", { attribute: attr, step: Math.max(-0.5, Math.min(0.5, step)) }).catch(() => {});
  }, 40);
}

// what a cell shows: the value the selected lights have now, where the desk
// says it (their level, colour and aim - the 3D's own feed); "mix" when they
// differ, "–" when it isn't known here
const selHeads = () => (state.lite && state.lite.selected) || (state.snap && state.snap.selected) || [];
const hexPart = (hex, i) => { const m = /^#?([0-9a-f]{6})$/i.exec(hex || ""); return m ? parseInt(m[1].slice(i * 2, i * 2 + 2), 16) / 255 : null; };
function valueOf(attr) {
  const looks = state.looks || {};
  const vals = [];
  for (const n of selHeads()) {
    const lk = looks[n];
    if (!lk) continue;
    const v = attr === "pan" || attr === "tilt" ? lk[attr]
      : attr === "red" ? hexPart(lk.hex, 0) : attr === "green" ? hexPart(lk.hex, 1) : attr === "blue" ? hexPart(lk.hex, 2) : undefined;
    if (typeof v === "number") vals.push(v);
  }
  if (!vals.length) return null;
  const pct = vals.map((v) => Math.round(Math.max(0, Math.min(1, v)) * 100));
  return new Set(pct).size > 1 ? { mixed: true, v: pct[0] } : { v: pct[0] };
}
function cell(attr, label) {
  const val = h("b.pv-val", "–");
  const el = h("div.pv", { role: "slider", tabindex: 0, "aria-label": `${label}: drag sideways or scroll to change it, Shift for fine`,
    "aria-valuetext": "–", dataset: { attr } }, h("span.pv-name", label), val, h("i.pv-bar"));
  let finger = null;                             // one finger moves it
  el.addEventListener("pointerdown", (e) => {
    if (finger !== null || e.button !== 0) return;
    finger = e.pointerId;
    el.setPointerCapture(e.pointerId);
    el.classList.add("on");
    let last = e.clientX;
    const move = (ev) => {
      if (ev.pointerId !== finger) return;
      const n = Math.trunc((ev.clientX - last) / 4);           // 4 px a notch, right = more
      if (n) { last += n * 4; const was = fine; if (ev.shiftKey) fine = true; turn(attr, n); fine = was; }
    };
    const up = (ev) => {
      if (ev.pointerId !== finger) return;
      finger = null;
      el.classList.remove("on");
      for (const t of ["pointerup", "pointercancel", "lostpointercapture"]) el.removeEventListener(t, up);
      el.removeEventListener("pointermove", move);
    };
    el.addEventListener("pointermove", move);
    for (const t of ["pointerup", "pointercancel", "lostpointercapture"]) el.addEventListener(t, up);
  });
  el.addEventListener("wheel", (e) => { e.preventDefault(); const was = fine; if (e.shiftKey) fine = true; turn(attr, e.deltaY < 0 ? 1 : -1); fine = was; }, { passive: false });
  el.addEventListener("keydown", (e) => {
    const n = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 }[e.key];
    if (!n) return;
    e.preventDefault();
    const was = fine; if (e.shiftKey) fine = true; turn(attr, n); fine = was;
  });
  return el;
}
function showValues() {
  const box = $("#prog-wheels");
  if (!box || box.hidden) return;
  for (const el of box.querySelectorAll(".pv")) {
    const got = el.classList.contains("off") ? null : valueOf(el.dataset.attr);
    const text = !got ? "–" : got.mixed ? "mix" : `${got.v}%`;
    const b = el.querySelector(".pv-val");
    if (b.textContent !== text) b.textContent = text;
    el.setAttribute("aria-valuetext", text);
    el.style.setProperty("--v", got ? `${got.v}%` : "0%");
  }
}

export function renderWheels(name = tab) {
  tab = name;
  const box = $("#prog-wheels");
  if (!box) return;
  const roles = rolesOfSelection();
  // every wheel of the tab is always in its place; one the selected lights
  // don't have (Tilt on a light that only pans) is greyed, not taken away
  // - the row never shifts under the pointer
  const list = WHEELS[name] || [];
  box.hidden = !WHEELS[name];
  const sig = name + list.map(([a]) => (roles.has(a) ? a : "-")).join();
  if (box.dataset.sig === sig) return;              // the same wheels: keep them (a drag in progress)
  box.dataset.sig = sig;
  const fineBtn = h("button.btn.small" + (fine ? ".on" : ""), { type: "button", "aria-pressed": String(fine),
    title: "Fine: a tenth of the step (or hold Shift)", onclick: () => { fine = !fine; fineBtn.classList.toggle("on", fine); fineBtn.setAttribute("aria-pressed", String(fine)); } }, "Fine");
  box.replaceChildren(...list.map(([a, l]) => {
    const el = cell(a, l);
    el.classList.add("f-" + name);
    if (!roles.has(a)) {
      el.classList.add("off");
      el.title = roles.size ? `The selected lights have no ${l.toLowerCase()}` : "Select lights to change this";
      el.tabIndex = -1;
    }
    return el;
  }), h("span.grow"), fineBtn);
  showValues();
}

export function initWheels() {
  on("snapshot", () => renderWheels());
  on("lite", () => renderWheels());
  on("looks", showValues);
}

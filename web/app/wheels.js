// On-screen encoder wheels (backlog A10 item 9), like a real desk's: under
// the programmer tabs, the open tab's attributes for the selected lights.
// Drag up / down (or scroll) to turn; hold Shift - or tap FINE - for fine.
// Each wheel moves every selected light by the same share of its travel,
// so a mixed rig keeps its spread.
import { state, on } from "./store.js";
import { act } from "./api.js";
import { h, $ } from "./ui.js";

const WHEELS = {
  intensity: [["dimmer", "Dimmer"]],
  colour: [["red", "Red"], ["green", "Green"], ["blue", "Blue"], ["white", "White"], ["cyan", "Cyan"], ["magenta", "Magenta"], ["yellow", "Yellow"]],
  position: [["pan", "Pan"], ["tilt", "Tilt"]],

  beam: [["zoom", "Zoom"], ["focus", "Focus"], ["iris", "Iris"], ["frost", "Frost"], ["gobo_rot", "Gobo spin"], ["prism_rot", "Prism spin"]],
};
const COARSE = 0.01, FINE = 0.001;                 // a share of the travel per notch
let fine = false;
let tab = "intensity";

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

function wheel(attr, label) {
  const knob = h("div.wheel-knob", { role: "slider", tabindex: 0, "aria-label": `${label} wheel: arrow keys turn it, Shift for fine`,
    "aria-valuetext": "relative" }, h("i"));
  let angle = 0;
  const spin = (n) => { angle += n * 12; knob.style.setProperty("--a", `${angle}deg`); turn(attr, n); };
  let finger = null;                             // one finger turns it
  knob.addEventListener("pointerdown", (e) => {
    if (finger !== null) return;
    finger = e.pointerId;
    knob.setPointerCapture(e.pointerId);
    let last = e.clientY;
    const move = (ev) => {
      if (ev.pointerId !== finger) return;
      const n = Math.trunc((last - ev.clientY) / 4);          // 4 px a notch, up = more
      if (n) { last -= n * 4; const was = fine; if (ev.shiftKey) fine = true; spin(n); fine = was; }
    };
    const up = (ev) => {
      if (ev.pointerId !== finger) return;
      finger = null;
      for (const t of ["pointerup", "pointercancel", "lostpointercapture"]) knob.removeEventListener(t, up);
      knob.removeEventListener("pointermove", move);
    };
    knob.addEventListener("pointermove", move);
    for (const t of ["pointerup", "pointercancel", "lostpointercapture"]) knob.addEventListener(t, up);
  });
  knob.addEventListener("wheel", (e) => { e.preventDefault(); const was = fine; if (e.shiftKey) fine = true; spin(e.deltaY < 0 ? 1 : -1); fine = was; }, { passive: false });
  knob.addEventListener("keydown", (e) => {
    const n = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 }[e.key];
    if (!n) return;
    e.preventDefault();
    const was = fine; if (e.shiftKey) fine = true; spin(n); fine = was;
  });
  return h("div.wheel", knob, h("span.small", label));
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
    title: "Fine: a tenth of the step (or hold Shift)", onclick: () => { fine = !fine; fineBtn.classList.toggle("on", fine); fineBtn.setAttribute("aria-pressed", String(fine)); } }, "FINE");
  box.replaceChildren(...list.map(([a, l]) => {
    const el = wheel(a, l);
    if (!roles.has(a)) {
      el.classList.add("off");
      el.title = roles.size ? `The selected lights have no ${l.toLowerCase()}` : "Select lights to turn this";
      el.querySelector(".wheel-knob").tabIndex = -1;
    }
    return el;
  }), fineBtn);
}

// Other places with wheels of their own (the palette view's Knobs window):
// [box, [[attr, label]...]] - kept up to date with the selection like the strip
const extra = new Set();
export function mountWheels(box, list) {
  extra.add([box, list]);
  fill(box, list);
}
function fill(box, list) {
  const roles = rolesOfSelection();
  const sig = list.map(([a]) => (roles.has(a) ? a : "-")).join() + fine;
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const fineBtn = h("button.btn.small" + (fine ? ".on" : ""), { type: "button", "aria-pressed": String(fine),
    title: "Fine: a tenth of the step (or hold Shift)", onclick: () => { fine = !fine; refreshExtra(); renderWheels(); } }, "FINE");
  box.replaceChildren(...list.map(([a, l]) => {
    const el = wheel(a, l);
    if (!roles.has(a)) {
      el.classList.add("off");
      el.title = roles.size ? `The selected lights have no ${l.toLowerCase()}` : "Select lights to turn this";
      el.querySelector(".wheel-knob").tabIndex = -1;
    }
    return el;
  }), fineBtn);
}
function refreshExtra() {
  for (const it of [...extra]) { if (!it[0].isConnected) { extra.delete(it); continue; } fill(it[0], it[1]); }
}

export function initWheels() {
  on("snapshot", refreshExtra);
  on("lite", refreshExtra);
  on("snapshot", () => renderWheels());
  on("lite", () => renderWheels());
}

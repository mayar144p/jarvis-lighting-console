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
  knob.addEventListener("pointerdown", (e) => {
    knob.setPointerCapture(e.pointerId);
    let last = e.clientY;
    const move = (ev) => {
      const n = Math.trunc((last - ev.clientY) / 4);          // 4 px a notch, up = more
      if (n) { last -= n * 4; const was = fine; if (ev.shiftKey) fine = true; spin(n); fine = was; }
    };
    const up = () => { knob.removeEventListener("pointermove", move); knob.removeEventListener("pointerup", up); };
    knob.addEventListener("pointermove", move);
    knob.addEventListener("pointerup", up);
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
  const list = (WHEELS[name] || []).filter(([a]) => roles.has(a));
  // the row keeps its place on a tab with wheels (selecting lights never
  // shifts the page under the pointer); only a tab change shows / hides it
  box.hidden = !WHEELS[name];
  const sig = name + list.map(([a]) => a).join();
  if (!list.length) {
    if (box.dataset.sig !== sig) box.replaceChildren(h("span.muted.small", "Select lights to get wheels for them here."));
    box.dataset.sig = sig;
    return;
  }
  if (box.dataset.sig === sig) return;              // the same wheels: keep them (a drag in progress)
  box.dataset.sig = sig;
  const fineBtn = h("button.btn.small" + (fine ? ".on" : ""), { type: "button", "aria-pressed": String(fine),
    title: "Fine: a tenth of the step (or hold Shift)", onclick: () => { fine = !fine; fineBtn.classList.toggle("on", fine); fineBtn.setAttribute("aria-pressed", String(fine)); } }, "FINE");
  box.replaceChildren(...list.map(([a, l]) => wheel(a, l)), fineBtn);
}

export function initWheels() {
  on("snapshot", () => renderWheels());
  on("lite", () => renderWheels());
}

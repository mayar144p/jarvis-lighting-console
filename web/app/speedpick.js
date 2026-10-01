// Tap-and-type knobs for speeds and sizes: big preset chips (Slow / Medium
// / Fast), − and + steps, ½× and 2×, and a number you can type.  A slider
// is a poor way to set a speed on a busy desk: a drag overshoots, a tap
// doesn't.
import { h } from "./ui.js";

const round = (v) => (Math.abs(v) >= 10 ? Math.round(v) : Math.abs(v) >= 1 ? Math.round(v * 10) / 10 : Math.round(v * 100) / 100);

/**
 * knob({label, value, unit, presets: [[label, value, title?]], min, max,
 *       step: factor for − / + (1.25) or {add: n}, halves: show ½× 2×,
 *       invert: ½× / 2× act on 1/value (seconds per turn: 2× faster = half
 *       the seconds), onSet(value)})
 */
export function knob(o) {
  let cur = +o.value;
  const clamp = (v) => Math.max(o.min ?? -Infinity, Math.min(o.max ?? Infinity, v));
  const input = h("input.knob-in", { type: "number", value: round(cur), min: o.min, max: o.max,
    step: "any", inputmode: "decimal", "aria-label": o.label, title: o.title || `Type a ${o.label.toLowerCase()} and press Enter` });
  const chips = [];
  const show = () => {
    input.value = String(round(cur));
    for (const [c, v] of chips) c.classList.toggle("on", Math.abs(v - cur) < 1e-6 * Math.max(1, Math.abs(v)));
  };
  const set = (v) => {
    if (!Number.isFinite(v)) { show(); return; }
    cur = clamp(round(v));
    show();
    o.onSet(cur);
  };
  input.addEventListener("change", () => set(+input.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
  const step = o.step || 1.25;
  const nudge = (dir) => () => set(typeof step === "object" ? cur + dir * step.add : dir > 0 ? cur * step : cur / step);
  const preset = (o.presets || []).map(([l, v, t]) => {
    const c = h("button.chip.knob-p", { title: t || "", onclick: () => set(v) }, l);
    chips.push([c, v]);
    return c;
  });
  const faster = o.invert ? 0.5 : 2;
  const halves = o.halves === false ? [] : [
    h("button.chip.knob-x", { title: o.invert ? "Half as fast" : "Half", onclick: () => set(cur / faster) }, "½×"),
    h("button.chip.knob-x", { title: o.invert ? "Twice as fast" : "Double", onclick: () => set(cur * faster) }, "2×")];
  const box = h("div.knob",
    h("span.knob-k", o.label),
    preset.length ? h("span.knob-presets", ...preset) : null,
    h("span.knob-num",
      h("button.btn.knob-step", { title: o.minusTitle || "Less", onclick: nudge(-1) }, "−"),
      input, o.unit ? h("span.knob-u", o.unit) : null,
      h("button.btn.knob-step", { title: o.plusTitle || "More", onclick: nudge(1) }, "+"),
      ...halves));
  box.setValue = (v) => { if (document.activeElement !== input) { cur = +v; show(); } };
  show();
  return box;
}

// a running effect's speed, the way people say it
export const SPEED_PRESETS = [["Slow", 0.3, "About one cycle every 3 s"], ["Medium", 1, "The effect's normal speed"],
  ["Fast", 3, "Three times the normal speed"]];
// a movement: seconds for one turn
export const TURN_PRESETS = [["Slow", 16], ["Medium", 8], ["Fast", 4], ["Very fast", 2, "As fast as most motors can follow"]];
export const SIZE_PRESETS = [["Small", 10], ["Medium", 20], ["Big", 45], ["Huge", 90]];

// The autopilot: a cue list plays itself, a new look every phrase on the
// beat clock - calmer or bigger with the room, the biggest on a drop.
import { run } from "./actions.js";
import { state, on } from "./store.js";
import { h, modal } from "./ui.js";

const ap = () => (state.lite && state.lite.autopilot) || (state.snap && state.snap.autopilot) || {};
const TIER = { low: "calm", mid: "medium", high: "big" };

export function openAutopilot() {
  const pbs = (state.snap && state.snap.playbacks) || [];
  const cur = ap();
  const pb = h("select.select", ...pbs.map((p) => h("option", { value: p.n },
    `${p.name || "Playback " + p.n} · ${(p.stack || []).length} cue(s)`)));
  pb.value = String(cur.playback || 1);
  const bars = h("div.seg", ...[4, 8, 16, 32].map((b) => h("button", { type: "button", dataset: { b },
    onclick: () => run("autopilot", { bars: b }) }, `${b} bars`)));
  const follow = h("input", { type: "checkbox" });
  const drop = h("input", { type: "checkbox" });
  const toggle = h("button.btn.primary", "Start");
  const status = h("p.small", "");
  pb.addEventListener("change", () => run("autopilot", { playback: +pb.value }, { toast: true }));
  follow.addEventListener("change", () => run("autopilot", { follow_sound: follow.checked }));
  drop.addEventListener("change", () => run("autopilot", { on_drop: drop.checked }));
  toggle.addEventListener("click", () => run("autopilot", { state: !ap().on, playback: +pb.value }, { toast: true }));
  const draw = () => {
    const a = ap();
    toggle.textContent = a.on ? "Stop" : "Start";
    toggle.classList.toggle("primary", !a.on);
    bars.querySelectorAll("button").forEach((b) => b.classList.toggle("on", +b.dataset.b === a.bars));
    follow.checked = a.follow_sound !== false;
    drop.checked = a.on_drop !== false;
    status.textContent = !a.on ? "Off." : [
      a.bars_left != null ? `Next look in ${a.bars_left} bar(s)` : "Waiting for the next phrase",
      a.last ? `now: ${a.last.name} (${TIER[a.last.tier] || a.last.tier}, ${a.last.why})` : "",
      a.room ? `the room is ${TIER[a.room]}` : (a.follow_sound ? "no sound: going round the list in order" : ""),
    ].filter(Boolean).join(" · ");
  };
  const off1 = on("lite", draw), off2 = on("snapshot", draw);
  const body = h("div.ap",
    h("p.muted.small", "For a set nobody programmed: record a handful of looks as cues in one playback, and the autopilot moves between them "
      + "on the phrase (on the beat clock: tap it, MIDI clock, the CDJs or the room). With Sound on, it picks calmer or bigger looks as the room gets quieter or louder."),
    h("div.form-grid", h("label.field", h("span", "Looks from"), pb)),
    h("div.field", h("span.muted.small", "A new look every"), bars),
    h("label.check", follow, h("span", "Follow the room's energy (Sound)")),
    h("label.check", drop, h("span", "On a drop, the biggest look straight away")),
    h("div.row-btns", toggle,
      h("button.btn", { onclick: () => run("autopilot_next", {}, { toast: true }) }, "Next look now"),
      h("button.btn", { onclick: () => run("autopilot_next", { biggest: true }, { toast: true }) }, "Biggest now")),
    status);
  const close = modal({ title: "Autopilot", body, onClose: () => { off1(); off2(); } });
  draw();
  return close;
}

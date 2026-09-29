// The playback strip: ten faders with GO, and the grand master.
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { $, h, vfader, throttle, menu, promptBox, confirmBox } from "./ui.js";
import { openCueList, openCueDialog } from "./dialogs.js";
import { setTimelineVisible } from "./timeline.js";

const cards = new Map();
let gm = null;
let focusPb = 1;

export const focusedPlayback = () => focusPb;

function cueLabel(c) { return c ? (c.name || `Cue ${c.n}`) : ""; }

function build() {
  const strip = $("#pb-strip");
  const count = ((state.snap && state.snap.playbacks) || []).length || 10;
  if (cards.size === count) return;
  strip.replaceChildren();
  cards.clear();
  for (let n = 1; n <= count; n++) {
    const fader = h("div.vfader", { "aria-label": `Playback ${n} level` });
    const name = h("span.name", { title: "Cue list" });
    const cue = h("div.pb-cue");
    const prog = h("div.pb-prog", h("i"));
    const card = h("div.pb", { dataset: { pb: n } },
      fader,
      h("div.pb-top", h("span.n", `PB${n}`), name,
        h("button.btn.ghost.small.icon", { title: "Playback options", onclick: (e) => pbMenu(e.currentTarget, n) }, "⋯")),
      h("div", cue, prog),
      h("div.pb-btns",
        h("button", { title: "Back one cue", onclick: () => { focusPb = n; run("cue_back", { playback: n }); } }, "◀"),
        h("button.go", { title: "GO: next cue", onclick: () => { focusPb = n; run("cue_go", { playback: n }); } }, "GO"),
        h("button", { title: "Release this playback", onclick: () => run("playback_release", { playback: n }) }, "■")));
    name.addEventListener("click", () => openCueList(n));
    card.addEventListener("pointerdown", () => setFocus(n));
    const send = throttle((level) => run("playback_level", { playback: n, level }, { silentError: true }), 60);
    const fd = vfader(fader, { min: 0, max: 100, onInput: send });
    cards.set(n, { card, fd, name, cue, prog });
    strip.append(card);
  }
  setFocus(focusPb);
}

function setFocus(n) {
  focusPb = n;
  for (const [k, c] of cards) c.card.classList.toggle("focus", k === n);
}

function pbMenu(btn, n) {
  const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === n) || {};
  const f = pb.follow || {};
  menu(btn, [
    { label: "Record cue from programmer…", run: () => openCueDialog(n) },
    { label: "Edit cue list…", run: () => openCueList(n) },
    "-",
    { label: f.on ? "Stop auto-follow" : "Auto-follow (run the list by itself)", run: () => run("follow_set", { playback: n, on: !f.on }, { toast: true }) },
    { label: f.loop ? "Stop looping" : "Loop the cue list", run: () => run("follow_set", { playback: n, loop: !f.loop }, { toast: true }) },
    { label: "Crossfade time for the fader…", run: async () => {
      const v = await promptBox("Fader crossfade", "Seconds a fader move takes (0 = instant)", String(pb.xfade_s ?? 0));
      if (v !== null) run("playback_level", { playback: n, level: pb.level ?? 100, xfade: +v || 0 });
    } },
    "-",
    { label: "Release", run: () => run("playback_release", { playback: n }) },
    { label: "Delete every cue on this playback", danger: true, disabled: !(pb.stack || []).length, run: async () => {
      if (!(await confirmBox("Delete cues", `Delete all ${(pb.stack || []).length} cue(s) on PB${n}? Ctrl+Z brings them back.`, { ok: "Delete", danger: true }))) return;
      for (let i = (pb.stack || []).length; i >= 1; i--) await run("delete_cue", { playback: n, cue: i }, { silentError: true });
    } },
  ]);
}

function render() {
  build();
  const pbs = (state.snap && state.snap.playbacks) || [];
  for (const pb of pbs) {
    const c = cards.get(pb.n);
    if (!c) continue;
    const stack = pb.stack || [];
    const cur = pb.index >= 0 ? stack[pb.index] : null;
    const next = stack[pb.index + 1] || (pb.follow && pb.follow.loop ? stack[0] : null);
    c.card.classList.toggle("active", !!pb.active);
    c.card.classList.toggle("empty", !stack.length);
    c.name.textContent = pb.name || (stack.length ? `${stack.length} cue${stack.length > 1 ? "s" : ""}` : "Empty");
    if (!stack.length) {
      c.cue.replaceChildren("Record a cue here");
    } else {
      c.cue.replaceChildren(
        h("b", cur ? `${pb.index + 1}  ${cueLabel(cur)}` : "Not started"),
        h("span.next", next ? `next: ${cueLabel(next)}` : "end of list"));
    }
    const f = pb.follow || {};
    const bar = c.prog.firstChild;
    if (f.on && typeof f.in === "number" && f.delay) {
      bar.style.width = Math.max(0, Math.min(100, (1 - f.in / f.delay) * 100)) + "%";
    } else if (f.on && typeof f.in === "number") {
      bar.style.width = "100%";
    } else {
      bar.style.width = "0";
    }
    c.fd.set(pb.level ?? 100);
  }
  const s = state.snap || {};
  gm.set(s.master ?? 100);
  $("#gm-num").textContent = s.master ?? 100;
  $("#bo-btn").classList.toggle("on", !!s.blackout);
  $("#mobile-bo").classList.toggle("on", !!s.blackout);
}

function wireModes() {
  const modes = [...document.querySelectorAll("#pb-mode button")];
  const set = (mode) => {
    modes.forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
    $("#pb-strip").hidden = mode !== "faders";
    $("#qb").hidden = mode !== "buttons";
    $("#tl").hidden = mode !== "timeline";
    setTimelineVisible(mode === "timeline");
    document.body.dataset.bottom = mode;
    try { localStorage.setItem("jarvis.bottom", mode); } catch (e) { /* ignore */ }
    window.dispatchEvent(new Event("resize"));
  };
  modes.forEach((b) => b.addEventListener("click", () => set(b.dataset.mode)));
  let saved = "faders";
  try { saved = localStorage.getItem("jarvis.bottom") || saved; } catch (e) { /* ignore */ }
  set(modes.some((b) => b.dataset.mode === saved) ? saved : "faders");
}

export function initPlaybacks() {
  wireModes();
  gm = vfader($("#gm-fader"), {
    min: 0, max: 100,
    onInput: throttle((v) => { $("#gm-num").textContent = v; run("master", { level: v }, { silentError: true }); }, 60),
  });
  $("#bo-btn").addEventListener("click", () => run("blackout", { state: state.snap && state.snap.blackout ? 0 : 1 }));
  // on a phone the faders are one tab away: Blackout stays in the bottom bar
  $("#mobile-bo").addEventListener("click", () => run("blackout", { state: state.snap && state.snap.blackout ? 0 : 1 }));
  on("snapshot", render);
  on("lite", render);
}

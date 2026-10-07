// The executor wing: ten playbacks, always in their place, each with its
// fader and GO / Flash / Back / Stop; the group masters and the grand master.
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
    // an executor: its number and name, what is on and what is next, its
    // fader, and GO / Flash / Back / Stop - always in the same place
    const fader = h("div.vfader", { "aria-label": `Playback ${n} level` });
    const name = h("span.name", { title: "Cue list" });
    const cue = h("div.pb-cue");
    const prog = h("div.pb-prog", h("i"));
    const flash = h("button.flash", { title: "Flash: full while held" }, "Flash");
    const rec = h("button.pb-rec", { title: "Record what the programmer holds as this playback's first cue",
      onclick: () => openCueDialog(n) }, h("b", String(n)), h("span", "Record here"));
    const card = h("div.pb", { dataset: { pb: n } },
      fader,
      h("div.pb-top", h("span.n", String(n)), name,
        h("button.btn.ghost.small.icon", { title: "Playback options", onclick: (e) => pbMenu(e.currentTarget, n) }, "⋯")),
      h("div.pb-mid", cue, prog),
      h("div.pb-btns",
        h("button.go", { title: "GO: next cue", onclick: () => { focusPb = n; run("cue_go", { playback: n }); } }, "GO"),
        flash,
        h("button", { title: "Back one cue", onclick: () => { focusPb = n; run("cue_back", { playback: n }); } }, "◀"),
        h("button", { title: "Stop: release this playback", onclick: () => run("playback_release", { playback: n }) }, "■")),
      rec);
    wireFlash(flash, n);
    name.addEventListener("click", () => openCueList(n));
    card.addEventListener("pointerdown", () => setFocus(n));
    const send = throttle((level) => run("playback_level", { playback: n, level }, { silentError: true }), 60);
    const fd = vfader(fader, { min: 0, max: 100, onInput: send });
    cards.set(n, { card, fd, name, cue, prog });
    strip.append(card);
  }
  setFocus(focusPb);
}

// Flash: held, the playback runs at full; let go, it goes back to where it
// was (and stops again if it wasn't running)
function wireFlash(btn, n) {
  let was = null;
  const down = (e) => {
    e.preventDefault();
    const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === n) || {};
    if (!(pb.stack || []).length) return;
    was = { active: !!pb.active, level: pb.level ?? 100 };
    btn.setPointerCapture?.(e.pointerId);
    btn.classList.add("on");
    if (!was.active) run("playback_activate", { playback: n }, { silentError: true });
    run("playback_level", { playback: n, level: 100 }, { silentError: true });
  };
  const up = () => {
    if (!was) return;
    const w = was;
    was = null;
    btn.classList.remove("on");
    run("playback_level", { playback: n, level: w.level }, { silentError: true });
    if (!w.active) run("playback_release", { playback: n }, { silentError: true });
  };
  btn.addEventListener("pointerdown", down);
  btn.addEventListener("pointerup", up);
  btn.addEventListener("pointercancel", up);
  btn.addEventListener("lostpointercapture", up);
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
  // every executor stays in its place (muscle memory); an empty one is a
  // quiet "Record here"
  for (const pb of pbs) {
    const c = cards.get(pb.n);
    if (!c) continue;
    const stack = pb.stack || [];
    const cur = pb.index >= 0 ? stack[pb.index] : null;
    const next = stack[pb.index + 1] || (pb.follow && pb.follow.loop ? stack[0] : null);
    c.card.classList.toggle("active", !!pb.active);
    c.card.classList.toggle("empty", !stack.length);
    c.name.textContent = pb.name || (stack.length ? (stack.length === 1 ? cueLabel(stack[0]) : `${stack.length} cues`) : "Empty");
    c.card.title = stack.length ? "" : `Playback ${pb.n}: empty`;
    if (stack.length) {
      c.cue.replaceChildren(
        h("b", cur ? `${pb.index + 1}/${stack.length}  ${cueLabel(cur)}` : "ready"),
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
  renderGroupMasters();
  const s = state.snap || {};
  gm.set(s.master ?? 100);
  $("#gm-num").textContent = s.master ?? 100;
  $("#bo-btn").classList.toggle("on", !!s.blackout);
}

// A fader per group (the first eight): its lights at that share of
// whatever they are doing, like the grand master for one group.
let grpKey = "";
const grpFaders = new Map();
function renderGroupMasters() {
  const groups = ((state.snap && state.snap.groups) || []).slice(0, 8);
  const box = $("#grp-masters");
  const key = JSON.stringify(groups.map((g) => [g.n, g.name]));
  if (key !== grpKey) {
    grpKey = key;
    grpFaders.clear();
    box.replaceChildren(...groups.map((g) => {
      const el = h("div.vfader", { "aria-label": `${g.name} master` });
      const num = h("output", String(g.master ?? 100));
      const f = vfader(el, { min: 0, max: 100, onInput: throttle((v) => { num.textContent = v; run("group_master", { group: g.n, level: v }, { silentError: true }); }, 60) });
      grpFaders.set(g.n, { f, num });
      return h("div.master.grp", { title: `${g.name}: a master for this group` }, el, h("span.m-label", g.name.slice(0, 8), " ", num));
    }));
  }
  for (const g of groups) {
    const it = grpFaders.get(g.n);
    if (it) { it.f.set(g.master ?? 100); it.num.textContent = g.master ?? 100; }
  }
}

// The bottom dock's height: drag its top edge (double-click: back to the
// default), remembered per mode - the buttons want room, the faders less
const DOCK_MIN = 140;
function dockHeight(mode, px) {
  const ws = document.querySelector(".workspace");
  if (!ws) return;
  const key = "jarvis.dockh." + mode;
  if (px === undefined) {
    try { px = +localStorage.getItem(key) || 0; } catch (e) { px = 0; }
  } else {
    try { if (px) localStorage.setItem(key, String(px)); else localStorage.removeItem(key); } catch (e) { /* private window */ }
  }
  if (px) ws.style.setProperty("--pb-h", Math.max(DOCK_MIN, Math.min(window.innerHeight * 0.75, px)) + "px");
  else ws.style.removeProperty("--pb-h");
}

function wireGrip() {
  const grip = $("#pb-grip");
  if (!grip) return;
  const pb = grip.parentElement;
  let y0 = 0, h0 = 0, on = false;
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    on = true;
    y0 = e.clientY;
    h0 = pb.getBoundingClientRect().height;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("on");
  });
  grip.addEventListener("pointermove", (e) => {
    if (!on) return;
    dockHeight(document.body.dataset.bottom || "faders", Math.round(h0 + (y0 - e.clientY)));
  });
  const end = () => { if (on) { on = false; grip.classList.remove("on"); window.dispatchEvent(new Event("resize")); } };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
  grip.addEventListener("dblclick", () => { dockHeight(document.body.dataset.bottom || "faders", 0); window.dispatchEvent(new Event("resize")); });
}

function wireModes() {
  wireGrip();
  const modes = [...document.querySelectorAll("#pb-mode button")];
  const set = (mode) => {
    modes.forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
    $("#pb-strip").hidden = mode !== "faders";
    $("#qb").hidden = mode !== "buttons";
    $("#tl").hidden = mode !== "timeline";
    setTimelineVisible(mode === "timeline");
    document.body.dataset.bottom = mode;
    try { localStorage.setItem("jarvis.bottom", mode); } catch (e) { /* ignore */ }
    dockHeight(mode);
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
  on("snapshot", render);
  on("lite", render);
}

// The dock and the master rail.  The dock shows one thing at a time - the
// buttons, the cues (playbacks ten to a page, always in their place, each
// with its fader and GO / Flash / Back / Stop, and the cue list of the one
// in hand), the timeline, the group faders.  The rail on the right has the
// grand master, Release all and Blackout: the same place in every mode.
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { act } from "./api.js";
import { $, h, vfader, throttle, menu, promptBox, confirmBox, modal, toast, grab } from "./ui.js";
import { onNote, onCC } from "./webmidi.js";
import { icon } from "./icons.js";
import { openCueList, openCueDialog } from "./dialogs.js";
import { setTimelineVisible } from "./timeline.js";
import { groupColour } from "./groupcolour.js";
import { groupColourMenu } from "./groupcolourmenu.js";

const cards = new Map();
let gm = null;
let focusPb = 1;
const PER_PAGE = 10;
let pbPage = 1;
try { pbPage = Math.max(1, parseInt(localStorage.getItem("haze.pbpage") || "1", 10) || 1); } catch (e) { /* private window */ }

export const focusedPlayback = () => focusPb;

// Shift+click a Record: the next cue at the end, straight away (the
// programmer stays, so the next cue builds on it); a plain click asks
function quickRecord(e, n) {
  if (!e.shiftKey) return openCueDialog(n);
  let keep = true;
  try { keep = localStorage.getItem("haze.clearAfterRecord") !== "1"; } catch (err) { /* fine */ }
  return run("record_cue", { playback: n, keep }, { toast: true });
}

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
    const lvl = h("output.pb-lvl", { title: "Fader level" });
    const keys = h("kbd.pb-keys", { hidden: true, title: "This playback's key and MIDI (⋯ -> Keys and MIDI)" });
    const cue = h("div.pb-cue");
    const prog = h("div.pb-prog", h("i"));
    const flash = h("button.flash", { title: "Flash: full while held" }, "Flash");
    // an empty playback is a "+" after the last one used: it records the
    // programmer's look as a new playback (the next "+" then sits beside it)
    const rec = h("button.pb-rec", { title: "A new playback: record what the programmer holds as its first cue (Shift+click: straight away, no questions)",
      "aria-label": `New playback (${n})`, onclick: (e) => quickRecord(e, n) }, h("b.pb-plus", "+"));
    // its options as buttons on the card (they were hidden in the ⋯ menu)
    const opt = (label, title, fn) => h("button.pb-opt", { type: "button", title, onclick: (e) => { e.stopPropagation(); fn(); } }, label);
    const pbNow = () => allPbs().find((p) => p.n === n) || {};
    const loopB = opt("Loop", "Loop the cue list (back to the first after the last)", () => run("follow_set", { playback: n, loop: !(pbNow().follow || {}).loop }, { toast: true }));
    const autoB = opt("Auto", "Auto-follow: run the cue list by itself", () => run("follow_set", { playback: n, on: !(pbNow().follow || {}).on }, { toast: true }));
    const swopB = opt("Swop", "Flash works as Swop: this playback alone while held", () => { const b = pbNow().bind || {}; run("playback_bind", { playback: n, ...b, swop: !b.swop }, { toast: true }); });
    const opts = h("div.pb-opts",
      opt("+ Cue", "Record the programmer as a new cue at the end of this list", () => openCueDialog(n)),
      opt("Cues", "Open the cue list", () => openCueList(n)),
      loopB, autoB);
    const card = h("div.pb", { dataset: { pb: n } },
      fader,
      lvl,
      h("div.pb-top", h("span.n", String(n)), name,
        h("button.btn.ghost.small.icon", { title: "Playback options", onclick: (e) => pbMenu(e.currentTarget, n) }, "⋯")),
      h("div.pb-mid", cue, keys, prog),
      opts,
      h("div.pb-btns",
        h("button.go", { title: "GO: next cue", onclick: () => { setFocus(n); run("cue_go", { playback: n }); } }, "GO"),
        flash,
        h("button", { title: "Back one cue", "aria-label": "Back one cue", onclick: () => { setFocus(n); run("cue_back", { playback: n }); } }, icon("back")),
        h("button", { title: "Stop: release this playback", "aria-label": "Stop", onclick: () => run("playback_release", { playback: n }) }, icon("stop"))),
      rec);
    wireFlash(flash, n);
    name.addEventListener("click", () => openCueList(n));
    card.addEventListener("pointerdown", () => setFocus(n));
    const send = throttle((level) => run("playback_level", { playback: n, level }, { silentError: true }), 60);
    const fd = vfader(fader, { min: 0, max: 100, onInput: send });
    cards.set(n, { card, fd, name, cue, prog, lvl, keys, flash, loopB, autoB, swopB });
    strip.append(card);
  }
  setFocus(focusPb);
  if (!build.told) {                                  // the engine learns it once it is there
    build.told = true;
    on("snapshot", () => tellFocus(focusPb));
  }
}

// Flash: held, the playback runs at full; let go, it goes back to where it
// was (and stops again if it wasn't running).  A playback set to Swop also
// takes the other running playbacks out while it is held.
const allPbs = () => (state.snap && state.snap.playbacks) || [];
function flashDown(n) {
  const pb = allPbs().find((p) => p.n === n) || {};
  if (!(pb.stack || []).length) return null;
  const was = { active: !!pb.active, level: pb.level ?? 100, others: [] };
  if (pb.bind && pb.bind.swop) {
    for (const o of allPbs()) {
      if (o.n !== n && o.active && (o.level ?? 100) > 0) {
        was.others.push([o.n, o.level ?? 100]);
        run("playback_level", { playback: o.n, level: 0 }, { silentError: true });
      }
    }
  }
  if (!was.active) run("playback_activate", { playback: n }, { silentError: true });
  run("playback_level", { playback: n, level: 100 }, { silentError: true });
  return was;
}
function flashUp(n, w) {
  run("playback_level", { playback: n, level: w.level }, { silentError: true });
  if (!w.active) run("playback_release", { playback: n }, { silentError: true });
  for (const [o, level] of w.others) run("playback_level", { playback: o, level }, { silentError: true });
}
function wireFlash(btn, which) {
  let n = 0;                                     // (which: a number, or the mini row's slot)
  let was = null, finger = null;                 // one finger per Flash: a second can't undo the first
  const down = (e) => {
    e.preventDefault();
    if (finger !== null) return;
    n = typeof which === "function" ? which() : which;
    was = flashDown(n);
    if (!was) return;
    finger = e.pointerId;
    grab(btn, e.pointerId);
    btn.classList.add("on");
  };
  const up = (e) => {
    if (!was || (e && e.pointerId !== finger)) return;
    const w = was;
    was = null;
    finger = null;
    btn.classList.remove("on");
    flashUp(n, w);
  };
  btn.addEventListener("pointerdown", down);
  btn.addEventListener("pointerup", up);
  btn.addEventListener("pointercancel", up);
  btn.addEventListener("lostpointercapture", up);
}

function setFocus(n) {
  focusPb = n;
  for (const [k, c] of cards) c.card.classList.toggle("focus", k === n);
  tellFocus(n);
}

// the 3D's Next GO previews the playback you are working with: the desk is
// told whenever the focus moves (the 3D may be in a window of its own)
function tellFocus(n) {
  // (only the window with the playbacks on it: the 3D's own window has none)
  if (document.documentElement.dataset.window === "stage") return;
  const vis = (state.snap && state.snap.vis_source) || {};
  if (!state.snap || vis.focus === n) return;
  vis.focus = n;
  act("vis_source", { focus: n }).catch(() => { /* the next change tells it again */ });
}

function pbMenu(btn, n) {
  const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === n) || {};
  const f = pb.follow || {};
  const b = pb.bind || {};
  menu(btn, [
    { label: "Record cue from programmer…", run: () => openCueDialog(n) },
    { label: "Edit cue list…", run: () => openCueList(n) },
    "-",
    { label: "Keys and MIDI…", hint: bindText(b) || "a key or a pad for GO, Flash, the fader", run: () => openBind(n) },
    { label: b.swop ? "Flash button: back to Flash" : "Flash button works as Swop", hint: b.swop ? "" : "this one alone while held",
      run: () => run("playback_bind", { playback: n, ...b, swop: !b.swop }, { toast: true }) },
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

const bindText = (b) => [b.key ? `${b.key.toUpperCase()}` : "", b.go != null ? `♪${b.go}` : "",
  b.flash != null ? `F♪${b.flash}` : "", b.fader != null ? `CC${b.fader}` : ""].filter(Boolean).join(" ");

// A playback's own controls: a keyboard key for GO, a MIDI pad for GO and
// one for Flash, a MIDI fader (CC) for its level.  Learn listens for the
// next pad or fader moved.
async function openBind(n) {
  const { learnMidi, RESERVED } = await import("./quickbuttons.js");
  const pb = allPbs().find((p) => p.n === n) || {};
  const s = { key: "", go: "", flash: "", fader: "", swop: false, ...(pb.bind || {}) };
  const note = h("p.muted.small", "Learn listens for 10 seconds: hit the pad or move the fader.");
  const keyIn = h("input.qe-num.qe-key", { type: "text", maxlength: 1, value: s.key, placeholder: "none",
    oninput: (e) => {
      const k = e.target.value.toLowerCase();
      s.key = /^[a-z0-9]$/.test(k) && !RESERVED.has(k) ? k : "";
      e.target.value = s.key;
      note.textContent = k && !s.key ? `“${k}” is one of the desk's own keys - pick another` : "Press the key: GO on this playback.";
    } });
  const num = (field, kind) => {
    const inp = h("input.qe-num.qe-key", { type: "number", min: 0, max: 127, value: s[field] ?? "", placeholder: "—",
      oninput: (e) => { const v = e.target.value; s[field] = v === "" ? "" : Math.max(0, Math.min(127, Math.round(+v))); } });
    const learn = h("button.chip", { onclick: () => learnMidi(learn, inp, note, (v) => { s[field] = v; }, kind) }, "Learn");
    return h("div.chip-row", inp, learn);
  };
  const row = (label, el, help) => h("div.bind-row", h("label", label), el, h("small.muted", help));
  const close = modal({
    title: `PB${n} ${pb.name || ""}: keys and MIDI`,
    body: h("div.bind-box",
      row("GO key", keyIn, "a key on the keyboard"),
      row("GO pad", num("go", "note"), "a MIDI note"),
      row("Flash pad", num("flash", "note"), s.swop ? "held: this one alone (Swop)" : "held: full"),
      row("Fader", num("fader", "cc"), "a MIDI fader or knob (CC)"),
      note),
    foot: [
      h("button.btn", { onclick: () => { run("playback_bind", { playback: n, swop: s.swop }, { toast: true }); close(); } }, "Clear all"),
      h("button.btn.primary", { onclick: () => { run("playback_bind", { playback: n, ...s }, { toast: true }); close(); } }, "Save"),
    ],
  });
}

// the page buttons beside the faders: 1-10, 11-20...; on each, how many
// of its playbacks are running (a page you can't see still plays)
function renderPages(pbs) {
  const pages = Math.max(1, Math.ceil(pbs.length / PER_PAGE));
  if (pbPage > pages) pbPage = 1;
  const box = $("#pb-pages");
  if (!box) return;
  box.hidden = pages < 2 || $("#pb-strip").hidden;
  box.replaceChildren(...Array.from({ length: pages }, (_, i) => {
    const p = i + 1;
    const on = pbs.slice(i * PER_PAGE, p * PER_PAGE);
    const live = on.filter((pb) => pb.active).length;
    const used = on.some((pb) => (pb.stack || []).length);
    return h("button" + (p === pbPage ? ".on" : "") + (live ? ".live" : "") + (used ? "" : ".unused"), {
      title: `Playbacks ${i * PER_PAGE + 1}-${p * PER_PAGE}` + (live ? ` (${live} running)` : ""),
      onclick: () => { pbPage = p; try { localStorage.setItem("haze.pbpage", String(p)); } catch (e) { /* ignore */ } render(); },
    }, `P${p}`, live ? h("span.pg-run", String(live)) : null);
  }));
  // the playbacks in use, in their places, and one "+" after the last one
  // used (no row of empty numbered slots); a page with none: just the "+"
  const page = pbs.slice((pbPage - 1) * PER_PAGE, pbPage * PER_PAGE);
  const lastUsed = page.reduce((m, pb) => ((pb.stack || []).length ? Math.max(m, pb.n) : m), 0);
  const plus = (page.find((pb) => pb.n > lastUsed && !(pb.stack || []).length)
    || page.find((pb) => !(pb.stack || []).length) || {}).n;
  const shown = new Set(page.filter((pb) => (pb.stack || []).length || pb.n === plus).map((pb) => pb.n));
  for (const [n, c] of cards) c.card.hidden = !shown.has(n);
  const strip = $("#pb-strip");
  if (strip) {
    const cols = page.filter((pb) => shown.has(pb.n))
      .map((pb) => ((pb.stack || []).length ? "minmax(0, calc(260px * var(--ctl)))" : "calc(64px * var(--ctl))")).join(" ");
    if (strip.style.gridTemplateColumns !== cols) strip.style.gridTemplateColumns = cols;
  }
}

function render() {
  build();
  const pbs = (state.snap && state.snap.playbacks) || [];
  renderPages(pbs);
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
      // what is on, then what comes: as many next cues as the card has room
      // for (its middle was empty space)
      const ahead = [];
      for (let k = 1; k <= 4 && pb.index + k < stack.length; k++) ahead.push(stack[pb.index + k]);
      if (!ahead.length && next) ahead.push(next);
      c.cue.replaceChildren(
        h("b", cur ? `${pb.index + 1}/${stack.length}  ${cueLabel(cur)}` : "ready"),
        ...(ahead.length ? ahead.map((x, k) => h("span.pb-then", (k ? "then " : "next: ") + cueLabel(x))) : [h("span.pb-then", "end of list")]));
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
    c.lvl.textContent = `${Math.round(pb.level ?? 100)}%`;
    const bt = bindText(pb.bind || {});
    c.keys.hidden = !bt;
    c.keys.textContent = bt;
    const swop = !!(pb.bind && pb.bind.swop);
    c.loopB.classList.toggle("on", !!f.loop);
    c.autoB.classList.toggle("on", !!f.on);
    c.swopB.classList.toggle("on", swop);
    c.flash.textContent = swop ? "Swop" : "Flash";
    c.flash.title = swop ? "Swop: this playback alone, at full, while held" : "Flash: full while held";
  }
  renderGroupMasters();
  renderCues();
  renderMini(pbs);
  renderRail(pbs);
  const s = state.snap || {};
  // the live feed's GM (the full snapshot isn't sent again for a GM move:
  // read from it, the GM jumped back to its old place - often 100 - the
  // next time anything else on the dock was touched)
  const m = state.lite && state.lite.master != null ? state.lite.master : s.master ?? 100;
  gm.set(m);
  $("#gm-num").textContent = m;
  $("#bo-btn").classList.toggle("on", !!s.blackout);
}

// The rail's Release all: how many things it would let go of - the running
// playbacks (on every page) and the latched buttons.
function renderRail(pbs) {
  const rel = $("#pb-relall");
  if (!rel) return;
  const q = (state.lite && state.lite.quick) || (state.snap && state.snap.quick) || {};
  const running = pbs.filter((pb) => pb.active).length, latched = (q.active || []).length;
  const n = running + latched;
  rel.disabled = !n;
  $("#pb-relall-n").textContent = n ? String(n) : "";
  const parts = [running ? `${running} playback${running === 1 ? "" : "s"}` : "", latched ? `${latched} button${latched === 1 ? "" : "s"}` : ""].filter(Boolean);
  rel.title = n ? `Hold to release everything that is running: ${parts.join(" and ")}` : "Nothing is running";
}

// The dock on Buttons: the playbacks of this page as a row of small
// executors above the buttons (name, what's on, level, GO, Flash) -
// busking, GO stays on screen.  Only the ones that hold cues.
const minis = [];
function renderMini(pbs) {
  const row = $("#pb-mini");
  if (!row) return;
  if (!minis.length) {
    for (let i = 0; i < PER_PAGE; i++) {
      const slot = { n: 0 };
      const name = h("b"), cue = h("small"), lvl = h("i.pbm-lvl");
      const flash = h("button.pbm-flash", { title: "Flash: full while held" }, "Flash");
      const go = h("button.pbm-go", { title: "GO: next cue", onclick: () => { setFocus(slot.n); run("cue_go", { playback: slot.n }); } }, icon("go"), "GO");
      const el = h("div.pbm", h("div.pbm-top", h("span.pbm-n"), name), cue, h("div.pbm-bar", lvl), h("div.pbm-btns", go, flash));
      wireFlash(flash, () => slot.n);
      minis.push(Object.assign(slot, { el, name, cue, lvl }));
      row.append(el);
    }
  }
  const page = pbs.slice((pbPage - 1) * PER_PAGE, pbPage * PER_PAGE);
  let shown = 0;
  minis.forEach((m, i) => {
    const pb = page[i];
    const stack = (pb && pb.stack) || [];
    m.el.hidden = !stack.length;
    if (!stack.length) return;
    shown++;
    m.n = pb.n;
    const cur = pb.index >= 0 ? stack[pb.index] : null;
    m.el.querySelector(".pbm-n").textContent = String(pb.n);
    m.name.textContent = pb.name || (stack.length === 1 ? cueLabel(stack[0]) : `${stack.length} cues`);
    m.cue.textContent = cur ? `${pb.index + 1}/${stack.length} ${cueLabel(cur)}` : "ready";
    m.lvl.style.width = `${pb.level ?? 100}%`;
    m.el.classList.toggle("active", !!pb.active);
  });
  row.hidden = !shown;
}

// The cue list of the playback in hand, beside the playbacks on the Cues
// tab: every cue with its fade, the one live marked; a tap jumps there.
// (Nothing to list until a playback holds cues.)
let cuesKey = "";
function renderCues() {
  const box = $("#pb-cues");
  if (!box) return;
  const pbs = (state.snap && state.snap.playbacks) || [];
  const used = pbs.filter((x) => (x.stack || []).length);
  const pb = used.find((x) => x.n === focusPb) || used[0];
  box.hidden = !pb || document.body.dataset.bottom !== "faders";
  if (!pb) { cuesKey = ""; return; }
  const stack = pb.stack || [];
  const key = JSON.stringify([pb.n, pb.index, stack.map((c) => [c.name, c.fade])]);
  if (key === cuesKey) return;
  cuesKey = key;
  box.replaceChildren(
    h("div.pb-cues-head", h("b", pb.name || `Playback ${pb.n}`), h("span.muted.small", ` ${stack.length} cue${stack.length === 1 ? "" : "s"}`),
      h("button.btn.small.ghost", { title: "Edit the cue list", onclick: () => openCueList(pb.n) }, "Edit…")),
    stack.length ? h("ol.pb-cues-list", ...stack.map((c, i) => h("li" + (i === pb.index ? ".on" : ""), {
      title: `GO to cue ${i + 1}`, onclick: () => run("cue_go", { playback: pb.n, cue: i + 1 }),
    }, h("span.pb-cue-n", String(i + 1)), h("span.pb-cue-name", cueLabel(c)), h("small.muted", c.fade != null ? `${c.fade}s` : ""))))
      : h("p.muted.small", "No cues yet: set a look, then Record cue."));
  const on = box.querySelector("li.on");
  if (on) on.scrollIntoView({ block: "nearest" });
}

// A fader per group: its lights at that share of whatever they are doing,
// like the grand master for one group.  The dock's Group faders tab in
// Program; in Live, a strip under the dock whatever its tab.  Each wears
// its group's colour - the name's strip and the fader's fill.
let grpKey = "";
const grpFaders = new Map();
function renderGroupMasters() {
  const groups = ((state.snap && state.snap.groups) || []).slice(0, 16);
  const box = $("#grp-masters");
  const dock = $("#grp-dock");
  if (dock) dock.classList.toggle("none", !groups.length);
  const key = JSON.stringify(groups.map((g) => [g.n, g.name, g.colour, (g.heads || []).length]));
  if (key !== grpKey) {
    grpKey = key;
    grpFaders.clear();
    box.replaceChildren(...groups.map((g, i) => {
      const el = h("div.vfader", { "aria-label": `${g.name} master` });
      const num = h("output", String(g.master ?? 100));
      const f = vfader(el, { min: 0, max: 100, onInput: throttle((v) => {
        num.textContent = v;
        // kept here too: the fader stays where it was put until the next snapshot says otherwise
        const sg = ((state.snap && state.snap.groups) || []).find((x) => x.n === g.n);
        if (sg) { if (v >= 100) delete sg.master; else sg.master = v; }
        if (state.lite && state.lite.group_masters) state.lite.group_masters[g.n] = v;
        run("group_master", { group: g.n, level: v }, { silentError: true });
      }, 60) });
      grpFaders.set(g.n, { f, num });
      // the name on its colour strip (click the strip's square to change the
      // colour; right-click / a held finger on the name does too), the fader,
      // and beside it the level and how many lights it holds
      const colour = groupColour(g, i);
      const dot = h("button.m-colour", { type: "button", title: `${g.name}'s colour (on the desk and the phone remote) - click to change`,
        "aria-label": `${g.name} colour`, onclick: (e) => groupColourMenu(e.currentTarget, g, i) });
      const name = h("span.m-name", g.name);
      name.addEventListener("contextmenu", (e) => { e.preventDefault(); groupColourMenu(dot, g, i); });
      const count = (g.heads || []).length;
      const wrap = h("div.master.grp", { title: `${g.name}: a master for this group` },
        h("div.m-strip", dot, name),
        h("div.m-in", el, h("div.m-read", h("span.m-label.m-num", num), h("small", `${count} light${count === 1 ? "" : "s"}`))));
      wrap.style.setProperty("--c", colour);
      return wrap;
    }));
    if (!groups.length) box.replaceChildren(h("p.dock-empty.muted", "No groups yet. Select some lights and press Group: each group gets a fader here."));
    scrollHints();
  }
  const live = (state.lite && state.lite.group_masters) || {};
  for (const g of groups) {
    const it = grpFaders.get(g.n);
    const v = live[g.n] ?? g.master ?? 100;            // the live feed first: it is always current
    if (it) { it.f.set(v); it.num.textContent = v; }
  }
}

// More group faders than fit: the strip scrolls (wheel, finger, its
// scrollbar) and fades at the side where more are waiting
function scrollHints() {
  const box = $("#grp-masters");
  if (!box) return;
  const l = box.scrollLeft > 2, r = box.scrollLeft + box.clientWidth < box.scrollWidth - 2;
  box.classList.toggle("more-l", l);
  box.classList.toggle("more-r", r);
}

// The dock's height under the programmer: drag its top edge (double-click:
// back to the default), remembered per tab - the buttons want room, the
// cues less
const DOCK_MIN = 140;
function dockHeight(mode, px) {
  const ws = document.querySelector("#work");
  if (!ws) return;
  const key = "haze.dockh." + mode;
  if (px === undefined) {
    try { px = +localStorage.getItem(key) || 0; } catch (e) { px = 0; }
  } else {
    try { if (px) localStorage.setItem(key, String(px)); else localStorage.removeItem(key); } catch (e) { /* private window */ }
  }
  if (px) ws.style.setProperty("--dock-h", Math.max(DOCK_MIN, Math.min(window.innerHeight * 0.75, px)) + "px");
  else ws.style.removeProperty("--dock-h");
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
    dockHeight(document.body.dataset.bottom || "buttons", Math.round(h0 + (y0 - e.clientY)));
  });
  const end = () => { if (on) { on = false; grip.classList.remove("on"); window.dispatchEvent(new Event("resize")); } };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
  grip.addEventListener("dblclick", () => { dockHeight(document.body.dataset.bottom || "buttons", 0); window.dispatchEvent(new Event("resize")); });
}

function wireModes() {
  wireGrip();
  const modes = [...document.querySelectorAll("#pb-mode button")];
  const tools = [...document.querySelectorAll("#dock-bar .dock-tools")];
  const set = (mode) => {
    modes.forEach((b) => { const lit = b.dataset.mode === mode; b.classList.toggle("on", lit); b.setAttribute("aria-pressed", String(lit)); });
    // each tab's own tools, beside the tabs
    tools.forEach((t) => { t.hidden = t.dataset.for !== mode; });
    $("#pb-strip").hidden = mode !== "faders";
    $("#pb-pages").hidden = mode !== "faders" || ((state.snap && state.snap.playbacks) || []).length <= PER_PAGE;
    $("#qb").hidden = mode !== "buttons";
    $("#tl").hidden = mode !== "timeline";
    setTimelineVisible(mode === "timeline");
    document.body.dataset.bottom = mode;
    try { localStorage.setItem("haze.bottom", mode); } catch (e) { /* ignore */ }
    dockHeight(mode);
    renderCues();
    window.dispatchEvent(new Event("resize"));
  };
  modes.forEach((b) => b.addEventListener("click", () => set(b.dataset.mode)));
  let saved = "buttons";
  try { saved = localStorage.getItem("haze.bottom") || saved; } catch (e) { /* ignore */ }
  set(modes.some((b) => b.dataset.mode === saved) ? saved : "buttons");
}

/** Show one of the dock's tabs: buttons, faders (Cues), timeline or groups. */
export function setBottom(mode) {
  const b = document.querySelector(`#pb-mode button[data-mode="${mode}"]`);
  if (b) b.click();
}

// A playback's key (GO) and, from a controller on this device, its pads
// and fader.  (The desk's own MIDI input plays them in the engine.)
function wireBindings() {
  if (document.documentElement.dataset.window === "stage") return;
  const typing = (t) => t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || typing(e.target) || document.querySelector(".modal-scrim")) return;
    const k = (e.key || "").toLowerCase();
    if (k.length !== 1) return;
    const pb = allPbs().find((p) => p.bind && p.bind.key === k);
    if (!pb) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    setFocus(pb.n);
    run("cue_go", { playback: pb.n });
  }, true);
  const flashing = new Map();
  onNote((ev) => {
    for (const pb of allPbs()) {
      const b = pb.bind || {};
      if (b.go === ev.number && ev.on) run("cue_go", { playback: pb.n });
      if (b.flash === ev.number) {
        if (ev.on && !flashing.has(pb.n)) { const w = flashDown(pb.n); if (w) flashing.set(pb.n, w); }
        if (!ev.on && flashing.has(pb.n)) { flashUp(pb.n, flashing.get(pb.n)); flashing.delete(pb.n); }
      }
    }
  });
  const senders = new Map();                     // one per playback: two faders moved together both land
  onCC((ev) => {
    const pb = allPbs().find((p) => p.bind && p.bind.fader === ev.number);
    if (!pb) return;
    if (!senders.has(pb.n)) senders.set(pb.n, throttle((level) => run("playback_level", { playback: pb.n, level }, { silentError: true }), 60));
    senders.get(pb.n)(Math.round((ev.value * 100) / 127));
  });
}

// Release all: every running playback and every latched button.  It takes
// a half-second hold (the key fills while held) - a stray tap beside
// BLACKOUT can't drop the whole look.  From the keyboard it asks.
function wireRelease(btn) {
  let timer = 0, held = false, byPointer = false;
  const fire = () => {
    run("playback_release_all", {}, { silentError: true });
    run("quick_release_all", {}, { silentError: true });
    toast("Released everything that was running", "ok");
  };
  const cancel = () => { clearTimeout(timer); btn.classList.remove("holding"); };
  btn.addEventListener("pointerdown", (e) => {
    byPointer = true;
    if (btn.disabled || e.button !== 0) return;
    held = false;
    btn.classList.add("holding");
    timer = setTimeout(() => { held = true; btn.classList.remove("holding"); fire(); }, 500);
  });
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) btn.addEventListener(ev, cancel);
  btn.addEventListener("click", async () => {
    const pointer = byPointer;
    byPointer = false;
    if (held) { held = false; return; }
    if (pointer) { toast("Hold to release everything that is running"); return; }
    if (await confirmBox("Release all", "Release every running playback and every latched button?", { ok: "Release", danger: true })) fire();
  });
}

export function initPlaybacks() {
  wireModes();
  gm = vfader($("#gm-fader"), {
    min: 0, max: 100,
    onInput: throttle((v) => {
      $("#gm-num").textContent = v;
      if (state.snap) state.snap.master = v;
      if (state.lite) state.lite.master = v;
      run("master", { level: v }, { silentError: true });
    }, 60),
  });
  $("#bo-btn").addEventListener("click", () => run("blackout", { state: state.snap && state.snap.blackout ? 0 : 1 }));
  wireRelease($("#pb-relall"));
  // the rail on the left or the right of the screen (right-click it, or hold
  // a finger on it): kept on this computer
  const keep = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } };
  const read = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const rail = $("#rail");
  const setSide = (left) => { document.body.classList.toggle("rail-left", left); keep("haze.mastersSide", left ? "left" : ""); window.dispatchEvent(new Event("resize")); };
  setSide(read("haze.mastersSide") === "left");
  const sideMenu = (anchor) => menu(anchor, [
    { label: document.body.classList.contains("rail-left") ? "Master on the right of the screen" : "Master on the left of the screen",
      run: () => setSide(!document.body.classList.contains("rail-left")) },
  ]);
  rail.addEventListener("contextmenu", (e) => { if (e.target.closest(".vfader, button")) return; e.preventDefault(); sideMenu(rail.querySelector(".m-name")); });
  // Live: the group faders' strip under the dock - drag its top edge to make
  // it taller or shorter (nearly shut folds it away), double-click folds /
  // shows it.  Kept on this computer.
  const gdock = $("#grp-dock"), fold = $("#grp-fold-btn");
  const setFold = (folded) => {
    gdock.classList.toggle("folded", folded);
    fold.setAttribute("aria-expanded", String(!folded));
    keep("haze.grpFold", folded ? "1" : "");
    window.dispatchEvent(new Event("resize"));
  };
  const setHeight = (px) => {
    if (px) gdock.style.setProperty("--strip-h", `${Math.round(px)}px`); else gdock.style.removeProperty("--strip-h");
    keep("haze.grpH", px ? String(Math.round(px)) : "");
  };
  setFold(read("haze.grpFold") === "1");
  if (+read("haze.grpH")) gdock.style.setProperty("--strip-h", `${+read("haze.grpH")}px`);
  let drag = null;
  fold.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    fold.setPointerCapture(e.pointerId);
    drag = { y: e.clientY, h: gdock.classList.contains("folded") ? 0 : gdock.getBoundingClientRect().height, moved: false };
    fold.classList.add("on");
  });
  fold.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dy = drag.y - e.clientY;
    if (Math.abs(dy) > 4) drag.moved = true;
    if (!drag.moved) return;
    const tall = Math.max(0, Math.min(window.innerHeight * 0.5, drag.h + dy));
    if (tall < 56) { if (!gdock.classList.contains("folded")) setFold(true); return; }
    if (gdock.classList.contains("folded")) setFold(false);
    setHeight(Math.max(104, tall));
  });
  const endDrag = () => { if (!drag) return; drag = null; fold.classList.remove("on"); window.dispatchEvent(new Event("resize")); };
  fold.addEventListener("pointerup", endDrag);
  fold.addEventListener("pointercancel", endDrag);
  fold.addEventListener("dblclick", () => { if (gdock.classList.contains("folded")) setFold(false); else { setHeight(0); setFold(true); } });
  fold.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFold(!gdock.classList.contains("folded")); } });
  // more group faders than fit: a mouse wheel over the strip (not on a
  // fader) scrolls it sideways
  const grp = $("#grp-masters");
  grp.addEventListener("wheel", (e) => {
    if (e.target.closest(".vfader") || grp.scrollWidth <= grp.clientWidth) return;
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { grp.scrollLeft += e.deltaY; e.preventDefault(); }
  }, { passive: false });
  grp.addEventListener("scroll", scrollHints, { passive: true });
  window.addEventListener("resize", scrollHints);
  wireBindings();
  on("snapshot", render);
  on("lite", render);
}

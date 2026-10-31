// The hub beside the 3D view in the Set up and Live modes (Program has the
// programmer there).
//   Set up  every job before a show, in order - the lights, the room, the
//           connections, the checks - each with how it stands now
//   Live    what is playing and what comes next, the tempo, the autopilot,
//           the hits; master and blackout are on the dock's right
import { $, h, confirmBox } from "./ui.js";
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { needs3d } from "./view3d.js";

let mode = "program";
let liveTimer = 0;
let seen = 0;                          // when the last snapshot came (for the timeline's position)

const snap = () => state.snap || {};
const patch = () => snap().patch || [];

export function renderHub(m) {
  mode = m;
  clearInterval(liveTimer);
  liveTimer = 0;
  const box = $("#mode-hub");
  if (!box) return;
  if (m === "setup") paintSetup();
  else if (m === "live") {
    paintLive();
    liveTimer = setInterval(paintLiveNow, 250);       // the bars to the next part count down
  }
}

on("snapshot", () => {
  seen = performance.now();
  if (mode === "setup") paintSetup();
  else if (mode === "live") paintLive();
});

// ------------------------------------------------------------------ Set up
// Each step: how it stands (done / to do / nothing to judge), what it is
// for, and its buttons - the first is the step's main job.  The first step
// still to do is the one lit up: "do this next".
const btn = (label, fn) => [label, fn];
const lazy = (mod, fn, ...args) => () => import(mod).then((m) => m[fn](...args));
const mark = (ok, n) => h("i.hub-mark", { "aria-hidden": "true" }, ok === true ? "✓" : ok === false ? "!" : "");

function stepEl(st, n, next) {
  const cls = (st.ok === true ? ".ok" : st.ok === false ? ".todo" : "") + (next ? ".next" : "") + (st.optional ? ".opt" : "");
  return h("div.hub-step" + cls,
    mark(st.ok, n),
    h("div.hub-step-main",
      h("div.hub-step-head", h("b", st.title), next ? h("span.hub-tag", "Next") : null, st.optional ? h("span.hub-tag.soft", "Optional") : null),
      h("div.hub-status", st.status),
      h("div.hub-btns", ...st.buttons.filter(Boolean).map(([label, fn], i) =>
        h("button.btn.small" + (i === 0 ? (next ? ".primary" : "") : ".ghost"), { onclick: fn }, label)))));
}

function paintSetup() {
  const s = snap();
  const n = patch().length;
  const clashes = (s.clashes || []).length;
  const v = s.venue || {};
  const o = s.output || {};
  const tgt = (o.target || {});
  const where = tgt.mode === "usb" ? "a USB DMX box" : o.virtual_node ? "the desk's own test node" : (tgt.host || (o.host || "").split(":")[0] || "the network");
  const hue = s.hue || {};
  const sb = s.standby || {};
  const mv = patch().filter((x) => (x.map || []).includes("pan") && (x.map || []).includes("tilt"));
  const lined = mv.filter((x) => x.align).length;
  const zs = v.zones || [];
  const keep = zs.filter((z) => z.kind === "keepout").length, areas = zs.length - keep;
  const sections = [
    ["The lights", "what is hung, and where it listens", [
      { title: "Add the lights", status: n ? `${n} light${n > 1 ? "s" : ""} patched` : "no lights yet", ok: n > 0,
        buttons: [btn("Add lights…", lazy("./dialogs.js", "openAddDialog")), btn("Import a plot (MVR)…", lazy("./dialogs.js", "importMvr")),
          btn("Import a patch (CSV)…", lazy("./dialogs.js", "openCsvImport"))] },
      { title: "DMX addresses", status: !n ? "add lights first" : clashes ? `${clashes} address clash${clashes > 1 ? "es" : ""}` : "no clashes", ok: n ? clashes === 0 : null,
        buttons: [btn("DMX map…", lazy("./fixtures.js", "openDmxMap")), btn("Address them all", async () => {
          if (await confirmBox("Auto-address", "Re-address every light from 1.001 with no gaps or overlaps? Ctrl+Z undoes it.", { ok: "Re-address" })) run("auto_patch", {}, { toast: true });
        })] }]],
    ["The room", "the 3D room, and lights that aim where you point", [
      { title: "Room and rigging", status: v.name || v.template ? `${v.name || v.template}` : "a plain room so far", ok: v.name || v.template || (v.room || {}).width ? true : null,
        buttons: [btn("Arrange the room", () => needs3d("Arranging the room") && import("./venuepanel.js").then((m) => m.setArranging(true))),
          btn("Room shape…", lazy("./roomdialog.js", "openRoomDialog")), btn("Rigging…", lazy("./rigdialog.js", "openRigDialog")),
          btn("Describe it in words…", lazy("./settings.js", "openSettingsAt", "venue")), btn("Start again…", lazy("./venuepanel.js", "resetVenue"))] },
      { title: "Lights match the real ones", status: mv.length ? `${lined} of ${mv.length} moving light${mv.length > 1 ? "s" : ""} lined up` : "no moving lights",
        ok: mv.length ? lined === mv.length : null, buttons: [btn("Line up with the real lights…", lazy("./lineup.js", "openLineUp"))] },
      { title: "The room the lights know", status: (areas ? `${areas} zone${areas === 1 ? "" : "s"} (dance floor, bar…)` : "no zones yet - point a light to mark them") + (keep ? ` · ${keep} kept out` : ""),
        ok: mv.length ? areas > 0 : null,
        buttons: [btn("Teach the room by pointing…", lazy("./roomteach.js", "openRoomTeach")), btn("Not here (keep out)…", lazy("./roomteach.js", "openRoomTeach", "keepout"))] }]],
    ["Connections", "out to the lights, in from the DJ", [
      { title: "Output to the lights", status: s.live ? `live to ${where}` : `blind - set to send to ${where}`, ok: s.live ? true : null,
        buttons: [btn("Output settings…", lazy("./settings.js", "openSettingsAt", "output")), btn("Find nodes", lazy("./fixtures.js", "scanRig")),
          btn("Ask the lights (RDM)…", lazy("./fixtures.js", "openRdm"))] },
      { title: "DJ gear and controllers", status: tempoLine(s), ok: null,
        buttons: [btn("MIDI, OSC, Stream Deck…", lazy("./settings.js", "openSettingsAt", "midi")), btn("Songs…", lazy("./songs.js", "openSongs"))] },
      { title: "Smart lamps and a spare desk", optional: true, ok: hue.paired || sb.following ? true : null,
        status: [hue.paired ? `Hue: ${hue.links} lamp(s)` : "", sb.following ? `backup of ${sb.host}` : ""].filter(Boolean).join(" · ") || "Philips Hue lamps, a second desk as a backup",
        buttons: [btn("Philips Hue…", lazy("./settings.js", "openSettingsAt", "hue")), btn("Spare desk…", lazy("./settings.js", "openSettingsAt", "standby"))] }]],
    ["Checks", "on the day, before the doors open", [
      { title: "At the venue", status: "the lights found here against the show", ok: null,
        buttons: [btn("Venue check-in…", lazy("./checkin.js", "openCheckin"))] },
      { title: "Before doors", status: "test a light, then the ready check", ok: null,
        buttons: [btn("Ready check…", lazy("./dialogs.js", "openReadyCheck")), btn("Test a light…", () => {
          const sel = new Set(s.selected || []);
          const hd = patch().find((x) => sel.has(x.head_no)) || patch()[0];
          if (hd) import("./lightdialogs.js").then((m) => m.openLightTest(hd));
        })] }]],
  ];
  const all = sections.flatMap((x) => x[2]);
  const judged = all.filter((st) => st.ok !== null && !st.optional);
  const done = judged.filter((st) => st.ok).length;
  const next = all.find((st) => st.ok === false);
  let k = 0;
  const body = h("div.hub-body.setup",
    h("div.hub-hero",
      h("div.hub-hero-text", h("b", next ? `Next: ${next.title.toLowerCase()}` : "Ready to program"),
        h("span.muted", next ? "Everything before the show, in order. The fixture list on the left follows along."
          : "The lights, the room and the output are set. Program the show, or go to Live.")),
      h("div.hub-progress", { title: `${done} of ${judged.length} done` },
        h("span.muted.small", `${done} of ${judged.length} done`), h("div.hub-bar", h("i", { style: `width:${judged.length ? (done / judged.length) * 100 : 0}%` })))),
    h("div.hub-secs", ...sections.map(([name, sub, steps], i) => h("section.hub-sec",
      h("div.hub-sec-head", h("span.hub-sec-n", String(i + 1)), h("div", h("h3", name), h("span.muted.small", sub))),
      ...steps.map((st) => stepEl(st, ++k, st === next))))),
    h("div.hub-next", h("button.btn.primary", { onclick: () => import("./workspaces.js").then((m) => m.useMode("program")) }, "Program the show →"),
      h("button.btn", { onclick: () => import("./workspaces.js").then((m) => m.useMode("live")) }, "Go to Live")));
  $("#mode-hub-body").replaceChildren(body);
  $("#mode-hub-title").textContent = "Set up";
}

function tempoLine(s) {
  const t = s.tempo || {};
  const src = { prodj: "CDJs (Pro DJ Link)", link: "Ableton Link", midi: "MIDI clock", tap: "tapped", manual: "set by hand", sound: "the music" }[t.source] || t.source || "";
  return `${Math.round(t.bpm || 0)} BPM${src ? ` from ${src}` : ""}` + ((s.osc || {}).on ? " · OSC in on" : "");
}

// -------------------------------------------------------------------- Live
let liveEls = null;
let holding = 0;                       // hits held down now: no repaint under a finger

function partsNow() {
  const tl = snap().timeline || {};
  const tr = tl.transport || {};
  const marks = (tl.markers || []).filter((m) => m.name);
  let pos = +tr.pos || 0;
  if (tr.playing) pos += ((performance.now() - seen) / 1000) * (tr.rate || 1);
  let cur = null, next = null;
  for (const m of marks) {
    if (m.t <= pos + 1e-3) cur = m;
    else { next = m; break; }
  }
  const bars = next ? (next.t - pos) * (tl.bpm || 120) / 240 : null;
  return { cur, next, bars, playing: !!tr.playing };
}

function paintLiveNow() {
  if (!liveEls) return;
  const p = partsNow();
  liveEls.part.textContent = p.cur ? p.cur.name : (p.playing ? "-" : "");
  liveEls.next.textContent = p.next
    ? `${p.next.name} in ${Math.max(0, Math.ceil(p.bars))} bar${Math.ceil(p.bars) === 1 ? "" : "s"}`
    : (p.playing ? "the end of the song" : "");
  const span = p.cur && p.next ? (p.next.t - p.cur.t) * ((snap().timeline || {}).bpm || 120) / 240 : 0;
  liveEls.bar.style.width = span > 0 ? `${Math.max(0, Math.min(100, 100 - (p.bars / span) * 100))}%` : "0%";
  const t = snap().tempo || {};
  liveEls.beat.textContent = t.beat ? "●".repeat(t.beat) + "○".repeat(Math.max(0, 4 - t.beat)) : "";
}

const HITS = new Set(["strobe", "flash", "blinder", "blackout", "kill", "fog", "sfx", "colour", "color"]);

function paintLive() {
  if (holding) return;                 // repainted when the finger lifts
  const s = snap();
  const sg = s.songs || {};
  const song = (sg.songs || []).find((x) => x.id === (sg.following || sg.current));
  const t = s.tempo || {};
  const ap = s.autopilot || {};
  const q = s.quick || {};
  const active = new Set(q.active || []);
  const hits = (q.buttons || []).filter((b) => HITS.has(b.kind)).slice(0, 12);
  const pressBtn = (b) => {
    const el = h("button.live-hit" + (active.has(b.id) ? ".on" : ""), { type: "button", title: `${b.label} (${b.mode === "hold" ? "held while pressed" : "press"})` }, b.label);
    let finger = null;                           // the finger on it: one per button
    el.addEventListener("pointerdown", (e) => {
      if (finger !== null) return;
      finger = e.pointerId;
      el.setPointerCapture(e.pointerId);
      holding++;
      el.classList.add("on");
      run("quick_press", { id: b.id, down: true });
    });
    const up = (e) => {
      if (finger === null || e.pointerId !== finger) return;
      finger = null;
      holding = Math.max(0, holding - 1);
      run("quick_press", { id: b.id, down: false });
    };
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("lostpointercapture", up);
    return el;
  };
  const part = h("div.live-part"), next = h("div.live-next muted"), bar = h("i"), beat = h("span.live-beat");
  liveEls = { part, next, bar, beat };
  // Now, Tempo and Autopilot in one compact band at the top: what is
  // playing, the beat, and whether the desk is running looks by itself
  const body = h("div.hub-body.live",
    h("div.live-band",
      h("div.lb-now",
        h("div.lb-head", h("span.live-k", sg.following ? "Following the master deck" : song ? "Song" : "Now"),
          h("span.live-song", song ? song.name : (sg.deck ? `Track ${sg.deck} (no song)` : "No song playing"))),
        h("div.lb-part", part, next), h("div.live-progress", bar)),
      h("div.lb-cells",
        h("div.lb-cell", { title: tempoLine(s) },
          h("span.live-k", "Tempo"),
          h("div.live-bpm", h("b", String(Math.round((t.bpm || 0) * 10) / 10)), h("small", " BPM"), beat),
          h("div.live-row", h("button.btn.small", { onclick: () => run("tempo_tap", {}) }, "Tap"),
            h("button.btn.small", { title: "The phrase (8 bars) starts on this beat", onclick: () => run("tempo_phrase", {}, { toast: true }) }, "Phrase"))),
        h("div.lb-cell",
          h("span.live-k", "Autopilot"),
          h("div.lb-ap" + (ap.on ? ".on" : ""), ap.on ? `every ${ap.bars} bars` + (ap.bars_left != null ? ` · next in ${ap.bars_left}` : "") : sg.fallback ? "between songs" : "off"),
          h("div.live-row",
            h("button.btn.small" + (ap.on ? "" : ".primary"), { onclick: () => run("autopilot", { state: !ap.on }, { toast: true }) }, ap.on ? "Stop" : "Start"),
            h("button.btn.small", { disabled: !ap.on, title: "The next look now", onclick: () => run("autopilot_next", {}, { toast: true }) }, "Next"),
            h("button.btn.small.ghost", { title: "Autopilot settings", "aria-label": "Autopilot settings", onclick: () => import("./autopilot.js").then((m) => m.openAutopilot()) }, "⋯")))),
      h("div.live-row.lb-songs",
        h("button.btn.small", { onclick: () => import("./songs.js").then((m) => m.openSongs()) }, "Songs…"),
        h("label.check", h("input", { type: "checkbox", checked: !!sg.follow, onchange: (e) => run("song_follow", { state: e.target.checked }, { toast: true }) }),
          h("span", "Follow the DJ decks")))),
    // on a short screen this row comes first (app.css): blackout and the master in reach
    h("div.live-bo",
      h("button.btn" + (s.blackout ? ".on" : ""), { title: "Blackout (X)", onclick: () => run("blackout", { state: s.blackout ? 0 : 1 }) }, s.blackout ? "BLACKOUT on" : "BLACKOUT"),
      h("span.muted.small", `Master ${s.master ?? 100}%`)),
    h("div.live-k.hits-k", "Hits"),
    hits.length ? h("div.live-hits", ...hits.map(pressBtn)) : null,
    // the same buttons are already in the dock when it shows Buttons (app.css)
    hits.length ? h("p.muted.small.live-hits-below", "In the Buttons panel below.")
      : h("p.muted.small.live-nohits", "Strobe, flash and blinder buttons show here: press Edit on the Buttons panel to make them."),
  );
  $("#mode-hub-body").replaceChildren(body);
  $("#mode-hub-title").textContent = "Live";
  paintLiveNow();
}

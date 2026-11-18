// What Set up and Live put in the work area, where Program has the
// programmer.
//   Set up  every job before a show, in order - the lights, the room, the
//           connections, the checks - each with how it stands now
//   Live    one slim strip above the buttons, only while something plays by
//           itself: the song and its part, what comes next, the autopilot.
//           (The buttons are the screen; the tempo is in the title bar, the
//           master and blackout on the rail.)
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
          btn("Plot…", lazy("./plot.js", "openPlot")),
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
  liveEls.part.textContent = p.cur ? p.cur.name : "";
  liveEls.next.textContent = p.next
    ? `then ${p.next.name} in ${Math.max(0, Math.ceil(p.bars))} bar${Math.ceil(p.bars) === 1 ? "" : "s"}`
    : (p.playing ? "then the end of the song" : "");
  const span = p.cur && p.next ? (p.next.t - p.cur.t) * ((snap().timeline || {}).bpm || 120) / 240 : 0;
  liveEls.bar.style.width = span > 0 ? `${Math.max(0, Math.min(100, 100 - (p.bars / span) * 100))}%` : "0%";
}

// The strip is there only while the desk plays something by itself - the
// timeline running, a song followed from the DJ's decks, the autopilot.
// With nothing of that, the buttons have the whole work area.
let liveKey = "";
function paintLive() {
  const s = snap();
  const sg = s.songs || {};
  const song = (sg.songs || []).find((x) => x.id === (sg.following || sg.current));
  const ap = s.autopilot || {};
  const tr = (s.timeline || {}).transport || {};
  const playing = !!(tr.playing || sg.following);
  const busy = playing || !!ap.on;
  const box = $("#mode-hub");
  box.classList.toggle("quiet", !busy);
  $("#mode-hub-title").textContent = "Live";
  // (drawn again only when what it says changes: its buttons stay under a finger)
  const key = JSON.stringify([busy, playing, sg.following, song && song.name, sg.deck, ap.on, ap.bars, ap.bars_left]);
  if (key === liveKey && $("#mode-hub-body").firstChild) { paintLiveNow(); return; }
  liveKey = key;
  if (!busy) { liveEls = null; $("#mode-hub-body").replaceChildren(); return; }
  const part = h("b.now-part"), next = h("span.now-next"), bar = h("i");
  liveEls = { part, next, bar };
  $("#mode-hub-body").replaceChildren(h("div.hub-body.live.now",
    playing ? h("div.now-song", { title: tempoLine(s) },
      h("span.now-k", sg.following ? "Following the DJ" : "Playing"),
      h("b.now-name", song ? song.name : (sg.deck ? `Track ${sg.deck}` : "The timeline")),
      part, next, h("span.now-progress", bar)) : null,
    ap.on ? h("div.now-auto",
      h("span.now-k", "Autopilot"),
      h("span.now-next", `a new look every ${ap.bars} bars` + (ap.bars_left != null ? `, the next in ${ap.bars_left}` : "")),
      h("button.btn.small", { title: "The next look now", onclick: () => run("autopilot_next", {}, { toast: true }) }, "Next look"),
      h("button.btn.small", { title: "Stop the autopilot: the look stays as it is", onclick: () => run("autopilot", { state: false }, { toast: true }) }, "Stop")) : null,
    h("span.grow"),
    h("button.btn.small.ghost", { title: "Songs: a light show per song, follow the DJ's decks", onclick: () => import("./songs.js").then((m) => m.openSongs()) }, "Songs…")));
  paintLiveNow();
}

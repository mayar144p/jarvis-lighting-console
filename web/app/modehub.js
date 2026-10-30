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
const step = (title, status, ok, ...buttons) =>
  h("div.hub-step" + (ok === true ? ".ok" : ok === false ? ".todo" : ""),
    h("div.hub-step-head", h("i.hub-dot", { "aria-hidden": "true" }), h("b", title), h("span.muted.small", status)),
    h("div.hub-btns", ...buttons));
const btn = (label, fn, primary = false) => h("button.btn.small" + (primary ? ".primary" : ""), { onclick: fn }, label);
const lazy = (mod, fn, ...args) => () => import(mod).then((m) => m[fn](...args));

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
  const body = h("div.hub-body",
    h("p.muted.small", "Everything before the show, in order. The fixture list on the left follows along."),
    h("h3", "1 · The lights"),
    step("Add the lights", n ? `${n} light${n > 1 ? "s" : ""} patched` : "no lights yet", n > 0,
      btn("Add lights…", lazy("./dialogs.js", "openAddDialog"), !n),
      btn("Import a plot (MVR)…", lazy("./dialogs.js", "importMvr")),
      btn("Import a patch (CSV)…", lazy("./dialogs.js", "openCsvImport"))),
    step("DMX addresses", clashes ? `${clashes} address clash${clashes > 1 ? "es" : ""}` : (n ? "no clashes" : "-"), n ? clashes === 0 : null,
      btn("DMX map…", lazy("./fixtures.js", "openDmxMap")),
      btn("Address them all", async () => {
        if (await confirmBox("Auto-address", "Re-address every light from 1.001 with no gaps or overlaps? Ctrl+Z undoes it.", { ok: "Re-address" })) run("auto_patch", {}, { toast: true });
      })),
    h("h3", "2 · The room"),
    step("Room and rigging", v.name || v.template ? `${v.name || v.template}` : "a plain room", null,
      btn("Arrange the room", () => needs3d("Arranging the room") && import("./venuepanel.js").then((m) => m.setArranging(true))),
      btn("Room shape…", lazy("./roomdialog.js", "openRoomDialog")),
      btn("Rigging…", lazy("./rigdialog.js", "openRigDialog")),
      btn("Describe it in words…", lazy("./settings.js", "openSettingsAt", "venue"))),
    step("Lights match the real ones", (() => {
      const mv = (state.snap && state.snap.patch || []).filter((x) => (x.map || []).includes("pan") && (x.map || []).includes("tilt"));
      const done = mv.filter((x) => x.align).length;
      return mv.length ? `${done} of ${mv.length} moving light${mv.length > 1 ? "s" : ""} lined up` : "no moving lights";
    })(), null,
      btn("Line up with the real lights…", lazy("./lineup.js", "openLineUp"))),
    step("The room the lights know", (() => {
      const zs = (state.snap && state.snap.venue && state.snap.venue.zones) || [];
      const keep = zs.filter((z) => z.kind === "keepout").length, areas = zs.length - keep;
      return `${areas} zone${areas === 1 ? "" : "s"} (dance floor, bar...)${keep ? ` · ${keep} kept out` : ""} - point a light to mark them`;
    })(), null,
      btn("Teach the room by pointing…", lazy("./roomteach.js", "openRoomTeach")),
      btn("Not here (keep out)…", lazy("./roomteach.js", "openRoomTeach", "keepout"))),
    h("h3", "3 · Connections"),
    step("Output to the lights", s.live ? `LIVE to ${where}` : `blind - set to send to ${where}`, null,
      btn("Output settings…", lazy("./settings.js", "openSettingsAt", "output")),
      btn("Find nodes", lazy("./fixtures.js", "scanRig")),
      btn("Ask the lights (RDM)…", lazy("./fixtures.js", "openRdm"))),
    step("DJ gear and controllers", tempoLine(s), null,
      btn("MIDI, OSC, Stream Deck…", lazy("./settings.js", "openSettingsAt", "midi")),
      btn("Songs…", lazy("./songs.js", "openSongs"))),
    step("Smart lamps and a spare desk",
      [hue.paired ? `Hue: ${hue.links} lamp(s)` : "", sb.following ? `backup of ${sb.host}` : ""].filter(Boolean).join(" · ") || "optional", null,
      btn("Philips Hue…", lazy("./settings.js", "openSettingsAt", "hue")),
      btn("Spare desk…", lazy("./settings.js", "openSettingsAt", "standby"))),
    h("h3", "4 · Checks"),
    step("At the venue", "the lights found here against the show", null,
      btn("Venue check-in…", lazy("./checkin.js", "openCheckin"), n > 0)),
    step("Before doors", "test a light, then the ready check", null,
      btn("Test a light…", () => {
        const sel = new Set(s.selected || []);
        const hd = patch().find((x) => sel.has(x.head_no)) || patch()[0];
        if (hd) import("./lightdialogs.js").then((m) => m.openLightTest(hd));
      }),
      btn("Ready check…", lazy("./dialogs.js", "openReadyCheck"))),
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
  const body = h("div.hub-body.live",
    h("div.live-card",
      h("div.live-k", sg.following ? "Following the master deck" : song ? "Song" : "Now"),
      h("div.live-song", song ? song.name : (sg.deck ? `Track ${sg.deck} (no song)` : "No song playing")),
      part, h("div.live-progress", bar), next,
      h("div.live-row",
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
    h("div.live-pair",
      h("div.live-card",
      h("div.live-k", "Tempo"),
      h("div.live-bpm", h("b", String(Math.round((t.bpm || 0) * 10) / 10)), " BPM ", beat),
      h("div.muted.small", tempoLine(s)),
      h("div.live-row", h("button.btn.small", { onclick: () => run("tempo_tap", {}) }, "Tap"),
        h("button.btn.small", { onclick: () => run("tempo_phrase", {}, { toast: true }) }, "Phrase starts now"))),
      h("div.live-card",
      h("div.live-k", "Autopilot"),
      h("div", ap.on ? `On - a new look every ${ap.bars} bars` + (ap.bars_left != null ? ` (next in ${ap.bars_left})` : "") : sg.fallback ? "Covering between songs" : "Off"),
      h("div.live-row",
        h("button.btn.small" + (ap.on ? "" : ".primary"), { onclick: () => run("autopilot", { state: !ap.on }, { toast: true }) }, ap.on ? "Stop" : "Start"),
        h("button.btn.small", { disabled: !ap.on, onclick: () => run("autopilot_next", {}, { toast: true }) }, "Next look"),
        h("button.btn.small", { onclick: () => import("./autopilot.js").then((m) => m.openAutopilot()) }, "Settings…"))),
));
  $("#mode-hub-body").replaceChildren(body);
  $("#mode-hub-title").textContent = "Live";
  paintLiveNow();
}

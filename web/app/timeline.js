// The show timeline: tracks of timed clips under a playhead, with the
// song's waveform, a beat grid and snapping.  The engine owns the clock
// (lights stay in time even if this tab stutters); this panel draws it,
// edits it, and keeps the audio following the engine.
import { post, token } from "./api.js";
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { $, h, menu, modal, toast, promptBox, typingInField, cssVar } from "./ui.js";
import { songParts, loudness, musicTraits } from "./songparts.js";
import { icon } from "./icons.js";
import { groupColour, GROUP_TINTS } from "./groupcolour.js";
import { shortcutsOn } from "./shortcuts.js";

const LANE_H = 46;
const KINDS = [["cue", "Cue track", "GO a playback's cues at set times"],
  ["button", "Button track", "Hold quick buttons (flashes, strobes, bumps)"],
  ["fx", "Effect track", "Run effects on a group or type"],
  ["level", "Level track", "Automate a playback fader or the grand master"]];
const FX = [["rainbow", "Rainbow"], ["circle", "Circle"], ["figure_eight", "Figure 8"], ["pan_sweep", "Pan sweep"],
  ["breathe", "Breathe"], ["dimmer_chase", "Dimmer chase"], ["sparks", "Sparks"]];
const TARGETS = [["all", "All lights"], ["spot", "Moving spots"], ["beam", "Beams"], ["wash", "Washes"], ["par", "PARs"], ["bar", "Bars"]];

let pxs = 24;                         // pixels per second
let snap = "beat";
let transport = { playing: false, pos: 0, length: 120, loop: false };
let stamp = 0;                        // performance.now() of the last transport
let docSig = "";
let audio = null;                     // {el, id, peaks, rate, url}
let taps = [];
let selectedClip = null;
let visible = false;

const doc = () => (state.snap && state.snap.timeline) || { length: 120, bpm: 120, tracks: [], markers: [], audio: null };
const beat = () => 60 / (doc().bpm || 120);

function snapT(t, free = false) {
  if (free || snap === "off") return Math.max(0, Math.round(t * 100) / 100);
  const step = snap === "beat" ? beat() : snap === "bar" ? beat() * 4 : +snap;
  return Math.max(0, Math.round(t / step) * step);
}

function fmt(t) {
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

// the ruler names every Nth bar - 1, 2, 4, 8, 16... - at least 72 px apart,
// and the lanes' grid lines fall on those bars
function barsEvery() {
  const barPx = beat() * 4 * pxs;
  let every = 1;
  while (every * barPx < 72 && every < 1024) every *= 2;
  return every;
}

// a track's colour: an effect track takes its group's (as on its fader),
// or one of the group colours by its place; cues and hits are quiet, a
// level is the dimmer's gold
function trackColour(tr, i) {
  if (tr.kind === "level") return "var(--f-dim)";
  if (tr.kind !== "fx") return "var(--text-2)";
  const groups = (state.snap && state.snap.groups) || [];
  const used = new Set(tr.clips.map((c) => c.target && c.target.group).filter((x) => x !== undefined && x !== null && x !== ""));
  const bare = String(tr.name || "").replace(/^Auto · /, "");
  const gi = used.size === 1 ? groups.findIndex((g) => g.n === [...used][0] || g.name === [...used][0]) : groups.findIndex((g) => g.name === bare);
  return gi >= 0 ? groupColour(groups[gi], gi) : GROUP_TINTS[i % GROUP_TINTS.length];
}
const KIND_LINE = { cue: (tr) => `Cues, playback ${tr.playback}`, button: () => "Button presses", fx: () => "Effects",
  level: (tr) => (tr.target === "master" ? "Level, the master" : `Level, ${String(tr.target || "").toUpperCase()}`) };

function trackMenu(anchor, tr) {
  menu(anchor, [
    { label: "Rename…", run: async () => {
      const name = await promptBox("Rename track", "Name", String(tr.name || "").replace(/^Auto · /, ""), { ok: "Rename" });
      if (name && name.trim()) run("timeline_track", { id: tr.id, name: name.trim() });
    } },
    { label: tr.mute ? "Play it again" : "Mute it", run: () => run("timeline_track", { id: tr.id, mute: !tr.mute }) },
    "-",
    { label: "Delete the track", danger: true, hint: "Undo brings it back", run: () => run("timeline_track", { id: tr.id, remove: true }) },
  ]);
}

function pos() {
  if (!transport.playing) return transport.pos;
  return Math.min(transport.length, transport.pos + (performance.now() - stamp) / 1000 * (transport.rate || 1));
}

// ------------------------------------------------------------ drawing
function render() {
  const d = doc();
  const sig = JSON.stringify([d, pxs]);
  $("#tl-len").textContent = fmt(d.length);
  $("#tl-loop").classList.toggle("on", !!d.loop);
  if (sig === docSig) return;
  docSig = sig;
  const width = Math.max(600, Math.ceil(d.length * pxs) + 40);
  const canvas = $("#tl-canvas");
  canvas.style.width = width + "px";
  // the lanes' faint lines fall on the bars the ruler names
  canvas.style.setProperty("--bar-px", `${Math.max(4, beat() * 4 * pxs * barsEvery())}px`);
  // headers and lanes
  const heads = [h("div.tl-head.ruler-head", h("button.tl-tempo", { type: "button", title: "The timeline's tempo: click to change it (or tap it: Music…)",
    onclick: setTempo }, `Bars at ${+(+d.bpm || 120).toFixed(1)} BPM`))];
  const lanes = [];
  if (d.audio) {
    heads.push(h("div.tl-head.audio-head", h("b", "♪ " + d.audio.name),
      h("button.icon-x", { title: "Remove the audio", onclick: () => run("timeline_set", { clear_audio: true }) }, "×")));
    lanes.push(h("div.tl-lane.audio-lane", h("canvas.wave", { width, height: LANE_H })));
  }
  // each track: its colour down the edge, its name, what it plays, Mute, ⋯
  d.tracks.forEach((tr, i) => {
    const colour = trackColour(tr, i);
    const bare = String(tr.name || "").replace(/^Auto · /, "");
    heads.push(h("div.tl-head" + (tr.mute ? ".muted" : ""), { dataset: { track: tr.id }, style: `--k:${colour}` },
      h("div.tl-head-text", h("b", { title: tr.name.startsWith("Auto · ") ? `${bare} (made by the show builder)` : bare }, bare),
        h("small", (KIND_LINE[tr.kind] || (() => ""))(tr))),
      h("button.tl-mute" + (tr.mute ? ".on" : ""), { type: "button", "aria-pressed": String(!!tr.mute),
        title: tr.mute ? "Muted: click to play this track again" : "Mute this track", onclick: () => run("timeline_track", { id: tr.id, mute: !tr.mute }) }, "Mute"),
      h("button.tl-more", { type: "button", "aria-label": `${bare}: more`, title: "Rename, mute, delete", onclick: (e) => trackMenu(e.currentTarget, tr) }, "⋯")));
    lanes.push(lane(tr, width, colour));
  });
  $("#tl-heads").replaceChildren(...heads);
  $("#tl-lanes").replaceChildren(...lanes);
  // a word on how, while it is new: no tracks yet, or no clips on them
  const hint = $("#tl-hint");
  const clips = d.tracks.some((tr) => tr.clips.length);
  hint.hidden = clips;
  hint.textContent = !d.tracks.length ? "No tracks yet: + Track adds one - cues, button hits, effects or a level (or ask the Copilot to build the show)."
    : "Double-click a lane to add · drag a clip to move it, its edge to resize · right-click to edit · Alt: no snap";
  drawRuler(width);
  drawWave();
  drawHead();
}

function lane(tr, width, colour) {
  const el = h("div.tl-lane" + (tr.mute ? ".muted" : ""), { dataset: { track: tr.id, kind: tr.kind } });
  el.style.width = width + "px";
  if (colour) el.style.setProperty("--k", colour);
  if (tr.kind === "level") {
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("width", width);
    svg.setAttribute("height", LANE_H);
    const pts = tr.clips.map((c) => [c.t * pxs, (1 - c.v / 100) * (LANE_H - 8) + 4]);
    if (pts.length) {
      const all = [[0, pts[0][1]], ...pts, [width, pts[pts.length - 1][1]]];
      // the level filled under its line, the dimmer's gold
      const fill = document.createElementNS(svgNS, "polygon");
      fill.setAttribute("points", [...all, [width, LANE_H], [0, LANE_H]].map((p) => p.join(",")).join(" "));
      fill.setAttribute("class", "lvl-fill");
      svg.appendChild(fill);
      const line = document.createElementNS(svgNS, "polyline");
      line.setAttribute("points", all.map((p) => p.join(",")).join(" "));
      line.setAttribute("class", "lvl-line");
      svg.appendChild(line);
    }
    el.appendChild(svg);
    for (const c of tr.clips) {
      const dot = h("div.lvl-key", { title: `${c.v}% at ${fmt(c.t)} · drag to move, right-click to delete`, dataset: { clip: c.id } });
      dot.style.left = c.t * pxs + "px";
      dot.style.top = (1 - c.v / 100) * (LANE_H - 8) + 4 + "px";
      dragKey(dot, tr, c);
      dot.addEventListener("contextmenu", (e) => { e.preventDefault(); run("timeline_clip", { id: c.id, remove: true }); });
      el.appendChild(dot);
    }
  } else {
    for (const c of tr.clips) el.appendChild(clipEl(tr, c));
  }
  el.addEventListener("dblclick", (e) => {
    if (e.target !== el && !e.target.closest("svg")) return;
    const r = el.getBoundingClientRect();
    const t = snapT((e.clientX - r.left) / pxs, e.altKey);
    if (tr.kind === "level") {
      const v = Math.round(Math.max(0, Math.min(100, (1 - (e.clientY - r.top - 4) / (LANE_H - 8)) * 100)));
      run("timeline_clip", { track: tr.id, t, v });
    } else {
      addClip(tr, t, e);
    }
  });
  return el;
}

function clipLabel(tr, c) {
  if (c.label) return c.label;
  if (tr.kind === "cue") {
    const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === tr.playback);
    const cue = c.cue === "next" ? null : ((pb && pb.stack) || []).find((x) => x.n === c.cue);
    return c.cue === "next" ? "GO" : (cue && cue.name) || `Cue ${c.cue}`;
  }
  if (tr.kind === "button") {
    const b = (((state.snap && state.snap.quick) || {}).buttons || []).find((x) => x.id === c.button);
    return b ? b.label : c.button;
  }
  if (tr.kind === "fx") {
    const step = c.step && ((state.snap && state.snap.step_fx) || []).find((x) => x.id === c.step);
    const mv = c.move && ((state.snap && state.snap.moves) || []).find((x) => x.id === c.move);
    const name = c.step ? (c.label || (step && step.name) || "Step effect")
      : c.move ? (c.label || (mv && mv.name) || "Move") : (FX.find(([k]) => k === c.fx) || [c.fx, c.fx])[1];
    const t = c.target || {};
    const who = t.type ? (TARGETS.find(([k]) => k === t.type) || [0, t.type])[1] : t.group ? `group ${t.group}` : t.heads ? `${t.heads.length} light(s)` : "all";
    return `${name} · ${who}`;
  }
  return "";
}

function clipEl(tr, c) {
  const span = c.dur !== undefined;
  const el = h("div.tl-clip." + tr.kind + (selectedClip === c.id ? ".sel" : "") + (span ? "" : ".point"), {
    title: `${clipLabel(tr, c)} · ${fmt(c.t)}${span ? " for " + c.dur.toFixed(2) + " s" : ""}\nDrag to move · right-click for options`,
    dataset: { clip: c.id },
  }, h("span", clipLabel(tr, c)));
  el.style.left = c.t * pxs + "px";
  if (span) {
    el.style.width = Math.max(6, c.dur * pxs) + "px";
    const grip = h("i.grip");
    el.appendChild(grip);
    dragClip(grip, tr, c, "dur");
  }
  dragClip(el, tr, c, "t");
  el.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    selectedClip = c.id;
    menu(el, [
      { label: "Edit…", run: () => addClip(tr, c.t, e, c) },
      { label: "Duplicate after it", run: () => run("timeline_clip", { track: tr.id, ...c, id: undefined, t: c.t + (c.dur || beat() * 4) }) },
      { label: "Delete", danger: true, run: () => run("timeline_clip", { id: c.id, remove: true }) },
    ]);
  });
  return el;
}

function dragClip(handle, tr, c, what) {
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    selectedClip = c.id;
    const el = handle.closest(".tl-clip");
    el.classList.add("sel");
    handle.setPointerCapture(e.pointerId);
    const x0 = e.clientX;
    const start = what === "t" ? c.t : c.dur;
    let value = start;
    const move = (ev) => {
      const dt = (ev.clientX - x0) / pxs;
      if (what === "t") {
        value = snapT(start + dt, ev.altKey);
        el.style.left = value * pxs + "px";
      } else {
        value = Math.max(0.05, snapT(c.t + start + dt, ev.altKey) - c.t);
        el.style.width = Math.max(6, value * pxs) + "px";
      }
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      if (Math.abs(value - start) > 1e-3) run("timeline_clip", { id: c.id, [what]: +value.toFixed(3) });
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
}

function dragKey(dot, tr, c) {
  dot.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    dot.setPointerCapture(e.pointerId);
    const x0 = e.clientX, y0 = e.clientY;
    let t = c.t, v = c.v;
    const move = (ev) => {
      t = snapT(c.t + (ev.clientX - x0) / pxs, ev.altKey);
      v = Math.round(Math.max(0, Math.min(100, c.v - (ev.clientY - y0) / (LANE_H - 8) * 100)));
      dot.style.left = t * pxs + "px";
      dot.style.top = (1 - v / 100) * (LANE_H - 8) + 4 + "px";
    };
    const up = () => {
      dot.removeEventListener("pointermove", move);
      dot.removeEventListener("pointerup", up);
      if (t !== c.t || v !== c.v) run("timeline_clip", { id: c.id, t, v });
    };
    dot.addEventListener("pointermove", move);
    dot.addEventListener("pointerup", up);
  });
}

// The ruler: every Nth bar its number and its time ("17  0:32"), a line
// down to the lanes there, short ticks for the bars between (when there is
// room); the song's markers in amber.  As tall as the head beside it.
function drawRuler(width) {
  const cv = $("#tl-ruler");
  const head = $(".tl-head.ruler-head");
  const H = Math.max(18, Math.round(head ? head.getBoundingClientRect().height : 30) || 30);
  const k = H / 30, dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(width * dpr);
  cv.height = Math.round(H * dpr);
  cv.style.width = width + "px";             // (its height is the head's, in CSS: they line up whenever it is drawn)
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, width, H);
  const font = cssVar("--font") || "system-ui, sans-serif";
  const ink = { bar: cssVar("--text-2"), time: cssVar("--text-3"), major: cssVar("--line-strong"), minor: cssVar("--line"), mark: cssVar("--blind") };
  const d = doc();
  const barS = beat() * 4, barPx = barS * pxs, every = barsEvery();
  const mid = Math.round(H / 2 + 4 * k);
  for (let i = 0; i * barS <= d.length + 1e-6; i++) {
    const x = Math.round(i * barPx) + 0.5;
    const major = i % every === 0;
    if (!major && barPx < 8) continue;
    g.strokeStyle = major ? ink.major : ink.minor;
    g.beginPath(); g.moveTo(x, major ? 0 : H - 5 * k); g.lineTo(x, H); g.stroke();
    if (!major) continue;
    const num = String(i + 1);
    g.font = `500 ${Math.round(12 * k)}px ${font}`;
    g.fillStyle = ink.bar;
    g.fillText(num, x + 6, mid);
    const w = g.measureText(num).width;
    g.font = `400 ${Math.round(12 * k)}px ${font}`;
    g.fillStyle = ink.time;
    g.fillText(fmt(i * barS).replace(/\.0$/, ""), x + 6 + w + 7 * k, mid);
  }
  g.font = `500 ${Math.round(11 * k)}px ${font}`;
  for (const m of d.markers || []) {
    const x = m.t * pxs;
    g.fillStyle = ink.mark;
    g.fillRect(x, 0, 2, H);
    g.fillText(m.name, x + 4, H - 3 * k);
  }
}

function drawWave() {
  const d = doc();
  const cv = $("#tl-lanes .wave");
  if (!cv || !d.audio || !audio || audio.id !== d.audio.id || !audio.peaks) return;
  const g = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  g.fillStyle = cssVar("--text-3");
  const off = d.audio.offset || 0;
  for (let x = 0; x < W; x++) {
    const t0 = x / pxs - off, t1 = (x + 1) / pxs - off;
    if (t1 < 0 || t0 > audio.duration) continue;
    const i0 = Math.max(0, Math.floor(t0 * audio.rate)), i1 = Math.min(audio.peaks.length, Math.ceil(t1 * audio.rate) + 1);
    let peak = 0;
    for (let i = i0; i < i1; i++) peak = Math.max(peak, audio.peaks[i]);
    const hh = Math.max(1, peak * (H - 4));
    g.fillRect(x, (H - hh) / 2, 1, hh);
  }
}

function drawHead() {
  const p = pos();
  $("#tl-pos").textContent = fmt(p);
  const head = $("#tl-playhead");
  head.style.transform = `translateX(${p * pxs}px)`;
  const m = Math.floor(p / 60);
  const tag = $("#tl-ph-tag");
  tag.textContent = `${m}:${String(Math.floor(p - m * 60)).padStart(2, "0")}`;
  tag.style.transform = p * pxs < 24 ? "none" : "";              // (at the start: beside the line, not half off)
  const play = $("#tl-play");
  if (play.dataset.playing !== String(transport.playing)) {
    play.dataset.playing = String(transport.playing);
    play.replaceChildren(icon(transport.playing ? "pause" : "play"), h("span", transport.playing ? "Pause" : "Play"));
  }
  if (transport.playing) {
    const sc = $("#tl-scroll");
    const x = p * pxs;
    if (x < sc.scrollLeft + 40 || x > sc.scrollLeft + sc.clientWidth - 80) sc.scrollLeft = Math.max(0, x - 80);
  }
}

// ------------------------------------------------------------ editing
function addTrackMenu(btn) {
  const pbs = ((state.snap && state.snap.playbacks) || []).filter((p) => (p.stack || []).length);
  menu(btn, [
    ...KINDS.map(([k, l, hint]) => ({ label: l, hint, run: () => addTrack(k) })),
    "-",
    ...pbs.map((p) => ({ label: `Cue list from PB${p.n}`, hint: `${p.stack.length} cues, timed by their fades and follows`,
      run: () => run("timeline_from_playback", { playback: p.n }, { toast: true }) })),
  ]);
}

async function addTrack(kind) {
  if (kind === "cue") {
    const n = await promptBox("Cue track", "Which playback does this track GO? (1-10)", "1");
    if (n) run("timeline_track", { kind, playback: +n || 1, name: `PB${+n || 1}` });
  } else if (kind === "level") {
    const target = await promptBox("Level track", "Automate which fader? (type pb1…pb10, or master)", "master");
    if (target) run("timeline_track", { kind, target: target.trim().toLowerCase(), name: target.trim().toLowerCase() === "master" ? "Grand master" : target.toUpperCase() });
  } else {
    run("timeline_track", { kind, name: kind === "button" ? "Hits" : "Effects" });
  }
}

function addClip(tr, t, ev, existing = null) {
  const anchor = ev.target instanceof Element ? ev.target : $("#tl-lanes");
  if (tr.kind === "cue") {
    const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === tr.playback);
    const cues = (pb && pb.stack) || [];
    menu(anchor, [
      { label: "GO (next cue)", run: () => save({ cue: "next" }) },
      ...cues.map((c) => ({ label: `Cue ${c.n}${c.name ? " · " + c.name : ""}`, run: () => save({ cue: c.n }) })),
    ]);
  } else if (tr.kind === "button") {
    const buttons = (((state.snap && state.snap.quick) || {}).buttons || []);
    if (!buttons.length) { toast("Set up some quick buttons first (Buttons → Suggest)"); return; }
    menu(anchor, buttons.map((b) => ({ label: b.label, hint: `${b.kind} · page ${b.page}`,
      run: () => save({ button: b.id, dur: existing ? existing.dur : +(beat()).toFixed(3) }) })));
  } else if (tr.kind === "fx") {
    // the effects, and the show's own saved moves (My moves) with their settings
    const moves = (state.snap && state.snap.moves) || [];
    const fx = h("select.select", h("optgroup", { label: "Effects" }, ...FX.map(([k, l]) => h("option", { value: k }, l))),
      moves.length ? h("optgroup", { label: "My moves" }, ...moves.map((m) => h("option", { value: "move:" + m.id }, m.name))) : null);
    const who = h("select.select", ...TARGETS.map(([k, l]) => h("option", { value: k }, l)),
      ...((state.snap && state.snap.groups) || []).map((g) => h("option", { value: "g" + g.n }, "Group: " + g.name)));
    const dur = h("input", { type: "number", min: 0.1, step: 0.5, value: existing ? existing.dur : +(beat() * 16).toFixed(2) });
    if (existing) {
      fx.value = existing.move ? "move:" + existing.move : existing.fx;
      const t0 = existing.target || {};
      who.value = t0.group !== undefined ? "g" + t0.group : t0.type || "all";
    }
    const close = modal({
      title: existing ? "Edit effect clip" : "Effect clip",
      body: h("div.form-grid", h("label.field", h("span", "Effect"), fx), h("label.field", h("span", "On"), who), h("label.field", h("span", "Length (s)"), dur)),
      foot: [h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", { onclick: () => {
        const w = who.value;
        const pick = fx.value.startsWith("move:") ? { move: fx.value.slice(5), fx: "" } : { fx: fx.value, move: "" };
        save({ ...pick, dur: +dur.value || 4, target: w === "all" ? { all: true } : w[0] === "g" ? { group: +w.slice(1) } : { type: w } });
        close();
      } }, "Save")],
    });
  }
  function save(fields) {
    if (existing) run("timeline_clip", { id: existing.id, ...fields });
    else run("timeline_clip", { track: tr.id, t, ...fields });
  }
}

// ------------------------------------------------------------ audio
async function loadAudio(meta) {
  if (!meta) { if (audio) { audio.el.pause(); } audio = null; return; }
  if (audio && audio.id === meta.id) return;
  if (audio) audio.el.pause();
  const t = token();
  const r = await fetch("/api/console/audio?id=" + encodeURIComponent(meta.id), { headers: t ? { "X-Haze-Token": t } : {} });
  if (!r.ok) { toast("Could not load the timeline audio", "bad"); return; }
  const buf = await r.arrayBuffer();
  const url = URL.createObjectURL(new Blob([buf]));
  const el = new Audio(url);
  el.preload = "auto";
  audio = { id: meta.id, el, url, peaks: null, rate: 200, duration: meta.duration || 0 };
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await ctx.decodeAudioData(buf.slice(0));
    audio.duration = decoded.duration;
    audio.decoded = decoded;
    const ch = decoded.getChannelData(0);
    const per = Math.max(1, Math.floor(decoded.sampleRate / audio.rate));
    const peaks = new Float32Array(Math.ceil(ch.length / per));
    let max = 0;
    for (let i = 0; i < peaks.length; i++) {
      let p = 0;
      for (let j = i * per, e = Math.min(ch.length, j + per); j < e; j += 4) p = Math.max(p, Math.abs(ch[j]));
      peaks[i] = p;
      max = Math.max(max, p);
    }
    if (max > 0) for (let i = 0; i < peaks.length; i++) peaks[i] /= max;
    audio.peaks = peaks;
    ctx.close();
    docSig = "";
    render();
  } catch (e) { /* plays without a waveform */ }
}

/** Tempo from the audio: onset energy, autocorrelated over 70-180 BPM. */
function detectBpm() {
  if (!audio || !audio.decoded) { toast("Load audio first"); return null; }
  const ch = audio.decoded.getChannelData(0), sr = audio.decoded.sampleRate;
  const hop = Math.floor(sr / 100);                   // 100 Hz envelope
  const env = [];
  for (let i = 0; i + hop < ch.length && env.length < 100 * 90; i += hop) {
    let e = 0;
    for (let j = i; j < i + hop; j += 2) e += ch[j] * ch[j];
    env.push(Math.sqrt(e));
  }
  const on = env.map((v, i) => Math.max(0, v - (env[i - 1] || 0)));
  let best = 0, bestLag = 0;
  for (let lag = Math.floor(6000 / 180); lag <= Math.ceil(6000 / 70); lag++) {
    let s = 0;
    for (let i = lag; i < on.length; i++) s += on[i] * on[i - lag];
    if (s > best) { best = s; bestLag = lag; }
  }
  return bestLag ? Math.round(6000 / bestLag * 10) / 10 : null;
}

/** The song's parts (intro, build, drop, breakdown, outro) as markers, from
 * its loudness phrase by phrase - Auto show then follows this song. */
function findParts() {
  if (!audio || !audio.decoded) { toast("The audio is still loading - try again in a moment"); return; }
  const d = doc();
  let bpm = d.bpm;
  if (!bpm || bpm === 120) bpm = detectBpm() || bpm || 120;   // 120 is the default, not a measurement
  const env = loudness(audio.decoded.getChannelData(0), audio.decoded.sampleRate, 20);
  const off = (d.audio && d.audio.offset) || 0;
  const raw = songParts(env, 20, bpm);
  // what each part sounds like (drums, bass, a lead, how bright): the show designer reads it
  const hear = musicTraits(audio.decoded.getChannelData(0), audio.decoded.sampleRate, raw, bpm);
  const parts = raw.map((p, i) => ({ t: +(p.t + off).toFixed(2), name: p.name, hear: (hear[i] || {}).hear || "" }));
  if (parts.length < 2) { toast("No clear parts in this song: add markers by hand (Add a marker at the playhead)"); return; }
  run("timeline_set", { bpm, markers: parts }, { toast: `${parts.length} parts: ${parts.map((p) => p.name).join(", ")}` });
}

async function uploadAudio(file) {
  if (file.size > 22 * 1024 * 1024) { toast("Audio must be under 22 MB", "bad"); return; }
  toast("Uploading audio…");
  const data = await new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = () => rej(r.error);
    r.readAsDataURL(file);
  });
  let got;
  try {
    got = await post("/api/console/audio", { data });
  } catch (err) { toast(err.message || String(err), "bad"); return; }
  let duration = 0;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    duration = (await ctx.decodeAudioData(await file.arrayBuffer())).duration;
    ctx.close();
  } catch (e) { toast("The browser could not decode that audio", "bad"); }
  const d = doc();
  await run("timeline_set", { audio: { id: got.id, name: file.name, duration, offset: 0 },
    length: Math.max(d.length, Math.ceil(duration) + 2) }, { toast: `Audio "${file.name}" on the timeline` });
}

function syncAudio() {
  const d = doc();
  if (!audio || !d.audio || audio.id !== d.audio.id) return;
  const want = pos() - (d.audio.offset || 0);
  const el = audio.el;
  // a song following a DJ deck: the deck plays the music, the desk stays quiet
  if (transport.playing && !transport.deck && want >= 0 && want < audio.duration) {
    if (el.playbackRate !== (transport.rate || 1)) el.playbackRate = transport.rate || 1;
    if (Math.abs(el.currentTime - want) > 0.08) el.currentTime = want;
    if (el.paused) el.play().catch(() => { /* needs a click first */ });
  } else if (!el.paused) {
    el.pause();
  }
}

// ------------------------------------------------------------ wiring
function onTransport(t) {
  if (!t) return;
  transport = t;
  const tc = t.timecode || {};
  const b = $("#tl-tc");
  if (b) {
    b.classList.toggle("on", !!tc.follow);
    const kind = tc.source || "MTC";
    b.textContent = tc.follow ? (tc.running ? `${kind} ${tc.time}` : "TC · waiting") : "TC";
  }
  stamp = performance.now();
  if (visible) drawHead();
  syncAudio();
}

function loop() {
  if (visible && transport.playing) { drawHead(); }
  requestAnimationFrame(loop);
}

/** The timeline's music as a stream for the video recorder (it plays as
 *  the timeline plays), or null when there's none or the browser can't. */
/** The timeline's music as sound to put in a film drawn frame by frame
 *  (movierender.js): {buffer (decoded), offset (s: where on the timeline
 *  it starts)}, or null - none, or not decoded yet. */
export function timelineMusic() {
  if (!audio || !audio.decoded) return null;
  const d = doc();
  return { buffer: audio.decoded, offset: (d.audio && d.audio.offset) || 0 };
}

export function timelineAudioStream() {
  if (!audio || !audio.el) return null;
  try {
    const s = audio.el.captureStream ? audio.el.captureStream() : audio.el.mozCaptureStream ? audio.el.mozCaptureStream() : null;
    return s && s.getAudioTracks().length ? s : null;
  } catch (e) {
    return null;
  }
}

export function setTimelineVisible(on) {
  visible = on;
  if (on) { docSig = ""; render(); loadAudio(doc().audio); }
}

export function initTimeline() {
  $("#tl-play").addEventListener("click", () => run(transport.playing ? "timeline_pause" : "timeline_play"));
  $("#tl-stop").addEventListener("click", () => run("timeline_stop"));
  $("#tl-tc").addEventListener("click", (e) => timecodeMenu(e.currentTarget));
  $("#tl-loop").addEventListener("click", () => run("timeline_set", { loop: !doc().loop }));
  $("#tl-snap").addEventListener("change", (e) => { snap = e.target.value; });
  $("#tl-zoom-in").addEventListener("click", () => { pxs = Math.min(240, pxs * 1.5); docSig = ""; render(); });
  $("#tl-zoom-out").addEventListener("click", () => { pxs = Math.max(2, pxs / 1.5); docSig = ""; render(); });
  $("#tl-length").addEventListener("click", async () => {
    const v = await promptBox("Timeline length", "Length in seconds", String(doc().length));
    if (v) run("timeline_set", { length: +v || doc().length });
  });
  // Music…: the song, its tempo and parts, timecode, the songs
  $("#tl-audio").addEventListener("click", (e) => {
    const btn = e.currentTarget;
    menu(btn, [
      { label: doc().audio ? "Replace the music…" : "Add music…", hint: "MP3, WAV, OGG, FLAC, M4A", run: () => $("#tl-audio-file").click() },
      { label: "Find the tempo in the music", disabled: !doc().audio, run: () => {
        const bpm = detectBpm();
        if (bpm) run("timeline_set", { bpm }, { toast: `Tempo ${bpm} BPM` });
      } },
      { label: "Find the song's parts", hint: "intro, build, drop… as markers", disabled: !doc().audio, run: findParts },
      { label: "Add a marker at the playhead", run: async () => {
        const name = await promptBox("Marker", "Name (verse, drop, chorus…)", "");
        if (name) run("timeline_set", { markers: [...(doc().markers || []), { t: +pos().toFixed(2), name }] });
      } },
      "-",
      { label: `Tempo: ${+(+doc().bpm || 120).toFixed(1)} BPM…`, run: setTempo },
      { label: "Tap the tempo", hint: "tap here along with the music (three taps or more)", keep: true, run: tapTempo },
      { label: "Follow timecode…", hint: "MIDI (MTC) or audio (LTC)", run: () => timecodeMenu(btn) },
      { label: "Songs…", hint: "a light show per song; follow the DJ's decks", run: () => import("./songs.js").then((m) => m.openSongs()) },
      "-",
      { label: "Remove the music", disabled: !doc().audio, danger: true, run: () => run("timeline_set", { clear_audio: true }) },
    ]);
  });
  $("#tl-add").addEventListener("click", (e) => addTrackMenu(e.currentTarget));
  $("#tl-audio-file").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) uploadAudio(f);
  });
  wireLanes();
}

// Timecode: follow MIDI (MTC) or audio (LTC) timecode, or stop following
async function timecodeMenu(btn) {
    const tc = transport.timecode || {};
    const { ltcListening, startLtc, stopLtc } = await import("./ltcin.js");
    const follow = async (title, ltc) => {
      const off = await promptBox(title, "The timecode where the timeline starts (seconds, or h:mm:ss)", tc.offset ? String(tc.offset) : "0", { ok: "Follow" });
      if (off === null) return;
      const secs = String(off).includes(":") ? String(off).split(":").reduce((a, v) => a * 60 + (+v || 0), 0) : +off || 0;
      if (ltc) {
        try { await startLtc(); } catch (err) { toast(err.message || String(err), "bad", 7000); return; }
      } else stopLtc();
      run("timecode", { state: true, offset: secs }, { toast: true });
    };
    menu(btn, [
      { label: "Follow MIDI timecode (MTC)", hint: "from the desk's MIDI input", run: () => follow("Follow MIDI timecode", false) },
      { label: "Follow audio timecode (LTC)", hint: "a timecode track into this computer's sound input (picked in Sound)",
        run: () => follow("Follow audio timecode", true) },
      { label: "Don't follow timecode", disabled: !tc.follow && !ltcListening(), run: () => { stopLtc(); run("timecode", { state: false }, { toast: true }); } },
    ]);
}

// the timeline's tempo: typed, or tapped along with the music
async function setTempo() {
  const v = await promptBox("Timeline tempo", "Beats a minute (BPM)", String(+(+doc().bpm || 120).toFixed(1)), { ok: "Set" });
  if (v !== null && +v >= 20 && +v <= 300) run("timeline_set", { bpm: +(+v).toFixed(1) });
}
function tapTempo() {
  const now = performance.now();
  taps = taps.filter((t) => now - t < 3000);
  taps.push(now);
  if (taps.length >= 3) {
    const gaps = taps.slice(1).map((t, i) => t - taps[i]);
    const bpm = Math.round(60000 / (gaps.reduce((x, y) => x + y, 0) / gaps.length) * 10) / 10;
    run("timeline_set", { bpm });
  }
}

// the ruler, the lanes' scrolling and zoom, the keys, and the feed
function wireLanes() {
  // click or drag on the ruler to move the playhead
  const ruler = $("#tl-ruler");
  const seekAt = (ev) => {
    const r = ruler.getBoundingClientRect();
    return snapT((ev.clientX - r.left) / pxs, ev.altKey || snap === "off");
  };
  ruler.addEventListener("pointerdown", (e) => {
    ruler.setPointerCapture(e.pointerId);
    const move = (ev) => { transport = { ...transport, pos: seekAt(ev), playing: transport.playing }; stamp = performance.now(); drawHead(); };
    const up = (ev) => {
      ruler.removeEventListener("pointermove", move);
      ruler.removeEventListener("pointerup", up);
      run("timeline_seek", { t: seekAt(ev) });
    };
    ruler.addEventListener("pointermove", move);
    ruler.addEventListener("pointerup", up);
    move(e);
  });
  // horizontal scroll with the wheel, zoom with ctrl/cmd + wheel
  $("#tl-scroll").addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      pxs = Math.max(2, Math.min(240, pxs * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      docSig = ""; render();
    }
  }, { passive: false });
  document.addEventListener("keydown", (e) => {
    if (!visible || typingInField(e) || !shortcutsOn()) return;     // (off for now - shortcuts.js)
    if (e.key === " " && e.shiftKey) { e.preventDefault(); e.stopImmediatePropagation(); run(transport.playing ? "timeline_pause" : "timeline_play"); }
    if ((e.key === "Delete" || e.key === "Backspace") && selectedClip && !document.querySelector(".modal-scrim")) {
      e.preventDefault(); e.stopImmediatePropagation();
      run("timeline_clip", { id: selectedClip, remove: true });
      selectedClip = null;
    }
  }, true);
  // keep the heads column scrolled with the lanes
  $("#tl-scroll").addEventListener("scroll", () => { $("#tl-heads").scrollTop = $("#tl-scroll").scrollTop; });

  on("snapshot", (s) => {
    onTransport(s.timeline && s.timeline.transport);
    loadAudio(doc().audio);
    if (visible) render();
  });
  on("lite", (l) => onTransport(l.timeline));
  requestAnimationFrame(loop);
}

// The show timeline: tracks of timed clips under a playhead, with the
// song's waveform, a beat grid and snapping.  The engine owns the clock
// (lights stay in time even if this tab stutters); this panel draws it,
// edits it, and keeps the audio following the engine.
import { post, token } from "./api.js";
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { $, h, menu, modal, toast, promptBox, typingInField } from "./ui.js";

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

function pos() {
  if (!transport.playing) return transport.pos;
  return Math.min(transport.length, transport.pos + (performance.now() - stamp) / 1000);
}

// ------------------------------------------------------------ drawing
function render() {
  const d = doc();
  const sig = JSON.stringify([d, pxs]);
  $("#tl-bpm").value = d.bpm;
  $("#tl-len").textContent = fmt(d.length);
  $("#tl-loop").classList.toggle("on", !!d.loop);
  if (sig === docSig) return;
  docSig = sig;
  const width = Math.max(600, Math.ceil(d.length * pxs) + 40);
  const canvas = $("#tl-canvas");
  canvas.style.width = width + "px";
  // headers and lanes
  const heads = [h("div.tl-head.ruler-head", h("span.muted.small", "Time"))];
  const lanes = [];
  if (d.audio) {
    heads.push(h("div.tl-head.audio-head", h("b", "♪ " + d.audio.name),
      h("button.icon-x", { title: "Remove the audio", onclick: () => run("timeline_set", { clear_audio: true }) }, "×")));
    lanes.push(h("div.tl-lane.audio-lane", h("canvas.wave", { width, height: LANE_H })));
  }
  for (const tr of d.tracks) {
    const kindLabel = tr.kind === "cue" ? `Cue · PB${tr.playback}` : tr.kind === "button" ? "Buttons"
      : tr.kind === "fx" ? "Effects" : tr.target === "master" ? "Level · GM" : `Level · ${String(tr.target || "").toUpperCase()}`;
    heads.push(h("div.tl-head" + (tr.mute ? ".muted" : ""), { dataset: { track: tr.id } },
      h("div.tl-head-text", h("b", tr.name), h("small", kindLabel)),
      h("button.tl-mute" + (tr.mute ? ".on" : ""), { title: "Mute this track", onclick: () => run("timeline_track", { id: tr.id, mute: !tr.mute }) }, "M"),
      h("button.icon-x", { title: "Delete this track", onclick: () => run("timeline_track", { id: tr.id, remove: true }) }, "×")));
    lanes.push(lane(tr, width));
  }
  heads.push(h("div.tl-head.add-head", h("button.btn.small.ghost", { onclick: (e) => addTrackMenu(e.currentTarget) }, "+ Track")));
  $("#tl-heads").replaceChildren(...heads);
  $("#tl-lanes").replaceChildren(...lanes);
  drawRuler(width);
  drawWave();
  drawHead();
}

function lane(tr, width) {
  const el = h("div.tl-lane" + (tr.mute ? ".muted" : ""), { dataset: { track: tr.id, kind: tr.kind } });
  el.style.width = width + "px";
  if (tr.kind === "level") {
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("width", width);
    svg.setAttribute("height", LANE_H);
    const pts = tr.clips.map((c) => [c.t * pxs, (1 - c.v / 100) * (LANE_H - 8) + 4]);
    if (pts.length) {
      const all = [[0, pts[0][1]], ...pts, [width, pts[pts.length - 1][1]]];
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
    const name = (FX.find(([k]) => k === c.fx) || [c.fx, c.fx])[1];
    const t = c.target || {};
    const who = t.type ? (TARGETS.find(([k]) => k === t.type) || [0, t.type])[1] : t.group ? `group ${t.group}` : "all";
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

function drawRuler(width) {
  const cv = $("#tl-ruler");
  cv.width = width;
  cv.height = 22;
  const g = cv.getContext("2d");
  g.clearRect(0, 0, width, 22);
  g.font = "10px system-ui, sans-serif";
  const b = beat();
  const d = doc();
  // beat and bar lines, thinned out when zoomed out
  const beatPx = b * pxs;
  if (beatPx >= 6) {
    for (let i = 0, t = 0; t <= d.length; i++, t = i * b) {
      const x = Math.round(t * pxs) + 0.5;
      const bar = i % 4 === 0;
      g.strokeStyle = bar ? "rgba(148,163,184,.55)" : "rgba(148,163,184,.2)";
      g.beginPath(); g.moveTo(x, bar ? 8 : 15); g.lineTo(x, 22); g.stroke();
      if (bar && beatPx * 4 > 34) { g.fillStyle = "#94a3b8"; g.fillText(String(i / 4 + 1), x + 3, 17); }
    }
  }
  const step = pxs >= 40 ? 1 : pxs >= 12 ? 5 : pxs >= 4 ? 15 : 60;
  g.fillStyle = "#cbd5e1";
  for (let t = 0; t <= d.length; t += step) {
    const x = Math.round(t * pxs) + 0.5;
    g.fillRect(x, 0, 1, 7);
    g.fillText(fmt(t).replace(/\.0$/, ""), x + 3, 8);
  }
  for (const m of d.markers || []) {
    const x = m.t * pxs;
    g.fillStyle = "#fbbf24";
    g.fillRect(x, 0, 2, 22);
    g.fillText(m.name, x + 4, 20);
  }
}

function drawWave() {
  const d = doc();
  const cv = $("#tl-lanes .wave");
  if (!cv || !d.audio || !audio || audio.id !== d.audio.id || !audio.peaks) return;
  const g = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  g.fillStyle = "rgba(56,189,248,.55)";
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
  $("#tl-play").textContent = transport.playing ? "Pause" : "Play";
  $("#tl-play").classList.toggle("on", transport.playing);
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
    const fx = h("select.select", ...FX.map(([k, l]) => h("option", { value: k }, l)));
    const who = h("select.select", ...TARGETS.map(([k, l]) => h("option", { value: k }, l)),
      ...((state.snap && state.snap.groups) || []).map((g) => h("option", { value: "g" + g.n }, "Group: " + g.name)));
    const dur = h("input", { type: "number", min: 0.1, step: 0.5, value: existing ? existing.dur : +(beat() * 16).toFixed(2) });
    if (existing) {
      fx.value = existing.fx;
      const t0 = existing.target || {};
      who.value = t0.group !== undefined ? "g" + t0.group : t0.type || "all";
    }
    const close = modal({
      title: existing ? "Edit effect clip" : "Effect clip",
      body: h("div.form-grid", h("label.field", h("span", "Effect"), fx), h("label.field", h("span", "On"), who), h("label.field", h("span", "Length (s)"), dur)),
      foot: [h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", { onclick: () => {
        const w = who.value;
        save({ fx: fx.value, dur: +dur.value || 4, target: w === "all" ? { all: true } : w[0] === "g" ? { group: +w.slice(1) } : { type: w } });
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
  const r = await fetch("/api/console/audio?id=" + encodeURIComponent(meta.id), { headers: t ? { "X-Jarvis-Token": t } : {} });
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
  if (transport.playing && want >= 0 && want < audio.duration) {
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
  stamp = performance.now();
  if (visible) drawHead();
  syncAudio();
}

function loop() {
  if (visible && transport.playing) { drawHead(); }
  requestAnimationFrame(loop);
}

export function setTimelineVisible(on) {
  visible = on;
  if (on) { docSig = ""; render(); loadAudio(doc().audio); }
}

export function initTimeline() {
  $("#tl-play").addEventListener("click", () => run(transport.playing ? "timeline_pause" : "timeline_play"));
  $("#tl-stop").addEventListener("click", () => run("timeline_stop"));
  $("#tl-loop").addEventListener("click", () => run("timeline_set", { loop: !doc().loop }));
  $("#tl-bpm").addEventListener("change", (e) => run("timeline_set", { bpm: +e.target.value || 120 }));
  $("#tl-tap").addEventListener("click", () => {
    const now = performance.now();
    taps = taps.filter((t) => now - t < 3000);
    taps.push(now);
    if (taps.length >= 3) {
      const gaps = taps.slice(1).map((t, i) => t - taps[i]);
      const bpm = Math.round(60000 / (gaps.reduce((a, b) => a + b, 0) / gaps.length) * 10) / 10;
      run("timeline_set", { bpm });
    }
  });
  $("#tl-snap").addEventListener("change", (e) => { snap = e.target.value; });
  $("#tl-zoom-in").addEventListener("click", () => { pxs = Math.min(240, pxs * 1.5); docSig = ""; render(); });
  $("#tl-zoom-out").addEventListener("click", () => { pxs = Math.max(2, pxs / 1.5); docSig = ""; render(); });
  $("#tl-length").addEventListener("click", async () => {
    const v = await promptBox("Timeline length", "Length in seconds", String(doc().length));
    if (v) run("timeline_set", { length: +v || doc().length });
  });
  $("#tl-audio").addEventListener("click", (e) => menu(e.currentTarget, [
    { label: doc().audio ? "Replace audio…" : "Add audio…", hint: "MP3, WAV, OGG, FLAC, M4A", run: () => $("#tl-audio-file").click() },
    { label: "Detect BPM from the audio", disabled: !doc().audio, run: () => {
      const bpm = detectBpm();
      if (bpm) run("timeline_set", { bpm }, { toast: `Tempo ${bpm} BPM` });
    } },
    { label: "Add a marker at the playhead", run: async () => {
      const name = await promptBox("Marker", "Name (verse, drop, chorus…)", "");
      if (name) run("timeline_set", { markers: [...(doc().markers || []), { t: +pos().toFixed(2), name }] });
    } },
    { label: "Remove audio", disabled: !doc().audio, danger: true, run: () => run("timeline_set", { clear_audio: true }) },
  ]));
  $("#tl-audio-file").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) uploadAudio(f);
  });
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
    if (!visible || typingInField(e)) return;
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

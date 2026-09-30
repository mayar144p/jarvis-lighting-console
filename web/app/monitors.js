// Test tools: the virtual node's monitor (what the desk is really sending,
// byte by byte, and the RDM lights it pretends to be) and the MIDI monitor
// (every message from a controller and what it did).
import { get } from "./api.js";
import { run } from "./actions.js";
import { state, patch } from "./store.js";
import { h, modal } from "./ui.js";
import { onAnyMidi, webMidiOn } from "./webmidi.js";

// ------------------------------------------------------------ node monitor
export function openNodeMonitor() {
  let universe = null;
  let timer = 0;
  const toggle = h("button.btn.primary", "…");
  // blind sends nothing at all; to the virtual node (loopback) going live is safe
  const live = h("button.btn.hidden", {
    onclick: async () => {
      if (state.snap && state.snap.dry_run) await run("set_dry_run", { state: false, confirm: true });
      await run("set_output", { state: 1, confirm: true }, { toast: true });
      tick();
    },
  }, "Send to it (go live)");
  const where = h("span.muted.small", "");
  const unis = h("div.nm-unis");
  const grid = h("canvas.nm-grid", { width: 640, height: 320 });
  const hover = h("div.muted.small.nm-hover", "Point at a channel to see whose it is.");
  const lights = h("div.nm-lights");
  let frame = null;
  const owner = (u, ch) => patch().find((p) => p.universe === u && ch >= p.address && ch < p.address + p.channels);

  const draw = () => {
    const ctx = grid.getContext("2d");
    const cw = grid.width / 32, chh = grid.height / 16;
    ctx.fillStyle = "#0b0e14";
    ctx.fillRect(0, 0, grid.width, grid.height);
    ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let i = 0; i < 512; i++) {
      const v = frame ? frame[i] : 0;
      const x = (i % 32) * cw, y = Math.floor(i / 32) * chh;
      const mine = universe && owner(universe, i + 1);
      ctx.fillStyle = v ? `rgba(56,189,248,${0.15 + 0.85 * v / 255})` : (mine ? "#151b27" : "#0f131b");
      ctx.fillRect(x + 1, y + 1, cw - 2, chh - 2);
      if (v) {
        ctx.fillStyle = v > 150 ? "#04121c" : "#dbe7f5";
        ctx.fillText(String(v), x + cw / 2, y + chh / 2);
      }
    }
  };
  grid.addEventListener("mousemove", (e) => {
    const r = grid.getBoundingClientRect();
    const i = Math.floor((e.clientY - r.top) / (r.height / 16)) * 32 + Math.floor((e.clientX - r.left) / (r.width / 32));
    if (i < 0 || i > 511 || !universe) return;
    const p = owner(universe, i + 1);
    hover.textContent = `${universe}.${String(i + 1).padStart(3, "0")} = ${frame ? frame[i] : 0}`
      + (p ? ` · #${p.head_no} ${p.name || p.model}, channel ${i + 2 - p.address} (${(p.map || [])[i + 1 - p.address] || "?"})` : " · nothing patched here");
  });

  const tick = async () => {
    let s;
    try { s = await get("/api/console/vnode" + (universe ? `?universe=${universe}` : "")); } catch (e) { s = { running: false, error: e.message }; }
    toggle.textContent = s.running ? "Turn off" : "Turn on";
    const out = (state.snap && state.snap.output) || {};
    live.classList.toggle("hidden", !(s.running && (state.snap?.dry_run || !out.running)));
    toggle.classList.toggle("primary", !s.running);
    where.textContent = s.running
      ? `Listening at ${s.host}:${s.port} · ${s.counts.dmx} frames · ${s.counts.sync} syncs · ${s.counts.rdm} RDM requests`
      : (s.error || "Off. On: the output goes to this virtual node instead of the network - nothing reaches real lights.");
    const list = s.universes || [];
    if (universe == null && list.length) universe = list[0].universe;
    unis.replaceChildren(...(list.length ? list.map((u) => h("button.chip" + (u.universe === universe ? ".on" : ""), {
      onclick: () => { universe = u.universe; tick(); },
      title: `${u.used} channels above 0 · last frame ${u.age_ms ?? "?"} ms ago`,
    }, `U${u.universe} · ${u.fps} fps`)) : [h("span.muted.small", s.running ? "Nothing received yet: is the output on (Go live, or blind with output running)?" : "")]));
    frame = s.frame || null;
    draw();
    lights.replaceChildren(...(s.lights || []).slice(0, 60).map((l) => h("div.nm-light",
      h("span.mono", l.uid), h("span", `#${l.head} ${l.model}`), h("span.mono.muted", `${l.universe}.${l.address}`))));
  };
  toggle.addEventListener("click", async () => {
    await run("virtual_node", {}, { toast: true });
    tick();
  });
  const body = h("div.nm",
    h("p.muted.small", "A pretend Art-Net node inside Jarvis, with an RDM light for every patched light. Try the whole output - "
      + "Go live, cues, effects, Fixtures ⋯ → Ask the lights (RDM) - with no hardware."),
    h("div.row-btns", toggle, live, where),
    unis, grid, hover,
    h("h3", "RDM lights it answers for"), lights);
  const close = modal({ title: "Virtual node", body, wide: true, onClose: () => clearInterval(timer) });
  timer = setInterval(() => { if (!document.body.contains(body)) { clearInterval(timer); return; } tick(); }, 250);
  tick();
  return close;
}

// ------------------------------------------------------------ MIDI monitor
const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const noteName = (n) => `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`;

function describe(bytes) {
  const [st, a, b] = bytes;
  const kind = st & 0xf0, ch = (st & 0x0f) + 1;
  if (kind === 0x90 && b > 0) return `ch ${ch} · note ${a} (${noteName(a)}) on · velocity ${b}`;
  if (kind === 0x80 || kind === 0x90) return `ch ${ch} · note ${a} (${noteName(a)}) off`;
  if (kind === 0xb0) return `ch ${ch} · CC ${a} = ${b}`;
  if (kind === 0xe0) return `ch ${ch} · pitch bend ${((b << 7) | a) - 8192}`;
  if (kind === 0xc0) return `ch ${ch} · program ${a}`;
  if (st === 0xf8) return "clock";
  return bytes.map((x) => x.toString(16).padStart(2, "0")).join(" ");
}

export function openMidiMonitor() {
  let timer = 0;
  const desk = h("div.mm-list");
  const here = h("div.mm-list");
  const deskState = h("p.muted.small", "");
  const hereRows = [];
  let lastClock = 0;
  const off = onAnyMidi((m) => {
    if (m.data[0] === 0xf8) { if (m.at - lastClock < 1000) return; lastClock = m.at; }   // clock: one line a second
    hereRows.unshift(h("div.mm-row", h("span.mono.muted", new Date(m.at).toLocaleTimeString()),
      h("span", describe(m.data)), h("span.muted.small", m.input)));
    hereRows.splice(30);
    here.replaceChildren(...hereRows);
  });
  const tick = async () => {
    let st;
    try { st = (await get("/api/status")).midi || {}; } catch (e) { st = { error: e.message }; }
    deskState.textContent = !st.enabled ? "MIDI on the desk computer is off (MIDI_ENABLED=false in .env)."
      : st.open ? `Listening to ${st.device} · ${st.events} messages, ${st.mapped} did something` : (st.error || "No MIDI device found on the desk computer.");
    const rows = (st.recent || []).slice().reverse();
    desk.replaceChildren(...(rows.length ? rows.map((r) => h("div.mm-row",
      h("span.mono.muted", new Date(r.at * 1000).toLocaleTimeString()),
      h("span", r.kind === "cc" ? `ch ${r.channel} · CC ${r.number} = ${r.value}` : `ch ${r.channel} · note ${r.number} (${noteName(r.number)}) ${r.on ? "on" : "off"}`),
      h(r.did ? "span.small" : "span.muted.small", r.did || "not mapped"))) : [h("span.muted.small", "Nothing yet: press a pad or move a fader.")]));
  };
  const body = h("div.mm",
    h("p.muted.small", "Press pads and move faders to see what the controller sends: the note or CC number goes into a button's MIDI field, or into midi_map.json."),
    h("div.mm-cols",
      h("div", h("h3", "Desk computer"), deskState, desk),
      h("div", h("h3", "This device (browser)"),
        h("p.muted.small", webMidiOn() ? "Browser MIDI is on." : "Browser MIDI is off: switch it on in Settings → MIDI to see a controller plugged in here."),
        here)));
  const close = modal({ title: "MIDI monitor", body, wide: true, onClose: () => { clearInterval(timer); off(); } });
  timer = setInterval(() => { if (!document.body.contains(body)) { clearInterval(timer); off(); return; } tick(); }, 300);
  tick();
  return close;
}

export const virtualNodeOn = () => !!(state.snap && state.snap.output && state.snap.output.virtual_node);

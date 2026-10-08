// Hardware controllers with feedback (backlog A10 item 1).  An Akai APC
// mini (mk1 / mk2), a Novation Launchpad Mini MK3 / X or a Mackie Control
// surface (Behringer X-Touch, X-Touch Compact...) plugged into this
// computer is recognised by name and just works:
//   pads    = the buttons page on screen, lit in each button's colour
//             (dim when off, full / blinking when on);
//   faders  = playbacks 1-8 and the grand master (motor faders follow);
//   buttons = GO / release a playback, pick the buttons page.
// Needs MIDI in the browser (Settings -> MIDI & OSC); the layouts are in
// ctrlprofiles.js.
import { state, on } from "./store.js";
import { act } from "./api.js";
import { PROFILES, profileFor, hexRgb } from "./ctrlprofiles.js";
import { claimInput, onMidiChange, midiAccess } from "./webmidi.js";
import { currentPage, setPage, tileColour } from "./quickbuttons.js";

const bound = new Map();                 // input id -> { prof, id, input, output, leds, touch, sent }

export function connectedControllers() {
  return [...bound.values()].map((c) => ({ name: c.input.name, layout: PROFILES[c.id].name }));
}

function send(c, msgs) {
  if (!c.output) return;
  for (const m of msgs) { try { c.output.send(m); } catch (e) { /* unplugged mid-send */ } }
}

// only what changed goes out: a controller's MIDI is slow, and 64 pads x 20 Hz would flood it
function led(c, key, msgs) {
  const sig = JSON.stringify(msgs);
  if (c.leds.get(key) === sig) return;
  c.leds.set(key, sig);
  send(c, msgs);
}

const quick = () => (state.snap && state.snap.quick) || { buttons: [] };
const activeIds = () => new Set((state.lite && state.lite.quick_active) || quick().active || []);
const playbacks = () => (state.lite && state.lite.playbacks) || (state.snap && state.snap.playbacks) || [];

function refresh(c) {
  const p = c.prof, page = currentPage(), on = activeIds();
  const bySlot = new Map(quick().buttons.filter((b) => b.page === page).map((b) => [b.slot, b]));
  for (let i = 0; i < p.pads; i++) {
    const b = bySlot.get(i + 1);
    led(c, "pad" + i, p.padLed(i, b ? hexRgb(tileColour(b)) : null, !!(b && on.has(b.id))));
  }
  const pbs = playbacks();
  for (let i = 0; i < 8; i++) {
    led(c, "go" + i, p.buttonLed("go", i, !!(pbs[i] && pbs[i].active)));
    led(c, "page" + i, p.buttonLed("page", i, page === i + 1));
  }
  if (p.motor) {                             // motor faders follow the desk (not while a hand is on one)
    for (let i = 0; i < 8; i++) if (pbs[i] && !c.touch.has(i)) led(c, "f" + i, p.faderOut(i, (pbs[i].level || 0) / 100));
    const gm = (state.lite && state.lite.master) ?? (state.snap && state.snap.master);
    if (gm !== undefined && !c.touch.has(8)) led(c, "f8", p.faderOut(8, gm / 100));
  }
}

// a fader sends ~100 messages a second while it moves: the desk gets the last one every 40 ms
const pending = new Map();
function throttled(key, fn) {
  const had = pending.has(key);
  pending.set(key, fn);
  if (had) return;
  setTimeout(() => { const f = pending.get(key); pending.delete(key); f(); }, 40);
}

function handle(c, bytes) {
  const ev = c.prof.decode(bytes);
  if (!ev) return;
  if (ev.pad !== undefined) {
    const b = quick().buttons.find((x) => x.page === currentPage() && x.slot === ev.pad + 1);
    if (!b) return;
    if (ev.down || b.mode === "hold") act("quick_press", { id: b.id, down: ev.down }).catch(() => {});
  } else if (ev.fader !== undefined) {
    const level = Math.round(ev.value * 100);
    throttled("pb" + ev.fader, () => act("playback_level", { playback: ev.fader + 1, level }).catch(() => {}));
    c.leds.delete("f" + ev.fader);           // the hand sets it: don't echo it back
  } else if (ev.master !== undefined) {
    const level = Math.round(ev.master * 100);
    throttled("gm", () => act("master", { level }).catch(() => {}));
    c.leds.delete("f8");
  } else if (ev.go !== undefined && ev.down) {
    act("cue_go", { playback: ev.go + 1 }).catch(() => {});
  } else if (ev.release !== undefined && ev.down) {
    act("playback_release", { playback: ev.release + 1 }).catch(() => {});
  } else if (ev.page !== undefined && ev.down) {
    setPage(ev.page + 1);
  } else if (ev.touch !== undefined) {
    if (ev.down) c.touch.add(ev.touch); else { c.touch.delete(ev.touch); refresh(c); }
  }
}

// the output port that belongs to an input (the same device, by name)
function outputFor(access, input) {
  const outs = [...access.outputs.values()];
  const base = (n) => String(n || "").replace(/\s*(in|out|input|output|midi)\s*\d*$/i, "").trim().toLowerCase();
  return outs.find((o) => o.name === input.name) || outs.find((o) => base(o.name) === base(input.name))
    || outs.find((o) => profileFor(o.name) === profileFor(input.name)) || null;
}

function rebind(access) {
  const seen = new Set();
  for (const input of access ? access.inputs.values() : []) {
    const id = profileFor(input.name);
    if (!id || input.state === "disconnected") continue;
    seen.add(input.id);
    if (bound.has(input.id)) continue;
    const c = { id, prof: PROFILES[id], input, output: outputFor(access, input), leds: new Map(), touch: new Set() };
    bound.set(input.id, c);
    claimInput(input.id, (bytes) => handle(c, bytes));
    send(c, c.prof.hello());
    refresh(c);
  }
  for (const [key, c] of [...bound]) {
    if (seen.has(key)) continue;
    claimInput(key, null);
    if (c.prof.bye) send(c, c.prof.bye());
    bound.delete(key);
  }
}

export function initControllers() {
  onMidiChange(rebind);
  rebind(midiAccess());
  const all = () => { for (const c of bound.values()) refresh(c); };
  on("lite", all);
  on("snapshot", all);
  on("qbpage", all);
  addEventListener("beforeunload", () => {
    for (const c of bound.values()) {
      for (let i = 0; i < c.prof.pads; i++) send(c, c.prof.padLed(i, null, false));
      if (c.prof.bye) send(c, c.prof.bye());
    }
  });
}

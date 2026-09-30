import { act } from "./api.js";
// MIDI in the browser (Web MIDI): a controller plugged into THIS device -
// a tablet or a laptop running only the browser - plays the buttons.  The
// desk's own MIDI input (app/midi.py) is separate; this one is off until
// switched on in Settings, so one controller heard by both never plays a
// button twice.  Remembered per device.
const KEY = "jarvis.webmidi";
const listeners = new Set();
let access = null;
let error = "";

export const webMidiSupported = () => typeof navigator !== "undefined" && !!navigator.requestMIDIAccess;

export function webMidiOn() {
  try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; }
}

/** Note events: fn({ number, channel, on, velocity }). Returns an unsubscribe. */
export function onNote(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const monitors = new Set();

/** Every message (notes, CC, anything): fn({ data, input, at }). For the MIDI monitor. */
export function onAnyMidi(fn) {
  monitors.add(fn);
  return () => monitors.delete(fn);
}

// MIDI clock from a controller or DJ software on this device: 24 ticks a
// beat.  The tempo goes to the desk's beat clock about once a second (not
// 48 times), Start says "this is the 1".
let ticks = [];
let sentBpm = 0, sentAt = 0;
function clock(st, at) {
  if (st === 0xfa) { ticks = []; act("tempo_sync", { beat: 1 }).catch(() => {}); return; }
  if (ticks.length && at - ticks[ticks.length - 1] > 1000) ticks = [];
  ticks.push(at);
  if (ticks.length > 97) ticks = ticks.slice(-97);
  if (ticks.length < 25 || at - sentAt < 1000) return;
  const bpm = 60000 / (((ticks[ticks.length - 1] - ticks[0]) / (ticks.length - 1)) * 24);
  if (Math.abs(bpm - sentBpm) < 0.2) return;
  sentBpm = bpm; sentAt = at;
  act("tempo_set", { bpm: Math.round(bpm * 10) / 10, source: "browser" }).catch(() => {});
}

function message(e) {
  const st0 = (e.data || [])[0];
  if (st0 === 0xf8 || st0 === 0xfa) clock(st0, e.timeStamp || performance.now());
  if (monitors.size) {
    const m = { data: [...(e.data || [])], input: (e.target && e.target.name) || "", at: Date.now() };
    for (const fn of [...monitors]) { try { fn(m); } catch (err) { /* ignore */ } }
  }
  const [status, number, velocity] = e.data || [];
  const kind = status & 0xf0;
  if (kind !== 0x90 && kind !== 0x80) return;
  const ev = { number, velocity, channel: (status & 0x0f) + 1, on: kind === 0x90 && velocity > 0 };
  for (const fn of [...listeners]) {
    try { fn(ev); } catch (err) { /* a listener never stops the others */ }
  }
}

function attach() {
  if (!access) return;
  for (const input of access.inputs.values()) input.onmidimessage = message;
}

export function webMidiInputs() {
  return access ? [...access.inputs.values()].map((i) => i.name || "MIDI input") : [];
}

export const webMidiError = () => error;

/** Switch browser MIDI on or off for this device (asks for permission once). */
export async function setWebMidi(on) {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* ignore */ }
  if (!on) {
    if (access) for (const input of access.inputs.values()) input.onmidimessage = null;
    access = null;
    return true;
  }
  if (!webMidiSupported()) {
    error = "This browser has no MIDI (use Chrome or Edge)";
    return false;
  }
  try {
    access = await navigator.requestMIDIAccess();
    access.onstatechange = attach;             // controllers plugged in later
    attach();
    error = "";
    return true;
  } catch (e) {
    error = "MIDI permission was refused";
    access = null;
    return false;
  }
}

export function initWebMidi() {
  if (webMidiOn()) setWebMidi(true);
}

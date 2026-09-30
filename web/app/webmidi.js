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

function message(e) {
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

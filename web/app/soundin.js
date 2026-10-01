// Listening to the room (Web Audio): the analysis (soundanalysis.js) of
// each frame is sent to the desk about 25 times a second (app/sound.py
// uses it).
//
// A microphone needs a secure page: https, or the desk computer itself
// (http://localhost).  From another computer on plain http the browser refuses.
import { post } from "./api.js";
import { createAnalysis } from "./soundanalysis.js";

let ctx = null, stream = null, analyser = null, timer = 0;
let freq = null, wave = null, analysis = null;
let pending = { beat: false, drop: false };
let sending = false, failures = 0;
const listeners = new Set();
const EMPTY = { level: 0, bass: 0, mid: 0, high: 0, bpm: null, confidence: 0, beatAt: -1e9, dropAt: -1e9 };
export let reading = EMPTY;

export const soundSupported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext;
export const listening = () => !!stream;
export function onSound(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function frame() {
  analyser.getFloatFrequencyData(freq);
  analyser.getFloatTimeDomainData(wave);
  const ev = analysis.step(freq, wave, performance.now());
  if (ev.beat) pending.beat = true;
  if (ev.drop) pending.drop = true;
  for (const fn of [...listeners]) { try { fn(reading); } catch (e) { /* ignore */ } }
  send();
}

async function send() {
  if (sending || failures > 20) return;
  sending = true;
  const body = { level: reading.level, bass: reading.bass, mid: reading.mid, high: reading.high,
    beat: pending.beat, drop: pending.drop, bpm: reading.bpm, confidence: reading.confidence,
    device: "this computer" };
  pending = { beat: false, drop: false };
  try { await post("/api/console/sound", body); failures = 0; } catch (e) { failures++; }
  sending = false;
}

function attach(mediaStream) {
  stream = mediaStream;
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.2;
  src.connect(analyser);
  freq = new Float32Array(analyser.frequencyBinCount);
  wave = new Float32Array(analyser.fftSize);
  analysis = createAnalysis(ctx.sampleRate, analyser.frequencyBinCount);
  reading = analysis.reading;
  failures = 0;
  timer = setInterval(frame, 40);
}

// which input: the microphone the browser picks, or one chosen here (a
// line in from the DJ mixer, an audio interface) - kept on this computer
const INPUT_KEY = "jarvis.input";
export function chosenInput() {
  try { return localStorage.getItem(INPUT_KEY) || ""; } catch (e) { return ""; }
}

/** [{id, label}] of the audio inputs; the names show once the browser has
 *  been allowed to listen. */
export async function listInputs() {
  if (!(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices)) return [];
  const all = await navigator.mediaDevices.enumerateDevices();
  return all.filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications")
    .map((d, i) => ({ id: d.deviceId, label: d.label || `Input ${i + 1}` }));
}

const RAW = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };

export async function startListening() {
  if (stream) return true;
  if (!soundSupported()) throw new Error(window.isSecureContext ? "This browser can't listen (no microphone access)."
    : "Browsers only give a microphone to https pages or the desk computer itself (localhost).");
  const id = chosenInput();
  let media;
  try {
    media = await navigator.mediaDevices.getUserMedia({ audio: id ? { ...RAW, deviceId: { exact: id } } : RAW });
  } catch (e) {
    if (!id || !/NotFound|Overconstrained/.test(e.name || "")) throw e;
    media = await navigator.mediaDevices.getUserMedia({ audio: RAW });   // that input is unplugged: the default
  }
  attach(media);
  try { localStorage.setItem("jarvis.listen", "1"); } catch (e) { /* ignore */ }
  return true;
}

/** Listen to another input from now on (restarts if listening). */
export async function chooseInput(id) {
  try { localStorage.setItem(INPUT_KEY, id || ""); } catch (e) { /* ignore */ }
  if (stream) {
    stopListening();
    await startListening();
  }
}

/** The input actually in use, by name. */
export const inputLabel = () => (stream && stream.getAudioTracks()[0] && stream.getAudioTracks()[0].label) || "";

export function stopListening() {
  clearInterval(timer);
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ctx) ctx.close().catch(() => {});
  stream = ctx = analyser = null;
  reading = EMPTY;
  try { localStorage.setItem("jarvis.listen", "0"); } catch (e) { /* ignore */ }
}

/** Listen to a given stream instead of the microphone (a line in, a test). */
export function listenTo(mediaStream) {
  if (stream) stopListening();
  attach(mediaStream);
}

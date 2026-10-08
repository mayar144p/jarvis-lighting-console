// Audio timecode (LTC) in: this page listens to a sound input (a line in
// from the playback computer, an audio interface) and tells the desk the
// time it reads, ten times a second.  The desk's timeline follows it the
// way it follows MIDI timecode.  The input is the one picked for Sound
// (kept on this computer); a microphone works too if it can hear the tone.
import { act } from "./api.js";
import { chosenInput, soundSupported } from "./soundin.js";
import { LtcDecoder } from "./ltc.js";

let ctx = null, stream = null, node = null, last = null, timer = 0;
export let reading = "";
export const ltcListening = () => !!stream;

const RAW = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };

export async function startLtc() {
  if (stream) return true;
  if (!soundSupported()) throw new Error(window.isSecureContext ? "This browser can't listen to a sound input."
    : "Browsers only give a sound input to https pages or the desk computer itself (localhost).");
  const id = chosenInput();
  stream = await navigator.mediaDevices.getUserMedia({ audio: id ? { deviceId: { exact: id }, ...RAW } : RAW });
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const dec = new LtcDecoder(ctx.sampleRate, (f) => { last = f; reading = f.text; });
  // ScriptProcessor: old, but everywhere (and the decoding is cheap)
  node = ctx.createScriptProcessor(2048, 1, 1);
  node.onaudioprocess = (e) => dec.feed(e.inputBuffer.getChannelData(0));
  src.connect(node);
  node.connect(ctx.destination);           // (silent: the output buffer stays empty)
  timer = setInterval(() => {
    if (!last) return;
    const f = last;
    last = null;
    act("timecode_ltc", { t: f.seconds, fps: f.fps, text: f.text }).catch(() => {});
  }, 100);
  return true;
}

export function stopLtc() {
  clearInterval(timer);
  timer = 0;
  try { node && node.disconnect(); } catch (e) { /* gone */ }
  try { stream && stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* gone */ }
  try { ctx && ctx.close(); } catch (e) { /* gone */ }
  ctx = stream = node = null;
  reading = "";
}

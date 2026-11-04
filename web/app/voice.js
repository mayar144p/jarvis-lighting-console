// Talk to the copilot: hold the mic, speak, let go - the words go in like
// typed ones.  The browser's own speech recognition when it has one (Chrome);
// otherwise the sound is recorded here and the AI writes down what was said
// (/api/console/transcribe).  Hands stay on the faders.
import { post } from "./api.js";
import { toast } from "./ui.js";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let srBroken = false;            // (Electron has the object but no service behind it)

// ---- recording: 16 kHz mono WAV, which the AI takes ------------------------
async function recorder() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(node);
  node.connect(ctx.destination);
  return {
    async stop() {
      node.disconnect(); src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      const rate = ctx.sampleRate;
      await ctx.close();
      return wav(chunks, rate);
    },
  };
}

function wav(chunks, rate) {
  const len = chunks.reduce((n, c) => n + c.length, 0);
  const all = new Float32Array(len);
  let o = 0;
  for (const c of chunks) { all.set(c, o); o += c.length; }
  const to = 16000, step = rate / to, n = Math.floor(len / step);
  const buf = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (p, s) => { for (let i = 0; i < s.length; i++) buf.setUint8(p + i, s.charCodeAt(i)); };
  str(0, "RIFF"); buf.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
  buf.setUint32(16, 16, true); buf.setUint16(20, 1, true); buf.setUint16(22, 1, true);
  buf.setUint32(24, to, true); buf.setUint32(28, to * 2, true); buf.setUint16(32, 2, true); buf.setUint16(34, 16, true);
  str(36, "data"); buf.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, all[Math.floor(i * step)] || 0));
    buf.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  const bytes = new Uint8Array(buf.buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { b64: btoa(bin), seconds: n / to };
}

// ---- hold to talk ------------------------------------------------------------
export function wireMic(btn, onText) {
  let live = null;
  let rec = null;
  const start = async (e) => {
    e.preventDefault();
    if (live || rec) return;
    btn.classList.add("on");
    if (SR && !srBroken) {
      const r = live = new SR();
      r.lang = navigator.language || "en-GB";
      r.interimResults = false;
      r.maxAlternatives = 1;
      let said = "";
      r.onresult = (ev) => { said = [...ev.results].map((x) => x[0].transcript).join(" "); };
      r.onerror = (ev) => { if (ev.error === "network" || ev.error === "service-not-allowed") srBroken = true; };
      r.onend = () => { live = null; btn.classList.remove("on"); if (said.trim()) onText(said.trim()); else if (srBroken) toast("Speech recognition isn't available here - hold the mic again (the AI will listen)"); };
      try { r.start(); } catch { live = null; srBroken = true; }
      if (live) return;
    }
    try { rec = await recorder(); } catch (err) { btn.classList.remove("on"); toast("The microphone isn't allowed: " + err.message, "bad"); }
  };
  const stop = async () => {
    btn.classList.remove("on");
    if (live) { try { live.stop(); } catch { /* ended */ } return; }
    if (!rec) return;
    const r = rec; rec = null;
    const { b64, seconds } = await r.stop();
    if (seconds < 0.4) return;
    btn.classList.add("busy");
    try {
      const res = await post("/api/console/transcribe", { audio: b64, format: "wav" });
      if (res.ok && res.text) onText(res.text);
      else toast(res.error || "nothing heard", "bad");
    } catch (err) { toast(err.message, "bad"); } finally { btn.classList.remove("busy"); }
  };
  btn.addEventListener("pointerdown", start);
  btn.addEventListener("pointerup", stop);
  btn.addEventListener("pointerleave", () => { if (live || rec) stop(); });
  btn.addEventListener("pointercancel", stop);
}
